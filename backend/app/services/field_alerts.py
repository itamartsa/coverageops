"""
Field reception — alert evaluation (spec 7, MVP subset).

Implemented types: device_offline, no_contact, low_battery, monitoring_degraded,
monitoring_paused (ORG only), data_loss, config_pending, command_expired,
dead_zone_entered. All are evaluated by the maintenance job (every run, default
60 s), not inside the ingest request. One open alert per dedup_key (partial
unique index); a resolved key is not reopened for 10 minutes (cooldown); a
reopened alert links details.previous_id and carries occurrences + 1.
"""
import logging
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.field import (
    FLAG_STALE_CELL, FieldAlert, FieldAlertRule, FieldDevice, FieldDeviceCommand, FieldHeartbeat,
    FieldMeasurement,
)
from app.services.field_config import effective_config
from app.services.field_status import compute_status
from app.services.field_time import iso

log = logging.getLogger(__name__)

COOLDOWN = timedelta(minutes=10)
ORG_CRITICAL_REASONS = {"location_permission_missing", "location_approximate_only", "location_services_off",
                        "background_location_missing", "phone_state_missing"}

TYPE_LABELS_HE = {
    "device_offline": "מכשיר לא מקוון",
    "no_contact": "ללא קשר",
    "low_battery": "סוללה חלשה",
    "monitoring_degraded": "ניטור מוגבל",
    "monitoring_paused": "ניטור הושהה (ORG)",
    "data_loss": "אובדן נתונים במכשיר",
    "config_pending": "תצורה ממתינה",
    "command_expired": "פקודה פגה",
    "dead_zone_entered": "כניסה לאזור מת",
}


def _name(d: FieldDevice) -> str:
    return d.label or f"מכשיר {str(d.public_id)[:8]}"


class AlertWriter:
    def __init__(self, db: Session, now: datetime):
        self.db = db
        self.now = now
        self.rules = {r.type: r for r in db.query(FieldAlertRule).filter(FieldAlertRule.scope == "all").all()}
        self.open = {a.dedup_key: a for a in db.query(FieldAlert).filter(FieldAlert.status != "resolved").all()}
        self.opened = self.resolved = 0

    def rule(self, t: str) -> Optional[FieldAlertRule]:
        r = self.rules.get(t)
        return r if r is not None and r.enabled else None

    def raise_(self, t: str, key: str, message: str, severity: Optional[str] = None,
               device: Optional[FieldDevice] = None, details: Optional[dict] = None,
               resolve_now: bool = False) -> Optional[FieldAlert]:
        rule = self.rule(t)
        if rule is None:
            return None
        sev = severity or rule.severity
        a = self.open.get(key)
        if a is not None:
            a.last_seen_at = self.now
            if a.severity != sev:
                a.severity = sev
                a.message_he = message[:256]
            if details:
                a.details = {**(a.details or {}), **details}
            return a
        prev = self.db.query(FieldAlert).filter(FieldAlert.dedup_key == key, FieldAlert.status == "resolved") \
            .order_by(FieldAlert.resolved_at.desc()).first()
        if prev is not None and prev.resolved_at and self.now - prev.resolved_at < COOLDOWN and not resolve_now:
            return None
        det = dict(details or {})
        if prev is not None:
            det["previous_id"] = prev.id
        a = FieldAlert(rule_id=rule.id, type=t, dedup_key=key[:96], device_pk=device.id if device else None,
                       status="resolved" if resolve_now else "open", severity=sev, message_he=message[:256],
                       details=det, opened_at=self.now, last_seen_at=self.now,
                       resolved_at=self.now if resolve_now else None,
                       occurrences=(prev.occurrences + 1) if prev is not None else 1)
        self.db.add(a)
        self.db.flush()
        if not resolve_now:
            self.open[key] = a
        self.opened += 1
        return a

    def resolve(self, key: str) -> None:
        a = self.open.pop(key, None)
        if a is not None:
            a.status = "resolved"
            a.resolved_at = self.now
            self.resolved += 1

    def resolve_prefix(self, prefix: str) -> None:
        for k in [k for k in self.open if k.startswith(prefix)]:
            self.resolve(k)


def _data_loss(db: Session, d: FieldDevice, now: datetime, quiet_min: int) -> int:
    since = now - timedelta(minutes=quiet_min)
    rows = db.execute(select(FieldHeartbeat.evicted_samples_total).where(
        FieldHeartbeat.device_pk == d.id, FieldHeartbeat.received_at >= since,
        FieldHeartbeat.evicted_samples_total.isnot(None)).order_by(FieldHeartbeat.received_at)).scalars().all()
    base = db.execute(select(FieldHeartbeat.evicted_samples_total).where(
        FieldHeartbeat.device_pk == d.id, FieldHeartbeat.received_at < since,
        FieldHeartbeat.evicted_samples_total.isnot(None)).order_by(FieldHeartbeat.received_at.desc())
        .limit(1)).scalars().first()
    seq = ([base] if base is not None else []) + list(rows)
    return sum(max(0, b - a) for a, b in zip(seq, seq[1:]))   # a decrease = new baseline


def _last_fresh_tiers(db: Session, d: FieldDevice, n: int) -> list:
    rows = db.execute(select(FieldMeasurement.ts, FieldMeasurement.tier, FieldMeasurement.flags).where(
        FieldMeasurement.device_pk == d.id).order_by(FieldMeasurement.ts.desc()).limit(n * 4)).all()
    fresh = [r for r in rows if not r.flags & FLAG_STALE_CELL]
    return fresh[:n]


