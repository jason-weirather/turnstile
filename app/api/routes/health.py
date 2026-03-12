from fastapi import APIRouter

from app.models.health import HealthResponse
from app.services.ops import get_health_snapshot

router = APIRouter()


@router.get("/healthz", response_model=HealthResponse)
def healthcheck() -> HealthResponse:
    return get_health_snapshot()
