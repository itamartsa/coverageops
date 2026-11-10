"""
/api/field — field-reception dashboard API (user JWT; spec 3.4, contract v2/v2.1).

Roles: A = ADMIN, O = OPERATOR, V = VIEWER. VIEWER gets aggregates only
(spec 9.4): no device endpoints, alerts, exports of measurements or audit
(403 server-side); spatial cells only with ≥ FIELD_VIEWER_K_MIN distinct
devices; day resolution; never n_devices or any device identity.

Static paths are declared before /devices/{device_id} (TECHNICAL.md convention).
M4–M6 endpoints (compare/calibration/predict/device-health) do not exist yet.
"""
import csv
import io
import json
import secrets
import urllib.parse
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import and_, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import ratelimit
from app.core.audit import log_activity
from app.core.config import settings
from app.core.database import get_db
from app.core.device_auth import FieldHTTPError, hash_secret
from app.core.security import get_current_user, require_role
from app.models.field import (
    FLAG_COARSE, FLAG_STALE_CELL, FieldAlert, FieldAlertRule, FieldDevice, FieldDeviceCommand,
    FieldDeviceGroup, FieldEnrollmentCode, FieldHeartbeat, FieldMeasurement, FieldOperatorMap,
)
from app.models.user import ActivityLog, User, UserRole
from app.services import field_agg, field_kpi
from app.services.field_alerts import TYPE_LABELS_HE
from app.services.field_config import (
    DEFAULT_CONFIG, bump_device, bump_group_devices, default_group, effective_config, validate_config,
)
from app.services.field_maintenance import ALL_TASKS, run_maintenance
from app.services.field_privacy import cancel_open_commands
from app.services.field_quality import (
    G100_DLAT, G100_DLON, G2500_DLAT, G2500_DLON, OOS_STATES, TIERS, UNCLASSIFIED,
)
from app.services.field_requirements import DEVICE_REQUIREMENTS
from app.services.field_status import (
    PAUSED_STATES, STATUS_LABELS_HE, compute_status, degraded_reason_items, status_flags,
)
from app.services.field_time import floor_hour, il_day, il_day_bounds, iso, parse_iso

router = APIRouter()

AO = require_role(UserRole.ADMIN, UserRole.OPERATOR)
A = require_role(UserRole.ADMIN)
ANY = get_current_user

CODE_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
MAX_HEAT_CELLS = 20_000
MAX_EXPORT_ROWS = 2_000_000
TECH_LABELS_HE = {"LTE": "LTE (כולל עוגן NSA)", "NR": "5G NR SA", "LEGACY": "2G/3G", "NONE": "ללא שירות"}


# ── helpers ───────────────────────────────────────────────────────────────────
def _is_viewer(u: User) -> bool:
    return u.role == UserRole.VIEWER


def _forbid() -> HTTPException:
    return HTTPException(403, detail="אין הרשאה לפעולה זו")


def _window(dfrom: Optional[str], dto: Optional[str], max_days: Optional[float],
            default_hours: float = 24) -> tuple:
    now = datetime.utcnow()
    to = parse_iso(dto) if dto else now
    if dto and to is None:
        raise HTTPException(422, detail="פרמטר to אינו ISO 8601 תקין")
    fr = parse_iso(dfrom) if dfrom else to - timedelta(hours=default_hours)
    if dfrom and fr is None:
        raise HTTPException(422, detail="פרמטר from אינו ISO 8601 תקין")
    if to <= fr:
        raise HTTPException(422, detail="to חייב להיות אחרי from")
    if max_days is not None and to - fr > timedelta(days=max_days):
        raise HTTPException(422, detail=f"חלון הזמן המרבי הוא {max_days:g} ימים")
    return fr, to


def _bbox(value: Optional[str], required: bool = False) -> Optional[tuple]:
    if not value:
        if required:
            raise HTTPException(422, detail="bbox נדרש: sw_lat,sw_lon,ne_lat,ne_lon")
        return None
    try:
        s, w, n, e = (float(x) for x in value.split(","))
    except ValueError:
        raise HTTPException(422, detail="bbox: sw_lat,sw_lon,ne_lat,ne_lon")
    if not (-90 <= s < n <= 90 and -180 <= w < e <= 180):
        raise HTTPException(422, detail="bbox לא תקין")
    return s, w, n, e


def _csv_list(value: Optional[str]) -> Optional[list]:
    if not value:
        return None
    return [x.strip() for x in value.split(",") if x.strip()] or None


def _attachment(filename: str) -> str:
    ascii_name = filename.encode("ascii", "ignore").decode().replace('"', "") or "download"
    return f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{urllib.parse.quote(filename)}'


def _get_device(db: Session, device_id: uuid.UUID, allow_hidden: bool = False) -> FieldDevice:
    d = db.query(FieldDevice).filter(FieldDevice.public_id == device_id,
                                     FieldDevice.is_anon_sink.is_(False)).first()
    if d is None or d.status == "deleted" or (not allow_hidden and d.status == "withdrawn"):
        raise HTTPException(404, detail="מכשיר לא נמצא")
    return d


def _group_names(db: Session) -> dict:
    return {g.id: g.name for g in db.query(FieldDeviceGroup).all()}


def _group_id_of(db: Session, d: FieldDevice) -> Optional[int]:
    if d.group_id is not None:
        return d.group_id
    g = default_group(db)
    return g.id if g else None


def _visible_devices(db: Session, group_id: Optional[int] = None) -> list:
    q = db.query(FieldDevice).filter(FieldDevice.is_anon_sink.is_(False), FieldDevice.status != "deleted")
    devs = q.all()
    if group_id is not None:
        dg = default_group(db)
        devs = [d for d in devs if d.group_id == group_id or (d.group_id is None and dg and dg.id == group_id)]
    return devs


def _tier_info(t: Optional[int]) -> dict:
    return {"tier": t, **(TIERS[t] if t is not None else UNCLASSIFIED)}


# ── meta ──────────────────────────────────────────────────────────────────────
@router.get("/meta/device-requirements")
def device_requirements(_u: User = Depends(ANY)):
    return DEVICE_REQUIREMENTS


@router.get("/meta/tiers")
def tiers_meta(_u: User = Depends(ANY)):
    return {"measure_he": "RSRP dBm (LTE / עוגן NSA) · SS-RSRP dBm (NR SA)",
            "tiers": [{"tier": k, **v} for k, v in sorted(TIERS.items(), reverse=True)],
            "unclassified": UNCLASSIFIED,
            "status_labels_he": STATUS_LABELS_HE}


# ── summary ───────────────────────────────────────────────────────────────────
@router.get("/summary")
def summary(dfrom: Optional[str] = Query(None, alias="from"), dto: Optional[str] = Query(None, alias="to"),
            group_id: Optional[int] = None, operator: Optional[str] = None,
            db: Session = Depends(get_db), user: User = Depends(ANY)):
    if _is_viewer(user) and group_id is not None:
        raise _forbid()
    fr, to = _window(dfrom, dto, settings.FIELD_RAW_RETENTION_DAYS)
    now = datetime.utcnow()
    devs = _visible_devices(db, group_id)
    fleet = Counter({k: 0 for k in ("active", "degraded", "paused", "no_contact", "offline", "never_seen",
                                    "withdrawn", "revoked")})
    watching = reporting = 0
    for d in devs:
        st, _ = compute_status(d, now)
        fleet[st] += 1
        if d.status == "active" and st in ("active", "degraded", "offline"):
            watching += 1
            if d.last_heartbeat_at and now - d.last_heartbeat_at <= timedelta(minutes=15):
                reporting += 1
    fleet_out = {"total": len(devs), **{k: fleet[k] for k in fleet}}
    pks = {d.id for d in devs} if group_id is not None else None
    samples = field_kpi.load_window(db, fr, to, pks, _csv_list(operator))
    kpis = field_kpi.compute_kpis(samples)
    kpis["devices_reporting_15m"] = {"reporting": reporting, "expected": watching,
                                     "pct": round(100 * reporting / watching, 1) if watching else None}
    last_rx = max((d.last_sample_received_at for d in devs if d.last_sample_received_at), default=None)
    return {"from": iso(fr), "to": iso(to), "fleet": fleet_out, "kpis": kpis,
            "kpi_notes_he": "אחוזי רמות לפי זמן, מתוך זמן מסווג בלבד; pct_time_by_tier[i] = רמה i "
                            "(0 שולי … 4 מצוין). RSRP של LTE ו-SS-RSRP של NR לעולם אינם מעורבבים.",
            "freshness": {"last_sample_received_at": iso(last_rx), "server_time": iso(now)}}


# ── devices list / live ───────────────────────────────────────────────────────
DEVICE_FIELDS = ["id", "label", "ownership", "group_id", "group", "status", "status_label_he", "status_since",
                 "monitoring_state", "degraded_reasons", "effective_location_mode", "tier", "tier_stale",
                 "rsrp", "nr_ss_rsrp", "operator_key", "operator_name", "network_type", "battery_pct",
                 "is_charging", "last_seen_at", "app_version", "config_pending", "open_alerts",
                 "open_commands", "flags"]


def _open_counts(db: Session) -> tuple:
    alerts = dict(db.execute(select(FieldAlert.device_pk, func.count()).where(
        FieldAlert.status != "resolved", FieldAlert.device_pk.isnot(None)).group_by(FieldAlert.device_pk)).all())
    cmds = dict(db.execute(select(FieldDeviceCommand.device_pk, func.count()).where(
        FieldDeviceCommand.status.in_(("queued", "delivered"))).group_by(FieldDeviceCommand.device_pk)).all())
    return alerts, cmds


