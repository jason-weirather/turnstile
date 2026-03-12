from datetime import datetime

from pydantic import BaseModel, Field

from app.models.job import JobResponse
from app.models.service import ServiceDescriptor


class WarmServiceState(BaseModel):
    service_id: str
    container_id: str
    base_url: str
    gpu_required: bool
    started_at: datetime
    last_used_at: datetime
    idle_ttl_s: int
    expires_at: datetime
    status: str


class QueueSnapshot(BaseModel):
    lane: str
    pending: int
    queued_job_ids: list[str] = Field(default_factory=list)
    active_job_id: str | None
    active_service_id: str | None


class WorkerLaneStatus(BaseModel):
    lane: str
    workers: list[str] = Field(default_factory=list)
    healthy: bool


class RuntimeSnapshot(BaseModel):
    queues: list[QueueSnapshot]
    active_job_id: str | None
    active_service_id: str | None
    warm_services: list[WarmServiceState] = Field(default_factory=list)
    redis_reachable: bool
    docker_reachable: bool
    worker_lanes: list[WorkerLaneStatus] = Field(default_factory=list)


class JobsSnapshot(BaseModel):
    jobs: list[JobResponse] = Field(default_factory=list)


class ServiceRuntimeView(BaseModel):
    service: ServiceDescriptor
    warm_state: WarmServiceState | None = None


class ServicesSnapshot(BaseModel):
    services: list[ServiceRuntimeView] = Field(default_factory=list)
