"""Auth endpoints + the get_current_user dependency.

POST /api/auth/login    body: {email, password} → {access_token, user}
POST /api/auth/logout   no-op server side (client clears the token)
GET  /api/auth/me       returns the authenticated user
"""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.core.security import (
    create_access_token,
    decode_access_token,
    verify_password,
)
from app.db.models import User
from app.db.session import get_session

router = APIRouter(prefix="/auth", tags=["auth"])
log = get_logger("api.auth")

bearer_scheme = HTTPBearer(auto_error=False)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class UserOut(BaseModel):
    id: uuid.UUID
    email: str
    display_name: str | None
    last_login_at: datetime | None


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


@router.post("/login", response_model=LoginResponse)
def login(req: LoginRequest, session: Session = Depends(get_session)) -> LoginResponse:
    user = session.execute(select(User).where(User.email == req.email.lower())).scalar_one_or_none()
    # Constant-ish-time: always run verify_password even on missing user so a
    # response timing doesn't leak which emails exist. Single-user dev tool
    # so this is mostly belt-and-suspenders.
    valid_hash = user.password_hash if user and user.password_hash else "$2b$12$" + "x" * 53
    is_ok = verify_password(req.password, valid_hash) and user is not None
    if not is_ok or user is None:
        log.info("login_failed", email=req.email)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid credentials",
        )
    user.last_login_at = datetime.utcnow()
    session.commit()

    token = create_access_token(subject=str(user.id), extra={"email": user.email})
    return LoginResponse(
        access_token=token,
        user=UserOut(
            id=user.id,
            email=user.email,
            display_name=user.display_name,
            last_login_at=user.last_login_at,
        ),
    )


@router.post("/logout", status_code=204)
def logout() -> None:
    """Client clears the token; we don't keep a server-side session table."""
    return None


# ---------------- get_current_user dependency ----------------


def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    session: Session = Depends(get_session),
) -> User:
    if creds is None or not creds.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    payload = decode_access_token(creds.credentials)
    if payload is None or "sub" not in payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        user_id = uuid.UUID(payload["sub"])
    except (ValueError, TypeError) as e:
        raise HTTPException(status_code=401, detail="malformed subject") from e
    user = session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="user no longer exists")
    return user


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)) -> UserOut:
    return UserOut(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        last_login_at=user.last_login_at,
    )
