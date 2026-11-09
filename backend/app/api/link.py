"""
Point-to-point Link API  (/api/link)
────────────────────────────────────
  POST   /                 link budget transmitter → receiver point (optionally saved)
  POST   /best-servers     rank civil cell sites around a receiver point (not saved)
  GET    /                 last 100 saved links (summary fields)
  GET    /{id}             full stored response JSON (incl. terrain profile)
  GET    /{id}/docx        Word report
  DELETE /{id}             delete (owner or ADMIN)

Physics lives in services/link_engine.py (calculate_link / best_servers);
this module only resolves the transmitter / receiver parameters, fetches
the DEM once per request and serialises / persists the result.

Transmitter
  kind="site"       → a user Site (sites table): stored ground elevation, else
                      sampled from the DEM, else 0 m with a warning.
  kind="cell_site"  → a civil CellSite: ant_height_m or 15 m, tx_power_dbm or
                      40 dBm, frequency = explicit, else manual RF value, else
                      default_band(technologies); ground always sampled from
                      the DEM (0 m + warning when unknown).
Receiver
  device profile (explicit id, else the default profile, else handset
  1.5 m / −105 dBm / 0 dBi) overridden by explicit height / sensitivity / gain.
"""
import dataclasses
import json
import logging
import math
from datetime import datetime
from typing import Any, List, Literal, Optional, Tuple

import numpy as np
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.api.coverage import (
    M_PER_DEG_LAT, _attachment_header, _bbox_sides_km, _estimate_dem_m_per_px,
    _m_per_deg_lon,
)
from app.core.config import settings
from app.core.database import get_db
from app.core.security import get_current_user, require_role
from app.models.cell_site import CellSite
from app.models.device_profile import DeviceProfile
from app.models.link_result import LinkResult as LinkResultRow
from app.models.user import AnalysisMode, Site, User, UserRole
from app.services.cell_sites_import import band_defaults_map, bands_for, default_band, FALLBACK_BAND_MHZ
from app.services.coverage_engine import (
    DEM_SOURCE_NONE, RX_HEIGHT_AGL, BoundingBox, SiteParams, haversine_km,
)
from app.services.link_engine import (
    BEST_PROFILE_POINTS, DEM_PARTIAL_PCT, Receiver, best_servers, calculate_link, link_bbox,
)
from app.services.report_generator import generate_link_docx
from app.services.terrain import DemAreaTooLarge, ElevationGrid, fetch_elevation_grid_ex

router = APIRouter()
log    = logging.getLogger(__name__)

# ── Limits / defaults ─────────────────────────────────────────────────────────
MAX_LINK_KM           = 150.0
LINK_DEM_MARGIN_DEG   = 0.01      # ~1 km around the link endpoints
DEM_MIN_CELLS         = 20
DEM_MAX_CELLS         = 500
FREQ_MIN_MHZ          = 100
FREQ_MAX_MHZ          = 6000
CELL_DEFAULT_HEIGHT_M = 15.0
CELL_DEFAULT_POWER    = 40.0
CELL_MAX_RADIUS_KM    = 60.0
BEST_MAX_CANDIDATES   = 400
BEST_EXCLUDED_TYPES   = ("small_indoor",)
LIST_LIMIT            = 100

DEFAULT_RX_SENS_DBM   = -105.0
DEFAULT_RX_GAIN_DBI   = 0.0

EST_FIELD_HE = {
    "ant_height_m":  "גובה אנטנה",
    "tx_power_dbm":  "הספק שידור",
    "frequency_mhz": "תדר",
}


# ── Schemas ───────────────────────────────────────────────────────────────────

class TransmitterIn(BaseModel):
    kind:          Literal["site", "cell_site"]
    id:            int
    frequency_mhz: Optional[int] = Field(None, ge=FREQ_MIN_MHZ, le=FREQ_MAX_MHZ)


class ReceiverIn(BaseModel):
    lat:               float = Field(..., ge=-90, le=90)
    lon:               float = Field(..., ge=-180, le=180)
    device_profile_id: Optional[int] = None
    height_agl:        Optional[float] = Field(None, ge=0.1, le=300)
    sensitivity_dbm:   Optional[float] = Field(None, ge=-150, le=-30)
    antenna_gain_dbi:  Optional[float] = Field(None, ge=-20, le=40)
    name:              Optional[str]   = Field(None, max_length=128)


