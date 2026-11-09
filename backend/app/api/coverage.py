"""
Coverage API
────────────
Endpoints for running coverage analyses, retrieving results,
generating reports, cross-section analysis, and topography overlay.

Storage model
  New results store the raw RSSI raster (coverage_engine.encode_rssi_grid,
  row 0 = north, NaN = outside the analysis radius) in
  CoverageResult.rssi_raster. PNG, GeoJSON and report are all derived from
  that raster on demand. Legacy rows (created before raster storage) only
  have a GeoJSON file at geojson_path and are still served through the
  old code paths.
"""
import io
import json
import logging
import math
import os
import time
import urllib.parse
from collections import deque
from dataclasses import replace as dc_replace
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from PIL import Image
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, joinedload

from app.core.config import settings
from app.core.database import get_db
from app.core.security import get_current_user, require_role
from app.models.user import User, Site, CoverageResult, AnalysisMode
from app.services import terrain
from app.services.coverage_engine import (
    ANY_SIGNAL_DBM, DEM_SOURCE_TERRARIUM,
    SiteParams, BoundingBox,
    calculate_coverage, calculate_cross_section,
    decode_rssi_grid, effective_height_gain_db, encode_rssi_grid,
    fspl_db, level_distribution_from_grid, rssi_grid_to_geojson, rssi_grid_to_png,
)
from app.services.elevation import fetch_elevation
from app.services.report_generator import generate_cross_section_docx
from app.services.risk import (
    assess_risk, build_area_recommendations, operational_summary,
)
from app.services.terrain import DemAreaTooLarge, ElevationGrid, fetch_elevation_grid_ex

router = APIRouter()
log    = logging.getLogger(__name__)

M_PER_DEG_LAT       = 111_320.0   # metres per degree of latitude (mean)
SITE_DEM_MARGIN_KM  = 0.5         # DEM margin around a site outside the bbox
CLIENT_RES_MIN      = 20
CLIENT_RES_MAX      = 500
MAX_PNG_SIDE        = 4096
MAX_REPORT_CLUSTERS = 10
TERRAIN_MARGIN_DB   = 7.5         # report-only assumption for theoretical radii

# Lower RSSI bound (exclusive) of each level — the boundaries used by
# coverage_engine.classify_rssi (verified in tests/test_api_coverage.py).
LEVEL_LOWER_BOUND_DBM: Dict[str, float] = {
    "excellent": -70.0, "good": -80.0, "medium": -90.0,
    "weak": -100.0, "marginal": -110.0,
}


# ── Request / Response Schemas ────────────────────────────────────────────────

class CoverageRequest(BaseModel):
    site_id:    int
    mode:       AnalysisMode
    sw_lat:     float = Field(..., ge=-90,  le=90)
    sw_lon:     float = Field(..., ge=-180, le=180)
    ne_lat:     float = Field(..., ge=-90,  le=90)
    ne_lon:     float = Field(..., ge=-180, le=180)
    # Grid resolution (cells per axis). None → adaptive (see adaptive_resolution).
    resolution: Optional[int] = Field(None, ge=CLIENT_RES_MIN, le=CLIENT_RES_MAX)


class CoverageResultOut(BaseModel):
    id:           int
    site_id:      int
    mode:         str
    covered_pct:  Optional[float] = None          # % cells ≥ threshold_dbm
    rssi_avg:     Optional[float] = None
    rssi_max:     Optional[float] = None
    rssi_min:     Optional[float] = None          # over cells with any signal
    rssi_min_all: Optional[float] = None          # over ALL in-radius cells (risk input)
    duration_sec: Optional[float] = None
    created_at:   datetime
    # Bounding box — needed by frontend to position the PNG image overlay
    sw_lat: Optional[float] = None
    sw_lon: Optional[float] = None
    ne_lat: Optional[float] = None
    ne_lon: Optional[float] = None
    # Threshold-based statistics
    covered_pct_any_signal: Optional[float] = None
    threshold_dbm:          Optional[float] = None
    weak_pct:               Optional[float] = None
    level_distribution:     Optional[Dict[str, Dict[str, float]]] = None
    # Data quality / provenance
    dem_source:         Optional[str]   = None
    dem_valid_pct:      Optional[float] = None
    dem_m_per_px:       Optional[float] = None
    resolution:         Optional[int]   = None
    model:              Optional[str]   = None
    diffraction_method: Optional[str]   = None
    dem_extended:       Optional[bool]  = None    # analyze only
    has_raster:         bool            = False
    risk_level:         Optional[str]   = None    # risk.assess_risk on stored stats
    risk_score:         Optional[int]   = None
    warnings:           List[str]       = []
    # No longer returned (use GET /result/{id}/geojson); kept for schema compat.
    geojson:            Optional[dict]  = None

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


# ── Geometry helpers ──────────────────────────────────────────────────────────

def _m_per_deg_lon(lat: float) -> float:
    return M_PER_DEG_LAT * max(math.cos(math.radians(lat)), 1e-6)


def _bbox_max_span_m(bbox: BoundingBox) -> float:
    """Larger side of the bbox in metres (lon side measured at mid-latitude)."""
    mid_lat = (bbox.sw_lat + bbox.ne_lat) / 2.0
    h = (bbox.ne_lat - bbox.sw_lat) * M_PER_DEG_LAT
    w = (bbox.ne_lon - bbox.sw_lon) * _m_per_deg_lon(mid_lat)
    return max(h, w)


def adaptive_resolution(bbox: BoundingBox, dem_m_per_px: Optional[float] = None) -> int:
    """
    Cells per axis for an analysis bbox:
        clamp(round(max_span_m / max(TARGET_CELL_M, dem_m_per_px)),
              MIN_GRID_RESOLUTION, MAX_GRID_RESOLUTION)
    A cell is never finer than the native DEM resolution (no fake detail).
    """
    cell_m = max(float(settings.TARGET_CELL_M), float(dem_m_per_px or 0.0))
    r = int(round(_bbox_max_span_m(bbox) / cell_m))
    return int(min(max(r, settings.MIN_GRID_RESOLUTION), settings.MAX_GRID_RESOLUTION))


def _estimate_dem_m_per_px(bbox: BoundingBox) -> Optional[float]:
    """Native m/px of the DEM zoom terrain.py will pick for this bbox (no network)."""
    try:
        z = terrain._choose_zoom(bbox.sw_lat, bbox.sw_lon, bbox.ne_lat, bbox.ne_lon)
        return terrain._m_per_px((bbox.sw_lat + bbox.ne_lat) / 2.0, z)
    except Exception:                                   # pragma: no cover
        return None


