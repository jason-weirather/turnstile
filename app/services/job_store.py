from __future__ import annotations

import time
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Any, cast

import redis

from app.core.config import get_settings
from app.models.capability import QueueLane
from app.models.job import JobRecord, JobStatus
from app.models.ops import QueueSnapshot, WarmServiceState


class RedisJobStore:
    JOB_KEY_PREFIX = "turnstile:jobs:"
    JOB_INDEX_KEY = "turnstile:jobs:index"
    GPU_QUEUE_KEY = "turnstile:gpu:queue"
    GPU_ACTIVE_JOB_KEY = "turnstile:gpu:active_job"
    GPU_ACTIVE_SERVICE_KEY = "turnstile:gpu:active_service"
    GPU_LOCK_KEY = "turnstile:gpu:lock"
    WARM_SERVICE_KEY_PREFIX = "turnstile:warm_services:"

    def __init__(self, client: redis.Redis) -> None:
        self._client = client
        settings = get_settings()
        self._job_ttl_s = settings.job_ttl_s
        self._gpu_lock_ttl_s = settings.gpu_lock_ttl_s
        self._poll_interval_s = settings.arbiter_poll_interval_s

    def enqueue(self, job: JobRecord) -> None:
        payload = job.model_dump_json()
        pipeline = self._client.pipeline()
        pipeline.set(self._job_key(job.job_id), payload, ex=self._job_ttl_s)
        pipeline.zadd(self.JOB_INDEX_KEY, {job.job_id: job.created_at.timestamp()})
        pipeline.expire(self.JOB_INDEX_KEY, self._job_ttl_s)
        if job.queue_lane == QueueLane.GPU:
            pipeline.rpush(self.GPU_QUEUE_KEY, job.job_id)
            pipeline.expire(self.GPU_QUEUE_KEY, self._job_ttl_s)
        pipeline.execute()

    def get(self, job_id: str) -> JobRecord | None:
        payload = self._client.get(self._job_key(job_id))
        if payload is None:
            return None
        return JobRecord.model_validate_json(cast(str, payload))

    def list_jobs(self, limit: int) -> list[JobRecord]:
        if limit <= 0:
            return []
        job_ids = cast(list[str], self._client.zrevrange(self.JOB_INDEX_KEY, 0, limit - 1))
        jobs: list[JobRecord] = []
        stale_ids: list[str] = []
        for job_id in job_ids:
            job = self.get(job_id)
            if job is None:
                stale_ids.append(job_id)
                continue
            jobs.append(job)
        if stale_ids:
            self._client.zrem(self.JOB_INDEX_KEY, *stale_ids)
        return jobs

    def list_jobs_for_lane(
        self,
        lane: str,
        *,
        statuses: set[JobStatus] | None = None,
    ) -> list[JobRecord]:
        return [
            job
            for job in self._all_jobs()
            if job.queue_lane.value == lane and (statuses is None or job.status in statuses)
        ]

    def set_status(
        self,
        job_id: str,
        status: JobStatus,
        *,
        result_payload: dict[str, Any] | None = None,
        error_code: str | None = None,
        error_detail: str | None = None,
        container_id: str | None = None,
    ) -> JobRecord | None:
        job = self.get(job_id)
        if job is None:
            return None

        started_at = job.started_at
        finished_at = job.finished_at
        if status == JobStatus.RUNNING and started_at is None:
            started_at = datetime.now(timezone.utc)
        if status in {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED}:
            finished_at = datetime.now(timezone.utc)

        updated = job.model_copy(
            update={
                "status": status,
                "result_payload": (
                    result_payload if result_payload is not None else job.result_payload
                ),
                "error_code": error_code if error_code is not None else job.error_code,
                "error_detail": error_detail if error_detail is not None else job.error_detail,
                "container_id": container_id if container_id is not None else job.container_id,
                "started_at": started_at,
                "finished_at": finished_at,
            }
        )
        self._client.set(self._job_key(job_id), updated.model_dump_json(), ex=self._job_ttl_s)
        self._client.zadd(self.JOB_INDEX_KEY, {job_id: updated.created_at.timestamp()})
        self._client.expire(self.JOB_INDEX_KEY, self._job_ttl_s)
        return updated

    def attach_container(self, job_id: str, container_id: str) -> JobRecord | None:
        job = self.get(job_id)
        if job is None:
            return None
        return self.set_status(job_id, job.status, container_id=container_id)

    def try_start_gpu_job(self, job_id: str, service_id: str) -> bool:
        job = self.get(job_id)
        if job is None:
            raise RuntimeError(f"Missing job state for '{job_id}'.")
        if job.status == JobStatus.CANCELLED:
            raise RuntimeError(f"Job '{job_id}' was cancelled before execution.")

        queue_head = self._client.lindex(self.GPU_QUEUE_KEY, 0)
        active_job_id = self.active_job_id()
        active_service_id = self.active_service_id()
        if queue_head != job_id or active_job_id is not None:
            return False
        if active_service_id not in {None, service_id}:
            return False

        acquired = self._client.set(self.GPU_LOCK_KEY, job_id, nx=True, ex=self._gpu_lock_ttl_s)
        if not acquired:
            return False

        pipeline = self._client.pipeline()
        pipeline.lrem(self.GPU_QUEUE_KEY, 1, job_id)
        pipeline.expire(self.GPU_QUEUE_KEY, self._job_ttl_s)
        pipeline.set(self.GPU_ACTIVE_JOB_KEY, job_id, ex=self._gpu_lock_ttl_s)
        pipeline.set(self.GPU_ACTIVE_SERVICE_KEY, service_id, ex=self._gpu_lock_ttl_s)
        pipeline.set(self.GPU_LOCK_KEY, job_id, ex=self._gpu_lock_ttl_s)
        pipeline.execute()
        self.set_status(job_id, JobStatus.RUNNING, error_detail=None)
        return True

    def wait_for_gpu_turn(self, job_id: str, service_id: str) -> None:
        while True:
            if self.try_start_gpu_job(job_id, service_id):
                return
            self.set_status(job_id, JobStatus.WAITING_FOR_GPU, error_detail=None)
            time.sleep(self._poll_interval_s)

    def renew_gpu_job_lease(self, job_id: str, service_id: str) -> None:
        if self._client.get(self.GPU_LOCK_KEY) == job_id:
            self._client.expire(self.GPU_LOCK_KEY, self._gpu_lock_ttl_s)
        if self._client.get(self.GPU_ACTIVE_JOB_KEY) == job_id:
            self._client.expire(self.GPU_ACTIVE_JOB_KEY, self._gpu_lock_ttl_s)
        if self._client.get(self.GPU_ACTIVE_SERVICE_KEY) == service_id:
            self._client.expire(self.GPU_ACTIVE_SERVICE_KEY, self._gpu_lock_ttl_s)

    def cancel(self, job_id: str) -> JobRecord | None:
        job = self.get(job_id)
        if job is None:
            return None

        if job.status in {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED}:
            return job

        if job.status in {JobStatus.QUEUED, JobStatus.WAITING_FOR_GPU} and (
            job.queue_lane == QueueLane.GPU
        ):
            self._client.lrem(self.GPU_QUEUE_KEY, 0, job_id)
        return self.set_status(
            job_id,
            JobStatus.CANCELLED,
            error_code="cancelled",
            error_detail="Cancelled by user request.",
        )

    def release_gpu_slot(self, job_id: str, preserve_service_id: str | None = None) -> None:
        active_job_id = self.active_job_id()
        pipeline = self._client.pipeline()
        if active_job_id == job_id:
            pipeline.delete(self.GPU_ACTIVE_JOB_KEY)
        if self._client.get(self.GPU_LOCK_KEY) == job_id:
            pipeline.delete(self.GPU_LOCK_KEY)
        if preserve_service_id is None:
            if active_job_id == job_id:
                pipeline.delete(self.GPU_ACTIVE_SERVICE_KEY)
        elif self._client.get(self.GPU_ACTIVE_SERVICE_KEY) == preserve_service_id:
            pipeline.set(self.GPU_ACTIVE_SERVICE_KEY, preserve_service_id, ex=self._gpu_lock_ttl_s)
        pipeline.execute()

    def set_warm_service(self, state: WarmServiceState) -> WarmServiceState:
        self._client.set(
            self._warm_service_key(state.service_id),
            state.model_dump_json(),
            ex=self._warm_service_ttl(state.idle_ttl_s),
        )
        if state.gpu_required:
            self._client.set(self.GPU_ACTIVE_SERVICE_KEY, state.service_id, ex=self._gpu_lock_ttl_s)
        return state

    def get_warm_service(self, service_id: str) -> WarmServiceState | None:
        payload = self._client.get(self._warm_service_key(service_id))
        if payload is None:
            return None
        return WarmServiceState.model_validate_json(cast(str, payload))

    def list_warm_services(self) -> list[WarmServiceState]:
        services: list[WarmServiceState] = []
        for key in cast(list[str], self._client.keys(f"{self.WARM_SERVICE_KEY_PREFIX}*")):
            payload = self._client.get(key)
            if payload is None:
                continue
            services.append(WarmServiceState.model_validate_json(cast(str, payload)))
        return sorted(services, key=lambda item: item.service_id)

    def touch_warm_service(self, service_id: str) -> WarmServiceState | None:
        state = self.get_warm_service(service_id)
        if state is None:
            return None
        now = datetime.now(timezone.utc)
        updated = state.model_copy(
            update={
                "last_used_at": now,
                "expires_at": now + timedelta(seconds=state.idle_ttl_s),
            }
        )
        return self.set_warm_service(updated)

    def renew_warm_service_lease(self, service_id: str) -> WarmServiceState | None:
        state = self.get_warm_service(service_id)
        if state is None:
            return None
        self._client.expire(
            self._warm_service_key(service_id),
            self._warm_service_ttl(state.idle_ttl_s),
        )
        if state.gpu_required and self._client.get(self.GPU_ACTIVE_JOB_KEY) is None:
            self._client.set(self.GPU_ACTIVE_SERVICE_KEY, service_id, ex=self._gpu_lock_ttl_s)
        return state

    def clear_warm_service(self, service_id: str) -> None:
        state = self.get_warm_service(service_id)
        pipeline = self._client.pipeline()
        pipeline.delete(self._warm_service_key(service_id))
        if (
            state is not None
            and state.gpu_required
            and self._client.get(self.GPU_ACTIVE_JOB_KEY) is None
        ):
            if self._client.get(self.GPU_ACTIVE_SERVICE_KEY) == service_id:
                pipeline.delete(self.GPU_ACTIVE_SERVICE_KEY)
        pipeline.execute()

    def clear_active_service(self, service_id: str) -> None:
        if self._client.get(self.GPU_ACTIVE_SERVICE_KEY) == service_id:
            self._client.delete(self.GPU_ACTIVE_SERVICE_KEY)

    def active_job_id(self) -> str | None:
        return cast(str | None, self._client.get(self.GPU_ACTIVE_JOB_KEY))

    def active_service_id(self) -> str | None:
        return cast(str | None, self._client.get(self.GPU_ACTIVE_SERVICE_KEY))

    def queue_snapshots(self, lanes: list[str]) -> list[QueueSnapshot]:
        jobs = self._all_jobs()
        gpu_queue = cast(list[str], self._client.lrange(self.GPU_QUEUE_KEY, 0, -1))
        snapshots: list[QueueSnapshot] = []
        for lane in lanes:
            if lane == "gpu":
                snapshots.append(
                    QueueSnapshot(
                        lane=lane,
                        pending=len(gpu_queue),
                        queued_job_ids=gpu_queue,
                        active_job_id=self.active_job_id(),
                        active_service_id=self.active_service_id(),
                    )
                )
                continue

            queued_job_ids = [
                job.job_id
                for job in jobs
                if job.queue_lane == lane and job.status in {JobStatus.QUEUED, JobStatus.RUNNING}
            ]
            snapshots.append(
                QueueSnapshot(
                    lane=lane,
                    pending=len(queued_job_ids),
                    queued_job_ids=queued_job_ids,
                    active_job_id=None,
                    active_service_id=None,
                )
            )
        return snapshots

    def ping(self) -> bool:
        try:
            return bool(self._client.ping())
        except Exception:
            return False

    def clear(self) -> None:
        keys = list(cast(Iterable[str], self._client.keys(f"{self.JOB_KEY_PREFIX}*")))
        keys.extend(cast(Iterable[str], self._client.keys(f"{self.WARM_SERVICE_KEY_PREFIX}*")))
        keys.extend(
            [
                self.JOB_INDEX_KEY,
                self.GPU_QUEUE_KEY,
                self.GPU_ACTIVE_JOB_KEY,
                self.GPU_ACTIVE_SERVICE_KEY,
                self.GPU_LOCK_KEY,
            ]
        )
        if keys:
            self._client.delete(*keys)

    def _all_jobs(self) -> list[JobRecord]:
        job_ids = cast(list[str], self._client.zrange(self.JOB_INDEX_KEY, 0, -1))
        jobs: list[JobRecord] = []
        stale_ids: list[str] = []
        for job_id in job_ids:
            job = self.get(job_id)
            if job is None:
                stale_ids.append(job_id)
                continue
            jobs.append(job)
        if stale_ids:
            self._client.zrem(self.JOB_INDEX_KEY, *stale_ids)
        return jobs

    def _job_key(self, job_id: str) -> str:
        return f"{self.JOB_KEY_PREFIX}{job_id}"

    def _warm_service_key(self, service_id: str) -> str:
        return f"{self.WARM_SERVICE_KEY_PREFIX}{service_id}"

    def _warm_service_ttl(self, idle_ttl_s: int) -> int:
        return max(self._job_ttl_s, idle_ttl_s * 2)


@lru_cache(maxsize=1)
def get_redis_client() -> redis.Redis:
    settings = get_settings()
    return redis.Redis.from_url(settings.redis_url, decode_responses=True)


@lru_cache(maxsize=1)
def get_job_store() -> RedisJobStore:
    return RedisJobStore(get_redis_client())
