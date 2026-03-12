from __future__ import annotations

from app.core.celery_app import celery_app
from app.models.health import DependencyHealth, HealthResponse, QueueHealth
from app.models.ops import (
    CapabilityView,
    JobsSnapshot,
    QueueSnapshot,
    RuntimeSnapshot,
    ServiceRuntimeView,
    ServicesSnapshot,
    WorkerLaneStatus,
)
from app.services.capabilities import get_capability_registry
from app.services.job_store import get_job_store
from app.services.jobs import list_job_responses
from app.services.registry import get_service_registry
from app.services.runtime import get_runtime_controller


def get_runtime_snapshot() -> RuntimeSnapshot:
    store = get_job_store()
    queue_lanes = _queue_lanes()
    docker_reachable, _ = get_runtime_controller().docker_reachable()
    return RuntimeSnapshot(
        queues=store.queue_snapshots(queue_lanes),
        active_job_id=store.active_job_id(),
        active_service_id=store.active_service_id(),
        warm_services=store.list_warm_services(),
        redis_reachable=store.ping(),
        docker_reachable=docker_reachable,
        worker_lanes=_worker_lane_status(queue_lanes),
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
    return get_job_store().queue_snapshots(_queue_lanes())


def get_health_snapshot() -> HealthResponse:
    store = get_job_store()
    docker_reachable, docker_detail = get_runtime_controller().docker_reachable()
    runtime = get_runtime_snapshot()
    queue_statuses = _worker_lane_status(_queue_lanes())
    health_queues = []
    for queue in runtime.queues:
        lane_status = next((item for item in queue_statuses if item.lane == queue.lane), None)
        health_queues.append(
            QueueHealth(
                lane=queue.lane,
                pending=queue.pending,
                active_job_id=queue.active_job_id,
                workers=[] if lane_status is None else lane_status.workers,
                healthy=False if lane_status is None else lane_status.healthy,
            )
        )

    status = "ok" if store.ping() and docker_reachable else "degraded"
    return HealthResponse(
        status=status,
        redis=DependencyHealth(reachable=store.ping()),
        docker=DependencyHealth(reachable=docker_reachable, detail=docker_detail),
        queues=health_queues,
        active_job_id=runtime.active_job_id,
        active_service_id=runtime.active_service_id,
    )


def _queue_lanes() -> list[str]:
    return sorted(
        {
            capability.queue_lane.value
            for capability in get_capability_registry().list_capabilities()
        }
    )


def _worker_lane_status(queue_lanes: list[str]) -> list[WorkerLaneStatus]:
    workers_by_lane = {lane: set[str]() for lane in queue_lanes}
    try:
        inspect = celery_app.control.inspect(timeout=1.0)
        active_queues = inspect.active_queues() or {}
    except Exception:
        active_queues = {}

    for worker_name, queues in active_queues.items():
        for queue in queues:
            lane = str(queue.get("name"))
            if lane in workers_by_lane:
                workers_by_lane[lane].add(worker_name)

    return [
        WorkerLaneStatus(
            lane=lane,
            workers=sorted(workers_by_lane[lane]),
            healthy=bool(workers_by_lane[lane]),
        )
        for lane in queue_lanes
    ]
