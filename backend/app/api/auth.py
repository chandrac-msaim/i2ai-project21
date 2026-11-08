"""POST /auth/login, GET /auth/me."""
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth import CurrentUser, create_token, get_current_user, verify_password
from app.db import get_db
from app.models import AuditLog, User
from app.services.program_info import (
    ProgramInfo,
    UniversityInfo,
    program_of_student,
    programs_of_advisor,
    university_of_advisor,
)

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    email: str
    password: str


class MeResponse(CurrentUser):
    """The logged-in user plus the university and program every screen and email reads its names from."""
    university: UniversityInfo | None = None
    programs: list[ProgramInfo] = []


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: CurrentUser


@router.post("/login", response_model=LoginResponse)
def login(body: LoginRequest, db: Annotated[Session, Depends(get_db)]) -> LoginResponse:
    user = db.scalar(select(User).where(func.lower(User.email) == body.email.strip().lower()))
    if user is None or not user.is_active or not verify_password(body.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Wrong email or password")

    user.last_login_at = datetime.now(UTC)
    db.add(AuditLog(actor_type="user", actor_id=str(user.user_id), action="login",
                    entity_type="user", entity_id=str(user.user_id), details={"role": user.role}))
    db.commit()
    current = CurrentUser(user_id=user.user_id, email=user.email, role=user.role,
                          student_id=user.student_id, advisor_id=user.advisor_id)
    return LoginResponse(access_token=create_token(user), user=current)


@router.get("/me", response_model=MeResponse)
def me(user: Annotated[CurrentUser, Depends(get_current_user)], db: Annotated[Session, Depends(get_db)]) -> MeResponse:
    base = user.model_dump()
    if user.role == "student" and user.student_id:
        program = program_of_student(db, user.student_id)
        return MeResponse(**base, university=program.university, programs=[program])
    if user.role == "advisor" and user.advisor_id:
        return MeResponse(**base, university=university_of_advisor(db, user.advisor_id),
                          programs=programs_of_advisor(db, user.advisor_id))
    return MeResponse(**base)  # an administrator belongs to no single university
