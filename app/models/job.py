from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from app.models.capability import QueueLane


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
    queue_lane: QueueLane
    requested_service_id: str
    selected_service_id: str
    status: JobStatus = JobStatus.QUEUED
    request_payload: dict[str, Any]
    result_payload: dict[str, Any] | None = None
    error_code: str | None = None
    error_detail: str | None = None
    container_id: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: datetime | None = None
    finished_at: datetime | None = None


class JobResponse(BaseModel):
    job_id: str
    capability: str
    queue_lane: QueueLane
    requested_service_id: str
    selected_service_id: str
    status: JobStatus
    request_payload: dict[str, Any]
    result_payload: dict[str, Any] | None = None
    error_code: str | None = None
    error_detail: str | None = None
    container_id: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    result: dict[str, Any] | None = None
    error: str | None = None


class JobCancelResponse(BaseModel):
    job_id: str
    status: JobStatus
