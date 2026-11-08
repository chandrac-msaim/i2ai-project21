"""M4b: schedule lifecycle service (D15). Nothing is committed: the db fixture rolls back at the end."""
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models import AuditLog, Schedule, ScheduleChange
from app.services import schedule_lifecycle as lifecycle
from app.services.schedule_lifecycle import (
    DraftExists,
    InvalidTransition,
    ItemError,
    ItemNotFound,
    ScheduleConflict,
    ScheduleItemDraft,
    ScheduleLocked,
)

STUDENT, TERM = "S0126", "S27"  # Alex: no schedules in the demo data


def items(*codes: str) -> list[ScheduleItemDraft]:
    sections = {"95-719": "95-719-S27-A3", "95-864": "95-864-S27-A3", "95-897": "95-897-S27-A3"}
    return [ScheduleItemDraft(course_code=c, section_id=sections[c], units=Decimal(6), item_type="core",
                              is_locked=c != "95-719", reason=f"why {c}") for c in codes]


def build(db, replace=False, *codes):
    return lifecycle.build(db, STUDENT, TERM, items(*(codes or ("95-864", "95-897"))), ai_explanation="x",
                           load_rationale="y", replace=replace)


def get(db, saved) -> Schedule:
    return db.get(Schedule, saved.schedule_id)


def actions(db, schedule: Schedule) -> list[str]:
    return list(db.scalars(select(ScheduleChange.action).where(ScheduleChange.schedule_id == schedule.schedule_id)
                           .order_by(ScheduleChange.change_id)))


def audit_actions(db, schedule: Schedule) -> list[str]:
    return list(db.scalars(select(AuditLog.action).where(AuditLog.entity_id == str(schedule.schedule_id))
                           .order_by(AuditLog.log_id)))


def decide(db, schedule: Schedule, status: str) -> None:
    schedule.status, schedule.decided_by, schedule.decided_at = status, "ADV001", datetime.now(timezone.utc)
    if schedule.submitted_at is None:
        schedule.submitted_at = datetime.now(timezone.utc)
    db.flush()


def submit(db, schedule: Schedule) -> None:
    schedule.status, schedule.submitted_at = "submitted", datetime.now(timezone.utc)
    db.flush()


# ---------- build ----------

def test_build_creates_version_1_with_history_rows(db):
    saved = build(db)
    s = get(db, saved)
    assert (s.version, s.status, s.previous_schedule_id) == (1, "draft", None)
    assert {i.course_code for i in s.items} == {"95-864", "95-897"} and all(i.added_by == "ai" for i in s.items)
    assert actions(db, s) == ["build"] and audit_actions(db, s) == ["schedule_build"]


def test_build_while_draft_exists_without_replace_raises(db):
    first = build(db)
    with pytest.raises(DraftExists) as err:
        build(db)
    assert err.value.schedule_id == first.schedule_id
    assert get(db, first).status == "draft"  # untouched


def test_build_with_replace_supersedes_and_links(db):
    first = build(db)
    second = build(db, True, "95-719")
    assert second.version == 2 and second.superseded_id == first.schedule_id
    assert get(db, first).status == "superseded"
    assert get(db, second).status == "draft" and get(db, second).previous_schedule_id == first.schedule_id
    assert actions(db, get(db, second)) == ["rebuild"] and audit_actions(db, get(db, second)) == ["schedule_rebuild"]


def test_submitted_schedule_is_never_replaced(db):
    first = build(db)
    submit(db, get(db, first))
    for replace in (False, True):
        with pytest.raises(ScheduleConflict):
            build(db, replace)


# ---------- discard ----------

def test_discard_then_build_again_keeps_both_versions(db):
    first = build(db)
    s = lifecycle.discard(db, get(db, first))
    assert s.status == "discarded" and s.discarded_at is not None  # the row is kept
    assert actions(db, s) == ["build", "discard"]
    again = build(db)  # no DraftExists: the discarded draft is not open
    assert again.version == 2
    versions = db.execute(select(Schedule.version, Schedule.status).where(
        Schedule.student_id == STUDENT, Schedule.term_code == TERM).order_by(Schedule.version)).all()
    assert [tuple(v) for v in versions] == [(1, "discarded"), (2, "draft")]
    assert get(db, again).previous_schedule_id is None  # a discard is not a replacement


