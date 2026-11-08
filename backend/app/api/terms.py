"""GET /terms: terms from the current year on, with registration status for today."""
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import CurrentUser, get_current_user
from app.db import get_db
from app.services.terms import TermPicker, TermStatus, get_today, registration_status, term_picker

router = APIRouter(tags=["terms"])


class TermsResponse(BaseModel):
    today: date
    terms: list[TermStatus]


@router.get("/terms", response_model=TermsResponse)
def list_terms(
    _user: Annotated[CurrentUser, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    today: Annotated[date, Depends(get_today)],
) -> TermsResponse:
    return TermsResponse(today=today, terms=registration_status(db, today, from_year=today.year))


@router.get("/terms/plannable", response_model=TermPicker)
def plannable(
    _user: Annotated[CurrentUser, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    today: Annotated[date, Depends(get_today)],
) -> TermPicker:
    """The term picker: terms open for planning today (registration open and course data exists), then the
    upcoming ones (not open yet, or open without course data) and the last two closed ones."""
    return term_picker(db, today)
