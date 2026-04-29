from typing import List
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from datetime import datetime

from app.core.database import get_db
from app.core.security import require_role, get_current_user, hash_password
from app.models.user import User, UserRole

router = APIRouter()


class UserOut(BaseModel):
    id:         int
    username:   str
    full_name:  str
    role:       str
    is_active:  bool
    last_login: datetime | None
    created_at: datetime

    class Config:
        from_attributes = True


class UserUpdate(BaseModel):
    full_name:  str | None = None
    role:       UserRole | None = None
    is_active:  bool | None = None
    password:   str | None = None


class UserCreate(BaseModel):
    username:   str
    full_name:  str
    password:   str
    role:       UserRole = UserRole.OPERATOR


@router.post("/", response_model=UserOut, status_code=201)
def create_user(
    body: UserCreate,
    db: Session = Depends(get_db),
    _: User = Depends(require_role("ADMIN")),
):
    if db.query(User).filter(User.username == body.username).first():
        raise HTTPException(409, detail="שם משתמש כבר קיים")
    user = User(
        username=body.username,
        full_name=body.full_name,
        hashed_pw=hash_password(body.password),
        role=body.role,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@router.get("/", response_model=List[UserOut])
def list_users(
    db: Session = Depends(get_db),
    _: User = Depends(require_role("ADMIN")),
):
    return db.query(User).all()


@router.put("/{user_id}", response_model=UserOut)
def update_user(
    user_id: int,
    body: UserUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(require_role("ADMIN")),
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(404, detail="משתמש לא נמצא")

    if body.full_name  is not None: user.full_name = body.full_name
    if body.role       is not None: user.role = body.role
    if body.is_active  is not None: user.is_active = body.is_active
    if body.password   is not None: user.hashed_pw = hash_password(body.password)

    db.commit()
    db.refresh(user)
    return user


@router.delete("/{user_id}", status_code=204)
def delete_user(
    user_id: int,
    db: Session = Depends(get_db),
    current: User = Depends(require_role("ADMIN")),
):
    if current.id == user_id:
        raise HTTPException(400, detail="לא ניתן למחוק את עצמך")
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(404, detail="משתמש לא נמצא")
    db.delete(user)
    db.commit()