class LinkRequest(BaseModel):
    transmitter: TransmitterIn
    receiver:    ReceiverIn
    mode:        AnalysisMode = AnalysisMode.DTM
    save:        bool = True


class BestServersRequest(BaseModel):
    receiver:    ReceiverIn
    mode:        AnalysisMode = AnalysisMode.DTM
    radius_km:   float = Field(10.0, ge=1, le=30)
    operator:    Optional[Literal["PHI", "PELEPHONE", "CELLCOM"]] = None
    statuses:    List[Literal["active", "construction"]] = Field(default_factory=lambda: ["active"])
    top_n:       int = Field(5, ge=1, le=10)
    band_policy: Literal["lowest", "highest"] = "lowest"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _jsonable(v: Any) -> Any:
    """Recursively convert numpy scalars / NaN to plain JSON values."""
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    if isinstance(v, np.generic):
        v = v.item()
    if isinstance(v, float) and not math.isfinite(v):
        return None
    return v


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() + "Z" if dt is not None else None


def _dem_dims(bbox: BoundingBox) -> Tuple[int, int]:
    """
    (width, height) cells for a DEM bbox: per axis
        clamp(round(side_m / max(TARGET_CELL_M, native_dem_m_per_px)), 20, 500)
    (same cell-size rule as coverage.adaptive_resolution, but non-square so a
    long east-west link does not waste cells north-south).
    """
    cell_m = max(float(settings.TARGET_CELL_M), float(_estimate_dem_m_per_px(bbox) or 0.0))
    cap    = min(DEM_MAX_CELLS, int(settings.MAX_GRID_RESOLUTION))
    ns_km, ew_km = _bbox_sides_km(bbox)

    def _n(side_km: float) -> int:
        return int(min(max(round(side_km * 1000.0 / cell_m), DEM_MIN_CELLS), cap))
    return _n(ew_km), _n(ns_km)


def _fetch_dem(bbox: BoundingBox) -> Optional[ElevationGrid]:
    """One DEM fetch for the bbox; DemAreaTooLarge → HTTP 413 (Hebrew)."""
    w, h = _dem_dims(bbox)
    try:
        return fetch_elevation_grid_ex(bbox.sw_lat, bbox.sw_lon, bbox.ne_lat, bbox.ne_lon,
                                       out_width=w, out_height=h)
    except DemAreaTooLarge as exc:
        raise HTTPException(413, detail=exc.message_he)


def _sample_dem(eg: Optional[ElevationGrid], b: BoundingBox,
                lat: float, lon: float) -> Optional[float]:
    """Bilinear DEM sample (cell-centre convention, as coverage.py); None = unknown."""
    if eg is None:
        return None
    if not (b.sw_lat <= lat <= b.ne_lat and b.sw_lon <= lon <= b.ne_lon):
        return None
    elev = eg.elev
    H, W = elev.shape
    rf = (b.ne_lat - lat) / (b.ne_lat - b.sw_lat) * H - 0.5
    cf = (lon - b.sw_lon) / (b.ne_lon - b.sw_lon) * W - 0.5
    rf = min(max(rf, 0.0), H - 1.0)
    cf = min(max(cf, 0.0), W - 1.0)
    r0, c0 = int(math.floor(rf)), int(math.floor(cf))
    r1, c1 = min(r0 + 1, H - 1), min(c0 + 1, W - 1)
    fr, fc = rf - r0, cf - c0
    v = (elev[r0, c0] * (1 - fr) * (1 - fc) + elev[r0, c1] * (1 - fr) * fc
         + elev[r1, c0] * fr * (1 - fc) + elev[r1, c1] * fr * fc)
    return round(float(v), 1) if np.isfinite(v) else None


def _tx_ground_warning(name: str) -> str:
    return (f"גובה הקרקע במיקום המשדר ({name}) אינו ידוע – המשדר הוצב בגובה 0 מ' מעל פני הים. "
            "התוצאה עלולה להיות שגויה.")


def _cell_name(c: CellSite) -> str:
    parts = [c.operator_he or c.operator or "", c.site_code or "", c.city or ""]
    s = " ".join(p for p in parts[:2] if p)
    if parts[2]:
        s = f"{s} – {parts[2]}" if s else parts[2]
    return s or f"אתר סלולר {c.id}"


