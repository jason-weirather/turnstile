import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import router as api_router
from app.main import app
from app.services.capabilities import CapabilityRegistry
from app.services.definition_loader import DefinitionLoader


def test_openapi_includes_capability_routes() -> None:
    client = TestClient(app)

    response = client.get("/openapi.json")

    assert response.status_code == 200
    paths = response.json()["paths"]
    assert "/v1/example/http/echo" in paths
    assert "/v1/example/command/run" in paths
    assert "/v1/image/generate" in paths
    assert "/v1/audio/transcribe" in paths


def test_invalid_audio_payload_is_rejected() -> None:
    client = TestClient(app)

    response = client.post("/v1/audio/transcribe", json={"language": "en"})

    assert response.status_code == 422


def test_submit_image_job_and_lookup_result() -> None:
    client = TestClient(app)

    submit_response = client.post(
        "/v1/image/generate",
        json={"prompt": "studio portrait", "style": "cinematic"},
    )

    assert submit_response.status_code == 202
    job_id = submit_response.json()["job_id"]

    job_response = client.get(f"/v1/jobs/{job_id}")
    assert job_response.status_code == 200
    body = job_response.json()
    assert body["status"] == "succeeded"
    assert body["capability"] == "image.generate"
    assert body["result_payload"]["backend"] == "warm-http"
    assert body["result_payload"]["style"] == "cinematic"


def test_submit_audio_transcribe_job() -> None:
    client = TestClient(app)

    submit_response = client.post(
        "/v1/audio/transcribe",
        json={"audio_url": "https://example.com/clip.wav", "language": "en"},
    )

    assert submit_response.status_code == 202
    job_id = submit_response.json()["job_id"]

    job_response = client.get(f"/v1/jobs/{job_id}")
    assert job_response.status_code == 200
    body = job_response.json()
    assert body["status"] == "succeeded"
    assert body["result_payload"]["language"] == "en"
    assert body["result_payload"]["transcript_file"] == "transcript.txt"


def test_submit_example_http_job_with_service_override() -> None:
    client = TestClient(app)

    submit_response = client.post(
        "/v1/example/http/echo",
        json={"text": "hello beta", "service_id": "mock-http-beta"},
    )

    assert submit_response.status_code == 202
    job_id = submit_response.json()["job_id"]

    job_response = client.get(f"/v1/jobs/{job_id}")
    assert job_response.status_code == 200
    body = job_response.json()
    assert body["status"] == "succeeded"
    assert body["requested_service_id"] == "mock-http-beta"
    assert body["selected_service_id"] == "mock-http-beta"
    assert body["result_payload"]["service_id"] == "mock-http-beta"
    assert body["result_payload"]["echo"]["text"] == "hello beta"


def test_submit_example_command_job_with_service_override() -> None:
    client = TestClient(app)

    submit_response = client.post(
        "/v1/example/command/run",
        json={
            "text": "artifact body",
            "artifact_name": "note.txt",
            "artifact_text": "artifact body",
            "service_id": "mock-command-alpha",
        },
    )

    assert submit_response.status_code == 202
    job_id = submit_response.json()["job_id"]

    job_response = client.get(f"/v1/jobs/{job_id}")
    assert job_response.status_code == 200
    body = job_response.json()
    assert body["status"] == "succeeded"
    assert body["requested_service_id"] == "mock-command-alpha"
    assert body["selected_service_id"] == "mock-command-alpha"
    assert body["result_payload"]["service_id"] == "mock-command-alpha"
    assert body["result_payload"]["echo"]["artifact_name"] == "note.txt"


def test_ops_and_health_endpoints_expose_runtime_visibility() -> None:
    client = TestClient(app)

    health_response = client.get("/healthz")
    runtime_response = client.get("/ops/runtime")
    capabilities_response = client.get("/ops/capabilities")
    queues_response = client.get("/ops/queues")
    services_response = client.get("/ops/services")

    assert health_response.status_code == 200
    assert runtime_response.status_code == 200
    assert capabilities_response.status_code == 200
    assert queues_response.status_code == 200
    assert services_response.status_code == 200

    assert sorted(queue["lane"] for queue in queues_response.json()) == ["cpu", "gpu"]
    assert runtime_response.json()["docker_reachable"] is True
    assert health_response.json()["redis"]["reachable"] is True
    assert len(services_response.json()["services"]) >= 2
    assert {item["capability_id"] for item in capabilities_response.json()} >= {
        "example.http.echo",
        "example.command.run",
        "audio.transcribe",
        "image.generate",
    }


def test_temporary_sync_capability_definition_registers_route(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "config"
    capabilities_dir = root / "capabilities"
    services_dir = root / "services"
    requests_dir = root / "requests"
    responses_dir = root / "responses"
    capabilities_dir.mkdir(parents=True)
    services_dir.mkdir()
    requests_dir.mkdir()
    responses_dir.mkdir()

    (requests_dir / "text_echo.request.json").write_text(
        json.dumps(
            {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "type": "object",
                "additionalProperties": False,
                "required": ["text"],
                "properties": {
                    "text": {"type": "string"},
                },
            }
        ),
        encoding="utf-8",
    )
    (responses_dir / "text_echo.response.json").write_text(
        json.dumps(
            {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "type": "object",
                "additionalProperties": False,
                "required": ["echoed"],
                "properties": {
                    "echoed": {"type": "string"},
                },
            }
        ),
        encoding="utf-8",
    )
    (capabilities_dir / "text_echo.yaml").write_text(
        "\n".join(
            [
                "capability_id: text.echo",
                "method: POST",
                "path: /text/echo",
                "summary: Echo text synchronously",
                "request_schema: requests/text_echo.request.json",
                "response_schema: responses/text_echo.response.json",
                "execution_mode: sync",
                "queue_lane: cpu",
                "adapter_type: noop_stub",
                "default_service_selection: mock-text-echo",
            ]
        ),
        encoding="utf-8",
    )

    loader = DefinitionLoader(
        capabilities_dir=capabilities_dir,
        services_dir=services_dir,
        schemas_dir=Path(__file__).resolve().parents[1] / "config" / "schemas",
    )
    registry = CapabilityRegistry(loader.load_capabilities())

    monkeypatch.setattr(api_router, "get_capability_registry", lambda: registry)
    monkeypatch.setattr(
        api_router,
        "execute_capability_request",
        lambda capability_id, payload: {"echoed": f"{capability_id}:{payload['text']}"},
    )

    custom_app = FastAPI()
    custom_app.include_router(api_router.build_api_router())
    client = TestClient(custom_app)

    openapi_response = client.get("/openapi.json")
    route_response = client.post("/v1/text/echo", json={"text": "hello"})

    assert openapi_response.status_code == 200
    assert "/v1/text/echo" in openapi_response.json()["paths"]
    assert route_response.status_code == 200
    assert route_response.json() == {"echoed": "text.echo:hello"}
