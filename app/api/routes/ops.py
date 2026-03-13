from fastapi import APIRouter, HTTPException, status

from app.models.ops import (
    CapabilityView,
    JobsSnapshot,
    LaneQueueActionResponse,
    QueueSnapshot,
    ReadinessSnapshot,
    RuntimeSnapshot,
    ServicesSnapshot,
)
from app.services.ops import (
    cancel_lane_queue,
    get_capability_views,
    get_jobs_snapshot,
    get_queue_snapshots,
    get_readiness_status,
    get_runtime_snapshot,
    get_services_snapshot,
)

router = APIRouter()


@router.get("/ops/runtime", response_model=RuntimeSnapshot)
def get_runtime_snapshot_endpoint() -> RuntimeSnapshot:
    return get_runtime_snapshot()


@router.get("/ops/readiness", response_model=ReadinessSnapshot)
def get_readiness_snapshot_endpoint() -> ReadinessSnapshot:
    return get_readiness_status()


@router.get("/ops/jobs", response_model=JobsSnapshot)
def get_jobs_snapshot_endpoint() -> JobsSnapshot:
    return get_jobs_snapshot()


@router.get("/ops/services", response_model=ServicesSnapshot)
def get_services_snapshot_endpoint() -> ServicesSnapshot:
    return get_services_snapshot()


@router.get("/ops/capabilities", response_model=list[CapabilityView])
def get_capability_views_endpoint() -> list[CapabilityView]:
    return get_capability_views()


@router.get("/ops/queues", response_model=list[QueueSnapshot])
def get_queue_snapshot_endpoint() -> list[QueueSnapshot]:
    return get_queue_snapshots()


@router.post("/ops/queues/{lane}/cancel", response_model=LaneQueueActionResponse)
def cancel_lane_queue_endpoint(lane: str) -> LaneQueueActionResponse:
    try:
        return cancel_lane_queue(lane)
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Lane '{lane}' is not configured.",
        ) from exc