# ── Receiver ──────────────────────────────────────────────────────────────────

def _resolve_receiver(db: Session, rin: ReceiverIn) -> Tuple[Receiver, dict]:
    """Device profile (explicit / default / built-in handset) + explicit overrides."""
    profile: Optional[DeviceProfile] = None
    if rin.device_profile_id is not None:
        profile = db.query(DeviceProfile).filter(DeviceProfile.id == rin.device_profile_id).first()
        if profile is None:
            raise HTTPException(404, detail="פרופיל מכשיר לא נמצא")
    else:
        profile = (db.query(DeviceProfile).filter(DeviceProfile.is_default.is_(True))
                   .order_by(DeviceProfile.id).first())

    h    = profile.rx_height_m        if profile else RX_HEIGHT_AGL
    sens = profile.rx_sensitivity_dbm if profile else DEFAULT_RX_SENS_DBM
    gain = (profile.antenna_gain_dbi or 0.0) if profile else DEFAULT_RX_GAIN_DBI
    if rin.height_agl is not None:
        h = rin.height_agl
    if rin.sensitivity_dbm is not None:
        sens = rin.sensitivity_dbm
    if rin.antenna_gain_dbi is not None:
        gain = rin.antenna_gain_dbi

    rx = Receiver(lat=rin.lat, lon=rin.lon, height_agl=float(h),
                  sensitivity_dbm=float(sens), antenna_gain_dbi=float(gain),
                  name=rin.name or "")
    info = {
        "lat":              rin.lat,
        "lon":              rin.lon,
        "height_agl":       float(h),
        "sensitivity_dbm":  float(sens),
        "antenna_gain_dbi": float(gain),
        "name":             rin.name or "",
        "profile_id":       profile.id if profile else None,
        "profile_key":      profile.key if profile else None,
        "profile_name":     profile.name_he if profile else None,
        "overridden":       [k for k, v in (("height_agl", rin.height_agl),
                                            ("sensitivity_dbm", rin.sensitivity_dbm),
                                            ("antenna_gain_dbi", rin.antenna_gain_dbi))
                             if v is not None],
    }
    return rx, info


# ── Transmitter ───────────────────────────────────────────────────────────────

def _check_freq(f: int) -> int:
    if not (FREQ_MIN_MHZ <= int(f) <= FREQ_MAX_MHZ):
        raise HTTPException(400, detail=f"תדר {f} MHz מחוץ לטווח הנתמך ({FREQ_MIN_MHZ}–{FREQ_MAX_MHZ} MHz)")
    return int(f)


