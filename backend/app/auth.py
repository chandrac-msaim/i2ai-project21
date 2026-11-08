"""Password hashing, JWT tokens and role dependencies."""
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.models import User

ALGORITHM = "HS256"
bearer = HTTPBearer(auto_error=False)


class CurrentUser(BaseModel):
    user_id: uuid.UUID
    email: str
    role: str
    student_id: str | None = None
    advisor_id: str | None = None


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except ValueError:
        return False


def create_token(user: User) -> str:
    settings = get_settings()
    claims = {
        "sub": str(user.user_id),
        "email": user.email,
        "role": user.role,
        "student_id": user.student_id,
        "advisor_id": user.advisor_id,
        "exp": datetime.now(UTC) + timedelta(minutes=settings.jwt_expire_minutes),
    }
    return jwt.encode(claims, settings.jwt_secret, algorithm=ALGORITHM)


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(status.HTTP_401_UNAUTHORIZED, detail, headers={"WWW-Authenticate": "Bearer"})


def decode_token(token: str) -> CurrentUser:
    try:
        claims = jwt.decode(token, get_settings().jwt_secret, algorithms=[ALGORITHM])
    except jwt.PyJWTError as exc:
        raise _unauthorized("Invalid or expired token") from exc
    return CurrentUser(
        user_id=claims["sub"],
        email=claims["email"],
        role=claims["role"],
        student_id=claims.get("student_id"),
        advisor_id=claims.get("advisor_id"),
    )


def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    db: Annotated[Session, Depends(get_db)],
) -> CurrentUser:
    if credentials is None:
        raise _unauthorized("Not authenticated")
    user = decode_token(credentials.credentials)
    row = db.get(User, user.user_id)
    if row is None or not row.is_active:
        raise _unauthorized("User is inactive or no longer exists")
    return user


def require_role(*roles: str):
    """Dependency factory: Depends(require_role("advisor")) -> 403 for any other role."""

    def checker(user: Annotated[CurrentUser, Depends(get_current_user)]) -> CurrentUser:
        if user.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Requires role: {', '.join(roles)}")
        return user

    return checker
