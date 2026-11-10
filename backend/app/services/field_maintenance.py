"""
Field reception — maintenance job (spec 9.5): command expiry, aggregation of
dirty buckets, alert evaluation, pending purges (withdraw/delete) and retention.

Every task is idempotent and catches its own errors. Entry points:
  * run_maintenance(db, tasks)           — one pass (CLI, POST /maintenance/run, tests)
  * python -m app.scripts.field_maintenance [--task …]
  * maintenance_loop()                   — asyncio task started from the app lifespan
                                           (FIELD_MAINTENANCE_INTERVAL_S, 0 = off; never under pytest)
Concurrency: a process-wide lock, plus pg_try_advisory_lock on PostgreSQL so
several workers / a cron run never overlap.
"""
import asyncio
import logging
import os
import threading
import time
from datetime import datetime, timedelta
from typing import Iterable, Optional

from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.field import (
    FieldAggHourly, FieldDeviceCommand, FieldHeartbeat, FieldMeasurement,
)
from app.services.field_agg import process_dirty
from app.services.field_alerts import evaluate_device_alerts, expire_commands
from app.services.field_privacy import run_pending_purges

log = logging.getLogger(__name__)

ALL_TASKS = ("commands", "purge", "aggregate", "alerts", "retention")
ADVISORY_LOCK_KEY = 0x46494C44     # "FILD"
_lock = threading.Lock()
_last_retention: Optional[float] = None


def retention(db: Session, now: Optional[datetime] = None) -> dict:
    now = now or datetime.utcnow()
    out = {}
    raw_cut = now - timedelta(days=settings.FIELD_RAW_RETENTION_DAYS)
    n = 0
    oldest = db.execute(select(func.min(FieldMeasurement.ts))).scalar()
    while oldest is not None and oldest < raw_cut:      # day slices — bounded transactions
        upto = min(oldest + timedelta(days=1), raw_cut)
        n += db.execute(delete(FieldMeasurement).where(FieldMeasurement.ts < upto)).rowcount or 0
        db.commit()
        oldest = db.execute(select(func.min(FieldMeasurement.ts))).scalar()
    out["raw"] = n
    out["heartbeats"] = db.execute(delete(FieldHeartbeat).where(
        FieldHeartbeat.received_at < now - timedelta(days=settings.FIELD_HEARTBEAT_RETENTION_DAYS))).rowcount or 0
    out["hourly"] = db.execute(delete(FieldAggHourly).where(
        FieldAggHourly.hour < now - timedelta(days=settings.FIELD_HOURLY_AGG_RETENTION_DAYS))).rowcount or 0
    out["commands"] = db.execute(delete(FieldDeviceCommand).where(
        FieldDeviceCommand.status.notin_(("queued", "delivered")),
        FieldDeviceCommand.issued_at < now - timedelta(days=settings.FIELD_COMMAND_RETENTION_DAYS))).rowcount or 0
    db.commit()
    return out


def _pg_lock(db: Session) -> bool:
    if db.get_bind().dialect.name != "postgresql":
        return True
    return bool(db.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": ADVISORY_LOCK_KEY}).scalar())


def _pg_unlock(db: Session) -> None:
    if db.get_bind().dialect.name == "postgresql":
        try:
            db.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": ADVISORY_LOCK_KEY})
            db.commit()
        except Exception:
            db.rollback()


def run_maintenance(db: Session, tasks: Optional[Iterable[str]] = None,
                    now: Optional[datetime] = None) -> dict:
    tasks = list(tasks or ALL_TASKS)
    unknown = set(tasks) - set(ALL_TASKS)
    if unknown:
        raise ValueError(f"unknown task(s): {sorted(unknown)}")
    result: dict = {}
    if not _lock.acquire(blocking=False):
        return {"skipped": "already running"}
    try:
        if not _pg_lock(db):
            return {"skipped": "locked by another process"}
        try:
            for t in ALL_TASKS:
                if t not in tasks:
                    continue
                try:
                    if t == "commands":
                        result[t] = {"expired": expire_commands(db, now)}
                    elif t == "purge":
                        result[t] = run_pending_purges(db)
                    elif t == "aggregate":
                        total = {"buckets": 0, "hourly": 0, "daily": 0, "coarse": 0}
                        for _ in range(50):                  # drain the queue in slices
                            r = process_dirty(db, now)
                            for k in total:
                                total[k] += r[k]
                            if r["buckets"] == 0 or r["buckets"] < 20000:
                                break
                        result[t] = total
                    elif t == "alerts":
                        result[t] = evaluate_device_alerts(db, now)
                    elif t == "retention":
                        result[t] = retention(db, now)
                except Exception as exc:              # a failing task never stops the others
                    db.rollback()
                    log.exception("field maintenance task %s failed", t)
                    result[t] = {"error": str(exc)[:300]}
        finally:
            _pg_unlock(db)
    finally:
        _lock.release()
    return result


def _run_once_sync() -> dict:
    global _last_retention
    from app.core.database import SessionLocal
    tasks = ["commands", "purge", "aggregate", "alerts"]
    if _last_retention is None or time.monotonic() - _last_retention > 6 * 3600:
        tasks.append("retention")
        _last_retention = time.monotonic()
    db = SessionLocal()
    try:
        return run_maintenance(db, tasks)
    finally:
        db.close()


def loop_enabled() -> bool:
    return settings.FIELD_MAINTENANCE_INTERVAL_S > 0 and "PYTEST_CURRENT_TEST" not in os.environ


async def maintenance_loop(initial_delay_s: float = 10.0) -> None:
    """Background loop for local dev / single-host deployments. Runs the
    blocking work in a thread; never raises out of the loop."""
    interval = max(10, settings.FIELD_MAINTENANCE_INTERVAL_S)
    await asyncio.sleep(initial_delay_s)
    while True:
        try:
            res = await asyncio.to_thread(_run_once_sync)
            log.debug("field maintenance: %s", res)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("field maintenance loop iteration failed")
        await asyncio.sleep(interval)
