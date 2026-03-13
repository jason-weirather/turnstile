from __future__ import annotations

import os
import time
from typing import Any

import docker
import httpx
import pytest
from docker.errors import APIError, DockerException

pytestmark = [pytest.mark.integration]


API_BASE_URL = os.getenv("TURNSTILE_INTEGRATION_API_URL", "http://127.0.0.1:8000")
JOB_TIMEOUT_S = float(os.getenv("TURNSTILE_INTEGRATION_JOB_TIMEOUT_S", "120"))
POLL_INTERVAL_S = float(os.getenv("TURNSTILE_INTEGRATION_POLL_INTERVAL_S", "1"))


@pytest.fixture(scope="session")
def client() -> httpx.Client:
    with httpx.Client(base_url=API_BASE_URL, timeout=30.0) as http_client:
        yield http_client


@pytest.fixture(scope="session")
def gpu_runtime_available() -> tuple[bool, str | None]:
    client = docker.from_env()
    container = None
    try:
        container = client.containers.create(
            "turnstile/mock-http-tool:latest",
            detach=True,
            device_requests=[docker.types.DeviceRequest(count=-1, capabilities=[["gpu"]])],
            labels={"turnstile.integration.probe": "true"},
        )
        container.start()
        return (True, None)
    except APIError as exc:
        detail = str(exc)
        if 'capabilities: [[gpu]]' in detail:
            return (False, detail)
        raise
    except DockerException as exc:
        return (False, str(exc))
    finally:
        if container is not None:
            try:
                container.remove(force=True)
            except DockerException:
                pass


def _require_success(response: httpx.Response) -> dict[str, Any]:
    response.raise_for_status()
    return dict(response.json())


def _submit_job(client: httpx.Client, path: str, payload: dict[str, Any]) -> str:
    body = _require_success(client.post(path, json=payload))
    assert body["status"] == "queued"
    return str(body["job_id"])


