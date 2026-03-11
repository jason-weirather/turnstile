from fastapi import APIRouter, status

from app.models.image import ImageGenerateAccepted, ImageGenerateRequest
from app.services.jobs import submit_image_generate_job

router = APIRouter()


@router.post(
    "/image/generate",
    response_model=ImageGenerateAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
def generate_image(payload: ImageGenerateRequest) -> ImageGenerateAccepted:
    return submit_image_generate_job(payload)
