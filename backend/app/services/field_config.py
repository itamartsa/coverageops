"""
Field reception — remote configuration (contract "תצורה מרחוק" + v2, spec 3.6)
and idempotent seeds (default group, operator map, default alert rules).

Effective config = contract defaults ⊕ group.config ⊕ device.config_override
(shallow merge). config_version is per device and monotonic.
"""
import re
from datetime import datetime
from typing import Optional

from sqlalchemy import or_, update
from sqlalchemy.orm import Session

from app.models.field import FieldAlertRule, FieldDevice, FieldDeviceGroup, FieldOperatorMap

DEFAULT_CONFIG = {
    "sampling_interval_s": 30,
    "min_distance_m": 25,
    "stationary_interval_s": 300,
    "upload_interval_s": 60,
    "upload_wifi_only": False,
    "active_test_interval_s": 0,
    "collect_neighbors": True,
    "location_mode": "precise",
    "schedule": None,
    "low_battery_threshold_pct": 15,
    "allow_user_pause": True,
}

_INT_RANGES = {
    "sampling_interval_s": (5, 3600),
    "min_distance_m": (0, 1000),
    "stationary_interval_s": (30, 3600),
    "upload_interval_s": (15, 3600),
    "low_battery_threshold_pct": (5, 50),
}
_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")

DEFAULT_GROUP_NAME = "כללי"


def validate_config(cfg, field: str = "config") -> dict:
    """Validate a (partial) config dict. Returns a cleaned copy; raises
    ValueError with a Hebrew message (→ 422)."""
    if cfg is None:
        return {}
    if not isinstance(cfg, dict):
        raise ValueError(f"{field}: נדרש אובייקט")
    out = {}
    for k, v in cfg.items():
        if k == "config_version":
            continue                           # server-managed
        if k not in DEFAULT_CONFIG:
            raise ValueError(f"{field}.{k}: שדה תצורה לא מוכר")
        if k in _INT_RANGES:
            lo, hi = _INT_RANGES[k]
            if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
                raise ValueError(f"{field}.{k}: נדרש מספר שלם בטווח {lo}–{hi}")
        elif k == "active_test_interval_s":
            if isinstance(v, bool) or not isinstance(v, int) or not (v == 0 or 300 <= v <= 86400):
                raise ValueError(f"{field}.{k}: 0 (כבוי) או 300–86400")
        elif k in ("upload_wifi_only", "collect_neighbors", "allow_user_pause"):
            if not isinstance(v, bool):
                raise ValueError(f"{field}.{k}: נדרש ערך בוליאני")
        elif k == "location_mode":
            if v not in ("precise", "coarse", "off"):
                raise ValueError(f"{field}.{k}: precise | coarse | off")
        elif k == "schedule":
            v = validate_schedule(v, f"{field}.schedule")
        out[k] = v
    return out


def validate_schedule(v, field: str = "schedule"):
    if v is None:
        return None
    if not isinstance(v, list):
        raise ValueError(f"{field}: נדרשת רשימה")
    if len(v) > 14:
        raise ValueError(f"{field}: לכל היותר 14 חלונות")
    out = []
    for i, w in enumerate(v):
        if not isinstance(w, dict) or set(w) - {"days", "start", "end"}:
            raise ValueError(f"{field}[{i}]: נדרש {{days, start, end}}")
        days, start, end = w.get("days"), w.get("start"), w.get("end")
        if (not isinstance(days, list) or not days or len(set(days)) != len(days)
                or any(isinstance(d, bool) or not isinstance(d, int) or not 0 <= d <= 6 for d in days)):
            raise ValueError(f"{field}[{i}].days: רשימה לא ריקה של ימים 0–6 ללא כפילויות (0=ראשון)")
        if not isinstance(start, str) or not _HHMM.match(start) or not isinstance(end, str) or not _HHMM.match(end):
            raise ValueError(f"{field}[{i}]: start/end בפורמט HH:MM")
        if start == end:
            raise ValueError(f"{field}[{i}]: start ו-end זהים — חלון לא חד-משמעי")
        out.append({"days": days, "start": start, "end": end})
    return out


