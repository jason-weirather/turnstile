from fastapi.testclient import TestClient

from app.main import app


def test_openapi_includes_capability_routes() -> None:
    client = TestClient(app)

    response = client.get("/openapi.json")

    assert response.status_code == 200
    paths = response.json()["paths"]
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


def test_ops_and_health_endpoints_expose_runtime_visibility() -> None:
    client = TestClient(app)

    health_response = client.get("/healthz")
    runtime_response = client.get("/ops/runtime")
    queues_response = client.get("/ops/queues")
    services_response = client.get("/ops/services")

    assert health_response.status_code == 200
    assert runtime_response.status_code == 200
    assert queues_response.status_code == 200
    assert services_response.status_code == 200

    assert sorted(queue["lane"] for queue in queues_response.json()) == ["cpu", "gpu"]
    assert runtime_response.json()["docker_reachable"] is True
    assert health_response.json()["redis"]["reachable"] is True
    assert len(services_response.json()["services"]) >= 2
