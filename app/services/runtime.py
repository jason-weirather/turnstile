from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import docker

from app.models.service import ServiceDescriptor


@dataclass(frozen=True)
class RuntimeExecutionResult:
    output_uri: str
    backend: str


class RuntimeController(Protocol):
    def execute_image_generate(
        self,
        service: ServiceDescriptor,
        prompt: str,
    ) -> RuntimeExecutionResult: ...


class NoopDockerRuntimeController:
    """Stub runtime controller for Milestone 1.

    The Docker SDK is initialized so the integration point exists, but no real
    containers are launched yet.
    """

    def __init__(self) -> None:
        self._docker_sdk = docker

    def execute_image_generate(
        self,
        service: ServiceDescriptor,
        prompt: str,
    ) -> RuntimeExecutionResult:
        del prompt
        return RuntimeExecutionResult(
            output_uri=f"memory://artifacts/{service.service_id}/latest.png",
            backend=service.image,
        )


def get_runtime_controller() -> RuntimeController:
    return NoopDockerRuntimeController()
