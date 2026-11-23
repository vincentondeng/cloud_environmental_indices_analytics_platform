"""
Writes a composite xarray DataArray as a real georeferenced GeoTIFF — not
just a flat image. A PNG is a picture; this is actual geospatial data
someone can load into QGIS, ArcGIS or any other GIS tool and work with
directly (reproject it, sample pixel values, combine it with other layers).

The DataArray must carry its CRS (the engine writes it explicitly after the
same-day merge) and centre-labelled x/y coordinates, which is what
rioxarray assumes when it derives the GeoTIFF's transform.
"""
from pathlib import Path

import numpy as np
import rioxarray  # noqa: F401 — import registers the .rio accessor on xarray objects
from rasterio.warp import transform_bounds


def write_geotiff(data_array, path: Path) -> None:
    out = data_array.astype("float32").rio.write_nodata(np.nan, encoded=False)
    # Deflate + horizontal float predictor: typically shrinks an index
    # composite by more than half with no loss. Tiled so GIS tools can read
    # parts of the file without loading all of it.
    out.rio.to_raster(path, driver="GTiff", compress="DEFLATE", predictor=3, tiled=True)


def wgs84_bounds(data_array) -> list[list[float]]:
    """
    Outer pixel-edge bounds in longitude/latitude, as [[south, west],
    [north, east]] — the order Leaflet and folium expect for an image
    overlay. Using the edges (not the centre coordinates) keeps the overlay
    aligned to the GeoTIFF to within a fraction of a pixel.
    """
    west, south, east, north = transform_bounds(data_array.rio.crs, "EPSG:4326", *data_array.rio.bounds())
    return [[south, west], [north, east]]
