from __future__ import annotations

from celery import Task

from app.core.celery_app import celery_app
from app.services.orchestrator import run_image_generate_job


@celery_app.task(bind=True, name="turnstile.image.generate")
def generate_image_task(
    self: Task,
    job_id: str,
    prompt: str,
    service_id: str,
) -> dict[str, str]:
    self.update_state(state="STARTED")
    return run_image_generate_job(job_id=job_id, prompt=prompt, service_id=service_id)
