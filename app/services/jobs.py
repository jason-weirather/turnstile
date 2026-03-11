from uuid import uuid4

from app.models.image import ImageGenerateAccepted, ImageGenerateRequest
from app.models.job import JobRecord, JobResponse, JobStatus
from app.services.job_store import get_job_store
from app.services.registry import service_registry
from app.tasks import generate_image_task


def submit_image_generate_job(payload: ImageGenerateRequest) -> ImageGenerateAccepted:
    service = service_registry.resolve_for_capability("image.generate", payload.service_id)
    job_id = str(uuid4())
    get_job_store().enqueue(
        JobRecord(
            job_id=job_id,
            capability="image.generate",
            requested_service_id=service.service_id,
            selected_service_id=service.service_id,
        )
    )
    generate_image_task.apply_async(
        kwargs={
            "job_id": job_id,
            "prompt": payload.prompt,
            "service_id": service.service_id,
        },
        queue="gpu",
        task_id=job_id,
    )
    return ImageGenerateAccepted(job_id=job_id, status=JobStatus.QUEUED)


def get_job_response(job_id: str) -> JobResponse | None:
    record = get_job_store().get(job_id)
    if record is None:
        return None

    return JobResponse(
        job_id=record.job_id,
        capability=record.capability,
        requested_service_id=record.requested_service_id,
        selected_service_id=record.selected_service_id,
        status=record.status,
        result=record.result,
        error=record.error,
    )
