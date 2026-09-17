"""
Central configuration, loaded from environment variables.
Keeps the STAC catalog, Dask cluster mode, and AWS settings swappable
without touching application code.
"""
import os
from functools import lru_cache
from pydantic import BaseModel


class Settings(BaseModel):
    # --- STAC catalog ---
    stac_provider: str = os.getenv("STAC_PROVIDER", "planetary-computer")
    stac_urls: dict = {
        "planetary-computer": "https://planetarycomputer.microsoft.com/api/stac/v1",
        "earth-search": "https://earth-search.aws.element84.com/v1",
    }

    # --- Dask cluster ---
    dask_cluster_mode: str = os.getenv("DASK_CLUSTER_MODE", "local")
    dask_n_workers: int = int(os.getenv("DASK_N_WORKERS", "4"))
    dask_scheduler_address: str | None = os.getenv("DASK_SCHEDULER_ADDRESS")

    # --- AWS (only used when dask_cluster_mode == "fargate") ---
    aws_region: str = os.getenv("AWS_REGION", "us-east-1")
    ecs_cluster_arn: str | None = os.getenv("ECS_CLUSTER_ARN")
    fargate_image: str = os.getenv("FARGATE_IMAGE", "ghcr.io/dask/dask:latest")

    # --- Analysis defaults ---
    max_polygon_area_km2: float = float(os.getenv("MAX_POLYGON_AREA_KM2", "50000"))
    default_cloud_cover_pct: int = int(os.getenv("DEFAULT_CLOUD_COVER_PCT", "20"))
    default_collection: str = os.getenv("DEFAULT_COLLECTION", "sentinel-2-l2a")

    # --- API ---
    api_title: str = "Cloud NDVI Analytics Platform"
    api_version: str = "0.1.0"
    cors_origins: list[str] = os.getenv("CORS_ORIGINS", "*").split(",")

    @property
    def stac_url(self) -> str:
        return self.stac_urls[self.stac_provider]


@lru_cache
def get_settings() -> Settings:
    return Settings()
