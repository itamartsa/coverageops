"""
Field reception — device data removal (spec 9.6, contract v2.1 rule 5).

purge_device() is run by the maintenance job for devices whose status is
withdrawn/deleted with a pending purge_mode:
  * delete    — raw samples, heartbeats, commands and alerts of the device are
                deleted (in chunks); the device row stays as a tombstone with
                its identifying fields cleared.
  * anonymize — raw samples are kept until their 90-day retention, moved to a
                shared per-day "anonymous" sink device, lat/lon snapped to the
                centre of their G100 cell, ts truncated to the hour, a new
                random sample_id, flag 8; heartbeats/commands/alerts deleted.
Aggregates (field_agg_*) are kept (counts; device_set holds an internal pk that
is no longer linked to a person).
"""
import logging
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.core.audit import log_activity
from app.models.field import (
    FLAG_ANONYMIZED, FieldAlert, FieldDevice, FieldDeviceCommand, FieldHeartbeat, FieldMeasurement,
)
from app.services.field_quality import G100_DLAT, G100_DLON, g100

log = logging.getLogger(__name__)
CHUNK = 10_000


def _clear_identity(d: FieldDevice) -> None:
    d.label = None
    d.model = d.manufacturer = d.os_version = d.app_version = None
    d.device_uuid = uuid.uuid4()
    d.consent_version = None
    d.consented_at = None
    d.token_hash = None
    d.notes = None
    d.config_override = None
    for col in FieldDevice.__table__.columns:
        if col.name.startswith("last_"):
            setattr(d, col.name, None)
    d.state_changed_at = None


def anon_sink(db: Session, day) -> FieldDevice:
    """Shared tombstone device that owns anonymised rows of one UTC day."""
    key = uuid.uuid5(uuid.NAMESPACE_URL, f"coverageops-field-anon-sink:{day.isoformat()}")
    d = db.query(FieldDevice).filter(FieldDevice.device_uuid == key).first()
    if d is None:
        d = FieldDevice(public_id=uuid.uuid4(), device_uuid=key, ownership="ORG", status="deleted",
                        is_anon_sink=True, label=None, config_version=1, deleted_at=datetime.utcnow(),
                        purge_mode="anonymize", purged_at=datetime.utcnow())
        db.add(d)
        db.flush()
    return d


def _delete_rows(db: Session, device_pk: int) -> int:
    total = 0
    while True:
        keys = db.execute(select(FieldMeasurement.ts, FieldMeasurement.sample_id).where(
            FieldMeasurement.device_pk == device_pk).limit(CHUNK)).all()
        if not keys:
            break
        for i in range(0, len(keys), 500):
            part = keys[i:i + 500]
            db.execute(delete(FieldMeasurement).where(
                FieldMeasurement.device_pk == device_pk,
                FieldMeasurement.sample_id.in_([k.sample_id for k in part])))
        total += len(keys)
        db.commit()
    return total


def _anonymize_rows(db: Session, device_pk: int) -> int:
    total = 0
    sinks: dict = {}
    while True:
        rows = db.execute(select(FieldMeasurement).where(
            FieldMeasurement.device_pk == device_pk).limit(2000)).scalars().all()
        if not rows:
            break
        for r in rows:
            day = r.ts.date()
            if day not in sinks:
                sinks[day] = anon_sink(db, day).id
            new = {c.name: getattr(r, c.name) for c in FieldMeasurement.__table__.columns}
            new["device_pk"] = sinks[day]
            new["ts"] = r.ts.replace(minute=0, second=0, microsecond=0)
            new["sample_id"] = uuid.uuid4()
            if r.lat is not None and r.lon is not None:
                gx, gy = g100(r.lat, r.lon)
                new["lat"] = round((gy + 0.5) * G100_DLAT, 6)
                new["lon"] = round((gx + 0.5) * G100_DLON, 6)
            new["altitude_m"] = new["altitude_msl_m"] = new["speed_mps"] = new["bearing_deg"] = None
            new["flags"] = (r.flags or 0) | FLAG_ANONYMIZED
            db.delete(r)
            db.flush()
            db.add(FieldMeasurement(**new))
        total += len(rows)
        db.commit()
    return total


def purge_device(db: Session, device: FieldDevice, mode: str, actor_user_id: Optional[int] = None) -> int:
    """Executes the data part of a delete/anonymise. Idempotent."""
    if mode == "delete":
        rows = _delete_rows(db, device.id)
    elif mode == "anonymize":
        rows = _anonymize_rows(db, device.id)
    else:
        raise ValueError(mode)
    db.execute(delete(FieldHeartbeat).where(FieldHeartbeat.device_pk == device.id))
    db.execute(delete(FieldDeviceCommand).where(FieldDeviceCommand.device_pk == device.id))
    db.execute(delete(FieldAlert).where(FieldAlert.device_pk == device.id))
    _clear_identity(device)
    device.purged_at = datetime.utcnow()
    log_activity(db, actor_user_id, "FIELD_PURGE_DONE",
                 {"device_id": str(device.public_id), "mode": mode, "rows": rows}, commit=False)
    db.commit()
    return rows


def run_pending_purges(db: Session) -> list:
    pending = db.query(FieldDevice).filter(
        FieldDevice.status.in_(("withdrawn", "deleted")), FieldDevice.purge_mode.isnot(None),
        FieldDevice.purged_at.is_(None), FieldDevice.is_anon_sink.is_(False)).all()
    done = []
    for d in pending:
        try:
            rows = purge_device(db, d, d.purge_mode)
            done.append({"device_id": str(d.public_id), "mode": d.purge_mode, "rows": rows})
        except Exception as exc:          # one device must not block the others
            db.rollback()
            log.warning("field purge failed for device %s: %s", d.id, exc)
    return done


def cancel_open_commands(db: Session, device_pk: int) -> int:
    res = db.execute(update(FieldDeviceCommand).where(
        FieldDeviceCommand.device_pk == device_pk,
        FieldDeviceCommand.status.in_(("queued", "delivered"))).values(status="cancelled"))
    return res.rowcount or 0
