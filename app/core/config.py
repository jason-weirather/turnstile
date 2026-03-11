import os
from functools import lru_cache

from pydantic import BaseModel


class Settings(BaseModel):
    app_name: str = "Turnstile"
    env: str = "development"
    redis_url: str = "redis://localhost:6379/2"
    celery_broker_url: str = "redis://localhost:6379/0"
    celery_result_backend: str = "redis://localhost:6379/1"
    runtime_mode: str = "noop"
    stub_task_delay_s: float = 0.2
    job_ttl_s: int = 3600
    gpu_lock_ttl_s: int = 60
    arbiter_poll_interval_s: float = 0.05


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings(
        app_name=os.getenv("TURNSTILE_APP_NAME", "Turnstile"),
        env=os.getenv("TURNSTILE_ENV", "development"),
        redis_url=os.getenv("TURNSTILE_REDIS_URL", "redis://localhost:6379/2"),
        celery_broker_url=os.getenv("TURNSTILE_CELERY_BROKER_URL", "redis://localhost:6379/0"),
        celery_result_backend=os.getenv(
            "TURNSTILE_CELERY_RESULT_BACKEND",
            "redis://localhost:6379/1",
        ),
        runtime_mode=os.getenv("TURNSTILE_RUNTIME_MODE", "noop"),
        stub_task_delay_s=float(os.getenv("TURNSTILE_STUB_TASK_DELAY_S", "0.2")),
        job_ttl_s=int(os.getenv("TURNSTILE_JOB_TTL_S", "3600")),
        gpu_lock_ttl_s=int(os.getenv("TURNSTILE_GPU_LOCK_TTL_S", "60")),
        arbiter_poll_interval_s=float(os.getenv("TURNSTILE_ARBITER_POLL_INTERVAL_S", "0.05")),
    )