def _site_union_bbox(bbox: BoundingBox, lat: float, lon: float,
                     margin_km: float = SITE_DEM_MARGIN_KM) -> BoundingBox:
    """Union of bbox and the site location ± margin."""
    m_lat = margin_km * 1000.0 / M_PER_DEG_LAT
    m_lon = margin_km * 1000.0 / _m_per_deg_lon(lat)
    if bbox.sw_lat <= lat <= bbox.ne_lat and bbox.sw_lon <= lon <= bbox.ne_lon:
        return bbox
    return BoundingBox(
        min(bbox.sw_lat, lat - m_lat), min(bbox.sw_lon, lon - m_lon),
        max(bbox.ne_lat, lat + m_lat), max(bbox.ne_lon, lon + m_lon),
    )


def _extend_bbox_to_site(
    bbox: BoundingBox, site_lat: float, site_lon: float,
    R: int, max_res: int,
) -> Tuple[BoundingBox, int, bool, bool]:
    """
    Extend the analysis bbox so that it also contains the site (+ margin),
    keeping the user's cell size and alignment: the bbox is grown by whole
    cells on each side, and both axes end up with the same cell count R_ext
    (the engine needs a square R×R grid). The user bbox therefore maps to an
    exact integer window of the extended grid.

    If R_ext would exceed max_res the extension is scaled down (the part of
    the path beyond the extended bbox then uses the engine's linear-ground
    fallback).

    Returns (ext_bbox, R_ext, extended, capped).
    """
    if bbox.sw_lat <= site_lat <= bbox.ne_lat and bbox.sw_lon <= site_lon <= bbox.ne_lon:
        return bbox, R, False, False

    dlat  = (bbox.ne_lat - bbox.sw_lat) / R
    dlon  = (bbox.ne_lon - bbox.sw_lon) / R
    union = _site_union_bbox(bbox, site_lat, site_lon)

    def _cells(excess: float, d: float) -> int:
        return max(0, int(math.ceil(excess / d - 1e-9)))

    n_n = _cells(union.ne_lat - bbox.ne_lat, dlat)
    n_s = _cells(bbox.sw_lat - union.sw_lat, dlat)
    n_e = _cells(union.ne_lon - bbox.ne_lon, dlon)
    n_w = _cells(bbox.sw_lon - union.sw_lon, dlon)

    capped = False
    budget = max_res - R
    need   = max(n_n + n_s, n_e + n_w)
    if need > budget:
        capped = True
        if budget <= 0:
            return bbox, R, False, True
        f = budget / need
        n_n, n_s, n_e, n_w = (int(math.floor(n * f)) for n in (n_n, n_s, n_e, n_w))

    n_lat = R + n_n + n_s
    n_lon = R + n_e + n_w
    R_ext = max(n_lat, n_lon)
    if R_ext == R:
        return bbox, R, False, capped
    # Pad the shorter axis (split evenly) so the grid is square in cells.
    if n_lat < R_ext:
        extra = R_ext - n_lat
        n_n += extra // 2
        n_s += extra - extra // 2
    if n_lon < R_ext:
        extra = R_ext - n_lon
        n_e += extra // 2
        n_w += extra - extra // 2

    ext = BoundingBox(
        bbox.sw_lat - n_s * dlat, bbox.sw_lon - n_w * dlon,
        bbox.ne_lat + n_n * dlat, bbox.ne_lon + n_e * dlon,
    )
    return ext, R_ext, True, capped


def _crop_grid_to_bbox(grid: np.ndarray, ext: BoundingBox,
                       bbox: BoundingBox, R: int) -> np.ndarray:
    """
    Cut the window covering `bbox` out of a raster that covers `ext`
    (row 0 = north), then resample (nearest) to R×R if the window is not
    already exactly that size.
    """
    H, W     = grid.shape
    lat_span = ext.ne_lat - ext.sw_lat
    lon_span = ext.ne_lon - ext.sw_lon
    r0 = int(round((ext.ne_lat - bbox.ne_lat) / lat_span * H))
    r1 = int(round((ext.ne_lat - bbox.sw_lat) / lat_span * H))
    c0 = int(round((bbox.sw_lon - ext.sw_lon) / lon_span * W))
    c1 = int(round((bbox.ne_lon - ext.sw_lon) / lon_span * W))
    r0, r1 = max(0, r0), min(H, max(r1, r0 + 1))
    c0, c1 = max(0, c0), min(W, max(c1, c0 + 1))
    sub = grid[r0:r1, c0:c1]
    if sub.shape != (R, R):
        rows = np.minimum(((np.arange(R) + 0.5) * sub.shape[0] / R).astype(int), sub.shape[0] - 1)
        cols = np.minimum(((np.arange(R) + 0.5) * sub.shape[1] / R).astype(int), sub.shape[1] - 1)
        sub = sub[np.ix_(rows, cols)]
    return np.ascontiguousarray(sub, dtype=np.float32)


def _grid_summary(grid: np.ndarray, threshold: float) -> dict:
    """Threshold-based distribution + RSSI avg/max/min over cells with any signal."""
    dist = level_distribution_from_grid(grid, threshold)
    with np.errstate(invalid="ignore"):
        any_sig = grid[~np.isnan(grid) & (grid > ANY_SIGNAL_DBM)]
    dist["rssi_avg"] = round(float(np.mean(any_sig)), 1) if any_sig.size else 0.0
    dist["rssi_max"] = round(float(np.max(any_sig)),  1) if any_sig.size else 0.0
    dist["rssi_min"] = round(float(np.min(any_sig)),  1) if any_sig.size else 0.0
    # Risk input: robust minimum over ALL in-radius cells, including "none"
    # (≤ -110 dBm). The 5th percentile is used instead of the absolute minimum:
    # a 15×15 km box almost always contains a few deeply shadowed cells, and the
    # true minimum would rate nearly every area analysis CRITICAL.
    finite = grid[~np.isnan(grid)]
    dist["rssi_min_all"] = round(float(np.percentile(finite, 5)), 1) if finite.size else None
    return dist


def _bbox_sides_km(bbox: BoundingBox) -> Tuple[float, float]:
    """(north-south, east-west) side lengths of a bbox in km."""
    mid_lat = (bbox.sw_lat + bbox.ne_lat) / 2.0
    return ((bbox.ne_lat - bbox.sw_lat) * M_PER_DEG_LAT / 1000.0,
            (bbox.ne_lon - bbox.sw_lon) * _m_per_deg_lon(mid_lat) / 1000.0)


