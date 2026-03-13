from __future__ import annotations

from pathlib import Path

import httpx

from app.models.capability import AdapterType, CapabilityDefinition, ExecutionMode, QueueLane
from app.models.service import ServiceDescriptor, ServiceMode
from app.services.adapters import ContainerCommandAdapter, HttpForwardJsonAdapter
from app.services.runtime import (
    EphemeralExecutionResult,
    RuntimeArtifact,
    RuntimeController,
    WarmServiceHandle,
)


class FakeRuntime(RuntimeController):
    def prepare_for_service(self, service: ServiceDescriptor) -> None:
        del service

    def execute_container_command(
        self,
        service: ServiceDescriptor,
        payload: dict[str, object],
        job_id: str,
    ) -> EphemeralExecutionResult:
        del service, payload, job_id
        return EphemeralExecutionResult(
            container_id="container-123",
            stdout='{"text":"hello"}',
            stderr="",
            artifacts=[
                RuntimeArtifact(
                    name="transcript.txt",
                    path="/tmp/transcript.txt",
                    size_bytes=5,
                )
            ],
        )

    def ensure_warm_http_service(self, service: ServiceDescriptor) -> WarmServiceHandle:
        del service
        raise NotImplementedError

    def cancel_job(self, job: object, service: ServiceDescriptor) -> bool:
        del job, service
        return False

    def docker_reachable(self) -> tuple[bool, str | None]:
        return (True, None)


def test_http_forward_json_adapter_normalizes_response() -> None:
    capability = CapabilityDefinition(
        capability_id="image.edit",
        method="POST",
        path="/image/edit",
        summary="Edit an image",
        request_schema=Path("request.json"),
        response_schema=Path("response.json"),
        execution_mode=ExecutionMode.ASYNC,
        queue_lane=QueueLane.GPU,
        adapter_type=AdapterType.HTTP_FORWARD_JSON,
        default_service_selection="mock-http",
    )
    service = ServiceDescriptor(
        service_id="mock-http",
        capabilities=["image.edit"],
        image="ghcr.io/example/mock-http:latest",
        mode=ServiceMode.WARM,
        gpu_required=True,
        estimated_vram_mb=2048,
        startup_timeout_s=30,
        idle_ttl_s=300,
        adapter_type=AdapterType.HTTP_FORWARD_JSON,
        adapter_config={"base_url": "https://example.test", "path": "/invoke"},
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/invoke"
        return httpx.Response(200, json={"ok": True, "backend": "warm-http"})

    transport = httpx.MockTransport(handler)

    def client_factory(**_: object) -> httpx.Client:
        return httpx.Client(transport=transport, base_url="https://example.test", timeout=10.0)

    adapter = HttpForwardJsonAdapter(client_factory=client_factory)
    result = adapter.execute(capability, service, {"prompt": "test"}, job_id="job-1")

    assert result.result_payload == {"ok": True, "backend": "warm-http"}


def test_container_command_adapter_normalizes_runtime_result() -> None:
    capability = CapabilityDefinition(
        capability_id="example.command.run",
        method="POST",
        path="/example/command/run",
        summary="Run command example",
        request_schema=Path("request.json"),
        response_schema=Path("response.json"),
        execution_mode=ExecutionMode.ASYNC,
        queue_lane=QueueLane.CPU,
        adapter_type=AdapterType.CONTAINER_COMMAND,
        default_service_selection="mock-container",
    )
    service = ServiceDescriptor(
        service_id="mock-container",
        capabilities=["example.command.run"],
        image="ghcr.io/example/mock-container:latest",
        mode=ServiceMode.EPHEMERAL,
        gpu_required=False,
        estimated_vram_mb=0,
        startup_timeout_s=30,
        idle_ttl_s=300,
        adapter_type=AdapterType.CONTAINER_COMMAND,
        adapter_config={},
    )

    result = ContainerCommandAdapter(runtime_controller=FakeRuntime()).execute(
        capability,
        service,
        {"text": "hello"},
        job_id="job-2",
    )

    assert result.container_id == "container-123"
    assert result.result_payload["text"] == "hello"
    assert result.result_payload["artifacts"][0]["name"] == "transcript.txt"
