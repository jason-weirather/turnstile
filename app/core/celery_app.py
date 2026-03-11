from celery import Celery

from app.core.config import get_settings


def create_celery_app() -> Celery:
    settings = get_settings()
    celery = Celery(
        "turnstile",
        broker=settings.celery_broker_url,
        backend=settings.celery_result_backend,
    )
    celery.conf.update(
        task_default_queue="default",
        task_track_started=True,
        task_serializer="json",
        accept_content=["json"],
        result_serializer="json",
        result_extended=True,
    )
    return celery


celery_app = create_celery_app()