@router.get("/devices")
def list_devices(group_id: Optional[int] = None, status: Optional[str] = None,
                 ownership: Optional[str] = None, q: Optional[str] = None, sort: Optional[str] = None,
                 db: Session = Depends(get_db), _u: User = Depends(AO)):
    now = datetime.utcnow()
    names = _group_names(db)
    dg = default_group(db)
    alerts, cmds = _open_counts(db)
    statuses = _csv_list(status)
    owns = _csv_list(ownership)
    rows = []
    for d in _visible_devices(db, group_id):
        st, since = compute_status(d, now)
        if statuses and st not in statuses:
            continue
        if owns and d.ownership not in owns:
            continue
        if q:
            ql = q.lower()
            if not any(ql in (x or "").lower() for x in (d.label, d.model, str(d.public_id))):
                continue
        gid = d.group_id if d.group_id is not None else (dg.id if dg else None)
        cfg = effective_config(db, d)
        fl = status_flags(d, cfg, now)
        rows.append([str(d.public_id), d.label, d.ownership, gid, names.get(gid), st, STATUS_LABELS_HE[st],
                     iso(since), d.last_monitoring_state, d.last_degraded_reasons or [],
                     d.last_effective_location_mode, d.last_tier, bool(d.last_tier_stale), d.last_rsrp_dbm,
                     d.last_nr_ss_rsrp_dbm, d.last_operator_key, d.last_operator_name, d.last_network_type,
                     d.last_battery_pct, d.last_is_charging,
                     iso(max([t for t in (d.last_heartbeat_at, d.last_sample_received_at) if t], default=None)),
                     d.app_version, fl["config_pending"], alerts.get(d.id, 0), cmds.get(d.id, 0), fl])
    if sort:
        desc = sort.startswith("-")
        key = sort.lstrip("-")
        if key not in DEVICE_FIELDS:
            raise HTTPException(422, detail=f"sort: אחד מ-{', '.join(DEVICE_FIELDS)}")
        i = DEVICE_FIELDS.index(key)
        rows.sort(key=lambda r: (r[i] is None, r[i] if not isinstance(r[i], (list, dict)) else str(r[i])),
                  reverse=desc)
    return {"fields": DEVICE_FIELDS, "rows": rows, "server_time": iso(now)}


LIVE_FIELDS = ["id", "label", "lat", "lon", "acc", "precision", "tier", "tier_stale", "rsrp", "network_type",
               "operator_key", "status", "loc_ts"]


@router.get("/devices/live")
def live_devices(bbox: Optional[str] = None, since: Optional[str] = None,
                 db: Session = Depends(get_db), _u: User = Depends(AO)):
    now = datetime.utcnow()
    box = _bbox(bbox)
    since_dt = parse_iso(since) if since else None
    if since and since_dt is None:
        raise HTTPException(422, detail="since אינו ISO 8601 תקין")
    rows = []
    for d in _visible_devices(db):
        st, _ = compute_status(d, now)
        if st not in ("active", "degraded", "offline"):
            continue                          # paused / withdrawn / revoked are never on the live map
        precise_ok = d.last_loc_ts is not None and now - d.last_loc_ts <= timedelta(minutes=60)
        coarse_ok = d.last_coarse_ts is not None and now - d.last_coarse_ts <= timedelta(minutes=60)
        if coarse_ok and (not precise_ok or d.last_coarse_ts > d.last_loc_ts):
            lat, lon, acc, prec, lts = d.last_coarse_lat, d.last_coarse_lon, 1000.0, "coarse", d.last_coarse_ts
        elif precise_ok:
            lat, lon, acc, prec, lts = d.last_lat, d.last_lon, d.last_loc_accuracy_m, "precise", d.last_loc_ts
        else:
            continue
        if box and not (box[0] <= lat <= box[2] and box[1] <= lon <= box[3]):
            continue
        changed = max([t for t in (lts, d.last_heartbeat_at, d.last_sample_received_at) if t])
        if since_dt and changed <= since_dt:
            continue
        rows.append([str(d.public_id), d.label, lat, lon, acc, prec, d.last_tier, bool(d.last_tier_stale),
                     d.last_rsrp_dbm if d.last_network_type != "NR_SA" else d.last_nr_ss_rsrp_dbm,
                     d.last_network_type, d.last_operator_key, st, iso(lts)])
    return {"server_time": iso(now), "fields": LIVE_FIELDS, "rows": rows}


# ── commands (static path /commands/{id} — not under /devices) ───────────────
def _cmd_dict(c: FieldDeviceCommand, users: Optional[dict] = None) -> dict:
    return {"id": str(c.id), "type": c.type, "status": c.status,
            "issued_by": (users or {}).get(c.issued_by, c.issued_by), "issued_at": iso(c.issued_at),
            "delivered_at": iso(c.delivered_at), "acked_at": iso(c.acked_at), "expires_at": iso(c.expires_at)}


def _usernames(db: Session) -> dict:
    return {u.id: u.username for u in db.query(User.id, User.username).all()}


@router.delete("/commands/{command_id}")
def cancel_command(command_id: uuid.UUID, request: Request, db: Session = Depends(get_db),
                   user: User = Depends(AO)):
    c = db.get(FieldDeviceCommand, command_id)
    if c is None:
        raise HTTPException(404, detail="פקודה לא נמצאה")
    if c.status != "queued":
        raise FieldHTTPError(409, "ניתן לבטל רק פקודה שטרם נמסרה למכשיר", "command_not_cancellable")
    c.status = "cancelled"
    d = db.get(FieldDevice, c.device_pk)
    log_activity(db, user.id, "FIELD_COMMAND_CANCELLED",
                 {"command_id": str(c.id), "device_id": str(d.public_id) if d else None, "type": c.type},
                 request, commit=False)
    db.commit()
    return _cmd_dict(c, _usernames(db))


# ── device detail & edits ─────────────────────────────────────────────────────
@router.get("/devices/{device_id}")
def get_device(device_id: uuid.UUID, db: Session = Depends(get_db), _u: User = Depends(AO)):
    d = _get_device(db, device_id, allow_hidden=True)
    now = datetime.utcnow()
    st, since = compute_status(d, now)
    cfg = effective_config(db, d)
    gid = _group_id_of(db, d)
    users = _usernames(db)
    cmds = db.query(FieldDeviceCommand).filter(FieldDeviceCommand.device_pk == d.id) \
        .order_by(FieldDeviceCommand.issued_at.desc()).limit(10).all()
    alerts = db.query(FieldAlert).filter(FieldAlert.device_pk == d.id, FieldAlert.status != "resolved") \
        .order_by(FieldAlert.opened_at.desc()).all()
    precise = d.last_loc_ts is not None and (d.last_coarse_ts is None or d.last_loc_ts >= d.last_coarse_ts)
    return {
        "id": str(d.public_id), "label": d.label, "ownership": d.ownership, "admin_status": d.status,
        "status": st, "status_label_he": STATUS_LABELS_HE[st], "status_since": iso(since),
        "status_flags": status_flags(d, cfg, now),
        "group_id": gid, "group": _group_names(db).get(gid), "notes": d.notes,
        "model": d.model, "manufacturer": d.manufacturer, "os_version": d.os_version, "app_version": d.app_version,
        "consent_version": d.consent_version, "consented_at": iso(d.consented_at),
        "enrolled_at": iso(d.enrolled_at), "revoked_at": iso(d.revoked_at), "withdrawn_at": iso(d.withdrawn_at),
        "config": cfg, "config_override": d.config_override, "config_version": d.config_version,
        "applied_config_version": d.last_applied_config_version,
        "monitoring_state": d.last_monitoring_state, "state_changed_at": iso(d.state_changed_at),
        "degraded_reasons": degraded_reason_items(d.last_degraded_reasons),
        "effective_location_mode": d.last_effective_location_mode,
        "location_services_enabled": d.last_location_services_enabled,
        "permissions": d.last_permissions,
        "battery_pct": d.last_battery_pct, "is_charging": d.last_is_charging, "queue_size": d.last_queue_size,
        "evicted_samples_total": d.last_evicted_samples_total,
        "last_heartbeat_at": iso(d.last_heartbeat_at), "last_sample_at": iso(d.last_sample_at),
        "last_sample_received_at": iso(d.last_sample_received_at),
        "last_sample_ts_reported": iso(d.last_sample_ts_reported),
        "location": ({"lat": d.last_lat, "lon": d.last_lon, "acc": d.last_loc_accuracy_m,
                      "precision": "precise", "ts": iso(d.last_loc_ts)} if precise else
                     {"lat": d.last_coarse_lat, "lon": d.last_coarse_lon, "acc": 1000.0, "precision": "coarse",
                      "ts": iso(d.last_coarse_ts)} if d.last_coarse_ts else None),
        "signal": {**_tier_info(d.last_tier), "tier_stale": bool(d.last_tier_stale),
                   "rsrp_dbm": d.last_rsrp_dbm, "nr_ss_rsrp_dbm": d.last_nr_ss_rsrp_dbm,
                   "network_type": d.last_network_type, "tier_basis_he": "רמה לפי עוגן LTE"
                   if d.last_network_type == "NR_NSA" else None,
                   "operator_key": d.last_operator_key, "operator_name": d.last_operator_name,
                   "service_state": d.last_service_state},
        "current_cell": d.last_cell, "nr_leg": d.last_nr_leg,
        "commands": [_cmd_dict(c, users) for c in cmds],
        "open_alerts": [_alert_dict(a, {d.id: d}) for a in alerts],
    }


class DevicePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: Optional[str] = Field(None, max_length=64)
    group_id: Optional[int] = None
    notes: Optional[str] = Field(None, max_length=256)
    config_override: Optional[dict] = None


@router.patch("/devices/{device_id}")
def patch_device(device_id: uuid.UUID, body: DevicePatch, request: Request, db: Session = Depends(get_db),
                 user: User = Depends(A)):
    d = _get_device(db, device_id)
    fs = body.model_fields_set
    changed = []
    if "label" in fs:
        d.label = body.label
    if "notes" in fs:
        d.notes = body.notes
    bump = False
    if "group_id" in fs and body.group_id != d.group_id:
        if body.group_id is not None and db.get(FieldDeviceGroup, body.group_id) is None:
            raise HTTPException(422, detail="קבוצה לא קיימת")
        d.group_id = body.group_id
        bump = True
        changed.append("group_id")
    if "config_override" in fs:
        try:
            ov = validate_config(body.config_override, "config_override") if body.config_override else None
        except ValueError as e:
            raise HTTPException(422, detail=str(e))
        if ov != d.config_override:
            changed += sorted(set((ov or {}).keys()) | set((d.config_override or {}).keys()))
            d.config_override = ov or None
            bump = True
    if bump:
        bump_device(d)
        log_activity(db, user.id, "FIELD_CONFIG_CHANGED",
                     {"device_id": str(d.public_id), "changed_keys": changed}, request, commit=False)
    db.commit()
    return get_device(device_id, db, user)


@router.post("/devices/{device_id}/revoke")
def revoke_device(device_id: uuid.UUID, request: Request, db: Session = Depends(get_db),
                  user: User = Depends(A)):
    d = _get_device(db, device_id)
    if d.status == "active":
        d.status = "revoked"
        d.revoked_at = datetime.utcnow()
        d.token_hash = None
        cancel_open_commands(db, d.id)
        log_activity(db, user.id, "FIELD_DEVICE_REVOKED", {"device_id": str(d.public_id),
                                                           "ownership": d.ownership}, request, commit=False)
        db.commit()
    return {"status": d.status}


@router.delete("/devices/{device_id}", status_code=202)
def delete_device(device_id: uuid.UUID, request: Request, mode: Optional[Literal["delete", "anonymize"]] = None,
                  db: Session = Depends(get_db), user: User = Depends(A)):
    from app.api.field_ingest import completes_by
    d = _get_device(db, device_id, allow_hidden=True)
    if d.ownership == "ORG":
        if mode == "delete":
            # v2.1 rule 5: ORG data is anonymised by an ADMIN, never deleted
            raise FieldHTTPError(422, "נתוני מכשיר ארגוני מאונונמים ואינם נמחקים (mode=anonymize)",
                                 "org_anonymize_only")
        mode = "anonymize"
    else:
        mode = mode or "delete"
    now = datetime.utcnow()
    if d.status == "withdrawn" and d.purge_mode:
        mode = d.purge_mode                         # already scheduled by the device itself
    d.status = "deleted"
    d.deleted_at = now
    d.token_hash = None
    d.purge_mode = mode
    d.purged_at = None
    cancel_open_commands(db, d.id)
    log_activity(db, user.id, "FIELD_DEVICE_DELETED" if mode == "delete" else "FIELD_DEVICE_ANONYMIZED",
                 {"device_id": str(d.public_id), "ownership": d.ownership, "mode": mode}, request, commit=False)
    db.commit()
    return {"data_action": mode, "completes_by": iso(completes_by(now))}


# ── track & per-device time series ────────────────────────────────────────────
def _device_samples(db: Session, d: FieldDevice, fr: datetime, to: datetime, cols) -> list:
    return [dict(r) for r in db.execute(select(*cols).where(
        FieldMeasurement.device_pk == d.id, FieldMeasurement.ts >= fr, FieldMeasurement.ts < to)
        .order_by(FieldMeasurement.ts, FieldMeasurement.sim_slot)).mappings().all()]


def _thin(points: list, max_points: int) -> list:
    """Uniform decimation that always keeps the first/last point and every tier change."""
    if len(points) <= max_points:
        return points
    keep = {0, len(points) - 1}
    for i in range(1, len(points)):
        if points[i]["tier"] != points[i - 1]["tier"] or points[i]["coarse"] != points[i - 1]["coarse"]:
            keep.update((i - 1, i))
    budget = max(max_points - len(keep), 0)
    if budget:
        step = len(points) / budget
        keep.update(int(i * step) for i in range(budget))
    idx = sorted(keep)
    if len(idx) > max_points:
        step = len(idx) / max_points
        idx = sorted({idx[int(i * step)] for i in range(max_points - 1)} | {len(points) - 1})
    return [points[i] for i in idx[:max_points]]


@router.get("/devices/{device_id}/track")
def device_track(device_id: uuid.UUID, request: Request, dfrom: Optional[str] = Query(None, alias="from"),
                 dto: Optional[str] = Query(None, alias="to"), max_points: int = Query(2000, ge=10, le=20000),
                 db: Session = Depends(get_db), user: User = Depends(AO)):
    d = _get_device(db, device_id)
    fr, to = _window(dfrom, dto, 7)
    cols = (FieldMeasurement.ts, FieldMeasurement.sim_slot, FieldMeasurement.lat, FieldMeasurement.lon,
            FieldMeasurement.loc_accuracy_m, FieldMeasurement.tier, FieldMeasurement.flags,
            FieldMeasurement.rsrp_dbm, FieldMeasurement.network_type, FieldMeasurement.service_state,
            FieldMeasurement.operator_key)
    pts, last_ts = [], None
    for s in _device_samples(db, d, fr, to, cols):
        if s["lat"] is None or s["ts"] == last_ts:
            continue                                   # no position / second SIM of the same instant
        last_ts = s["ts"]
        stale = bool(s["flags"] & FLAG_STALE_CELL)
        pts.append({"ts": s["ts"], "lat": s["lat"], "lon": s["lon"], "acc": s["loc_accuracy_m"],
                    "tier": (0 if s["service_state"] in OOS_STATES else None) if stale else s["tier"],
                    "stale": stale, "coarse": bool(s["flags"] & FLAG_COARSE), "rsrp": None if stale else s["rsrp_dbm"],
                    "network_type": s["network_type"], "operator_key": s["operator_key"]})
    total = len(pts)
    pts = _thin(pts, max_points)
    feats = []
    run: list = []

    def flush():
        if len(run) >= 2:
            feats.append({"type": "Feature", "geometry": {"type": "LineString",
                                                          "coordinates": [[p["lon"], p["lat"]] for p in run]},
                          "properties": {"kind": "segment", "tier": run[0]["tier"], "stale": run[0]["stale"],
                                         "from": iso(run[0]["ts"]), "to": iso(run[-1]["ts"])}})

    coarse_cells = []
    for p in pts:
        if p["coarse"]:
            flush()
            run = []
            cell = (round(p["lat"], 2), round(p["lon"], 2))
            if not coarse_cells or coarse_cells[-1][0] != cell:
                coarse_cells.append((cell, p))
            continue
        if run and (run[-1]["tier"] != p["tier"] or run[-1]["stale"] != p["stale"]):
            run.append(p)            # close the segment at the change point, then start a new one
            flush()
            run = [p]
        else:
            run.append(p)
    flush()
    for (clat, clon), p in coarse_cells:
        h = 0.005
        feats.append({"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [[
            [clon - h, clat - h], [clon + h, clat - h], [clon + h, clat + h], [clon - h, clat + h],
            [clon - h, clat - h]]]},
            "properties": {"kind": "coarse_cell", "location_precision": "coarse", "ts": iso(p["ts"]),
                           "tier": p["tier"], "stale": p["stale"]}})
    for p in pts:
        if p["coarse"]:
            continue
        feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [p["lon"], p["lat"]]},
                      "properties": {"kind": "point", "ts": iso(p["ts"]), "tier": p["tier"], "stale": p["stale"],
                                     "rsrp_dbm": p["rsrp"], "network_type": p["network_type"],
                                     "operator_key": p["operator_key"], "acc": p["acc"]}})
    log_activity(db, user.id, "FIELD_TRACK_VIEW", {"device_id": str(d.public_id), "from": iso(fr), "to": iso(to),
                                                    "points": total}, request)
    return {"type": "FeatureCollection", "features": feats,
            "properties": {"device_id": str(d.public_id), "from": iso(fr), "to": iso(to), "points_total": total,
                           "points_returned": len(pts), "thinned": total > len(pts)}}


TS_METRICS = {
    "rsrp": ("dBm", "RSRP LTE (כולל עוגן NSA)"),
    "nr_ss_rsrp": ("dBm", "SS-RSRP NR SA"),
    "nr_leg_ss_rsrp": ("dBm", "SS-RSRP רגל NR ב-NSA"),
    "rsrq": ("dB", "RSRQ"),
    "sinr": ("dB", "SINR"),
    "battery": ("%", "סוללה"),
}
_BUCKETS = (60, 300, 900, 1800, 3600, 10800, 21600, 43200, 86400)


