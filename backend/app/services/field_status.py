"""
Field reception — live device status (computed, never stored; spec 3.5 + v2/v2.1)
and Hebrew labels for degraded_reasons (closed list owned by the Android team,
ANDROID_APP_SPEC 5.6). Unknown codes are shown raw, never rejected.
"""
from datetime import datetime, timedelta
from typing import Optional

from app.core.config import settings

PAUSED_STATES = ("paused_by_user", "paused_schedule")
WATCHING_STATES = ("active", "degraded")

STATUS_LABELS_HE = {
    "withdrawn": "ההסכמה בוטלה",
    "revoked": "בוטל",
    "deleted": "נמחק",
    "never_seen": "טרם התחבר",
    "offline": "לא מקוון",
    "no_contact": "ללא קשר",
    "paused": "מושהה",
    "degraded": "ניטור מוגבל",
    "active": "פעיל",
}

DEGRADED_REASON_LABELS_HE = {
    "location_permission_missing": "אין הרשאת מיקום",
    "location_approximate_only": "מיקום משוער בלבד — אין נתוני תא",
    "location_services_off": "שירותי המיקום כבויים",
    "phone_state_missing": "אין הרשאת מצב טלפון",
    "notifications_denied": "התראות חסומות",
    "background_location_missing": "אין מיקום ברקע",
    "battery_optimization_not_exempt": "אין פטור מהגבלת סוללה",
    "no_cell_identity": "אין זהות תא",
    "cell_info_frozen": "נתוני תא קפואים",
    "no_location_fix": "אין קליטת מיקום",
    "power_save_location": "חיסכון חשמל מגביל מיקום",
    "low_battery_mode": "סוללה חלשה — קצב מופחת",
    "queue_evicting": "אחסון מלא — דגימות נמחקות",
    "repeated_gaps": "המערכת עוצרת את האפליקציה",
    "upload_blocked": "העלאה חסומה",
}


def degraded_reason_items(codes) -> list:
    return [{"code": c, "label_he": DEGRADED_REASON_LABELS_HE.get(c, f"סיבה לא מוכרת: {c}"),
             "known": c in DEGRADED_REASON_LABELS_HE} for c in (codes or [])]


def last_contact(d) -> Optional[datetime]:
    """Heartbeat time; a device that only uploaded samples counts its last upload."""
    cands = [t for t in (d.last_heartbeat_at, d.last_sample_received_at) if t is not None]
    return max(cands) if cands else None


def compute_status(d, now: Optional[datetime] = None) -> tuple:
    """(status_key, since) — priority order of spec 3.5."""
    now = now or datetime.utcnow()
    if d.status == "withdrawn":
        return "withdrawn", d.withdrawn_at
    if d.status == "revoked":
        return "revoked", d.revoked_at
    if d.status == "deleted":
        return "deleted", d.deleted_at
    seen = last_contact(d)
    if seen is None:
        return "never_seen", d.enrolled_at
    age = now - seen
    state = d.last_monitoring_state or "active"     # samples without heartbeat: assume watching
    if state in WATCHING_STATES and age > timedelta(minutes=settings.FIELD_OFFLINE_MIN):
        return "offline", seen
    if state in PAUSED_STATES:
        if age > timedelta(hours=settings.FIELD_NO_CONTACT_H):
            return "no_contact", d.state_changed_at or seen
        return "paused", d.state_changed_at or seen
    if state == "degraded":
        return "degraded", d.state_changed_at or seen
    return "active", d.state_changed_at or seen


def status_flags(d, cfg: dict, now: Optional[datetime] = None) -> dict:
    """Sub-flags shown next to the status (spec 3.5)."""
    now = now or datetime.utcnow()
    upload = cfg.get("upload_interval_s", 60) if cfg else 60
    pending = (d.last_applied_config_version is not None and d.config_version is not None
               and d.last_applied_config_version < d.config_version
               and d.config_changed_at is not None and now - d.config_changed_at > timedelta(minutes=15))
    key, _ = compute_status(d, now)
    return {
        "config_pending": bool(pending),
        "no_recent_samples": bool(key == "active" and d.last_sample_at is not None and
                                  now - d.last_sample_at > max(timedelta(seconds=3 * upload), timedelta(minutes=10))),
        "upload_lag": bool(d.last_sample_ts_reported and d.last_sample_at and
                           d.last_sample_ts_reported - d.last_sample_at > timedelta(minutes=15)),
        "cell_stale": bool(d.last_tier_stale),
        "coarse": d.last_effective_location_mode == "coarse",
    }
