from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.core.database import get_db
from app.core.security import (
    verify_password, create_access_token,
    hash_password, get_current_user
)
from app.models.user import User, UserRole, ActivityLog

router = APIRouter()


# ── Schemas ───────────────────────────────────────────────────────────────────
class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: int
    username: str
    full_name: str
    role: str


class RegisterRequest(BaseModel):
    username: str
    full_name: str
    password: str
    role: UserRole = UserRole.OPERATOR


# ── Routes ────────────────────────────────────────────────────────────────────
@router.post("/login", response_model=TokenResponse)
def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
    request: Request = None,
):
    user = db.query(User).filter(User.username == form_data.username).first()

    if not user or not verify_password(form_data.password, user.hashed_pw):
        # Log failed attempt
        _log(db, None, "LOGIN_FAILED", f"username={form_data.username}",
             request.client.host if request else None)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="שם משתמש או סיסמה שגויים",
        )

    if not user.is_active:
        raise HTTPException(status_code=403, detail="חשבון מושהה")

    # Update last_login
    user.last_login = datetime.utcnow()
    db.commit()

    token = create_access_token({"sub": str(user.id)})
    _log(db, user.id, "LOGIN_SUCCESS", None, request.client.host if request else None)

    return TokenResponse(
        access_token=token,
        user_id=user.id,
        username=user.username,
        full_name=user.full_name,
        role=user.role.value,
    )


@router.post("/register", status_code=201)
def register(
    body: RegisterRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Only ADMIN can register new users."""
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="רק מנהל יכול להוסיף משתמשים")

    if db.query(User).filter(User.username == body.username).first():
        raise HTTPException(status_code=409, detail="שם משתמש כבר קיים")

    user = User(
        username=body.username,
        full_name=body.full_name,
        hashed_pw=hash_password(body.password),
        role=body.role,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return {"id": user.id, "username": user.username, "role": user.role}


@router.get("/me")
def me(current_user: User = Depends(get_current_user)):
    return {
        "id": current_user.id,
        "username": current_user.username,
        "full_name": current_user.full_name,
        "role": current_user.role,
        "last_login": current_user.last_login,
    }


# ── Helpers ───────────────────────────────────────────────────────────────────
def _log(db, user_id, action, detail, ip):
    db.add(ActivityLog(user_id=user_id, action=action, detail=detail, ip_address=ip))
    db.commit()
