"""Same-day tile merging, on synthetic data (no network)."""
import numpy as np
import xarray as xr

from app.core.stac_client import BAND_ALIASES
from app.core.vegetation_indices import VegetationIndexEngine


def test_same_day_tiles_merge_into_one_full_image():
    times = np.array(["2023-01-05T07:40", "2023-01-05T07:41", "2023-01-10T07:40"], dtype="datetime64[ns]")
    data = np.full((3, 2, 2), np.nan, dtype="float32")
    data[0, :, 0] = 0.6  # tile A covers the left column only
    data[1, :, 1] = 0.4  # tile B covers the right column only
    data[2, :, :] = 0.5  # a later day, full coverage
    arr = xr.DataArray(
        data,
        dims=("time", "y", "x"),
        coords={
            "time": times,
            "y": [1.0, 0.0],
            "x": [0.0, 1.0],
            "s2:processing_baseline": ("time", ["05.10", "05.10", "05.10"]),
        },
    )

    merged = VegetationIndexEngine(items=[], bands=BAND_ALIASES["sentinel-2-l2a"])._merge_same_day(arr)

    assert merged.sizes["time"] == 2  # two days, not three items
    first_day = merged.isel(time=0).values
    assert not np.isnan(first_day).any()  # the two half-tiles fill each other in
    assert np.allclose(first_day[:, 0], 0.6) and np.allclose(first_day[:, 1], 0.4)
    assert np.allclose(merged.isel(time=1).values, 0.5)
