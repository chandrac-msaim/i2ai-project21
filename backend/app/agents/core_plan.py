"""Locked-core selection shared by Agent 2 (clash flags) and Agent 3 (the draft), so both see the same core.

Remaining core courses offered in the term are taken in recommended-term order while they fit under the unit
limit; then one section per course without a time clash (depth-first, best sections first). Pure functions:
no database, no LLM.
"""
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal

from app.rules.engine import Meeting, SectionInfo, clash
from app.tools.catalog import SectionOut
from app.tools.student import StudentHistory

_TIME_OF_DAY = {"morning": (0, 12), "afternoon": (12, 17), "evening": (17, 24)}


@dataclass(frozen=True)
class CoreCandidate:
    course_code: str
    units: Decimal
    seq: int | None  # recommended_term_seq


@dataclass
class CorePlan:
    picks: list[CoreCandidate] = field(default_factory=list)
    chosen: dict[str, SectionOut] = field(default_factory=dict)  # course_code -> section
    skipped: dict[str, str] = field(default_factory=dict)  # course_code -> why it is not in the plan

    @property
    def units(self) -> Decimal:
        return sum((c.units for c in self.picks), Decimal(0))


def pick_core(candidates: Sequence[CoreCandidate], sections: dict[str, list[SectionOut]],
              limit: Decimal) -> CorePlan:
    """Earliest recommended term first, while the total stays <= `limit`; clash-free sections."""
    plan = CorePlan()
    total = Decimal(0)
    for c in sorted(candidates, key=lambda c: (c.seq or 99, c.course_code)):
        if total + c.units <= limit:
            plan.picks.append(c)
            total += c.units
        else:
            plan.skipped[c.course_code] = f"Would exceed the {limit.normalize():f}-unit limit; take it in a later term"
    chosen = assign_sections([c.course_code for c in plan.picks], sections)
    while plan.picks and chosen is None:
        dropped = plan.picks.pop()
        plan.skipped[dropped.course_code] = "No section without a time clash"
        chosen = assign_sections([c.course_code for c in plan.picks], sections)
    plan.chosen = chosen or {}
    return plan


def assign_sections(codes: list[str], sections: dict[str, list[SectionOut]],
                    chosen: dict[str, SectionOut] | None = None) -> dict[str, SectionOut] | None:
    """One section per course with no time clash (depth-first, best sections first); None if impossible."""
    chosen = dict(chosen or {})
    if not codes:
        return chosen
    code, rest = codes[0], codes[1:]
    for section in sections.get(code, []):
        if any(clash(section_info(section), section_info(other)) for other in chosen.values()):
            continue
        result = assign_sections(rest, sections, chosen | {code: section})
        if result is not None:
            return result
    return None


def clashes_with(section: SectionOut, others: Sequence[SectionOut]) -> bool:
    return any(clash(section_info(section), section_info(o)) for o in others)


def section_info(s: SectionOut) -> SectionInfo:
    return SectionInfo(section_id=s.section_id, course_code=s.course_code, term_code=s.term_code,
                       session_code=s.session_code, session_start=s.session_start, session_end=s.session_end,
                       status=s.status, seats_left=s.seats_left, is_time_tba=s.is_time_tba,
                       meetings=tuple(Meeting(m.day, m.start, m.end) for m in s.meetings))


def preference_key(s: SectionOut, history: StudentHistory, low_seats: int) -> tuple:
    """Sort key, best section first: seats, preferred modality, preferred time of day, known times, most seats."""
    nearly_full = s.seats_left is not None and s.seats_left < low_seats
    hours = _TIME_OF_DAY.get(history.preferred_time_of_day or "")
    in_window = sum(1 for m in s.meetings if hours and hours[0] <= m.start.hour < hours[1])
    return (nearly_full, s.modality != history.preferred_modality, -in_window, s.is_time_tba,
            -(s.seats_left if s.seats_left is not None else 999), s.section_code)


def usable(s: SectionOut) -> bool:
    """Offered and with seats (no capacity limit counts as having seats, as in the rule engine)."""
    return s.status == "offered" and (s.seats_left is None or s.seats_left > 0)
