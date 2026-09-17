"""
Owns the single Dask Client used by the whole app.

Local dev  -> distributed.LocalCluster (in-process workers, zero setup)
Production -> dask_cloudprovider.aws.FargateCluster (spins up ECS tasks
              as workers on demand, scales to zero when idle)
Gateway    -> connect to a cluster you already run elsewhere via its
              scheduler address

Created once at FastAPI startup and reused across requests.
"""
import logging

from dask.distributed import Client, LocalCluster

from app.config import get_settings

logger = logging.getLogger("ndvi-platform.dask")
settings = get_settings()

_client: Client | None = None


def get_dask_client() -> Client:
    if _client is None:
        raise RuntimeError("Dask client not initialized — call init_dask_client() at startup.")
    return _client


def init_dask_client() -> Client:
    global _client
    mode = settings.dask_cluster_mode
    logger.info(f"Initializing Dask cluster (mode={mode})")

    if mode == "local":
        cluster = LocalCluster(
            n_workers=settings.dask_n_workers,
            threads_per_worker=2,
            processes=True,
        )
        _client = Client(cluster)

    elif mode == "fargate":
        from dask_cloudprovider.aws import FargateCluster

        cluster = FargateCluster(
            image=settings.fargate_image,
            cluster_arn=settings.ecs_cluster_arn,
            region_name=settings.aws_region,
            n_workers=settings.dask_n_workers,
            scheduler_timeout="15 minutes",
        )
        _client = Client(cluster)

    elif mode == "gateway":
        if not settings.dask_scheduler_address:
            raise RuntimeError("DASK_SCHEDULER_ADDRESS must be set when DASK_CLUSTER_MODE=gateway")
        _client = Client(settings.dask_scheduler_address)

    else:
        raise ValueError(f"Unknown DASK_CLUSTER_MODE: {mode}")

    logger.info(f"Dask dashboard: {_client.dashboard_link}")
    return _client


def shutdown_dask_client():
    global _client
    if _client is not None:
        _client.close()
        _client = None
