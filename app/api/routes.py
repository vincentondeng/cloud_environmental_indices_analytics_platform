"""
Analysis runs as a background job, not inline in the request: a
multi-scene Dask reduction can take anywhere from seconds to minutes,
well past what should block an HTTP connection.

Finished jobs keep their raster files on disk (see app/core/results.py)
and are served from /jobs/{job_id}/files/{index}/{kind}. Jobs and their
files are removed after JOB_TTL_HOURS, so neither memory nor disk grows
without limit.
"""
import logging
import re
import time
import uuid
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse

from app.config import get_settings
from app.core import results
from app.core.dask_cluster import get_dask_client
from app.core.pipeline import run_analysis
from app.models.schemas import AnalysisRequest, JobStatus, VegetationIndex

logger = logging.getLogger("ndvi-platform.api")
router = APIRouter()

# In-memory job store: fine for a single-process deployment. Swap for
# Redis or a database table before running more than one API process.
_jobs: dict[str, JobStatus] = {}
_created_at: dict[str, float] = {}

_URL = re.compile(r"https?://\S+")


def _public_error(exc: Exception) -> str:
    """
    Error text that is safe to return to a client. Remote read failures
    embed the full signed blob URL (including its access token) in the
    message; the full detail still goes to the server log.
    """
    return _URL.sub("<remote file>", str(exc))


def _purge_expired() -> None:
    for job_id in results.expired_job_ids(_created_at, get_settings().job_ttl_hours):
        _jobs.pop(job_id, None)
        _created_at.pop(job_id, None)
        results.remove_job_files(job_id)


def _execute(job_id: str, request: AnalysisRequest):
    _jobs[job_id].status = "running"
    try:
        result = run_analysis(request, output_dir=results.job_dir(job_id))
        result.job_id = job_id
        _jobs[job_id] = JobStatus(job_id=job_id, status="complete", progress=1.0, result=result)
    except Exception as exc:
        logger.exception(f"Job {job_id} failed")
        _jobs[job_id] = JobStatus(job_id=job_id, status="failed", error=_public_error(exc))
        results.remove_job_files(job_id)


@router.post("/analyze", response_model=JobStatus, status_code=202)
async def submit_analysis(request: AnalysisRequest, background_tasks: BackgroundTasks):
    _purge_expired()
    job_id = str(uuid.uuid4())
    _jobs[job_id] = JobStatus(job_id=job_id, status="queued")
    _created_at[job_id] = time.time()
    background_tasks.add_task(_execute, job_id, request)
    return _jobs[job_id]


@router.get("/jobs/{job_id}", response_model=JobStatus)
async def get_job(job_id: str):
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.get("/jobs/{job_id}/files/{index}/{kind}")
async def get_job_file(job_id: str, index: VegetationIndex, kind: Literal["geotiff", "overlay", "figure"]):
    # The job must exist in the store: an arbitrary job_id can't be used to
    # probe the results folder, and index/kind are validated enums.
    job = _jobs.get(job_id)
    if job is None or job.status != "complete":
        raise HTTPException(status_code=404, detail="Job not found or not complete")

    path = results.file_path(job_id, index.value, kind)
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"No {kind} file for {index.value} in this job")
    download_name = f"{index.value}_composite.tif" if kind == "geotiff" else path.name
    return FileResponse(path, media_type=results.media_type(kind), filename=download_name)


@router.get("/health")
async def health():
    try:
        client = get_dask_client()
        info = client.scheduler_info()
        return {"status": "ok", "dask_workers": len(info.get("workers", {})), "dashboard": client.dashboard_link}
    except RuntimeError:
        return {"status": "degraded", "detail": "Dask client not initialized"}
