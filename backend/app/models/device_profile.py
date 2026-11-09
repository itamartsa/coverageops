"""
Receiver (device) profiles — who is listening: handset, vehicle modem, CPE...
Used by link/coverage analysis for rx height, sensitivity and antenna gain.
"""
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Float, Integer, String
from sqlalchemy.orm import Session

from app.core.database import Base


class DeviceProfile(Base):
    __tablename__ = "device_profiles"

    id                 = Column(Integer, primary_key=True, index=True)
    key                = Column(String(32), unique=True, nullable=False, index=True)
    name_he            = Column(String(64), nullable=False)
    rx_height_m        = Column(Float, nullable=False)          # metres AGL
    rx_sensitivity_dbm = Column(Float, nullable=False)          # dBm
    antenna_gain_dbi   = Column(Float, nullable=False, default=0.0)
    description_he     = Column(String(256), nullable=True)
    is_default         = Column(Boolean, nullable=False, default=False)
    created_at         = Column(DateTime, default=datetime.utcnow)


# key: (name_he, rx_height_m, rx_sensitivity_dbm, antenna_gain_dbi, description_he, is_default)
DEFAULT_DEVICE_PROFILES: dict[str, tuple] = {
    "handset":        ("טלפון נייד",          1.5, -105.0, 0.0,
                       "טלפון סלולרי ביד, בגובה אדם", True),
    "vehicle":        ("רדיו/מודם רכבי",      2.0, -108.0, 2.0,
                       "מודם/רדיו מותקן ברכב עם אנטנת גג", False),
    "cpe":            ("CPE / אנטנה קבועה",   6.0, -110.0, 9.0,
                       "יחידת קצה קבועה עם אנטנה כיוונית על גג/תורן", False),
    "handheld_radio": ("מכשיר קשר נישא",      1.5, -110.0, 0.0,
                       "מכשיר קשר ידני", False),
}


def seed_device_profiles(db: Session) -> None:
    """Insert the built-in profiles — only into an empty table, so profiles an
    admin deleted or renamed never come back."""
    if db.query(DeviceProfile.id).first() is not None:
        return
    for key, (name, h, sens, gain, desc, is_def) in DEFAULT_DEVICE_PROFILES.items():
        db.add(DeviceProfile(key=key, name_he=name, rx_height_m=h, rx_sensitivity_dbm=sens,
                             antenna_gain_dbi=gain, description_he=desc, is_default=is_def))
    db.commit()
