"""
Field reception — ingest of batches and heartbeats (spec 3.3, contract v2/v2.1).
Used by api/field_ingest.py and by the demo seed, so both store identical data.
"""
import logging
import uuid
from datetime import datetime
from typing import Iterable, Optional

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.models.field import (
    FLAG_COARSE, FLAG_STALE_CELL, FLAG_STALE_LOC, FieldAggDirty, FieldDevice, FieldDeviceCommand,
    FieldHeartbeat, FieldMeasurement,
)
from app.services.field_quality import EnvelopeError, process_sample
from app.services.field_time import floor_hour

log = logging.getLogger(__name__)

UNKNOWN_OPERATOR = "unknown"
_COLS = [c.name for c in FieldMeasurement.__table__.columns]


def _insert_ignore(db: Session, table, rows: list) -> int:
    """INSERT … ON CONFLICT DO NOTHING (PostgreSQL / SQLite) as one executemany —
    the statement is compiled once and cached. Returns the rows inserted
    (the driver's summed rowcount; len(rows) if the driver does not report it)."""
    if not rows:
        return 0
    if db.get_bind().dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    res = db.execute(insert(table).on_conflict_do_nothing(), rows)
    rc = res.rowcount
    return len(rows) if rc is None or rc < 0 else rc


def mark_dirty(db: Session, rows: Iterable[dict], now: datetime) -> None:
    keys = {}
    for r in rows:
        op = r.get("operator_key") or UNKNOWN_OPERATOR
        hour = floor_hour(r["ts"])
        if r.get("gx") is not None:
            keys[("g100", r["gx"], r["gy"], hour, op, r["meas_tech"])] = 1
        if r.get("gx25") is not None:
            keys[("g2500", r["gx25"], r["gy25"], hour, op, r["meas_tech"])] = 1
    _insert_ignore(db, FieldAggDirty.__table__, [
        {"level": k[0], "gx": k[1], "gy": k[2], "hour": k[3], "operator_key": k[4], "tech": k[5],
         "marked_at": now} for k in keys])


def ingest_batch(db: Session, device: FieldDevice, samples: list, sent_at: Optional[datetime],
                 now: Optional[datetime] = None) -> dict:
    """Validate, store and account for a list of raw samples. Raises EnvelopeError.
    Invariant: accepted + duplicates + len(rejected) == len(samples)."""
    now = now or datetime.utcnow()
    processed = [process_sample(s, now, sent_at) for s in samples]   # may raise EnvelopeError
    rejected, valid = [], []
    normalized = 0
    for p in processed:
        if p["reason"]:
            rejected.append({"sample_id": p["sample_id"], "reason": p["reason"]})
        else:
            valid.append(p)
            normalized += 1 if p["normalized"] else 0

    # idempotency: (1) already stored, (2) repeated inside this batch, (3) ON CONFLICT DO NOTHING
    ids = [p["row"]["sample_id"] for p in valid]
    existing = set()
    for i in range(0, len(ids), 400):
        existing.update(db.execute(select(FieldMeasurement.sample_id).where(and_(
            FieldMeasurement.device_pk == device.id,
            FieldMeasurement.sample_id.in_(ids[i:i + 400])))).scalars())
    seen, new_rows = set(), []
    for p in valid:
        sid = p["row"]["sample_id"]
        if sid in existing or sid in seen:
            continue
        seen.add(sid)
        row = dict(p["row"])
        row["device_pk"] = device.id
        new_rows.append({c: row.get(c) for c in _COLS})
    inserted = _insert_ignore(db, FieldMeasurement.__table__, new_rows) if new_rows else 0
    if inserted != len(new_rows):        # lost a race with a concurrent identical batch
        log.info("field ingest: %d rows ignored on conflict", len(new_rows) - inserted)
    accepted = inserted
    duplicates = len(valid) - accepted

    if new_rows:
        mark_dirty(db, new_rows, now)
        _update_last_state(device, new_rows, now)
    if device.token_used_at is None:
        device.token_used_at = now
    db.commit()
    return {"accepted": accepted, "duplicates": duplicates, "rejected": rejected,
            "normalized": normalized, "config_version": device.config_version}


def _update_last_state(d: FieldDevice, rows: list, now: datetime) -> None:
    rows = sorted(rows, key=lambda r: (r["ts"], -(r.get("sim_slot") or 0)))
    newest = rows[-1]
    d.last_sample_received_at = now
    if d.last_sample_at is None or newest["ts"] >= d.last_sample_at:
        prev_last = d.last_sample_at
        d.last_sample_at = newest["ts"]
        d.last_service_state = newest["service_state"]
        d.last_tier_stale = bool(newest["flags"] & FLAG_STALE_CELL)
        if newest.get("battery_pct") is not None and (d.last_heartbeat_at is None or newest["ts"] >= d.last_heartbeat_at):
            d.last_battery_pct = newest["battery_pct"]
        fresh = [r for r in rows if not r["flags"] & FLAG_STALE_CELL
                 and (prev_last is None or r["ts"] >= prev_last)]
        if fresh:
            r = fresh[-1]
            d.last_tier = r["tier"]
            d.last_rsrp_dbm = r["rsrp_dbm"] if r["network_type"] in ("LTE", "NR_NSA") else None
            d.last_nr_ss_rsrp_dbm = (r["rsrp_dbm"] if r["network_type"] == "NR_SA"
                                     else r.get("nr_ss_rsrp_dbm") if r["network_type"] == "NR_NSA" else None)
            d.last_network_type = r["network_type"]
            d.last_operator_key = r.get("operator_key")
            d.last_operator_name = r.get("operator_name")
            d.last_cell = {k: r.get(k) for k in ("cell_id", "pci", "tac", "earfcn_arfcn", "band",
                                                 "timing_advance", "rsrq_db", "sinr_db", "rssi_dbm")}
            d.last_nr_leg = ({"pci": r.get("nr_pci"), "arfcn": r.get("nr_arfcn"), "band": r.get("nr_band"),
                              "ss_rsrp_dbm": r.get("nr_ss_rsrp_dbm"), "ss_rsrq_db": r.get("nr_ss_rsrq_db"),
                              "ss_sinr_db": r.get("nr_ss_sinr_db")}
                             if r["network_type"] == "NR_NSA" else None)
    got_precise = got_coarse = False
    for r in reversed(rows):                      # newest first; one precise + one coarse fix
        if r.get("lat") is None:
            continue
        if r["flags"] & FLAG_COARSE:
            if not got_coarse and (d.last_coarse_ts is None or r["ts"] > d.last_coarse_ts):
                d.last_coarse_lat, d.last_coarse_lon, d.last_coarse_ts = r["lat"], r["lon"], r["ts"]
            got_coarse = True
        elif not r["flags"] & FLAG_STALE_LOC and not got_precise:
            if d.last_loc_ts is None or r["ts"] > d.last_loc_ts:
                d.last_lat, d.last_lon = r["lat"], r["lon"]
                d.last_loc_accuracy_m, d.last_loc_ts = r.get("loc_accuracy_m"), r["ts"]
            got_precise = True
        if got_precise and got_coarse:
            break