def default_group(db: Session) -> Optional[FieldDeviceGroup]:
    return db.query(FieldDeviceGroup).filter(FieldDeviceGroup.is_default.is_(True)).first()


def device_group(db: Session, device: FieldDevice) -> Optional[FieldDeviceGroup]:
    if device.group_id is not None:
        g = db.get(FieldDeviceGroup, device.group_id)
        if g is not None:
            return g
    return default_group(db)


def effective_config(db: Session, device: FieldDevice) -> dict:
    cfg = dict(DEFAULT_CONFIG)
    g = device_group(db, device)
    if g is not None and isinstance(g.config, dict):
        cfg.update({k: v for k, v in g.config.items() if k in DEFAULT_CONFIG})
    if isinstance(device.config_override, dict):
        cfg.update({k: v for k, v in device.config_override.items() if k in DEFAULT_CONFIG})
    if device.ownership == "PERSONAL":
        cfg["allow_user_pause"] = True        # v2: always true for PERSONAL
    cfg["config_version"] = device.config_version
    return cfg


def bump_device(device: FieldDevice, now: Optional[datetime] = None) -> None:
    device.config_version = (device.config_version or 1) + 1
    device.config_changed_at = now or datetime.utcnow()


def bump_group_devices(db: Session, group: FieldDeviceGroup, now: Optional[datetime] = None) -> int:
    now = now or datetime.utcnow()
    cond = FieldDevice.group_id == group.id
    if group.is_default:
        cond = or_(cond, FieldDevice.group_id.is_(None))
    res = db.execute(update(FieldDevice).where(cond).values(
        config_version=FieldDevice.config_version + 1, config_changed_at=now))
    return res.rowcount or 0


# ── seeds (idempotent; never overwrite admin edits) ──────────────────────────
OPERATOR_SEED = (
    ("425-01", "פרטנר", "PHI", None),
    ("425-02", "סלקום", "CELLCOM", None),
    ("425-03", "פלאפון", "PELEPHONE", None),
    ("425-07", "הוט מובייל", "PHI", None),
    ("425-08", "גולן טלקום", "OTHER", "לא ודאי — לאמת שיוך רשת"),
)

# type: (severity, params) — spec 7.1 defaults
ALERT_RULE_SEED = (
    ("device_offline", "warning", {"minutes": 15}),
    ("no_contact", "info", {"hours": 6}),
    ("low_battery", "warning", {"critical_pct": 5, "hysteresis_pct": 5}),
    ("monitoring_degraded", "warning", {}),
    ("monitoring_paused", "warning", {}),
    ("data_loss", "warning", {"quiet_minutes": 60}),
    ("config_pending", "info", {"minutes": 30}),
    ("command_expired", "info", {}),
    ("dead_zone_entered", "warning", {"samples": 3, "min_span_s": 60}),
)


def seed_field_defaults(db: Session) -> None:
    if default_group(db) is None:
        existing = db.query(FieldDeviceGroup).filter(FieldDeviceGroup.name == DEFAULT_GROUP_NAME).first()
        if existing is not None:
            existing.is_default = True
        else:
            db.add(FieldDeviceGroup(name=DEFAULT_GROUP_NAME, description="קבוצת ברירת המחדל",
                                    config={}, is_default=True))
    have = {r.operator_key for r in db.query(FieldOperatorMap.operator_key).all()}
    for key, name, op, note in OPERATOR_SEED:
        if key not in have:
            db.add(FieldOperatorMap(operator_key=key, name_he=name, cell_sites_operator=op, note=note))
    have_rules = {r.type for r in db.query(FieldAlertRule.type).all()}
    for t, sev, params in ALERT_RULE_SEED:
        if t not in have_rules:
            db.add(FieldAlertRule(type=t, scope="all", params=params, severity=sev, enabled=True))
    db.commit()
