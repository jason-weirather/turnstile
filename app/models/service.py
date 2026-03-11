from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from app.models.capability import AdapterType


class ServiceMode(StrEnum):
    WARM = "warm"
    EPHEMERAL = "ephemeral"


class ServiceDescriptor(BaseModel):
    service_id: str
    capabilities: list[str]
    image: str
    mode: ServiceMode
    gpu_required: bool
    estimated_vram_mb: int = Field(ge=0)
    startup_timeout_s: int = Field(gt=0)
    idle_ttl_s: int = Field(gt=0)
    healthcheck: dict[str, Any] = Field(default_factory=dict)
    adapter_type: AdapterType
    adapter_config: dict[str, Any] = Field(default_factory=dict)
    cancel_strategy: str = "celery_revoke"
    eviction_priority: int = 100
