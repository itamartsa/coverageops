"""
Device authentication for the field probe API (spec 3.2, contract v2 "HTTP").

* Device tokens are `fdt_` + 43 base64url chars (256 bit), stored only as
  sha256(FIELD_TOKEN_PEPPER + token). They are not JWTs: get_current_user
  rejects them (jose cannot decode them) and get_current_device rejects user
  JWTs (wrong prefix / no matching hash).
* 401 ONLY for a missing/invalid/revoked token — the app deletes its local data
  after two 401s, so any database problem inside this dependency is a 503 with
  Retry-After, never a 401.
"""
import hashlib
import logging
import secrets
from typing import Optional

from fastapi import Depends, Request
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db

log = logging.getLogger(__name__)

TOKEN_PREFIX = "fdt_"
device_bearer = HTTPBearer(auto_error=False, scheme_name="DeviceToken",
                           description="Field probe device token (fdt_…) — not a user JWT")


class FieldHTTPError(Exception):
    """Error with the contract body `{detail, detail_code}` (+ optional headers)."""

    def __init__(self, status_code: int, detail: str, detail_code: Optional[str] = None,
                 headers: Optional[dict] = None):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail
        self.detail_code = detail_code
        self.headers = headers or {}


async def field_http_error_handler(_request: Request, exc: FieldHTTPError) -> JSONResponse:
    body = {"detail": exc.detail}
    if exc.detail_code:
        body["detail_code"] = exc.detail_code
    return JSONResponse(status_code=exc.status_code, content=body, headers=exc.headers)


def pepper_is_default() -> bool:
    return settings.FIELD_TOKEN_PEPPER == "CHANGE-ME-FIELD-PEPPER"


def hash_secret(value: str) -> str:
    return hashlib.sha256((settings.FIELD_TOKEN_PEPPER + value).encode("utf-8")).hexdigest()


def new_device_token() -> tuple:
    """(plain token, hash)."""
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    return token, hash_secret(token)


def token_invalid() -> FieldHTTPError:
    return FieldHTTPError(401, "טוקן מכשיר לא תקף", "token_invalid", {"WWW-Authenticate": "Bearer"})


def service_unavailable(retry_after: int = 30) -> FieldHTTPError:
    return FieldHTTPError(503, "השרת אינו זמין זמנית — נסו שוב", "unavailable",
                          {"Retry-After": str(retry_after)})


def get_current_device(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(device_bearer),
    db: Session = Depends(get_db),
):
    from app.models.field import FieldDevice   # local import: models import core

    token = credentials.credentials if credentials else None
    if not token or not token.startswith(TOKEN_PREFIX) or len(token) > 128:
        raise token_invalid()
    try:
        device = db.query(FieldDevice).filter(FieldDevice.token_hash == hash_secret(token)).first()
    except Exception as exc:          # DB down / timeout → 503, never 401
        log.warning("field device auth: database error: %s", exc)
        try:
            db.rollback()
        except Exception:
            pass
        raise service_unavailable()
    if device is None or device.status != "active":
        raise token_invalid()
    return device
