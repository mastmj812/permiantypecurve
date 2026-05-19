"""Password hashing + JWT issue/verify.

Single-user today; structure is generic enough to layer SSO on top later
(swap out the password-verify step, keep the JWT format).

Algorithm choices:
  * passwords — bcrypt via passlib (cost factor 12, the default).
  * tokens   — HS256, signed with settings.jwt_secret.
                30-day TTL by default. Single-user dev tool, no rotation.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.config import settings

ALGORITHM: str = "HS256"
ACCESS_TOKEN_TTL = timedelta(days=30)

_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(plaintext: str) -> str:
    return _pwd.hash(plaintext)


def verify_password(plaintext: str, hashed: str) -> bool:
    try:
        return _pwd.verify(plaintext, hashed)
    except Exception:
        return False


def create_access_token(subject: str, *, extra: dict[str, Any] | None = None) -> str:
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": subject,
        "iat": int(now.timestamp()),
        "exp": int((now + ACCESS_TOKEN_TTL).timestamp()),
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.jwt_secret, algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict[str, Any] | None:
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=[ALGORITHM])
    except JWTError:
        return None
