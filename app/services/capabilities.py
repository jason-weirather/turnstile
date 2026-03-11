from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, cast

from pydantic import BaseModel, Field, create_model

from app.models.capability import CapabilityDefinition
from app.services.definition_loader import DefinitionLoader


class CapabilityRegistry:
    def __init__(self, capabilities: list[CapabilityDefinition]) -> None:
        self._capabilities = {capability.capability_id: capability for capability in capabilities}
        self._request_models: dict[str, type[BaseModel]] = {}
        self._response_models: dict[str, type[BaseModel]] = {}

    def list_capabilities(self) -> list[CapabilityDefinition]:
        return list(self._capabilities.values())

    def get(self, capability_id: str) -> CapabilityDefinition:
        return self._capabilities[capability_id]

    def get_request_model(self, capability_id: str) -> type[BaseModel]:
        capability = self.get(capability_id)
        if capability_id not in self._request_models:
            schema = _load_json(capability.request_schema)
            self._request_models[capability_id] = _build_model_from_schema(
                f"{_model_name(capability.capability_id)}Request",
                schema,
            )
        return self._request_models[capability_id]

    def get_response_model(self, capability_id: str) -> type[BaseModel]:
        capability = self.get(capability_id)
        if capability_id not in self._response_models:
            schema = _load_json(capability.response_schema)
            self._response_models[capability_id] = _build_model_from_schema(
                f"{_model_name(capability.capability_id)}Response",
                schema,
            )
        return self._response_models[capability_id]


@lru_cache(maxsize=1)
def get_capability_registry() -> CapabilityRegistry:
    loader = DefinitionLoader(
        capabilities_dir=_repo_root() / "config" / "capabilities",
        services_dir=_repo_root() / "config" / "services",
        schemas_dir=_repo_root() / "config" / "schemas",
    )
    return CapabilityRegistry(loader.load_capabilities())


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _load_json(path: Path) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))


def _build_model_from_schema(name: str, schema: dict[str, Any]) -> type[BaseModel]:
    if schema.get("type") != "object":
        raise ValueError(f"{name} schema must define an object")

    required = set(schema.get("required", []))
    properties = schema.get("properties", {})
    fields: dict[str, Any] = {}
    for field_name, field_schema in properties.items():
        annotation = _annotation_for_schema(field_schema)
        is_required = field_name in required
        default: Any
        if is_required:
            default = Field(...)
        else:
            annotation = annotation | None
            default = None
        fields[field_name] = (annotation, default)
    return cast(type[BaseModel], create_model(name, **fields))


def _annotation_for_schema(schema: dict[str, Any]) -> Any:
    schema_type = schema.get("type")
    if schema_type == "string":
        return str
    if schema_type == "integer":
        return int
    if schema_type == "number":
        return float
    if schema_type == "boolean":
        return bool
    if schema_type == "array":
        return list[Any]
    if schema_type == "object":
        return dict[str, Any]
    raise ValueError(f"Unsupported schema type: {schema_type!r}")


def _model_name(capability_id: str) -> str:
    return "".join(part.capitalize() for part in capability_id.replace("-", "_").split("."))
