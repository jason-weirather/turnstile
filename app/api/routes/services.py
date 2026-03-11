from fastapi import APIRouter

from app.models.service import ServiceDescriptor
from app.services.registry import service_registry

router = APIRouter()


@router.get("/services", response_model=list[ServiceDescriptor])
def list_services() -> list[ServiceDescriptor]:
    return service_registry.list_services()
