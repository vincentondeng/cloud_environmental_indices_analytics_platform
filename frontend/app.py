import base64
import json
import os
import time

import folium
import requests
import streamlit as st
from streamlit_folium import st_folium

API_BASE = os.getenv("API_BASE", "http://localhost:8000/api/v1")
POLL_TIMEOUT_S = int(os.getenv("POLL_TIMEOUT_S", "1800"))  # give up waiting after 30 min

BASEMAPS = {
    "Satellite": (
        "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        "Tiles &copy; Esri &mdash; Source: Esri, Maxar, Earthstar Geographics, and the GIS User Community",
    ),
    "Streets": ("OpenStreetMap", None),
}

st.set_page_config(page_title="Cloud NDVI Analytics Platform", layout="wide")

st.title("Cloud NDVI Analytics Platform")
st.caption("Provide an area of interest, pick a date range, get vegetation index trends.")


@st.cache_data(ttl=3600, show_spinner=False, max_entries=32)
def fetch_file(url: str) -> bytes:
    """Fetched once per file and reused, so moving a slider doesn't re-download it."""
    resp = requests.get(url, timeout=120)
    resp.raise_for_status()
    return resp.content


def file_url(job_id: str, index: str, kind: str) -> str:
    return f"{API_BASE}/jobs/{job_id}/files/{index}/{kind}"


# --- All inputs live in the sidebar, so the main area is reserved for
# results — avoids the whole page growing taller with every new field. ---
with st.sidebar:
    st.header("Area of interest")
    input_method = st.radio("How to provide the area?", ["Bounding box", "Upload GeoJSON"], label_visibility="collapsed")

    geometry = None

    if input_method == "Upload GeoJSON":
        uploaded = st.file_uploader("Upload .geojson / .json", type=["geojson", "json"])
        if uploaded is not None:
            try:
                data = json.loads(uploaded.read())
                gtype = data.get("type")

                if gtype == "FeatureCollection":
                    feats = data.get("features", [])
                    if not feats:
                        st.error("No features in this file.")
                    else:
                        if len(feats) > 1:
                            st.warning(f"{len(feats)} features — using the first.")
                        geometry = feats[0]
                elif gtype in ("Feature", "Polygon"):
                    geometry = data
                else:
                    st.error(f"Unsupported type: {gtype}.")

                if geometry is not None:
                    inner_type = geometry.get("geometry", geometry).get("type")
                    if inner_type != "Polygon":
                        st.error(f"'{inner_type}' not supported — only Polygon.")
                        geometry = None

            except json.JSONDecodeError:
                st.error("Couldn't parse that file as JSON.")

        if geometry is not None:
            st.success("Geometry loaded.")

    else:
        west = st.number_input("West", value=36.80, format="%.4f")
        south = st.number_input("South", value=-1.30, format="%.4f")
        east = st.number_input("East", value=36.85, format="%.4f")
        north = st.number_input("North", value=-1.25, format="%.4f")

        geometry = {
            "type": "Polygon",
            "coordinates": [[
                [west, south], [east, south], [east, north], [west, north], [west, south]
            ]],
        }

    st.header("Date range")
    start_date = st.date_input("Start date", value="2023-01-01")
    end_date = st.date_input("End date", value="2023-12-31")

    st.header("Indices")
    indices = st.multiselect(
        "Indices", options=["NDVI", "EVI", "SAVI", "MNDWI"], default=["NDVI"],
        label_visibility="collapsed",
    )
    max_cloud_cover = st.slider("Max cloud cover (%)", 0, 100, 30)

    submitted = st.button("Submit analysis", width="stretch")


# --- Submitting runs the job and stores the outcome in session_state.
# Results are drawn from session_state below, not inside this `if`, because
# Streamlit reruns the whole script on every interaction: drawn inside the
# `if`, they vanished as soon as someone clicked a download button or moved
# the opacity slider. ---
if submitted:
    if geometry is None:
        st.error("No valid area of interest provided yet.")
    elif not indices:
        st.error("Pick at least one index.")
    else:
        payload = {
            "geometry": geometry,
            "start_date": str(start_date),
            "end_date": str(end_date),
            "indices": indices,
            "max_cloud_cover": max_cloud_cover,
            "resample_freq": "MS",
        }
        try:
            resp = requests.post(f"{API_BASE}/analyze", json=payload, timeout=10)
            resp.raise_for_status()
            job_id = resp.json()["job_id"]

            status_box = st.empty()
            started = time.time()
            data = None
            with st.spinner("Running analysis — this can take a few minutes for large areas..."):
                while time.time() - started < POLL_TIMEOUT_S:
                    poll = requests.get(f"{API_BASE}/jobs/{job_id}", timeout=10)
                    poll.raise_for_status()
                    data = poll.json()
                    status_box.text(f"Status: {data['status']} ({time.time() - started:.0f}s)")
                    if data["status"] in ("complete", "failed"):
                        break
                    time.sleep(3)
            status_box.empty()

            if data is None or data["status"] not in ("complete", "failed"):
                st.session_state.job = None
                st.error(f"Stopped waiting after {POLL_TIMEOUT_S // 60} minutes. The job may still finish on the server.")
            else:
                st.session_state.job = {**data, "geometry": geometry}
        except requests.exceptions.ConnectionError:
            st.error(f"Couldn't reach the API at {API_BASE} — is `uvicorn app.main:app` running?")
        except requests.exceptions.HTTPError as exc:
            st.error(f"API returned an error: {exc.response.status_code} — {exc.response.text}")


