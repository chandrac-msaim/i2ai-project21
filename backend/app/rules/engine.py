"""The rule engine: 7 deterministic checks on a candidate schedule. No LLM, no FastAPI, no database.

Rules decide (DESIGN.md section 2). Facts come in through a RuleContext built by app.rules.repository.

Result convention (CheckResult):
- passed=True                         the rule holds (PREREQ may still carry flag='conditional')
- passed=False, severity='block'      the schedule is invalid until fixed
- passed=False, other severity        a flag for the advisor (petition, special approval); not blocking
"""
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, time
from decimal import Decimal
from itertools import combinations

from pydantic import BaseModel

BLOCK = "block"
CODES = ("UNIT_LIMIT", "PREREQ", "OFFERED", "LEVEL", "APPROVAL", "TIME_CONFLICT", "PRIVACY")
_DEFAULT_SEVERITY = {"UNDERGRAD_CAP": "medium", "NEEDS_APPROVAL": "info", "CONDITIONAL_PREREQ": "low"}


# ---------- input / output ----------

class ScheduleItemIn(BaseModel):
    course_code: str
    section_id: str | None = None
    units: Decimal | None = None  # None = the course's default units


class CandidateSchedule(BaseModel):
    student_id: str
    term_code: str
    items: list[ScheduleItemIn]


class CheckResult(BaseModel):
    code: str  # one of CODES
    passed: bool
    severity: str  # block | high | medium | low | info
    detail: str
    course_code: str | None = None
    flag: str | None = None  # 'conditional' | 'needs_petition' | 'needs_approval' | 'unit_overload'

    @property
    def blocking(self) -> bool:
        return not self.passed and self.severity == BLOCK


# ---------- facts (built by the repository, or by hand in tests) ----------

@dataclass(frozen=True)
class Meeting:
    day: str  # M T W R F S U
    start: time
    end: time


@dataclass(frozen=True)
class SectionInfo:
    section_id: str
    course_code: str
    term_code: str
    session_code: str
    session_start: date
    session_end: date
    status: str  # offered | cancelled
    seats_left: int | None  # None = no capacity limit
    is_time_tba: bool = False
    meetings: tuple[Meeting, ...] = ()


@dataclass(frozen=True)
class CourseInfo:
    course_code: str
    title: str
    course_number: int
    units_default: Decimal | None
    type_code: str | None = "regular"
    needs_approval: bool = False
    # OR-of-ANDs: the course passes if every course of any one option is done
    prereq_options: tuple[frozenset[str], ...] = ()
    catalog_prereqs_apply: bool = True  # False = program sequence replaces catalog prerequisites
    prereq_override_note: str | None = None

    @property
    def is_undergraduate(self) -> bool:
        return self.course_number < 600


@dataclass
class RuleContext:
    student_id: str
    track_code: str
    term_code: str
    term_registration: str | None  # 'open' | 'closed' | 'not_open_yet'; None = unknown term
    max_units_per_term: Decimal
    max_undergrad_units: Decimal
    max_units_extended: Decimal | None = None  # None = no extended limit (same as max_units_per_term)
    extended_units_min_qpa: Decimal | None = None  # cumulative QPA must be strictly above this for 55..extended
    qpa: Decimal | None = None  # cumulative QPA; None = no grades yet
    completed: set[str] = field(default_factory=set)  # passed courses + approved waivers
    in_progress: set[str] = field(default_factory=set)  # in progress in an earlier term than term_code
    prior_undergrad_units: Decimal = Decimal(0)  # undergrad units already taken (count toward the cap)
    courses: dict[str, CourseInfo] = field(default_factory=dict)
    offered_courses: set[str] = field(default_factory=set)  # course_offerings.status='offered' in term_code
    sections: dict[str, SectionInfo] = field(default_factory=dict)
    severities: dict[str, str] = field(default_factory=dict)  # rule_settings.rule_code -> severity
    term_label: str = ""  # "Spring 2027"; text for people uses this, never the code

    def severity(self, rule_code: str) -> str:
        return self.severities.get(rule_code) or _DEFAULT_SEVERITY.get(rule_code, BLOCK)

    @property
    def term_name(self) -> str:
        return self.term_label or self.term_code

    def units_of(self, item: ScheduleItemIn) -> Decimal:
        if item.units is not None:
            return item.units
        course = self.courses.get(item.course_code)
        return course.units_default if course and course.units_default is not None else Decimal(0)


# ---------- the 7 checks ----------