# ── heartbeat ─────────────────────────────────────────────────────────────────
def open_commands(db: Session, device: FieldDevice, now: datetime) -> list:
    rows = db.query(FieldDeviceCommand).filter(
        FieldDeviceCommand.device_pk == device.id,
        FieldDeviceCommand.status.in_(("queued", "delivered"))).order_by(FieldDeviceCommand.issued_at).all()
    return [c for c in rows if c.status == "delivered" or c.expires_at > now]


def apply_heartbeat(db: Session, device: FieldDevice, hb: dict, now: Optional[datetime] = None) -> dict:
    """hb: validated heartbeat dict (api schema .model_dump()). Returns the contract response."""
    from app.services.field_time import iso
    now = now or datetime.utcnow()
    d = device
    perms = hb.get("permissions")
    state = hb.get("monitoring_state")
    if state != d.last_monitoring_state:
        d.state_changed_at = now
    d.last_heartbeat_at = now
    d.last_monitoring_state = state
    d.last_battery_pct = hb.get("battery_pct")
    d.last_is_charging = hb.get("is_charging")
    d.last_queue_size = hb.get("queue_size")
    d.last_permissions = perms
    d.last_degraded_reasons = hb.get("degraded_reasons") or []
    d.last_effective_location_mode = hb.get("effective_location_mode")
    d.last_location_services_enabled = hb.get("location_services_enabled")
    if hb.get("applied_config_version") is not None:
        d.last_applied_config_version = hb["applied_config_version"]
    if hb.get("evicted_samples_total") is not None:
        # a decrease (reinstall/reset) is stored as the new baseline; alerts compare heartbeats
        d.last_evicted_samples_total = hb["evicted_samples_total"]
    if hb.get("last_sample_ts") is not None:
        d.last_sample_ts_reported = hb["last_sample_ts"]
    if hb.get("app_version"):
        d.app_version = hb["app_version"][:64]
    if d.token_used_at is None:
        d.token_used_at = now

    acks = hb.get("acked_commands") or []
    ignored = 0
    if acks:
        ids = []
        for a in acks:
            try:
                ids.append(uuid.UUID(str(a["id"])))
            except (ValueError, KeyError, TypeError):
                ignored += 1
        cmds = {c.id: c for c in db.query(FieldDeviceCommand).filter(
            FieldDeviceCommand.id.in_(ids), FieldDeviceCommand.device_pk == d.id).all()} if ids else {}
        for a in acks:
            try:
                cid = uuid.UUID(str(a["id"]))
            except (ValueError, KeyError, TypeError):
                continue
            c = cmds.get(cid)
            if c is None or c.status not in ("queued", "delivered"):
                ignored += 1           # unknown / other device / already final
                continue
            c.status = a["status"]
            c.acked_at = now
            if c.delivered_at is None:
                c.delivered_at = now
    if ignored:
        log.info("field heartbeat: %d ack(s) ignored for device %s", ignored, d.public_id)

    db.add(FieldHeartbeat(
        device_pk=d.id, ts=hb.get("ts") or now, received_at=now, battery_pct=hb.get("battery_pct"),
        is_charging=hb.get("is_charging"), app_version=hb.get("app_version"), queue_size=hb.get("queue_size"),
        permissions=perms, monitoring_state=state, degraded_reasons=hb.get("degraded_reasons"),
        effective_location_mode=hb.get("effective_location_mode"),
        location_services_enabled=hb.get("location_services_enabled"),
        applied_config_version=hb.get("applied_config_version"),
        evicted_samples_total=hb.get("evicted_samples_total"), last_sample_ts=hb.get("last_sample_ts"),
        acked_commands=[{"id": str(a.get("id")), "status": a.get("status")} for a in acks] or None))
    db.flush()

    out = []
    for c in open_commands(db, d, now):
        if c.status == "queued":
            c.status = "delivered"
            c.delivered_at = now
        out.append({"id": str(c.id), "type": c.type, "issued_at": iso(c.issued_at)})
    db.commit()
    return {"config_version": d.config_version, "server_time": iso(now), "commands": out}


__all__ = ["EnvelopeError", "ingest_batch", "apply_heartbeat", "mark_dirty", "open_commands", "UNKNOWN_OPERATOR"]
