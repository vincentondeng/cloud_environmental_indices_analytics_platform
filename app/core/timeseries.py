"""
Turns a lazy (time, y, x) Dask-backed index cube into a clean,
regularly-spaced time series ready for charting and stats.

Split into a "build lazy reductions" step and a "turn computed results
into a DataFrame" step, so a caller that needs more than one reduction
from the same cube (e.g. the pipeline also wants a time-mean composite
image) can submit everything in a single dask.compute() call rather
than recomputing the same upstream STAC read + index math once per
reduction.
"""
import dask
import numpy as np
import pandas as pd
import xarray as xr

from app.models.schemas import TimeSeriesPoint


def build_lazy_mean_and_valid(index_cube: xr.DataArray, footprint: xr.DataArray | None = None):
    """
    Returns (spatial_mean, valid_frac) as LAZY xarray objects — not computed yet.
    footprint: optional 2D mask of pixels inside the AOI polygon. Pixels
    outside it are NaN but aren't cloudy, so the valid fraction divides by
    the in-polygon pixel count, not the whole bounding box.
    """
    spatial_mean = index_cube.mean(dim=["x", "y"], skipna=True)
    valid_count = index_cube.notnull().sum(dim=["x", "y"])
    if footprint is not None:
        # Stays lazy: summed inside the same compute as everything else.
        total = footprint.sum().clip(min=1)
    else:
        total = max(index_cube.sizes["x"] * index_cube.sizes["y"], 1)
    valid_frac = valid_count / total
    return spatial_mean, valid_frac


def mean_valid_to_dataframe(computed_mean, computed_valid, resample_freq: str = "MS") -> pd.DataFrame:
    """Takes ALREADY-COMPUTED mean/valid arrays (plain numpy-backed xarray, post dask.compute()) and builds the resampled DataFrame."""
    df = pd.DataFrame(
        {
            "value": computed_mean.values,
            "cloud_free_pct": (computed_valid.values * 100).round(1),
        },
        index=pd.to_datetime(computed_mean["time"].values),
    ).sort_index()

    if resample_freq:
        agg = df.resample(resample_freq).agg(
            value=("value", "mean"),
            n_observations=("value", "count"),
            cloud_free_pct=("cloud_free_pct", "mean"),
        )
        return agg

    df["n_observations"] = 1
    return df


def reduce_to_timeseries(index_cube: xr.DataArray, resample_freq: str = "MS") -> pd.DataFrame:
    """
    Standalone convenience wrapper — computes mean+valid together in one
    shared dask.compute() call. Used by callers (the test notebook, direct
    scripts) that only need the time series, not a composite image. The
    pipeline itself calls build_lazy_mean_and_valid() + dask.compute()
    directly instead, so it can fold the composite image into the same
    compute call.
    """
    spatial_mean, valid_frac = build_lazy_mean_and_valid(index_cube)
    computed_mean, computed_valid = dask.compute(spatial_mean, valid_frac)
    return mean_valid_to_dataframe(computed_mean, computed_valid, resample_freq)


def to_points(df: pd.DataFrame) -> list[TimeSeriesPoint]:
    return [
        TimeSeriesPoint(
            date=idx.date(),
            value=None if pd.isna(row["value"]) else round(float(row["value"]), 4),
            n_observations=int(row["n_observations"]),
            cloud_free_pct=None if pd.isna(row.get("cloud_free_pct")) else round(float(row["cloud_free_pct"]), 1),
        )
        for idx, row in df.iterrows()
    ]


def linear_trend_per_year(df: pd.DataFrame) -> float | None:
    valid = df.dropna(subset=["value"])
    if len(valid) < 3:
        return None
    t_years = (valid.index - valid.index[0]).days / 365.25
    slope, _ = np.polyfit(t_years, valid["value"], 1)
    return round(float(slope), 5)


def summary_stats(df: pd.DataFrame) -> dict:
    valid = df["value"].dropna()
    if valid.empty:
        return {"mean": None, "min": None, "max": None, "std": None}
    return {
        "mean": round(float(valid.mean()), 4),
        "min": round(float(valid.min()), 4),
        "max": round(float(valid.max()), 4),
        "std": round(float(valid.std()), 4),
    }
