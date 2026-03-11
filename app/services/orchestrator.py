from __future__ import annotations

import time
from typing import Any

from app.core.config import get_settings
from app.models.capability import QueueLane
from app.models.job import JobStatus
from app.services.adapters import get_adapter_registry
from app.services.capabilities import get_capability_registry
from app.services.job_store import get_job_store
from app.services.registry import get_service_registry


class JobCancelledError(RuntimeError):
    """Raised when a job is cancelled before completion."""


def run_capability_job(
    job_id: str,
    capability_id: str,
    payload: dict[str, Any],
    service_id: str,
) -> dict[str, Any]:
    capability = get_capability_registry().get(capability_id)
    service = get_service_registry().get(service_id)
    job_store = get_job_store()

    if service is None:
        job_store.set_status(
            job_id,
            status=JobStatus.FAILED,
            error_code="unknown_service",
            error_detail=f"Unknown service '{service_id}'.",
        )
        raise ValueError(f"Unknown service '{service_id}'.")

    acquired_slot = False
    try:
        if capability.queue_lane == QueueLane.GPU:
            job_store.wait_for_gpu_turn(job_id, service_id)
            acquired_slot = True
        else:
            job_store.set_status(job_id, status=JobStatus.RUNNING)

        current = job_store.get(job_id)
        if current is not None and current.status == JobStatus.CANCELLED:
            raise JobCancelledError(f"Job '{job_id}' was cancelled before execution.")

        time.sleep(get_settings().stub_task_delay_s)
        adapter = get_adapter_registry().get(service.adapter_type.value)
        result = adapter.execute(capability=capability, service=service, payload=payload)
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
        job_store.set_status(
            job_id,
            status=JobStatus.FAILED,
            error_code="execution_failed",
            error_detail=str(exc),
        )
        raise
    finally:
        if acquired_slot:
            job_store.release_gpu_slot(job_id)
