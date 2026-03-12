from __future__ import annotations

import io
import json
import tarfile
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from docker.errors import ImageNotFound

from app.models.capability import AdapterType, QueueLane
from app.models.job import JobRecord
from app.models.ops import WarmServiceState
from app.models.service import ServiceDescriptor, ServiceMode
from app.services.job_store import get_job_store
from app.services.orchestrator import GpuLeaseHeartbeat
from app.services.runtime import DockerRuntimeController, WarmServiceHandle


class FakeContainer:
    def __init__(
        self,
        container_id: str,
        *,
        status_code: int = 0,
        stdout: str = "",
        stderr: str = "",
        host_port: str = "18000",
    ) -> None:
        self.id = container_id
        self.status = "created"
        self._status_code = status_code
        self._stdout = stdout
        self._stderr = stderr
        self.stopped = False
        self.removed = False
        self.started = False
        self.put_archive_calls: list[tuple[str, bytes]] = []
        self.get_archive_calls: list[str] = []
        self._output_archive = _build_archive(
            {
                "output/result.json": json.dumps({"text": "runtime-result", "language": "en"}),
                "output/artifact.txt": "artifact",
            }
        )
        self.attrs = {
            "NetworkSettings": {"Ports": {"8000/tcp": [{"HostPort": host_port}]}},
            "State": {"Health": {"Status": "healthy"}},
        }

    def start(self) -> None:
        self.started = True
        self.status = "running"

    def wait(self, timeout: int) -> dict[str, int]:
        del timeout
        self.status = "exited"
        return {"StatusCode": self._status_code}

    def logs(self, *, stdout: bool, stderr: bool) -> bytes:
        if stdout and not stderr:
            return self._stdout.encode()
        if stderr and not stdout:
            return self._stderr.encode()
        return b""

    def reload(self) -> None:
        return None

    def stop(self, timeout: int) -> None:
        del timeout
        self.stopped = True
        self.status = "exited"

    def remove(self, force: bool) -> None:
        del force
        self.removed = True

    def put_archive(self, path: str, data: bytes) -> bool:
        self.put_archive_calls.append((path, data))
        return True

    def get_archive(self, path: str) -> tuple[list[bytes], dict[str, str]]:
        self.get_archive_calls.append(path)
        return [self._output_archive], {}


class FakeContainerCollection:
    def __init__(self, *, missing_images: set[str] | None = None) -> None:
        self._containers: dict[str, FakeContainer] = {}
        self.run_calls: list[dict[str, Any]] = []
        self.create_calls: list[dict[str, Any]] = []
        self._missing_images = missing_images or set()

    def run(self, image: str, **kwargs: Any) -> FakeContainer:
        if image in self._missing_images:
            raise ImageNotFound(f"missing image: {image}")
        container_id = f"container-{len(self._containers) + 1}"
        container = FakeContainer(
            container_id,
            stdout=json.dumps({"text": "stdout-result"}),
        )
        container.start()
        self._containers[container_id] = container
        self.run_calls.append({"image": image, **kwargs})
        return container

    def create(self, image: str, **kwargs: Any) -> FakeContainer:
        if image in self._missing_images:
            raise ImageNotFound(f"missing image: {image}")
        container_id = f"container-{len(self._containers) + 1}"
        container = FakeContainer(
            container_id,
            stdout=json.dumps({"text": "stdout-result"}),
        )
        self._containers[container_id] = container
        self.create_calls.append({"image": image, **kwargs})
        return container

    def get(self, container_id: str) -> FakeContainer:
        return self._containers[container_id]


class FakeDockerClient:
    def __init__(self, containers: FakeContainerCollection | None = None) -> None:
        self.containers = containers or FakeContainerCollection()
        self.images = FakeImageCollection(self.containers)

    def ping(self) -> bool:
        return True


class FakeImageCollection:
    def __init__(self, containers: FakeContainerCollection) -> None:
        self._containers = containers
        self.pull_calls: list[str] = []

    def pull(self, image: str) -> None:
        self.pull_calls.append(image)
        self._containers._missing_images.discard(image)


