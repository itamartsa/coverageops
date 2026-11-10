"""
Activity-log helper for the field module (spec 9.3). Writes to the existing
`activity_log` table — the same row shape as api/auth._log, which stays private
to that module and is not changed. `detail` is a JSON string.
"""
import json
from typing import Optional

from fastapi import Request
from sqlalchemy.orm import Session

from app.models.user import ActivityLog


def client_ip(request: Optional[Request]) -> Optional[str]:
    try:
        return request.client.host if request is not None and request.client else None
    except Exception:
        return None


def log_activity(db: Session, user_id: Optional[int], action: str, detail: dict,
                 request: Optional[Request] = None, commit: bool = True) -> None:
    db.add(ActivityLog(user_id=user_id, action=action,
                       detail=json.dumps(detail, ensure_ascii=False, default=str),
                       ip_address=client_ip(request)))
    if commit:
        db.commit()
