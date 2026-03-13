import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import router as api_router
from app.main import app
from app.services import readiness as readiness_module
from app.services.capabilities import CapabilityRegistry
from app.services.definition_loader import DefinitionLoader


def _set_worker_inspect(
    monkeypatch: pytest.MonkeyPatch,
    *,
    ping: dict[str, dict[str, str]] | None,
    active_queues: dict[str, list[dict[str, str]]] | None,
) -> None:
    class FakeInspect:
        def ping(self) -> dict[str, dict[str, str]]:
            return {} if ping is None else ping

        def active_queues(self) -> dict[str, list[dict[str, str]]]:
            return {} if active_queues is None else active_queues

    monkeypatch.setattr(
        readiness_module,
        "get_celery_inspector",
        lambda timeout_s: FakeInspect(),
    )


def test_openapi_includes_capability_routes() -> None:
    client = TestClient(app)

    response = client.get("/openapi.json")

    assert response.status_code == 200
    paths = response.json()["paths"]
    assert "/v1/example/http/echo" in paths
    assert "/v1/example/command/run" in paths
    assert "/readyz" in paths


def test_readyz_returns_503_when_workers_are_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = TestClient(app)
    _set_worker_inspect(monkeypatch, ping=None, active_queues=None)

    response = client.get("/readyz")

    assert response.status_code == 503
    body = response.json()
    assert body["ready"] is False
    assert body["status"] == "not_ready"
    lane = next(item for item in body["worker_lanes"] if item["lane"] == "gpu")
    assert lane["submission_ready"] is False
    assert lane["reason"] == "No healthy workers are attached to lane 'gpu'."


def test_submit_example_http_job_and_lookup_result() -> None:
    client = TestClient(app)

    submit_response = client.post(
        "/v1/example/http/echo",
        json={"text": "hello default"},
    )

    assert submit_response.status_code == 202
    job_id = submit_response.json()["job_id"]

    job_response = client.get(f"/v1/jobs/{job_id}")
    assert job_response.status_code == 200
    body = job_response.json()
    assert body["status"] == "succeeded"
    assert body["capability"] == "example.http.echo"
    assert body["selected_service_id"] == "mock-http-alpha"
    assert body["result_payload"]["service_id"] == "mock-http-alpha"
    assert body["result_payload"]["echo"]["text"] == "hello default"


def test_submit_example_command_job() -> None:
    client = TestClient(app)

    submit_response = client.post(
        "/v1/example/command/run",
        json={"text": "artifact body", "artifact_name": "note.txt"},
    )

    assert submit_response.status_code == 202
    job_id = submit_response.json()["job_id"]

    job_response = client.get(f"/v1/jobs/{job_id}")
    assert job_response.status_code == 200
    body = job_response.json()
    assert body["status"] == "succeeded"
    assert body["capability"] == "example.command.run"
    assert body["selected_service_id"] == "mock-command-alpha"
    assert body["result_payload"]["service_id"] == "mock-command-alpha"
    assert body["result_payload"]["echo"]["artifact_name"] == "note.txt"


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


def test_async_submit_returns_503_when_target_lane_has_no_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = TestClient(app)
    _set_worker_inspect(
        monkeypatch,
        ping={"worker-cpu@test": {"ok": "pong"}},
        active_queues={"worker-cpu@test": [{"name": "cpu"}]},
    )

    response = client.post("/v1/example/http/echo", json={"text": "hello gpu"})

    assert response.status_code == 503
    assert response.json()["detail"] == {
        "error_code": "queue_unavailable",
        "lane": "gpu",
        "detail": "Workers are running, but none are attached to lane 'gpu'.",
    }


def test_ops_and_health_endpoints_expose_runtime_visibility() -> None:
    client = TestClient(app)

    health_response = client.get("/healthz")
    readiness_response = client.get("/readyz")
    runtime_response = client.get("/ops/runtime")
    readiness_ops_response = client.get("/ops/readiness")
    capabilities_response = client.get("/ops/capabilities")
    queues_response = client.get("/ops/queues")
    services_response = client.get("/ops/services")

    assert health_response.status_code == 200
    assert readiness_response.status_code == 200
    assert runtime_response.status_code == 200
    assert readiness_ops_response.status_code == 200
    assert capabilities_response.status_code == 200
    assert queues_response.status_code == 200
    assert services_response.status_code == 200

    assert sorted(queue["lane"] for queue in queues_response.json()) == ["cpu", "gpu"]
    assert runtime_response.json()["docker_reachable"] is True
    assert health_response.json()["redis"]["reachable"] is True
    assert health_response.json()["ready"] is True
    assert runtime_response.json()["submission_ready"] is True
    gpu_lane = next(
        item for item in runtime_response.json()["worker_lanes"] if item["lane"] == "gpu"
    )
    assert gpu_lane["submission_ready"] is True
    assert gpu_lane["reason"] is None
    assert "identity" in runtime_response.json()
    assert "worker_inspection" in readiness_ops_response.json()
    assert len(services_response.json()["services"]) >= 4
    assert {item["capability_id"] for item in capabilities_response.json()} >= {
        "example.http.echo",
        "example.command.run",
    }


def test_ops_runtime_reports_worker_readiness_reason_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = TestClient(app)
    _set_worker_inspect(
        monkeypatch,
        ping={"worker-cpu@test": {"ok": "pong"}},
        active_queues={"worker-cpu@test": [{"name": "cpu"}]},
    )

    response = client.get("/ops/runtime")

    assert response.status_code == 200
    gpu_lane = next(item for item in response.json()["worker_lanes"] if item["lane"] == "gpu")
    assert gpu_lane["workers"] == []
    assert gpu_lane["healthy"] is False
    assert gpu_lane["submission_ready"] is False
    assert gpu_lane["reason"] == "Workers are running, but none are attached to lane 'gpu'."


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
