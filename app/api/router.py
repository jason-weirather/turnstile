from fastapi import APIRouter

from app.api.routes import health, jobs, ops, services
from app.api.routes.image import router as image_router

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(services.router, prefix="/v1")
api_router.include_router(jobs.router, prefix="/v1")
api_router.include_router(image_router, prefix="/v1")
api_router.include_router(ops.router)
