"""
Vegetation/water index computation on a lazily-loaded, Dask-backed
xarray DataCube built via stackstac.

Class-based and catalog-agnostic by design: pass in whichever band
mapping the STAC collection uses (see stac_client.BAND_ALIASES) and the
same class computes NDVI/EVI/SAVI/MNDWI regardless of Sentinel-2 vs
Landsat vs anything else with the right bands.
"""
import dask.array as da
import numpy as np
from affine import Affine
import rioxarray  # noqa: F401 (registers the .rio accessor)
import stackstac
import xarray as xr
from rasterio.features import geometry_mask
from rasterio.warp import transform_geom


class VegetationIndexEngine:
    # Raw assets are scaled integers, not reflectance. Landsat C2 L2 uses a
    # fixed documented scale/offset. Sentinel-2 L2A uses scale 1/10000, but
    # its offset depends on each scene's processing baseline (see
    # _scene_offset), so the 0.0 here is a placeholder that is overridden.
    REFLECTANCE_SCALE = {
        "sentinel-2-l2a": {"scale": 1 / 10000, "offset": 0.0},
        "landsat-c2-l2": {"scale": 0.0000275, "offset": -0.2},
    }

    # ESA processing baseline 04.00+ stores L2A as DN = reflectance*10000 + 1000,
    # so reflectance = DN/10000 - 0.1. Older baselines have no offset. The
    # baseline is a property of each scene (reprocessed archives carry newer
    # baselines), not of the acquisition date, so it is decided per scene.
    S2_OFFSET_BASELINE = 4.0
    S2_OFFSET = -0.1

    CLOUD_MASK_BAND = {
        "sentinel-2-l2a": "SCL",
    }
    SCL_BAD_VALUES = {0, 1, 3, 8, 9, 10}

    INDEX_BAND_KEYS = {
        "NDVI": ["red", "nir"],
        "EVI": ["red", "nir", "blue"],
        "SAVI": ["red", "nir"],
        "MNDWI": ["green", "swir16"],
    }

    def __init__(self, items: list, bands: dict, resolution: int = 10, epsg: int = 3857, collection: str = "sentinel-2-l2a"):
        self.items = items
        self.bands = bands
        self.resolution = resolution
        self.epsg = epsg
        self.collection = collection
        self.reflectance = self.REFLECTANCE_SCALE.get(collection, {"scale": 1.0, "offset": 0.0})
        self.mask_band = self.CLOUD_MASK_BAND.get(collection)
        self._mask_cache: xr.DataArray | None = None
        self._polygon_cache: xr.DataArray | None = None

    @property
    def footprint(self) -> xr.DataArray | None:
        """2D (y, x) boolean mask of pixels inside the AOI polygon, once computed."""
        return self._polygon_cache

    def _resolve_asset(self, key: str) -> str:
        if key == "blue" and "blue" not in self.bands:
            key = "green"
        return self.bands[key]

    def _stack_assets(self, geometry: dict, assets: list[str]) -> xr.DataArray:
        # xy_coords="center": label pixels by their centres. stackstac's
        # default is the top-left corner, but rioxarray (used for clipping
        # and GeoTIFF export) assumes centres, which shifted the exported
        # GeoTIFF by half a pixel. Every stack must use the same setting so
        # the bands, cloud mask and polygon mask share one grid.
        return stackstac.stack(
            self.items,
            assets=assets,
            resolution=self.resolution,
            epsg=self.epsg,
            bounds_latlon=self._bbox(geometry),
            rescale=False,
            chunksize="auto",
            xy_coords="center",
        )

    def _stack(self, geometry: dict, band_keys: list[str]) -> xr.DataArray:
        assets = []
        for key in band_keys:
            asset = self._resolve_asset(key)
            if asset not in assets:
                assets.append(asset)
        return self._stack_assets(geometry, assets)

    @staticmethod
    def _bbox(geometry: dict) -> tuple:
        coords = geometry["coordinates"][0] if geometry["type"] == "Polygon" else geometry["geometry"]["coordinates"][0]
        xs = [c[0] for c in coords]
        ys = [c[1] for c in coords]
        return (min(xs), min(ys), max(xs), max(ys))

    def _scene_offset(self, stack: xr.DataArray):
        if self.collection != "sentinel-2-l2a":
            return self.reflectance["offset"]

        key = "s2:processing_baseline"
        if key not in stack.coords:
            raise ValueError(
                "Scene metadata has no s2:processing_baseline, so the correct "
                "reflectance offset can't be determined."
            )
        baseline = stack.coords[key]
        is_new = np.vectorize(lambda b: float(b) >= self.S2_OFFSET_BASELINE)(baseline.values)
        offsets = np.where(is_new, self.S2_OFFSET, 0.0)
        return xr.DataArray(offsets, coords=baseline.coords, dims=baseline.dims)

    def _band(self, stack: xr.DataArray, key: str) -> xr.DataArray:
        asset = self._resolve_asset(key)
        raw = stack.sel(band=asset).astype("float32")
        raw = raw.where(raw != 0)  # DN 0 is nodata
        return raw * self.reflectance["scale"] + self._scene_offset(stack)

    def _polygon_mask(self, template: xr.DataArray, geometry: dict) -> xr.DataArray:
        """
        True for pixels inside the AOI polygon. The stack covers the
        polygon's bounding box, so without this an irregular boundary
        would average in everything inside its bbox. all_touched=True
        keeps edge pixels that the boundary passes through.
        """
        if self._polygon_cache is not None:
            return self._polygon_cache

        grid = template.rio.write_crs(f"EPSG:{self.epsg}")
        transform = grid.rio.transform()
        geom_projected = transform_geom("EPSG:4326", f"EPSG:{self.epsg}", geometry)

        # Built lazily, one block per data chunk, on the workers. Building the
        # whole mask here embedded a 1-byte-per-pixel array in the Dask graph
        # (15 MiB for a 1,000 km2 AOI). Each block now rasterizes the polygon
        # onto its own window, so only the polygon travels with the graph.
        chunksizes = getattr(template, "chunksizes", None) or {}
        ychunks = chunksizes.get("y", (grid.sizes["y"],))
        xchunks = chunksizes.get("x", (grid.sizes["x"],))

        def rasterize_block(block, block_info=None):
            (y0, y1), (x0, x1) = block_info[0]["array-location"]
            return geometry_mask(
                [geom_projected],
                out_shape=(y1 - y0, x1 - x0),
                transform=transform @ Affine.translation(x0, y0),
                invert=True,
                all_touched=True,
            )

        base = da.zeros((grid.sizes["y"], grid.sizes["x"]), chunks=(ychunks, xchunks), dtype=bool)
        inside = base.map_blocks(rasterize_block, dtype=bool)
        self._polygon_cache = xr.DataArray(inside, coords={"y": grid["y"], "x": grid["x"]}, dims=("y", "x"))
        return self._polygon_cache

    def _cloud_valid_mask(self, geometry: dict) -> xr.DataArray | None:
        if self.mask_band is None:
            return None
        if self._mask_cache is not None:
            return self._mask_cache

        scl = self._stack_assets(geometry, [self.mask_band]).sel(band=self.mask_band)
        self._mask_cache = ~scl.isin(list(self.SCL_BAD_VALUES))
        return self._mask_cache

    def _merge_same_day(self, data: xr.DataArray) -> xr.DataArray:
        """
        An AOI spanning several Sentinel-2 tiles gets one STAC item per tile
        per pass. Each covers only part of the AOI, so treating them as
        separate observations understates clear-sky coverage and averages
        partial-area snapshots as if each were the whole area. This merges
        each day's items into one image, keeping the first clear value at
        each pixel. It must run AFTER cloud/nodata masking, so a clear pixel
        from one tile is never overwritten by a cloudy one from another.
        Grouping is by UTC day: one orbit pass falls within a single UTC day
        everywhere except right next to the antimeridian.
        """
        days = data["time"].values.astype("datetime64[D]")
        data = data.reset_coords(drop=True)  # per-scene properties don't survive a merge
        merged = []
        for day in np.unique(days):
            idx = np.flatnonzero(days == day)
            # drop=True discards each scene's own timestamp, which would
            # otherwise conflict between same-day scenes (07:40 vs 07:41) and
            # be dropped unpredictably by fillna. The day label replaces it.
            combined = data.isel(time=idx[0], drop=True)
            for i in idx[1:]:
                combined = combined.fillna(data.isel(time=i, drop=True))
            merged.append(combined.expand_dims(time=[np.datetime64(day, "ns")]))
        out = xr.concat(merged, dim="time")
        # Write the CRS explicitly so the GeoTIFF export never depends on
        # stackstac's attributes surviving the merge.
        return out.rio.write_crs(f"EPSG:{self.epsg}")

    def compute(self, geometry: dict, index: str) -> xr.DataArray:
        if index not in self.INDEX_BAND_KEYS:
            raise ValueError(f"Unsupported index: {index}")

        stack = self._stack(geometry, self.INDEX_BAND_KEYS[index])
        eps = 1e-6

        if index == "NDVI":
            red, nir = self._band(stack, "red"), self._band(stack, "nir")
            result = (nir - red) / (nir + red + eps)

        elif index == "EVI":
            red, nir, blue = self._band(stack, "red"), self._band(stack, "nir"), self._band(stack, "blue")
            result = 2.5 * (nir - red) / (nir + 6 * red - 7.5 * blue + 1 + eps)

        elif index == "SAVI":
            red, nir = self._band(stack, "red"), self._band(stack, "nir")
            L = 0.5
            result = ((nir - red) / (nir + red + L + eps)) * (1 + L)

        elif index == "MNDWI":
            green, swir = self._band(stack, "green"), self._band(stack, "swir16")
            result = (green - swir) / (green + swir + eps)

        cloud_mask = self._cloud_valid_mask(geometry)
        if cloud_mask is not None:
            result = result.where(cloud_mask)

        result = self._merge_same_day(result)
        result = result.where(self._polygon_mask(result, geometry))

        result.name = index
        return result
