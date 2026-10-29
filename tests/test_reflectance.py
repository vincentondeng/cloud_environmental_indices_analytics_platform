"""
Checks the reflectance conversion on synthetic data, no network needed.
Sentinel-2 baseline >= 04.00 must get the -0.1 offset, older baselines
must not, and DN 0 must become NaN rather than a reflectance of -0.1.
"""
import numpy as np
import xarray as xr

from app.core.stac_client import BAND_ALIASES
from app.core.vegetation_indices import VegetationIndexEngine


def _fake_stack(baselines):
    # 1 band, 2x2 pixels, one scene per baseline. DN 1500 everywhere except
    # one nodata (0) pixel.
    data = np.full((len(baselines), 1, 2, 2), 1500, dtype="uint16")
    data[:, 0, 0, 0] = 0
    return xr.DataArray(
        data,
        dims=("time", "band", "y", "x"),
        coords={"band": ["B04"], "s2:processing_baseline": ("time", baselines)},
    )


def _engine():
    return VegetationIndexEngine(items=[], bands=BAND_ALIASES["sentinel-2-l2a"])


def test_offset_applied_per_scene_by_baseline():
    red = _engine()._band(_fake_stack(["03.01", "05.10"]), "red")
    assert np.isclose(float(red.isel(time=0, y=1, x=1)), 0.15)  # old baseline: no offset
    assert np.isclose(float(red.isel(time=1, y=1, x=1)), 0.05)  # new baseline: -0.1


def test_nodata_becomes_nan_not_negative_reflectance():
    red = _engine()._band(_fake_stack(["05.10"]), "red")
    assert np.isnan(float(red.isel(time=0, y=0, x=0)))


def test_baseline_exactly_0400_gets_offset():
    red = _engine()._band(_fake_stack(["04.00"]), "red")
    assert np.isclose(float(red.isel(time=0, y=1, x=1)), 0.05)
