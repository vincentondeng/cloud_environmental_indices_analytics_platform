"""
Thin, catalog-agnostic STAC search wrapper.
"""
from datetime import date

import planetary_computer
import pystac_client

from app.config import get_settings

settings = get_settings()

BAND_ALIASES = {
    "sentinel-2-l2a": {"red": "B04", "nir": "B08", "green": "B03", "swir16": "B11"},
    "landsat-c2-l2": {"red": "red", "nir": "nir08", "green": "green", "swir16": "swir16"},
}


class StacSearchClient:
    def __init__(self, provider: str | None = None):
        self.provider = provider or settings.stac_provider
        self.url = settings.stac_urls[self.provider]
        modifier = planetary_computer.sign_inplace if self.provider == "planetary-computer" else None
        self.catalog = pystac_client.Client.open(self.url, modifier=modifier)

    def search(
        self,
        geometry: dict,
        start: date,
        end: date,
        collection: str,
        max_cloud_cover: int = 20,
    ):
        search = self.catalog.search(
            collections=[collection],
            intersects=geometry,
            datetime=f"{start.isoformat()}/{end.isoformat()}",
            query={"eo:cloud_cover": {"lt": max_cloud_cover}},
        )
        items = list(search.item_collection())
        return items

    def bands_for(self, collection: str) -> dict:
        try:
            return BAND_ALIASES[collection]
        except KeyError:
            raise ValueError(f"No band mapping registered for collection '{collection}'.")
