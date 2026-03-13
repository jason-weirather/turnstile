from __future__ import annotations

import io
import json
import shutil
import tarfile
import tempfile
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, cast
from uuid import uuid4

import docker
import httpx
from docker.errors import DockerException, ImageNotFound, NotFound

from app.core.config import get_settings
from app.models.job import JobRecord
from app.models.ops import WarmServiceState
from app.models.service import ServiceDescriptor, ServiceMode
from app.services.job_store import get_job_store


@dataclass(frozen=True)
class RuntimeArtifact:
    name: str
    path: str
    size_bytes: int


@dataclass(frozen=True)
class EphemeralExecutionResult:
    container_id: str | None
    stdout: str
    stderr: str
    artifacts: list[RuntimeArtifact]
    result_payload: dict[str, object] | None = None


@dataclass(frozen=True)
class WarmServiceHandle:
    service_id: str
    container_id: str
    base_url: str
    reused: bool


class RuntimeController:
    def prepare_for_service(self, service: ServiceDescriptor) -> None:
        raise NotImplementedError

    def execute_container_command(
        self,
        service: ServiceDescriptor,
        payload: dict[str, object],
        job_id: str,
    ) -> EphemeralExecutionResult:
        raise NotImplementedError

    def ensure_warm_http_service(self, service: ServiceDescriptor) -> WarmServiceHandle:
        raise NotImplementedError

    def simulate_http_request(
        self,
        service: ServiceDescriptor,
        payload: dict[str, object],
        job_id: str,
    ) -> dict[str, object] | None:
        del service, payload, job_id
        return None

    def cancel_job(self, job: JobRecord, service: ServiceDescriptor) -> bool:
        raise NotImplementedError

    def docker_reachable(self) -> tuple[bool, str | None]:
        raise NotImplementedError


class StubRuntimeController(RuntimeController):
    def prepare_for_service(self, service: ServiceDescriptor) -> None:
        if service.mode == ServiceMode.WARM:
            now = datetime.now(timezone.utc)
            get_job_store().set_warm_service(
                WarmServiceState(
                    service_id=service.service_id,
                    container_id=f"stub-{service.service_id}",
                    base_url=f"http://stub/{service.service_id}",
                    gpu_required=service.gpu_required,
                    started_at=now,
                    last_used_at=now,
                    idle_ttl_s=service.idle_ttl_s,
                    expires_at=now + timedelta(seconds=service.idle_ttl_s),
                    status="running",
                )
            )

    def execute_container_command(
        self,
        service: ServiceDescriptor,
        payload: dict[str, object],
        job_id: str,
    ) -> EphemeralExecutionResult:
        result_payload: dict[str, object] = {
            "adapter": "container_command",
            "service_id": service.service_id,
            "job_id": job_id,
            "echo": payload,
        }
        return EphemeralExecutionResult(
            container_id=f"stub-{job_id}",
            stdout=json.dumps(result_payload, sort_keys=True),
            stderr="",
            artifacts=[],
            result_payload=result_payload,
        )

    def ensure_warm_http_service(self, service: ServiceDescriptor) -> WarmServiceHandle:
        self.prepare_for_service(service)
        return WarmServiceHandle(
            service_id=service.service_id,
            container_id=f"stub-{service.service_id}",
            base_url=f"http://stub/{service.service_id}",
            reused=True,
        )

    def simulate_http_request(
        self,
        service: ServiceDescriptor,
        payload: dict[str, object],
        job_id: str,
    ) -> dict[str, object] | None:
        del job_id
        return {
            "adapter": "http_forward_json",
            "service_id": service.service_id,
            "echo": payload,
        }

    def cancel_job(self, job: JobRecord, service: ServiceDescriptor) -> bool:
        del job, service
        return True

    def docker_reachable(self) -> tuple[bool, str | None]:
        return (True, "stub runtime")


