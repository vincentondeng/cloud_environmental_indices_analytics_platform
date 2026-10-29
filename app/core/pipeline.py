"""
Orchestrates: polygon -> STAC search -> Dask-backed index cube ->
[spatial mean, valid fraction, time-mean composite] (one shared compute
call) -> time series / chart / composite PNG+GeoTIFF.
"""
import math
import uuid

import dask
from pyproj import Geod
from shapely.geometry import shape

from app.config import get_settings
from app.core.dask_cluster import get_dask_client
from app.core.stac_client import StacSearchClient
from app.core.timeseries import build_lazy_mean_and_valid, linear_trend_per_year, mean_valid_to_dataframe, summary_stats, to_points
from app.core.vegetation_indices import VegetationIndexEngine
from app.models.schemas import AnalysisRequest, AnalysisResponse, IndexResult
from app.utils.charts import build_timeseries_chart
from app.utils.geotiff import array_to_geotiff_base64
from app.utils.raster_image import array_to_png_base64

settings = get_settings()


def _extract_geometry(geom_or_feature) -> dict:
    d = geom_or_feature.model_dump(exclude_none=True)
    return d["geometry"] if d.get("type") == "Feature" else d


def _aoi_area_km2(geometry: dict) -> float:
    # Geodesic area on the WGS84 ellipsoid: accurate at any latitude,
    # unlike a degrees-to-km approximation.
    area_m2, _ = Geod(ellps="WGS84").geometry_area_perimeter(shape(geometry))
    return abs(area_m2) / 1e6


def run_analysis(request: AnalysisRequest) -> AnalysisResponse:
    get_dask_client()  # raises clearly if the cluster isn't up

    geometry = _extract_geometry(request.geometry)
    area_km2 = _aoi_area_km2(geometry)
    if area_km2 > settings.max_polygon_area_km2:
        raise ValueError(f"AOI is {area_km2:,.0f} km2, exceeds {settings.max_polygon_area_km2:,.0f} km2 limit.")

    collection = request.collection or settings.default_collection

    stac = StacSearchClient()
    items = stac.search(
        geometry=geometry, start=request.start_date, end=request.end_date,
        collection=collection, max_cloud_cover=request.max_cloud_cover,
    )
    if not items:
        raise ValueError("No scenes found for this AOI/date range/cloud-cover combination.")

    bands = stac.bands_for(collection)
    engine = VegetationIndexEngine(items=items, bands=bands, collection=collection)

    results: list[IndexResult] = []
    for index in request.indices:
        cube = engine.compute(geometry, index.value)

        # Build every reduction we need as LAZY objects first — nothing
        # computes yet — then submit them all in one dask.compute() call.
        # This is the fix for computing each index twice: previously
        # reduce_to_timeseries() ran its own dask.compute() for the
        # spatial mean/valid fraction, and the composite was computed
        # separately afterward, re-running the STAC read + index math
        # from scratch. Sharing one call means that work happens once.
        spatial_mean, valid_frac = build_lazy_mean_and_valid(cube, footprint=engine.footprint)
        composite = cube.mean(dim="time", skipna=True)

        computed_mean, computed_valid, computed_composite = dask.compute(
            spatial_mean, valid_frac, composite
        )

        df = mean_valid_to_dataframe(computed_mean, computed_valid, request.resample_freq)
        trend = linear_trend_per_year(df)
        stats = summary_stats(df)
        chart_json = build_timeseries_chart(df, index.value, trend)

        png_b64 = array_to_png_base64(computed_composite.values, index.value)
        tif_b64 = array_to_geotiff_base64(computed_composite)

        results.append(IndexResult(
            index=index, mean=stats["mean"], min=stats["min"], max=stats["max"], std=stats["std"],
            trend_per_year=trend, time_series=to_points(df), chart_json=chart_json,
            composite_image_png_base64=png_b64,
            composite_image_tif_base64=tif_b64,
        ))

    return AnalysisResponse(
        job_id=str(uuid.uuid4()), aoi_area_km2=round(area_km2, 2),
        n_scenes_used=len(items), collection=collection,
        date_range=(request.start_date, request.end_date), results=results,
    )
