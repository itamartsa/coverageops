"""
Coverage API
────────────
Endpoints for running coverage analyses, retrieving results,
generating reports, cross-section analysis, and topography overlay.
"""
import io
import json
import math
import os
import urllib.request
from datetime import datetime
from io import BytesIO
from typing import Optional, List

import numpy as np
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from PIL import Image
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, joinedload

from app.core.config import settings
from app.core.database import get_db
from app.core.security import get_current_user, require_role
from app.models.user import User, Site, CoverageResult, AnalysisMode
from app.services.coverage_engine import (
    SiteParams, BoundingBox,
    calculate_coverage, calculate_cross_section,
)
from app.services.terrain import fetch_elevation_grid
from app.services.report_generator import generate_cross_section_docx

router = APIRouter()


# ── Request / Response Schemas ────────────────────────────────────────────────

class CoverageRequest(BaseModel):
    site_id:    int
    mode:       AnalysisMode
    sw_lat:     float = Field(..., ge=-90,  le=90)
    sw_lon:     float = Field(..., ge=-180, le=180)
    ne_lat:     float = Field(..., ge=-90,  le=90)
    ne_lon:     float = Field(..., ge=-180, le=180)
    # Grid resolution (points per axis). Higher = finer grid, slower compute.
    resolution: Optional[int] = Field(300, ge=20, le=500)


class CoverageResultOut(BaseModel):
    id:           int
    site_id:      int
    mode:         str
    covered_pct:  float
    rssi_avg:     float
    rssi_max:     float
    rssi_min:     float
    duration_sec: Optional[float]
    created_at:   datetime
    geojson:      dict
    # Bounding box — needed by frontend to position the PNG image overlay
    sw_lat: Optional[float] = None
    sw_lon: Optional[float] = None
    ne_lat: Optional[float] = None
    ne_lon: Optional[float] = None

    class Config:
        from_attributes = True


class Waypoint(BaseModel):
    lat: float = Field(..., ge=-90,  le=90)
    lon: float = Field(..., ge=-180, le=180)


class CrossSectionRequest(BaseModel):
    site_id:    int
    mode:       AnalysisMode
    waypoints:  List[Waypoint] = Field(..., min_length=2)
    # Points sampled per km along the route
    resolution: Optional[int] = Field(5, ge=2, le=20)


# ── Coverage Analysis ─────────────────────────────────────────────────────────

@router.post("/analyze", response_model=CoverageResultOut)
def analyze(
    body: CoverageRequest,
    db: Session = Depends(get_db),
    _: User = Depends(require_role("ADMIN", "OPERATOR")),
):
    """Run coverage analysis over a bounding box and persist the result."""
    site = db.query(Site).filter(Site.id == body.site_id).first()
    if not site:
        raise HTTPException(404, detail="אתר לא נמצא")
    if body.sw_lat >= body.ne_lat or body.sw_lon >= body.ne_lon:
        raise HTTPException(400, detail="גבולות תא שטח לא תקינים")

    sp = SiteParams(
        lat=site.lat, lon=site.lon,
        ant_height=site.ant_height,
        elevation_m=site.elevation_m or 0.0,
        frequency=site.frequency,
        tx_power=site.tx_power,
        rx_threshold=site.rx_threshold,
        # Cap analysis radius at the system maximum to prevent runaway jobs
        max_radius=min(site.max_radius, settings.MAX_ANALYSIS_RADIUS_KM),
    )
    bbox = BoundingBox(body.sw_lat, body.sw_lon, body.ne_lat, body.ne_lon)

    # Fetch real terrain elevation grid for the analysis bbox.
    # Falls back to None automatically if tiles are unavailable (network error),
    # in which case the engine uses the pseudo-random fallback.
    res = body.resolution or settings.COVERAGE_GRID_RESOLUTION
    elev_grid = fetch_elevation_grid(
        body.sw_lat, body.sw_lon,
        body.ne_lat, body.ne_lon,
        out_width=res, out_height=res,
    )

    stats = calculate_coverage(sp, bbox, body.mode.value, res, elev_grid=elev_grid)

    # Persist GeoJSON to disk for later retrieval without re-computing
    os.makedirs(settings.RESULTS_DIR, exist_ok=True)
    geojson_path = os.path.join(
        settings.RESULTS_DIR,
        f"site{site.id}_{body.mode.value}_{int(datetime.utcnow().timestamp())}.geojson",
    )
    with open(geojson_path, "w") as f:
        json.dump(stats.geojson, f)

    result = CoverageResult(
        site_id=site.id,
        mode=body.mode,
        poly_sw_lat=body.sw_lat, poly_sw_lon=body.sw_lon,
        poly_ne_lat=body.ne_lat, poly_ne_lon=body.ne_lon,
        covered_pct=stats.covered_pct,
        rssi_avg=stats.rssi_avg,
        rssi_max=stats.rssi_max,
        rssi_min=stats.rssi_min,
        duration_sec=stats.duration_sec,
        geojson_path=geojson_path,
    )
    db.add(result)
    db.commit()
    db.refresh(result)

    return {
        "id":           result.id,
        "site_id":      result.site_id,
        "mode":         result.mode.value,
        "covered_pct":  result.covered_pct,
        "rssi_avg":     result.rssi_avg,
        "rssi_max":     result.rssi_max,
        "rssi_min":     result.rssi_min,
        "duration_sec": result.duration_sec,
        "created_at":   result.created_at,
        "geojson":      stats.geojson,
        "sw_lat":       body.sw_lat,
        "sw_lon":       body.sw_lon,
        "ne_lat":       body.ne_lat,
        "ne_lon":       body.ne_lon,
    }


