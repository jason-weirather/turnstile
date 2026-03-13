from fastapi import APIRouter, Response, status

from app.models.health import HealthResponse
from app.models.ops import ReadinessSnapshot
from app.services.ops import get_health_snapshot, get_readiness_status

router = APIRouter()


@router.get("/healthz", response_model=HealthResponse)
def healthcheck() -> HealthResponse:
    return get_health_snapshot()


@router.get("/readyz", response_model=ReadinessSnapshot)
def readiness_check(response: Response) -> ReadinessSnapshot:
    readiness = get_readiness_status()
    response.status_code = (
        status.HTTP_200_OK
        if readiness.ready
        else status.HTTP_503_SERVICE_UNAVAILABLE
    )
    return readiness
