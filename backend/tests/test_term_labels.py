"""M8b: a term is named for people as "Spring 2027", built in one place. Codes (S27) stay in URLs, the database, logs."""
from datetime import date
from types import SimpleNamespace

from app.agents.base import ungrounded_numbers
from app.agents.compliance_agent import compose_email, template_paragraph
from app.services.terms import label_of, registration_status, term_label, term_labels
from app.tools.schedules import ContactOut, ScheduleItemOut, ScheduleOut

NOV_7 = date(2026, 11, 7)


def test_term_label_is_season_and_year():
    assert term_label(SimpleNamespace(season="Spring", year=2027)) == "Spring 2027"
    assert term_label(SimpleNamespace(season="Summer", year=2026)) == "Summer 2026"


def test_every_term_has_a_label_that_is_not_its_code(db):
    labels = term_labels(db)
    assert labels["S27"] == "Spring 2027" and labels["F26"] == "Fall 2026" and labels["M27"] == "Summer 2027"
    assert all(label != code for code, label in labels.items())
    assert label_of(db, "S27") == "Spring 2027" and label_of(db, "NOPE") == "NOPE"  # unknown codes are kept as they are


def test_term_status_from_the_api_layer_carries_the_label(db):
    for t in registration_status(db, NOV_7):
        assert t.term_label == f"{t.season} {t.year}"
        assert t.model_dump()["term_label"] == t.term_label  # it is part of the JSON the screens receive


def test_the_llm_guard_accepts_the_year_when_the_facts_name_the_term():
    facts = "The draft for Spring 2027 has 48 units (limit 54)."
    assert ungrounded_numbers("Your Spring 2027 draft has 48 units.", facts) == []
    assert ungrounded_numbers("Your Spring 2028 draft has 48 units.", facts) == ["2028"]  # an invented year is caught


def _schedule(label="Spring 2027") -> ScheduleOut:
    import uuid
    from decimal import Decimal
    item = ScheduleItemOut(course_code="94-815", title="Agentic Technologies", section_id="94-815-S27-A4",
                           section_code="A4", units=Decimal("6"), item_type="core", is_locked=True,
                           is_conditional=False, needs_approval=False)
    return ScheduleOut(schedule_id=uuid.uuid4(), student_id="S0056", term_code="S27", term_label=label, version=1,
                       status="submitted", advisor_comment=None, items=[item], total_units=Decimal("6"))


def test_email_text_names_the_term_and_the_university():
    contact = ContactOut(student_id="S0056", first_name="Priya", email="p@x", advisor_id="ADV001",
                         advisor_name="Dr. Lee", university_name="Carnegie Mellon University",
                         college="Heinz College", program_title="Master of Science in AI Systems Management",
                         program_name="AI Systems Management")
    body = compose_email(contact, _schedule(), True, template_paragraph(_schedule(), True), "See you in week 3.")
    assert body.startswith("Dear Priya, Carnegie Mellon University AI Systems Management advising has approved your Spring 2027 schedule.")
    assert "Schedule for Spring 2027 (6 units)" in body and "approved your course schedule for Spring 2027" in body
    assert body.rstrip().endswith("Carnegie Mellon University, Heinz College, Master of Science in AI Systems Management")
    assert "S27" not in body.replace("94-815-S27", "")


def test_rejection_text_opens_with_the_university_and_carries_the_comment():
    contact = ContactOut(student_id="S0056", first_name="Priya", email="p@x", advisor_id="ADV001", advisor_name="Dr. Lee",
                         university_name="Zeta University", college="Faculty of Data",
                         program_title="Master of Data Science", program_name="Data Science")
    body = compose_email(contact, _schedule(), False, template_paragraph(_schedule(), False), "Please take 95-888 first.")
    lines = body.split("\n")
    assert lines[0] == "Dear Priya, Zeta University Data Science advising has reviewed your Spring 2027 schedule and it needs changes."
    assert "Advisor comment: Please take 95-888 first." in body
    assert lines[-1] == "Zeta University, Faculty of Data, Master of Data Science"  # footer
    assert "Carnegie" not in body
