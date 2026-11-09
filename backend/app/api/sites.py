import logging
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field, field_validator
from datetime import datetime

from app.core.database import get_db
from app.core.security import get_current_user, require_role
from app.models.user import User, UserRole, Site
from app.services.elevation import fetch_elevation

router = APIRouter()
log = logging.getLogger(__name__)

# Antenna height (m AGL) applied when the user does not enter one.
# Sites created this way are flagged so the UI can mark the value as a default.
DEFAULT_ANT_HEIGHT_M = 10.0


# ── Schemas ───────────────────────────────────────────────────────────────────
class SiteCreate(BaseModel):
    name:         str   = Field(..., min_length=1, max_length=128)
    lat:          float = Field(..., ge=-90, le=90)
    lon:          float = Field(..., ge=-180, le=180)
    # Omit (or null) to use DEFAULT_ANT_HEIGHT_M – the site is then flagged as default.
    ant_height:   Optional[float] = Field(None, ge=0.5, le=200)
    frequency:    int   = Field(..., description="MHz – 700/850/900/1800/2100/2600/3500")
    tx_power:     float = Field(43.0, ge=0, le=70)
    rx_threshold: float = Field(-90.0, ge=-130, le=-40)
    max_radius:   float = Field(350.0, ge=0.5, le=350)
    notes:        Optional[str] = None


class SiteUpdate(BaseModel):
    name:         Optional[str]   = None
    lat:          Optional[float] = None
    lon:          Optional[float] = None
    ant_height:   Optional[float] = Field(None, ge=0.5, le=200)
    frequency:    Optional[int]   = None
    tx_power:     Optional[float] = None
    rx_threshold: Optional[float] = None
    max_radius:   Optional[float] = None
    notes:        Optional[str]   = None


class SiteOut(BaseModel):
    id:           int
    name:         str
    lat:          float
    lon:          float
    ant_height:   float
    ant_height_is_default: bool = False   # True = system default, not user-entered
    elevation_m:  Optional[float] = None   # null = unknown (DEM unavailable)
    frequency:    int
    tx_power:     float
    rx_threshold: float
    max_radius:   float
    notes:        Optional[str]
    owner_id:     int
    created_at:   datetime

    @field_validator("ant_height_is_default", mode="before")
    @classmethod
    def _null_is_false(cls, v):
        return bool(v)            # legacy rows store NULL

    class Config:
        from_attributes = True


# ── Routes ────────────────────────────────────────────────────────────────────
@router.get("/", response_model=List[SiteOut])
def list_sites(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if current_user.role == UserRole.ADMIN:
        return db.query(Site).all()
    return db.query(Site).filter(Site.owner_id == current_user.id).all()


@router.post("/", response_model=SiteOut, status_code=201)
def create_site(
    body: SiteCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("ADMIN", "OPERATOR")),
):
    VALID_FREQS = {700, 850, 900, 1800, 2100, 2600, 3500}
    if body.frequency not in VALID_FREQS:
        raise HTTPException(400, detail=f"תדר לא תקין. תדרים מותרים: {VALID_FREQS}")

    elevation = _site_elevation(body.lat, body.lon)
    data = body.model_dump()
    is_default = data.get("ant_height") is None
    if is_default:
        data["ant_height"] = DEFAULT_ANT_HEIGHT_M
    site = Site(**data, ant_height_is_default=is_default,
                elevation_m=elevation, owner_id=current_user.id)
    db.add(site)
    db.commit()
    db.refresh(site)
    return site


@router.get("/{site_id}", response_model=SiteOut)
def get_site(
    site_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    site = _get_or_404(db, site_id)
    _check_access(site, current_user)
    return site


@router.put("/{site_id}", response_model=SiteOut)
def update_site(
    site_id: int,
    body: SiteUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("ADMIN", "OPERATOR")),
):
    site = _get_or_404(db, site_id)
    _check_access(site, current_user)

    updates = body.model_dump(exclude_none=True)

    # An explicitly sent antenna height is a user value – clear the default flag.
    if "ant_height" in updates:
        updates["ant_height_is_default"] = False

    # Re-fetch elevation if coordinates changed
    new_lat = updates.get("lat", site.lat)
    new_lon = updates.get("lon", site.lon)
    if "lat" in updates or "lon" in updates:
        updates["elevation_m"] = _site_elevation(new_lat, new_lon)

    for field, value in updates.items():
        setattr(site, field, value)

    db.commit()
    db.refresh(site)
    return site


@router.delete("/{site_id}", status_code=204)
def delete_site(
    site_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("ADMIN", "OPERATOR")),
):
    site = _get_or_404(db, site_id)
    _check_access(site, current_user)
    db.delete(site)
    db.commit()


# ── Helpers ───────────────────────────────────────────────────────────────────
def _site_elevation(lat: float, lon: float) -> Optional[float]:
    """
    Ground elevation (m ASL) for a site, or None when the DEM tile is
    unavailable. None means "unknown": the coverage API then samples the
    analysis DEM grid (or warns) instead of placing the transmitter at 0 m.
    """
    elev = fetch_elevation(lat, lon)
    if elev is None:
        log.warning("Site elevation unavailable for (%.5f, %.5f) – storing NULL", lat, lon)
    return elev


def _get_or_404(db, site_id):
    site = db.query(Site).filter(Site.id == site_id).first()
    if not site:
        raise HTTPException(404, detail="אתר לא נמצא")
    return site


def _check_access(site, user):
    if user.role == UserRole.ADMIN:
        return
    if site.owner_id != user.id:
        raise HTTPException(403, detail="אין גישה לאתר זה")
