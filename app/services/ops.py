from __future__ import annotations

from app.models.health import DependencyHealth, HealthResponse, QueueHealth
from app.models.ops import (
    CapabilityView,
    JobsSnapshot,
    LaneQueueActionResponse,
    QueueSnapshot,
    ReadinessSnapshot,
    RuntimeSnapshot,
    ServiceRuntimeView,
    ServicesSnapshot,
)
from app.services.capabilities import get_capability_registry
from app.services.job_store import get_job_store
from app.services.jobs import cancel_queued_jobs_for_lane, list_job_responses
from app.services.readiness import get_readiness_snapshot
from app.services.registry import get_service_registry


def get_runtime_snapshot() -> RuntimeSnapshot:
    store = get_job_store()
    readiness = get_readiness_snapshot()
    return RuntimeSnapshot(
        queues=store.queue_snapshots(readiness.required_lanes),
        active_job_id=store.active_job_id(),
        active_service_id=store.active_service_id(),
        warm_services=store.list_warm_services(),
        redis_reachable=readiness.redis_reachable,
        docker_reachable=readiness.docker_reachable,
        submission_ready=readiness.ready,
        required_lanes=readiness.required_lanes,
        worker_lanes=readiness.worker_lanes,
        worker_inspection=readiness.worker_inspection,
        identity=readiness.identity,
    )


def get_jobs_snapshot() -> JobsSnapshot:
    return JobsSnapshot(jobs=list_job_responses())


def get_services_snapshot() -> ServicesSnapshot:
    store = get_job_store()
    services = []
    for service in get_service_registry().list_services():
        services.append(
            ServiceRuntimeView(
                service=service,
                warm_state=store.get_warm_service(service.service_id),
            )
        )
    return ServicesSnapshot(services=services)


def get_capability_views() -> list[CapabilityView]:
    return [
        CapabilityView(
            capability_id=capability.capability_id,
            method=capability.method,
            path=capability.path,
            summary=capability.summary,
            execution_mode=capability.execution_mode,
            queue_lane=capability.queue_lane,
            adapter_type=capability.adapter_type,
            default_service_selection=capability.default_service_selection,
        )
        for capability in get_capability_registry().list_capabilities()
    ]


def get_queue_snapshots() -> list[QueueSnapshot]:
    return get_job_store().queue_snapshots(get_readiness_snapshot().required_lanes)


def get_health_snapshot() -> HealthResponse:
    store = get_job_store()
    readiness = get_readiness_snapshot()
    queues = store.queue_snapshots(readiness.required_lanes)
    health_queues = []
    for queue in queues:
        lane_status = next(
            (item for item in readiness.worker_lanes if item.lane == queue.lane),
            None,
        )
        health_queues.append(
            QueueHealth(
                lane=queue.lane,
                pending=queue.pending,
                active_job_id=queue.active_job_id,
                workers=[] if lane_status is None else lane_status.workers,
                healthy=False if lane_status is None else lane_status.healthy,
            )
        )

    readiness_reasons = [lane.reason for lane in readiness.worker_lanes if lane.reason is not None]
    if not readiness.redis_reachable:
        readiness_reasons.insert(0, "Redis is unreachable.")
    if not readiness.docker_reachable:
        readiness_reasons.insert(0, "Docker is unreachable in docker runtime mode.")

    status = "ok" if readiness.redis_reachable and readiness.docker_reachable else "degraded"
    return HealthResponse(
        status=status,
        ready=readiness.ready,
        readiness_reasons=readiness_reasons,
        redis=DependencyHealth(reachable=store.ping()),
        docker=DependencyHealth(
            reachable=readiness.docker_reachable,
            detail=readiness.detail if not readiness.docker_reachable else None,
        ),
        queues=health_queues,
        active_job_id=store.active_job_id(),
        active_service_id=store.active_service_id(),
    )


def get_readiness_status() -> ReadinessSnapshot:
    return get_readiness_snapshot()


def cancel_lane_queue(lane: str) -> LaneQueueActionResponse:
    return cancel_queued_jobs_for_lane(lane)
