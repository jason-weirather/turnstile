from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from app.models.capability import CapabilityDefinition
from app.models.service import ServiceDescriptor, ServiceMode
from app.services.job_store import get_job_store
from app.services.runtime import (
    RuntimeController,
    get_runtime_controller,
    runtime_artifacts_payload,
)


@dataclass(frozen=True)
class AdapterExecutionResult:
    result_payload: dict[str, Any]
    container_id: str | None = None


class Adapter(Protocol):
    def execute(
        self,
        capability: CapabilityDefinition,
        service: ServiceDescriptor,
        payload: dict[str, Any],
        *,
        job_id: str = "",
    ) -> AdapterExecutionResult: ...


class NoopStubAdapter:
    def execute(
        self,
        capability: CapabilityDefinition,
        service: ServiceDescriptor,
        payload: dict[str, Any],
        *,
        job_id: str = "",
    ) -> AdapterExecutionResult:
        del job_id
        return AdapterExecutionResult(
            result_payload={
                "adapter": "noop_stub",
                "capability_id": capability.capability_id,
                "service_id": service.service_id,
                "echo": json.dumps(payload, sort_keys=True),
            }
        )


class HttpForwardJsonAdapter:
    def __init__(
        self,
        *,
        client_factory: Callable[..., httpx.Client] = httpx.Client,
        runtime_controller: RuntimeController | None = None,
    ) -> None:
        self._client_factory = client_factory
        self._runtime = runtime_controller or get_runtime_controller()

    def execute(
        self,
        capability: CapabilityDefinition,
        service: ServiceDescriptor,
        payload: dict[str, Any],
        *,
        job_id: str = "",
    ) -> AdapterExecutionResult:
        del capability
        container_id: str | None = None
        base_url = service.adapter_config.get("base_url")
        if service.mode == ServiceMode.WARM and base_url is None:
            warm_handle = self._runtime.ensure_warm_http_service(service)
            container_id = warm_handle.container_id
            base_url = warm_handle.base_url
            get_job_store().attach_container(job_id, container_id)
            get_job_store().touch_warm_service(service.service_id)
            simulated = self._runtime.simulate_http_request(service, payload, job_id)
            if simulated is not None:
                return AdapterExecutionResult(result_payload=simulated, container_id=container_id)
        elif isinstance(base_url, str):
            base_url = str(base_url)
        else:
            raise RuntimeError(f"Warm HTTP service '{service.service_id}' is missing a base URL")

        path = str(service.adapter_config.get("path", "/"))
        method = str(service.adapter_config.get("method", "POST"))
        timeout_s = float(service.adapter_config.get("timeout_s", 10.0))
        headers = {"X-Turnstile-Job-Id": job_id}
        with self._client_factory(base_url=str(base_url), timeout=timeout_s) as client:
            response = client.request(method=method, url=path, json=payload, headers=headers)
        response.raise_for_status()
        if service.mode == ServiceMode.WARM:
            get_job_store().touch_warm_service(service.service_id)

        result_payload: dict[str, Any]
        if "application/json" in response.headers.get("content-type", ""):
            result_payload = dict(response.json())
        else:
            result_payload = {"body": response.text}
        return AdapterExecutionResult(result_payload=result_payload, container_id=container_id)


class ContainerCommandAdapter:
    def __init__(self, runtime_controller: RuntimeController | None = None) -> None:
        self._runtime = runtime_controller or get_runtime_controller()

    def execute(
        self,
        capability: CapabilityDefinition,
        service: ServiceDescriptor,
        payload: dict[str, Any],
        *,
        job_id: str = "",
    ) -> AdapterExecutionResult:
        del capability
        execution = self._runtime.execute_container_command(service, payload, job_id)
        result_payload = execution.result_payload or self._normalize_stdout(execution.stdout)
        if execution.stderr:
            result_payload.setdefault("stderr", execution.stderr)
        if execution.artifacts:
            result_payload["artifacts"] = runtime_artifacts_payload(execution.artifacts)
        return AdapterExecutionResult(
            result_payload=result_payload,
            container_id=execution.container_id,
        )

    def _normalize_stdout(self, stdout: str) -> dict[str, Any]:
        if not stdout:
            return {}
        try:
            parsed = json.loads(stdout)
        except json.JSONDecodeError:
            return {"stdout": stdout}
        return dict(parsed) if isinstance(parsed, dict) else {"value": parsed}


class AdapterRegistry:
    def __init__(
        self,
        *,
        http_adapter: HttpForwardJsonAdapter | None = None,
        container_adapter: ContainerCommandAdapter | None = None,
    ) -> None:
        self._adapters: dict[str, Adapter] = {
            "noop_stub": NoopStubAdapter(),
            "http_forward_json": http_adapter or HttpForwardJsonAdapter(),
            "container_command": container_adapter or ContainerCommandAdapter(),
        }

    def get(self, adapter_type: str) -> Adapter:
        return self._adapters[adapter_type]


_adapter_registry = AdapterRegistry()


def get_adapter_registry() -> AdapterRegistry:
    return _adapter_registry