@router.get("/devices/{device_id}/timeseries")
def device_timeseries(device_id: uuid.UUID, metric: str = "rsrp",
                      dfrom: Optional[str] = Query(None, alias="from"), dto: Optional[str] = Query(None, alias="to"),
                      bucket: str = "auto", db: Session = Depends(get_db), _u: User = Depends(AO)):
    if metric not in TS_METRICS:
        raise HTTPException(422, detail=f"metric: {', '.join(TS_METRICS)}")
    d = _get_device(db, device_id)
    fr, to = _window(dfrom, dto, 31)
    span = (to - fr).total_seconds()
    if bucket == "auto":
        bucket_s = next((b for b in _BUCKETS if span / b <= 300), 86400)
    else:
        try:
            bucket_s = int(bucket)
        except ValueError:
            raise HTTPException(422, detail="bucket: auto או מספר שניות")
        if bucket_s < 60 or span / bucket_s > 5000:
            raise HTTPException(422, detail="bucket קטן מדי לחלון הזמן")
    cols = (FieldMeasurement.ts, FieldMeasurement.sim_slot, FieldMeasurement.flags, FieldMeasurement.tier,
            FieldMeasurement.service_state, FieldMeasurement.network_type, FieldMeasurement.meas_tech,
            FieldMeasurement.rsrp_dbm, FieldMeasurement.rsrq_db, FieldMeasurement.sinr_db,
            FieldMeasurement.nr_ss_rsrp_dbm, FieldMeasurement.battery_pct)
    samples = _device_samples(db, d, fr, to, cols)

    epoch = datetime(1970, 1, 1)

    def bstart(t: datetime) -> datetime:          # buckets aligned to UTC multiples of bucket_s
        return epoch + timedelta(seconds=int((t - epoch).total_seconds() // bucket_s) * bucket_s)

    series: dict = defaultdict(lambda: defaultdict(list))
    tiers: dict = defaultdict(lambda: defaultdict(list))
    for s in samples:
        stale = bool(s["flags"] & FLAG_STALE_CELL)
        nt = s["network_type"]
        if metric == "battery":
            if s["battery_pct"] is not None:
                series["ALL"][bstart(s["ts"])].append(float(s["battery_pct"]))
            continue
        if stale:
            continue
        if metric == "rsrp":
            if nt in ("LTE", "NR_NSA") and s["rsrp_dbm"] is not None:
                series["LTE"][bstart(s["ts"])].append(s["rsrp_dbm"])
                tiers["LTE"][bstart(s["ts"])].append(s["tier"])
        elif metric == "nr_ss_rsrp":
            if nt == "NR_SA" and s["rsrp_dbm"] is not None:
                series["NR"][bstart(s["ts"])].append(s["rsrp_dbm"])
                tiers["NR"][bstart(s["ts"])].append(s["tier"])
        elif metric == "nr_leg_ss_rsrp":
            if nt == "NR_NSA" and s["nr_ss_rsrp_dbm"] is not None:
                series["NSA_NR_LEG"][bstart(s["ts"])].append(s["nr_ss_rsrp_dbm"])
        else:
            v = s["rsrq_db"] if metric == "rsrq" else s["sinr_db"]
            if v is not None and s["meas_tech"] in ("LTE", "NR"):
                series[s["meas_tech"]][bstart(s["ts"])].append(v)
    if metric == "battery":
        for hb in db.query(FieldHeartbeat).filter(FieldHeartbeat.device_pk == d.id, FieldHeartbeat.received_at >= fr,
                                                  FieldHeartbeat.received_at < to).all():
            if hb.battery_pct is not None:
                series["ALL"][bstart(hb.received_at)].append(float(hb.battery_pct))
    out_series = []
    for tech, buckets in series.items():
        pts = []
        for t in sorted(buckets):
            vals = buckets[t]
            tl = [x for x in tiers[tech].get(t, []) if x is not None]
            mode = Counter(tl).most_common(1)[0][0] if tl else None
            pts.append([iso(t), round(min(vals), 1), round(sum(vals) / len(vals), 1), round(max(vals), 1),
                        len(vals), mode])
        out_series.append({"meas_tech": tech, "points": pts})

    # gaps: no data / paused (from heartbeats), OOS runs, stale runs
    gaps = []
    cap = timedelta(seconds=field_agg.DT_CAP_S)
    paused_hb = [h.received_at for h in db.query(FieldHeartbeat.received_at).filter(
        FieldHeartbeat.device_pk == d.id, FieldHeartbeat.received_at >= fr, FieldHeartbeat.received_at < to,
        FieldHeartbeat.monitoring_state.in_(PAUSED_STATES)).all()]
    run_kind, run_start = None, None
    prev = None
    for s in samples:
        if prev is not None and s["ts"] - prev["ts"] > cap:
            reason = "paused" if any(prev["ts"] <= t <= s["ts"] for t in paused_hb) else "no_data"
            gaps.append([iso(prev["ts"]), iso(s["ts"]), reason])
        kind = ("oos" if s["service_state"] in OOS_STATES else
                "stale" if s["flags"] & FLAG_STALE_CELL else None)
        if kind != run_kind:
            if run_kind is not None:
                gaps.append([iso(run_start), iso(s["ts"]), run_kind])
            run_kind, run_start = kind, s["ts"]
        prev = s
    if run_kind is not None and prev is not None:
        gaps.append([iso(run_start), iso(prev["ts"]), run_kind])
    unit, label = TS_METRICS[metric]
    return {"metric": metric, "label_he": label, "unit": unit, "bucket_s": bucket_s, "from": iso(fr), "to": iso(to),
            "point_fields": ["t", "min", "avg", "max", "n", "tier_mode"], "series": out_series,
            "gaps": sorted(gaps, key=lambda g: g[0])}


# ── device commands ───────────────────────────────────────────────────────────
class CommandIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["sample_now", "upload_now"]


@router.post("/devices/{device_id}/commands", status_code=201)
def issue_command(device_id: uuid.UUID, body: CommandIn, request: Request, db: Session = Depends(get_db),
                  user: User = Depends(AO)):
    d = _get_device(db, device_id, allow_hidden=True)
    if d.status != "active":
        raise FieldHTTPError(409, "המכשיר אינו פעיל (בוטל / ההסכמה בוטלה)", "device_inactive")
    open_same = db.query(FieldDeviceCommand).filter(
        FieldDeviceCommand.device_pk == d.id, FieldDeviceCommand.type == body.type,
        FieldDeviceCommand.status.in_(("queued", "delivered"))).first()
    if open_same is not None:
        raise FieldHTTPError(409, "כבר קיימת פקודה פתוחה מסוג זה למכשיר", "command_pending")
    retry = ratelimit.take(f"cmd:{d.id}", *ratelimit.COMMANDS)
    if retry is not None:
        raise FieldHTTPError(429, "יותר מדי פקודות למכשיר זה — נסו שוב מאוחר יותר", "rate_limited",
                             {"Retry-After": str(retry)})
    now = datetime.utcnow()
    c = FieldDeviceCommand(id=uuid.uuid4(), device_pk=d.id, type=body.type, status="queued", issued_by=user.id,
                           issued_at=now, expires_at=now + timedelta(minutes=settings.FIELD_COMMAND_TTL_MIN))
    db.add(c)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise FieldHTTPError(409, "כבר קיימת פקודה פתוחה מסוג זה למכשיר", "command_pending")
    log_activity(db, user.id, "FIELD_COMMAND_ISSUED",
                 {"command_id": str(c.id), "device_id": str(d.public_id), "type": c.type}, request, commit=False)
    db.commit()
    out = {"id": str(c.id), "type": c.type, "status": c.status, "issued_at": iso(c.issued_at),
           "expires_at": iso(c.expires_at)}
    if d.last_monitoring_state in PAUSED_STATES:
        out["warning_he"] = ("המכשיר מושהה — פקודות אינן מבוצעות במצב מושהה (צפוי skipped_paused), "
                             "ואין זמן מסירה מובטח עד ה-heartbeat הבא")
    return out


@router.get("/devices/{device_id}/commands")
def list_commands(device_id: uuid.UUID, limit: int = Query(20, ge=1, le=200), db: Session = Depends(get_db),
                  _u: User = Depends(AO)):
    d = _get_device(db, device_id, allow_hidden=True)
    rows = db.query(FieldDeviceCommand).filter(FieldDeviceCommand.device_pk == d.id) \
        .order_by(FieldDeviceCommand.issued_at.desc()).limit(limit).all()
    users = _usernames(db)
    return [_cmd_dict(c, users) for c in rows]


# ── heatmap / dead zones / aggregate series / breakdown ──────────────────────
def _heat_cells(db: Session, user: User, bbox: tuple, fr: datetime, to: datetime, operator: Optional[str],
                measure: str, metric: str, level: int) -> dict:
    viewer = _is_viewer(user)
    tech = "LTE" if measure == "lte_rsrp" else "NR"
    if viewer:                                      # day resolution for VIEWER
        fr = il_day_bounds(il_day(fr))[0]
        to = il_day_bounds(il_day(to - timedelta(microseconds=1)))[1]
    cells, source = field_agg.load_cells(db, level, fr, to, tech, _csv_list(operator), bbox, viewer)
    merged = field_agg.merge_operators(cells)
    k = settings.FIELD_VIEWER_K_MIN
    rows, suppressed = [], 0
    for (_tech, cx, cy), c in sorted(merged.items(), key=lambda kv: (kv[0][2], kv[0][1])):
        st = c["st"]
        if viewer and len(st["device_set"]) < k:
            suppressed += 1
            continue
        m = field_agg.cell_metrics(st, tech)
        value = {"p50": m["p50"], "p5": m["p5"], "tier": m["tier"], "n": m["n"], "oos_pct": m["oos_pct"]}[metric]
        row = [cx, cy, m["n"], value, m["tier"]]
        if not viewer:
            row.append(m["n_devices"])
        rows.append(row)
    if len(rows) > MAX_HEAT_CELLS:
        raise FieldHTTPError(413, f"יותר מ-{MAX_HEAT_CELLS} תאים — הגדילו את הרמה (level) או הקטינו את התחום",
                             "too_many_cells")
    if level == 2500:
        cell_deg = [G2500_DLAT, G2500_DLON]
    else:
        f = 5 if level == 500 else 1
        cell_deg = [round(G100_DLAT * f, 6), round(G100_DLON * f, 6)]
    fields = ["gx", "gy", "n", "value", "tier"] + ([] if viewer else ["n_devices"])
    return {"level": level, "measure": measure, "metric": metric, "meas_tech": tech, "cell_deg": cell_deg,
            "cell_origin_note": "south = gy·dlat, west = gx·dlon", "from": iso(fr), "to": iso(to),
            "source": source, "includes_coarse": level == 2500, "fields": fields, "rows": rows,
            "suppressed": suppressed if viewer else 0, "k_min": k if viewer else None}


@router.get("/heatmap")
def heatmap(bbox: str, measure: Literal["lte_rsrp", "nr_ss_rsrp"],
            dfrom: Optional[str] = Query(None, alias="from"), dto: Optional[str] = Query(None, alias="to"),
            operator: Optional[str] = None, metric: Literal["p50", "p5", "tier", "n", "oos_pct"] = "p50",
            level: Literal["100", "500", "2500"] = "100", db: Session = Depends(get_db), user: User = Depends(ANY)):
    fr, to = _window(dfrom, dto, None)
    return _heat_cells(db, user, _bbox(bbox, True), fr, to, operator, measure, metric, int(level))


@router.get("/dead-zones")
def dead_zones(dfrom: Optional[str] = Query(None, alias="from"), dto: Optional[str] = Query(None, alias="to"),
               operator: Optional[str] = None, tech: Optional[Literal["LTE", "NR"]] = None,
               bbox: Optional[str] = None, db: Session = Depends(get_db), user: User = Depends(ANY)):
    viewer = _is_viewer(user)
    fr, to = _window(dfrom, dto, None, default_hours=24 * 7)
    techs = [tech] if tech else ["LTE", "NR"]
    zones = []
    for t in techs:
        cells, _src = field_agg.load_cells(db, 100, fr, to, t, _csv_list(operator), _bbox(bbox), viewer)
        zones += field_agg.dead_zones(cells, (G100_DLAT, G100_DLON),
                                      settings.FIELD_VIEWER_K_MIN if viewer else None)
    for z in zones:
        z["first_seen"], z["last_seen"] = iso(z["first_seen"]), iso(z["last_seen"])
        if viewer:
            z.pop("n_devices", None)
    return zones


def _series_key(metric: str, s: dict) -> Optional[tuple]:
    if metric.startswith("rsrp"):
        if s["flags"] & FLAG_STALE_CELL or s["rsrp_dbm"] is None:
            return None
        if s["network_type"] in ("LTE", "NR_NSA"):
            return (s["operator_key"], "LTE")
        if s["network_type"] == "NR_SA":
            return (s["operator_key"], "NR")
        return None
    return (s["operator_key"], None)


@router.get("/timeseries")
def aggregate_timeseries(metric: Literal["rsrp_p50", "rsrp_p5", "pct_time_ge_medium", "oos_minutes", "samples"],
                         dfrom: Optional[str] = Query(None, alias="from"), dto: Optional[str] = Query(None, alias="to"),
                         bucket: Literal["1h", "1d"] = "1h", group_id: Optional[int] = None,
                         operator: Optional[str] = None, meas_tech: Optional[Literal["LTE", "NR"]] = None,
                         bbox: Optional[str] = None, db: Session = Depends(get_db), user: User = Depends(ANY)):
    viewer = _is_viewer(user)
    if viewer and group_id is not None:
        raise _forbid()
    if viewer and bucket != "1d":
        raise HTTPException(422, detail="לצופה זמינה רזולוציה יומית בלבד (bucket=1d)")
    fr, to = _window(dfrom, dto, settings.FIELD_RAW_RETENTION_DAYS)
    pks = {d.id for d in _visible_devices(db, group_id)} if group_id is not None else None
    samples = field_kpi.load_window(db, fr, to, pks, _csv_list(operator), bbox=_bbox(bbox))
    buckets: dict = defaultdict(lambda: defaultdict(list))
    for s in samples:
        key = _series_key(metric, s)
        if key is None or (meas_tech and key[1] not in (None, meas_tech)):
            continue
        b = floor_hour(s["ts"]) if bucket == "1h" else il_day_bounds(il_day(s["ts"]))[0]
        buckets[key][b].append(s)
    k = settings.FIELD_VIEWER_K_MIN
    series, suppressed = [], 0
    for (op, tech), by_t in sorted(buckets.items(), key=lambda kv: (kv[0][0] or "", kv[0][1] or "")):
        pts = []
        for t in sorted(by_t):
            ss = by_t[t]
            if viewer and len({x["device_pk"] for x in ss}) < k:
                suppressed += 1
                continue
            if metric in ("rsrp_p50", "rsrp_p5"):
                from app.services.field_quality import percentile
                v = percentile([x["rsrp_dbm"] for x in ss], 50 if metric == "rsrp_p50" else 5)
            else:
                kp = field_kpi.compute_kpis(ss)
                if metric == "samples":
                    v = kp["samples"]
                elif metric == "oos_minutes":
                    v = kp["oos_minutes"]
                else:
                    tiers = kp["pct_time_by_tier"]
                    v = round(sum(x for x in tiers[2:] if x is not None), 1) if tiers[0] is not None else None
            pts.append([iso(t), v, len(ss)])
        if pts:
            series.append({"key": op, "meas_tech": tech, "points": pts})
    return {"metric": metric, "bucket": bucket, "from": iso(fr), "to": iso(to), "series": series,
            "suppressed": suppressed if viewer else 0}


def _breakdown_rows(db: Session, user: User, by: str, fr: datetime, to: datetime, bbox: Optional[tuple],
                    operator: Optional[str], group_id: Optional[int], fair: bool) -> tuple:
    viewer = _is_viewer(user)
    pks = {d.id for d in _visible_devices(db, group_id)} if group_id is not None else None
    samples = field_kpi.load_window(db, fr, to, pks, _csv_list(operator), bbox=bbox)
    if fair and by == "operator":
        samples = field_kpi.fair_filter(samples)
    ops = {o.operator_key: o.name_he for o in db.query(FieldOperatorMap).all()}
    if by == "group":
        devs = {d.id: _group_id_of(db, d) for d in db.query(FieldDevice).all()}
        names = _group_names(db)
        keyfn = lambda s: devs.get(s["device_pk"])                      # noqa: E731
        labelfn = lambda k: names.get(k, str(k))                        # noqa: E731
    elif by == "operator":
        keyfn = lambda s: s["operator_key"] or "unknown"                # noqa: E731
        labelfn = lambda k: ops.get(k, "לא ידוע" if k == "unknown" else k)  # noqa: E731
    elif by == "tech":
        keyfn = lambda s: s["meas_tech"]                                # noqa: E731
        labelfn = lambda k: TECH_LABELS_HE.get(k, k)                    # noqa: E731
    elif by == "band":
        keyfn = lambda s: s["band"]                                     # noqa: E731
        labelfn = lambda k: f"B{k}" if k is not None else "לא ידוע"     # noqa: E731
    else:  # cell
        keyfn = lambda s: (f"{s['operator_key']}:{s['cell_id']}"        # noqa: E731
                           if s["cell_id"] is not None and not s["flags"] & FLAG_STALE_CELL else None)
        labelfn = lambda k: f"{ops.get(k.split(':')[0], k.split(':')[0])} · {k.split(':')[1]}"  # noqa: E731
    groups = field_kpi.group_by(samples, keyfn)
    k_min = settings.FIELD_VIEWER_K_MIN
    rows, suppressed = [], 0
    for key, ss in groups.items():
        ndev = len({s["device_pk"] for s in ss})
        if viewer and ndev < k_min:
            suppressed += 1
            continue
        kp = field_kpi.compute_kpis(ss)
        row = {"key": key, "label": labelfn(key), "n": kp["samples"], "time_s": kp["time_s"],
               "pct_time_by_tier": kp["pct_time_by_tier"], "pct_time_unclassified": kp["pct_time_unclassified"],
               "rsrp": kp["rsrp"], "oos_min": kp["oos_minutes"], "cell_changes_per_h": kp["cell_change_per_hour"],
               "nsa_share": kp["nsa_share_pct"], "stale_excluded_pct": kp["stale_excluded_pct"]}
        if not viewer:
            row["n_devices"] = ndev
        rows.append(row)
    rows.sort(key=lambda r: -r["time_s"])
    return rows, suppressed


@router.get("/breakdown")
def breakdown(by: Literal["operator", "tech", "band", "cell", "group"] = "operator",
              dfrom: Optional[str] = Query(None, alias="from"), dto: Optional[str] = Query(None, alias="to"),
              bbox: Optional[str] = None, operator: Optional[str] = None, group_id: Optional[int] = None,
              fair: bool = False, db: Session = Depends(get_db), user: User = Depends(ANY)):
    if _is_viewer(user) and (by == "group" or group_id is not None):
        raise _forbid()
    fr, to = _window(dfrom, dto, settings.FIELD_RAW_RETENTION_DAYS)
    rows, suppressed = _breakdown_rows(db, user, by, fr, to, _bbox(bbox), operator, group_id, fair)
    return {"by": by, "from": iso(fr), "to": iso(to), "fair": bool(fair and by == "operator"), "rows": rows,
            "suppressed": suppressed if _is_viewer(user) else 0}


# ── alerts ────────────────────────────────────────────────────────────────────
def _alert_dict(a: FieldAlert, devices: dict) -> dict:
    d = devices.get(a.device_pk)
    return {"id": a.id, "type": a.type, "type_label_he": TYPE_LABELS_HE.get(a.type, a.type), "severity": a.severity,
            "status": a.status, "message_he": a.message_he,
            "device_id": str(d.public_id) if d is not None else None, "device_label": d.label if d else None,
            "gx": a.gx, "gy": a.gy, "operator_key": a.operator_key, "details": a.details,
            "opened_at": iso(a.opened_at), "last_seen_at": iso(a.last_seen_at), "resolved_at": iso(a.resolved_at),
            "occurrences": a.occurrences, "acked_by": a.acked_by, "acked_at": iso(a.acked_at), "note": a.note}


@router.get("/alerts")
def list_alerts(status: str = "open,acked", type: Optional[str] = None, device_id: Optional[uuid.UUID] = None,
                limit: int = Query(200, ge=1, le=1000), db: Session = Depends(get_db), _u: User = Depends(AO)):
    q = db.query(FieldAlert).filter(FieldAlert.status.in_(_csv_list(status) or ["open"]))
    if type:
        q = q.filter(FieldAlert.type.in_(_csv_list(type)))
    if device_id is not None:
        d = db.query(FieldDevice).filter(FieldDevice.public_id == device_id).first()
        if d is None:
            return []
        q = q.filter(FieldAlert.device_pk == d.id)
    rows = q.order_by(FieldAlert.opened_at.desc()).limit(limit).all()
    devs = {d.id: d for d in db.query(FieldDevice).filter(
        FieldDevice.id.in_({a.device_pk for a in rows if a.device_pk})).all()} if rows else {}
    return [_alert_dict(a, devs) for a in rows]


class AlertActionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    note: Optional[str] = Field(None, max_length=256)


def _alert_action(alert_id: int, body: Optional[AlertActionIn], db: Session, user: User, action: str) -> dict:
    a = db.get(FieldAlert, alert_id)
    if a is None:
        raise HTTPException(404, detail="התראה לא נמצאה")
    now = datetime.utcnow()
    if action == "ack" and a.status == "open":
        a.status, a.acked_by, a.acked_at = "acked", user.username, now
    elif action == "resolve" and a.status != "resolved":
        a.status, a.resolved_at = "resolved", now
        if a.acked_at is None:
            a.acked_by, a.acked_at = user.username, now
    if body is not None and body.note:
        a.note = body.note
    db.commit()
    devs = {a.device_pk: db.get(FieldDevice, a.device_pk)} if a.device_pk else {}
    return _alert_dict(a, devs)


@router.post("/alerts/{alert_id}/ack")
def ack_alert(alert_id: int, body: Optional[AlertActionIn] = None, db: Session = Depends(get_db),
              user: User = Depends(AO)):
    return _alert_action(alert_id, body, db, user, "ack")


@router.post("/alerts/{alert_id}/resolve")
def resolve_alert(alert_id: int, body: Optional[AlertActionIn] = None, db: Session = Depends(get_db),
                  user: User = Depends(AO)):
    return _alert_action(alert_id, body, db, user, "resolve")


def _rule_dict(r: FieldAlertRule) -> dict:
    return {"id": r.id, "type": r.type, "type_label_he": TYPE_LABELS_HE.get(r.type, r.type), "scope": r.scope,
            "scope_id": r.scope_id, "params": r.params, "severity": r.severity, "enabled": r.enabled,
            "updated_by": r.updated_by, "updated_at": iso(r.updated_at)}


@router.get("/alert-rules")
def list_alert_rules(db: Session = Depends(get_db), _u: User = Depends(AO)):
    return [_rule_dict(r) for r in db.query(FieldAlertRule).order_by(FieldAlertRule.id).all()]


class AlertRuleIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: Optional[bool] = None
    params: Optional[dict] = None
    severity: Optional[Literal["info", "warning", "critical"]] = None


@router.put("/alert-rules/{rule_id}")
def update_alert_rule(rule_id: int, body: AlertRuleIn, db: Session = Depends(get_db), user: User = Depends(A)):
    r = db.get(FieldAlertRule, rule_id)
    if r is None:
        raise HTTPException(404, detail="כלל לא נמצא")
    if body.params is not None:
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0 for v in body.params.values()):
            raise HTTPException(422, detail="params: ערכים מספריים אי-שליליים בלבד")
        r.params = {**(r.params or {}), **body.params}
    if body.enabled is not None:
        r.enabled = body.enabled
    if body.severity is not None:
        r.severity = body.severity
    r.updated_by, r.updated_at = user.username, datetime.utcnow()
    db.commit()
    return _rule_dict(r)


# ── enrollment codes ──────────────────────────────────────────────────────────
def _code_dict(c: FieldEnrollmentCode, groups: dict, users: dict) -> dict:
    return {"id": c.id, "label": c.label, "code_hint": c.code_hint, "group_id": c.group_id,
            "group": groups.get(c.group_id), "ownership": c.ownership, "max_uses": c.max_uses,
            "use_count": c.use_count, "expires_at": iso(c.expires_at), "created_by": users.get(c.created_by),
            "created_at": iso(c.created_at), "revoked_at": iso(c.revoked_at)}


@router.get("/enrollment-codes")
def list_codes(include_expired: bool = False, db: Session = Depends(get_db), _u: User = Depends(A)):
    q = db.query(FieldEnrollmentCode)
    if not include_expired:
        q = q.filter(FieldEnrollmentCode.expires_at > datetime.utcnow(), FieldEnrollmentCode.revoked_at.is_(None))
    groups, users = _group_names(db), _usernames(db)
    return [_code_dict(c, groups, users) for c in q.order_by(FieldEnrollmentCode.created_at.desc()).all()]


class CodeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: Optional[str] = Field(None, max_length=64)
    group_id: Optional[int] = None
    ownership: Literal["ORG", "PERSONAL"]
    max_uses: int = Field(1, ge=1, le=200)
    ttl_hours: int = Field(24, ge=1, le=720)


def qr_payload(code: str, ownership: Optional[str] = None) -> str:
    base = settings.FIELD_PUBLIC_BASE_URL.rstrip("/")
    s = f"coverageops-enroll:v1?server={urllib.parse.quote(base, safe=':/')}&code={code}"
    return s + (f"&ownership={ownership}" if ownership else "")


def qr_svg(payload: str) -> Optional[str]:
    try:
        import segno
    except ImportError:      # dependency not installed yet (image built before it was added) – the
        return None          # dialog falls back to the manual code; `pip install -r requirements.txt` fixes it
    buf = io.BytesIO()
    segno.make(payload, error="m", micro=False).save(buf, kind="svg", scale=6, border=2, xmldecl=False,
                                                       svgns=True, dark="#000000", light="#ffffff")
    return buf.getvalue().decode("utf-8")


@router.post("/enrollment-codes", status_code=201)
def create_code(body: CodeIn, request: Request, db: Session = Depends(get_db), user: User = Depends(A)):
    from app.api.field_ingest import normalize_code
    if body.group_id is not None and db.get(FieldDeviceGroup, body.group_id) is None:
        raise HTTPException(422, detail="קבוצה לא קיימת")
    for _ in range(5):
        raw = "".join(secrets.choice(CODE_ALPHABET) for _ in range(10))
        h = hash_secret(normalize_code(raw))
        if db.query(FieldEnrollmentCode.id).filter(FieldEnrollmentCode.code_hash == h).first() is None:
            break
    display = f"{raw[:4]}-{raw[4:8]}-{raw[8:]}"
    now = datetime.utcnow()
    c = FieldEnrollmentCode(code_hash=h, code_hint=raw[-4:], label=body.label, group_id=body.group_id,
                            ownership=body.ownership, max_uses=body.max_uses, use_count=0,
                            expires_at=now + timedelta(hours=body.ttl_hours), created_by=user.id, created_at=now)
    db.add(c)
    db.flush()
    log_activity(db, user.id, "FIELD_CODE_CREATED", {"code_id": c.id, "code_hint": c.code_hint,
                                                     "max_uses": c.max_uses, "expires_at": iso(c.expires_at),
                                                     "ownership": c.ownership}, request, commit=False)
    db.commit()
    payload = qr_payload(display, body.ownership)
    out = _code_dict(c, _group_names(db), _usernames(db))
    out.update(code=display, qr_payload=payload, qr_svg=qr_svg(payload),
               note_he="הקוד מוצג פעם אחת בלבד — הוא אינו נשמר בשרת. ה-QR מכיל את הקוד.")
    return out


@router.delete("/enrollment-codes/{code_id}", status_code=204)
def revoke_code(code_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(A)):
    c = db.get(FieldEnrollmentCode, code_id)
    if c is None:
        raise HTTPException(404, detail="קוד לא נמצא")
    if c.revoked_at is None:
        c.revoked_at = datetime.utcnow()
        log_activity(db, user.id, "FIELD_CODE_REVOKED", {"code_id": c.id, "code_hint": c.code_hint,
                                                         "max_uses": c.max_uses, "expires_at": iso(c.expires_at)},
                     request, commit=False)
        db.commit()
    return Response(status_code=204)


# ── groups & config ───────────────────────────────────────────────────────────
def _group_dict(db: Session, g: FieldDeviceGroup, counts: dict) -> dict:
    eff = dict(DEFAULT_CONFIG)
    eff.update(g.config or {})
    return {"id": g.id, "name": g.name, "description": g.description, "is_default": g.is_default,
            "config": g.config or {}, "effective_config": eff, "device_count": counts.get(g.id, 0),
            "updated_at": iso(g.updated_at), "updated_by": g.updated_by,
            "personal_note_he": "במכשיר אישי allow_user_pause תמיד true; location_mode הוא תקרה — המשתמש יכול רק להחמיר"}


def _group_counts(db: Session) -> dict:
    dg = default_group(db)
    counts: dict = Counter()
    for d in db.query(FieldDevice).filter(FieldDevice.is_anon_sink.is_(False),
                                          FieldDevice.status.in_(("active", "revoked"))).all():
        gid = d.group_id if d.group_id is not None else (dg.id if dg else None)
        counts[gid] += 1
    return counts


@router.get("/groups")
def list_groups(db: Session = Depends(get_db), _u: User = Depends(AO)):
    counts = _group_counts(db)
    return [_group_dict(db, g, counts) for g in db.query(FieldDeviceGroup).order_by(FieldDeviceGroup.id).all()]


class GroupIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(..., min_length=1, max_length=64)
    description: Optional[str] = Field(None, max_length=256)
    config: dict = Field(default_factory=dict)


class GroupPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Optional[str] = Field(None, min_length=1, max_length=64)
    description: Optional[str] = Field(None, max_length=256)
    config: Optional[dict] = None


@router.post("/groups", status_code=201)
def create_group(body: GroupIn, request: Request, db: Session = Depends(get_db), user: User = Depends(A)):
    try:
        cfg = validate_config(body.config)
    except ValueError as e:
        raise HTTPException(422, detail=str(e))
    if db.query(FieldDeviceGroup).filter(FieldDeviceGroup.name == body.name).first():
        raise HTTPException(409, detail="קבוצה בשם זה כבר קיימת")
    g = FieldDeviceGroup(name=body.name, description=body.description, config=cfg, updated_by=user.username)
    db.add(g)
    db.flush()
    log_activity(db, user.id, "FIELD_CONFIG_CHANGED", {"group_id": g.id, "changed_keys": sorted(cfg)}, request,
                 commit=False)
    db.commit()
    return _group_dict(db, g, {})


@router.put("/groups/{group_id}")
def update_group(group_id: int, body: GroupPatch, request: Request, db: Session = Depends(get_db),
                 user: User = Depends(A)):
    g = db.get(FieldDeviceGroup, group_id)
    if g is None:
        raise HTTPException(404, detail="קבוצה לא נמצאה")
    if body.name is not None and body.name != g.name:
        if db.query(FieldDeviceGroup).filter(FieldDeviceGroup.name == body.name).first():
            raise HTTPException(409, detail="קבוצה בשם זה כבר קיימת")
        g.name = body.name
    if "description" in body.model_fields_set:
        g.description = body.description
    if body.config is not None:
        try:
            cfg = validate_config(body.config)
        except ValueError as e:
            raise HTTPException(422, detail=str(e))
        if cfg != (g.config or {}):
            keys = sorted(set(cfg) | set(g.config or {}))
            g.config = cfg
            bump_group_devices(db, g)
            log_activity(db, user.id, "FIELD_CONFIG_CHANGED", {"group_id": g.id, "changed_keys": keys}, request,
                         commit=False)
    g.updated_at, g.updated_by = datetime.utcnow(), user.username
    db.commit()
    return _group_dict(db, g, _group_counts(db))


@router.delete("/groups/{group_id}", status_code=204)
def delete_group(group_id: int, db: Session = Depends(get_db), _u: User = Depends(A)):
    g = db.get(FieldDeviceGroup, group_id)
    if g is None:
        raise HTTPException(404, detail="קבוצה לא נמצאה")
    if g.is_default:
        raise HTTPException(409, detail="לא ניתן למחוק את קבוצת ברירת המחדל")
    if db.query(FieldDevice.id).filter(FieldDevice.group_id == g.id, FieldDevice.status != "deleted").first():
        raise HTTPException(409, detail="יש מכשירים בקבוצה — העבירו אותם קודם")
    db.query(FieldEnrollmentCode).filter(FieldEnrollmentCode.group_id == g.id).update(
        {FieldEnrollmentCode.group_id: None}, synchronize_session=False)
    db.query(FieldDevice).filter(FieldDevice.group_id == g.id).update(
        {FieldDevice.group_id: None}, synchronize_session=False)
    db.delete(g)
    db.commit()
    return Response(status_code=204)


# ── exports ───────────────────────────────────────────────────────────────────
EXPORT_COLS = ["device_id", "ts", "lat", "lon", "loc_accuracy_m", "location_precision", "network_type",
               "operator_key", "operator_name", "sim_slot", "cell_id", "pci", "tac", "earfcn_arfcn", "band",
               "rsrp_dbm", "rsrq_db", "sinr_db", "rssi_dbm", "nr_ss_rsrp_dbm", "service_state", "data_state",
               "tier", "meas_tech", "stale_cell", "battery_pct", "trigger"]


def _export_query(db: Session, user: User, device_id, group_id, fr, to, bbox):
    conds = [FieldMeasurement.ts >= fr, FieldMeasurement.ts < to]
    hidden = field_agg.hidden_device_pks(db)
    if device_id is not None:
        d = _get_device(db, device_id)
        conds.append(FieldMeasurement.device_pk == d.id)
    elif group_id is not None:
        conds.append(FieldMeasurement.device_pk.in_({d.id for d in _visible_devices(db, group_id)} or {-1}))
    if hidden:
        conds.append(FieldMeasurement.device_pk.notin_(hidden))
    if bbox:
        s, w, n, e = bbox
        conds += [FieldMeasurement.lat >= s, FieldMeasurement.lat <= n, FieldMeasurement.lon >= w,
                  FieldMeasurement.lon <= e]
    return and_(*conds)


def _export_rows(bind, where):
    from sqlalchemy.orm import Session as S
    with S(bind=bind) as s:
        pubs = {d.id: str(d.public_id) for d in s.query(FieldDevice.id, FieldDevice.public_id).all()}
        for m in s.execute(select(FieldMeasurement).where(where).order_by(FieldMeasurement.ts)
                           .execution_options(yield_per=2000)).scalars():
            coarse = bool(m.flags & FLAG_COARSE)
            yield {"device_id": pubs.get(m.device_pk), "ts": iso(m.ts), "lat": m.lat, "lon": m.lon,
                   "loc_accuracy_m": m.loc_accuracy_m,
                   "location_precision": None if m.lat is None else ("coarse" if coarse else "precise"),
                   "network_type": m.network_type, "operator_key": m.operator_key, "operator_name": m.operator_name,
                   "sim_slot": m.sim_slot, "cell_id": m.cell_id, "pci": m.pci, "tac": m.tac,
                   "earfcn_arfcn": m.earfcn_arfcn, "band": m.band, "rsrp_dbm": m.rsrp_dbm, "rsrq_db": m.rsrq_db,
                   "sinr_db": m.sinr_db, "rssi_dbm": m.rssi_dbm, "nr_ss_rsrp_dbm": m.nr_ss_rsrp_dbm,
                   "service_state": m.service_state, "data_state": m.data_state, "tier": m.tier,
                   "meas_tech": m.meas_tech, "stale_cell": bool(m.flags & FLAG_STALE_CELL),
                   "battery_pct": m.battery_pct, "trigger": m.trigger}


def _export_measurements(fmt: str, request: Request, device_id, group_id, dfrom, dto, bbox, db, user):
    fr, to = _window(dfrom, dto, 31)
    box = _bbox(bbox)
    where = _export_query(db, user, device_id, group_id, fr, to, box)
    n = db.execute(select(func.count()).select_from(FieldMeasurement).where(where)).scalar() or 0
    if n > MAX_EXPORT_ROWS:
        raise FieldHTTPError(413, f"יותר מ-{MAX_EXPORT_ROWS:,} שורות — צמצמו את הטווח", "too_many_rows")
    log_activity(db, user.id, "FIELD_EXPORT", {"scope": "measurements", "format": fmt,
                                               "device_id": str(device_id) if device_id else None,
                                               "group_id": group_id, "from": iso(fr), "to": iso(to), "rows": n},
                 request)
    bind = db.get_bind()
    stamp = datetime.utcnow().strftime("%Y%m%d_%H%M")
    if fmt == "csv":
        def gen():
            buf = io.StringIO()
            buf.write("﻿")
            w = csv.DictWriter(buf, fieldnames=EXPORT_COLS)
            w.writeheader()
            for i, r in enumerate(_export_rows(bind, where)):
                w.writerow(r)
                if i % 1000 == 999:
                    yield buf.getvalue()
                    buf.seek(0)
                    buf.truncate()
            yield buf.getvalue()
        return StreamingResponse(gen(), media_type="text/csv; charset=utf-8",
                                 headers={"Content-Disposition": _attachment(f"field_measurements_{stamp}.csv")})

    def gen_geo():
        yield '{"type":"FeatureCollection","features":['
        first = True
        for r in _export_rows(bind, where):
            if r["lat"] is None:
                continue
            if r["location_precision"] == "coarse":
                la, lo, h = round(r["lat"], 2), round(r["lon"], 2), 0.005
                geom = {"type": "Polygon", "coordinates": [[[lo - h, la - h], [lo + h, la - h], [lo + h, la + h],
                                                            [lo - h, la + h], [lo - h, la - h]]]}
            else:
                geom = {"type": "Point", "coordinates": [r["lon"], r["lat"]]}
            props = {k: v for k, v in r.items() if k not in ("lat", "lon")}
            yield ("" if first else ",") + json.dumps({"type": "Feature", "geometry": geom, "properties": props},
                                                      ensure_ascii=False)
            first = False
        yield "]}"
    return StreamingResponse(gen_geo(), media_type="application/geo+json",
                             headers={"Content-Disposition": _attachment(f"field_measurements_{stamp}.geojson")})


@router.get("/export/measurements.csv")
def export_measurements_csv(request: Request, device_id: Optional[uuid.UUID] = None, group_id: Optional[int] = None,
                            dfrom: Optional[str] = Query(None, alias="from"),
                            dto: Optional[str] = Query(None, alias="to"), bbox: Optional[str] = None,
                            db: Session = Depends(get_db), user: User = Depends(AO)):
    return _export_measurements("csv", request, device_id, group_id, dfrom, dto, bbox, db, user)


@router.get("/export/measurements.geojson")
def export_measurements_geojson(request: Request, device_id: Optional[uuid.UUID] = None,
                                group_id: Optional[int] = None, dfrom: Optional[str] = Query(None, alias="from"),
                                dto: Optional[str] = Query(None, alias="to"), bbox: Optional[str] = None,
                                db: Session = Depends(get_db), user: User = Depends(AO)):
    return _export_measurements("geojson", request, device_id, group_id, dfrom, dto, bbox, db, user)


@router.get("/export/heatmap.geojson")
def export_heatmap(bbox: str, measure: Literal["lte_rsrp", "nr_ss_rsrp"],
                   dfrom: Optional[str] = Query(None, alias="from"), dto: Optional[str] = Query(None, alias="to"),
                   operator: Optional[str] = None, metric: Literal["p50", "p5", "tier", "n", "oos_pct"] = "p50",
                   level: Literal["100", "500", "2500"] = "100", db: Session = Depends(get_db),
                   user: User = Depends(ANY)):
    fr, to = _window(dfrom, dto, None)
    hm = _heat_cells(db, user, _bbox(bbox, True), fr, to, operator, measure, metric, int(level))
    dlat, dlon = hm["cell_deg"]
    feats = []
    for row in hm["rows"]:
        rec = dict(zip(hm["fields"], row))
        gx, gy = rec["gx"], rec["gy"]
        s, w = gy * dlat, gx * dlon
        feats.append({"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [[
            [w, s], [w + dlon, s], [w + dlon, s + dlat], [w, s + dlat], [w, s]]]}, "properties": rec})
    body = {"type": "FeatureCollection", "features": feats,
            "properties": {k: hm[k] for k in ("level", "measure", "metric", "from", "to", "suppressed",
                                               "includes_coarse")}}
    return Response(json.dumps(body, ensure_ascii=False), media_type="application/geo+json",
                    headers={"Content-Disposition": _attachment(f"field_heatmap_{measure}_{metric}.geojson")})


