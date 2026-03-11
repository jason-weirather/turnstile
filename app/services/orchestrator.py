from __future__ import annotations

import time

from app.core.config import get_settings
from app.models.job import JobStatus
from app.services.job_store import get_job_store
from app.services.registry import service_registry
from app.services.runtime import get_runtime_controller


def run_image_generate_job(job_id: str, prompt: str, service_id: str) -> dict[str, str]:
    service = service_registry.get(service_id)
    job_store = get_job_store()

    if service is None:
        job_store.set_status(
            job_id,
            status=JobStatus.FAILED,
            error=f"Unknown service '{service_id}'.",
        )
        raise ValueError(f"Unknown service '{service_id}'.")

    acquired_slot = False
    try:
        job_store.wait_for_gpu_turn(job_id, service_id)
        acquired_slot = True

        settings = get_settings()
        time.sleep(settings.stub_task_delay_s)

        if "fail" in prompt.lower():
            message = "Stub image generation failure requested by prompt."
            job_store.set_status(job_id, status=JobStatus.FAILED, error=message)
            raise RuntimeError(message)

        runtime = get_runtime_controller()
        result = runtime.execute_image_generate(service=service, prompt=prompt)
        payload = {
            "output_uri": result.output_uri,
            "backend": result.backend,
        }
        job_store.set_status(job_id, status=JobStatus.SUCCEEDED, result=payload, error=None)
        return payload
    except Exception as exc:
        if acquired_slot:
            job_store.set_status(job_id, status=JobStatus.FAILED, error=str(exc))
        raise
    finally:
        if acquired_slot:
            job_store.release_gpu_slot(job_id)