def check_unit_limit(schedule: CandidateSchedule, ctx: RuleContext) -> list[CheckResult]:
    """<= standard limit (54) ok; up to the extended limit (60) only with a QPA strictly above the threshold
    (passes with flag 'unit_overload' for the advisor); above the extended limit never."""
    total = sum((ctx.units_of(i) for i in schedule.items), Decimal(0))
    limit = ctx.max_units_per_term
    extended = max(ctx.max_units_extended or limit, limit)
    if total <= limit:
        return [CheckResult(code="UNIT_LIMIT", passed=True, severity="info",
                            detail=f"{_n(total)} units planned; limit is {_n(limit)} per term")]
    severity = ctx.severity("UNIT_LIMIT")
    if total > extended:
        return [CheckResult(code="UNIT_LIMIT", passed=False, severity=severity,
                            detail=f"{_n(total)} units planned; never more than {_n(extended)} per term")]
    threshold = ctx.extended_units_min_qpa
    if ctx.qpa is not None and threshold is not None and ctx.qpa > threshold:
        return [CheckResult(code="UNIT_LIMIT", passed=True, severity="info", flag="unit_overload",
                            detail=f"{_n(total)} units planned: above {_n(limit)}, allowed up to {_n(extended)} "
                                   f"because the QPA {ctx.qpa:.2f} is above {threshold:.2f}")]
    return [CheckResult(code="UNIT_LIMIT", passed=False, severity=severity,
                        detail=f"{_n(total)} units planned; limit is {_n(limit)}. Above {_n(limit)} units needs a "
                               f"QPA above {threshold:.2f}" if threshold is not None else
                               f"{_n(total)} units planned; limit is {_n(limit)} per term")]


def check_prereq(schedule: CandidateSchedule, ctx: RuleContext) -> list[CheckResult]:
    results = []
    for item in schedule.items:
        course = ctx.courses.get(item.course_code)
        r = CheckResult(code="PREREQ", passed=True, severity="info", course_code=item.course_code, detail="")
        if course is None:
            r.passed, r.severity, r.detail = False, BLOCK, "Course not found in the catalog"
        elif not course.catalog_prereqs_apply:
            r.detail = f"Program sequence applies instead of catalog prerequisites: {course.prereq_override_note}"
        elif not course.prereq_options:
            r.detail = "No prerequisites"
        elif met := _first_option(course.prereq_options, ctx.completed):
            r.detail = f"Prerequisites met: {_join(met)}"
        elif met := _first_option(course.prereq_options, ctx.completed | ctx.in_progress):
            r.severity, r.flag = ctx.severity("CONDITIONAL_PREREQ"), "conditional"
            r.detail = (f"Conditional course: needs {_join(met & ctx.in_progress)} (in progress) "
                        "to be completed first")
        else:
            options = " or ".join(f"({_join(o, ' and ')})" for o in course.prereq_options)
            r.passed, r.severity, r.detail = False, BLOCK, f"Prerequisites not met; needs {options}"
        results.append(r)
    return results


def check_offered(schedule: CandidateSchedule, ctx: RuleContext) -> list[CheckResult]:
    reg = ctx.term_registration
    if reg != "open":
        why = {"closed": "is closed", "not_open_yet": "is not open yet"}.get(reg or "", "is unknown")
        return [CheckResult(code="OFFERED", passed=False, severity=BLOCK,
                            detail=f"Registration for {ctx.term_name} {why}")]
    results = []
    for item in schedule.items:
        problem = _offering_problem(item, ctx)
        results.append(CheckResult(
            code="OFFERED", passed=problem is None, severity=BLOCK if problem else "info",
            course_code=item.course_code,
            detail=problem or f"Offered in {ctx.term_name}, section {item.section_id} has seats"))
    return results


def check_level(schedule: CandidateSchedule, ctx: RuleContext) -> list[CheckResult]:
    results = []
    planned = Decimal(0)
    for item in schedule.items:
        course = ctx.courses.get(item.course_code)
        if course is None:
            continue
        if course.is_undergraduate:
            planned += ctx.units_of(item)
            results.append(CheckResult(
                code="LEVEL", passed=False, severity=ctx.severity("UNDERGRAD_CAP"),
                course_code=item.course_code, flag="needs_petition",
                detail="Undergraduate course: needs a petition approved by the advisor"))
        else:
            results.append(CheckResult(code="LEVEL", passed=True, severity="info",
                                       course_code=item.course_code, detail="Graduate course"))
    if planned:
        total = ctx.prior_undergrad_units + planned
        cap = ctx.max_undergrad_units
        results.append(CheckResult(
            code="LEVEL", passed=total <= cap, severity="info" if total <= cap else BLOCK,
            detail=f"{_n(total)} undergraduate units in total (including earlier terms); at most "
                   f"{_n(cap)} count toward the degree"))
    return results


