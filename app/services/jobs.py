from uuid import uuid4

from celery import states

from app.core.celery_app import celery_app
from app.models.job import JobCancelResponse, JobRecord, JobResponse, JobStatus
from app.services.capabilities import get_capability_registry
from app.services.job_store import get_job_store
from app.services.registry import get_service_registry
from app.tasks import execute_capability_task


def submit_capability_job(capability_id: str, payload: dict[str, object]) -> dict[str, str]:
    capability = get_capability_registry().get(capability_id)
    service_id = payload.get("service_id")
    service = get_service_registry().resolve_for_capability(
        capability_id,
        service_id if isinstance(service_id, str) else None,
    )
    job_id = str(uuid4())
    get_job_store().enqueue(
        JobRecord(
            job_id=job_id,
            capability=capability_id,
            queue_lane=capability.queue_lane,
            requested_service_id=service.service_id,
            selected_service_id=service.service_id,
            request_payload=payload,
        )
    )
    execute_capability_task.apply_async(
        kwargs={
            "job_id": job_id,
            "capability_id": capability_id,
            "payload": payload,
            "service_id": service.service_id,
        },
        queue=capability.queue_lane.value,
        task_id=job_id,
    )
    return {"job_id": job_id, "status": JobStatus.QUEUED.value}


def get_job_response(job_id: str) -> JobResponse | None:
    record = get_job_store().get(job_id)
    if record is None:
        return None

    return JobResponse(
        job_id=record.job_id,
        capability=record.capability,
        queue_lane=record.queue_lane,
        requested_service_id=record.requested_service_id,
        selected_service_id=record.selected_service_id,
        status=record.status,
        request_payload=record.request_payload,
        result_payload=record.result_payload,
        error_code=record.error_code,
        error_detail=record.error_detail,
        container_id=record.container_id,
        created_at=record.created_at,
        started_at=record.started_at,
        finished_at=record.finished_at,
        result=record.result_payload,
        error=record.error_detail,
    )


def cancel_job(job_id: str) -> JobCancelResponse | None:
    job = get_job_store().cancel(job_id)
    if job is None:
        return None
    celery_app.control.revoke(job_id, terminate=False)
    celery_app.backend.store_result(job_id, {"status": "cancelled"}, states.REVOKED)
    return JobCancelResponse(job_id=job_id, status=JobStatus.CANCELLED)
