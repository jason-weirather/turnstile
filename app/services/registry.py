from app.models.service import ServiceDescriptor, ServiceMode


class InMemoryServiceRegistry:
    def __init__(self) -> None:
        self._services = {
            "mock-image-generator": ServiceDescriptor(
                service_id="mock-image-generator",
                capability="image.generate",
                image="ghcr.io/example/mock-image-generator:latest",
                mode=ServiceMode.EPHEMERAL,
                gpu_required=True,
                estimated_vram_mb=4096,
                startup_timeout_s=30,
                idle_ttl_s=300,
                endpoint_adapter="stub_image_generate",
            )
        }

    def list_services(self) -> list[ServiceDescriptor]:
        return list(self._services.values())

    def get(self, service_id: str) -> ServiceDescriptor | None:
        return self._services.get(service_id)

    def resolve_for_capability(
        self,
        capability: str,
        service_id: str | None = None,
    ) -> ServiceDescriptor:
        if service_id is not None:
            service = self.get(service_id)
            if service is None:
                raise KeyError(service_id)
            return service

        for service in self._services.values():
            if service.capability == capability:
                return service
        raise KeyError(capability)


service_registry = InMemoryServiceRegistry()
