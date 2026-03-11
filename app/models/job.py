from datetime import datetime, timezone
from enum import StrEnum

from pydantic import BaseModel, Field


class JobStatus(StrEnum):
    QUEUED = "queued"
    WAITING_FOR_GPU = "waiting_for_gpu"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobRecord(BaseModel):
    job_id: str
    capability: str
    requested_service_id: str
    selected_service_id: str
    status: JobStatus = JobStatus.QUEUED
    result: dict[str, str] | None = None
    error: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class JobResponse(BaseModel):
    job_id: str
    capability: str
    requested_service_id: str
    selected_service_id: str
    status: JobStatus
    result: dict[str, str] | None = None
    error: str | None = None
