from fastapi import APIRouter

from app.models.health import HealthResponse

router = APIRouter()


@router.get("/healthz", response_model=HealthResponse)
def healthcheck() -> HealthResponse:
    return HealthResponse(status="ok")
