"""
/api/field — probe-device endpoints (device token only; spec 3.3, contract v2/v2.1).

Registered in main.py BEFORE app.api.field so that /devices/enroll,
/devices/heartbeat and /devices/me/* win over /devices/{device_id}.
"""
import json
import logging
import uuid
import zlib
from datetime import datetime, timedelta
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core import ratelimit
from app.core.audit import log_activity
from app.core.config import settings
from app.core.database import get_db
from app.core.device_auth import (
    FieldHTTPError, get_current_device, hash_secret, new_device_token, service_unavailable,
)
from app.models.field import FieldDevice, FieldDeviceCommand, FieldEnrollmentCode
from app.services.field_config import effective_config
from app.services.field_ingest import EnvelopeError, apply_heartbeat, ingest_batch
from app.services.field_time import iso, parse_iso

log = logging.getLogger(__name__)
router = APIRouter()

MAX_SAMPLES = 500
ENROLL_RETRY_WINDOW = timedelta(minutes=15)


def _naive(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is not None:
        from datetime import timezone
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _rate(key: str, limit: tuple) -> None:
    retry = ratelimit.take(key, *limit)
    if retry is not None:
        raise FieldHTTPError(429, "יותר מדי בקשות — נסו שוב מאוחר יותר", "rate_limited",
                             {"Retry-After": str(retry)})


def normalize_code(code: str) -> str:
    return "".join(ch for ch in code.upper() if ch not in "- \t")


def completes_by(now: datetime) -> datetime:
    interval = settings.FIELD_MAINTENANCE_INTERVAL_S or 300
    return now + timedelta(seconds=max(interval, 60)) + timedelta(minutes=5)


# ── enroll ────────────────────────────────────────────────────────────────────
class EnrollIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enrollment_code: str = Field(..., min_length=1, max_length=32)
    device_uuid: uuid.UUID
    model: str = Field(..., max_length=64)
    manufacturer: str = Field(..., max_length=64)
    os_version: str = Field(..., max_length=64)
    app_version: str = Field(..., max_length=64)
    ownership: Optional[Literal["ORG", "PERSONAL"]] = None
    label: Optional[str] = Field(None, max_length=64)
    consent_version: Optional[str] = Field(None, max_length=32)
    consented_at: Optional[datetime] = None


@router.post("/devices/enroll", status_code=201)
def enroll(body: EnrollIn, request: Request, db: Session = Depends(get_db)):
    ip = request.client.host if request.client else "unknown"
    _rate(f"enroll:{ip}", ratelimit.ENROLL)

    def fail(status: int, msg: str, code: str):
        ratelimit.record_failure(f"enroll:{ip}", 5, 600, 900)
        raise FieldHTTPError(status, msg, code)

    now = datetime.utcnow()
    try:
        code = db.query(FieldEnrollmentCode).filter(
            FieldEnrollmentCode.code_hash == hash_secret(normalize_code(body.enrollment_code))).first()
        if code is None or code.revoked_at is not None:
            fail(400, "קוד צירוף לא תקף", "code_invalid")

        existing = db.query(FieldDevice).filter(FieldDevice.device_uuid == body.device_uuid).first()
        if existing is not None:
            # v2.1 rule 6: same (code, device_uuid) within 15 min → same result, new token
            # (previous one invalidated) — only while the issued token was never used.
            if (existing.enrolled_by_code_id == code.id and existing.status == "active"
                    and existing.enrolled_at and now - existing.enrolled_at <= ENROLL_RETRY_WINDOW
                    and existing.token_used_at is None):
                token, token_hash = new_device_token()
                existing.token_hash, existing.token_issued_at = token_hash, now
                db.commit()
                return {"device_id": str(existing.public_id), "device_token": token,
                        "ownership": existing.ownership, "config": effective_config(db, existing)}
            fail(409, "הקוד כבר נוצל", "code_used")

        if code.expires_at <= now:
            fail(410, "קוד הצירוף פג תוקף", "code_expired")
        if code.use_count >= code.max_uses:
            fail(409, "הקוד כבר נוצל", "code_used")
        if body.ownership is not None and body.ownership != code.ownership:
            fail(409, f"סוג המכשיר אינו תואם לקוד (הקוד מיועד ל-{code.ownership})", "ownership_mismatch")
        consented_at = _naive(body.consented_at)
        if code.ownership == "PERSONAL":
            if not body.consent_version or consented_at is None:
                raise FieldHTTPError(422, "במכשיר אישי נדרשים consent_version ו-consented_at",
                                     "consent_required")
            if consented_at > now + timedelta(minutes=5) or consented_at < now - timedelta(days=7):
                raise FieldHTTPError(422, "consented_at אינו סביר (בעתיד או ישן מ-7 ימים)",
                                     "consent_required")

        # atomic use_count++ (one-time codes: concurrent enrolls → exactly one wins)
        res = db.execute(update(FieldEnrollmentCode)
                         .where(FieldEnrollmentCode.id == code.id,
                                FieldEnrollmentCode.use_count < FieldEnrollmentCode.max_uses)
                         .values(use_count=FieldEnrollmentCode.use_count + 1))
        if (res.rowcount or 0) != 1:
            db.rollback()
            fail(409, "הקוד כבר נוצל", "code_used")

        token, token_hash = new_device_token()
        dev = FieldDevice(
            public_id=uuid.uuid4(), device_uuid=body.device_uuid, label=body.label,
            ownership=code.ownership, consent_version=body.consent_version, consented_at=consented_at,
            group_id=code.group_id, config_version=1, config_changed_at=now,
            model=body.model, manufacturer=body.manufacturer, os_version=body.os_version,
            app_version=body.app_version, token_hash=token_hash, token_issued_at=now,
            status="active", enrolled_at=now, enrolled_by_code_id=code.id)
        db.add(dev)
        db.flush()
        log_activity(db, code.created_by, "FIELD_DEVICE_ENROLLED",
                     {"device_id": str(dev.public_id), "ownership": dev.ownership,
                      "consent_version": dev.consent_version}, request, commit=False)
        db.commit()
        return {"device_id": str(dev.public_id), "device_token": token, "ownership": dev.ownership,
                "config": effective_config(db, dev)}
    except OperationalError:
        db.rollback()
        raise service_unavailable()


# ── measurements/batch ────────────────────────────────────────────────────────
def _read_json_body(raw: bytes, encoding: str):
    if encoding == "gzip":
        if len(raw) > settings.FIELD_MAX_BATCH_COMPRESSED_BYTES:
            raise FieldHTTPError(413, "גוף הבקשה גדול מדי", "payload_too_large")
        d = zlib.decompressobj(16 + zlib.MAX_WBITS)
        try:
            data = d.decompress(raw, settings.FIELD_MAX_BATCH_BYTES + 1)
        except zlib.error:
            raise FieldHTTPError(422, "gzip פגום", "invalid_envelope")
        if len(data) > settings.FIELD_MAX_BATCH_BYTES or d.unconsumed_tail:
            raise FieldHTTPError(413, "גוף הבקשה גדול מדי אחרי פריסה", "payload_too_large")
    elif encoding in ("", "identity"):
        if len(raw) > settings.FIELD_MAX_BATCH_BYTES:
            raise FieldHTTPError(413, "גוף הבקשה גדול מדי", "payload_too_large")
        data = raw
    else:
        raise FieldHTTPError(415, f"Content-Encoding לא נתמך: {encoding}", "unsupported_encoding")
    try:
        return json.loads(data)
    except (ValueError, UnicodeDecodeError):
        raise FieldHTTPError(422, "JSON לא תקין", "invalid_envelope")


ENVELOPE_KEYS = {"device_id", "sent_at", "config_version", "samples"}


@router.post("/measurements/batch")
async def measurements_batch(request: Request, device: FieldDevice = Depends(get_current_device),
                             db: Session = Depends(get_db)):
    _rate(f"batch:{device.id}", ratelimit.BATCH)
    raw = await request.body()
    body = _read_json_body(raw, request.headers.get("content-encoding", "").strip().lower())
    if not isinstance(body, dict):
        raise FieldHTTPError(422, "מעטפה לא תקינה: נדרש אובייקט", "invalid_envelope")
    extra = set(body) - ENVELOPE_KEYS
    if extra:
        raise FieldHTTPError(422, f"מעטפה לא תקינה: שדות לא מוכרים {sorted(extra)}", "invalid_envelope")
    try:
        dev_id = uuid.UUID(str(body.get("device_id")))
    except ValueError:
        raise FieldHTTPError(422, "מעטפה לא תקינה: device_id", "invalid_envelope")
    sent_at = parse_iso(body.get("sent_at"))
    if sent_at is None:
        raise FieldHTTPError(422, "מעטפה לא תקינה: sent_at", "invalid_envelope")
    cv = body.get("config_version")
    if isinstance(cv, bool) or not isinstance(cv, int):
        raise FieldHTTPError(422, "מעטפה לא תקינה: config_version", "invalid_envelope")
    samples = body.get("samples")
    if not isinstance(samples, list) or not samples:
        raise FieldHTTPError(422, "מעטפה לא תקינה: samples (1–500)", "invalid_envelope")
    if len(samples) > MAX_SAMPLES:
        raise FieldHTTPError(413, f"עד {MAX_SAMPLES} דגימות בבקשה", "too_many_samples")
    if dev_id != device.public_id:
        raise FieldHTTPError(403, "device_id אינו תואם לטוקן", "device_mismatch")
    try:
        return ingest_batch(db, device, samples, sent_at)
    except EnvelopeError as e:
        db.rollback()
        raise FieldHTTPError(422, f"מעטפה לא תקינה: {e}", "invalid_envelope")
    except OperationalError:
        db.rollback()
        raise service_unavailable()


# ── heartbeat ─────────────────────────────────────────────────────────────────
class PermissionsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    location: Literal["precise", "approximate", "none"]
    background_location: bool
    phone_state: bool
    notifications: bool
    battery_optimization_exempt: bool


class AckIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: uuid.UUID
    status: Literal["done", "skipped_paused", "failed", "expired"]


class HeartbeatIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ts: datetime
    battery_pct: int = Field(..., ge=0, le=100)
    is_charging: bool
    app_version: str = Field(..., max_length=64)
    queue_size: int = Field(..., ge=0)
    permissions: PermissionsIn
    monitoring_state: Literal["active", "paused_by_user", "paused_schedule", "degraded"]
    degraded_reasons: Optional[List[str]] = Field(None, max_length=32)
    effective_location_mode: Optional[Literal["precise", "coarse", "off"]] = None
    location_services_enabled: Optional[bool] = None
    applied_config_version: Optional[int] = Field(None, ge=0)
    evicted_samples_total: Optional[int] = Field(None, ge=0)
    last_sample_ts: Optional[datetime] = None
    acked_commands: Optional[List[AckIn]] = Field(None, max_length=100)


@router.post("/devices/heartbeat")
def heartbeat(body: HeartbeatIn, device: FieldDevice = Depends(get_current_device),
              db: Session = Depends(get_db)):
    _rate(f"hb:{device.id}", ratelimit.HEARTBEAT)
    hb = body.model_dump()
    hb["ts"] = _naive(body.ts)
    hb["last_sample_ts"] = _naive(body.last_sample_ts)
    hb["degraded_reasons"] = [str(r)[:64] for r in (body.degraded_reasons or [])]
    hb["acked_commands"] = [{"id": str(a.id), "status": a.status} for a in body.acked_commands or []]
    try:
        return apply_heartbeat(db, device, hb)
    except OperationalError:
        db.rollback()
        raise service_unavailable()


# ── me/config, me/withdraw ────────────────────────────────────────────────────
@router.get("/devices/me/config")
def my_config(request: Request, response: Response, device: FieldDevice = Depends(get_current_device),
              db: Session = Depends(get_db)):
    etag = f'"{device.config_version}"'
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag})
    response.headers["ETag"] = etag
    return effective_config(db, device)


class WithdrawIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: Optional[str] = Field(None, max_length=128)


@router.post("/devices/me/withdraw")
async def withdraw(request: Request, device: FieldDevice = Depends(get_current_device),
                   db: Session = Depends(get_db)):
    raw = await request.body()
    reason = None
    if raw.strip():
        try:
            reason = WithdrawIn.model_validate_json(raw).reason
        except Exception:
            raise FieldHTTPError(422, "גוף לא תקין: {reason?: string ≤ 128}", "invalid_envelope")
    if device.ownership != "PERSONAL":
        # v2.1 rule 5: withdraw is PERSONAL-only; ORG devices are removed by an ADMIN
        raise FieldHTTPError(403, "ביטול הסכמה זמין רק למכשיר אישי; הסרת מכשיר ארגוני נעשית ע\"י מנהל",
                             "withdraw_not_allowed")
    now = datetime.utcnow()
    device.token_hash = None
    device.status = "withdrawn"
    device.withdrawn_at = now
    device.purge_mode = "delete"
    device.purged_at = None
    db.query(FieldDeviceCommand).filter(
        FieldDeviceCommand.device_pk == device.id,
        FieldDeviceCommand.status.in_(("queued", "delivered"))).update(
        {FieldDeviceCommand.status: "cancelled"}, synchronize_session=False)
    log_activity(db, None, "FIELD_DEVICE_WITHDRAWN",
                 {"device_id": str(device.public_id), "ownership": device.ownership,
                  "data_action": "delete", "reason": reason}, request, commit=False)
    db.commit()
    return {"status": "withdrawn", "data_action": "delete", "completes_by": iso(completes_by(now))}
