from pydantic import BaseModel


class OpsSnapshot(BaseModel):
    queue: list[str]
    active_job_id: str | None
    active_service_id: str | None