def _validate_bbox(sw_lat: float, sw_lon: float, ne_lat: float, ne_lon: float) -> BoundingBox:
    """400 on inverted bbox, 400 if either side exceeds MAX_BBOX_SPAN_KM."""
    if sw_lat >= ne_lat or sw_lon >= ne_lon:
        raise HTTPException(400, detail="גבולות תא שטח לא תקינים")
    bbox = BoundingBox(sw_lat, sw_lon, ne_lat, ne_lon)
    ns, ew = _bbox_sides_km(bbox)
    limit = float(settings.MAX_BBOX_SPAN_KM)
    if ns > limit or ew > limit:
        raise HTTPException(
            400,
            detail=(f"תא השטח גדול מדי ({max(ns, ew):.0f} ק\"מ) – "
                    f"אורך צלע מרבי {limit:.0f} ק\"מ. יש להקטין את האזור."),
        )
    return bbox


def _fetch_dem(bbox: BoundingBox, width: int, height: int) -> Optional[ElevationGrid]:
    """fetch_elevation_grid_ex with DemAreaTooLarge → HTTP 413 (Hebrew detail)."""
    try:
        return fetch_elevation_grid_ex(bbox.sw_lat, bbox.sw_lon, bbox.ne_lat, bbox.ne_lon,
                                       out_width=width, out_height=height)
    except DemAreaTooLarge as exc:
        raise HTTPException(413, detail=exc.message_he)


SITE_ELEV_UNKNOWN_WARNING = (
    "גובה האתר מעל פני הים אינו ידוע (לא נמצאו נתוני גבהים במיקום האתר) – "
    "המשדר הוצב בגובה 0 מ'. התוצאה עלולה להיות שגויה; יש לעדכן את מיקום/גובה האתר."
)


def _resolve_site_elevation(
    site: Site, eg: Optional[ElevationGrid], grid_bbox: BoundingBox,
) -> Tuple[float, Optional[str], str]:
    """
    Ground elevation (m ASL) of the transmitter site.

    Returns (elevation_m, warning | None, source) where source is
    "site" (stored value), "dem" (sampled from the analysis DEM grid) or
    "unknown" (0.0 with a warning). Never silently uses 0 m when the DEM has
    real terrain at the site.
    """
    if site.elevation_m is not None:
        return float(site.elevation_m), None, "site"
    if eg is not None:
        elev = eg.elev
        H, W = elev.shape
        b    = grid_bbox
        if b.sw_lat <= site.lat <= b.ne_lat and b.sw_lon <= site.lon <= b.ne_lon:
            # cell-centre convention: cell i covers [i, i+1) → centre at i + 0.5
            rf = (b.ne_lat - site.lat) / (b.ne_lat - b.sw_lat) * H - 0.5
            cf = (site.lon - b.sw_lon) / (b.ne_lon - b.sw_lon) * W - 0.5
            rf = min(max(rf, 0.0), H - 1.0)
            cf = min(max(cf, 0.0), W - 1.0)
            r0, c0 = int(math.floor(rf)), int(math.floor(cf))
            r1, c1 = min(r0 + 1, H - 1), min(c0 + 1, W - 1)
            fr, fc = rf - r0, cf - c0
            v = (elev[r0, c0] * (1 - fr) * (1 - fc) + elev[r0, c1] * (1 - fr) * fc
                 + elev[r1, c0] * fr * (1 - fc) + elev[r1, c1] * fr * fc)
            if np.isfinite(v):
                return round(float(v), 1), None, "dem"
    log.warning("site %s: elevation unknown – transmitter placed at 0 m ASL", site.id)
    return 0.0, SITE_ELEV_UNKNOWN_WARNING, "unknown"


# ── Result helpers ────────────────────────────────────────────────────────────

def _diffraction_method_from_model(model: Optional[str]) -> Optional[str]:
    if not model:
        return None
    if "Deygout" in model:
        return "deygout"
    if "single knife-edge" in model:
        return "single"
    return None


def _result_bbox(result: CoverageResult) -> BoundingBox:
    return BoundingBox(result.poly_sw_lat, result.poly_sw_lon,
                       result.poly_ne_lat, result.poly_ne_lon)


def _result_warnings(result: CoverageResult, dem_extension_capped: bool = False) -> List[str]:
    """Hebrew data-quality warnings for a stored result."""
    if result.rssi_raster is None:
        return [
            "תוצאה ממנוע קודם: אחוז הכיסוי חושב לפי 'כל אות' (מעל ‎-110 dBm) ולא לפי סף הקבלה, "
            "ואיכות נתוני הגבהים אינה ידועה. מומלץ להריץ את הניתוח מחדש."
        ]
    warnings: List[str] = []
    if result.dem_source != DEM_SOURCE_TERRARIUM:
        warnings.append(
            "החישוב בוצע ללא נתוני טופוגרפיה אמיתיים (מודל סימולציה בלבד) – "
            "אין להסתמך על התוצאה לתכנון מבצעי. יש להריץ מחדש כאשר נתוני הגבהים זמינים."
        )
    elif result.dem_valid_pct is not None and result.dem_valid_pct < 95.0:
        warnings.append(
            f"נתוני הגבהים חלקיים ({result.dem_valid_pct:.1f}% מהשטח) – באזורים ללא נתונים "
            "הונח שטח מישורי, ולכן הכיסוי שם עשוי להיות אופטימי."
        )
    if dem_extension_capped:
        warnings.append(
            "האתר רחוק מתא השטח: הטופוגרפיה בין האתר לתא השטח נכללה בחישוב באופן חלקי בלבד "
            "(מגבלת גודל רשת). חסימות בקטע הרחוק לא נלקחו בחשבון."
        )
    site = getattr(result, "site", None)
    if site is not None and getattr(site, "params_source", None) == "estimated":
        warnings.append(
            "פרמטרי המשדר (גובה אנטנה, הספק, תדר) משוערים לפי סוג האתר הסלולרי – "
            "התוצאה היא הערכה בלבד. ניתן לעדכן ערכים אמיתיים בלשונית אתרים."
        )
    return warnings


def _decode_raster(result: CoverageResult) -> Optional[np.ndarray]:
    if result.rssi_raster is None:
        return None
    try:
        return decode_rssi_grid(bytes(result.rssi_raster))
    except Exception as exc:
        log.warning("result %s: corrupt rssi_raster: %s", result.id, exc)
        return None


