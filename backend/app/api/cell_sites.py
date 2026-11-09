"""
/api/cell-sites — civil cellular sites layer (data.gov.il, Ministry of Environmental Protection)
"""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user, require_role
from app.models.user import User
from app.models.cell_site import CellSite, CellSiteSync, CellSiteTypeDefault
from app.services.cell_sites_import import (
    HEIGHT_DEFAULTS, estimated_height_for, seed_type_defaults, sync_live_or_snapshot,
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


class HeightIn(BaseModel):
    ant_height_m: float = Field(..., ge=0.5, le=300)
    note: Optional[str] = Field(None, max_length=256)


class TypeDefaultIn(BaseModel):
    default_height_m: float = Field(..., ge=0.5, le=300)


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() + "Z" if dt else None


def _site_dict(s: CellSite) -> dict:
    d = {c.name: getattr(s, c.name) for c in CellSite.__table__.columns}
    d["height_updated_at"] = _iso(s.height_updated_at)
    return d


def _type_defaults(db: Session) -> list[dict]:
    rows = {r.site_type: r for r in db.query(CellSiteTypeDefault).all()}
    if not rows:                       # never seeded (e.g. fresh test DB)
        seed_type_defaults(db)
        rows = {r.site_type: r for r in db.query(CellSiteTypeDefault).all()}
    order = list(HEIGHT_DEFAULTS) + sorted(t for t in rows if t not in HEIGHT_DEFAULTS)
    return [{"site_type": t, "default_height_m": rows[t].default_height_m,
             "label_he": rows[t].label_he, "note_he": rows[t].note_he}
            for t in order if t in rows]


def _get_site(db: Session, site_id: int) -> CellSite:
    s = db.query(CellSite).filter(CellSite.id == site_id).first()
    if not s:
        raise HTTPException(404, detail="אתר לא נמצא")
    return s


@router.get("/")
def list_cell_sites(
    status: Optional[str] = None,
    operator: Optional[str] = None,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Compact list for map rendering: [id, lat, lon, operator, status, site_type, techs,
    ant_height_m, height_source, height_note, height_updated_at]."""
    q = db.query(CellSite.id, CellSite.lat, CellSite.lon, CellSite.operator,
                 CellSite.status, CellSite.site_type, CellSite.technologies,
                 CellSite.ant_height_m, CellSite.height_source, CellSite.height_note,
                 CellSite.height_updated_at)
    if status:
        q = q.filter(CellSite.status == status)
    if operator:
        q = q.filter(CellSite.operator.in_(operator.split(",")))
    return {
        "fields": ["id", "lat", "lon", "operator", "status", "site_type", "techs",
                   "ant_height_m", "height_source", "height_note", "height_updated_at"],
        "rows": [[*r[:10], _iso(r[10])] for r in q.all()],
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
    }


@router.get("/type-defaults")
def list_type_defaults(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    """Estimated antenna height per site type."""
    return _type_defaults(db)


@router.put("/type-defaults/{site_type}")
def set_type_default(site_type: str, body: TypeDefaultIn, db: Session = Depends(get_db),
                     _: User = Depends(require_role("ADMIN"))):
    """Change a type's default height; every site of that type still marked
    `estimated` is updated too (manual heights are left untouched)."""
    _type_defaults(db)  # make sure the table is seeded
    row = db.query(CellSiteTypeDefault).filter(CellSiteTypeDefault.site_type == site_type).first()
    if not row:
        raise HTTPException(404, detail="סוג אתר לא מוכר")
    row.default_height_m = body.default_height_m
    updated = (db.query(CellSite)
                 .filter(CellSite.site_type == site_type, CellSite.height_source != "manual")
                 .update({CellSite.ant_height_m: body.default_height_m,
                          CellSite.height_source: "estimated"}, synchronize_session=False))
    db.commit()
    return {"site_type": row.site_type, "default_height_m": row.default_height_m,
            "label_he": row.label_he, "note_he": row.note_he, "updated_sites": updated}


@router.get("/{site_id}")
def get_cell_site(site_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return _site_dict(_get_site(db, site_id))


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
    return _site_dict(s)


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
    return _site_dict(s)


@router.post("/sync")
def sync_cell_sites(db: Session = Depends(get_db), _: User = Depends(require_role("ADMIN"))):
    """Refresh from data.gov.il (falls back to the bundled snapshot if unreachable)."""
    s = sync_live_or_snapshot(db)
    return {"source": s.source, "active": s.active_count, "construction": s.construction_count,
            "skipped": s.skipped_count, "data_fetched_at": s.data_fetched_at, "note": s.note}
