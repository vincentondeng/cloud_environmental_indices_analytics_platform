"""The chunked polygon mask must match a single whole-raster rasterization."""
import dask.array as da
import numpy as np
import xarray as xr
from rasterio.features import geometry_mask
from rasterio.warp import transform_geom

from app.core.stac_client import BAND_ALIASES
from app.core.vegetation_indices import VegetationIndexEngine


def test_chunked_mask_matches_whole_raster():
    # 300 x 200 pixel, 10 m, centre-labelled EPSG:3857 grid near Nairobi,
    # deliberately split into chunks that don't divide the grid evenly.
    x = 4_080_000 + 5 + 10 * np.arange(300)
    y = -130_000 - 5 - 10 * np.arange(200)
    template = xr.DataArray(
        da.zeros((1, 200, 300), chunks=(1, 64, 64)),
        dims=("time", "y", "x"),
        coords={"x": x, "y": y},
    )
    polygon = {"type": "Polygon", "coordinates": [[
        [36.660, -1.170], [36.670, -1.175], [36.672, -1.185], [36.661, -1.182], [36.660, -1.170]
    ]]}

    chunked = VegetationIndexEngine(items=[], bands=BAND_ALIASES["sentinel-2-l2a"])._polygon_mask(template, polygon).values

    grid = template.rio.write_crs("EPSG:3857")
    whole = geometry_mask(
        [transform_geom("EPSG:4326", "EPSG:3857", polygon)],
        out_shape=(200, 300),
        transform=grid.rio.transform(),
        invert=True,
        all_touched=True,
    )

    assert chunked.any() and not chunked.all()  # the polygon is really partial
    assert np.array_equal(chunked, whole)
