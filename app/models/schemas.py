"""
Request/response contracts for the API.

The input polygon is validated as real GeoJSON via geojson-pydantic, so
malformed geometries fail fast with a clear 422 instead of blowing up
deep inside the Dask graph.
"""
from datetime import date
from enum import Enum
from typing import Literal

from geojson_pydantic import Feature, Polygon
from pydantic import BaseModel, Field, field_validator


class VegetationIndex(str, Enum):
    NDVI = "NDVI"
    EVI = "EVI"
    SAVI = "SAVI"
    MNDWI = "MNDWI"


class AnalysisRequest(BaseModel):
    geometry: Polygon | Feature = Field(
        ..., description="GeoJSON Polygon or Feature defining the area of interest"
    )
    start_date: date = Field(..., description="Start of the time-series window")
    end_date: date = Field(..., description="End of the time-series window")
    indices: list[VegetationIndex] = Field(
        default=[VegetationIndex.NDVI],
        description="Which vegetation/water indices to compute",
    )
    collection: str | None = Field(
        default=None, description="STAC collection override, e.g. 'sentinel-2-l2a'"
    )
    max_cloud_cover: int = Field(
        default=20, ge=0, le=100, description="Max scene cloud cover percentage to include"
    )
    resample_freq: Literal["D", "W", "MS", "QS"] = Field(
        default="MS", description="Temporal resampling frequency (pandas offset alias)"
    )

    @field_validator("end_date")
    @classmethod
    def end_after_start(cls, v, info):
        start = info.data.get("start_date")
        if start and v <= start:
            raise ValueError("end_date must be after start_date")
        return v


class TimeSeriesPoint(BaseModel):
    date: date
    value: float | None
    n_observations: int
    cloud_free_pct: float | None = None


class MapLayer(BaseModel):
    """What a web map needs to place the composite overlay and draw its legend."""
    bounds: list[list[float]] = Field(..., description="[[south, west], [north, east]] in WGS84 degrees")
    vmin: float = Field(..., description="Value at the bottom of the colour scale (2nd percentile)")
    vmax: float = Field(..., description="Value at the top of the colour scale (98th percentile)")
    colormap: str
    legend_colors: list[str] = Field(..., description="Hex colours from vmin to vmax, for a legend")


class IndexResult(BaseModel):
    index: VegetationIndex
    mean: float | None
    min: float | None
    max: float | None
    std: float | None
    trend_per_year: float | None = None
    time_series: list[TimeSeriesPoint]
    chart_json: str = Field(..., description="Plotly figure JSON for client-side rendering")
    map: MapLayer | None = Field(None, description="Overlay placement and legend for the composite map")
    files: list[str] = Field(
        default_factory=list,
        description="Downloadable file kinds for this index: fetch from /jobs/{job_id}/files/{index}/{kind}",
    )


class AnalysisResponse(BaseModel):
    job_id: str
    aoi_area_km2: float
    n_scenes_used: int
    collection: str
    date_range: tuple[date, date]
    results: list[IndexResult]


class JobStatus(BaseModel):
    job_id: str
    status: Literal["queued", "running", "complete", "failed"]
    progress: float = 0.0
    result: AnalysisResponse | None = None
    error: str | None = None
