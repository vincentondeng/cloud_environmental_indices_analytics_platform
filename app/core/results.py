"""
Where finished jobs keep their raster files, and how old ones are removed.

Rasters used to travel inside the JSON response as base64, which made the
response tens of megabytes for a large AOI and kept every result in the API
process's memory forever. Now each job writes its files to its own folder,
the response carries only small metadata, and files are fetched on request
from /jobs/{job_id}/files/{index}/{kind}.
"""
import shutil
import time
from pathlib import Path

from app.config import get_settings

# File kind -> filename pattern. Only these names can ever be served, so a
# request can't reach outside a job's folder whatever it puts in the URL.
FILE_KINDS = {
    "geotiff": ("{index}.tif", "image/tiff"),
    "overlay": ("{index}_overlay.png", "image/png"),
    "figure": ("{index}_figure.png", "image/png"),
}


def results_root() -> Path:
    root = Path(get_settings().results_dir)
    root.mkdir(parents=True, exist_ok=True)
    return root


def job_dir(job_id: str) -> Path:
    path = results_root() / job_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def file_path(job_id: str, index: str, kind: str) -> Path:
    pattern, _ = FILE_KINDS[kind]
    return results_root() / job_id / pattern.format(index=index)


def media_type(kind: str) -> str:
    return FILE_KINDS[kind][1]


def remove_job_files(job_id: str) -> None:
    shutil.rmtree(results_root() / job_id, ignore_errors=True)


def expired_job_ids(created_at: dict[str, float], ttl_hours: float, now: float | None = None) -> list[str]:
    now = time.time() if now is None else now
    cutoff = now - ttl_hours * 3600
    return [job_id for job_id, created in created_at.items() if created < cutoff]
