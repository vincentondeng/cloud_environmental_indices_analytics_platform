# Cloud NDVI Analytics Platform

A user-facing API that accepts a GeoJSON polygon and returns NDVI/EVI/SAVI/MNDWI
time series, trend statistics, and interactive charts for any location globally
— computed at scale via Dask over Sentinel-2 / Landsat imagery discovered through
STAC.

```
Polygon (GeoJSON) → FastAPI → Dask cluster → STAC search → stackstac/xarray
                                                              → index math
                                                              → time series
                                                              → Plotly chart
                                                              → JSON response
```

Built on top of an existing [STAC vegetation index pipeline](#) — the
class-based, catalog-agnostic NDVI/EVI/SAVI/MNDWI engine here follows the same
design, extended with an async job API and a distributed compute backend.

## Quickstart (local)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env   # defaults already work: local Dask + Planetary Computer STAC

uvicorn app.main:app --reload
```

- API docs: http://localhost:8000/docs
- Dask dashboard: printed in the startup logs
- Demo notebook: `notebooks/demo.ipynb`

## Example request

```bash
curl -X POST http://localhost:8000/api/v1/analyze \
  -H "Content-Type: application/json" \
  -d '{
    "geometry": {
      "type": "Polygon",
      "coordinates": [[[36.80,-1.30],[36.85,-1.30],[36.85,-1.25],[36.80,-1.25],[36.80,-1.30]]]
    },
    "start_date": "2023-01-01",
    "end_date": "2024-12-31",
    "indices": ["NDVI"],
    "resample_freq": "MS"
  }'
```

Returns a `job_id` immediately (202 Accepted); poll `GET /api/v1/jobs/{job_id}`
until `status: "complete"`.

## Project layout

```
app/
  main.py              FastAPI app, startup/shutdown of the Dask cluster
  config.py            Env-driven settings (STAC provider, Dask mode, AWS)
  models/schemas.py    Request/response contracts (GeoJSON-validated input)
  core/
    stac_client.py         Catalog-agnostic STAC search
    vegetation_indices.py  Class-based NDVI/EVI/SAVI/MNDWI engine (lazy, Dask-backed)
    dask_cluster.py        Local / Fargate / gateway cluster lifecycle
    timeseries.py          Spatial reduction, resampling, trend fitting
    pipeline.py             Orchestrates the full request → response flow
  api/routes.py         /analyze, /jobs/{id}, /health
  utils/charts.py       Plotly chart JSON generation
notebooks/demo.ipynb  End-to-end usage example
deploy/               Dockerfile, docker-compose, AWS deployment guide
tests/                API smoke tests
```

## Design notes

- **Async job model**: `/analyze` returns a `job_id` immediately rather than
  blocking the request — a multi-scene Dask reduction can take from seconds
  to minutes depending on AOI size and date range.
- **Lazy end-to-end**: the STAC → stackstac → index-math chain builds one
  Dask graph; nothing computes until `timeseries.reduce_to_timeseries()`
  calls `.compute()`, so the whole pipeline distributes as a single
  submission to the cluster instead of many small ones.
- **`rescale=False`** on the stackstac call: avoids stackstac's default
  reflectance rescaling from double-scaling already-scaled L2A/L2 surface
  reflectance products.
- **Catalog-agnostic by band mapping**: adding a new STAC collection is a
  one-line addition to `BAND_ALIASES` in `stac_client.py`, not a code change.

## Deploying to AWS

See [`deploy/aws/README.md`](deploy/aws/README.md) — ECS Fargate for the API,
with Dask workers spun up on-demand via `dask_cloudprovider` and torn down
when idle.

## Running tests

```bash
pip install pytest
pytest tests/
```
