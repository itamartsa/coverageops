"""
SQLAlchemy ORM models
"""
from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Float, Boolean,
    DateTime, ForeignKey, Text, Enum
)
from sqlalchemy.orm import relationship
import enum

from app.core.database import Base


class UserRole(str, enum.Enum):
    ADMIN    = "ADMIN"
    OPERATOR = "OPERATOR"
    VIEWER   = "VIEWER"


class User(Base):
    __tablename__ = "users"

    id         = Column(Integer, primary_key=True, index=True)
    username   = Column(String(64), unique=True, index=True, nullable=False)
    full_name  = Column(String(128), nullable=False)
    hashed_pw  = Column(String(256), nullable=False)
    role       = Column(Enum(UserRole), default=UserRole.OPERATOR, nullable=False)
    is_active  = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_login = Column(DateTime, nullable=True)

    sites    = relationship("Site", back_populates="owner")
    activity = relationship("ActivityLog", back_populates="user")


class Site(Base):
    __tablename__ = "sites"

    id          = Column(Integer, primary_key=True, index=True)
    name        = Column(String(128), nullable=False)
    lat         = Column(Float, nullable=False)
    lon         = Column(Float, nullable=False)
    ant_height  = Column(Float, default=6.0)        # meters above ground
    elevation_m = Column(Float, default=0.0, nullable=True)  # meters above sea level (ASL)
    frequency   = Column(Integer, nullable=False)   # MHz
    tx_power    = Column(Float, default=43.0)        # dBm EIRP
    rx_threshold= Column(Float, default=-90.0)      # dBm
    max_radius  = Column(Float, default=350.0)      # km
    notes       = Column(Text, nullable=True)
    owner_id    = Column(Integer, ForeignKey("users.id"))
    created_at  = Column(DateTime, default=datetime.utcnow)
    updated_at  = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    owner   = relationship("User", back_populates="sites")
    results = relationship("CoverageResult", back_populates="site", cascade="all, delete-orphan")


class AnalysisMode(str, enum.Enum):
    DTM = "DTM"   # Terrain only
    DSM = "DSM"   # Terrain + buildings/clutter


class CoverageResult(Base):
    __tablename__ = "coverage_results"

    id           = Column(Integer, primary_key=True, index=True)
    site_id      = Column(Integer, ForeignKey("sites.id"), nullable=False)
    mode         = Column(Enum(AnalysisMode), nullable=False)

    # Bounding box of analysis polygon
    poly_sw_lat  = Column(Float, nullable=False)
    poly_sw_lon  = Column(Float, nullable=False)
    poly_ne_lat  = Column(Float, nullable=False)
    poly_ne_lon  = Column(Float, nullable=False)

    # Aggregate statistics
    covered_pct  = Column(Float)   # % of polygon with signal
    rssi_avg     = Column(Float)
    rssi_max     = Column(Float)
    rssi_min     = Column(Float)

    # GeoJSON result (coverage grid as FeatureCollection)
    geojson_path = Column(String(512), nullable=True)   # path to stored file

    created_at   = Column(DateTime, default=datetime.utcnow)
    duration_sec = Column(Float, nullable=True)

    site = relationship("Site", back_populates="results")


class ActivityLog(Base):
    __tablename__ = "activity_log"

    id         = Column(Integer, primary_key=True, index=True)
    user_id    = Column(Integer, ForeignKey("users.id"))
    action     = Column(String(128))
    detail     = Column(Text, nullable=True)
    ip_address = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="activity")
