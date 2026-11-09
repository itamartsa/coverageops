"""
/api/cell-sites — civil cellular sites layer (data.gov.il, Ministry of Environmental Protection)
"""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user, require_role
from app.api import sites as sites_api
from app.api.sites import SiteOut
from app.models.user import Site, User, UserRole
from app.models.cell_site import (
    CellSite, CellSiteBandDefault, CellSiteSync, CellSiteTypeDefault, sync_rf_source,
)
from app.services.cell_sites_import import (
    BAND_DEFAULTS, HEIGHT_DEFAULTS, band_defaults_map, band_label, bands_for, default_band,
    estimated_height_for, estimated_tx_power_for, seed_band_defaults, seed_type_defaults,
    sync_live_or_snapshot,
)

router = APIRouter()

SOURCE_INFO = {
    "publisher": "המשרד להגנת הסביבה – אגף קרינה",
    "portal": "data.gov.il",
    "datasets": [
        {"id": "antennaactive",  "title": "אנטנות סלולריות פעילות",
         "url": "https://data.gov.il/dataset/antennaactive"},
        {"id": "antenna_hakama", "title": "אנטנות סלולריות בהקמה",
         "url": "https://data.gov.il/dataset/antenna_hakama"},
    ],
    "update_frequency": "המשרד מעדכן את המאגר באופן שוטף (בפועל כמעט יומי)",
    "crs_note": "המקור ברשת ישראל החדשה (ITM, EPSG:2039); הומר ל-WGS84 בדיוק של כ-3 מ'",
}

HEIGHT_NOTE_HE = (
    "גובה האנטנות אינו חלק מהמאגר הממשלתי; הערכים משוערים לפי סוג האתר "
    "(ברירות מחדל שמנהל המערכת יכול לעדכן), אלא אם משתמש הזין את הגובה האמיתי לאתר מסוים."
)


RF_NOTE_HE = (
    "הספק השידור (EIRP) והתדר אינם חלק מהמאגר הממשלתי; ההספק משוער לפי סוג האתר "
    "והתדר הוא הנמוך מבין הדורות שהאתר משדר בהם (ברירות מחדל שמנהל המערכת יכול לעדכן), "
    "אלא אם משתמש הזין ערך אמיתי לאתר מסוים (לכל שדה בנפרד)."
)

# Receiver threshold for promoted sites = default handset sensitivity
PROMOTE_RX_THRESHOLD_DBM = -105.0
PROMOTE_MAX_RADIUS_KM = 30.0


class HeightIn(BaseModel):
    ant_height_m: float = Field(..., ge=0.5, le=300)
    note: Optional[str] = Field(None, max_length=256)


class TypeDefaultIn(BaseModel):
    default_height_m: Optional[float] = Field(None, ge=0.5, le=300)
    default_tx_power_dbm: Optional[float] = Field(None, ge=0, le=70)

    @model_validator(mode="after")
    def _one_required(self):
        if self.default_height_m is None and self.default_tx_power_dbm is None:
            raise ValueError("default_height_m or default_tx_power_dbm is required")
        return self


class RfIn(BaseModel):
    tx_power_dbm:  Optional[float] = Field(None, ge=0, le=70)
    frequency_mhz: Optional[int]   = Field(None, ge=100, le=6000)
    note:          Optional[str]   = Field(None, max_length=256)

    @model_validator(mode="after")
    def _one_required(self):
        if self.tx_power_dbm is None and self.frequency_mhz is None:
            raise ValueError("tx_power_dbm or frequency_mhz is required")
        return self


class BandDefaultIn(BaseModel):
    frequency_mhz: int = Field(..., ge=100, le=6000)


class PromoteIn(BaseModel):
    frequency_mhz: Optional[int]   = Field(None, ge=100, le=6000)
    rx_threshold:  Optional[float] = Field(None, ge=-130, le=-40)


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() + "Z" if dt else None


def _site_dict(s: CellSite, db: Optional[Session] = None) -> dict:
    d = {c.name: getattr(s, c.name) for c in CellSite.__table__.columns}
    d["height_updated_at"] = _iso(s.height_updated_at)
    d["rf_updated_at"] = _iso(s.rf_updated_at)
    d["bands"] = bands_for(s.technologies, db)
    return d


