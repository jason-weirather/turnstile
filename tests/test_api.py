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
    payload = submit_response.json()
    job_id = payload["job_id"]

    job_response = client.get(f"/v1/jobs/{job_id}")
    assert job_response.status_code == 200
    body = job_response.json()
    assert body["status"] == "succeeded"
    assert body["capability"] == "image.generate"
    assert body["result_payload"]["adapter"] == "noop_stub"


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