def _load_legacy_geojson(result: CoverageResult) -> Optional[dict]:
    if result.geojson_path and os.path.exists(result.geojson_path):
        with open(result.geojson_path) as f:
            return json.load(f)
    return None


def _risk_rssi_min(result: CoverageResult) -> Optional[float]:
    """Risk input: rssi_min_all (all in-radius cells); legacy rows fall back to rssi_min."""
    return result.rssi_min_all if result.rssi_min_all is not None else result.rssi_min


def _result_out(
    result: CoverageResult,
    grid: Optional[np.ndarray] = None,
    dem_extended: Optional[bool] = None,
    dem_extension_capped: bool = False,
    extra_warnings: Optional[List[str]] = None,
) -> dict:
    """Serialise a CoverageResult for /analyze and /result/{id} (no GeoJSON)."""
    level_distribution = None
    if grid is None:
        grid = _decode_raster(result)
    if grid is not None:
        thr = result.threshold_dbm if result.threshold_dbm is not None else -110.0
        level_distribution = level_distribution_from_grid(grid, thr)["level_distribution"]
    risk_level, risk_score = assess_risk(
        result.covered_pct or 0.0, weak_pct=result.weak_pct or 0.0,
        rssi_min=_risk_rssi_min(result),
    )
    return {
        "id":                     result.id,
        "site_id":                result.site_id,
        "mode":                   result.mode.value,
        "covered_pct":            result.covered_pct,
        "rssi_avg":               result.rssi_avg,
        "rssi_max":               result.rssi_max,
        "rssi_min":               result.rssi_min,
        "rssi_min_all":           result.rssi_min_all,
        "duration_sec":           result.duration_sec,
        "created_at":             result.created_at,
        "sw_lat":                 result.poly_sw_lat,
        "sw_lon":                 result.poly_sw_lon,
        "ne_lat":                 result.poly_ne_lat,
        "ne_lon":                 result.poly_ne_lon,
        "covered_pct_any_signal": result.covered_pct_any_signal,
        "threshold_dbm":          result.threshold_dbm,
        "weak_pct":               result.weak_pct,
        "level_distribution":     level_distribution,
        "dem_source":             result.dem_source,
        "dem_valid_pct":          result.dem_valid_pct,
        "dem_m_per_px":           result.dem_m_per_px,
        "resolution":             result.resolution,
        "model":                  result.model,
        "diffraction_method":     _diffraction_method_from_model(result.model),
        "dem_extended":           dem_extended,
        "has_raster":             result.rssi_raster is not None,
        "risk_level":             risk_level,
        "risk_score":             risk_score,
        "warnings":               (_result_warnings(result, dem_extension_capped)
                                   + list(extra_warnings or [])),
        "geojson":                None,
    }


def _result_scalars(r: CoverageResult, has_raster: bool) -> dict:
    """
    New scalar fields shared by /history and / listings. `has_raster` comes
    from a SQL expression so the (deferred) raster blob is never loaded.
    """
    return {
        "rssi_min_all":           r.rssi_min_all,
        "covered_pct_any_signal": r.covered_pct_any_signal,
        "threshold_dbm":          r.threshold_dbm,
        "weak_pct":               r.weak_pct,
        "dem_source":             r.dem_source,
        "dem_valid_pct":          r.dem_valid_pct,
        "dem_m_per_px":           r.dem_m_per_px,
        "resolution":             r.resolution,
        "model":                  r.model,
        "diffraction_method":     _diffraction_method_from_model(r.model),
        "has_raster":             bool(has_raster),
    }


def _build_site_params(site: Site, cap_radius: bool = False) -> SiteParams:
    """
    Helper: convert a Site ORM object to the engine's SiteParams dataclass.
    elevation_m is a placeholder (0.0) when unknown — callers replace it via
    _resolve_site_elevation once the DEM grid is available.
    """
    max_radius = site.max_radius
    if cap_radius:
        # Cap analysis radius at the system maximum to prevent runaway jobs
        max_radius = min(site.max_radius, settings.MAX_ANALYSIS_RADIUS_KM)
    return SiteParams(
        lat=site.lat, lon=site.lon,
        ant_height=site.ant_height,
        elevation_m=site.elevation_m if site.elevation_m is not None else 0.0,
        frequency=site.frequency,
        tx_power=site.tx_power,
        rx_threshold=site.rx_threshold,
        max_radius=max_radius,
    )


# ── Coverage Analysis ─────────────────────────────────────────────────────────

@router.post("/analyze", response_model=CoverageResultOut)
def analyze(
    body: CoverageRequest,
    db: Session = Depends(get_db),
    _: User = Depends(require_role("ADMIN", "OPERATOR")),
):
    """
    Run coverage analysis over a bounding box and persist the RSSI raster.

    Terrain between a site OUTSIDE the bbox and the bbox itself is included:
    the analysis runs over the bbox extended (by whole cells) to contain the
    site + 0.5 km, and the resulting raster is cropped back to the user bbox.
    """
    t0 = time.time()
    site = db.query(Site).filter(Site.id == body.site_id).first()
    if not site:
        raise HTTPException(404, detail="אתר לא נמצא")
    bbox = _validate_bbox(body.sw_lat, body.sw_lon, body.ne_lat, body.ne_lon)
    sp   = _build_site_params(site, cap_radius=True)

    # ── Grid resolution (explicit or adaptive) ────────────────────────────────
    if body.resolution:
        R = int(min(max(body.resolution, CLIENT_RES_MIN), CLIENT_RES_MAX))
    else:
        union = _site_union_bbox(bbox, sp.lat, sp.lon)
        R = adaptive_resolution(bbox, _estimate_dem_m_per_px(union))

    # ── Analysis bbox: user bbox extended to include the site ─────────────────
    ext, R_ext, extended, capped = _extend_bbox_to_site(
        bbox, sp.lat, sp.lon, R, max(settings.MAX_GRID_RESOLUTION, R),
    )

    # Real terrain for the (extended) analysis bbox at the analysis cell size.
    # None → engine uses the labelled pseudo-random fallback (dem_source="none").
    eg = _fetch_dem(ext, R_ext, R_ext)

    # Transmitter ground height: stored value, else sampled from this DEM grid
    site_elev, elev_warning, _src = _resolve_site_elevation(site, eg, ext)
    sp = dc_replace(sp, elevation_m=site_elev)

    stats = calculate_coverage(
        sp, ext, body.mode.value, R_ext,
        elev_grid=eg.elev if eg is not None else None,
        build_geojson=False,
    )

    grid       = stats.rssi_grid
    valid_mask = stats.valid_mask
    if extended:
        grid = _crop_grid_to_bbox(grid, ext, bbox, R)
        if valid_mask is not None:
            valid_mask = _crop_grid_to_bbox(
                valid_mask.astype(np.float32), ext, bbox, R) > 0.5
    summary = _grid_summary(grid, sp.rx_threshold)
    # DEM completeness of the USER's bbox (not the extended analysis grid)
    dem_valid_pct = (round(float(valid_mask.mean()) * 100, 1)
                     if valid_mask is not None else stats.dem_valid_pct)

    model = stats.metadata.get("model")
    result = CoverageResult(
        site_id=site.id,
        mode=body.mode,
        poly_sw_lat=body.sw_lat, poly_sw_lon=body.sw_lon,
        poly_ne_lat=body.ne_lat, poly_ne_lon=body.ne_lon,
        covered_pct=summary["covered_pct"],
        rssi_avg=summary["rssi_avg"],
        rssi_max=summary["rssi_max"],
        rssi_min=summary["rssi_min"],
        rssi_min_all=summary["rssi_min_all"],
        covered_pct_any_signal=summary["covered_pct_any_signal"],
        weak_pct=summary["weak_pct"],
        threshold_dbm=sp.rx_threshold,
        rssi_raster=encode_rssi_grid(grid),
        resolution=R,
        dem_source=stats.dem_source,
        dem_valid_pct=dem_valid_pct,
        dem_m_per_px=round(eg.m_per_px, 2) if eg is not None else None,
        model=model[:128] if model else None,
        duration_sec=max(round(time.time() - t0, 2), 0.01),
    )
    db.add(result)
    db.commit()
    db.refresh(result)

    return _result_out(result, grid=grid, dem_extended=extended, dem_extension_capped=capped,
                       extra_warnings=[elev_warning] if elev_warning else None)


