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

from app.models.user import CoverageResult, Site

log = logging.getLogger(__name__)

# (table, column name) pairs – the SQL type is taken from the ORM model so the
# DDL always matches the declared Column (BYTEA on PostgreSQL, BLOB on SQLite…).
_ADDITIVE_COLUMNS: Tuple[Tuple[type, str], ...] = (
    (Site,           "elevation_m"),
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
)


def _existing_columns(engine: Engine, table: str) -> set:
    try:
        return {c["name"] for c in inspect(engine).get_columns(table)}
    except Exception as exc:                      # table missing / no permission
        log.warning("migrations: cannot inspect %s: %s", table, exc)
        return set()


def run_startup_migrations(engine: Engine, columns: Iterable[Tuple[type, str]] = _ADDITIVE_COLUMNS) -> None:
    """Add any missing columns listed in _ADDITIVE_COLUMNS. Safe to call repeatedly."""
    is_pg = engine.dialect.name == "postgresql"
    cache: dict = {}

    for model, col_name in columns:
        table = model.__tablename__
        col   = model.__table__.columns[col_name]
        if not is_pg:
            if table not in cache:
                cache[table] = _existing_columns(engine, table)
            if col_name in cache[table]:
                continue

        col_type = col.type.compile(dialect=engine.dialect)
        default  = " DEFAULT 0" if (model is Site and col_name == "elevation_m") else ""
        if_not   = "IF NOT EXISTS " if is_pg else ""
        ddl = f"ALTER TABLE {table} ADD COLUMN {if_not}{col_name} {col_type}{default}"
        try:
            with engine.begin() as conn:
                conn.execute(text(ddl))
            if not is_pg:
                cache[table].add(col_name)
        except Exception as exc:
            log.warning("migrations: '%s' failed (%s): %s", ddl, engine.dialect.name, exc)
