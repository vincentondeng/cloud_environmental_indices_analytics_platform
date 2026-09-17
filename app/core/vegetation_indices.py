"""
Vegetation/water index computation on a lazily-loaded, Dask-backed
xarray DataCube built via stackstac.

Class-based and catalog-agnostic by design: pass in whichever band
mapping the STAC collection uses (see stac_client.BAND_ALIASES) and the
same class computes NDVI/EVI/SAVI/MNDWI regardless of Sentinel-2 vs
Landsat vs anything else with the right bands.
"""
import numpy as np
import stackstac
import xarray as xr


class VegetationIndexEngine:
    # Raw STAC assets are scaled integers, not physical reflectance —
    # Sentinel-2 L2A stores 0-10000 with no offset; Landsat C2 L2 uses
    # USGS's documented scale/offset to convert DN to true reflectance.
    # This doesn't matter for pure ratios (NDVI, MNDWI — the scale cancels
    # algebraically), but it silently breaks any index with an additive
    # constant: EVI's "+1" and SAVI's "L" assume 0-1 reflectance. Against
    # raw ~10000-scale values those constants are negligible — EVI's
    # denominator can land near zero (huge blowup), and SAVI's soil term
    # effectively stops doing anything.
    REFLECTANCE_SCALE = {
        "sentinel-2-l2a": {"scale": 1 / 10000, "offset": 0.0},
        "landsat-c2-l2": {"scale": 0.0000275, "offset": -0.2},
    }

    # Per-pixel cloud/shadow masking via Sentinel-2's Scene Classification
    # Layer (SCL) — separate from, and much finer-grained than, the
    # scene-level eo:cloud_cover filter applied at STAC search time. A
    # scene can pass a 30%-cloud-cover filter while still having cloud or
    # shadow sitting directly over the AOI; SCL flags that per pixel.
    # Landsat has an equivalent (QA_PIXEL bit flags) but with a different
    # encoding — not implemented yet, so masking is skipped for it for now.
    CLOUD_MASK_BAND = {
        "sentinel-2-l2a": "SCL",
    }
    # SCL classes excluded as unusable: 0 no data, 1 saturated/defective,
    # 3 cloud shadow, 8/9 cloud medium/high probability, 10 thin cirrus.
    # Kept as valid: 2 dark area, 4 vegetation, 5 bare soil, 6 water,
    # 7 unclassified, 11 snow/ice.
    SCL_BAD_VALUES = {0, 1, 3, 8, 9, 10}

    def __init__(self, items: list, bands: dict, resolution: int = 10, epsg: int = 3857, collection: str = "sentinel-2-l2a"):
        """
        items:      list of pystac Items from StacSearchClient.search()
        bands:      dict like {"red": "B04", "nir": "B08", ...}
        resolution: output pixel size in `epsg` units (meters)
        collection: STAC collection id, used to pick the right reflectance
                    scale/offset (falls back to scale=1/offset=0 — i.e. no
                    change — for unregistered collections) and cloud mask band
        """
        self.items = items
        self.bands = bands
        self.resolution = resolution
        self.epsg = epsg
        self.reflectance = self.REFLECTANCE_SCALE.get(collection, {"scale": 1.0, "offset": 0.0})
        self.mask_band = self.CLOUD_MASK_BAND.get(collection)
        self._mask_cache: xr.DataArray | None = None

    def _stack(self, geometry: dict) -> xr.DataArray:
        needed = list(self.bands.values())
        # rescale=False: let each collection's own reflectance scaling stand.
        # (stackstac's default rescale collides with already-scaled L2A/L2
        #  surface reflectance products and silently skews every index.)
        arr = stackstac.stack(
            self.items,
            assets=needed,
            resolution=self.resolution,
            epsg=self.epsg,
            bounds_latlon=self._bbox(geometry),
            rescale=False,
            chunksize="auto",  # Dask-backed; no compute happens yet
        )
        return arr

    @staticmethod
    def _bbox(geometry: dict) -> tuple:
        coords = geometry["coordinates"][0] if geometry["type"] == "Polygon" else geometry["geometry"]["coordinates"][0]
        xs = [c[0] for c in coords]
        ys = [c[1] for c in coords]
        return (min(xs), min(ys), max(xs), max(ys))

    def _band(self, stack: xr.DataArray, name: str) -> xr.DataArray:
        raw = stack.sel(band=self.bands[name]).astype("float32")
        return raw * self.reflectance["scale"] + self.reflectance["offset"]

    def _cloud_valid_mask(self, geometry: dict) -> xr.DataArray | None:
        """
        Returns a lazy (time, y, x) boolean mask — True where the pixel is
        a genuine clear observation — or None if this collection has no
        registered mask band. Cached on the instance: the same engine is
        reused across multiple compute() calls (one per requested index),
        and the mask only needs fetching once per AOI.
        """
        if self.mask_band is None:
            return None
        if self._mask_cache is not None:
            return self._mask_cache

        scl_stack = stackstac.stack(
            self.items,
            assets=[self.mask_band],
            resolution=self.resolution,
            epsg=self.epsg,
            bounds_latlon=self._bbox(geometry),
            rescale=False,
            chunksize="auto",
        )
        scl = scl_stack.sel(band=self.mask_band)
        self._mask_cache = ~scl.isin(list(self.SCL_BAD_VALUES))
        return self._mask_cache

    def compute(self, geometry: dict, index: str) -> xr.DataArray:
        """
        Returns a lazy (time, y, x) DataArray for the requested index,
        with cloud/shadow-flagged pixels masked to NaN (see
        _cloud_valid_mask). Caller decides when/how to .compute() —
        typically inside a Dask cluster context so the reduction is
        distributed across workers.
        """
        stack = self._stack(geometry)
        eps = 1e-6  # guard against divide-by-zero on nodata edges

        if index == "NDVI":
            red, nir = self._band(stack, "red"), self._band(stack, "nir")
            result = (nir - red) / (nir + red + eps)

        elif index == "EVI":
            red, nir = self._band(stack, "red"), self._band(stack, "nir")
            blue = self._band(stack, self.bands.get("blue", "green"))
            result = 2.5 * (nir - red) / (nir + 6 * red - 7.5 * blue + 1 + eps)

        elif index == "SAVI":
            red, nir = self._band(stack, "red"), self._band(stack, "nir")
            L = 0.5
            result = ((nir - red) / (nir + red + L + eps)) * (1 + L)

        elif index == "MNDWI":
            green, swir = self._band(stack, "green"), self._band(stack, "swir16")
            result = (green - swir) / (green + swir + eps)

        else:
            raise ValueError(f"Unsupported index: {index}")

        mask = self._cloud_valid_mask(geometry)
        if mask is not None:
            result = result.where(mask)

        result.name = index
        return result
