"""
SQLAlchemy ORM models
"""
from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Float, Boolean,
    DateTime, ForeignKey, Text, Enum, LargeBinary, Index, text
)
from sqlalchemy.orm import deferred, relationship
import enum

from app.core.database import Base
from app.models import cell_site  # noqa: F401  (sites.cell_site_id -> cell_sites.id)


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
    ant_height  = Column(Float, default=10.0)       # meters above ground (AGL)
    # True while ant_height is the system default (10 m) and was never set by a user.
    # NULL on rows created before this column existed = treated as False.
    ant_height_is_default = Column(Boolean, nullable=True, default=False)
    elevation_m = Column(Float, nullable=True)  # meters ASL; NULL = unknown (DEM unavailable)
    frequency   = Column(Integer, nullable=False)   # MHz
    tx_power    = Column(Float, default=43.0)        # dBm EIRP
    rx_threshold= Column(Float, default=-90.0)      # dBm
    max_radius  = Column(Float, default=350.0)      # km
    notes       = Column(Text, nullable=True)
    # Provenance: set when the site was promoted from a civil cell site
    cell_site_id  = Column(Integer, ForeignKey("cell_sites.id", ondelete="SET NULL"),
                           nullable=True, index=True)
    params_source = Column(String(16), nullable=True, default="manual",
                           server_default="manual")      # manual | estimated
    source_note   = Column(String(256), nullable=True)
    owner_id    = Column(Integer, ForeignKey("users.id"))
    created_at  = Column(DateTime, default=datetime.utcnow)
    updated_at  = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    owner   = relationship("User", back_populates="sites")
    results = relationship("CoverageResult", back_populates="site", cascade="all, delete-orphan")

    # One promoted Site per (owner, cell site); NULL cell_site_id = unconstrained.
    __table_args__ = (
        Index("uq_sites_owner_cell_site", "owner_id", "cell_site_id", unique=True,
              postgresql_where=text("cell_site_id IS NOT NULL"),
              sqlite_where=text("cell_site_id IS NOT NULL")),
    )


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
    # covered_pct: % of in-radius cells with RSSI >= threshold_dbm (rx_threshold).
    # Legacy rows (threshold_dbm IS NULL) stored the "any signal" share instead.
    covered_pct  = Column(Float)
    rssi_avg     = Column(Float)
    rssi_max     = Column(Float)
    rssi_min     = Column(Float)                             # over cells > -110 dBm
    rssi_min_all = Column(Float, nullable=True)              # 5th-percentile RSSI over ALL in-radius cells (risk input)
    covered_pct_any_signal = Column(Float, nullable=True)   # % cells > -110 dBm
    weak_pct     = Column(Float, nullable=True)              # % weak + marginal
    threshold_dbm = Column(Float, nullable=True)             # rx_threshold used

    # RSSI raster (coverage_engine.encode_rssi_grid; row 0 = north, NaN = outside radius)
    # deferred: listings never load the blob (use rssi_raster.isnot(None) in SQL)
    rssi_raster  = deferred(Column(LargeBinary, nullable=True))
    resolution   = Column(Integer, nullable=True)            # cells per axis

    # Data quality / provenance
    dem_source    = Column(String(32), nullable=True)        # "terrarium" | "none"
    dem_valid_pct = Column(Float, nullable=True)
    dem_m_per_px  = Column(Float, nullable=True)             # native DEM resolution
    model         = Column(String(128), nullable=True)       # propagation model label

    # Legacy: GeoJSON FeatureCollection on disk (rows created before raster storage)
    geojson_path = Column(String(512), nullable=True)

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
