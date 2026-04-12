from functools import lru_cache
from pathlib import Path

from app.models.service import ServiceDescriptor
from app.services.definition_loader import DefinitionLoader


class ServiceRegistry:
    def __init__(self, services: list[ServiceDescriptor]) -> None:
        self._services = {service.service_id: service for service in services}

    def list_services(self) -> list[ServiceDescriptor]:
        return list(self._services.values())

    def get(self, service_id: str) -> ServiceDescriptor | None:
        return self._services.get(service_id)

    def resolve_for_capability(
        self,
        capability: str,
        selector_value: str | None = None,
        default_service_id: str | None = None,
        *,
        selector_field: str = "service_id",
    ) -> ServiceDescriptor:
        if selector_value is not None:
            service = self._resolve_selector_for_capability(
                capability,
                selector_field,
                selector_value,
            )
            if service is None:
                raise KeyError(selector_value)
            return service

        if default_service_id is not None:
            service = self.get(default_service_id)
            if service is None or capability not in service.capabilities:
                raise KeyError(default_service_id)
            return service

        for service in self._services.values():
            if capability in service.capabilities:
                return service
        raise KeyError(capability)

    def _resolve_selector_for_capability(
        self,
        capability: str,
        selector_field: str,
        selector_value: str,
    ) -> ServiceDescriptor | None:
        if selector_field == "service_id":
            service = self.get(selector_value)
            if service is None or capability not in service.capabilities:
                return None
            return service

        for service in self._services.values():
            if capability not in service.capabilities:
                continue
            if service.selectors.get(selector_field) == selector_value:
                return service
        return None


@lru_cache(maxsize=1)
def get_service_registry() -> ServiceRegistry:
    root = Path(__file__).resolve().parents[2]
    loader = DefinitionLoader(
        capabilities_dir=root / "config" / "capabilities",
        services_dir=root / "config" / "services",
        schemas_dir=root / "config" / "schemas",
    )
    return ServiceRegistry(loader.load_services())
