"""
/api/cell-sites — civil cellular sites layer (data.gov.il, Ministry of Environmental Protection)
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user, require_role
from app.models.user import User
from app.models.cell_site import CellSite, CellSiteSync
from app.services.cell_sites_import import sync_live_or_snapshot

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


@router.get("/")
def list_cell_sites(
    status: Optional[str] = None,
    operator: Optional[str] = None,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Compact list for map rendering: [id, lat, lon, operator, status, site_type, techs]."""
    q = db.query(CellSite.id, CellSite.lat, CellSite.lon, CellSite.operator,
                 CellSite.status, CellSite.site_type, CellSite.technologies)
    if status:
        q = q.filter(CellSite.status == status)
    if operator:
        q = q.filter(CellSite.operator.in_(operator.split(",")))
    return {
        "fields": ["id", "lat", "lon", "operator", "status", "site_type", "techs"],
        "rows": [list(r) for r in q.all()],
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
    }


@router.get("/{site_id}")
def get_cell_site(site_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    s = db.query(CellSite).filter(CellSite.id == site_id).first()
    if not s:
        raise HTTPException(404, detail="אתר לא נמצא")
    return {c.name: getattr(s, c.name) for c in CellSite.__table__.columns}


@router.post("/sync")
def sync_cell_sites(db: Session = Depends(get_db), _: User = Depends(require_role("ADMIN"))):
    """Refresh from data.gov.il (falls back to the bundled snapshot if unreachable)."""
    s = sync_live_or_snapshot(db)
    return {"source": s.source, "active": s.active_count, "construction": s.construction_count,
            "skipped": s.skipped_count, "data_fetched_at": s.data_fetched_at, "note": s.note}
