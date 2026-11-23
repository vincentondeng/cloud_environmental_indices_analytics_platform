# Cloud Environmental Indices Analytics Platform

Send a polygon, get back NDVI, EVI, SAVI and MNDWI trends for that area, computed
from Sentinel-2 imagery and returned as a time series, a chart and a composite map
(PNG for display, GeoTIFF for GIS).

## What it does

- Searches Sentinel-2 L2A scenes through STAC (Microsoft Planetary Computer or Earth Search).
- Masks clouds and cloud shadows per pixel with the Scene Classification Layer (SCL).
- Applies the correct reflectance scale and offset per scene (including the processing
  baseline 04.00+ offset) before computing indices, and fetches only the bands each index needs.
- Merges same-day tiles, clips to the polygon and reports geodesic area and the cloud-free
  share inside the polygon.
- Runs the computation on a Dask cluster behind a FastAPI service with background jobs,
  and includes a Streamlit frontend.

## Try it locally

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload
```

The API is now at `http://localhost:8000` (interactive docs at `/docs`).

### 1. Submit an area of interest

```bash
curl -X POST http://localhost:8000/api/v1/analyze \
  -H "Content-Type: application/json" \
  -d '{
    "geometry": {
      "type": "Polygon",
      "coordinates": [[[36.80,-1.30],[36.85,-1.30],[36.85,-1.25],[36.80,-1.25],[36.80,-1.30]]]
    },
    "start_date": "2023-01-01",
    "end_date": "2023-12-31",
    "indices": ["NDVI"]
  }'
```

You'll get back a `job_id` right away — the actual analysis runs in the
background and can take anywhere from a few seconds to a couple of minutes,
depending on the area size and date range.

```json
{"job_id": "3f9e1a2b-...", "status": "queued"}
```

### 2. Get the result

```bash
curl http://localhost:8000/api/v1/jobs/3f9e1a2b-...
```

Keep checking this every few seconds until `"status": "complete"`. You'll get
back, per index: the mean/min/max/trend over the period, the full time
series, and a chart you can render directly.

## Endpoints

| Method | Path | What it does |
|---|---|---|
| `POST` | `/api/v1/analyze` | Submit a polygon + date range, get a `job_id` |
| `GET` | `/api/v1/jobs/{job_id}` | Check status / get the result |
| `GET` | `/api/v1/jobs/{job_id}/files/{index}/{kind}` | Download a composite (`geotiff`, `overlay` or `figure`) |
| `GET` | `/api/v1/health` | Is the service up |

## Run the full stack with Docker

```bash
docker compose up --build
```

The API runs at `http://localhost:8000` and the Streamlit frontend at
`http://localhost:8501`. To run the frontend without Docker:

```bash
pip install -r frontend/requirements.txt
streamlit run frontend/app.py
```

An example area of interest is in `examples/nairobi.geojson` (an approximate,
simplified boundary of Nairobi County).

## Deployment

Deployment to AWS is in progress. The configuration already supports running
Dask on AWS Fargate (`DASK_CLUSTER_MODE=fargate`) instead of a local cluster.

## Running tests

```bash
pytest tests/
```
