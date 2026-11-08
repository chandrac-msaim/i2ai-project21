"""GET /health: database, Ollama, the current term and the terms plannable today."""
from datetime import date
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.models import Term
from app.services.terms import get_today, plannable_terms

router = APIRouter(tags=["health"])


class Health(BaseModel):
    status: str
    db: bool
    ollama: bool
    current_term: str | None = None
    today: date
    plannable_terms: list[str] = []
    env: str = "production"  # APP_ENV; the login page shows the demo logins only for "development"


def _ollama_reachable() -> bool:
    try:
        return httpx.get(f"{get_settings().ollama_url}/api/tags", timeout=2).status_code == 200
    except httpx.HTTPError:
        return False


@router.get("/health", response_model=Health)
def health(db: Annotated[Session, Depends(get_db)], today: Annotated[date, Depends(get_today)]) -> Health:
    current, plannable = None, []
    try:
        current = db.scalar(select(Term.term_code).where(Term.is_current))
        plannable = [t.term_code for t in plannable_terms(db, today)]
        db_ok = True
    except SQLAlchemyError:
        db_ok = False
    return Health(status="ok" if db_ok else "degraded", db=db_ok, ollama=_ollama_reachable(),
                  current_term=current, today=today, plannable_terms=plannable,
                  env=get_settings().app_env)
