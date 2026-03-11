from enum import StrEnum

from pydantic import BaseModel, Field


class ServiceMode(StrEnum):
    WARM = "warm"
    EPHEMERAL = "ephemeral"


class ServiceDescriptor(BaseModel):
    service_id: str
    capability: str
    image: str
    mode: ServiceMode
    gpu_required: bool
    estimated_vram_mb: int = Field(ge=0)
    startup_timeout_s: int = Field(gt=0)
    idle_ttl_s: int = Field(gt=0)
    endpoint_adapter: str
