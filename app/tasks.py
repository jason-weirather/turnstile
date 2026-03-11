from __future__ import annotations

from typing import Any

from celery import Task
from celery.exceptions import Ignore

from app.core.celery_app import celery_app
from app.services.orchestrator import JobCancelledError, run_capability_job


@celery_app.task(bind=True, name="turnstile.capability.execute")
def execute_capability_task(
    self: Task,
    job_id: str,
    capability_id: str,
    payload: dict[str, Any],
    service_id: str,
) -> dict[str, Any]:
    self.update_state(state="STARTED")
    try:
        return run_capability_job(
            job_id=job_id,
            capability_id=capability_id,
            payload=payload,
            service_id=service_id,
        )
    except JobCancelledError as exc:
        raise Ignore() from exc
