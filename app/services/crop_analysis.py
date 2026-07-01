"""
Converts raw NDVI/NDWI numbers from Sentinel-2 into the farmer-facing
fields the app already displays (healthScore, waterStressLevel, etc).
This is the "translate science into a decision" layer mentioned in the
product brief — the farmer never sees a raw NDVI float, just "Water
Stress: High" and a plain-language recommendation.

Thresholds are based on commonly used agricultural NDVI bands (see
AgroVision blueprint): >0.7 healthy, 0.5-0.7 moderate, 0.3-0.5 stress,
<0.3 severe stress. NDWI bands follow the same general shape but
indicate water content rather than vigor.
"""
from datetime import datetime


def ndvi_to_health_score(ndvi_mean: float) -> int:
    """Maps NDVI (-1 to 1, but realistically 0.1-0.9 for cropland) to
    a 0-100 health score the app already displays prominently."""
    if ndvi_mean is None:
        return 70  # neutral default when no reading is available
    clamped = max(0.0, min(1.0, ndvi_mean))
    # NDVI of 0.7+ -> 90-100, NDVI of 0.3 -> ~40, NDVI of 0.1 -> ~15
    score = int(round(clamped * 110))
    return max(10, min(100, score))


def ndvi_to_health_status(ndvi_mean: float) -> str:
    if ndvi_mean is None:
        return "Awaiting Scan"
    if ndvi_mean >= 0.7:
        return "Excellent"
    if ndvi_mean >= 0.5:
        return "Good"
    if ndvi_mean >= 0.3:
        return "Needs Attention"
    return "Critical"


def ndwi_to_water_stress(ndwi_mean: float) -> tuple[str, int]:
    """
    Returns (level, confidence). NDWI is the inverse relationship to
    water stress — LOW ndwi (dry canopy/soil) means HIGH water stress.
    Typical cropland NDWI ranges roughly -0.3 (very dry) to 0.3 (very
    wet/waterlogged); thresholds below are tuned for that range.

    Note: very high NDWI (>0.15) means the field has plenty of water -
    likely too much (see detect_waterlogging) rather than "stressed".
    Water stress level reflects dryness specifically, so it stays Low
    in that range; waterlogging_severity is the field that actually
    flags the opposite problem.
    """
    if ndwi_mean is None:
        return "Unknown", 0
    if ndwi_mean < -0.1:
        return "High", 88
    if ndwi_mean < 0.05:
        return "Moderate", 80
    return "Low", 85


def detect_waterlogging(ndwi_mean: float) -> tuple[str, str]:
    """Very high NDWI on cropland (which should be mostly vegetation,
    not standing water) suggests waterlogging rather than healthy
    moisture. Returns (severity, note)."""
    if ndwi_mean is None:
        return "None", ""
    if ndwi_mean > 0.25:
        return "Severe", "NDWI unusually high for cropland — possible standing water."
    if ndwi_mean > 0.15:
        return "Moderate", "Elevated soil moisture detected, monitor drainage."
    return "None", ""


def build_satellite_update(ndvi_result: dict, ndwi_result: dict) -> dict:
    """
    Combines the NDVI and NDWI service results into a single dict of
    farm fields ready to merge into the Farm DB row. Returns an empty
    dict (no changes) for any metric that wasn't available, so a
    partial read (e.g. NDVI worked, NDWI didn't) still updates what it
    can rather than failing entirely.
    """
    updates: dict = {}
    today_str = datetime.utcnow().strftime("%Y-%m-%d")

    if ndvi_result.get("available"):
        ndvi_mean = ndvi_result["mean"]
        updates["health_score"] = ndvi_to_health_score(ndvi_mean)
        updates["health_status"] = ndvi_to_health_status(ndvi_mean)
        updates["last_scan_date"] = ndvi_result.get("date", today_str)

    if ndwi_result.get("available"):
        ndwi_mean = ndwi_result["mean"]
        level, confidence = ndwi_to_water_stress(ndwi_mean)
        updates["water_stress_level"] = level
        updates["water_stress_confidence"] = confidence

        waterlog_severity, waterlog_note = detect_waterlogging(ndwi_mean)
        updates["waterlogging_severity"] = waterlog_severity
        if waterlog_note:
            updates["waterlogging_area"] = waterlog_note

    return updates
