from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class ExecutionMode(StrEnum):
    SYNC = "sync"
    ASYNC = "async"


class QueueLane(StrEnum):
    GPU = "gpu"
    CPU = "cpu"


class AdapterType(StrEnum):
    NOOP_STUB = "noop_stub"
    HTTP_FORWARD_JSON = "http_forward_json"
    CONTAINER_COMMAND = "container_command"


class CapabilityDefinition(BaseModel):
    capability_id: str
    method: str
    path: str
    summary: str
    request_schema: Path
    response_schema: Path
    execution_mode: ExecutionMode
    queue_lane: QueueLane
    adapter_type: AdapterType
    default_service_selection: str
    docs_examples: list[dict[str, Any]] = Field(default_factory=list)


class JobAcceptedResponse(BaseModel):
    job_id: str
    status: str