@router.get("/result/{result_id}", response_model=CoverageResultOut)
def get_result(
    result_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Return a stored result's statistics (no GeoJSON — use /png or /geojson)."""
    result = db.query(CoverageResult).filter(CoverageResult.id == result_id).first()
    if not result:
        raise HTTPException(404, detail="תוצאה לא נמצאה")
    return _result_out(result)


@router.delete("/result/{result_id}", status_code=204)
def delete_result(
    result_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_role("ADMIN", "OPERATOR")),
):
    """Delete a result record (and its legacy GeoJSON file, if any)."""
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

# rssi_raster is deferred on the model; selecting this expression tells us
# whether a raster exists without transferring the blob.
_HAS_RASTER = CoverageResult.rssi_raster.isnot(None).label("has_raster")

@router.get("/history/{site_id}")
def history(
    site_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Return the 20 most recent analyses for a single site."""
    results = (
        db.query(CoverageResult, _HAS_RASTER)
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
            "rssi_max":     r.rssi_max,
            "rssi_min":     r.rssi_min,
            "created_at":   r.created_at,
            "duration_sec": r.duration_sec,
            **_result_scalars(r, has_raster),
        }
        for r, has_raster in results
    ]


@router.get("/")
def list_all(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Return up to 100 recent results across all sites (history panel)."""
    results = (
        db.query(CoverageResult, _HAS_RASTER)
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
            **_result_scalars(r, has_raster),
        }
        for r, has_raster in results
    ]


# ── Analysis Report ───────────────────────────────────────────────────────────

def _legacy_geojson_to_grid(geojson: dict, bbox: BoundingBox) -> Optional[np.ndarray]:
    """
    Rasterise a legacy GeoJSON FeatureCollection back into an RSSI grid.
    Legacy files omit "none" cells, so cells without a feature are NaN
    (unknown) – statistics then cover only cells that had some signal.
    """
    feats = geojson.get("features") or []
    if not feats:
        return None
    try:
        ring = feats[0]["geometry"]["coordinates"][0]
        dlat = max(p[1] for p in ring) - min(p[1] for p in ring)
        dlon = max(p[0] for p in ring) - min(p[0] for p in ring)
        H = max(1, int(round((bbox.ne_lat - bbox.sw_lat) / dlat)))
        W = max(1, int(round((bbox.ne_lon - bbox.sw_lon) / dlon)))
    except (KeyError, IndexError, TypeError, ValueError, ZeroDivisionError):
        return None
    grid = np.full((H, W), np.nan, dtype=np.float32)
    for f in feats:
        try:
            ring = f["geometry"]["coordinates"][0]
            lat  = sum(p[1] for p in ring[:4]) / 4.0
            lon  = sum(p[0] for p in ring[:4]) / 4.0
            rssi = float(f["properties"]["rssi"])
        except (KeyError, IndexError, TypeError, ValueError):
            continue
        r = int((bbox.ne_lat - lat) / dlat)
        c = int((lon - bbox.sw_lon) / dlon)
        if 0 <= r < H and 0 <= c < W:
            grid[r, c] = rssi
    return grid


