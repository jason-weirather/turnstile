from __future__ import annotations

import threading
import time

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.capability import QueueLane
from app.models.job import JobRecord, JobStatus
from app.services import job_store as job_store_module
from app.services import orchestrator
from app.services.registry import get_service_registry


def test_shared_status_lookup_uses_redis_backing() -> None:
    client = TestClient(app)

    submit_response = client.post(
        "/v1/example/command/run",
        json={"text": "status check", "artifact_name": "status.txt"},
    )
    job_id = submit_response.json()["job_id"]

    shared_view = job_store_module.RedisJobStore(job_store_module.get_redis_client()).get(job_id)
    assert shared_view is not None
    assert shared_view.status == JobStatus.SUCCEEDED


def test_gpu_queue_order_and_state_transitions() -> None:
    store = job_store_module.get_job_store()
    service = get_service_registry().resolve_for_capability("example.http.echo")

    first_job = JobRecord(
        job_id="job-1",
        capability="example.http.echo",
        queue_lane=QueueLane.GPU,
        requested_service_id=service.service_id,
        selected_service_id=service.service_id,
        request_payload={"text": "first"},
    )
    second_job = JobRecord(
        job_id="job-2",
        capability="example.http.echo",
        queue_lane=QueueLane.GPU,
        requested_service_id=service.service_id,
        selected_service_id=service.service_id,
        request_payload={"text": "second"},
    )
    store.enqueue(first_job)
    store.enqueue(second_job)

    first_thread = threading.Thread(
        target=orchestrator.run_capability_job,
        kwargs={
            "job_id": "job-1",
            "capability_id": "example.http.echo",
            "payload": {"text": "first"},
            "service_id": service.service_id,
        },
    )
    second_thread = threading.Thread(
        target=orchestrator.run_capability_job,
        kwargs={
            "job_id": "job-2",
            "capability_id": "example.http.echo",
            "payload": {"text": "second"},
            "service_id": service.service_id,
        },
    )

    first_thread.start()
    time.sleep(0.01)
    second_thread.start()
    time.sleep(0.02)

    second_state = store.get("job-2")
    gpu_queue = next(queue for queue in store.queue_snapshots(["gpu"]) if queue.lane == "gpu")

    assert second_state is not None
    assert second_state.status == JobStatus.WAITING_FOR_GPU
    assert gpu_queue.active_job_id == "job-1"
    assert gpu_queue.queued_job_ids == ["job-2"]

    first_thread.join()
    second_thread.join()

    final_first = store.get("job-1")
    final_second = store.get("job-2")
    assert final_first is not None
    assert final_second is not None
    assert final_first.status == JobStatus.SUCCEEDED
    assert final_second.status == JobStatus.SUCCEEDED


def test_cancel_queued_job() -> None:
    from app.tasks import execute_capability_task

    execute_capability_task.app.conf.task_always_eager = False
    execute_capability_task.app.conf.task_store_eager_result = False
    execute_capability_task.app.conf.broker_url = "memory://"

    client = TestClient(app)
    submit_response = client.post(
        "/v1/example/http/echo",
        json={"text": "cancel me"},
    )
    job_id = submit_response.json()["job_id"]

    cancel_response = client.post(f"/v1/jobs/{job_id}/cancel")
    assert cancel_response.status_code == 200
    assert cancel_response.json()["status"] == "cancelled"

    job_response = client.get(f"/v1/jobs/{job_id}")
    assert job_response.status_code == 200
    assert job_response.json()["status"] == "cancelled"


def test_cancel_queued_jobs_for_lane_via_ops_endpoint() -> None:
    store = job_store_module.get_job_store()
    service = get_service_registry().resolve_for_capability("example.http.echo")
    store.enqueue(
        JobRecord(
            job_id="job-stuck",
            capability="example.http.echo",
            queue_lane=QueueLane.GPU,
            requested_service_id=service.service_id,
            selected_service_id=service.service_id,
            request_payload={"text": "stuck"},
        )
    )

    client = TestClient(app)
    response = client.post("/ops/queues/gpu/cancel")

    assert response.status_code == 200
    assert response.json() == {
        "lane": "gpu",
        "cancelled_count": 1,
        "cancelled_job_ids": ["job-stuck"],
    }

    job_response = client.get("/v1/jobs/job-stuck")
    assert job_response.status_code == 200
    assert job_response.json()["status"] == "cancelled"


def test_failed_example_command_job_sets_failed_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = job_store_module.get_job_store()
    service = get_service_registry().resolve_for_capability(
        "example.command.run",
        service_id="mock-command-alpha",
    )
    job = JobRecord(
        job_id="job-fail",
        capability="example.command.run",
        queue_lane=QueueLane.CPU,
        requested_service_id=service.service_id,
        selected_service_id=service.service_id,
        request_payload={"fail": True},
    )
    store.enqueue(job)

    class FailingAdapter:
        def execute(self, *args: object, **kwargs: object) -> object:
            del args, kwargs
            raise RuntimeError("forced failure")

    class Registry:
        def get(self, adapter_type: str) -> FailingAdapter:
            assert adapter_type == service.adapter_type.value
            return FailingAdapter()

    monkeypatch.setattr(orchestrator, "get_adapter_registry", lambda: Registry())

    with pytest.raises(RuntimeError, match="forced failure"):
        orchestrator.run_capability_job(
            job_id="job-fail",
            capability_id="example.command.run",
            payload={"fail": True},
            service_id=service.service_id,
        )

    failed_job = store.get("job-fail")
    assert failed_job is not None
    assert failed_job.status == JobStatus.FAILED
    assert failed_job.error_code == "execution_failed"