@pytest.mark.parametrize("status", ["submitted", "rejected", "superseded", "discarded"])
def test_only_drafts_can_be_discarded(db, status):
    s = get(db, build(db))
    if status == "submitted":
        submit(db, s)
    elif status == "rejected":
        decide(db, s, "rejected")
    elif status == "superseded":
        s.status = "superseded"
    else:
        lifecycle.discard(db, s)
    with pytest.raises(InvalidTransition):
        lifecycle.discard(db, s)


# ---------- withdraw ----------

def test_withdraw_only_from_submitted(db):
    s = get(db, build(db))
    with pytest.raises(InvalidTransition):
        lifecycle.withdraw(db, s)  # still a draft
    submit(db, s)
    lifecycle.withdraw(db, s)
    assert s.status == "draft" and s.submitted_at is None
    assert actions(db, s) == ["build", "withdraw"] and "schedule_withdraw" in audit_actions(db, s)
    with pytest.raises(InvalidTransition):
        lifecycle.withdraw(db, s)  # a draft again


def test_cannot_withdraw_after_a_decision(db):
    s = get(db, build(db))
    decide(db, s, "rejected")
    with pytest.raises(InvalidTransition):
        lifecycle.withdraw(db, s)
    assert s.status == "rejected"


# ---------- approved is locked ----------

def test_student_cannot_change_an_approved_schedule(db):
    s = get(db, build(db))
    decide(db, s, "approved")
    for step in (lifecycle.discard, lifecycle.withdraw, lifecycle.resubmit_from_rejected):
        with pytest.raises(ScheduleLocked):
            step(db, s, by_student=True)
    assert s.status == "approved" and s.discarded_at is None and s.submitted_at is not None
    for replace in (False, True):  # building again does not replace it either
        with pytest.raises(ScheduleLocked):
            build(db, replace)


# ---------- resubmit after rejection ----------

def test_resubmit_from_rejected_copies_items_into_a_new_version(db):
    s = get(db, build(db, False, "95-864", "95-719"))
    decide(db, s, "rejected")
    new = lifecycle.resubmit_from_rejected(db, s)
    assert (new.version, new.status, new.previous_schedule_id) == (2, "draft", s.schedule_id)
    assert {(i.course_code, i.section_id, i.is_locked) for i in new.items} == \
           {(i.course_code, i.section_id, i.is_locked) for i in s.items}
    assert s.status == "rejected"  # history kept
    assert actions(db, new) == ["resubmit"] and audit_actions(db, new) == ["schedule_resubmit"]
    with pytest.raises(DraftExists):
        lifecycle.resubmit_from_rejected(db, s)  # a new draft is already open


def test_resubmit_needs_a_rejected_schedule(db):
    s = get(db, build(db))
    with pytest.raises(InvalidTransition):
        lifecycle.resubmit_from_rejected(db, s)


# ---------- actors ----------

def test_actor_is_recorded(db):
    s = get(db, build(db))
    lifecycle.discard(db, s, by_student=False)
    assert list(db.scalars(select(ScheduleChange.actor).where(ScheduleChange.schedule_id == s.schedule_id)
                           .order_by(ScheduleChange.change_id))) == ["ai", "advisor"]


# ---------- ai_baseline, edits and reset (M6 A2) ----------

def baseline_of(items_: list[ScheduleItemDraft]) -> list[dict]:
    return lifecycle.snapshot(items_)


def current(s: Schedule) -> set[tuple]:
    return {(i.course_code, i.section_id, i.item_type, float(i.units), i.is_locked) for i in s.items}


def test_build_and_rebuild_write_the_ai_baseline(db):
    first = get(db, build(db))
    assert first.ai_baseline == baseline_of(items("95-864", "95-897"))
    assert {b["course_code"] for b in first.ai_baseline} == {"95-864", "95-897"}
    assert set(first.ai_baseline[0]) == {"course_code", "section_id", "item_type", "units", "is_locked",
                                         "is_conditional", "needs_approval", "match_score", "reason"}
    second = get(db, build(db, True, "95-719"))
    assert second.ai_baseline == baseline_of(items("95-719"))  # the rebuild has its own baseline
    assert first.ai_baseline == baseline_of(items("95-864", "95-897"))  # and the old one is untouched