@router.get("/export/kpis.csv")
def export_kpis(by: Literal["operator", "tech", "band", "cell", "group"] = "operator",
                dfrom: Optional[str] = Query(None, alias="from"), dto: Optional[str] = Query(None, alias="to"),
                bbox: Optional[str] = None, operator: Optional[str] = None, group_id: Optional[int] = None,
                fair: bool = False, db: Session = Depends(get_db), user: User = Depends(ANY)):
    if _is_viewer(user) and (by == "group" or group_id is not None):
        raise _forbid()
    fr, to = _window(dfrom, dto, settings.FIELD_RAW_RETENTION_DAYS)
    rows, _sup = _breakdown_rows(db, user, by, fr, to, _bbox(bbox), operator, group_id, fair)
    buf = io.StringIO()
    buf.write("﻿")
    cols = ["key", "label", "n", "time_s"] + [f"pct_time_tier{i}" for i in range(5)] + [
        "pct_time_unclassified", "lte_rsrp_p5", "lte_rsrp_p50", "lte_n", "nr_sa_rsrp_p5", "nr_sa_rsrp_p50", "nr_sa_n",
        "nsa_nr_leg_p50", "oos_min", "cell_changes_per_h", "nsa_share", "stale_excluded_pct"] + (
        [] if _is_viewer(user) else ["n_devices"])
    w = csv.writer(buf)
    w.writerow(cols)
    for r in rows:
        t = r["pct_time_by_tier"]
        w.writerow([r["key"], r["label"], r["n"], r["time_s"], *t, r["pct_time_unclassified"],
                    r["rsrp"]["lte"]["p5"], r["rsrp"]["lte"]["p50"], r["rsrp"]["lte"]["n"],
                    r["rsrp"]["nr_sa"]["p5"], r["rsrp"]["nr_sa"]["p50"], r["rsrp"]["nr_sa"]["n"],
                    r["rsrp"]["nsa_nr_leg"]["p50"], r["oos_min"], r["cell_changes_per_h"], r["nsa_share"],
                    r["stale_excluded_pct"]] + ([] if _is_viewer(user) else [r["n_devices"]]))
    return Response(buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": _attachment(f"field_kpis_{by}.csv")})


# ── audit, operators, maintenance ─────────────────────────────────────────────
@router.get("/audit")
def audit(action: Optional[str] = None, user_id: Optional[int] = None, device_id: Optional[uuid.UUID] = None,
          dfrom: Optional[str] = Query(None, alias="from"), dto: Optional[str] = Query(None, alias="to"),
          limit: int = Query(500, ge=1, le=5000), db: Session = Depends(get_db), _u: User = Depends(A)):
    fr, to = _window(dfrom, dto, None, default_hours=24 * 30)
    q = db.query(ActivityLog).filter(ActivityLog.action.like("FIELD\\_%", escape="\\"),
                                     ActivityLog.created_at >= fr, ActivityLog.created_at < to)
    if action:
        q = q.filter(ActivityLog.action.in_(_csv_list(action)))
    if user_id is not None:
        q = q.filter(ActivityLog.user_id == user_id)
    if device_id is not None:
        q = q.filter(ActivityLog.detail.like(f"%{device_id}%"))
    users = _usernames(db)
    out = []
    for r in q.order_by(ActivityLog.created_at.desc()).limit(limit).all():
        try:
            detail: Any = json.loads(r.detail) if r.detail else None
        except ValueError:
            detail = r.detail
        out.append({"id": r.id, "action": r.action, "user_id": r.user_id, "username": users.get(r.user_id),
                    "detail": detail, "ip_address": r.ip_address, "created_at": iso(r.created_at)})
    return out


@router.get("/operators")
def operators(db: Session = Depends(get_db), _u: User = Depends(ANY)):
    return [{"operator_key": o.operator_key, "name_he": o.name_he, "cell_sites_operator": o.cell_sites_operator,
             "note": o.note} for o in db.query(FieldOperatorMap).order_by(FieldOperatorMap.operator_key).all()]


@router.post("/maintenance/run")
def maintenance_run(task: Optional[str] = None, db: Session = Depends(get_db), _u: User = Depends(A)):
    tasks = _csv_list(task) or list(ALL_TASKS)
    bad = set(tasks) - set(ALL_TASKS)
    if bad:
        raise HTTPException(422, detail=f"task: {', '.join(ALL_TASKS)} (partitioning אינו ממומש)")
    return run_maintenance(db, tasks)


# ── Demo fleet (labelled group "דמו") – works on whichever database the server uses ──────────
def _demo_guard() -> None:
    if not settings.FIELD_DEMO_ENABLED:
        raise HTTPException(404, detail="נתוני דמו מנוטרלים בשרת זה")


@router.get("/demo")
def demo_status(db: Session = Depends(get_db), _u: User = Depends(A)):
    from app.services.field_demo import DEMO_GROUP, _demo_devices
    return {"enabled": bool(settings.FIELD_DEMO_ENABLED), "group": DEMO_GROUP,
            "devices": len(_demo_devices(db)) if settings.FIELD_DEMO_ENABLED else 0}


@router.post("/demo/seed")
def demo_seed(request: Request, hours: float = Query(8.0, ge=1.0, le=24.0),
              db: Session = Depends(get_db), user: User = Depends(A)):
    _demo_guard()
    from app.services.field_demo import seed_demo
    res = seed_demo(db, hours=hours)
    log_activity(db, user.id, "FIELD_DEMO_SEED", {"devices": res.get("devices"), "samples": res.get("samples")}, request)
    return res


@router.delete("/demo")
def demo_remove(request: Request, db: Session = Depends(get_db), user: User = Depends(A)):
    _demo_guard()
    from app.services.field_demo import remove_demo
    res = remove_demo(db)
    log_activity(db, user.id, "FIELD_DEMO_REMOVE", res if isinstance(res, dict) else {}, request)
    return res
