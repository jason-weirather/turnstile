from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml
from jsonschema import ValidationError
from jsonschema.validators import validator_for

from app.models.capability import CapabilityDefinition
from app.models.service import ServiceDescriptor


class DefinitionLoader:
    def __init__(
        self,
        capabilities_dir: Path,
        services_dir: Path,
        schemas_dir: Path,
    ) -> None:
        self._capabilities_dir = capabilities_dir
        self._services_dir = services_dir
        self._schemas_dir = schemas_dir
        self._capability_schema = self._load_json(self._schemas_dir / "capability.schema.json")
        self._service_schema = self._load_json(self._schemas_dir / "service.schema.json")

    def load_capabilities(self) -> list[CapabilityDefinition]:
        capabilities: list[CapabilityDefinition] = []
        for path in sorted(self._capabilities_dir.glob("*.yaml")):
            raw = self._load_yaml(path)
            self._validate_document(raw, self._capability_schema, path)
            self._validate_schema_file(path.parent.parent / raw["request_schema"])
            self._validate_schema_file(path.parent.parent / raw["response_schema"])
            capabilities.append(
                CapabilityDefinition.model_validate(
                    {
                        **raw,
                        "request_schema": path.parent.parent / raw["request_schema"],
                        "response_schema": path.parent.parent / raw["response_schema"],
                    }
                )
            )
        return capabilities

    def load_services(self) -> list[ServiceDescriptor]:
        services: list[ServiceDescriptor] = []
        for path in sorted(self._services_dir.glob("*.yaml")):
            raw = self._load_yaml(path)
            self._validate_document(raw, self._service_schema, path)
            services.append(ServiceDescriptor.model_validate(raw))
        return services

    def _validate_document(self, raw: dict[str, Any], schema: dict[str, Any], path: Path) -> None:
        validator_cls = validator_for(schema)
        validator_cls.check_schema(schema)
        validator = validator_cls(schema)
        errors = sorted(validator.iter_errors(raw), key=lambda error: list(error.absolute_path))
        if errors:
            error = errors[0]
            location = ".".join(str(part) for part in error.absolute_path) or "<root>"
            raise ValidationError(f"{path}: {location}: {error.message}")

    def _validate_schema_file(self, path: Path) -> None:
        schema = self._load_json(path)
        validator_cls = validator_for(schema)
        validator_cls.check_schema(schema)

    def _load_yaml(self, path: Path) -> dict[str, Any]:
        with path.open("r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle)
        if not isinstance(data, dict):
            raise ValidationError(f"{path}: top-level document must be an object")
        return data

    def _load_json(self, path: Path) -> dict[str, Any]:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            raise ValidationError(f"{path}: JSON schema must be an object")
        return data