def evaluate_device_alerts(db: Session, now: Optional[datetime] = None) -> dict:
    now = now or datetime.utcnow()
    w = AlertWriter(db, now)
    devices = db.query(FieldDevice).filter(FieldDevice.is_anon_sink.is_(False)).all()
    for d in devices:
        p = f"device:{d.id}"
        if d.status != "active":
            for k in [k for k in w.open if k.endswith(f":{p}")]:
                w.resolve(k)
            continue
        status, since = compute_status(d, now)
        name = _name(d)
        cfg = effective_config(db, d)

        k = f"device_offline:{p}"
        if status == "offline" and w.rule("device_offline"):
            w.raise_("device_offline", k, f"{name}: לא מקוון · נראה לאחרונה {iso(since)}",
                     "warning" if d.ownership == "ORG" else "info", d, {"last_seen": iso(since)})
        else:
            w.resolve(k)

        k = f"no_contact:{p}"
        if status == "no_contact" and w.rule("no_contact"):
            w.raise_("no_contact", k, f"{name}: ללא קשר · מושהה מאז {iso(since)}", "info", d)
        else:
            w.resolve(k)

        k = f"low_battery:{p}"
        thr = cfg.get("low_battery_threshold_pct", 15)
        b = d.last_battery_pct
        if b is not None and status not in ("offline", "no_contact", "never_seen") and not d.last_is_charging \
                and b <= thr and w.rule("low_battery"):
            crit = (w.rule("low_battery").params or {}).get("critical_pct", 5)
            w.raise_("low_battery", k, f"{name}: סוללה {b}%", "critical" if b <= crit else "warning", d,
                     {"battery_pct": b})
        elif b is None or d.last_is_charging or b >= thr + 5 or status in ("offline", "no_contact"):
            w.resolve(k)

        k = f"monitoring_degraded:{p}"
        if d.last_monitoring_state == "degraded" and status == "degraded" and w.rule("monitoring_degraded"):
            reasons = set(d.last_degraded_reasons or [])
            sev = ("info" if d.ownership == "PERSONAL"
                   else "critical" if reasons & ORG_CRITICAL_REASONS else "warning")
            w.raise_("monitoring_degraded", k, f"{name}: ניטור מוגבל ({', '.join(sorted(reasons)) or '—'})",
                     sev, d, {"degraded_reasons": sorted(reasons)})
        else:
            w.resolve(k)

        k = f"monitoring_paused:{p}"
        if d.ownership == "ORG" and d.last_monitoring_state == "paused_by_user" and w.rule("monitoring_paused"):
            w.raise_("monitoring_paused", k, f"{name}: הניטור הושהה ע\"י המשתמש", None, d)
        elif d.last_monitoring_state in ("active", "degraded") or d.ownership != "ORG":
            w.resolve(k)

        k = f"data_loss:{p}"
        r = w.rule("data_loss")
        if r:
            lost = _data_loss(db, d, now, (r.params or {}).get("quiet_minutes", 60))
            if lost > 0:
                w.raise_("data_loss", k, f"{name}: {lost} דגימות נמחקו במכשיר (אחסון מלא)", None, d,
                         {"evicted": lost})
            else:
                w.resolve(k)
        else:
            w.resolve(k)

        k = f"config_pending:{p}"
        r = w.rule("config_pending")
        pend = (d.last_applied_config_version is not None and d.last_applied_config_version < d.config_version
                and d.config_changed_at is not None
                and now - d.config_changed_at > timedelta(minutes=(r.params or {}).get("minutes", 30) if r else 30)
                and status in ("active", "degraded"))
        if pend and r:
            w.raise_("config_pending", k, f"{name}: תצורה v{d.config_version} טרם הוחלה "
                     f"(הוחלה v{d.last_applied_config_version})", None, d)
        elif not pend:
            w.resolve(k)

        k = f"dead_zone_entered:{p}"
        r = w.rule("dead_zone_entered")
        if r and status in ("active", "degraded"):
            n = (r.params or {}).get("samples", 3)
            last = _last_fresh_tiers(db, d, n)
            if len(last) == n and all(x.tier == 0 for x in last) and \
                    (last[0].ts - last[-1].ts).total_seconds() >= (r.params or {}).get("min_span_s", 60):
                w.raise_("dead_zone_entered", k, f"{name}: נכנס לאזור ללא קליטה", None, d,
                         {"since": iso(last[-1].ts)})
            elif len(last) == n and all(x.tier is not None and x.tier >= 1 for x in last):
                w.resolve(k)
        else:
            w.resolve(k)
    db.commit()
    return {"opened": w.opened, "resolved": w.resolved}


def expire_commands(db: Session, now: Optional[datetime] = None) -> int:
    """queued past expires_at → expired; delivered and unacknowledged for 24 h → expired."""
    now = now or datetime.utcnow()
    rows = db.query(FieldDeviceCommand).filter(FieldDeviceCommand.status.in_(("queued", "delivered"))).all()
    w = AlertWriter(db, now)
    n = 0
    for c in rows:
        if (c.status == "queued" and c.expires_at <= now) or \
                (c.status == "delivered" and c.delivered_at and now - c.delivered_at > timedelta(hours=24)):
            c.status = "expired"
            n += 1
            d = db.get(FieldDevice, c.device_pk)
            w.raise_("command_expired", f"command_expired:{c.id}",
                     f"{_name(d) if d else 'מכשיר'}: פקודה {c.type} פגה ללא ביצוע", None, d,
                     {"command_id": str(c.id), "type": c.type}, resolve_now=True)
    db.commit()
    return n