def test_add_remove_swap_then_reset_gives_back_the_ai_draft(db):
    s = get(db, build(db, False, "95-864", "95-719"))  # 95-864 is locked core, 95-719 is an unlocked elective
    ai_draft = current(s)
    assert s.edited_by_student is False

    lifecycle.add_item(db, s, "95-897", "95-897-S27-A3", Decimal(6))
    lifecycle.swap_item(db, s, "95-719", "94-728", None, Decimal(6))
    assert current(s) != ai_draft and s.edited_by_student is True
    assert {i.course_code for i in s.items} == {"95-864", "95-897", "94-728"}
    lifecycle.remove_item(db, s, "95-897")
    assert {i.course_code for i in s.items} == {"95-864", "94-728"}
    assert all(i.added_by == "student" for i in s.items if i.course_code == "94-728")

    changes = db.execute(select(ScheduleChange.action, ScheduleChange.course_code,
                                ScheduleChange.replaced_course_code, ScheduleChange.actor)
                         .where(ScheduleChange.schedule_id == s.schedule_id)
                         .order_by(ScheduleChange.change_id)).tuples().all()
    assert [tuple(c) for c in changes] == [
        ("build", None, None, "ai"), ("add", "95-897", None, "student"),
        ("swap", "94-728", "95-719", "student"),  # the AI's original course is on record
        ("remove", "95-897", None, "student")]

    lifecycle.reset(db, s)
    assert current(s) == ai_draft and s.edited_by_student is False  # exactly the AI draft again
    assert all(i.added_by == "ai" for i in s.items)
    assert actions(db, s)[-1] == "reset" and audit_actions(db, s)[-1] == "schedule_reset"
    lifecycle.reset(db, s)  # re-run safe
    assert current(s) == ai_draft


def test_locked_core_cannot_be_removed_or_swapped_and_duplicates_are_refused(db):
    s = get(db, build(db, False, "95-864", "95-719"))
    with pytest.raises(ItemError):
        lifecycle.remove_item(db, s, "95-864")
    with pytest.raises(ItemError):
        lifecycle.swap_item(db, s, "95-864", "94-728", None, Decimal(6))
    with pytest.raises(ItemError):
        lifecycle.add_item(db, s, "95-719", "95-719-S27-A3", Decimal(6))
    with pytest.raises(ItemError):
        lifecycle.swap_item(db, s, "95-719", "95-864", None, Decimal(6))
    with pytest.raises(ItemNotFound):
        lifecycle.remove_item(db, s, "94-728")
    db.expire(s, ["items"])
    assert {i.course_code for i in s.items} == {"95-864", "95-719"}  # nothing changed
    assert s.edited_by_student is False and actions(db, s) == ["build"]


def test_only_a_draft_can_be_edited_or_reset(db):
    s = get(db, build(db))
    submit(db, s)
    for step in (lambda: lifecycle.add_item(db, s, "95-719", None, Decimal(6)),
                 lambda: lifecycle.remove_item(db, s, "95-897"), lambda: lifecycle.reset(db, s)):
        with pytest.raises(InvalidTransition):
            step()
    decide(db, s, "approved")
    for step in (lambda: lifecycle.add_item(db, s, "95-719", None, Decimal(6)), lambda: lifecycle.reset(db, s)):
        with pytest.raises(ScheduleLocked):
            step()


def test_resubmit_keeps_the_ai_baseline(db):
    s = get(db, build(db, False, "95-864", "95-719"))
    lifecycle.remove_item(db, s, "95-719")
    decide(db, s, "rejected")
    new = lifecycle.resubmit_from_rejected(db, s)
    assert new.ai_baseline == s.ai_baseline and {i.course_code for i in new.items} == {"95-864"}
    lifecycle.reset(db, new)  # back to what the AI built, not to what was rejected
    assert {i.course_code for i in new.items} == {"95-864", "95-719"}