def _build_archive(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for path, content in files.items():
            data = content.encode("utf-8")
            info = tarfile.TarInfo(name=path)
            info.size = len(data)
            info.mode = 0o644
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def _archive_texts(raw_archive: bytes) -> dict[str, str]:
    extracted: dict[str, str] = {}
    with tarfile.open(fileobj=io.BytesIO(raw_archive), mode="r:*") as archive:
        for member in archive.getmembers():
            handle = archive.extractfile(member)
            if handle is None:
                continue
            extracted[member.name] = handle.read().decode("utf-8")
    return extracted


def test_warm_http_service_lifecycle_reuses_and_evicts_conflicts() -> None:
    controller = DockerRuntimeController(client_factory=lambda: FakeDockerClient())
    store = get_job_store()
    warm_service = ServiceDescriptor(
        service_id="warm-image",
        capabilities=["image.generate"],
        image="python:3.12-slim",
        mode=ServiceMode.WARM,
        gpu_required=True,
        estimated_vram_mb=4096,
        startup_timeout_s=5,
        idle_ttl_s=60,
        healthcheck={"type": "none"},
        adapter_type=AdapterType.HTTP_FORWARD_JSON,
        adapter_config={"container_port": 8000},
        cancel_strategy="http_request_cancel",
        eviction_priority=10,
    )
    conflicting_service = warm_service.model_copy(
        update={"service_id": "warm-other", "capabilities": ["image.edit"]}
    )

    first_handle = controller.ensure_warm_http_service(warm_service)
    second_handle = controller.ensure_warm_http_service(warm_service)

    assert isinstance(first_handle, WarmServiceHandle)
    assert first_handle.container_id == second_handle.container_id
    assert first_handle.base_url.startswith("http://127.0.0.1:")
    assert store.get_warm_service("warm-image") is not None
    assert controller._client().containers.run_calls[0]["device_requests"] is not None

    controller.prepare_for_service(conflicting_service)

    assert store.get_warm_service("warm-image") is None


def test_ephemeral_docker_job_lifecycle_collects_outputs_and_artifacts() -> None:
    fake_client = FakeDockerClient()
    controller = DockerRuntimeController(client_factory=lambda: fake_client)
    service = ServiceDescriptor(
        service_id="ephemeral-audio",
        capabilities=["audio.transcribe"],
        image="python:3.12-slim",
        mode=ServiceMode.EPHEMERAL,
        gpu_required=False,
        estimated_vram_mb=0,
        startup_timeout_s=5,
        idle_ttl_s=60,
        healthcheck={"type": "none"},
        adapter_type=AdapterType.CONTAINER_COMMAND,
        adapter_config={
            "command": ["python", "-c", "print('ok')"],
            "result_file": "result.json",
        },
        cancel_strategy="container_stop",
        eviction_priority=20,
    )

    result = controller.execute_container_command(
        service,
        {"audio_url": "https://example.com/file.wav"},
        "job-ephemeral",
    )

    assert result.container_id == "container-1"
    assert result.result_payload == {"text": "runtime-result", "language": "en"}
    assert [artifact.name for artifact in result.artifacts] == ["artifact.txt", "result.json"]
    create_call = fake_client.containers.create_calls[0]
    assert "volumes" not in create_call
    container = fake_client.containers.get("container-1")
    assert container.started is True
    assert container.get_archive_calls == ["/turnstile/output"]
    uploaded_archive = _archive_texts(container.put_archive_calls[0][1])
    assert container.put_archive_calls[0][0] == "/"
    assert json.loads(uploaded_archive["turnstile/input/request.json"]) == {
        "audio_url": "https://example.com/file.wav"
    }


def test_runtime_pulls_missing_image_before_ephemeral_execution() -> None:
    containers = FakeContainerCollection(missing_images={"python:3.12-slim"})
    fake_client = FakeDockerClient(containers=containers)
    controller = DockerRuntimeController(client_factory=lambda: fake_client)
    service = ServiceDescriptor(
        service_id="ephemeral-audio",
        capabilities=["audio.transcribe"],
        image="python:3.12-slim",
        mode=ServiceMode.EPHEMERAL,
        gpu_required=False,
        estimated_vram_mb=0,
        startup_timeout_s=5,
        idle_ttl_s=60,
        healthcheck={"type": "none"},
        adapter_type=AdapterType.CONTAINER_COMMAND,
        adapter_config={},
        cancel_strategy="container_stop",
        eviction_priority=20,
    )

    controller.execute_container_command(
        service,
        {"audio_url": "https://example.com/file.wav"},
        "job-ephemeral",
    )

    assert fake_client.images.pull_calls == ["python:3.12-slim"]


def test_runtime_cancellation_stops_ephemeral_container_and_supports_warm_cancel() -> None:
    containers = FakeContainerCollection()
    fake_client = FakeDockerClient(containers=containers)
    cancel_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        cancel_requests.append(request)
        return httpx.Response(202, json={"status": "cancelling"})

    transport = httpx.MockTransport(handler)

    def client_factory(**kwargs: Any) -> httpx.Client:
        return httpx.Client(transport=transport, **kwargs)

    controller = DockerRuntimeController(
        client_factory=lambda: fake_client,
        http_client_factory=client_factory,
    )
    store = get_job_store()

    ephemeral = ServiceDescriptor(
        service_id="ephemeral-audio",
        capabilities=["audio.transcribe"],
        image="python:3.12-slim",
        mode=ServiceMode.EPHEMERAL,
        gpu_required=False,
        estimated_vram_mb=0,
        startup_timeout_s=5,
        idle_ttl_s=60,
        healthcheck={"type": "none"},
        adapter_type=AdapterType.CONTAINER_COMMAND,
        adapter_config={},
        cancel_strategy="container_stop",
        eviction_priority=20,
    )
    warm = ServiceDescriptor(
        service_id="warm-image",
        capabilities=["image.generate"],
        image="python:3.12-slim",
        mode=ServiceMode.WARM,
        gpu_required=True,
        estimated_vram_mb=4096,
        startup_timeout_s=5,
        idle_ttl_s=60,
        healthcheck={"type": "none"},
        adapter_type=AdapterType.HTTP_FORWARD_JSON,
        adapter_config={"cancel_path": "/cancel"},
        cancel_strategy="http_request_cancel",
        eviction_priority=10,
    )

    ephemeral_container = containers.run(ephemeral.image)
    ephemeral_job = JobRecord(
        job_id="job-running",
        capability="audio.transcribe",
        queue_lane=QueueLane.CPU,
        requested_service_id=ephemeral.service_id,
        selected_service_id=ephemeral.service_id,
        request_payload={},
        container_id=ephemeral_container.id,
    )

    warm_state = WarmServiceState(
        service_id=warm.service_id,
        container_id="warm-container",
        base_url="http://warm-service.test",
        gpu_required=True,
        started_at=datetime.now(timezone.utc),
        last_used_at=datetime.now(timezone.utc),
        idle_ttl_s=60,
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=60),
        status="running",
    )
    store.set_warm_service(warm_state)
    warm_job = JobRecord(
        job_id="job-warm",
        capability="image.generate",
        queue_lane=QueueLane.GPU,
        requested_service_id=warm.service_id,
        selected_service_id=warm.service_id,
        request_payload={},
        container_id="warm-container",
    )

    assert controller.cancel_job(ephemeral_job, ephemeral) is True
    assert containers.get(ephemeral_container.id).stopped is True
    assert controller.cancel_job(warm_job, warm) is True
    assert cancel_requests[0].url.path == "/cancel"


class SpyJobStore:
    def __init__(self) -> None:
        self.renew_calls = 0

    def renew_gpu_job_lease(self, job_id: str, service_id: str) -> None:
        del job_id, service_id
        self.renew_calls += 1


def test_gpu_lease_heartbeat_renews_lease() -> None:
    store = SpyJobStore()
    heartbeat = GpuLeaseHeartbeat(job_store=store, job_id="job-1", service_id="service-1")

    heartbeat.start()
    time.sleep(0.25)
    heartbeat.stop()

    assert store.renew_calls >= 1
