"""Rule-engine and settings tools. Agents read pass/fail from here; they never decide eligibility themselves."""
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import RuleSetting
from app.rules.engine import CandidateSchedule, CheckResult
from app.rules.repository import course_blockers, validate_schedule as _validate_schedule
from app.services.terms import TermStatus, registration_status


def validate_schedule(db: Session, schedule: CandidateSchedule, today: date) -> list[CheckResult]:
    """All 7 checks on a candidate schedule (see app.rules.engine)."""
    return _validate_schedule(db, schedule, today)


def check_course_eligibility(db: Session, student_id: str, term_code: str, course_codes: list[str],
                             today: date) -> dict[str, list[CheckResult]]:
    """course_code -> blocking failures if the course were taken in `term_code` (empty list = eligible)."""
    return course_blockers(db, student_id, term_code, course_codes, today)


def get_term_registration(db: Session, today: date) -> list[TermStatus]:
    """Every term with its registration status on `today` and whether it can be planned."""
    return registration_status(db, today)


def get_rule_settings(db: Session) -> dict[str, dict[str, Any]]:
    """rule_code -> params for every active rule setting (score weights, retries, thresholds)."""
    return {r.rule_code: dict(r.params) for r in db.scalars(select(RuleSetting).where(RuleSetting.active))}
