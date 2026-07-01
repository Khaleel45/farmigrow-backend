"""
Endpoint that triggers a real Sentinel-2/Sentinel-1 satellite scan for
a saved farm and writes the results back into that farm's row. This
is what replaces the hardcoded "Low / 8% / Low" defaults a farm gets
when it's first drawn.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.models import Farm
from app.services import sentinel_client, crop_analysis
from app.services.sentinel_auth import is_configured

router = APIRouter(prefix="/satellite", tags=["satellite"])


@router.get("/status")
def satellite_status():
    """Lets the app (or you, via /docs) check whether Sentinel Hub
    credentials are configured on this deployment yet, without
    triggering an actual scan."""
    return {
        "sentinel_configured": is_configured(),
        "message": (
            "Ready to scan farms."
            if is_configured()
            else "SENTINEL_CLIENT_ID / SENTINEL_CLIENT_SECRET not set on this "
            "deployment yet. Sign up free at https://dataspace.copernicus.eu/ "
            "and add the credentials as Railway environment variables."
        ),
    }


@router.post("/scan/{farm_id}")
def scan_farm(farm_id: str, device_id: str, db: Session = Depends(get_db)):
    """
    Runs a real Sentinel-2 NDVI + NDWI scan for this farm's drawn
    boundary and updates its health/water-stress/waterlogging fields
    with the result. Requires the farm to have a gps_polygon with at
    least 3 points (i.e. an actual drawn boundary, not just a pin).
    """
    if not is_configured():
        raise HTTPException(
            status_code=503,
            detail="Sentinel Hub credentials not configured on this server yet.",
        )

    farm = db.query(Farm).filter(Farm.id == farm_id, Farm.device_id == device_id).first()
    if not farm:
        raise HTTPException(status_code=404, detail="Farm not found")

    if not farm.gps_polygon or len(farm.gps_polygon) < 3:
        raise HTTPException(
            status_code=400,
            detail="This farm has no drawn boundary to scan. Draw a boundary with at least 3 points first.",
        )

    ndvi_result = sentinel_client.get_ndvi_for_farm(farm.gps_polygon)
    ndwi_result = sentinel_client.get_ndwi_for_farm(farm.gps_polygon)

    updates = crop_analysis.build_satellite_update(ndvi_result, ndwi_result)

    for key, value in updates.items():
        setattr(farm, key, value)
    db.commit()
    db.refresh(farm)

    return {
        "farm_id": farm.id,
        "ndvi": ndvi_result,
        "ndwi": ndwi_result,
        "updated_fields": list(updates.keys()),
        "health_score": farm.health_score,
        "health_status": farm.health_status,
        "water_stress_level": farm.water_stress_level,
        "waterlogging_severity": farm.waterlogging_severity,
    }


@router.post("/scan-all")
def scan_all_farms(device_id: str, db: Session = Depends(get_db)):
    """
    Convenience endpoint to scan every farm belonging to this device in
    one call — useful for a "Refresh All" button in the app, or a
    scheduled daily job later.
    """
    if not is_configured():
        raise HTTPException(
            status_code=503,
            detail="Sentinel Hub credentials not configured on this server yet.",
        )

    farms = db.query(Farm).filter(Farm.device_id == device_id).all()
    results = []

    for farm in farms:
        if not farm.gps_polygon or len(farm.gps_polygon) < 3:
            results.append({"farm_id": farm.id, "skipped": "no drawn boundary"})
            continue

        ndvi_result = sentinel_client.get_ndvi_for_farm(farm.gps_polygon)
        ndwi_result = sentinel_client.get_ndwi_for_farm(farm.gps_polygon)
        updates = crop_analysis.build_satellite_update(ndvi_result, ndwi_result)

        for key, value in updates.items():
            setattr(farm, key, value)

        results.append({
            "farm_id": farm.id,
            "name": farm.name,
            "updated_fields": list(updates.keys()),
            "ndvi_available": ndvi_result.get("available", False),
            "ndwi_available": ndwi_result.get("available", False),
        })

    db.commit()
    return {"scanned": len(results), "results": results}
