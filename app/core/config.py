import os
from functools import lru_cache

from pydantic import BaseModel


class Settings(BaseModel):
    app_name: str = "Turnstile"
    env: str = "development"
    redis_url: str = "redis://localhost:6379/2"
    celery_broker_url: str = "redis://localhost:6379/0"
    celery_result_backend: str = "redis://localhost:6379/1"
    runtime_mode: str = "stub"
    stub_task_delay_s: float = 0.2
    job_ttl_s: int = 3600
    gpu_lock_ttl_s: int = 60
    arbiter_poll_interval_s: float = 0.05
    docker_host: str | None = None
    docker_network: str | None = None
    docker_service_host: str = "127.0.0.1"
    docker_label_prefix: str = "turnstile"
    runtime_heartbeat_interval_s: float = 1.0
    warm_probe_interval_s: float = 0.5
    ops_job_limit: int = 50
    allow_enqueue_without_workers: bool = False
    worker_inspect_timeout_s: float = 1.0
    worker_inspect_attempts: int = 3
    worker_inspect_retry_interval_s: float = 0.2
    instance_id: str | None = None


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


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
        runtime_mode=os.getenv("TURNSTILE_RUNTIME_MODE", "stub"),
        stub_task_delay_s=float(os.getenv("TURNSTILE_STUB_TASK_DELAY_S", "0.2")),
        job_ttl_s=int(os.getenv("TURNSTILE_JOB_TTL_S", "3600")),
        gpu_lock_ttl_s=int(os.getenv("TURNSTILE_GPU_LOCK_TTL_S", "60")),
        arbiter_poll_interval_s=float(os.getenv("TURNSTILE_ARBITER_POLL_INTERVAL_S", "0.05")),
        docker_host=os.getenv("TURNSTILE_DOCKER_HOST") or None,
        docker_network=os.getenv("TURNSTILE_DOCKER_NETWORK") or None,
        docker_service_host=os.getenv("TURNSTILE_DOCKER_SERVICE_HOST", "127.0.0.1"),
        docker_label_prefix=os.getenv("TURNSTILE_DOCKER_LABEL_PREFIX", "turnstile"),
        runtime_heartbeat_interval_s=float(
            os.getenv("TURNSTILE_RUNTIME_HEARTBEAT_INTERVAL_S", "1.0")
        ),
        warm_probe_interval_s=float(os.getenv("TURNSTILE_WARM_PROBE_INTERVAL_S", "0.5")),
        ops_job_limit=int(os.getenv("TURNSTILE_OPS_JOB_LIMIT", "50")),
        allow_enqueue_without_workers=_env_bool(
            "TURNSTILE_ALLOW_ENQUEUE_WITHOUT_WORKERS",
            False,
        ),
        worker_inspect_timeout_s=float(os.getenv("TURNSTILE_WORKER_INSPECT_TIMEOUT_S", "1.0")),
        worker_inspect_attempts=int(os.getenv("TURNSTILE_WORKER_INSPECT_ATTEMPTS", "3")),
        worker_inspect_retry_interval_s=float(
            os.getenv("TURNSTILE_WORKER_INSPECT_RETRY_INTERVAL_S", "0.2")
        ),
        instance_id=os.getenv("TURNSTILE_INSTANCE_ID") or None,
    )
