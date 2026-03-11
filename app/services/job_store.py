from __future__ import annotations

import time
from collections.abc import Iterable
from functools import lru_cache
from typing import cast

import redis

from app.core.config import get_settings
from app.models.job import JobRecord, JobStatus
from app.models.ops import OpsSnapshot


class RedisJobStore:
    JOB_KEY_PREFIX = "turnstile:jobs:"
    GPU_QUEUE_KEY = "turnstile:gpu:queue"
    GPU_ACTIVE_JOB_KEY = "turnstile:gpu:active_job"
    GPU_ACTIVE_SERVICE_KEY = "turnstile:gpu:active_service"
    GPU_LOCK_KEY = "turnstile:gpu:lock"

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
        pipeline.rpush(self.GPU_QUEUE_KEY, job.job_id)
        pipeline.expire(self.GPU_QUEUE_KEY, self._job_ttl_s)
        pipeline.execute()

    def get(self, job_id: str) -> JobRecord | None:
        payload = self._client.get(self._job_key(job_id))
        if payload is None:
            return None
        return JobRecord.model_validate_json(cast(str, payload))

    def set_status(
        self,
        job_id: str,
        status: JobStatus,
        *,
        result: dict[str, str] | None = None,
        error: str | None = None,
    ) -> JobRecord | None:
        job = self.get(job_id)
        if job is None:
            return None

        updated = job.model_copy(
            update={
                "status": status,
                "result": result if result is not None else job.result,
                "error": error,
            }
        )
        self._client.set(self._job_key(job_id), updated.model_dump_json(), ex=self._job_ttl_s)
        return updated

    def wait_for_gpu_turn(self, job_id: str, service_id: str) -> None:
        while True:
            job = self.get(job_id)
            if job is None:
                raise RuntimeError(f"Missing job state for '{job_id}'.")
            if job.status == JobStatus.CANCELLED:
                raise RuntimeError(f"Job '{job_id}' was cancelled before execution.")

            queue_head = self._client.lindex(self.GPU_QUEUE_KEY, 0)
            if queue_head == job_id and self._try_acquire_gpu_slot(job_id, service_id):
                self._client.lrem(self.GPU_QUEUE_KEY, 1, job_id)
                self._client.expire(self.GPU_QUEUE_KEY, self._job_ttl_s)
                self.set_status(job_id, JobStatus.RUNNING, error=None)
                return

            self.set_status(job_id, JobStatus.WAITING_FOR_GPU, error=None)
            time.sleep(self._poll_interval_s)

    def release_gpu_slot(self, job_id: str) -> None:
        if self._client.get(self.GPU_ACTIVE_JOB_KEY) == job_id:
            pipeline = self._client.pipeline()
            pipeline.delete(self.GPU_ACTIVE_JOB_KEY)
            pipeline.delete(self.GPU_ACTIVE_SERVICE_KEY)
            pipeline.execute()
        if self._client.get(self.GPU_LOCK_KEY) == job_id:
            self._client.delete(self.GPU_LOCK_KEY)

    def snapshot(self) -> OpsSnapshot:
        queue = cast(list[str], self._client.lrange(self.GPU_QUEUE_KEY, 0, -1))
        active_job_id = cast(str | None, self._client.get(self.GPU_ACTIVE_JOB_KEY))
        active_service_id = cast(str | None, self._client.get(self.GPU_ACTIVE_SERVICE_KEY))
        return OpsSnapshot(
            queue=queue,
            active_job_id=active_job_id,
            active_service_id=active_service_id,
        )

    def clear(self) -> None:
        keys = list(cast(Iterable[str], self._client.keys(f"{self.JOB_KEY_PREFIX}*")))
        keys.extend(
            [
                self.GPU_QUEUE_KEY,
                self.GPU_ACTIVE_JOB_KEY,
                self.GPU_ACTIVE_SERVICE_KEY,
                self.GPU_LOCK_KEY,
            ]
        )
        if keys:
            self._client.delete(*keys)

    def _job_key(self, job_id: str) -> str:
        return f"{self.JOB_KEY_PREFIX}{job_id}"

    def _try_acquire_gpu_slot(self, job_id: str, service_id: str) -> bool:
        acquired = self._client.set(self.GPU_LOCK_KEY, job_id, nx=True, ex=self._gpu_lock_ttl_s)
        if not acquired:
            return False

        pipeline = self._client.pipeline()
        pipeline.set(self.GPU_ACTIVE_JOB_KEY, job_id, ex=self._job_ttl_s)
        pipeline.set(self.GPU_ACTIVE_SERVICE_KEY, service_id, ex=self._job_ttl_s)
        pipeline.set(self.GPU_LOCK_KEY, job_id, ex=self._gpu_lock_ttl_s)
        pipeline.execute()
        return True


@lru_cache(maxsize=1)
def get_redis_client() -> redis.Redis:
    settings = get_settings()
    return redis.Redis.from_url(settings.redis_url, decode_responses=True)


@lru_cache(maxsize=1)
def get_job_store() -> RedisJobStore:
    return RedisJobStore(get_redis_client())