def _band_defaults(db: Session) -> list[dict]:
    rows = {r.generation: r for r in db.query(CellSiteBandDefault).all()}
    if not rows:
        seed_band_defaults(db)
        rows = {r.generation: r for r in db.query(CellSiteBandDefault).all()}
    order = list(BAND_DEFAULTS) + sorted(g for g in rows if g not in BAND_DEFAULTS)
    return [{"generation": g, "frequency_mhz": rows[g].frequency_mhz, "label_he": rows[g].label_he}
            for g in order if g in rows]


def _type_default_dict(r: CellSiteTypeDefault) -> dict:
    return {"site_type": r.site_type, "default_height_m": r.default_height_m,
            "default_tx_power_dbm": r.default_tx_power_dbm,
            "label_he": r.label_he, "note_he": r.note_he}


def _type_defaults(db: Session) -> list[dict]:
    rows = {r.site_type: r for r in db.query(CellSiteTypeDefault).all()}
    if not rows:                       # never seeded (e.g. fresh test DB)
        seed_type_defaults(db)
        rows = {r.site_type: r for r in db.query(CellSiteTypeDefault).all()}
    elif any(r.default_tx_power_dbm is None for r in rows.values()):
        seed_type_defaults(db)         # fills NULL tx power only
    order = list(HEIGHT_DEFAULTS) + sorted(t for t in rows if t not in HEIGHT_DEFAULTS)
    return [_type_default_dict(rows[t]) for t in order if t in rows]


def _get_site(db: Session, site_id: int) -> CellSite:
    s = db.query(CellSite).filter(CellSite.id == site_id).first()
    if not s:
        raise HTTPException(404, detail="אתר לא נמצא")
    return s


LIST_FIELDS = ["id", "lat", "lon", "operator", "status", "site_type", "techs",
               "ant_height_m", "height_source", "height_note", "height_updated_at",
               "tx_power_dbm", "frequency_mhz", "rf_source", "rf_note", "rf_updated_at", "bands",
               "tx_power_source", "frequency_source"]


