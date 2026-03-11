from fastapi import APIRouter

from app.models.ops import OpsSnapshot
from app.services.job_store import get_job_store

router = APIRouter()


@router.get("/ops/runtime", response_model=OpsSnapshot)
def get_runtime_snapshot() -> OpsSnapshot:
    return get_job_store().snapshot()