def _resolve_transmitter(db: Session, tin: TransmitterIn, rx: Receiver,
                         user: User) -> Tuple[SiteParams, dict, Optional[Site]]:
    """
    SiteParams (elevation_m = stored value or 0 placeholder) + transmitter info
    dict. The ground is resolved later from the DEM (see _apply_tx_ground).
    A Site transmitter must be visible to the user (same rule as /api/sites).
    """
    if tin.kind == "site":
        site = db.query(Site).filter(Site.id == tin.id).first()
        if site is None:
            raise HTTPException(404, detail="אתר לא נמצא")
        _check_site_access(site, user)
        est: List[str] = []
        if (site.params_source or "manual") == "estimated":
            est = ["ant_height_m", "tx_power_dbm", "frequency_mhz"]
        elif site.ant_height_is_default:
            est = ["ant_height_m"]
        freq = tin.frequency_mhz if tin.frequency_mhz is not None else site.frequency
        if tin.frequency_mhz is not None and "frequency_mhz" in est:
            est.remove("frequency_mhz")
        freq = _check_freq(freq)
        sp = SiteParams(
            lat=site.lat, lon=site.lon, ant_height=float(site.ant_height or 10.0),
            elevation_m=float(site.elevation_m) if site.elevation_m is not None else 0.0,
            frequency=freq, tx_power=float(site.tx_power if site.tx_power is not None else 43.0),
            rx_threshold=rx.sensitivity_dbm,
            max_radius=float(site.max_radius or CELL_MAX_RADIUS_KM),
        )
        info = {
            "kind": "site", "id": site.id, "name": site.name,
            "lat": site.lat, "lon": site.lon,
            "ant_height_m": sp.ant_height, "frequency_mhz": freq, "tx_power_dbm": sp.tx_power,
            "params_source": "estimated" if est else "manual", "estimated_fields": est,
        }
        return sp, info, site

    cell = db.query(CellSite).filter(CellSite.id == tin.id).first()
    if cell is None:
        raise HTTPException(404, detail="אתר סלולר לא נמצא")
    est = []
    if cell.ant_height_m is None or (cell.height_source or "estimated") != "manual":
        est.append("ant_height_m")
    if cell.tx_power_dbm is None or (cell.tx_power_source or "estimated") != "manual":
        est.append("tx_power_dbm")
    freq_manual = (cell.frequency_source or "estimated") == "manual" and bool(cell.frequency_mhz)
    if tin.frequency_mhz is not None:
        freq = tin.frequency_mhz
    elif freq_manual:
        freq = cell.frequency_mhz
    else:
        freq = default_band(cell.technologies, db)
    # The band list is itself an estimate: a frequency is "estimated" unless the
    # cell's frequency was entered manually — even when the client sent it.
    if not freq_manual:
        est.append("frequency_mhz")
    freq = _check_freq(freq)
    sp = SiteParams(
        lat=cell.lat, lon=cell.lon,
        ant_height=float(cell.ant_height_m or CELL_DEFAULT_HEIGHT_M),
        elevation_m=0.0, frequency=freq,
        tx_power=float(cell.tx_power_dbm or CELL_DEFAULT_POWER),
        rx_threshold=rx.sensitivity_dbm, max_radius=CELL_MAX_RADIUS_KM,
    )
    info = {
        "kind": "cell_site", "id": cell.id, "name": _cell_name(cell),
        "lat": cell.lat, "lon": cell.lon,
        "ant_height_m": sp.ant_height, "frequency_mhz": freq, "tx_power_dbm": sp.tx_power,
        "params_source": "estimated" if est else "manual", "estimated_fields": est,
        "operator": cell.operator, "operator_he": cell.operator_he,
        "site_type": cell.site_type, "site_type_he": cell.site_type_he,
        "technologies": cell.technologies,
        "bands": bands_for(cell.technologies, db),
    }
    return sp, info, None


def _link_warnings(lr, tx_info: dict, tx_ground_unknown: bool) -> List[str]:
    w: List[str] = []
    est = tx_info.get("estimated_fields") or []
    if est:
        names = ", ".join(EST_FIELD_HE.get(f, f) for f in est)
        w.append(f"פרמטרי המשדר משוערים ({names}) – התוצאה היא הערכה בלבד. "
                 "מומלץ להזין ערכים אמיתיים.")
    if lr.dem_source == DEM_SOURCE_NONE:
        w.append("החישוב בוצע ללא נתוני טופוגרפיה – לא חושבו חסימות טרן והתוצאה אופטימית. "
                 "אין להסתמך עליה לתכנון מבצעי.")
    elif lr.profile_valid_pct < DEM_PARTIAL_PCT:
        w.append(f"נתוני הגבהים לאורך התוואי חלקיים ({lr.profile_valid_pct:.0f}% זמינים) – "
                 "בקטעים ללא נתונים הונח שטח מישורי.")
    if tx_ground_unknown:
        w.append(_tx_ground_warning(tx_info.get("name") or ""))
    if lr.rx_ground_fallback:
        w.append(f"גובה הקרקע בנקודת הקליטה אינו ידוע – הונח גובה קרקע המשדר ({lr.tx_ground_asl:.0f} מ').")
    return w


# ── POST / ────────────────────────────────────────────────────────────────────