def dead_zone_clusters(
    grid: np.ndarray, bbox: BoundingBox, threshold: float,
) -> List[dict]:
    """
    Connected components (8-connectivity) of in-radius cells below threshold.

    O(cells): one BFS pass over the boolean mask, then per-cluster aggregates
    with np.bincount. Returns all clusters sorted by cell_count (desc) with
    centroid, cell_count, approx area_km2, rssi_avg, rssi_min and severity
    ("none" if any cell has no usable signal (≤ −110 dBm), else "marginal" –
    i.e. some signal but below threshold).
    """
    H, W = grid.shape
    with np.errstate(invalid="ignore"):
        dead = (~np.isnan(grid)) & (grid < threshold)
    if not dead.any():
        return []

    flat_dead = dead.ravel().tolist()
    labels    = np.zeros(H * W, dtype=np.int32)
    lab_list  = [0] * (H * W)
    n_labels  = 0
    neigh     = ((-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1))
    for start in np.flatnonzero(dead.ravel()).tolist():
        if lab_list[start]:
            continue
        n_labels += 1
        lab_list[start] = n_labels
        q = deque([start])
        while q:
            i = q.popleft()
            r, c = divmod(i, W)
            for dr, dc in neigh:
                rr, cc = r + dr, c + dc
                if 0 <= rr < H and 0 <= cc < W:
                    j = rr * W + cc
                    if flat_dead[j] and not lab_list[j]:
                        lab_list[j] = n_labels
                        q.append(j)
    labels[:] = lab_list

    idx  = np.flatnonzero(labels)
    lab  = labels[idx]
    rows = (idx // W).astype(np.float64)
    cols = (idx %  W).astype(np.float64)
    vals = grid.ravel()[idx].astype(np.float64)
    n    = n_labels + 1
    count   = np.bincount(lab, minlength=n)
    sum_r   = np.bincount(lab, weights=rows, minlength=n)
    sum_c   = np.bincount(lab, weights=cols, minlength=n)
    sum_v   = np.bincount(lab, weights=vals, minlength=n)
    min_v   = np.full(n, np.inf)
    np.minimum.at(min_v, lab, vals)

    dlat    = (bbox.ne_lat - bbox.sw_lat) / H
    dlon    = (bbox.ne_lon - bbox.sw_lon) / W
    mid_lat = (bbox.sw_lat + bbox.ne_lat) / 2.0
    cell_km2 = (dlat * M_PER_DEG_LAT / 1000.0) * (dlon * _m_per_deg_lon(mid_lat) / 1000.0)

    clusters = []
    for k in range(1, n):
        cnt = int(count[k])
        clusters.append({
            "centroid_lat": round(bbox.ne_lat - (sum_r[k] / cnt + 0.5) * dlat, 5),
            "centroid_lon": round(bbox.sw_lon + (sum_c[k] / cnt + 0.5) * dlon, 5),
            "cell_count":   cnt,
            "area_km2":     round(cnt * cell_km2, 3),
            "rssi_avg":     round(float(sum_v[k] / cnt), 1),
            "rssi_min":     round(float(min_v[k]), 1),
            "severity":     "none" if min_v[k] <= ANY_SIGNAL_DBM else "marginal",
        })
    clusters.sort(key=lambda c: c["cell_count"], reverse=True)
    return clusters


@router.get("/result/{result_id}/report")
def get_report(
    result_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """
    Return a comprehensive analysis report for a stored result.
    Includes: signal distribution, propagation model data, data quality,
    risk scoring (risk.assess_risk), dead-zone clustering, and operational
    recommendations (risk.build_area_recommendations).

    Legacy rows (GeoJSON only): the GeoJSON is rasterised back to a grid in
    which "none" cells are unknown, so distribution / clusters cover only the
    cells that had some signal; covered_pct is the stored legacy value.
    """
    result = (
        db.query(CoverageResult)
        .options(joinedload(CoverageResult.site))
        .filter(CoverageResult.id == result_id)
        .first()
    )
    if not result:
        raise HTTPException(404, detail="תוצאה לא נמצאה")

    site   = result.site
    bbox   = _result_bbox(result)
    legacy = result.rssi_raster is None
    thr    = result.threshold_dbm if result.threshold_dbm is not None else site.rx_threshold

    grid = _decode_raster(result)
    if grid is None and legacy:
        gj = _load_legacy_geojson(result)
        grid = _legacy_geojson_to_grid(gj, bbox) if gj else None

    # ── Signal distribution + dead zones from the raster ─────────────────────
    if grid is not None:
        dist                = level_distribution_from_grid(grid, thr)
        signal_distribution = dist["level_distribution"]
        weak_pct            = dist["weak_pct"]
        covered             = result.covered_pct if legacy else dist["covered_pct"]
        clusters            = dead_zone_clusters(grid, bbox, thr)
    else:
        signal_distribution = {}
        weak_pct            = result.weak_pct or 0.0
        covered             = result.covered_pct or 0.0
        clusters            = []
    covered = covered if covered is not None else 0.0

    # ── Risk (single source of truth: services/risk.py) ──────────────────────
    risk_rssi_min = _risk_rssi_min(result)
    risk_level, risk_score = assess_risk(
        covered, weak_pct=weak_pct, rssi_min=risk_rssi_min,
    )
    summary = operational_summary(risk_level)

    has_dem = result.dem_source == DEM_SOURCE_TERRARIUM
    recs = build_area_recommendations(
        site, covered, weak_pct, risk_rssi_min, len(clusters),
        # legacy rows: DEM provenance unknown → the legacy warning below
        # replaces the "no DEM" warning
        dem_source=DEM_SOURCE_TERRARIUM if legacy else result.dem_source,
        dem_valid_pct=None if legacy else result.dem_valid_pct,
        mode=result.mode.value,
    )
    warnings = _result_warnings(result)
    if legacy:
        recs = warnings + recs

    # ── Propagation (engine formulas) ─────────────────────────────────────────
    h_gain = effective_height_gain_db(site.ant_height, has_dem)

    def _max_radius_km(threshold: float) -> float:
        """Theoretical range for a given RSSI threshold (FSPL + assumed terrain margin)."""
        max_fspl = site.tx_power + h_gain - threshold - TERRAIN_MARGIN_DB
        log_d_m  = (max_fspl + 147.55 - 20 * math.log10(site.frequency * 1e6)) / 20
        return round(min((10 ** log_d_m) / 1000, site.max_radius), 2)

    theoretical_radii = {lvl: _max_radius_km(t) for lvl, t in LEVEL_LOWER_BOUND_DBM.items()}
    theoretical_radii["rx_threshold"] = _max_radius_km(thr)
    fspl_table = {f"{d}km": round(fspl_db(d, site.frequency), 1) for d in (0.5, 1, 2, 5, 10, 20)}

    model = result.model or (
        "FSPL + ITU-R P.526 knife-edge (מנוע קודם)" if legacy else "לא ידוע"
    )
    data_quality = {
        "dem_source":         result.dem_source,
        "dem_valid_pct":      result.dem_valid_pct,
        "dem_m_per_px":       result.dem_m_per_px,
        "resolution":         result.resolution if result.resolution else (
                                  grid.shape[0] if grid is not None else None),
        "diffraction_method": _diffraction_method_from_model(result.model),
    }

    return {
        "meta": {
            "result_id":     result.id,
            "site_name":     site.name,
            "site_lat":      site.lat,
            "site_lon":      site.lon,
            "ant_height":    site.ant_height,
            "ant_height_is_default": bool(site.ant_height_is_default),
            "frequency":     site.frequency,
            "tx_power":      site.tx_power,
            "rx_threshold":  site.rx_threshold,
            "threshold_dbm": thr,
            "max_radius":    site.max_radius,
            "mode":          result.mode.value,
            "analysis_date": result.created_at.isoformat(),
            "duration_sec":  result.duration_sec,
            "legacy":        legacy,
        },
        "macro": {
            "covered_pct":            covered,
            "covered_pct_any_signal": result.covered_pct_any_signal,
            "weak_pct":               weak_pct,
            "rssi_avg":               result.rssi_avg,
            "rssi_max":               result.rssi_max,
            "rssi_min":               result.rssi_min,
            "rssi_min_all":           result.rssi_min_all,
            "bbox": {
                "sw_lat": result.poly_sw_lat, "sw_lon": result.poly_sw_lon,
                "ne_lat": result.poly_ne_lat, "ne_lon": result.poly_ne_lon,
            },
        },
        "signal_distribution": signal_distribution,
        "propagation": {
            "model":   model,
            "formula": "RSSI(dBm) = Tx(dBm) – FSPL(d,f) – DiffractionLoss(Deygout, k=4/3) – Clutter + HeightGain",
            "height_gain_db":            round(h_gain, 2),
            "terrain_margin_assumed_db": TERRAIN_MARGIN_DB,
            "fspl_table_db":             fspl_table,
            "theoretical_radii_km":      theoretical_radii,
        },
        "operational_summary": {
            "status":           summary["status"],
            "conclusion":       summary["conclusion"],
            "covered_pct":      covered,
            "dead_zones_count": len(clusters),
            "rssi_avg":         result.rssi_avg,
            "rssi_min":         result.rssi_min,
            "rssi_max":         result.rssi_max,
        },
        "risk": {
            "level": risk_level,
            "score": risk_score,
        },
        "dead_zones_clusters": clusters[:MAX_REPORT_CLUSTERS],
        "recommendations":     recs,
        "data_quality":        data_quality,
        "warnings":            warnings,
    }


# ── Coverage PNG Renderer ─────────────────────────────────────────────────────

# Maps coverage-level hex colors → RGBA tuples (alpha ≈ 63%) — legacy renderer
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
    Legacy: render GeoJSON coverage cells to an RGBA PNG (rows without a raster).
    Each cell is painted as a filled rectangle via numpy slice assignment.
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
    sw_lat: Optional[float] = None, sw_lon: Optional[float] = None,
    ne_lat: Optional[float] = None, ne_lon: Optional[float] = None,
    width:  int = Query(900, ge=1, le=MAX_PNG_SIDE),
    height: int = Query(900, ge=1, le=MAX_PNG_SIDE),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """
    Render a stored coverage result as an RGBA PNG image overlay.

    The image ALWAYS covers the result's stored bbox (sw_lat…ne_lon in the
    result JSON); the client positions it with Leaflet's ImageOverlay using
    that bbox. The sw_lat/sw_lon/ne_lat/ne_lon query params are accepted for
    backward compatibility but ignored for raster rows; legacy GeoJSON-only
    rows use them if given (else the stored bbox).
    """
    result = db.query(CoverageResult).filter(CoverageResult.id == result_id).first()
    if not result:
        raise HTTPException(404, detail="תוצאה לא נמצאה")

    grid = _decode_raster(result)
    if grid is not None:
        png = rssi_grid_to_png(grid, width, height)
    else:
        geojson = _load_legacy_geojson(result)
        if geojson is None:
            raise HTTPException(404, detail="נתוני כיסוי לא נמצאו")
        b = _result_bbox(result)
        if None in (sw_lat, sw_lon, ne_lat, ne_lon):
            sw_lat, sw_lon, ne_lat, ne_lon = b.sw_lat, b.sw_lon, b.ne_lat, b.ne_lon
        png = _render_coverage_png(geojson, sw_lat, sw_lon, ne_lat, ne_lon, width, height)

    return Response(
        content=png,
        media_type="image/png",
        headers={"Cache-Control": "max-age=3600"},
    )


@router.get("/result/{result_id}/geojson")
def result_geojson(
    result_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """
    Download the result as a GeoJSON FeatureCollection (one square polygon per
    cell with any signal), built on demand from the raster. Legacy rows return
    their stored file.
    """
    result = (
        db.query(CoverageResult)
        .options(joinedload(CoverageResult.site))
        .filter(CoverageResult.id == result_id)
        .first()
    )
    if not result:
        raise HTTPException(404, detail="תוצאה לא נמצאה")

    grid = _decode_raster(result)
    if grid is not None:
        meta = {
            "result_id":     result.id,
            "site_id":       result.site_id,
            "site_lat":      result.site.lat if result.site else None,
            "site_lon":      result.site.lon if result.site else None,
            "mode":          result.mode.value,
            "covered_pct":   result.covered_pct,
            "covered_pct_any_signal": result.covered_pct_any_signal,
            "threshold_dbm": result.threshold_dbm,
            "weak_pct":      result.weak_pct,
            "model":         result.model,
            "dem_source":    result.dem_source,
            "dem_valid_pct": result.dem_valid_pct,
            "dem_m_per_px":  result.dem_m_per_px,
            "resolution":    result.resolution,
            "created_at":    result.created_at.isoformat() if result.created_at else None,
            "bbox": {
                "sw_lat": result.poly_sw_lat, "sw_lon": result.poly_sw_lon,
                "ne_lat": result.poly_ne_lat, "ne_lon": result.poly_ne_lon,
            },
        }
        geojson = rssi_grid_to_geojson(grid, _result_bbox(result), meta)
    else:
        geojson = _load_legacy_geojson(result)
        if geojson is None:
            raise HTTPException(404, detail="נתוני כיסוי לא נמצאו")

    filename = f"coverage_result_{result.id}_{result.mode.value}.geojson"
    return Response(
        content=json.dumps(geojson, ensure_ascii=False),
        media_type="application/geo+json",
        headers={"Content-Disposition": _attachment_header(filename)},
    )


def _attachment_header(filename: str) -> str:
    """Content-Disposition with an ASCII fallback + RFC 5987 UTF-8 name (Hebrew site names)."""
    ascii_name = filename.encode("ascii", "ignore").decode() or "download"
    ascii_name = ascii_name.replace('"', "")
    return (f'attachment; filename="{ascii_name}"; '
            f"filename*=UTF-8''{urllib.parse.quote(filename)}")


# ── Cross-Section ─────────────────────────────────────────────────────────────

def _cross_section_with_terrain(
    site: Site,
    wps: list,
    mode: str,
    resolution: int,
):
    """
    Build bbox from waypoints + site location, fetch the real elevation grid
    (adaptive resolution), then run calculate_cross_section with terrain data.
    Returns (CrossSectionStats, ElevationGrid | None, site-elevation warning | None).
    """
    sp = _build_site_params(site)

    # Bounding box that covers both the site transmitter and all waypoints
    all_lats = [w["lat"] for w in wps] + [site.lat]
    all_lons = [w["lon"] for w in wps] + [site.lon]
    # Add a small margin (~1 km ≈ 0.01°) so profile edges have elevation data
    MARGIN   = 0.01
    bbox = BoundingBox(
        min(all_lats) - MARGIN, min(all_lons) - MARGIN,
        max(all_lats) + MARGIN, max(all_lons) + MARGIN,
    )
    res = adaptive_resolution(bbox, _estimate_dem_m_per_px(bbox))
    eg  = _fetch_dem(bbox, res, res)          # DemAreaTooLarge → 413

    site_elev, elev_warning, _src = _resolve_site_elevation(site, eg, bbox)
    sp = dc_replace(sp, elevation_m=site_elev)

    stats = calculate_cross_section(
        sp, site.name, wps, mode, resolution,
        elev_grid=eg.elev if eg is not None else None,
        bbox=bbox,
    )
    return stats, eg, elev_warning


def _cross_section_warnings(stats, elev_warning: Optional[str] = None) -> List[str]:
    warnings: List[str] = [elev_warning] if elev_warning else []
    if stats.dem_source != DEM_SOURCE_TERRARIUM:
        warnings.append(
            "החתך חושב ללא נתוני טופוגרפיה אמיתיים (מודל סימולציה בלבד) – "
            "אין להסתמך עליו לתכנון מבצעי."
        )
    elif stats.dem_valid_pct is not None and stats.dem_valid_pct < 95.0:
        warnings.append(
            f"נתוני הגבהים חלקיים ({stats.dem_valid_pct:.1f}% מהשטח) – "
            "בקטעים ללא נתונים הונח שטח מישורי."
        )
    return warnings


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

    wps       = [{"lat": w.lat, "lon": w.lon} for w in body.waypoints]
    stats, eg, elev_warning = _cross_section_with_terrain(
        site, wps, body.mode.value, body.resolution)
    return {
        "site_id":             site.id,
        "site_name":           stats.site_name,
        "mode":                stats.mode,
        "waypoints":           stats.waypoints,
        "total_length_km":     stats.total_length_km,
        "covered_pct":         stats.covered_pct,              # % points rssi >= threshold_dbm
        "covered_pct_any_signal": stats.covered_pct_any_signal, # % points > -110 dBm
        "threshold_dbm":       stats.threshold_dbm,
        "rssi_min":            stats.rssi_min,
        "rssi_max":            stats.rssi_max,
        "rssi_avg":            stats.rssi_avg,
        "rssi_min_all":        stats.rssi_min_all,
        "risk_level":          stats.risk_level,
        "risk_score":          stats.risk_score,
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
        "dem_source":          stats.dem_source,
        "dem_valid_pct":       stats.dem_valid_pct,
        "dem_m_per_px":        round(eg.m_per_px, 2) if eg is not None else None,
        "warnings":            _cross_section_warnings(stats, elev_warning),
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

    wps       = [{"lat": w.lat, "lon": w.lon} for w in body.waypoints]
    stats, eg, _w = _cross_section_with_terrain(site, wps, body.mode.value, body.resolution)
    site_params = {
        "lat":          site.lat,
        "lon":          site.lon,
        "ant_height":   site.ant_height,
        "ant_height_is_default": bool(site.ant_height_is_default),
        "frequency":    site.frequency,
        "tx_power":     site.tx_power,
        "rx_threshold": site.rx_threshold,
        "max_radius":   site.max_radius,
        "dem_m_per_px": round(eg.m_per_px, 1) if eg is not None else None,
    }
    docx_bytes = generate_cross_section_docx(stats, site_params)
    filename   = f"cross_section_{site.name}_{stats.mode}.docx".replace(" ", "_")

    return Response(
        content=docx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": _attachment_header(filename)},
    )


# ── Point Elevation ───────────────────────────────────────────────────────────

@router.get("/point-elevation")
def point_elevation(
    lat: float, lon: float,
    _: User = Depends(get_current_user),
):
    """
    Return terrain elevation (metres ASL) for a single WGS-84 point.
    elevation_m is null when the DEM tile is unavailable.
    """
    elev = fetch_elevation(lat, lon)
    return {"lat": lat, "lon": lon, "elevation_m": elev}


# ── Topography Overlay ────────────────────────────────────────────────────────

def _transparent_png(width: int, height: int) -> bytes:
    buf = io.BytesIO()
    Image.new("RGBA", (width, height), (0, 0, 0, 0)).save(buf, format="PNG")
    return buf.getvalue()


@router.get("/topo-overlay")
def topo_overlay(
    sw_lat: float, sw_lon: float,
    ne_lat: float, ne_lon: float,
    width:  int = Query(512, ge=1, le=2048),
    height: int = Query(512, ge=1, le=2048),
    _: User = Depends(get_current_user),
):
    """
    Coloured RGBA elevation overlay (blue = low, red = high) for the bbox,
    built on the shared terrain layer (terrain.fetch_elevation_grid_ex).
    Pixels without DEM data are fully transparent; if no tile at all is
    available the whole image is transparent.
    """
    bbox = _validate_bbox(sw_lat, sw_lon, ne_lat, ne_lon)
    eg   = _fetch_dem(bbox, width, height)          # DemAreaTooLarge → 413
    if eg is None or not np.isfinite(eg.elev).any():
        return Response(content=_transparent_png(width, height), media_type="image/png")

    elev  = eg.elev
    valid = np.isfinite(elev)
    e_min, e_max = float(np.nanmin(elev)), float(np.nanmax(elev))
    if e_max <= e_min:
        e_max = e_min + 1

    # Normalise and map to blue→green→red palette
    with np.errstate(invalid="ignore"):
        norm = np.clip((np.where(valid, elev, e_min) - e_min) / (e_max - e_min), 0.0, 1.0)
    out = np.zeros((height, width, 4), dtype=np.uint8)
    out[:, :, 0] = np.clip(norm * 255.0, 0, 255)                              # R
    out[:, :, 1] = np.clip((1.0 - np.abs(norm * 2.0 - 1.0)) * 255.0, 0, 255)  # G
    out[:, :, 2] = np.clip((1.0 - norm) * 255.0, 0, 255)                      # B
    out[:, :, 3] = np.where(valid, 160, 0)                                    # Alpha

    buf = io.BytesIO()
    Image.fromarray(out, mode="RGBA").save(buf, format="PNG")
    return Response(content=buf.getvalue(), media_type="image/png")
