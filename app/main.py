from fastapi import FastAPI

from app.api.router import build_api_router
from app.core.config import get_settings


def create_app() -> FastAPI:
    settings = get_settings()
    application = FastAPI(title=settings.app_name)
    application.include_router(build_api_router())
    return application


app = create_app()
