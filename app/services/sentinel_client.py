"""
Talks to Copernicus's Sentinel Hub "Statistical API" — this is the
right endpoint for our use case because it returns aggregated stats
(mean/min/max NDVI etc) for a polygon directly, instead of forcing us
to download and process raw imagery ourselves.

Docs: https://documentation.dataspace.copernicus.eu/APIs/SentinelHub/Statistical.html
"""
import requests
from datetime import datetime, timedelta
from app.services.sentinel_auth import get_access_token, SentinelAuthError

STATS_API_URL = "https://sh.dataspace.copernicus.eu/api/v1/statistics"

# Evalscripts define which satellite bands to read and how to combine
# them. These run server-side on Copernicus's infrastructure.

NDVI_EVALSCRIPT = """
//VERSION=3
function setup() {
  return {
    input: [{ bands: ["B04", "B08", "dataMask"] }],
    output: [
      { id: "ndvi", bands: 1, sampleType: "FLOAT32" },
      { id: "dataMask", bands: 1 }
    ]
  };
}
function evaluatePixel(sample) {
  let ndvi = (sample.B08 - sample.B04) / (sample.B08 + sample.B04);
  return { ndvi: [ndvi], dataMask: [sample.dataMask] };
}
"""

NDWI_EVALSCRIPT = """
//VERSION=3
function setup() {
  return {
    input: [{ bands: ["B03", "B08", "dataMask"] }],
    output: [
      { id: "ndwi", bands: 1, sampleType: "FLOAT32" },
      { id: "dataMask", bands: 1 }
    ]
  };
}
function evaluatePixel(sample) {
  let ndwi = (sample.B03 - sample.B08) / (sample.B03 + sample.B08);
  return { ndwi: [ndwi], dataMask: [sample.dataMask] };
}
"""


class SentinelRequestError(Exception):
    pass


def _polygon_to_geojson(polygon_points: list) -> dict:
    """Converts our [{lat, lng}, ...] format into a GeoJSON Polygon.
    GeoJSON requires the ring to be closed (first point repeated at
    the end) and coordinates as [lng, lat] (note the order)."""
    coords = [[p["lng"], p["lat"]] for p in polygon_points]
    if coords[0] != coords[-1]:
        coords.append(coords[0])
    return {"type": "Polygon", "coordinates": [coords]}


def _request_stats(polygon_points: list, evalscript: str, band_id: str, days_back: int = 15) -> dict:
    token = get_access_token()

    to_date = datetime.utcnow()
    from_date = to_date - timedelta(days=days_back)

    payload = {
        "input": {
            "bounds": {
                "geometry": _polygon_to_geojson(polygon_points),
                "properties": {"crs": "http://www.opengis.net/def/crs/EPSG/0/4326"},
            },
            "data": [{"type": "sentinel-2-l2a"}],
        },
        "aggregation": {
            "timeRange": {
                "from": from_date.strftime("%Y-%m-%dT00:00:00Z"),
                "to": to_date.strftime("%Y-%m-%dT23:59:59Z"),
            },
            "aggregationInterval": {"of": "P1D"},
            "evalscript": evalscript,
            "resx": 10,
            "resy": 10,
        },
        "calculations": {band_id: {"statistics": {"default": {"percentiles": {"k": [50]}}}}},
    }

    response = requests.post(
        STATS_API_URL,
        json=payload,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        timeout=30,
    )

    if response.status_code != 200:
        raise SentinelRequestError(
            f"Sentinel Hub Statistical API failed ({response.status_code}): {response.text[:500]}"
        )

    return response.json()


def _extract_latest_mean(stats_response: dict, band_id: str) -> dict | None:
    """
    The Statistical API returns one entry per day in the requested
    range. Cloud cover means many days have no valid data
    (validCount=0). We walk backwards from most recent and return the
    first day that actually has usable pixels.
    """
    data = stats_response.get("data", [])
    # Most recent first
    for entry in sorted(data, key=lambda d: d["interval"]["from"], reverse=True):
        outputs = entry.get("outputs", {})
        band_stats = outputs.get(band_id, {}).get("bands", {}).get("B0", {}).get("stats")
        if band_stats and band_stats.get("sampleCount", 0) > 0 and band_stats.get("noDataCount", 0) < band_stats.get("sampleCount", 1):
            return {
                "mean": band_stats.get("mean"),
                "min": band_stats.get("min"),
                "max": band_stats.get("max"),
                "stDev": band_stats.get("stDev"),
                "date": entry["interval"]["from"][:10],
            }
    return None


def get_ndvi_for_farm(polygon_points: list) -> dict:
    """
    Returns the most recent usable NDVI reading for a farm's drawn
    boundary, e.g.:
      {"mean": 0.62, "min": 0.31, "max": 0.81, "date": "2026-06-24"}
    Returns None values if no cloud-free pass was found in the lookback
    window (rare, but happens during monsoon).
    """
    try:
        raw = _request_stats(polygon_points, NDVI_EVALSCRIPT, "ndvi")
        result = _extract_latest_mean(raw, "ndvi")
        if result is None:
            return {"available": False, "reason": "No cloud-free satellite pass in the last 15 days"}
        result["available"] = True
        return result
    except SentinelAuthError as e:
        return {"available": False, "reason": str(e)}
    except SentinelRequestError as e:
        return {"available": False, "reason": str(e)}


def get_ndwi_for_farm(polygon_points: list) -> dict:
    """Same idea as get_ndvi_for_farm but for water index (NDWI)."""
    try:
        raw = _request_stats(polygon_points, NDWI_EVALSCRIPT, "ndwi")
        result = _extract_latest_mean(raw, "ndwi")
        if result is None:
            return {"available": False, "reason": "No cloud-free satellite pass in the last 15 days"}
        result["available"] = True
        return result
    except SentinelAuthError as e:
        return {"available": False, "reason": str(e)}
    except SentinelRequestError as e:
        return {"available": False, "reason": str(e)}