@router.get("/")
def list_cell_sites(
    status: Optional[str] = None,
    operator: Optional[str] = None,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Compact list for map rendering — see LIST_FIELDS for the column order."""
    q = db.query(CellSite.id, CellSite.lat, CellSite.lon, CellSite.operator,
                 CellSite.status, CellSite.site_type, CellSite.technologies,
                 CellSite.ant_height_m, CellSite.height_source, CellSite.height_note,
                 CellSite.height_updated_at,
                 CellSite.tx_power_dbm, CellSite.frequency_mhz, CellSite.rf_source,
                 CellSite.rf_note, CellSite.rf_updated_at,
                 CellSite.tx_power_source, CellSite.frequency_source)
    if status:
        q = q.filter(CellSite.status == status)
    if operator:
        q = q.filter(CellSite.operator.in_(operator.split(",")))
    bands = band_defaults_map(db)
    band_cache: dict = {}

    def _bands(t):
        if t not in band_cache:
            band_cache[t] = bands_for(t, _cache=bands)
        return band_cache[t]

    return {
        "fields": LIST_FIELDS,
        "rows": [[*r[:10], _iso(r[10]), *r[11:15], _iso(r[15]), _bands(r[6]), r[16], r[17]]
                 for r in q.all()],
    }


@router.get("/meta")
def cell_sites_meta(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    def group(col, *filters):
        q = db.query(col, func.count()).filter(*filters).group_by(col)
        return {k or "—": v for k, v in q.all()}

    last = db.query(CellSiteSync).order_by(CellSiteSync.id.desc()).first()
    active = CellSite.status == "active"
    techs = {}
    for (t,) in db.query(CellSite.technologies).filter(active).all():
        for g in (t or "").split(","):
            if g:
                techs[g] = techs.get(g, 0) + 1
    return {
        "source": SOURCE_INFO,
        "total": db.query(func.count(CellSite.id)).scalar(),
        "by_status": group(CellSite.status),
        "by_operator": group(CellSite.operator, active),
        "by_site_type": group(CellSite.site_type_he, active),
        "by_technology": dict(sorted(techs.items())),
        "last_sync": None if not last else {
            "synced_at": last.synced_at.isoformat() + "Z",
            "source": last.source,
            "data_fetched_at": last.data_fetched_at,
            "active": last.active_count,
            "construction": last.construction_count,
            "skipped": last.skipped_count,
            "note": last.note,
        },
        "height_estimation": {
            "note_he": HEIGHT_NOTE_HE,
            "defaults": _type_defaults(db),
            "manual_count": db.query(func.count(CellSite.id))
                              .filter(CellSite.height_source == "manual").scalar(),
            "estimated_count": db.query(func.count(CellSite.id))
                                 .filter(CellSite.height_source != "manual").scalar(),
        },
        "rf_estimation": {
            "note_he": RF_NOTE_HE,
            "type_defaults": [{"site_type": d["site_type"], "label_he": d["label_he"],
                               "default_tx_power_dbm": d["default_tx_power_dbm"]}
                              for d in _type_defaults(db)],
            "band_defaults": _band_defaults(db),
            "manual_count": db.query(func.count(CellSite.id))
                              .filter(CellSite.rf_source == "manual").scalar(),
            "estimated_count": db.query(func.count(CellSite.id))
                                 .filter(CellSite.rf_source != "manual").scalar(),
            "tx_power_manual_count": db.query(func.count(CellSite.id))
                                       .filter(CellSite.tx_power_source == "manual").scalar(),
            "frequency_manual_count": db.query(func.count(CellSite.id))
                                        .filter(CellSite.frequency_source == "manual").scalar(),
        },
    }


@router.get("/type-defaults")
def list_type_defaults(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    """Estimated antenna height per site type."""
    return _type_defaults(db)


@router.put("/type-defaults/{site_type}")
def set_type_default(site_type: str, body: TypeDefaultIn, db: Session = Depends(get_db),
                     _: User = Depends(require_role("ADMIN"))):
    """Change a type's default height and/or EIRP; every site of that type still
    marked `estimated` (height / RF respectively) is updated too — manual values
    are left untouched."""
    _type_defaults(db)  # make sure the table is seeded
    row = db.query(CellSiteTypeDefault).filter(CellSiteTypeDefault.site_type == site_type).first()
    if not row:
        raise HTTPException(404, detail="סוג אתר לא מוכר")
    updated = updated_rf = 0
    if body.default_height_m is not None:
        row.default_height_m = body.default_height_m
        updated = (db.query(CellSite)
                     .filter(CellSite.site_type == site_type, CellSite.height_source != "manual")
                     .update({CellSite.ant_height_m: body.default_height_m,
                              CellSite.height_source: "estimated"}, synchronize_session=False))
    if body.default_tx_power_dbm is not None:
        row.default_tx_power_dbm = body.default_tx_power_dbm
        updated_rf = (db.query(CellSite)
                        .filter(CellSite.site_type == site_type, CellSite.tx_power_source != "manual")
                        .update({CellSite.tx_power_dbm: body.default_tx_power_dbm,
                                 CellSite.tx_power_source: "estimated"}, synchronize_session=False))
    db.commit()
    return {**_type_default_dict(row), "updated_sites": updated, "updated_rf_sites": updated_rf}


@router.get("/band-defaults")
def list_band_defaults(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    """Representative frequency (MHz) per technology generation."""
    return _band_defaults(db)


@router.put("/band-defaults/{generation}")
def set_band_default(generation: str, body: BandDefaultIn, db: Session = Depends(get_db),
                     _: User = Depends(require_role("ADMIN"))):
    """Change a generation's band (label regenerated, e.g. "4G – 2300 MHz"); sites
    whose frequency is still `estimated` and whose default band (lowest supported)
    changes as a result get the new frequency."""
    _band_defaults(db)  # make sure the table is seeded
    gen = generation.strip().upper()
    row = db.query(CellSiteBandDefault).filter(CellSiteBandDefault.generation == gen).first()
    if not row:
        raise HTTPException(404, detail="דור טכנולוגיה לא מוכר")
    if row.frequency_mhz != body.frequency_mhz:
        row.label_he = band_label(gen, body.frequency_mhz)
    row.frequency_mhz = body.frequency_mhz
    db.flush()
    bands = band_defaults_map(db)
    updated = 0
    techs = [t for (t,) in db.query(CellSite.technologies)
                             .filter(CellSite.frequency_source != "manual").distinct().all()]
    for t in techs:
        new = default_band(t, _cache=bands)
        col = CellSite.technologies.is_(None) if t is None else CellSite.technologies == t
        updated += (db.query(CellSite)
                      .filter(col, CellSite.frequency_source != "manual",
                              (CellSite.frequency_mhz != new) | CellSite.frequency_mhz.is_(None))
                      .update({CellSite.frequency_mhz: new}, synchronize_session=False))
    db.commit()
    return {"generation": row.generation, "frequency_mhz": row.frequency_mhz,
            "label_he": row.label_he, "updated_sites": updated}


@router.get("/{site_id}")
def get_cell_site(site_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return _site_dict(_get_site(db, site_id), db)


@router.patch("/{site_id}/height")
def set_cell_site_height(site_id: int, body: HeightIn, db: Session = Depends(get_db),
                         user: User = Depends(require_role("ADMIN", "OPERATOR"))):
    """Record the real antenna height of a site (marks it `manual`)."""
    s = _get_site(db, site_id)
    s.ant_height_m = round(body.ant_height_m, 1)
    s.height_source = "manual"
    s.height_note = (body.note or "").strip()[:256] or None
    s.height_updated_at = datetime.utcnow()
    s.height_updated_by = (user.username or "")[:64] or None
    db.commit()
    db.refresh(s)
    return _site_dict(s, db)


@router.delete("/{site_id}/height")
def reset_cell_site_height(site_id: int, db: Session = Depends(get_db),
                           user: User = Depends(require_role("ADMIN", "OPERATOR"))):
    """Drop a manual height and go back to the estimate for the site type."""
    s = _get_site(db, site_id)
    s.ant_height_m = estimated_height_for(s.site_type, db)
    s.height_source = "estimated"
    s.height_note = None
    s.height_updated_at = datetime.utcnow()
    s.height_updated_by = (user.username or "")[:64] or None
    db.commit()
    db.refresh(s)
    return _site_dict(s, db)


@router.patch("/{site_id}/rf")
def set_cell_site_rf(site_id: int, body: RfIn, db: Session = Depends(get_db),
                     user: User = Depends(require_role("ADMIN", "OPERATOR"))):
    """Record the real EIRP and/or frequency of a site. Only the fields sent are
    marked `manual`; a field left out keeps its current value and source."""
    s = _get_site(db, site_id)
    if body.tx_power_dbm is not None:
        s.tx_power_dbm = round(body.tx_power_dbm, 1)
        s.tx_power_source = "manual"
    if body.frequency_mhz is not None:
        s.frequency_mhz = body.frequency_mhz
        s.frequency_source = "manual"
    if s.tx_power_dbm is None:
        s.tx_power_dbm = estimated_tx_power_for(s.site_type, db)
    if s.frequency_mhz is None:
        s.frequency_mhz = default_band(s.technologies, db)
    sync_rf_source(s)
    s.rf_note = (body.note or "").strip()[:256] or None
    s.rf_updated_at = datetime.utcnow()
    s.rf_updated_by = (user.username or "")[:64] or None
    db.commit()
    db.refresh(s)
    return _site_dict(s, db)


@router.delete("/{site_id}/rf")
def reset_cell_site_rf(site_id: int, db: Session = Depends(get_db),
                       user: User = Depends(require_role("ADMIN", "OPERATOR"))):
    """Drop manual RF values and go back to the estimates (type EIRP, lowest band)."""
    s = _get_site(db, site_id)
    s.tx_power_dbm = estimated_tx_power_for(s.site_type, db)
    s.frequency_mhz = default_band(s.technologies, db)
    s.tx_power_source = s.frequency_source = "estimated"
    sync_rf_source(s)
    s.rf_note = None
    s.rf_updated_at = datetime.utcnow()
    s.rf_updated_by = (user.username or "")[:64] or None
    db.commit()
    db.refresh(s)
    return _site_dict(s, db)


def _promoted_name(c: CellSite) -> str:
    where = c.city or c.address or "—"
    code = f" ({c.site_code})" if c.site_code else ""
    return f"{c.operator_he or c.operator} – {where}{code}"[:128]


@router.post("/{site_id}/promote", response_model=SiteOut, status_code=201)
def promote_cell_site(site_id: int, response: Response, body: Optional[PromoteIn] = None,
                      db: Session = Depends(get_db),
                      user: User = Depends(require_role("ADMIN", "OPERATOR"))):
    """Analyse coverage from this site (נתח כיסוי מאתר זה): create an analysis Site from a civil cell site.

    Idempotent per user: if the caller already has a Site linked to this cell site
    (an ADMIN: any Site linked to it), that Site is returned with 200 instead.
    """
    body = body or PromoteIn()
    c = _get_site(db, site_id)

    existing = _existing_promoted(db, c.id, user)
    if existing is not None:
        response.status_code = 200
        return existing

    height_manual = c.height_source == "manual" and c.ant_height_m is not None
    tx_manual = c.tx_power_source == "manual" and c.tx_power_dbm is not None
    freq_manual = c.frequency_source == "manual" and c.frequency_mhz is not None
    ant_height = c.ant_height_m if c.ant_height_m is not None else estimated_height_for(c.site_type, db)
    tx_power = c.tx_power_dbm if c.tx_power_dbm is not None else estimated_tx_power_for(c.site_type, db)
    if body.frequency_mhz is not None:
        frequency = body.frequency_mhz
    elif c.frequency_mhz is not None:
        frequency = c.frequency_mhz
    else:
        frequency = default_band(c.technologies, db)

    # A frequency the caller picked is still "estimated" unless the cell's
    # frequency was entered manually — the band list itself is an estimate.
    estimated = []
    if not height_manual:
        estimated.append(f"גובה אנטנה ({ant_height:g} מ', לפי סוג האתר)")
    if not tx_manual:
        estimated.append(f"הספק שידור ({tx_power:g} dBm EIRP, לפי סוג האתר)")
    if not freq_manual:
        how = "נבחר מתוך הדורות באתר" if body.frequency_mhz is not None else "הדור הנמוך באתר"
        estimated.append(f"תדר ({frequency} MHz, {how})")
    params_source = "manual" if (height_manual and tx_manual and freq_manual) else "estimated"
    note = f"נוצר מאתר סלולרי אזרחי #{c.id} (data.gov.il). "
    note += ("ערכים משוערים: " + "; ".join(estimated) + ".") if estimated \
        else "כל הפרמטרים הוזנו ידנית."

    site = Site(
        name=_promoted_name(c), lat=c.lat, lon=c.lon,
        ant_height=ant_height, ant_height_is_default=False,
        elevation_m=sites_api._site_elevation(c.lat, c.lon),
        frequency=int(frequency), tx_power=tx_power,
        rx_threshold=body.rx_threshold if body.rx_threshold is not None else PROMOTE_RX_THRESHOLD_DBM,
        max_radius=PROMOTE_MAX_RADIUS_KM,
        cell_site_id=c.id, params_source=params_source, source_note=note[:256],
        owner_id=user.id,
    )
    db.add(site)
    try:
        db.commit()
    except IntegrityError:            # concurrent promote by the same user won the race
        db.rollback()
        existing = _existing_promoted(db, c.id, user)
        if existing is None:
            raise
        response.status_code = 200
        return existing
    db.refresh(site)
    return site


def _existing_promoted(db: Session, cell_id: int, user: User) -> Optional[Site]:
    q = db.query(Site).filter(Site.cell_site_id == cell_id)
    existing = q.filter(Site.owner_id == user.id).order_by(Site.id).first()
    if existing is None and user.role == UserRole.ADMIN:
        existing = q.order_by(Site.id).first()
    return existing


@router.post("/sync")
def sync_cell_sites(db: Session = Depends(get_db), _: User = Depends(require_role("ADMIN"))):
    """Refresh from data.gov.il (falls back to the bundled snapshot if unreachable)."""
    s = sync_live_or_snapshot(db)
    return {"source": s.source, "active": s.active_count, "construction": s.construction_count,
            "skipped": s.skipped_count, "data_fetched_at": s.data_fetched_at, "note": s.note}