def _poll_job(client: httpx.Client, job_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + JOB_TIMEOUT_S
    while time.monotonic() < deadline:
        body = _require_success(client.get(f"/v1/jobs/{job_id}"))
        if body["status"] in {"succeeded", "failed", "cancelled"}:
            return body
        time.sleep(POLL_INTERVAL_S)
    raise AssertionError(f"Timed out waiting for job '{job_id}'")


def _service_view(services: dict[str, Any], service_id: str) -> dict[str, Any]:
    return next(
        item for item in services["services"] if item["service"]["service_id"] == service_id
    )


def _queue_view(runtime: dict[str, Any], lane: str) -> dict[str, Any]:
    return next(item for item in runtime["queues"] if item["lane"] == lane)


def test_readiness_before_submit(client: httpx.Client) -> None:
    readyz = _require_success(client.get("/readyz"))
    readiness = _require_success(client.get("/ops/readiness"))

    assert readyz["ready"] is True
    assert readiness["ready"] is True
    lanes = {item["lane"]: item for item in readiness["worker_lanes"]}
    assert lanes["cpu"]["submission_ready"] is True
    assert lanes["gpu"]["submission_ready"] is True


def test_warm_http_alpha_success(client: httpx.Client) -> None:
    job = _poll_job(
        client,
        _submit_job(
            client,
            "/v1/example/http/echo",
            {"text": "hello from alpha", "service_id": "mock-http-alpha"},
        ),
    )

    assert job["status"] == "succeeded"
    assert job["selected_service_id"] == "mock-http-alpha"
    assert job["result_payload"]["backend_kind"] == "mock_http_tool"
    assert job["result_payload"]["instance_id"] == "alpha"
    assert job["result_payload"]["response_text"] == "alpha:hello from alpha"


def test_warm_http_beta_success(client: httpx.Client) -> None:
    job = _poll_job(
        client,
        _submit_job(
            client,
            "/v1/example/http/echo",
            {"text": "hello from beta", "service_id": "mock-http-beta"},
        ),
    )

    assert job["status"] == "succeeded"
    assert job["selected_service_id"] == "mock-http-beta"
    assert job["result_payload"]["backend_kind"] == "mock_http_tool"
    assert job["result_payload"]["instance_id"] == "beta"
    assert job["result_payload"]["response_text"] == "beta:hello from beta"


def test_warm_service_reuse_reuses_same_container(client: httpx.Client) -> None:
    first = _poll_job(
        client,
        _submit_job(
            client,
            "/v1/example/http/echo",
            {"text": "reuse one", "service_id": "mock-http-alpha"},
        ),
    )
    second = _poll_job(
        client,
        _submit_job(
            client,
            "/v1/example/http/echo",
            {"text": "reuse two", "service_id": "mock-http-alpha"},
        ),
    )

    assert first["status"] == "succeeded"
    assert second["status"] == "succeeded"
    assert first["container_id"] == second["container_id"]


def test_command_alpha_success(client: httpx.Client) -> None:
    job = _poll_job(
        client,
        _submit_job(
            client,
            "/v1/example/command/run",
            {
                "text": "write alpha artifact",
                "artifact_name": "alpha-note.txt",
                "artifact_text": "alpha artifact payload",
                "service_id": "mock-command-alpha",
            },
        ),
    )

    assert job["status"] == "succeeded"
    assert job["selected_service_id"] == "mock-command-alpha"
    assert job["result_payload"]["backend_kind"] == "mock_command_tool"
    assert job["result_payload"]["instance_id"] == "command-alpha"
    assert "alpha-note.txt" in job["result_payload"]["artifact_names"]


def test_command_beta_success(client: httpx.Client) -> None:
    job = _poll_job(
        client,
        _submit_job(
            client,
            "/v1/example/command/run",
            {
                "text": "write beta artifact",
                "artifact_name": "beta-note.txt",
                "artifact_text": "beta artifact payload",
                "service_id": "mock-command-beta",
            },
        ),
    )

    assert job["status"] == "succeeded"
    assert job["selected_service_id"] == "mock-command-beta"
    assert job["result_payload"]["backend_kind"] == "mock_command_tool"
    assert job["result_payload"]["instance_id"] == "command-beta"
    assert "beta-note.txt" in job["result_payload"]["artifact_names"]


def test_forced_http_failure(client: httpx.Client) -> None:
    job = _poll_job(
        client,
        _submit_job(
            client,
            "/v1/example/http/echo",
            {"text": "fail http", "fail": True, "service_id": "mock-http-alpha"},
        ),
    )

    assert job["status"] == "failed"
    assert job["error_code"] == "execution_failed"
    assert "500" in str(job["error_detail"])


def test_forced_command_failure(client: httpx.Client) -> None:
    job = _poll_job(
        client,
        _submit_job(
            client,
            "/v1/example/command/run",
            {
                "text": "fail command",
                "artifact_name": "failed.txt",
                "fail": True,
                "service_id": "mock-command-alpha",
            },
        ),
    )

    assert job["status"] == "failed"
    assert job["error_code"] == "execution_failed"
    assert "forced failure" in str(job["error_detail"])


def test_warm_http_cancellation(client: httpx.Client) -> None:
    job_id = _submit_job(
        client,
        "/v1/example/http/echo",
        {"text": "cancel me", "sleep_s": 15, "service_id": "mock-http-alpha"},
    )

    cancel_response = _require_success(client.post(f"/v1/jobs/{job_id}/cancel"))
    assert cancel_response["status"] == "cancelled"

    job = _poll_job(client, job_id)
    assert job["status"] == "cancelled"
    assert job["error_code"] == "cancelled"


def test_ops_state_sanity_after_jobs_complete(client: httpx.Client) -> None:
    runtime = _require_success(client.get("/ops/runtime"))
    services = _require_success(client.get("/ops/services"))
    queues = {item["lane"]: item for item in runtime["queues"]}

    assert runtime["docker_reachable"] is True
    assert runtime["submission_ready"] is True
    assert runtime["active_job_id"] is None
    assert queues["cpu"]["pending"] == 0
    assert queues["gpu"]["pending"] == 0
    assert queues["gpu"]["active_job_id"] is None
    assert len(services["services"]) >= 6


@pytest.mark.gpu_eviction
def test_gpu_warm_service_eviction_and_ops_handoff(
    client: httpx.Client,
    gpu_runtime_available: tuple[bool, str | None],
) -> None:
    gpu_ready, gpu_detail = gpu_runtime_available
    if not gpu_ready:
        pytest.skip(f"Docker GPU runtime is unavailable on this host: {gpu_detail}")

    alpha_first = _poll_job(
        client,
        _submit_job(
            client,
            "/v1/example/http/gpu-echo",
            {"text": "hello gpu alpha", "service_id": "mock-gpu-http-alpha"},
        ),
    )
    alpha_second = _poll_job(
        client,
        _submit_job(
            client,
            "/v1/example/http/gpu-echo",
            {"text": "hello gpu alpha again", "service_id": "mock-gpu-http-alpha"},
        ),
    )
    beta = _poll_job(
        client,
        _submit_job(
            client,
            "/v1/example/http/gpu-echo",
            {"text": "hello gpu beta", "service_id": "mock-gpu-http-beta"},
        ),
    )

    assert alpha_first["status"] == "succeeded"
    assert alpha_second["status"] == "succeeded"
    assert beta["status"] == "succeeded"
    assert alpha_first["container_id"] == alpha_second["container_id"]
    assert beta["container_id"] != alpha_first["container_id"]
    assert alpha_first["result_payload"]["instance_id"] == "gpu-alpha"
    assert beta["result_payload"]["instance_id"] == "gpu-beta"

    runtime = _require_success(client.get("/ops/runtime"))
    services = _require_success(client.get("/ops/services"))

    gpu_residents = [item for item in runtime["warm_services"] if item["gpu_required"] is True]
    assert runtime["active_job_id"] is None
    assert runtime["active_service_id"] == "mock-gpu-http-beta"
    assert len(gpu_residents) == 1
    assert gpu_residents[0]["service_id"] == "mock-gpu-http-beta"
    assert _queue_view(runtime, "gpu")["pending"] == 0
    assert _queue_view(runtime, "gpu")["active_job_id"] is None
    assert _queue_view(runtime, "gpu")["active_service_id"] == "mock-gpu-http-beta"
    assert _service_view(services, "mock-gpu-http-alpha")["warm_state"] is None
    assert _service_view(services, "mock-gpu-http-beta")["warm_state"] is not None
