from pydantic import BaseModel, Field

from app.models.job import JobStatus


class ImageGenerateRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=1000)
    service_id: str | None = None


class ImageGenerateAccepted(BaseModel):
    job_id: str
    status: JobStatus
