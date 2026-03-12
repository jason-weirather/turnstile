from pydantic import BaseModel, Field


class DependencyHealth(BaseModel):
    reachable: bool
    detail: str | None = None


class QueueHealth(BaseModel):
    lane: str
    pending: int
    active_job_id: str | None = None
    workers: list[str] = Field(default_factory=list)
    healthy: bool = False


class HealthResponse(BaseModel):
    status: str
    redis: DependencyHealth
    docker: DependencyHealth
    queues: list[QueueHealth]
    active_job_id: str | None = None
    active_service_id: str | None = None
