"""
Renders a single-band 2D composite (the time-mean of an index) as PNGs:

- a figure PNG (title + colorbar) for people who want a picture to share
- a map overlay PNG (RGBA, no axes, NaN fully transparent) that a web map
  can lay over a basemap using the composite's corner coordinates

Both use the same colour range, so the overlay's legend matches the figure.
"""
import io

import matplotlib
matplotlib.use("Agg")  # no display backend needed — runs server-side
import matplotlib.pyplot as plt
import numpy as np
from matplotlib import colors as mcolors
from PIL import Image

# Vegetation indices read naturally on a red-yellow-green scale (low to
# high vegetation vigor); MNDWI is a water index, so a brown-green-blue
# scale (dry land to water) is the more intuitive choice.
INDEX_COLORMAPS = {
    "NDVI": "RdYlGn",
    "EVI": "RdYlGn",
    "SAVI": "RdYlGn",
    "MNDWI": "BrBG",
}

# Overlays are downsampled to at most this many pixels on the long side.
# A 1,000 km2 AOI at 10 m is ~5,000 px across, which is heavier than a
# browser map needs; the GeoTIFF download keeps full resolution.
OVERLAY_MAX_PX = 2048


def colormap_name(index_name: str) -> str:
    return INDEX_COLORMAPS.get(index_name, "viridis")


def display_range(data: np.ndarray) -> tuple[float, float]:
    """
    2nd-98th percentile of the valid pixels: a few extreme pixels (water
    edges, bright roofs) would otherwise stretch the colour scale and wash
    out the variation people actually want to see.
    """
    valid = data[np.isfinite(data)]
    if valid.size == 0:
        return (0.0, 1.0)
    lo, hi = np.percentile(valid, [2, 98])
    if hi <= lo:
        hi = lo + 1e-3
    return (round(float(lo), 4), round(float(hi), 4))


def legend_colors(index_name: str, n: int = 7) -> list[str]:
    cmap = plt.get_cmap(colormap_name(index_name))
    return [mcolors.to_hex(cmap(i / (n - 1))) for i in range(n)]


def figure_png(data: np.ndarray, index_name: str, vmin: float, vmax: float) -> bytes:
    fig, ax = plt.subplots(figsize=(6, 5), dpi=120)
    im = ax.imshow(data, cmap=colormap_name(index_name), vmin=vmin, vmax=vmax)
    ax.set_title(f"{index_name} — composite (mean over period)")
    ax.axis("off")
    cbar = fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    cbar.set_label(index_name)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight")
    plt.close(fig)  # release the figure — without this, repeated calls leak memory
    return buf.getvalue()


def overlay_png(data: np.ndarray, index_name: str, vmin: float, vmax: float, max_px: int = OVERLAY_MAX_PX) -> bytes:
    """
    RGBA image with no decoration, for placing on a web map. Pixels outside
    the polygon or without a clear observation (NaN) are fully transparent,
    so the basemap shows through instead of a misleading colour.
    """
    step = max(1, int(np.ceil(max(data.shape) / max_px)))
    small = data[::step, ::step]

    norm = mcolors.Normalize(vmin=vmin, vmax=vmax, clip=True)
    rgba = plt.get_cmap(colormap_name(index_name))(norm(np.nan_to_num(small, nan=vmin)), bytes=True)
    rgba[..., 3] = np.where(np.isfinite(small), 255, 0)

    buf = io.BytesIO()
    Image.fromarray(rgba, mode="RGBA").save(buf, format="PNG", optimize=True)
    return buf.getvalue()