@router.post("/")
def run_link(
    body: LinkRequest,
    db:   Session = Depends(get_db),
    user: User = Depends(require_role("ADMIN", "OPERATOR")),
):
    """Point-to-point link budget; saved to link_results when save=true."""
    rx, rx_info = _resolve_receiver(db, body.receiver)
    sp, tx_info, site = _resolve_transmitter(db, body.transmitter, rx, user)

    dist = haversine_km(sp.lat, sp.lon, rx.lat, rx.lon)
    if dist > MAX_LINK_KM:
        raise HTTPException(
            400, detail=(f"המרחק בין המשדר לנקודת הקליטה ({dist:.0f} ק\"מ) חורג מהמרבי "
                         f"({MAX_LINK_KM:.0f} ק\"מ)."))

    bbox = link_bbox(sp.lat, sp.lon, rx.lat, rx.lon, LINK_DEM_MARGIN_DEG)
    eg   = _fetch_dem(bbox)

    # Transmitter ground: stored site value, else DEM sample, else 0 m (warned)
    tx_ground_unknown = False
    if site is not None and site.elevation_m is not None:
        tx_ground = float(site.elevation_m)
    else:
        g = _sample_dem(eg, bbox, sp.lat, sp.lon)
        if g is None:
            tx_ground, tx_ground_unknown = 0.0, True
        else:
            tx_ground = g
    sp = dataclasses.replace(sp, elevation_m=tx_ground)

    lr = calculate_link(sp, rx, body.mode.value,
                        eg.elev if eg is not None else None,
                        bbox if eg is not None else None)

    rx_info = {**rx_info, "ground_asl": lr.rx_ground_asl}
    tx_info = {**tx_info, "ground_asl": lr.tx_ground_asl}
    created = datetime.utcnow()
    resp = _jsonable({
        **dataclasses.asdict(lr),
        "id":           None,
        "created_at":   _iso(created),
        "transmitter":  tx_info,
        "receiver":     rx_info,
        "dem_m_per_px": round(eg.m_per_px, 2) if eg is not None else None,
        "warnings":     _link_warnings(lr, tx_info, tx_ground_unknown),
    })

    if body.save:
        row = LinkResultRow(
            owner_id=user.id,
            tx_kind=tx_info["kind"], tx_id=tx_info["id"], tx_name=(tx_info["name"] or "")[:256],
            tx_lat=sp.lat, tx_lon=sp.lon, tx_frequency=sp.frequency,
            tx_power_dbm=sp.tx_power, tx_ant_height_m=sp.ant_height,
            tx_params_source=tx_info["params_source"],
            rx_lat=rx.lat, rx_lon=rx.lon, rx_height_agl=rx.height_agl,
            rx_sensitivity_dbm=rx.sensitivity_dbm, rx_gain_dbi=rx.antenna_gain_dbi,
            rx_profile_key=rx_info["profile_key"], rx_name=rx.name or None,
            mode=lr.mode, distance_km=lr.distance_km, bearing_deg=lr.bearing_deg,
            rssi_dbm=lr.rssi_dbm, margin_db=lr.margin_db, level=lr.level,
            is_los=lr.is_los, diffraction_db=lr.diffraction_db, fspl_db=lr.fspl_db,
            fresnel_clearance_pct=lr.fresnel_clearance_pct,
            required_rx_height_m=lr.required_rx_height_m,
            dem_source=lr.dem_source, dem_valid_pct=lr.dem_valid_pct,
            result_json="{}", created_at=created,
        )
        db.add(row)
        db.flush()
        resp["id"] = row.id
        row.result_json = json.dumps(resp, ensure_ascii=False)
        db.commit()
    return resp


# ── POST /best-servers ────────────────────────────────────────────────────────

def _cell_out(c: CellSite, sp: SiteParams, bands: List[int]) -> dict:
    return {
        "id": c.id, "operator": c.operator, "operator_he": c.operator_he,
        "site_type": c.site_type, "site_type_he": c.site_type_he,
        "city": c.city, "address": c.address, "lat": c.lat, "lon": c.lon,
        "ant_height_m": sp.ant_height,
        "height_source": (c.height_source or "estimated") if c.ant_height_m is not None else "estimated",
        "frequency_mhz": sp.frequency, "tx_power_dbm": sp.tx_power,
        "rf_source": c.rf_source or "estimated", "bands": bands,
        "tx_power_source": c.tx_power_source or "estimated",
        "frequency_source": c.frequency_source or "estimated",
        "status": c.status, "site_code": c.site_code,
    }


