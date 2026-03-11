import threading
import time
from collections.abc import Generator

import fakeredis
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.job import JobRecord, JobStatus
from app.services import job_store as job_store_module
from app.services import jobs as jobs_service
from app.services import orchestrator
from app.services.registry import service_registry


@pytest.fixture(autouse=True)
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> Generator[fakeredis.FakeRedis, None, None]:
    from app.core.config import get_settings

    settings = get_settings()
    settings.stub_task_delay_s = 0.05
    settings.arbiter_poll_interval_s = 0.01
    settings.job_ttl_s = 120
    settings.gpu_lock_ttl_s = 5

    fake_client = fakeredis.FakeRedis(decode_responses=True)
    store = job_store_module.RedisJobStore(fake_client)

    job_store_module.get_redis_client.cache_clear()
    job_store_module.get_job_store.cache_clear()
    monkeypatch.setattr(job_store_module, "get_redis_client", lambda: fake_client)
    monkeypatch.setattr(job_store_module, "get_job_store", lambda: store)
    monkeypatch.setattr(jobs_service, "get_job_store", lambda: store)
    monkeypatch.setattr(orchestrator, "get_job_store", lambda: store)

    yield fake_client

    store.clear()


@pytest.fixture(autouse=True)
def eager_celery() -> None:
    from app.tasks import generate_image_task

    generate_image_task.app.conf.task_always_eager = True
    generate_image_task.app.conf.task_store_eager_result = True
    generate_image_task.app.conf.result_backend = "cache+memory://"
    generate_image_task.app.conf.broker_url = "memory://"


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_healthz(client: TestClient) -> None:
    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_submit_job_and_read_shared_status(client: TestClient) -> None:
    submit_response = client.post("/v1/image/generate", json={"prompt": "test skyline"})

    assert submit_response.status_code == 202
    payload = submit_response.json()
    assert payload["status"] == "queued"

    job_id = payload["job_id"]
    job_response = client.get(f"/v1/jobs/{job_id}")
    assert job_response.status_code == 200

    body = job_response.json()
    assert body["status"] == "succeeded"
    assert body["result"]["backend"] == "ghcr.io/example/mock-image-generator:latest"

    shared_view = job_store_module.RedisJobStore(job_store_module.get_redis_client()).get(job_id)
    assert shared_view is not None
    assert shared_view.status == JobStatus.SUCCEEDED


def test_ops_snapshot_reports_queue_and_active_job(client: TestClient) -> None:
    store = job_store_module.get_job_store()
    service = service_registry.resolve_for_capability("image.generate")

    first_job = JobRecord(
        job_id="job-1",
        capability="image.generate",
        requested_service_id=service.service_id,
        selected_service_id=service.service_id,
    )
    second_job = JobRecord(
        job_id="job-2",
        capability="image.generate",
        requested_service_id=service.service_id,
        selected_service_id=service.service_id,
    )
    store.enqueue(first_job)
    store.enqueue(second_job)
    store.wait_for_gpu_turn(first_job.job_id, service.service_id)

    response = client.get("/ops/runtime")
    assert response.status_code == 200
    snapshot = response.json()

    assert snapshot["queue"] == ["job-2"]
    assert snapshot["active_job_id"] == "job-1"
    assert snapshot["active_service_id"] == service.service_id


def test_gpu_queue_order_and_state_transitions() -> None:
    store = job_store_module.get_job_store()
    service = service_registry.resolve_for_capability("image.generate")

    first_job = JobRecord(
        job_id="job-1",
        capability="image.generate",
        requested_service_id=service.service_id,
        selected_service_id=service.service_id,
    )
    second_job = JobRecord(
        job_id="job-2",
        capability="image.generate",
        requested_service_id=service.service_id,
        selected_service_id=service.service_id,
    )
    store.enqueue(first_job)
    store.enqueue(second_job)

    first_thread = threading.Thread(
        target=orchestrator.run_image_generate_job,
        kwargs={"job_id": "job-1", "prompt": "first prompt", "service_id": service.service_id},
    )
    second_thread = threading.Thread(
        target=orchestrator.run_image_generate_job,
        kwargs={"job_id": "job-2", "prompt": "second prompt", "service_id": service.service_id},
    )

    first_thread.start()
    time.sleep(0.01)
    second_thread.start()
    time.sleep(0.02)

    second_state = store.get("job-2")
    snapshot = store.snapshot()

    assert second_state is not None
    assert second_state.status == JobStatus.WAITING_FOR_GPU
    assert snapshot.active_job_id == "job-1"
    assert snapshot.queue == ["job-2"]

    first_thread.join()
    second_thread.join()

    final_first = store.get("job-1")
    final_second = store.get("job-2")

    assert final_first is not None
    assert final_second is not None
    assert final_first.status == JobStatus.SUCCEEDED
    assert final_second.status == JobStatus.SUCCEEDED
    assert store.snapshot().active_job_id is None
