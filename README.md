# Cloud NDVI Analytics Platform

Send a polygon, get back NDVI trends for that area — computed from satellite
imagery and returned as a time series plus a chart.

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
| `GET` | `/api/v1/health` | Is the service up |

## Using the deployed version (once it's live on AWS)

Same everything — just swap `localhost:8000` for your deployed URL.

## Running tests

```bash
pytest tests/
```