def legend(layer: dict, index: str) -> None:
    gradient = ", ".join(layer["legend_colors"])
    st.markdown(
        f"""
        <div style="max-width:420px;font-size:0.85rem">
          <div style="height:12px;border-radius:3px;background:linear-gradient(to right, {gradient})"></div>
          <div style="display:flex;justify-content:space-between;margin-top:2px">
            <span>{layer['vmin']:.2f}</span><span>{index}</span><span>{layer['vmax']:.2f}</span>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.caption(
        "Colours span the 2nd to 98th percentile of this map. Transparent areas are outside "
        "your boundary or had no clear observation in the period."
    )


def map_viewer(job: dict, idx_result: dict) -> None:
    index, layer = idx_result["index"], idx_result["map"]
    key = f"{job['job_id']}-{index}"

    c1, c2 = st.columns([1, 2])
    basemap = c1.radio("Basemap", list(BASEMAPS), horizontal=True, key=f"basemap-{key}")
    opacity = c2.slider("Overlay opacity", 0.0, 1.0, 0.75, 0.05, key=f"opacity-{key}")

    try:
        overlay = fetch_file(file_url(job["job_id"], index, "overlay"))
    except requests.RequestException:
        st.warning("The map overlay is no longer available (results expire after a while). Run the analysis again.")
        return

    tiles, attribution = BASEMAPS[basemap]
    m = folium.Map(tiles=tiles, attr=attribution, control_scale=True)
    folium.raster_layers.ImageOverlay(
        image="data:image/png;base64," + base64.b64encode(overlay).decode(),
        bounds=layer["bounds"],
        opacity=opacity,
        name=f"{index} composite",
    ).add_to(m)
    folium.GeoJson(
        job["geometry"],
        name="Area of interest",
        style_function=lambda _: {"color": "#ffffff", "weight": 2, "fill": False, "dashArray": "6 4"},
    ).add_to(m)
    m.fit_bounds(layer["bounds"])

    # returned_objects=[]: panning or zooming the map doesn't rerun the app.
    st_folium(m, height=480, use_container_width=True, returned_objects=[], key=f"map-{key}")
    legend(layer, index)


def downloads(job: dict, idx_result: dict) -> None:
    index = idx_result["index"]
    labels = {
        "geotiff": ("Download GeoTIFF (for GIS)", f"{index}_composite.tif", "image/tiff"),
        "figure": ("Download PNG figure", f"{index}_figure.png", "image/png"),
    }
    kinds = [k for k in ("geotiff", "figure") if k in idx_result.get("files", [])]
    for col, kind in zip(st.columns(len(kinds) or 1), kinds):
        label, name, mime = labels[kind]
        try:
            data = fetch_file(file_url(job["job_id"], index, kind))
        except requests.RequestException:
            col.warning(f"{label.split(' (')[0]}: file no longer available.")
            continue
        col.download_button(label, data=data, file_name=name, mime=mime, width="stretch", key=f"dl-{kind}-{job['job_id']}-{index}")


# --- Main area: results from session_state ---
job = st.session_state.get("job")
if job is None:
    if not submitted:
        st.info("Set your area of interest and options in the sidebar, then click **Submit analysis**.")
elif job["status"] == "failed":
    st.error(f"Job failed: {job.get('error')}")
else:
    result = job["result"]
    st.success(f"Done — {result['n_scenes_used']} scenes used, AOI area {result['aoi_area_km2']} km²")

    # Multiple indices shown as tabs instead of stacked sections — keeps the
    # page the same height whether you pick 1 or 4.
    if len(result["results"]) > 1:
        tabs = st.tabs([r["index"] for r in result["results"]])
    else:
        tabs = [st.container()]

    for tab, idx_result in zip(tabs, result["results"]):
        with tab:
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Mean", idx_result["mean"])
            c2.metric("Min", idx_result["min"])
            c3.metric("Max", idx_result["max"])
            c4.metric("Trend/yr", idx_result["trend_per_year"])

            map_tab, chart_tab = st.tabs(["Map", "Time series"])
            with map_tab:
                if idx_result.get("map"):
                    map_viewer(job, idx_result)
                    downloads(job, idx_result)
                else:
                    st.info("No composite map was produced for this index.")
            with chart_tab:
                st.plotly_chart(json.loads(idx_result["chart_json"]), width="stretch")
                st.caption("Gaps in the line mean no cloud-free observations were available that month.")
                with st.expander("View raw time series data"):
                    st.dataframe(idx_result["time_series"], width="stretch")
