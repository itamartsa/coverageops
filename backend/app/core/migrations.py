"""
Startup migrations (additive, idempotent)
─────────────────────────────────────────
`Base.metadata.create_all` creates missing tables but never adds columns to
tables that already exist. This module adds the columns introduced after the
first deployment so that existing databases keep working without Alembic.

Every column is added in its own transaction:
  * PostgreSQL – `ALTER TABLE … ADD COLUMN IF NOT EXISTS …`
  * other dialects (SQLite in tests/dev) – the inspector is consulted first
    and a plain `ALTER TABLE … ADD COLUMN …` is issued only for missing
    columns. Failures are logged, never raised.
"""
import logging
from typing import Iterable, Tuple

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

from app.models.cell_site import CellSite, CellSiteTypeDefault
from app.models.user import CoverageResult, Site

log = logging.getLogger(__name__)

# (table, column name) pairs – the SQL type is taken from the ORM model so the
# DDL always matches the declared Column (BYTEA on PostgreSQL, BLOB on SQLite…).
_ADDITIVE_COLUMNS: Tuple[Tuple[type, str], ...] = (
    (Site,           "elevation_m"),
    (Site,           "ant_height_is_default"),
    (CoverageResult, "rssi_raster"),
    (CoverageResult, "resolution"),
    (CoverageResult, "threshold_dbm"),
    (CoverageResult, "covered_pct_any_signal"),
    (CoverageResult, "weak_pct"),
    (CoverageResult, "dem_source"),
    (CoverageResult, "dem_valid_pct"),
    (CoverageResult, "model"),
    (CoverageResult, "dem_m_per_px"),
    (CoverageResult, "rssi_min_all"),
    (CellSite,       "ant_height_m"),
    (CellSite,       "height_source"),
    (CellSite,       "height_note"),
    (CellSite,       "height_updated_at"),
    (CellSite,       "height_updated_by"),
    (CellSite,       "tx_power_dbm"),
    (CellSite,       "frequency_mhz"),
    (CellSite,       "rf_source"),
    (CellSite,       "tx_power_source"),
    (CellSite,       "frequency_source"),
    (CellSite,       "rf_note"),
    (CellSite,       "rf_updated_at"),
    (CellSite,       "rf_updated_by"),
    (CellSiteTypeDefault, "default_tx_power_dbm"),
    (Site,           "cell_site_id"),
    (Site,           "params_source"),
    (Site,           "source_note"),
    # link_results (app.models.link_result) is a new table created whole by
    # create_all – no additive columns yet.
)


def _existing_columns(engine: Engine, table: str) -> set:
    try:
        return {c["name"] for c in inspect(engine).get_columns(table)}
    except Exception as exc:                      # table missing / no permission
        log.warning("migrations: cannot inspect %s: %s", table, exc)
        return set()


# Indexes created after first deployment (IF NOT EXISTS works on PostgreSQL and SQLite).
_INDEXES: Tuple[str, ...] = (
    "CREATE INDEX IF NOT EXISTS ix_sites_cell_site_id ON sites (cell_site_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_sites_owner_cell_site ON sites (owner_id, cell_site_id) "
    "WHERE cell_site_id IS NOT NULL",
)


def _run(engine: Engine, sql: str) -> bool:
    try:
        with engine.begin() as conn:
            conn.execute(text(sql))
        return True
    except Exception as exc:
        log.warning("migrations: '%s' failed (%s): %s", sql, engine.dialect.name, exc)
        return False


def run_startup_migrations(engine: Engine, columns: Iterable[Tuple[type, str]] = _ADDITIVE_COLUMNS) -> None:
    """Add any missing columns listed in _ADDITIVE_COLUMNS, then the indexes in
    _INDEXES and one-off data fixes. Safe to call repeatedly."""
    is_pg = engine.dialect.name == "postgresql"
    cache: dict = {}
    # inspected up-front (also on PostgreSQL, where ADD COLUMN IF NOT EXISTS
    # cannot tell us whether the column was actually new)
    had_rf_fields = "frequency_source" in _existing_columns(engine, "cell_sites")

    for model, col_name in columns:
        table = model.__tablename__
        col   = model.__table__.columns[col_name]
        if not is_pg:
            if table not in cache:
                cache[table] = _existing_columns(engine, table)
            if col_name in cache[table]:
                continue

        col_type = col.type.compile(dialect=engine.dialect)
        if model is Site and col_name == "elevation_m":
            default = " DEFAULT 0"
        elif model is CellSite and col_name in ("height_source", "rf_source",
                                                   "tx_power_source", "frequency_source"):
            default = " NOT NULL DEFAULT 'estimated'"
        elif model is Site and col_name == "params_source":
            default = " DEFAULT 'manual'"
        elif model is Site and col_name == "cell_site_id":
            default = " REFERENCES cell_sites(id) ON DELETE SET NULL"
        else:
            default = ""
        if_not   = "IF NOT EXISTS " if is_pg else ""
        ddl = f"ALTER TABLE {table} ADD COLUMN {if_not}{col_name} {col_type}{default}"
        if _run(engine, ddl):
            if not is_pg:
                cache[table].add(col_name)

    # Per-field RF provenance replaced the single rf_source: rows that were
    # "manual" before had both fields entered manually.
    if not had_rf_fields and "frequency_source" in _existing_columns(engine, "cell_sites"):
        _run(engine, "UPDATE cell_sites SET tx_power_source = 'manual', frequency_source = 'manual' "
                     "WHERE rf_source = 'manual'")

    for sql in _INDEXES:
        _run(engine, sql)
