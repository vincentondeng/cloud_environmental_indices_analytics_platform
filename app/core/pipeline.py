"""
Orchestrates: polygon -> STAC search -> Dask-backed index cube -> time series -> chart.
"""
import math
import uuid

from shapely.geometry import shape

from app.config import get_settings
from app.core.dask_cluster import get_dask_client
from app.core.stac_client import StacSearchClient
from app.core.timeseries import linear_trend_per_year, reduce_to_timeseries, summary_stats, to_points
from app.core.vegetation_indices import VegetationIndexEngine
from app.models.schemas import AnalysisRequest, AnalysisResponse, IndexResult

settings = get_settings()


def _extract_geometry(geom_or_feature) -> dict:
    d = geom_or_feature.model_dump(exclude_none=True)
    return d["geometry"] if d.get("type") == "Feature" else d


def _aoi_area_km2(geometry: dict) -> float:
    geom = shape(geometry)
    centroid_lat = geom.centroid.y
    deg_to_km = 111.32
    lat_scale = max(0.1, abs(math.cos(math.radians(centroid_lat))))
    return geom.area * deg_to_km * deg_to_km * lat_scale


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
    engine = VegetationIndexEngine(items=items, bands=bands)

    from app.utils.charts import build_timeseries_chart

    results: list[IndexResult] = []
    for index in request.indices:
        cube = engine.compute(geometry, index.value)
        df = reduce_to_timeseries(cube, resample_freq=request.resample_freq)
        trend = linear_trend_per_year(df)
        stats = summary_stats(df)
        chart_json = build_timeseries_chart(df, index.value, trend)

        results.append(IndexResult(
            index=index, mean=stats["mean"], min=stats["min"], max=stats["max"], std=stats["std"],
            trend_per_year=trend, time_series=to_points(df), chart_json=chart_json,
        ))

    return AnalysisResponse(
        job_id=str(uuid.uuid4()), aoi_area_km2=round(area_km2, 2),
        n_scenes_used=len(items), collection=collection,
        date_range=(request.start_date, request.end_date), results=results,
    )
