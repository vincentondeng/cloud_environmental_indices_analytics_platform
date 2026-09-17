"""
Smoke tests. The heavy STAC/Dask path is mocked out — these check that
request validation, routing, and job-state transitions behave correctly
without needing network access or a full Dask job on every test run.
"""
from datetime import date
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.main import app

SAMPLE_POLYGON = {
    "type": "Polygon",
    "coordinates": [[
        [36.80, -1.30], [36.85, -1.30], [36.85, -1.25], [36.80, -1.25], [36.80, -1.30]
    ]],
}


@pytest.fixture(scope="session")
def client():
    # scope="session": start the local Dask cluster once for the whole
    # test run, not once per test — none of these tests actually exercise
    # Dask (the STAC/compute call is mocked), so repeated start/stop per
    # test is pure overhead and can trip up Dask on macOS.
    with TestClient(app) as c:
        yield c


def test_health(client):
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    assert resp.json()["status"] in ("ok", "degraded")


def test_analyze_rejects_bad_date_range(client):
    payload = {
        "geometry": SAMPLE_POLYGON,
        "start_date": "2024-06-01",
        "end_date": "2024-01-01",
        "indices": ["NDVI"],
    }
    resp = client.post("/api/v1/analyze", json=payload)
    assert resp.status_code == 422


def test_analyze_submits_job_and_polls(client):
    payload = {
        "geometry": SAMPLE_POLYGON,
        "start_date": "2024-01-01",
        "end_date": "2024-03-01",
        "indices": ["NDVI"],
    }
    with patch("app.api.routes.run_analysis") as mock_run:
        from app.models.schemas import AnalysisResponse, IndexResult

        mock_run.return_value = AnalysisResponse(
            job_id="placeholder",
            aoi_area_km2=25.0,
            n_scenes_used=3,
            collection="sentinel-2-l2a",
            date_range=(date(2024, 1, 1), date(2024, 3, 1)),
            results=[
                IndexResult(
                    index="NDVI", mean=0.5, min=0.3, max=0.7, std=0.1,
                    trend_per_year=0.01, time_series=[], chart_json="{}",
                )
            ],
        )
        submit = client.post("/api/v1/analyze", json=payload)
        assert submit.status_code == 202
        job_id = submit.json()["job_id"]

        poll = client.get(f"/api/v1/jobs/{job_id}")
        assert poll.status_code == 200
        assert poll.json()["status"] in ("queued", "running", "complete")


def test_job_not_found(client):
    resp = client.get("/api/v1/jobs/does-not-exist")
    assert resp.status_code == 404
