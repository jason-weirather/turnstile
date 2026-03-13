from __future__ import annotations

import socket
import time
from datetime import datetime, timezone
from typing import Protocol, cast

from app.core.celery_app import celery_app
from app.core.config import get_settings
from app.models.capability import ExecutionMode
from app.models.ops import (
    InstanceIdentity,
    ReadinessSnapshot,
    WorkerInspectionSnapshot,
    WorkerLaneStatus,
)
from app.services.capabilities import get_capability_registry
from app.services.job_store import get_job_store
from app.services.runtime import get_runtime_controller

_STARTED_AT = datetime.now(timezone.utc)


class CeleryInspector(Protocol):
    def ping(self) -> dict[str, dict[str, str]] | None: ...

    def active_queues(self) -> dict[str, list[dict[str, str]]] | None: ...


def get_celery_inspector(timeout_s: float) -> CeleryInspector:
    return cast(CeleryInspector, celery_app.control.inspect(timeout=timeout_s))


def get_instance_identity() -> InstanceIdentity:
    settings = get_settings()
    hostname = socket.gethostname()
    instance_id = settings.instance_id or f"{hostname}-{_STARTED_AT.timestamp():.0f}"
    return InstanceIdentity(
        instance_id=instance_id,
        hostname=hostname,
        started_at=_STARTED_AT,
    )


def get_required_async_lanes() -> list[str]:
    return sorted(
        {
            capability.queue_lane.value
            for capability in get_capability_registry().list_capabilities()
            if capability.execution_mode == ExecutionMode.ASYNC
        }
    )


def get_readiness_snapshot() -> ReadinessSnapshot:
    settings = get_settings()
    required_lanes = get_required_async_lanes()
    redis_reachable = get_job_store().ping()
    if settings.runtime_mode == "docker":
        docker_reachable, docker_detail = get_runtime_controller().docker_reachable()
    else:
        docker_reachable, docker_detail = (True, "stub runtime")

    inspection = _inspect_workers()
    worker_lanes = _build_lane_statuses(
        required_lanes=required_lanes,
        redis_reachable=redis_reachable,
        docker_reachable=docker_reachable,
        inspection=inspection,
    )
    reasons = [status.reason for status in worker_lanes if status.reason]
    if not redis_reachable:
        reasons.insert(0, "Redis is unreachable.")
    if settings.runtime_mode == "docker" and not docker_reachable:
        reasons.insert(0, docker_detail or "Docker is unreachable.")

    ready = redis_reachable and docker_reachable and all(
        status.submission_ready for status in worker_lanes
    )
    detail = None if ready else reasons[0] if reasons else "Turnstile is not ready."
    return ReadinessSnapshot(
        status="ready" if ready else "not_ready",
        ready=ready,
        detail=detail,
        runtime_mode=settings.runtime_mode,
        redis_reachable=redis_reachable,
        docker_reachable=docker_reachable,
        required_lanes=required_lanes,
        worker_lanes=worker_lanes,
        worker_inspection=inspection,
        identity=get_instance_identity(),
    )


def is_lane_submission_ready(lane: str) -> tuple[bool, str | None]:
    readiness = get_readiness_snapshot()
    lane_status = next((status for status in readiness.worker_lanes if status.lane == lane), None)
    if lane_status is None:
        return (False, f"Lane '{lane}' is not configured for async capabilities.")
    return (lane_status.submission_ready, lane_status.reason)


