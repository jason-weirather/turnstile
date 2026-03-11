from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from app.models.capability import CapabilityDefinition
from app.models.service import ServiceDescriptor


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
    ) -> AdapterExecutionResult: ...


class NoopStubAdapter:
    def execute(
        self,
        capability: CapabilityDefinition,
        service: ServiceDescriptor,
        payload: dict[str, Any],
    ) -> AdapterExecutionResult:
        return AdapterExecutionResult(
            result_payload={
                "adapter": "noop_stub",
                "capability_id": capability.capability_id,
                "service_id": service.service_id,
                "echo": json.dumps(payload, sort_keys=True),
            }
        )


class HttpForwardJsonAdapter:
    def __init__(self, client_factory: Callable[..., httpx.Client] = httpx.Client) -> None:
        self._client_factory = client_factory

    def execute(
        self,
        capability: CapabilityDefinition,
        service: ServiceDescriptor,
        payload: dict[str, Any],
    ) -> AdapterExecutionResult:
        del capability
        base_url = str(service.adapter_config["base_url"])
        path = str(service.adapter_config.get("path", "/"))
        method = str(service.adapter_config.get("method", "POST"))
        timeout_s = float(service.adapter_config.get("timeout_s", 10.0))
        with self._client_factory(base_url=base_url, timeout=timeout_s) as client:
            response = client.request(method=method, url=path, json=payload)
        response.raise_for_status()
        result_payload: dict[str, Any]
        if "application/json" in response.headers.get("content-type", ""):
            result_payload = dict(response.json())
        else:
            result_payload = {"body": response.text}
        return AdapterExecutionResult(result_payload=result_payload)


class ContainerCommandAdapter:
    def execute(
        self,
        capability: CapabilityDefinition,
        service: ServiceDescriptor,
        payload: dict[str, Any],
    ) -> AdapterExecutionResult:
        del capability
        command = [str(part) for part in service.adapter_config["command"]]
        timeout_s = float(service.adapter_config.get("timeout_s", 10.0))
        process = subprocess.run(
            command,
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=True,
        )
        stdout = process.stdout.strip()
        if not stdout:
            result_payload: dict[str, Any] = {}
        else:
            try:
                result_payload = dict(json.loads(stdout))
            except json.JSONDecodeError:
                result_payload = {"stdout": stdout}
        return AdapterExecutionResult(result_payload=result_payload)


class AdapterRegistry:
    def __init__(self, http_adapter: HttpForwardJsonAdapter | None = None) -> None:
        self._adapters: dict[str, Adapter] = {
            "noop_stub": NoopStubAdapter(),
            "http_forward_json": http_adapter or HttpForwardJsonAdapter(),
            "container_command": ContainerCommandAdapter(),
        }

    def get(self, adapter_type: str) -> Adapter:
        return self._adapters[adapter_type]


_adapter_registry = AdapterRegistry()


def get_adapter_registry() -> AdapterRegistry:
    return _adapter_registry
