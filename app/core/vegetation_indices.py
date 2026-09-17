"""
Class-based, catalog-agnostic vegetation/water index computation on a
lazily-loaded, Dask-backed xarray cube built via stackstac.
"""
import stackstac
import xarray as xr


class VegetationIndexEngine:
    def __init__(self, items: list, bands: dict, resolution: int = 10, epsg: int = 3857):
        self.items = items
        self.bands = bands
        self.resolution = resolution
        self.epsg = epsg

    def _stack(self, geometry: dict) -> xr.DataArray:
        needed = list(self.bands.values())
        # rescale=False: avoids stackstac's default rescale colliding with
        # already-scaled L2A/L2 surface reflectance products.
        arr = stackstac.stack(
            self.items,
            assets=needed,
            resolution=self.resolution,
            epsg=self.epsg,
            bounds_latlon=self._bbox(geometry),
            rescale=False,
            chunksize="auto",
        )
        return arr

    @staticmethod
    def _bbox(geometry: dict) -> tuple:
        coords = geometry["coordinates"][0]
        xs = [c[0] for c in coords]
        ys = [c[1] for c in coords]
        return (min(xs), min(ys), max(xs), max(ys))

    def _band(self, stack: xr.DataArray, name: str) -> xr.DataArray:
        return stack.sel(band=self.bands[name]).astype("float32")

    def compute(self, geometry: dict, index: str) -> xr.DataArray:
        stack = self._stack(geometry)
        eps = 1e-6

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

        result.name = index
        return result