@router.post("/best-servers")
def run_best_servers(
    body: BestServersRequest,
    db:   Session = Depends(get_db),
    _:    User = Depends(require_role("ADMIN", "OPERATOR")),
):
    """
    Rank civil cell sites within radius_km of the receiver by predicted RSSI.
    One DEM fetch for rx ± radius; candidates capped at 400 (nearest first).
    """
    rx, rx_info = _resolve_receiver(db, body.receiver)
    r_km  = float(body.radius_km)
    d_lat = r_km * 1000.0 / M_PER_DEG_LAT
    d_lon = r_km * 1000.0 / _m_per_deg_lon(rx.lat)

    q = (db.query(CellSite)
         .filter(CellSite.lat.between(rx.lat - d_lat, rx.lat + d_lat))
         .filter(CellSite.lon.between(rx.lon - d_lon, rx.lon + d_lon))
         .filter(CellSite.status.in_(list(body.statuses)))
         .filter(or_(CellSite.site_type.is_(None), CellSite.site_type.notin_(BEST_EXCLUDED_TYPES))))
    if body.operator:
        q = q.filter(CellSite.operator == body.operator)

    within = []
    for c in q.all():
        d = haversine_km(rx.lat, rx.lon, c.lat, c.lon)
        if d <= r_km:
            within.append((d, c))
    within.sort(key=lambda x: x[0])
    warnings: List[str] = []
    if len(within) > BEST_MAX_CANDIDATES:
        warnings.append(f"נמצאו {len(within)} אתרים ברדיוס – נבדקו {BEST_MAX_CANDIDATES} הקרובים ביותר.")
        within = within[:BEST_MAX_CANDIDATES]

    m = LINK_DEM_MARGIN_DEG
    bbox = BoundingBox(rx.lat - d_lat - m, rx.lon - d_lon - m,
                       rx.lat + d_lat + m, rx.lon + d_lon + m)
    eg = _fetch_dem(bbox) if within else None
    rx_ground = _sample_dem(eg, bbox, rx.lat, rx.lon)

    band_cache = band_defaults_map(db)
    cands: List[Tuple[int, SiteParams]] = []
    by_id: dict = {}
    n_ground_unknown = 0
    any_estimated = False
    for _d, c in within:
        bands = bands_for(c.technologies, _cache=band_cache)
        freq_manual = (c.frequency_source or "estimated") == "manual" and bool(c.frequency_mhz)
        tx_manual = (c.tx_power_source or "estimated") == "manual" and c.tx_power_dbm is not None
        if freq_manual:
            freq = int(c.frequency_mhz)
        elif bands:
            freq = bands[0] if body.band_policy == "lowest" else bands[-1]
        else:
            freq = FALLBACK_BAND_MHZ
        freq = int(min(max(freq, FREQ_MIN_MHZ), FREQ_MAX_MHZ))
        if (not freq_manual or not tx_manual
                or (c.height_source or "estimated") != "manual" or c.ant_height_m is None):
            any_estimated = True
        g = _sample_dem(eg, bbox, c.lat, c.lon)
        if g is None:
            n_ground_unknown += 1
            g = rx_ground if rx_ground is not None else 0.0
        sp = SiteParams(
            lat=c.lat, lon=c.lon,
            ant_height=float(c.ant_height_m or CELL_DEFAULT_HEIGHT_M),
            elevation_m=float(g), frequency=freq,
            tx_power=float(c.tx_power_dbm or CELL_DEFAULT_POWER),
            rx_threshold=rx.sensitivity_dbm, max_radius=r_km,
        )
        cands.append((c.id, sp))
        by_id[c.id] = (c, sp, bands)

    ranked = best_servers(cands, rx, body.mode.value,
                          eg.elev if eg is not None else None,
                          bbox if eg is not None else None,
                          top_n=body.top_n, profile_points=BEST_PROFILE_POINTS)

    if not within:
        warnings.append(f"לא נמצאו אתרים סלולריים מתאימים ברדיוס {r_km:g} ק\"מ.")
    elif eg is None:
        warnings.append("החישוב בוצע ללא נתוני טופוגרפיה – לא חושבו חסימות טרן והדירוג אופטימי.")
    elif eg.valid_pct < DEM_PARTIAL_PCT:
        warnings.append(f"נתוני הגבהים באזור חלקיים ({eg.valid_pct:.0f}%) – "
                        "בקטעים ללא נתונים הונח שטח מישורי.")
    if n_ground_unknown and eg is not None:
        warnings.append(f"ל-{n_ground_unknown} אתרים לא נמצא גובה קרקע – הונח גובה הקרקע בנקודת הקליטה.")
    if any_estimated:
        warnings.append("גובה האנטנה, ההספק והתדר של האתרים הם ברובם הערכות לפי סוג האתר "
                        "(המאגר הממשלתי אינו כולל נתוני RF) – הדירוג הוא הערכה בלבד.")

    results = []
    for rank, (cid, lr) in enumerate(ranked, start=1):
        c, sp, bands = by_id[cid]
        results.append({
            "rank": rank,
            "cell_site": _cell_out(c, sp, bands),
            "link": {
                "distance_km": lr.distance_km, "bearing_deg": lr.bearing_deg,
                "rssi_dbm": lr.rssi_dbm, "margin_db": lr.margin_db, "level": lr.level,
                "is_los": lr.is_los, "diffraction_db": lr.diffraction_db,
                "fresnel_clearance_pct": lr.fresnel_clearance_pct,
            },
        })

    return _jsonable({
        "receiver":             {**rx_info, "ground_asl": rx_ground},
        "mode":                 body.mode.value,
        "radius_km":            r_km,
        "band_policy":          body.band_policy,
        "candidates_evaluated": len(cands),
        "dem_source":           "terrarium" if eg is not None else DEM_SOURCE_NONE,
        "dem_valid_pct":        round(float(eg.valid_pct), 1) if eg is not None else 0.0,
        "results":              results,
        "warnings":             warnings,
    })