@router.get("/result/{result_id}", response_model=CoverageResultOut)
def get_result(
    result_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Return a stored result with its full GeoJSON (used to reload onto the map)."""
    result = db.query(CoverageResult).filter(CoverageResult.id == result_id).first()
    if not result:
        raise HTTPException(404, detail="תוצאה לא נמצאה")

    geojson: dict = {}
    if result.geojson_path and os.path.exists(result.geojson_path):
        with open(result.geojson_path) as f:
            geojson = json.load(f)

    return {
        "id":           result.id,
        "site_id":      result.site_id,
        "mode":         result.mode.value,
        "covered_pct":  result.covered_pct,
        "rssi_avg":     result.rssi_avg,
        "rssi_max":     result.rssi_max,
        "rssi_min":     result.rssi_min,
        "duration_sec": result.duration_sec,
        "created_at":   result.created_at,
        "geojson":      geojson,
    }


@router.delete("/result/{result_id}", status_code=204)
def delete_result(
    result_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_role("ADMIN", "OPERATOR")),
):
    """Delete a result record and its associated GeoJSON file."""
    result = db.query(CoverageResult).filter(CoverageResult.id == result_id).first()
    if not result:
        raise HTTPException(404, detail="תוצאה לא נמצאה")
    if result.geojson_path and os.path.exists(result.geojson_path):
        try:
            os.remove(result.geojson_path)
        except OSError:
            pass  # file already gone — not a fatal error
    db.delete(result)
    db.commit()


# ── History / Listing ─────────────────────────────────────────────────────────

@router.get("/history/{site_id}")
def history(
    site_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Return the 20 most recent analyses for a single site."""
    results = (
        db.query(CoverageResult)
        .filter(CoverageResult.site_id == site_id)
        .order_by(CoverageResult.created_at.desc())
        .limit(20)
        .all()
    )
    return [
        {
            "id":           r.id,
            "mode":         r.mode.value,
            "covered_pct":  r.covered_pct,
            "rssi_avg":     r.rssi_avg,
            "created_at":   r.created_at,
            "duration_sec": r.duration_sec,
        }
        for r in results
    ]


@router.get("/")
def list_all(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Return up to 100 recent results across all sites (history panel)."""
    results = (
        db.query(CoverageResult)
        .options(joinedload(CoverageResult.site))
        .order_by(CoverageResult.created_at.desc())
        .limit(100)
        .all()
    )
    return [
        {
            "id":           r.id,
            "site_id":      r.site_id,
            "site_name":    r.site.name if r.site else f"אתר {r.site_id}",
            "mode":         r.mode.value,
            "covered_pct":  r.covered_pct,
            "rssi_avg":     r.rssi_avg,
            "rssi_max":     r.rssi_max,
            "rssi_min":     r.rssi_min,
            "duration_sec": r.duration_sec,
            "created_at":   r.created_at,
            "poly_sw_lat":  r.poly_sw_lat, "poly_sw_lon": r.poly_sw_lon,
            "poly_ne_lat":  r.poly_ne_lat, "poly_ne_lon": r.poly_ne_lon,
        }
        for r in results
    ]


# ── Analysis Report ───────────────────────────────────────────────────────────

@router.get("/result/{result_id}/report")
def get_report(
    result_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """
    Return a comprehensive analysis report for a stored result.
    Includes: signal distribution, propagation model data,
    risk scoring, dead-zone clustering, and operational recommendations.
    """
    result = (
        db.query(CoverageResult)
        .options(joinedload(CoverageResult.site))
        .filter(CoverageResult.id == result_id)
        .first()
    )
    if not result:
        raise HTTPException(404, detail="תוצאה לא נמצאה")

    site = result.site

    # ── Signal distribution from stored GeoJSON ───────────────────────────────
    geojson = {}
    level_counts = {"excellent": 0, "good": 0, "medium": 0, "weak": 0, "marginal": 0}
    if result.geojson_path and os.path.exists(result.geojson_path):
        with open(result.geojson_path) as f:
            geojson = json.load(f)
        for feat in geojson.get("features", []):
            lvl = feat.get("properties", {}).get("level")
            if lvl in level_counts:
                level_counts[lvl] += 1

    total_covered = sum(level_counts.values())
    signal_distribution = {
        lvl: {
            "count": cnt,
            "pct":   round(cnt / total_covered * 100, 1) if total_covered else 0,
        }
        for lvl, cnt in level_counts.items()
    }

    # ── Propagation helpers (mirror of coverage_engine formulas) ──────────────
    def _fspl_db(dist_km: float, freq_mhz: int) -> float:
        """Free Space Path Loss: FSPL = 20log(d_m) + 20log(f_hz) - 147.55"""
        if dist_km <= 0:
            return 0.0
        return (20 * math.log10(dist_km * 1000)
                + 20 * math.log10(freq_mhz * 1e6)
                - 147.55)

    height_gain    = 20 * math.log10(max(site.ant_height, 1.5) / 1.5)
    TERRAIN_MARGIN = 7.5  # dB assumed average terrain loss for theoretical radii

    level_thresholds = {
        "excellent": -70, "good": -80, "medium": -90,
        "weak": -100, "marginal": -110,
    }

    def _max_radius_km(threshold: float) -> float:
        """Theoretical maximum range for a given RSSI threshold."""
        max_fspl  = site.tx_power + height_gain - threshold - TERRAIN_MARGIN
        log_d_m   = (max_fspl + 147.55 - 20 * math.log10(site.frequency * 1e6)) / 20
        d_km      = (10 ** log_d_m) / 1000
        return round(min(d_km, site.max_radius), 2)

    theoretical_radii = {lvl: _max_radius_km(thr) for lvl, thr in level_thresholds.items()}
    fspl_table = {
        f"{d}km": round(_fspl_db(d, site.frequency), 1)
        for d in [0.5, 1, 2, 5, 10, 20]
    }

    # ── Risk scoring ──────────────────────────────────────────────────────────
    weak_pct = (signal_distribution.get("weak",     {}).get("pct", 0) +
                signal_distribution.get("marginal", {}).get("pct", 0))
    covered  = result.covered_pct

    if   covered < 50 or result.rssi_min < -115 or weak_pct > 30:
        risk_level, risk_score = "CRITICAL", 4
    elif covered < 70 or result.rssi_min < -110 or weak_pct > 20:
        risk_level, risk_score = "HIGH",     3
    elif covered < 85 or result.rssi_min < -105 or weak_pct > 10:
        risk_level, risk_score = "MEDIUM",   2
    else:
        risk_level, risk_score = "LOW",      1

    # ── Dead-zone clustering (simple proximity-based) ─────────────────────────
    none_cells = []
    for feat in geojson.get("features", []):
        props = feat.get("properties", {})
        if props.get("level") in ("none", "marginal"):
            coords = feat.get("geometry", {}).get("coordinates", [[]])
            pts = coords[0] if coords else []
            if pts:
                lats = [p[1] for p in pts]
                lons = [p[0] for p in pts]
                none_cells.append({
                    "lat":   sum(lats) / len(lats),
                    "lon":   sum(lons) / len(lons),
                    "level": props.get("level"),
                    "rssi":  props.get("rssi", -999),
                })

    def _cluster(cells, radius=0.02):
        clusters, used = [], [False] * len(cells)
        for i, c in enumerate(cells):
            if used[i]:
                continue
            grp = [c]
            used[i] = True
            for j, c2 in enumerate(cells):
                if (not used[j]
                        and abs(c2["lat"] - c["lat"]) < radius
                        and abs(c2["lon"] - c["lon"]) < radius):
                    grp.append(c2)
                    used[j] = True
            clusters.append(grp)
        return clusters

    dead_clusters = []
    if none_cells:
        for grp in sorted(_cluster(none_cells), key=lambda g: len(g), reverse=True):
            lats  = [c["lat"]  for c in grp]
            lons  = [c["lon"]  for c in grp]
            rssis = [c["rssi"] for c in grp]
            dead_clusters.append({
                "centroid_lat": round(sum(lats)  / len(lats),  5),
                "centroid_lon": round(sum(lons)  / len(lons),  5),
                "cell_count":   len(grp),
                "severity":     "none" if any(c["level"] == "none" for c in grp) else "marginal",
                "rssi_avg":     round(sum(rssis) / len(rssis), 1),
            })

    # ── Recommendations ───────────────────────────────────────────────────────
    recs: list = []
    if covered < 60:
        recs.append("כיסוי הציר נמוך מ-60% – יש לשקול הוספת ממסר ביניים באזורי האפלה")
    if covered < 85:
        recs.append("מומלץ להגביה את האנטנה הראשית לשיפור הכיסוי השטחי")
    if result.rssi_min < -105:
        recs.append("עוצמת אות מינימלית נמוכה – יש לבחון גיבוי בתקשורת גרעינית או רדיו נייד")
    if weak_pct > 15:
        recs.append(f"כ-{round(weak_pct)}% מהאזור בעוצמת אות חלשה/שולית – מומלץ לתעד נקודות עיוורות")
    if dead_clusters:
        recs.append(f"זוהו {len(dead_clusters)} אשכולות אפלה – מומלץ ביצוע סיור שטח ובחינת נקודות ממסר")
    if not recs:
        recs.append("הכיסוי תקין – אין המלצות דחופות")

    # ── Operational summary ───────────────────────────────────────────────────
    if risk_level in ("CRITICAL", "HIGH"):
        op_status     = "בעייתי"
        op_conclusion = "כיסוי הרדיו באזור הניתוח בעייתי. נדרשת פעולה מיידית לשיפור הכיסוי."
    elif risk_level == "MEDIUM":
        op_status     = "גבולי"
        op_conclusion = "כיסוי הרדיו באזור הניתוח גבולי. מומלץ ביצוע פעולות שיפור בהקדם."
    else:
        op_status     = "תקין"
        op_conclusion = "כיסוי הרדיו באזור הניתוח תקין. אין צורך בפעולות מיידיות."

    return {
        "meta": {
            "result_id":    result.id,
            "site_name":    site.name,
            "site_lat":     site.lat,
            "site_lon":     site.lon,
            "ant_height":   site.ant_height,
            "frequency":    site.frequency,
            "tx_power":     site.tx_power,
            "rx_threshold": site.rx_threshold,
            "max_radius":   site.max_radius,
            "mode":         result.mode.value,
            "analysis_date": result.created_at.isoformat(),
            "duration_sec": result.duration_sec,
        },
        "macro": {
            "covered_pct": result.covered_pct,
            "rssi_avg":    result.rssi_avg,
            "rssi_max":    result.rssi_max,
            "rssi_min":    result.rssi_min,
            "bbox": {
                "sw_lat": result.poly_sw_lat, "sw_lon": result.poly_sw_lon,
                "ne_lat": result.poly_ne_lat, "ne_lon": result.poly_ne_lon,
            },
        },
        "signal_distribution": signal_distribution,
        "propagation": {
            "model":   ("FSPL + ITU-R P.526 Knife-Edge Diffraction (DTM)" if result.mode.value == "DTM"
                        else "FSPL + ITU-R P.526 Knife-Edge Diffraction + DSM Clutter"),
            "formula": "RSSI(dBm) = Tx(dBm) – FSPL(d,f) – DiffractionLoss(ν) + HeightGain",
            "height_gain_db":             round(height_gain, 2),
            "terrain_margin_assumed_db":  TERRAIN_MARGIN,
            "fspl_table_db":              fspl_table,
            "theoretical_radii_km":       theoretical_radii,
        },
        "operational_summary": {
            "status":           op_status,
            "conclusion":       op_conclusion,
            "covered_pct":      result.covered_pct,
            "dead_zones_count": len(dead_clusters),
            "rssi_avg":         result.rssi_avg,
            "rssi_min":         result.rssi_min,
            "rssi_max":         result.rssi_max,
        },
        "risk": {
            "level": risk_level,
            "score": risk_score,
        },
        "dead_zones_clusters": dead_clusters,
        "recommendations":     recs,
    }


# ── Coverage PNG Renderer ─────────────────────────────────────────────────────

# Maps coverage-level hex colors → RGBA tuples (alpha ≈ 63%)
_COVERAGE_COLOR_MAP: dict[str, tuple[int, int, int, int]] = {
    "#00ff88": (0,   255, 136, 160),   # excellent
    "#7dff6b": (125, 255, 107, 160),   # good
    "#ffe600": (255, 230, 0,   160),   # medium
    "#ff8c00": (255, 140, 0,   160),   # weak
    "#ff3b5c": (255, 59,  92,  160),   # marginal
}


def _render_coverage_png(
    geojson: dict,
    sw_lat: float, sw_lon: float,
    ne_lat: float, ne_lon: float,
    width:  int = 900,
    height: int = 900,
) -> bytes:
    """
    Render GeoJSON coverage cells to a pixel-perfect RGBA PNG.

    Each cell is painted as a filled rectangle in the canvas array using numpy
    slice assignment — no SVG, no per-polygon anti-aliasing gaps.
    """
    canvas   = np.zeros((height, width, 4), dtype=np.uint8)
    lat_span = ne_lat - sw_lat
    lon_span = ne_lon - sw_lon

    for feat in geojson.get("features", []):
        color_hex = feat.get("properties", {}).get("color", "")
        rgba      = _COVERAGE_COLOR_MAP.get(color_hex)
        if rgba is None:
            continue

        coords = feat.get("geometry", {}).get("coordinates", [[]])[0]
        if not coords:
            continue

        lats = [c[1] for c in coords]
        lons = [c[0] for c in coords]

        # y=0 is north (ne_lat), y=height is south (sw_lat)
        r0 = int((ne_lat - max(lats)) / lat_span * height)
        r1 = int((ne_lat - min(lats)) / lat_span * height) + 1
        c0 = int((min(lons) - sw_lon) / lon_span * width)
        c1 = int((max(lons) - sw_lon) / lon_span * width) + 1

        r0 = max(0, r0);  r1 = min(height, r1)
        c0 = max(0, c0);  c1 = min(width,  c1)

        if r0 < r1 and c0 < c1:
            canvas[r0:r1, c0:c1] = rgba

    buf = io.BytesIO()
    Image.fromarray(canvas, mode="RGBA").save(buf, format="PNG")
    return buf.getvalue()


@router.get("/result/{result_id}/png")
def result_png(
    result_id: int,
    sw_lat: float, sw_lon: float,
    ne_lat: float, ne_lon: float,
    width:  int = 900,
    height: int = 900,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """
    Render a stored coverage result as an RGBA PNG image overlay.
    The client positions it with Leaflet's ImageOverlay using the same bbox.
    """
    result = db.query(CoverageResult).filter(CoverageResult.id == result_id).first()
    if not result:
        raise HTTPException(404, detail="תוצאה לא נמצאה")
    if not result.geojson_path or not os.path.exists(result.geojson_path):
        raise HTTPException(404, detail="קובץ GeoJSON לא נמצא")

    with open(result.geojson_path) as f:
        geojson = json.load(f)

    png = _render_coverage_png(geojson, sw_lat, sw_lon, ne_lat, ne_lon, width, height)
    return Response(
        content=png,
        media_type="image/png",
        headers={"Cache-Control": "max-age=3600"},
    )


# ── Cross-Section ─────────────────────────────────────────────────────────────

def _build_site_params(site: Site) -> SiteParams:
    """Helper: convert a Site ORM object to the engine's SiteParams dataclass."""
    return SiteParams(
        lat=site.lat, lon=site.lon,
        ant_height=site.ant_height,
        elevation_m=site.elevation_m or 0.0,
        frequency=site.frequency,
        tx_power=site.tx_power,
        rx_threshold=site.rx_threshold,
        max_radius=site.max_radius,
    )


def _cross_section_with_terrain(
    site: Site,
    wps: list,
    mode: str,
    resolution: int,
) -> object:
    """
    Build bbox from waypoints + site location, fetch real elevation grid,
    then run calculate_cross_section with terrain data.
    """
    sp = _build_site_params(site)

    # Bounding box that covers both the site transmitter and all waypoints
    all_lats = [w["lat"] for w in wps] + [site.lat]
    all_lons = [w["lon"] for w in wps] + [site.lon]
    # Add a small margin (~1 km ≈ 0.01°) so profile edges have elevation data
    MARGIN   = 0.01
    sw_lat   = min(all_lats) - MARGIN
    sw_lon   = min(all_lons) - MARGIN
    ne_lat   = max(all_lats) + MARGIN
    ne_lon   = max(all_lons) + MARGIN

    bbox     = BoundingBox(sw_lat, sw_lon, ne_lat, ne_lon)
    # 300 pts per axis is enough precision for profile sampling
    elev_grid = fetch_elevation_grid(sw_lat, sw_lon, ne_lat, ne_lon,
                                     out_width=300, out_height=300)

    return calculate_cross_section(sp, site.name, wps, mode, resolution,
                                   elev_grid=elev_grid, bbox=bbox)


@router.post("/cross-section")
def run_cross_section(
    body: CrossSectionRequest,
    db:   Session = Depends(get_db),
    _: User = Depends(require_role("ADMIN", "OPERATOR")),
):
    """Compute radio propagation along a multi-point route and return JSON stats."""
    site = db.query(Site).filter(Site.id == body.site_id).first()
    if not site:
        raise HTTPException(404, detail="אתר לא נמצא")

    wps   = [{"lat": w.lat, "lon": w.lon} for w in body.waypoints]
    stats = _cross_section_with_terrain(site, wps, body.mode.value, body.resolution)
    return {
        "site_id":             site.id,
        "site_name":           stats.site_name,
        "mode":                stats.mode,
        "waypoints":           stats.waypoints,
        "total_length_km":     stats.total_length_km,
        "covered_pct":         stats.covered_pct,
        "rssi_min":            stats.rssi_min,
        "rssi_max":            stats.rssi_max,
        "rssi_avg":            stats.rssi_avg,
        "risk_level":          stats.risk_level,
        "points": [
            {
                "dist_along":     p.dist_along,
                "dist_from_site": p.dist_from_site,
                "lat":            p.lat,
                "lon":            p.lon,
                "rssi":           p.rssi,
                "level":          p.level,
            }
            for p in stats.points
        ],
        "dead_zones": [
            {
                "start_km":  dz.start_km,
                "end_km":    dz.end_km,
                "length_km": dz.length_km,
                "start_lat": dz.start_lat,
                "start_lon": dz.start_lon,
                "end_lat":   dz.end_lat,
                "end_lon":   dz.end_lon,
            }
            for dz in stats.dead_zones
        ],
        "signal_distribution": stats.signal_distribution,
        "recommendations":     stats.recommendations,
        "duration_sec":        stats.duration_sec,
    }


@router.post("/cross-section/docx")
def cross_section_docx(
    body: CrossSectionRequest,
    db:   Session = Depends(get_db),
    _: User = Depends(require_role("ADMIN", "OPERATOR")),
):
    """Compute cross-section and return a Word (.docx) operational report."""
    site = db.query(Site).filter(Site.id == body.site_id).first()
    if not site:
        raise HTTPException(404, detail="אתר לא נמצא")

    wps   = [{"lat": w.lat, "lon": w.lon} for w in body.waypoints]
    stats = _cross_section_with_terrain(site, wps, body.mode.value, body.resolution)
    site_params = {
        "lat":          site.lat,
        "lon":          site.lon,
        "ant_height":   site.ant_height,
        "frequency":    site.frequency,
        "tx_power":     site.tx_power,
        "rx_threshold": site.rx_threshold,
        "max_radius":   site.max_radius,
    }
    docx_bytes = generate_cross_section_docx(stats, site_params)
    filename   = f"cross_section_{site.name}_{stats.mode}.docx".replace(" ", "_")

    return Response(
        content=docx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── Point Elevation ───────────────────────────────────────────────────────────

@router.get("/point-elevation")
def point_elevation(lat: float, lon: float):
    """Return terrain elevation (metres ASL) for a single WGS-84 point."""
    from app.services.elevation import fetch_elevation
    elev = fetch_elevation(lat, lon)
    return {"lat": lat, "lon": lon, "elevation_m": elev}


# ── Topography Overlay ────────────────────────────────────────────────────────

@router.get("/topo-overlay")
def topo_overlay(
    sw_lat: float, sw_lon: float,
    ne_lat: float, ne_lon: float,
    width:  int = 512,
    height: int = 512,
):
    """
    Fetch Terrarium RGB elevation tiles for the requested bbox, decode elevation,
    and return a coloured RGBA PNG overlay (blue = low, red = high).
    Used by the frontend as a Leaflet ImageOverlay.
    """
    TILE_SIZE = 256

    def _deg2num(lat: float, lon: float, z: int):
        lat_r = math.radians(lat)
        n     = 2.0 ** z
        x     = int((lon + 180.0) / 360.0 * n)
        y     = int((1.0 - math.asinh(math.tan(lat_r)) / math.pi) / 2.0 * n)
        return x, y

    def _deg2px(lat: float, lon: float, z: int):
        lat_r = math.radians(lat)
        n     = 2.0 ** z
        px    = ((lon + 180.0) / 360.0 * n) * TILE_SIZE
        py    = ((1.0 - math.asinh(math.tan(lat_r)) / math.pi) / 2.0 * n) * TILE_SIZE
        return px, py

    # Choose zoom based on bbox span
    z = 8
    span = max(abs(ne_lat - sw_lat), abs(ne_lon - sw_lon))
    if span < 0.5: z = 10
    if span < 0.1: z = 12

    x_min, y_max = _deg2num(sw_lat, sw_lon, z)
    x_max, y_min = _deg2num(ne_lat, ne_lon, z)

    # Safety: drop zoom if tile count exceeds threshold
    if (x_max - x_min + 1) * (y_max - y_min + 1) > 20:
        z -= 1
        x_min, y_max = _deg2num(sw_lat, sw_lon, z)
        x_max, y_min = _deg2num(ne_lat, ne_lon, z)

    w_tiles = x_max - x_min + 1
    h_tiles = y_max - y_min + 1

    # Assemble tile mosaic
    composite = Image.new("RGB", (w_tiles * TILE_SIZE, h_tiles * TILE_SIZE))
    for tx in range(x_min, x_max + 1):
        for ty in range(y_min, y_max + 1):
            url = (f"https://s3.amazonaws.com/elevation-tiles-prod/terrarium"
                   f"/{z}/{tx}/{ty}.png")
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "CoverageOps/1.0"})
                with urllib.request.urlopen(req, timeout=2) as resp:
                    tile = Image.open(BytesIO(resp.read())).convert("RGB")
                    composite.paste(tile, ((tx - x_min) * TILE_SIZE,
                                           (ty - y_min) * TILE_SIZE))
            except Exception:
                pass  # missing tile → stays black, not fatal

    # Crop to exact bbox pixel bounds
    px_min, py_max = _deg2px(sw_lat, sw_lon, z)
    px_max, py_min = _deg2px(ne_lat, ne_lon, z)
    left   = px_min - x_min * TILE_SIZE
    right  = px_max - x_min * TILE_SIZE
    top    = py_min - y_min * TILE_SIZE
    bottom = py_max - y_min * TILE_SIZE

    cropped = composite.crop((int(left), int(top), int(right), int(bottom)))
    cropped = cropped.resize((width, height), Image.Resampling.BILINEAR)

    # Decode Terrarium RGB → elevation (metres)
    arr  = np.array(cropped, dtype=np.float32)
    elev = arr[:, :, 0] * 256.0 + arr[:, :, 1] + arr[:, :, 2] / 256.0 - 32768.0

    valid = elev > -500
    if not np.any(valid):
        valid = np.ones_like(elev, dtype=bool)
    e_min, e_max = float(np.min(elev[valid])), float(np.max(elev[valid]))
    if e_max <= e_min:
        e_max = e_min + 1

    # Normalise and map to blue→green→red palette
    norm    = np.clip((elev - e_min) / (e_max - e_min), 0.0, 1.0)
    out     = np.zeros((height, width, 4), dtype=np.uint8)
    out[:, :, 0] = np.clip(norm * 255.0, 0, 255)                           # R
    out[:, :, 1] = np.clip((1.0 - np.abs(norm * 2.0 - 1.0)) * 255.0, 0, 255)  # G
    out[:, :, 2] = np.clip((1.0 - norm) * 255.0, 0, 255)                   # B
    out[:, :, 3] = 160                                                      # Alpha

    buf = io.BytesIO()
    Image.fromarray(out).save(buf, format="PNG")
    return Response(content=buf.getvalue(), media_type="image/png")