def _build_lane_statuses(
    *,
    required_lanes: list[str],
    redis_reachable: bool,
    docker_reachable: bool,
    inspection: WorkerInspectionSnapshot,
) -> list[WorkerLaneStatus]:
    ping_workers = set(inspection.ping_workers)
    active_queue_workers = set(inspection.active_queue_workers)
    lane_workers = _workers_by_lane(inspection)

    statuses: list[WorkerLaneStatus] = []
    for lane in required_lanes:
        workers = sorted(lane_workers.get(lane, set()))
        if not redis_reachable:
            statuses.append(
                WorkerLaneStatus(
                    lane=lane,
                    workers=workers,
                    healthy=False,
                    submission_ready=False,
                    reason="Redis is unreachable.",
                )
            )
            continue
        if not docker_reachable:
            statuses.append(
                WorkerLaneStatus(
                    lane=lane,
                    workers=workers,
                    healthy=False,
                    submission_ready=False,
                    reason="Docker is unreachable in docker runtime mode.",
                )
            )
            continue
        if workers:
            statuses.append(
                WorkerLaneStatus(
                    lane=lane,
                    workers=workers,
                    healthy=True,
                    submission_ready=True,
                )
            )
            continue

        if inspection.status == "inspect_timeout":
            reason = "Celery inspect timed out before worker readiness could be determined."
        elif not ping_workers:
            reason = f"No healthy workers are attached to lane '{lane}'."
        elif not active_queue_workers:
            reason = (
                "Workers responded to ping, but none reported active queue subscriptions."
            )
        else:
            reason = f"Workers are running, but none are attached to lane '{lane}'."

        statuses.append(
            WorkerLaneStatus(
                lane=lane,
                workers=[],
                healthy=False,
                submission_ready=False,
                reason=reason,
            )
        )
    return statuses


def _workers_by_lane(inspection: WorkerInspectionSnapshot) -> dict[str, set[str]]:
    return {
        str(lane): {str(worker) for worker in workers}
        for lane, workers in inspection.workers_by_lane.items()
    }


def _inspect_workers() -> WorkerInspectionSnapshot:
    settings = get_settings()
    timeout_s = settings.worker_inspect_timeout_s
    attempts = settings.worker_inspect_attempts
    retry_interval_s = settings.worker_inspect_retry_interval_s

    ping_workers: set[str] = set()
    active_queue_workers: set[str] = set()
    lane_workers: dict[str, set[str]] = {lane: set() for lane in get_required_async_lanes()}
    errors: list[str] = []
    saw_response = False
    saw_timeout_hint = False

    for attempt in range(attempts):
        try:
            inspect = get_celery_inspector(timeout_s)
            ping_response_raw = inspect.ping()
            active_queues_raw = inspect.active_queues()
            if ping_response_raw is None or active_queues_raw is None:
                saw_timeout_hint = True
            ping_response = ping_response_raw or {}
            active_queues = active_queues_raw or {}
        except Exception as exc:
            errors.append(str(exc))
            ping_response = {}
            active_queues = {}

        if ping_response:
            saw_response = True
            ping_workers.update(str(worker_name) for worker_name in ping_response)

        if active_queues:
            saw_response = True
            for worker_name, queues in active_queues.items():
                active_queue_workers.add(str(worker_name))
                for queue in queues:
                    lane = str(queue.get("name"))
                    lane_workers.setdefault(lane, set()).add(str(worker_name))

        if ping_workers or active_queue_workers:
            break
        if attempt < attempts - 1:
            time.sleep(retry_interval_s)

    status = "ok"
    detail = None
    if not ping_workers and not active_queue_workers:
        if (errors or saw_timeout_hint) and not saw_response:
            status = "inspect_timeout"
            detail = (
                errors[-1]
                if errors
                else "Celery inspect returned no worker data before timeout."
            )
        else:
            status = "no_workers"
            detail = "No workers responded to Celery inspect."
    elif ping_workers and not active_queue_workers:
        status = "workers_without_queues"
        detail = "Workers responded to ping, but no queue attachments were reported."

    snapshot = WorkerInspectionSnapshot(
        status=status,
        timeout_s=timeout_s,
        attempts=attempts,
        ping_workers=sorted(ping_workers),
        active_queue_workers=sorted(active_queue_workers),
        workers_by_lane={
            lane: sorted(workers)
            for lane, workers in sorted(lane_workers.items())
            if workers
        },
        errors=errors,
        detail=detail,
    )
    return snapshot
