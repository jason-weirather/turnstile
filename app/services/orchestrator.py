from __future__ import annotations

import threading
import time
from typing import Any, Protocol

from app.core.config import get_settings
from app.models.capability import QueueLane
from app.models.job import JobStatus
from app.models.service import ServiceMode
from app.services.adapters import get_adapter_registry
from app.services.capabilities import get_capability_registry
from app.services.job_store import get_job_store
from app.services.registry import get_service_registry
from app.services.runtime import get_runtime_controller


class JobCancelledError(RuntimeError):
    """Raised when a job is cancelled before completion."""


class GpuLeaseStore(Protocol):
    def renew_gpu_job_lease(self, job_id: str, service_id: str) -> None: ...


class GpuLeaseHeartbeat:
    def __init__(
        self,
        *,
        job_store: GpuLeaseStore,
        job_id: str,
        service_id: str,
    ) -> None:
        self._job_store = job_store
        self._job_id = job_id
        self._service_id = service_id
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self._thread.join(timeout=1.0)

    def _run(self) -> None:
        interval = max(0.2, get_settings().runtime_heartbeat_interval_s)
        while not self._stop_event.wait(interval):
            self._job_store.renew_gpu_job_lease(self._job_id, self._service_id)


def run_capability_job(
    job_id: str,
    capability_id: str,
    payload: dict[str, Any],
    service_id: str,
) -> dict[str, Any]:
    capability = get_capability_registry().get(capability_id)
    service = get_service_registry().get(service_id)
    job_store = get_job_store()
    runtime = get_runtime_controller()

    if service is None:
        job_store.set_status(
            job_id,
            status=JobStatus.FAILED,
            error_code="unknown_service",
            error_detail=f"Unknown service '{service_id}'.",
        )
        raise ValueError(f"Unknown service '{service_id}'.")

    acquired_slot = False
    heartbeat: GpuLeaseHeartbeat | None = None
    try:
        if capability.queue_lane == QueueLane.GPU:
            while True:
                current = job_store.get(job_id)
                if current is None:
                    raise RuntimeError(f"Missing job state for '{job_id}'.")
                if current.status == JobStatus.CANCELLED:
                    raise JobCancelledError(f"Job '{job_id}' was cancelled before execution.")

                runtime.prepare_for_service(service)
                if job_store.try_start_gpu_job(job_id, service_id):
                    acquired_slot = True
                    heartbeat = GpuLeaseHeartbeat(
                        job_store=job_store,
                        job_id=job_id,
                        service_id=service_id,
                    )
                    heartbeat.start()
                    break
                job_store.set_status(job_id, status=JobStatus.WAITING_FOR_GPU, error_detail=None)
                time.sleep(get_settings().arbiter_poll_interval_s)
        else:
            job_store.set_status(job_id, status=JobStatus.RUNNING, error_detail=None)
            runtime.prepare_for_service(service)

        current = job_store.get(job_id)
        if current is not None and current.status == JobStatus.CANCELLED:
            raise JobCancelledError(f"Job '{job_id}' was cancelled before execution.")

        if get_settings().runtime_mode == "stub":
            time.sleep(get_settings().stub_task_delay_s)
        adapter = get_adapter_registry().get(service.adapter_type.value)
        result = adapter.execute(
            capability=capability,
            service=service,
            payload=payload,
            job_id=job_id,
        )
        current = job_store.get(job_id)
        if current is not None and current.status == JobStatus.CANCELLED:
            raise JobCancelledError(f"Job '{job_id}' was cancelled during execution.")

        job_store.set_status(
            job_id,
            status=JobStatus.SUCCEEDED,
            result_payload=result.result_payload,
            container_id=result.container_id,
        )
        return result.result_payload
    except JobCancelledError:
        job_store.set_status(job_id, status=JobStatus.CANCELLED, error_code="cancelled")
        raise
    except Exception as exc:
        current = job_store.get(job_id)
        if current is not None and current.status == JobStatus.CANCELLED:
            raise JobCancelledError(f"Job '{job_id}' was cancelled during execution.") from exc
        job_store.set_status(
            job_id,
            status=JobStatus.FAILED,
            error_code="execution_failed",
            error_detail=str(exc),
        )
        raise
    finally:
        if heartbeat is not None:
            heartbeat.stop()
        if acquired_slot:
            preserve_service_id: str | None = None
            if service.mode == ServiceMode.WARM and service.gpu_required:
                if job_store.get_warm_service(service.service_id) is not None:
                    preserve_service_id = service.service_id
            job_store.release_gpu_slot(job_id, preserve_service_id=preserve_service_id)