class DockerRuntimeController(RuntimeController):
    def __init__(
        self,
        *,
        client_factory: Callable[[], docker.DockerClient] | None = None,
        http_client_factory: Callable[..., httpx.Client] = httpx.Client,
    ) -> None:
        self._settings = get_settings()
        self._client_factory = client_factory or self._default_client_factory
        self._http_client_factory = http_client_factory
        self._job_store = get_job_store()
        self._warm_threads: dict[str, tuple[threading.Thread, threading.Event]] = {}
        self._warm_lock = threading.Lock()
        self._docker_client: docker.DockerClient | None = None

    def prepare_for_service(self, service: ServiceDescriptor) -> None:
        self._evict_idle_services()
        if not service.gpu_required:
            return

        active_service_id = self._job_store.active_service_id()
        active_job_id = self._job_store.active_job_id()
        if (
            active_service_id is None
            or active_service_id == service.service_id
            or active_job_id is not None
        ):
            return
        if self._job_store.get_warm_service(active_service_id) is None:
            self._job_store.clear_active_service(active_service_id)
            return
        self.stop_warm_service(active_service_id)

    def execute_container_command(
        self,
        service: ServiceDescriptor,
        payload: dict[str, object],
        job_id: str,
    ) -> EphemeralExecutionResult:
        workspace = Path(tempfile.mkdtemp(prefix=f"turnstile-{job_id}-"))
        output_dir = workspace / "output"
        output_dir.mkdir(parents=True, exist_ok=True)

        container = None
        try:
            create_kwargs: dict[str, object] = {
                "command": service.adapter_config.get("command"),
                "detach": True,
                "auto_remove": False,
                "device_requests": self._device_requests(service),
                "environment": self._container_environment(service, payload, job_id),
                "labels": self._labels(
                    job_id=job_id, service_id=service.service_id, mode="ephemeral"
                ),
                "working_dir": service.adapter_config.get("working_dir"),
            }
            if self._settings.docker_network:
                create_kwargs["network"] = self._settings.docker_network

            container = self._create_container(service.image, **create_kwargs)
            self._seed_container_workspace(container, payload)
            self._job_store.attach_container(job_id, container.id)
            container.start()
            wait_timeout_s = int(service.adapter_config.get("timeout_s", service.startup_timeout_s))
            result = container.wait(timeout=wait_timeout_s)
            stdout = self._decode_logs(container.logs(stdout=True, stderr=False))
            stderr = self._decode_logs(container.logs(stdout=False, stderr=True))
            self._download_output_archive(container, output_dir)
            artifacts = self._collect_artifacts(output_dir)
            result_payload = self._read_result_payload(service, output_dir, stdout)
            if int(result.get("StatusCode", 1)) != 0:
                raise RuntimeError(stderr or stdout or "container execution failed")
            return EphemeralExecutionResult(
                container_id=container.id,
                stdout=stdout,
                stderr=stderr,
                artifacts=artifacts,
                result_payload=result_payload,
            )
        finally:
            if container is not None:
                self._remove_container(container.id)
            shutil.rmtree(workspace, ignore_errors=True)

    def ensure_warm_http_service(self, service: ServiceDescriptor) -> WarmServiceHandle:
        if "base_url" in service.adapter_config:
            return WarmServiceHandle(
                service_id=service.service_id,
                container_id="external",
                base_url=str(service.adapter_config["base_url"]),
                reused=True,
            )

        self.prepare_for_service(service)
        state = self._job_store.get_warm_service(service.service_id)
        if state is not None and self._container_running(state.container_id):
            self._job_store.touch_warm_service(service.service_id)
            return WarmServiceHandle(
                service_id=service.service_id,
                container_id=state.container_id,
                base_url=state.base_url,
                reused=True,
            )

        self.stop_warm_service(service.service_id)
        container_name = self._container_name(service.service_id)
        container_port = int(service.adapter_config.get("container_port", 8000))
        run_kwargs: dict[str, object] = {
            "command": service.adapter_config.get("command"),
            "detach": True,
            "auto_remove": False,
            "device_requests": self._device_requests(service),
            "environment": self._string_environment(service.adapter_config.get("env", {})),
            "labels": self._labels(service_id=service.service_id, mode="warm"),
            "name": container_name,
            "working_dir": service.adapter_config.get("working_dir"),
        }
        if self._settings.docker_network:
            run_kwargs["network"] = self._settings.docker_network
        else:
            run_kwargs["ports"] = {f"{container_port}/tcp": None}

        container = self._run_container(service.image, **run_kwargs)
        container.reload()
        base_url = self._warm_base_url(container, container_name, container_port)
        self._wait_for_readiness(service, base_url, container.id)

        now = datetime.now(timezone.utc)
        self._job_store.set_warm_service(
            WarmServiceState(
                service_id=service.service_id,
                container_id=container.id,
                base_url=base_url,
                gpu_required=service.gpu_required,
                started_at=now,
                last_used_at=now,
                idle_ttl_s=service.idle_ttl_s,
                expires_at=now + timedelta(seconds=service.idle_ttl_s),
                status="running",
            )
        )
        self._start_warm_service_heartbeat(service)
        return WarmServiceHandle(
            service_id=service.service_id,
            container_id=container.id,
            base_url=base_url,
            reused=False,
        )

    def cancel_job(self, job: JobRecord, service: ServiceDescriptor) -> bool:
        cancelled = False
        if service.mode == ServiceMode.EPHEMERAL and job.container_id is not None:
            cancelled = self._stop_container(job.container_id)

        if (
            service.mode == ServiceMode.WARM
            and service.adapter_config.get("cancel_path")
            and (warm_state := self._job_store.get_warm_service(service.service_id)) is not None
        ):
            cancel_path = str(service.adapter_config["cancel_path"])
            with self._http_client_factory(base_url=warm_state.base_url, timeout=5.0) as client:
                response = client.post(cancel_path, json={"job_id": job.job_id})
                response.raise_for_status()
                cancelled = True
        return cancelled

    def docker_reachable(self) -> tuple[bool, str | None]:
        try:
            self._client().ping()
        except DockerException as exc:
            return (False, str(exc))
        return (True, None)

    def stop_warm_service(self, service_id: str) -> None:
        with self._warm_lock:
            thread_state = self._warm_threads.pop(service_id, None)
        if thread_state is not None:
            thread, stop_event = thread_state
            stop_event.set()
            if thread is not threading.current_thread():
                thread.join(timeout=1.0)
        state = self._job_store.get_warm_service(service_id)
        if state is not None:
            self._stop_container(state.container_id)
        self._job_store.clear_warm_service(service_id)

    def _default_client_factory(self) -> docker.DockerClient:
        if self._settings.docker_host:
            return docker.DockerClient(base_url=self._settings.docker_host)
        return docker.from_env()

    def _client(self) -> docker.DockerClient:
        if self._docker_client is None:
            self._docker_client = self._client_factory()
        return self._docker_client

    def _create_container(self, image: str, **kwargs: object) -> Any:
        try:
            return self._client().containers.create(image, **kwargs)
        except ImageNotFound:
            self._client().images.pull(image)
            return self._client().containers.create(image, **kwargs)

    def _run_container(self, image: str, **kwargs: object) -> Any:
        try:
            return self._client().containers.run(image, **kwargs)
        except ImageNotFound:
            self._client().images.pull(image)
            return self._client().containers.run(image, **kwargs)

    def _container_environment(
        self,
        service: ServiceDescriptor,
        payload: dict[str, object],
        job_id: str,
    ) -> dict[str, str]:
        environment = self._string_environment(service.adapter_config.get("env", {}))
        environment.update(
            {
                "TURNSTILE_JOB_ID": job_id,
                "TURNSTILE_REQUEST_JSON": json.dumps(payload),
                "TURNSTILE_REQUEST_FILE": "/turnstile/input/request.json",
                "TURNSTILE_OUTPUT_DIR": "/turnstile/output",
            }
        )
        return environment

    def _string_environment(self, raw: object) -> dict[str, str]:
        if not isinstance(raw, dict):
            return {}
        return {str(key): str(value) for key, value in raw.items()}

    def _device_requests(self, service: ServiceDescriptor) -> list[object] | None:
        if not service.gpu_required:
            return None
        return [docker.types.DeviceRequest(count=-1, capabilities=[["gpu"]])]

    def _seed_container_workspace(self, container: Any, payload: dict[str, object]) -> None:
        archive = self._build_workspace_archive(payload)
        uploaded = container.put_archive("/", archive)
        if uploaded is False:
            raise RuntimeError("Failed to upload request payload into container workspace")

    def _build_workspace_archive(self, payload: dict[str, object]) -> bytes:
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w") as archive:
            for directory in ("turnstile", "turnstile/input", "turnstile/output"):
                info = tarfile.TarInfo(name=directory)
                info.type = tarfile.DIRTYPE
                info.mode = 0o755
                archive.addfile(info)

            request_bytes = json.dumps(payload).encode("utf-8")
            request_info = tarfile.TarInfo(name="turnstile/input/request.json")
            request_info.size = len(request_bytes)
            request_info.mode = 0o644
            archive.addfile(request_info, io.BytesIO(request_bytes))
        return buffer.getvalue()

    def _download_output_archive(self, container: Any, output_dir: Path) -> None:
        try:
            stream, _ = container.get_archive("/turnstile/output")
        except NotFound:
            return

        archive_bytes = b"".join(
            chunk if isinstance(chunk, bytes) else bytes(chunk) for chunk in stream
        )
        if not archive_bytes:
            return

        staging_dir = output_dir.parent / "_downloaded_output"
        staging_dir.mkdir(parents=True, exist_ok=True)
        try:
            with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:*") as archive:
                self._safe_extract_archive(archive, staging_dir)
            source_dir = self._resolve_output_archive_root(staging_dir)
            if source_dir is None:
                return
            self._copy_directory_contents(source_dir, output_dir)
        finally:
            shutil.rmtree(staging_dir, ignore_errors=True)

    def _safe_extract_archive(self, archive: tarfile.TarFile, target_dir: Path) -> None:
        target_root = target_dir.resolve()
        for member in archive.getmembers():
            member_path = (target_dir / member.name).resolve()
            if target_root not in member_path.parents and member_path != target_root:
                raise RuntimeError("Refusing to extract Docker output outside the workspace")
        archive.extractall(target_dir, filter="data")

    def _resolve_output_archive_root(self, extracted_dir: Path) -> Path | None:
        for candidate in (
            extracted_dir / "output",
            extracted_dir / "turnstile" / "output",
        ):
            if candidate.exists():
                return candidate
        if any(extracted_dir.iterdir()):
            return extracted_dir
        return None

    def _copy_directory_contents(self, source_dir: Path, output_dir: Path) -> None:
        for path in source_dir.rglob("*"):
            relative_path = path.relative_to(source_dir)
            destination = output_dir / relative_path
            if path.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)

    def _labels(self, *, service_id: str, mode: str, job_id: str | None = None) -> dict[str, str]:
        prefix = self._settings.docker_label_prefix
        labels = {
            f"{prefix}.managed": "true",
            f"{prefix}.service_id": service_id,
            f"{prefix}.mode": mode,
        }
        if job_id is not None:
            labels[f"{prefix}.job_id"] = job_id
        return labels

    def _container_name(self, service_id: str) -> str:
        normalized = service_id.replace(".", "-").replace("_", "-")
        return f"{self._settings.docker_label_prefix}-{normalized}-{uuid4().hex[:8]}"

    def _warm_base_url(self, container: Any, container_name: str, container_port: int) -> str:
        if self._settings.docker_network:
            return f"http://{container_name}:{container_port}"
        host_port = str(
            cast(
                dict[str, list[dict[str, str]]],
                getattr(container, "attrs", {}).get("NetworkSettings", {}).get("Ports", {}),
            )[f"{container_port}/tcp"][0]["HostPort"]
        )
        return f"http://{self._settings.docker_service_host}:{host_port}"

    def _wait_for_readiness(
        self,
        service: ServiceDescriptor,
        base_url: str,
        container_id: str,
    ) -> None:
        healthcheck = service.healthcheck or {"type": "none"}
        check_type = str(healthcheck.get("type", "none"))
        if check_type == "none":
            return

        deadline = time.monotonic() + service.startup_timeout_s
        if check_type == "docker":
            while time.monotonic() < deadline:
                container = self._get_container(container_id)
                if container is None:
                    raise RuntimeError(f"Warm service container '{container_id}' disappeared")
                status = (
                    getattr(container, "attrs", {}).get("State", {}).get("Health", {}).get("Status")
                )
                if status == "healthy":
                    return
                time.sleep(self._settings.warm_probe_interval_s)
            raise RuntimeError(f"Timed out waiting for Docker health on '{service.service_id}'")

        if check_type == "http":
            method = str(healthcheck.get("method", "GET"))
            path = str(healthcheck.get("path", "/healthz"))
            expected_status = int(healthcheck.get("expected_status", 200))
            timeout_s = float(healthcheck.get("timeout_s", 5.0))
            while time.monotonic() < deadline:
                try:
                    with self._http_client_factory(base_url=base_url, timeout=timeout_s) as client:
                        response = client.request(method=method, url=path)
                    if response.status_code == expected_status:
                        return
                except httpx.HTTPError:
                    pass
                time.sleep(self._settings.warm_probe_interval_s)
            raise RuntimeError(f"Timed out waiting for warm HTTP service '{service.service_id}'")

        raise RuntimeError(f"Unsupported healthcheck type '{check_type}'")

    def _start_warm_service_heartbeat(self, service: ServiceDescriptor) -> None:
        with self._warm_lock:
            if service.service_id in self._warm_threads:
                return
            stop_event = threading.Event()
            thread = threading.Thread(
                target=self._warm_service_heartbeat,
                name=f"warm-service-{service.service_id}",
                kwargs={"service": service, "stop_event": stop_event},
                daemon=True,
            )
            self._warm_threads[service.service_id] = (thread, stop_event)
            thread.start()

    def _warm_service_heartbeat(
        self,
        service: ServiceDescriptor,
        stop_event: threading.Event,
    ) -> None:
        interval = max(0.2, self._settings.runtime_heartbeat_interval_s)
        while not stop_event.wait(interval):
            state = self._job_store.get_warm_service(service.service_id)
            if state is None:
                return
            if not self._container_running(state.container_id):
                self._job_store.clear_warm_service(service.service_id)
                return
            if (
                self._job_store.active_job_id() is None
                and datetime.now(timezone.utc) >= state.expires_at
            ):
                self.stop_warm_service(service.service_id)
                return
            self._job_store.renew_warm_service_lease(service.service_id)

    def _evict_idle_services(self) -> None:
        for state in self._job_store.list_warm_services():
            if (
                datetime.now(timezone.utc) >= state.expires_at
                and self._job_store.active_job_id() is None
            ):
                self.stop_warm_service(state.service_id)

    def _read_result_payload(
        self,
        service: ServiceDescriptor,
        output_dir: Path,
        stdout: str,
    ) -> dict[str, object] | None:
        result_file = service.adapter_config.get("result_file")
        if isinstance(result_file, str):
            path = output_dir / result_file
            if path.exists():
                loaded = json.loads(path.read_text(encoding="utf-8"))
                return cast(dict[str, object], loaded)

        if not stdout.strip():
            return None
        try:
            parsed = json.loads(stdout)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else {"value": parsed}

    def _collect_artifacts(self, output_dir: Path) -> list[RuntimeArtifact]:
        artifacts: list[RuntimeArtifact] = []
        for path in sorted(output_dir.rglob("*")):
            if path.is_dir():
                continue
            artifacts.append(
                RuntimeArtifact(
                    name=str(path.relative_to(output_dir)),
                    path=str(path),
                    size_bytes=path.stat().st_size,
                )
            )
        return artifacts

    def _container_running(self, container_id: str) -> bool:
        container = self._get_container(container_id)
        if container is None:
            return False
        container.reload()
        return getattr(container, "status", None) == "running"

    def _get_container(self, container_id: str) -> Any | None:
        try:
            return self._client().containers.get(container_id)
        except NotFound:
            return None

    def _stop_container(self, container_id: str) -> bool:
        container = self._get_container(container_id)
        if container is None:
            return False
        try:
            container.stop(timeout=1)
        except DockerException:
            pass
        self._remove_container(container_id)
        return True

    def _remove_container(self, container_id: str) -> None:
        container = self._get_container(container_id)
        if container is None:
            return
        try:
            container.remove(force=True)
        except DockerException:
            pass

    def _decode_logs(self, raw: object) -> str:
        if isinstance(raw, bytes):
            return raw.decode("utf-8", errors="replace").strip()
        return str(raw).strip()


@lru_cache(maxsize=1)
def get_runtime_controller() -> RuntimeController:
    if get_settings().runtime_mode == "docker":
        return DockerRuntimeController()
    return StubRuntimeController()


def runtime_artifacts_payload(artifacts: list[RuntimeArtifact]) -> list[dict[str, object]]:
    return [asdict(artifact) for artifact in artifacts]
