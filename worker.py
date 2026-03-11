import app.tasks  # noqa: F401
from app.core.celery_app import celery_app

__all__ = ["celery_app"]
