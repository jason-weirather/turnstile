from __future__ import annotations

import sys
from pathlib import Path

import httpx

from app.models.capability import AdapterType, CapabilityDefinition, ExecutionMode, QueueLane
from app.models.service import ServiceDescriptor, ServiceMode
from app.services.adapters import ContainerCommandAdapter, HttpForwardJsonAdapter


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
    result = adapter.execute(capability, service, {"prompt": "test"})

    assert result.result_payload == {"ok": True, "backend": "warm-http"}


def test_container_command_adapter_parses_json_stdout() -> None:
    capability = CapabilityDefinition(
        capability_id="audio.transcribe",
        method="POST",
        path="/audio/transcribe",
        summary="Transcribe audio",
        request_schema=Path("request.json"),
        response_schema=Path("response.json"),
        execution_mode=ExecutionMode.ASYNC,
        queue_lane=QueueLane.CPU,
        adapter_type=AdapterType.CONTAINER_COMMAND,
        default_service_selection="mock-container",
    )
    service = ServiceDescriptor(
        service_id="mock-container",
        capabilities=["audio.transcribe"],
        image="ghcr.io/example/mock-container:latest",
        mode=ServiceMode.EPHEMERAL,
        gpu_required=False,
        estimated_vram_mb=0,
        startup_timeout_s=30,
        idle_ttl_s=300,
        adapter_type=AdapterType.CONTAINER_COMMAND,
        adapter_config={
            "command": [
                sys.executable,
                "-c",
                (
                    "import json, sys; "
                    "payload=json.load(sys.stdin); "
                    "print(json.dumps({'text': payload['audio_url']}))"
                ),
            ]
        },
    )

    result = ContainerCommandAdapter().execute(
        capability,
        service,
        {"audio_url": "https://example.com/file.wav"},
    )

    assert result.result_payload == {"text": "https://example.com/file.wav"}
