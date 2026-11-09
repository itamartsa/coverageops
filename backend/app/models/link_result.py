"""
Point-to-point link analyses (transmitter → receiver point).
Created by POST /api/link/ when save=true; the full API response (incl. the
terrain profile) is kept in result_json so GET /api/link/{id} and the Word
report reproduce exactly what the user saw.
"""
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import deferred

from app.core.database import Base


class LinkResult(Base):
    __tablename__ = "link_results"

    id               = Column(Integer, primary_key=True, index=True)
    owner_id         = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)

    # Transmitter
    tx_kind          = Column(String(16), nullable=False)        # site | cell_site
    tx_id            = Column(Integer, nullable=False)
    tx_name          = Column(String(256))
    tx_lat           = Column(Float, nullable=False)
    tx_lon           = Column(Float, nullable=False)
    tx_frequency     = Column(Integer)                           # MHz
    tx_power_dbm     = Column(Float)
    tx_ant_height_m  = Column(Float)
    tx_params_source = Column(String(16))                        # manual | estimated

    # Receiver
    rx_lat             = Column(Float, nullable=False)
    rx_lon             = Column(Float, nullable=False)
    rx_height_agl      = Column(Float)
    rx_sensitivity_dbm = Column(Float)
    rx_gain_dbi        = Column(Float)
    rx_profile_key     = Column(String(32))
    rx_name            = Column(String(128))

    # Result summary
    mode                  = Column(String(8), nullable=False)     # DTM | DSM
    distance_km           = Column(Float)
    bearing_deg           = Column(Float)
    rssi_dbm              = Column(Float)
    margin_db             = Column(Float)
    level                 = Column(String(16))
    is_los                = Column(Boolean)
    diffraction_db        = Column(Float)
    fspl_db               = Column(Float)
    fresnel_clearance_pct = Column(Float)
    required_rx_height_m  = Column(Float, nullable=True)
    dem_source            = Column(String(32))
    dem_valid_pct         = Column(Float)

    # Full response JSON (profile included) — deferred: listings never load it
    result_json = deferred(Column(Text, nullable=False))
    created_at  = Column(DateTime, default=datetime.utcnow)
