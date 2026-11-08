"""Which terms can be planned, decided by date via SQL term_registration(today) (D12)."""
from datetime import date

from pydantic import BaseModel, computed_field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import get_settings


def term_label(term) -> str:
    """The one place a term is named for people: "Spring 2027". `term` is a Term row or anything with season and
    year. Codes (S27) stay in URLs, the database and logs."""
    return f"{term.season} {term.year}"


def term_labels(db: Session) -> dict[str, str]:
    """term_code -> "Spring 2027" for every term (one query, for lists)."""
    return {r.term_code: f"{r.season} {r.year}" for r in db.execute(text("SELECT term_code, season, year FROM terms"))}


def label_of(db: Session, term_code: str) -> str:
    """term_label for a term code; an unknown code is returned as it is."""
    row = db.execute(text("SELECT season, year FROM terms WHERE term_code = :c"), {"c": term_code}).first()
    return f"{row.season} {row.year}" if row else term_code


class TermStatus(BaseModel):
    term_code: str
    season: str
    year: int
    seq: int
    registration_opens: date
    registration_closes: date
    status: str  # 'open' | 'closed' | 'not_open_yet'
    has_offerings: bool

    @computed_field
    @property
    def plannable(self) -> bool:
        return self.status == "open" and self.has_offerings

    @computed_field
    @property
    def term_label(self) -> str:
        return term_label(self)


def get_today() -> date:
    """FastAPI dependency for "today" (APP_TODAY or the real date); tests override it."""
    return get_settings().today()


def registration_status(db: Session, today: date, from_year: int | None = None) -> list[TermStatus]:
    """Every term (optionally from `from_year` on) with its registration status on `today`, by seq."""
    rows = db.execute(text("SELECT * FROM term_registration(:today)"), {"today": today}).mappings()
    terms = [TermStatus(**row) for row in rows]
    return terms if from_year is None else [t for t in terms if t.year >= from_year]


def plannable_terms(db: Session, today: date) -> list[TermStatus]:
    """Terms a student can plan on `today`: registration open and course offerings exist."""
    return [t for t in registration_status(db, today) if t.plannable]


class TermNotPlannable(ValueError):
    """A schedule was requested for a term whose registration is not open today (or has no offerings)."""


def plannable_status(terms: list[TermStatus], term_code: str, today: date) -> TermStatus:
    """The term's status if it can be planned on `today`; otherwise TermNotPlannable with the reason."""
    term = next((t for t in terms if t.term_code == term_code), None)
    if term is None:
        raise TermNotPlannable(f"Unknown term {term_code}")
    if term.status != "open":
        why = "is closed" if term.status == "closed" else f"opens on {term.registration_opens}"
        raise TermNotPlannable(f"{term.term_label} cannot be planned on {today}: registration {why}")
    if not term.has_offerings:
        raise TermNotPlannable(f"{term.term_label} cannot be planned yet: no course data")
    return term


def require_plannable(db: Session, term_code: str, today: date) -> TermStatus:
    return plannable_status(registration_status(db, today), term_code, today)


class TermPicker(BaseModel):
    """What the term picker shows: terms to plan now, then what is coming, then what just closed."""

    today: date
    plannable: list[TermStatus]  # registration open and course offerings exist
    upcoming: list[TermStatus]  # not open yet (or open but no course data yet: has_offerings is false)
    recent_closed: list[TermStatus]  # the last two closed terms


def term_picker(db: Session, today: date) -> TermPicker:
    terms = registration_status(db, today)
    closed = [t for t in terms if t.status == "closed"]
    return TermPicker(
        today=today, plannable=[t for t in terms if t.plannable],
        upcoming=[t for t in terms if not t.plannable and t.status in ("open", "not_open_yet")][:3],
        recent_closed=closed[-2:])
