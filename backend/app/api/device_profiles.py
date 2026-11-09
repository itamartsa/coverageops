"""
/api/device-profiles — receiver (device) profiles used by coverage/link analysis.
Readable by every user; ADMIN-only writes. Exactly one profile is the default.
"""
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user, require_role
from app.models.device_profile import DeviceProfile, seed_device_profiles
from app.models.user import User

router = APIRouter()


class DeviceProfileIn(BaseModel):
    key:                str   = Field(..., min_length=1, max_length=32, pattern=r"^[a-z0-9_\-]+$")
    name_he:            str   = Field(..., min_length=1, max_length=64)
    rx_height_m:        float = Field(..., ge=0.1, le=300)
    rx_sensitivity_dbm: float = Field(..., ge=-150, le=-30)
    antenna_gain_dbi:   float = Field(0.0, ge=-20, le=40)
    description_he:     Optional[str] = Field(None, max_length=256)
    is_default:         bool  = False


class DeviceProfileUpdate(BaseModel):
    key:                Optional[str]   = Field(None, min_length=1, max_length=32, pattern=r"^[a-z0-9_\-]+$")
    name_he:            Optional[str]   = Field(None, min_length=1, max_length=64)
    rx_height_m:        Optional[float] = Field(None, ge=0.1, le=300)
    rx_sensitivity_dbm: Optional[float] = Field(None, ge=-150, le=-30)
    antenna_gain_dbi:   Optional[float] = Field(None, ge=-20, le=40)
    description_he:     Optional[str]   = Field(None, max_length=256)
    is_default:         Optional[bool]  = None


class DeviceProfileOut(BaseModel):
    id:                 int
    key:                str
    name_he:            str
    rx_height_m:        float
    rx_sensitivity_dbm: float
    antenna_gain_dbi:   float = 0.0
    description_he:     Optional[str] = None
    is_default:         bool = False
    created_at:         Optional[datetime] = None

    class Config:
        from_attributes = True


def _get_or_404(db: Session, profile_id: int) -> DeviceProfile:
    p = db.query(DeviceProfile).filter(DeviceProfile.id == profile_id).first()
    if not p:
        raise HTTPException(404, detail="פרופיל מכשיר לא נמצא")
    return p


def _key_taken(db: Session, key: str, exclude_id: Optional[int] = None) -> bool:
    q = db.query(DeviceProfile.id).filter(DeviceProfile.key == key)
    if exclude_id is not None:
        q = q.filter(DeviceProfile.id != exclude_id)
    return q.first() is not None


def _make_exclusive_default(db: Session, p: DeviceProfile) -> None:
    db.query(DeviceProfile).filter(DeviceProfile.id != p.id, DeviceProfile.is_default.is_(True)) \
      .update({DeviceProfile.is_default: False}, synchronize_session=False)
    p.is_default = True


@router.get("/", response_model=List[DeviceProfileOut])
def list_device_profiles(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    if db.query(DeviceProfile.id).first() is None:     # never seeded (fresh DB)
        seed_device_profiles(db)
    return db.query(DeviceProfile).order_by(DeviceProfile.is_default.desc(), DeviceProfile.id).all()


@router.post("/", response_model=DeviceProfileOut, status_code=201)
def create_device_profile(body: DeviceProfileIn, db: Session = Depends(get_db),
                          _: User = Depends(require_role("ADMIN"))):
    if _key_taken(db, body.key):
        raise HTTPException(409, detail="מפתח פרופיל קיים כבר")
    data = body.model_dump()
    is_default = data.pop("is_default")
    p = DeviceProfile(**data, is_default=False)
    db.add(p)
    db.flush()
    if is_default or db.query(DeviceProfile.id).filter(DeviceProfile.is_default.is_(True)).first() is None:
        _make_exclusive_default(db, p)
    db.commit()
    db.refresh(p)
    return p


@router.put("/{profile_id}", response_model=DeviceProfileOut)
def update_device_profile(profile_id: int, body: DeviceProfileUpdate, db: Session = Depends(get_db),
                          _: User = Depends(require_role("ADMIN"))):
    p = _get_or_404(db, profile_id)
    updates = body.model_dump(exclude_none=True)
    if "key" in updates and _key_taken(db, updates["key"], exclude_id=p.id):
        raise HTTPException(409, detail="מפתח פרופיל קיים כבר")
    make_default = updates.pop("is_default", None)
    if make_default is False and p.is_default:
        raise HTTPException(400, detail="חייב להיות פרופיל ברירת מחדל — סמן פרופיל אחר כברירת מחדל")
    for k, v in updates.items():
        setattr(p, k, v)
    if make_default:
        _make_exclusive_default(db, p)
    db.commit()
    db.refresh(p)
    return p


@router.delete("/{profile_id}", status_code=204)
def delete_device_profile(profile_id: int, db: Session = Depends(get_db),
                          _: User = Depends(require_role("ADMIN"))):
    p = _get_or_404(db, profile_id)
    if p.is_default:
        raise HTTPException(400, detail="לא ניתן למחוק את פרופיל ברירת המחדל")
    db.delete(p)
    db.commit()
