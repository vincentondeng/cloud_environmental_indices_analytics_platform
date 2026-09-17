"""
Turns a lazy (time, y, x) Dask-backed index cube into a clean,
regularly-spaced time series ready for charting and stats.
"""
import dask
import numpy as np
import pandas as pd
import xarray as xr

from app.models.schemas import TimeSeriesPoint


def reduce_to_timeseries(index_cube: xr.DataArray, resample_freq: str = "MS") -> pd.DataFrame:
    spatial_mean = index_cube.mean(dim=["x", "y"], skipna=True)
    valid_frac = index_cube.notnull().mean(dim=["x", "y"])

    # dask.compute() (not two separate .compute() calls) shares one graph
    # across both reductions, so shared upstream work isn't recomputed twice.
    computed_mean, computed_valid = dask.compute(spatial_mean, valid_frac)

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
