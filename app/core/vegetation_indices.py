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

    def _resolve_asset(self, key: str) -> str:
        if key == "blue" and "blue" not in self.bands:
            key = "green"
        return self.bands[key]

    def _stack(self, geometry: dict, band_keys: list[str]) -> xr.DataArray:
        assets = []
        for key in band_keys:
            asset = self._resolve_asset(key)
            if asset not in assets:
                assets.append(asset)

        return stackstac.stack(
            self.items,
            assets=assets,
            resolution=self.resolution,
            epsg=self.epsg,
            bounds_latlon=self._bbox(geometry),
            rescale=False,
            chunksize="auto",
        )

    @staticmethod
    def _bbox(geometry: dict) -> tuple:
        coords = geometry["coordinates"][0] if geometry["type"] == "Polygon" else geometry["geometry"]["coordinates"][0]
        xs = [c[0] for c in coords]
        ys = [c[1] for c in coords]
        return (min(xs), min(ys), max(xs), max(ys))

    def _scene_offset(self, stack: xr.DataArray):
        """
        Additive offset in reflectance units: a plain float, or a per-scene
        (time,) DataArray for Sentinel-2. stackstac keeps item properties as
        coordinates; a property shared by every scene becomes a scalar
        coordinate, which this handles the same way.
        """
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
        # DN 0 is nodata. Without this, the offset would turn it into a
        # reflectance of -0.1 and it would leak into the averages.
        raw = raw.where(raw != 0)
        return raw * self.reflectance["scale"] + self._scene_offset(stack)

    def _cloud_valid_mask(self, geometry: dict) -> xr.DataArray | None:
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

        mask = self._cloud_valid_mask(geometry)
        if mask is not None:
            result = result.where(mask)

        result.name = index
        return result
