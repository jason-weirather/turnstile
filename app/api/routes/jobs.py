from fastapi import APIRouter, HTTPException, status

from app.models.job import JobCancelResponse, JobResponse
from app.services.jobs import cancel_job, get_job_response

router = APIRouter()


@router.get("/jobs/{job_id}", response_model=JobResponse)
def get_job(job_id: str) -> JobResponse:
    job = get_job_response(job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job '{job_id}' was not found.",
        )
    return job


@router.post("/jobs/{job_id}/cancel", response_model=JobCancelResponse)
def cancel_job_endpoint(job_id: str) -> JobCancelResponse:
    job = cancel_job(job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job '{job_id}' was not found.",
        )
    return job
