"""
Analysis runs as a background job, not inline in the request: a
multi-scene Dask reduction can take anywhere from seconds to minutes,
well past what should block an HTTP connection.
"""
import logging
import uuid

from fastapi import APIRouter, BackgroundTasks, HTTPException

from app.core.dask_cluster import get_dask_client
from app.core.pipeline import run_analysis
from app.models.schemas import AnalysisRequest, JobStatus

logger = logging.getLogger("ndvi-platform.api")
router = APIRouter()

# In-memory job store: fine for a single-process demo deployment.
_jobs: dict[str, JobStatus] = {}


def _execute(job_id: str, request: AnalysisRequest):
    _jobs[job_id].status = "running"
    try:
        result = run_analysis(request)
        result.job_id = job_id
        _jobs[job_id] = JobStatus(job_id=job_id, status="complete", progress=1.0, result=result)
    except Exception as exc:
        logger.exception(f"Job {job_id} failed")
        _jobs[job_id] = JobStatus(job_id=job_id, status="failed", error=str(exc))


@router.post("/analyze", response_model=JobStatus, status_code=202)
async def submit_analysis(request: AnalysisRequest, background_tasks: BackgroundTasks):
    job_id = str(uuid.uuid4())
    _jobs[job_id] = JobStatus(job_id=job_id, status="queued")
    background_tasks.add_task(_execute, job_id, request)
    return _jobs[job_id]


@router.get("/jobs/{job_id}", response_model=JobStatus)
async def get_job(job_id: str):
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.get("/health")
async def health():
    try:
        client = get_dask_client()
        info = client.scheduler_info()
        return {"status": "ok", "dask_workers": len(info.get("workers", {})), "dashboard": client.dashboard_link}
    except RuntimeError:
        return {"status": "degraded", "detail": "Dask client not initialized"}
