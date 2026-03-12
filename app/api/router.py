from __future__ import annotations

from typing import Any, cast

from fastapi import APIRouter, status
from pydantic import BaseModel

from app.api.routes import health, jobs, ops, services
from app.models.capability import ExecutionMode
from app.services.capabilities import get_capability_registry
from app.services.jobs import execute_capability_request


def build_api_router() -> APIRouter:
    router = APIRouter()
    router.include_router(health.router)
    router.include_router(services.router, prefix="/v1")
    router.include_router(jobs.router, prefix="/v1")
    router.include_router(_build_capability_router(), prefix="/v1")
    router.include_router(ops.router)
    return router


def _build_capability_router() -> APIRouter:
    router = APIRouter()
    capability_registry = get_capability_registry()

    for capability in capability_registry.list_capabilities():
        request_model = capability_registry.get_request_model(capability.capability_id)
        response_model = capability_registry.get_response_model(capability.capability_id)

        def endpoint_factory(
            capability_id: str,
            payload_model: type[BaseModel],
        ) -> Any:
            def endpoint(payload: payload_model) -> dict[str, object]:  # type: ignore[valid-type]
                parsed_payload = cast(BaseModel, payload)
                return execute_capability_request(
                    capability_id,
                    parsed_payload.model_dump(exclude_none=True),
                )

            endpoint.__name__ = f"{capability_id.replace('.', '_')}_endpoint"
            endpoint.__annotations__["payload"] = payload_model
            return endpoint

        router.add_api_route(
            capability.path,
            endpoint_factory(capability.capability_id, request_model),
            methods=[capability.method],
            status_code=(
                status.HTTP_200_OK
                if capability.execution_mode == ExecutionMode.SYNC
                else status.HTTP_202_ACCEPTED
            ),
            response_model=response_model,
            summary=capability.summary,
            tags=[capability.capability_id.split(".", maxsplit=1)[0]],
        )

    return router
