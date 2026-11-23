FROM python:3.14-slim

# GDAL/rasterio build deps — matches what we installed via Homebrew for
# local dev (Step 5 of the environment setup). rasterio's wheel usually
# bundles its own GDAL, but rioxarray/stackstac's full dependency chain
# still needs these present to build cleanly.
RUN apt-get update && apt-get install -y --no-install-recommends \
    gdal-bin libgdal-dev g++ \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements first, separately from app code: Docker caches each
# layer, so as long as requirements.txt doesn't change, rebuilding after
# an app code edit won't re-run the (slow) pip install every time.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

ENV DASK_CLUSTER_MODE=local \
    DASK_N_WORKERS=4 \
    STAC_PROVIDER=planetary-computer

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
