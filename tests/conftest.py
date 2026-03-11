from __future__ import annotations

from collections.abc import Generator

import fakeredis
import pytest

from app.services import job_store as job_store_module
from app.services import jobs as jobs_service
from app.services import orchestrator


@pytest.fixture(autouse=True)
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> Generator[fakeredis.FakeRedis, None, None]:
    from app.core.config import get_settings

    settings = get_settings()
    settings.job_ttl_s = 120
    settings.gpu_lock_ttl_s = 5
    settings.arbiter_poll_interval_s = 0.01
    settings.stub_task_delay_s = 0.05

    fake_client = fakeredis.FakeRedis(decode_responses=True)
    store = job_store_module.RedisJobStore(fake_client)

    job_store_module.get_redis_client.cache_clear()
    job_store_module.get_job_store.cache_clear()
    monkeypatch.setattr(job_store_module, "get_redis_client", lambda: fake_client)
    monkeypatch.setattr(job_store_module, "get_job_store", lambda: store)
    monkeypatch.setattr(jobs_service, "get_job_store", lambda: store)
    monkeypatch.setattr(orchestrator, "get_job_store", lambda: store)

    yield fake_client

    store.clear()


@pytest.fixture(autouse=True)
def eager_celery() -> Generator[None, None, None]:
    from app.tasks import execute_capability_task

    execute_capability_task.app.conf.task_always_eager = True
    execute_capability_task.app.conf.task_store_eager_result = True
    execute_capability_task.app.conf.result_backend = "cache+memory://"
    execute_capability_task.app.conf.broker_url = "memory://"
    yield
    execute_capability_task.app.conf.task_always_eager = True