def check_approval(schedule: CandidateSchedule, ctx: RuleContext) -> list[CheckResult]:
    results = []
    for item in schedule.items:
        course = ctx.courses.get(item.course_code)
        if course is None:
            continue
        if course.needs_approval:
            results.append(CheckResult(
                code="APPROVAL", passed=False, severity=ctx.severity("NEEDS_APPROVAL"),
                course_code=item.course_code, flag="needs_approval",
                detail=f"{course.type_code or 'This course'} needs special approval; the advisor decides"))
        else:
            results.append(CheckResult(code="APPROVAL", passed=True, severity="info",
                                       course_code=item.course_code, detail="No special approval needed"))
    return results


def check_time_conflict(schedule: CandidateSchedule, ctx: RuleContext) -> list[CheckResult]:
    sections = [ctx.sections[i.section_id] for i in schedule.items if i.section_id in ctx.sections]
    results = [
        CheckResult(code="TIME_CONFLICT", passed=False, severity=BLOCK, course_code=a.course_code,
                    detail=f"{a.section_id} ({a.session_code}) clashes with {b.section_id} "
                           f"({b.session_code}) on {m.day} {m.start:%H:%M}-{m.end:%H:%M}")
        for a, b in combinations(sections, 2)
        if (m := clash(a, b)) is not None
    ]
    return results or [CheckResult(code="TIME_CONFLICT", passed=True, severity="info",
                                   detail="No overlapping sections")]


def check_privacy(schedule: CandidateSchedule, ctx: RuleContext) -> list[CheckResult]:
    # Placeholder: name removal happens in the LLM client; this records that it is required.
    return [CheckResult(code="PRIVACY", passed=True, severity="info",
                        detail=f"Personal names are removed before any LLM call; the student is "
                               f"referred to as {schedule.student_id}")]


CHECKS: tuple[Callable[[CandidateSchedule, RuleContext], list[CheckResult]], ...] = (
    check_unit_limit, check_prereq, check_offered, check_level,
    check_approval, check_time_conflict, check_privacy,
)


def validate(schedule: CandidateSchedule, ctx: RuleContext) -> list[CheckResult]:
    """Run all 7 checks. Every check code appears at least once in the result."""
    results = [r for check in CHECKS for r in check(schedule, ctx)]
    seen = {r.code for r in results}
    results += [CheckResult(code=c, passed=True, severity="info", detail="No courses to check")
                for c in CODES if c not in seen]
    return results


def is_valid(results: list[CheckResult]) -> bool:
    """Valid = no blocking failure. Flags (petition, approval, conditional) do not block."""
    return not any(r.blocking for r in results)


# ---------- helpers ----------

def clash(a: SectionInfo, b: SectionInfo) -> Meeting | None:
    """The first meeting of `a` that overlaps `b`: sessions overlap by date, same day, times overlap."""
    if a.is_time_tba or b.is_time_tba:
        return None
    if a.session_end < b.session_start or b.session_end < a.session_start:
        return None
    for ma in a.meetings:
        for mb in b.meetings:
            if ma.day == mb.day and ma.start < mb.end and mb.start < ma.end:
                return ma
    return None


def _offering_problem(item: ScheduleItemIn, ctx: RuleContext) -> str | None:
    if item.course_code not in ctx.offered_courses:
        return f"Not offered in {ctx.term_name}"
    if item.section_id is None:
        return "No section chosen"
    section = ctx.sections.get(item.section_id)
    if section is None or section.course_code != item.course_code or section.term_code != ctx.term_code:
        return f"Section {item.section_id} is not a {ctx.term_name} section of {item.course_code}"
    if section.status != "offered":
        return f"Section {item.section_id} is cancelled"
    if section.seats_left is not None and section.seats_left <= 0:
        return f"Section {item.section_id} is full"
    return None


def _first_option(options: tuple[frozenset[str], ...], done: set[str]) -> frozenset[str] | None:
    return next((o for o in options if o <= done), None)


def _join(codes: frozenset[str] | set[str], sep: str = ", ") -> str:
    return sep.join(sorted(codes))


def _n(units: Decimal) -> str:
    return f"{units.normalize():f}"
