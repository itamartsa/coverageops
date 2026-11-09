"""
Civil cellular sites in Israel — imported from data.gov.il
(Ministry of Environmental Protection, datasets `antennaactive` + `antenna_hakama`).
"""
from datetime import datetime
from sqlalchemy import Column, Integer, String, Float, DateTime, Text

from app.core.database import Base


class CellSite(Base):
    __tablename__ = "cell_sites"

    id            = Column(Integer, primary_key=True, index=True)
    status        = Column(String(16), index=True, nullable=False)   # active | construction
    operator      = Column(String(16), index=True, nullable=False)   # PHI | PELEPHONE | CELLCOM | OTHER
    operator_he   = Column(String(64))
    site_code     = Column(String(32), index=True)                   # operator's site number
    city          = Column(String(128))
    address       = Column(String(256))
    authority     = Column(String(128))
    jurisdiction  = Column(String(128))
    site_type     = Column(String(32))                               # normalized (ground_mast, rooftop_mast, ...)
    site_type_he  = Column(String(64))
    technologies  = Column(String(32))                               # e.g. "2G,3G,4G,5G"
    lat           = Column(Float, nullable=False, index=True)
    lon           = Column(Float, nullable=False, index=True)
    x_itm         = Column(Float)
    y_itm         = Column(Float)
    # active sites
    construction_permit_date = Column(String(10))
    operation_permit_date    = Column(String(10))
    last_periodic_check      = Column(String(10))
    radiation_permit         = Column(String(32))
    max_power_uw_cm2         = Column(Float)
    max_pct_of_threshold     = Column(Float)
    max_point_desc           = Column(Text)
    construction_permit_url  = Column(String(256))
    operation_permit_url     = Column(String(256))
    # construction (permit requests)
    request_date  = Column(String(10))
    permit_type   = Column(String(64))
    reference_no  = Column(String(32))
    source_id     = Column(Integer)                                  # ID in the source dataset
    # antenna height — NOT in the government dataset. Estimated by site type
    # (see CellSiteTypeDefault) unless a user entered the real value.
    ant_height_m      = Column(Float, nullable=True)                 # metres AGL
    height_source     = Column(String(16), nullable=False, default="estimated",
                               server_default="estimated")           # estimated | manual
    height_note       = Column(String(256), nullable=True)
    height_updated_at = Column(DateTime, nullable=True)
    height_updated_by = Column(String(64), nullable=True)


class CellSiteTypeDefault(Base):
    """Estimated antenna height per site type (admin-editable)."""
    __tablename__ = "cell_site_type_defaults"

    site_type        = Column(String(32), primary_key=True)
    default_height_m = Column(Float, nullable=False)
    label_he         = Column(String(64))
    note_he          = Column(String(256))


class CellSiteSync(Base):
    """One row per import run — powers the 'last updated' info in the UI."""
    __tablename__ = "cell_sites_sync"

    id            = Column(Integer, primary_key=True)
    synced_at     = Column(DateTime, default=datetime.utcnow)
    source        = Column(String(32))       # live | snapshot
    data_fetched_at = Column(String(32))     # when the data was pulled from data.gov.il
    active_count  = Column(Integer)
    construction_count = Column(Integer)
    skipped_count = Column(Integer)
    note          = Column(Text)