# ── Listing / retrieval ───────────────────────────────────────────────────────

_SUMMARY_COLS = (
    "id", "owner_id", "tx_kind", "tx_id", "tx_name", "tx_lat", "tx_lon", "tx_frequency",
    "tx_power_dbm", "tx_ant_height_m", "tx_params_source", "rx_lat", "rx_lon",
    "rx_height_agl", "rx_sensitivity_dbm", "rx_gain_dbi", "rx_profile_key", "rx_name",
    "mode", "distance_km", "bearing_deg", "rssi_dbm", "margin_db", "level", "is_los",
    "diffraction_db", "fspl_db", "fresnel_clearance_pct", "required_rx_height_m",
    "dem_source", "dem_valid_pct",
)


def _get_row(db: Session, link_id: int, user: Optional[User] = None) -> LinkResultRow:
    """Saved row; with `user`, a non-ADMIN may only read their own rows
    (someone else's row answers 404, like a missing one)."""
    row = db.query(LinkResultRow).filter(LinkResultRow.id == link_id).first()
    if row is None or (user is not None and user.role != UserRole.ADMIN and row.owner_id != user.id):
        raise HTTPException(404, detail="תוצאת קישור לא נמצאה")
    return row


def _check_site_access(site: Site, user: User) -> None:
    """Same rule as sites._check_access: ADMIN sees all, others only their own."""
    if user.role != UserRole.ADMIN and site.owner_id != user.id:
        raise HTTPException(403, detail="אין גישה לאתר זה")


def _stored_json(row: LinkResultRow) -> dict:
    try:
        return json.loads(row.result_json)
    except (TypeError, ValueError):
        raise HTTPException(500, detail="נתוני התוצאה השמורה פגומים")


@router.get("/")
def list_links(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Last 100 saved links, newest first (ADMIN: all users; others: own); no profile / JSON."""
    q = db.query(LinkResultRow)
    if user.role != UserRole.ADMIN:
        q = q.filter(LinkResultRow.owner_id == user.id)
    rows = (q.order_by(LinkResultRow.created_at.desc(), LinkResultRow.id.desc())
            .limit(LIST_LIMIT).all())
    return [{**{k: getattr(r, k) for k in _SUMMARY_COLS}, "created_at": _iso(r.created_at)}
            for r in rows]


@router.get("/{link_id}")
def get_link(link_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Full stored response (same shape as POST /)."""
    return _stored_json(_get_row(db, link_id, user))


@router.get("/{link_id}/docx")
def link_docx(link_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Word report for a saved link."""
    row  = _get_row(db, link_id, user)
    data = _stored_json(row)
    content  = generate_link_docx(data)
    filename = f"link_{row.id}_{row.tx_name or row.tx_kind}_{row.mode}.docx".replace(" ", "_")
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": _attachment_header(filename)},
    )


@router.delete("/{link_id}", status_code=204)
def delete_link(
    link_id: int,
    db:   Session = Depends(get_db),
    user: User = Depends(require_role("ADMIN", "OPERATOR")),
):
    """Delete a saved link — its owner or an ADMIN."""
    row = _get_row(db, link_id)
    if user.role != UserRole.ADMIN and row.owner_id != user.id:
        raise HTTPException(403, detail="ניתן למחוק רק תוצאות שיצרת")
    db.delete(row)
    db.commit()
