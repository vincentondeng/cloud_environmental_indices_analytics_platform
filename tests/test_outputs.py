"""Composite files, map overlay placement and the file endpoint (no network)."""
import io

import numpy as np
import rasterio
import rioxarray  # noqa: F401
import xarray as xr
from fastapi.testclient import TestClient
from PIL import Image

from app.api import routes
from app.config import get_settings
from app.core import results
from app.main import app
from app.models.schemas import AnalysisResponse, IndexResult, JobStatus
from app.utils.geotiff import wgs84_bounds, write_geotiff
from app.utils.raster_image import display_range, overlay_png


def _composite():
    # 40 x 60 px, 10 m, centre-labelled EPSG:3857 grid near Nairobi, with
    # one empty (NaN) corner standing in for pixels outside the polygon.
    x = 4_080_000 + 5 + 10 * np.arange(60)
    y = -130_000 - 5 - 10 * np.arange(40)
    data = np.linspace(0.1, 0.8, 40 * 60, dtype="float32").reshape(40, 60)
    data[:10, :10] = np.nan
    return xr.DataArray(data, dims=("y", "x"), coords={"x": x, "y": y}).rio.write_crs("EPSG:3857")


def test_overlay_is_transparent_exactly_where_data_is_missing():
    comp = _composite()
    img = np.array(Image.open(io.BytesIO(overlay_png(comp.values, "NDVI", *display_range(comp.values)))))
    assert img.shape == (40, 60, 4)
    assert (img[:10, :10, 3] == 0).all()   # no data -> fully transparent
    assert (img[20:, 20:, 3] == 255).all()  # data -> fully opaque


def test_overlay_is_downsampled_for_large_rasters():
    big = np.random.rand(5000, 3000).astype("float32")
    img = Image.open(io.BytesIO(overlay_png(big, "NDVI", 0.0, 1.0, max_px=2048)))
    assert max(img.size) <= 2048


def test_geotiff_keeps_crs_and_pixel_edges(tmp_path):
    comp = _composite()
    path = tmp_path / "NDVI.tif"
    write_geotiff(comp, path)
    with rasterio.open(path) as src:
        assert src.crs.to_epsg() == 3857
        # Centre coordinate 4,080,005 means the left edge is at 4,080,000:
        # this is the half-pixel alignment fixed earlier.
        assert src.transform.c == 4_080_000 and src.transform.f == -130_000
        assert src.res == (10.0, 10.0)
        assert np.isnan(src.read(1)[0, 0])


def test_overlay_bounds_are_south_west_then_north_east():
    (south, west), (north, east) = wgs84_bounds(_composite())
    assert south < north and west < east
    assert 36.6 < west < 36.7 and -1.2 < south < -1.1


def test_public_error_hides_signed_urls():
    exc = RuntimeError("Error reading from 'https://x.blob.core.windows.net/a.tif?sig=SECRET': failed")
    msg = routes._public_error(exc)
    assert "SECRET" not in msg and "blob.core" not in msg and "failed" in msg


def test_expired_jobs_are_found_by_age():
    created = {"old": 0.0, "new": 100_000.0}
    assert results.expired_job_ids(created, ttl_hours=24, now=100_000.0) == ["old"]


def test_file_endpoint_serves_only_known_files_of_complete_jobs(tmp_path):
    get_settings().results_dir = str(tmp_path)
    job_id = "job-123"
    results.job_dir(job_id)
    results.file_path(job_id, "NDVI", "overlay").write_bytes(b"\x89PNG fake")
    routes._jobs[job_id] = JobStatus(
        job_id=job_id, status="complete",
        result=AnalysisResponse(
            job_id=job_id, aoi_area_km2=1.0, n_scenes_used=1, collection="sentinel-2-l2a",
            date_range=("2023-01-01", "2023-02-01"),
            results=[IndexResult(index="NDVI", mean=0.5, min=0.4, max=0.6, std=0.1,
                                 time_series=[], chart_json="{}", files=["overlay"])],
        ),
    )
    client = TestClient(app)  # no `with`: these routes don't need the Dask cluster
    try:
        ok = client.get(f"/api/v1/jobs/{job_id}/files/NDVI/overlay")
        assert ok.status_code == 200 and ok.content == b"\x89PNG fake"
        assert client.get(f"/api/v1/jobs/{job_id}/files/NDVI/geotiff").status_code == 404  # not written
        assert client.get(f"/api/v1/jobs/{job_id}/files/NDVI/secrets").status_code == 422  # unknown kind
        assert client.get(f"/api/v1/jobs/{job_id}/files/..%2F..%2Fetc/overlay").status_code in (404, 422)
        assert client.get("/api/v1/jobs/nope/files/NDVI/overlay").status_code == 404
    finally:
        routes._jobs.pop(job_id, None)


def test_pipeline_writes_all_three_files_and_map_metadata(tmp_path):
    from app.core.pipeline import _write_composite_files

    layer = _write_composite_files(_composite(), "NDVI", tmp_path)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["NDVI.tif", "NDVI_figure.png", "NDVI_overlay.png"]
    assert layer.vmin < layer.vmax and layer.colormap == "RdYlGn" and len(layer.legend_colors) == 7
