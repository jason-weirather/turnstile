from datetime import datetime

from pydantic import BaseModel, Field

from app.models.capability import AdapterType, ExecutionMode, QueueLane
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


class InstanceIdentity(BaseModel):
    instance_id: str
    hostname: str
    started_at: datetime


class WorkerInspectionSnapshot(BaseModel):
    status: str
    timeout_s: float
    attempts: int
    ping_workers: list[str] = Field(default_factory=list)
    active_queue_workers: list[str] = Field(default_factory=list)
    workers_by_lane: dict[str, list[str]] = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)
    detail: str | None = None


class WorkerLaneStatus(BaseModel):
    lane: str
    workers: list[str] = Field(default_factory=list)
    healthy: bool
    submission_ready: bool = False
    reason: str | None = None


class ReadinessSnapshot(BaseModel):
    status: str
    ready: bool
    detail: str | None = None
    runtime_mode: str
    redis_reachable: bool
    docker_reachable: bool
    required_lanes: list[str] = Field(default_factory=list)
    worker_lanes: list[WorkerLaneStatus] = Field(default_factory=list)
    worker_inspection: WorkerInspectionSnapshot
    identity: InstanceIdentity


class RuntimeSnapshot(BaseModel):
    queues: list[QueueSnapshot]
    active_job_id: str | None
    active_service_id: str | None
    warm_services: list[WarmServiceState] = Field(default_factory=list)
    redis_reachable: bool
    docker_reachable: bool
    submission_ready: bool
    required_lanes: list[str] = Field(default_factory=list)
    worker_lanes: list[WorkerLaneStatus] = Field(default_factory=list)
    worker_inspection: WorkerInspectionSnapshot
    identity: InstanceIdentity


class JobsSnapshot(BaseModel):
    jobs: list[JobResponse] = Field(default_factory=list)


class ServiceRuntimeView(BaseModel):
    service: ServiceDescriptor
    warm_state: WarmServiceState | None = None


class ServicesSnapshot(BaseModel):
    services: list[ServiceRuntimeView] = Field(default_factory=list)


class CapabilityView(BaseModel):
    capability_id: str
    method: str
    path: str
    summary: str
    execution_mode: ExecutionMode
    queue_lane: QueueLane
    adapter_type: AdapterType
    default_service_selection: str


class LaneQueueActionResponse(BaseModel):
    lane: str
    cancelled_count: int
    cancelled_job_ids: list[str] = Field(default_factory=list)
