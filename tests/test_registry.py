from app.models.capability import AdapterType
from app.models.service import ServiceDescriptor, ServiceMode
from app.services.registry import ServiceRegistry


def _service(*, service_id: str, capability: str, model: str | None = None) -> ServiceDescriptor:
    selectors = {"model": model} if model is not None else {}
    return ServiceDescriptor(
        service_id=service_id,
        capabilities=[capability],
        selectors=selectors,
        image="ghcr.io/example/mock-http:latest",
        mode=ServiceMode.WARM,
        gpu_required=False,
        estimated_vram_mb=0,
        startup_timeout_s=30,
        idle_ttl_s=120,
        adapter_type=AdapterType.HTTP_FORWARD_JSON,
        adapter_config={},
    )


def test_resolve_for_capability_supports_model_selectors() -> None:
    registry = ServiceRegistry(
        [
            _service(
                service_id="gpt-5-4-service",
                capability="openai.chat.completions",
                model="gpt-5.4",
            ),
            _service(
                service_id="gpt-5-4-mini-service",
                capability="openai.chat.completions",
                model="gpt-5.4-mini",
            ),
        ]
    )

    service = registry.resolve_for_capability(
        "openai.chat.completions",
        selector_value="gpt-5.4-mini",
        selector_field="model",
    )

    assert service.service_id == "gpt-5-4-mini-service"


def test_resolve_for_capability_keeps_service_id_selection_working() -> None:
    registry = ServiceRegistry(
        [_service(service_id="mock-http-alpha", capability="example.http.echo")]
    )

    service = registry.resolve_for_capability(
        "example.http.echo",
        selector_value="mock-http-alpha",
    )

    assert service.service_id == "mock-http-alpha"
