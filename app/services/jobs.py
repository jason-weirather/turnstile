from __future__ import annotations

from typing import Any
from uuid import uuid4

from celery import states

from app.core.celery_app import celery_app
from app.core.config import get_settings
from app.models.capability import CapabilityDefinition, ExecutionMode
from app.models.job import JobCancelResponse, JobRecord, JobResponse, JobStatus
from app.models.service import ServiceDescriptor
from app.services.capabilities import get_capability_registry
from app.services.job_store import get_job_store
from app.services.orchestrator import run_capability_job
from app.services.registry import get_service_registry
from app.services.runtime import get_runtime_controller
from app.tasks import execute_capability_task


def execute_capability_request(capability_id: str, payload: dict[str, object]) -> dict[str, Any]:
    capability, service, job_record = _prepare_job_record(capability_id, payload)
    if capability.execution_mode == ExecutionMode.SYNC:
        return run_capability_job(
            job_id=job_record.job_id,
            capability_id=capability_id,
            payload=payload,
            service_id=service.service_id,
        )

    _dispatch_capability_job(
        job_id=job_record.job_id,
        capability_id=capability_id,
        payload=payload,
        service_id=service.service_id,
        queue_name=capability.queue_lane.value,
    )
    return _job_accepted_payload(job_record.job_id)


def submit_capability_job(capability_id: str, payload: dict[str, object]) -> dict[str, str]:
    capability, service, job_record = _prepare_job_record(capability_id, payload)
    if capability.execution_mode != ExecutionMode.ASYNC:
        raise ValueError(f"Capability '{capability_id}' is not configured for async execution.")

    _dispatch_capability_job(
        job_id=job_record.job_id,
        capability_id=capability_id,
        payload=payload,
        service_id=service.service_id,
        queue_name=capability.queue_lane.value,
    )
    return _job_accepted_payload(job_record.job_id)


def _prepare_job_record(
    capability_id: str,
    payload: dict[str, object],
) -> tuple[CapabilityDefinition, ServiceDescriptor, JobRecord]:
    capability = get_capability_registry().get(capability_id)
    service_id = payload.get("service_id")
    service = get_service_registry().resolve_for_capability(
        capability_id,
        service_id if isinstance(service_id, str) else None,
        capability.default_service_selection,
    )
    job_id = str(uuid4())
    record = JobRecord(
        job_id=job_id,
        capability=capability_id,
        queue_lane=capability.queue_lane,
        requested_service_id=service.service_id,
        selected_service_id=service.service_id,
        request_payload=payload,
    )
    get_job_store().enqueue(record)
    return capability, service, record


def _dispatch_capability_job(
    *,
    job_id: str,
    capability_id: str,
    payload: dict[str, object],
    service_id: str,
    queue_name: str,
) -> None:
    execute_capability_task.apply_async(
        kwargs={
            "job_id": job_id,
            "capability_id": capability_id,
            "payload": payload,
            "service_id": service_id,
        },
        queue=queue_name,
        task_id=job_id,
    )


def _job_accepted_payload(job_id: str) -> dict[str, str]:
    return {"job_id": job_id, "status": JobStatus.QUEUED.value}


def get_job_response(job_id: str) -> JobResponse | None:
    record = get_job_store().get(job_id)
    if record is None:
        return None
    return _job_response(record)


def list_job_responses(limit: int | None = None) -> list[JobResponse]:
    resolved_limit = limit if limit is not None else get_settings().ops_job_limit
    return [_job_response(job) for job in get_job_store().list_jobs(resolved_limit)]


def cancel_job(job_id: str) -> JobCancelResponse | None:
    store = get_job_store()
    existing = store.get(job_id)
    if existing is None:
        return None

    job = store.cancel(job_id)
    if job is None:
        return None

    service = get_service_registry().get(job.selected_service_id)
    if service is not None:
        get_runtime_controller().cancel_job(job, service)

    celery_app.control.revoke(job_id, terminate=False)
    celery_app.backend.store_result(job_id, {"status": "cancelled"}, states.REVOKED)
    return JobCancelResponse(job_id=job_id, status=JobStatus.CANCELLED)


def _job_response(record: JobRecord) -> JobResponse:
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
