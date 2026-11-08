"""M8b: the portal serves several universities. Names come from the program; an advisor sees only their university."""
from types import SimpleNamespace

from sqlalchemy import text

from app.models import Student
from app.services.access import can_access_student
from app.services.program_info import program_of_student, programs_of_advisor, university_of_advisor
from tests.conftest import PRIYA, auth_header

PRIYA_ID = "S0056"


def test_a_students_program_names_the_university_and_college(db):
    p = program_of_student(db, PRIYA_ID)
    assert p.university.name == "Carnegie Mellon University" and p.university.short_name == "CMU"
    assert p.college == "Heinz College" and p.title == f"{p.degree} in {p.name}" and p.title.startswith("Master of Science in")


def test_every_program_and_advisor_belongs_to_a_university(db):
    assert db.scalar(text("SELECT count(*) FROM programs WHERE university_code IS NULL")) == 0
    assert db.scalar(text("SELECT count(*) FROM advisors WHERE university_code IS NULL")) == 0
    assert university_of_advisor(db, "ADV001").name == "Carnegie Mellon University"
    assert [p.college for p in programs_of_advisor(db, "ADV001")] == ["Heinz College"]


def test_me_returns_the_university_and_program(client):
    me = client.get("/auth/me", headers=auth_header(client, PRIYA)).json()
    assert me["university"]["name"] == "Carnegie Mellon University"
    assert me["programs"][0]["college"] == "Heinz College" and me["role"] == "student"
    advisor = client.get("/auth/me", headers=auth_header(client, "morgan.lee@demo.graydi.local")).json()
    assert advisor["university"]["short_name"] == "CMU" and advisor["programs"]


def test_health_reports_the_environment_for_the_login_page(client):
    assert client.get("/health").json()["env"] in ("development", "production")


def test_an_advisor_sees_only_students_of_their_own_university(api):
    db = api.db
    morgan = SimpleNamespace(role="advisor", student_id=None, advisor_id="ADV001")  # what the token gives
    assert can_access_student(db, morgan, PRIYA_ID) is True
    before = api.get("morgan", "/advisor/caseload").json()
    assert PRIYA_ID in {r["student_id"] for r in before["rows"]}
    # Morgan moves to another university: her students (still assigned to her) are no longer hers to see
    db.execute(text("INSERT INTO universities (university_code, name, short_name) VALUES ('ZZU', 'Zeta University', 'ZZU')"))
    db.execute(text("UPDATE advisors SET university_code = 'ZZU' WHERE advisor_id = 'ADV001'"))
    db.expire_all()
    assert db.get(Student, PRIYA_ID).advisor_id == "ADV001"
    assert can_access_student(db, morgan, PRIYA_ID) is False
    after = api.get("morgan", "/advisor/caseload").json()
    assert after["rows"] == [] and after["university"]["name"] == "Zeta University"


# ---------- M8c: the university is named wherever a case or person is shown ----------

CMU = "Carnegie Mellon University"


def test_dashboard_names_the_university_and_the_terms_in_the_program(api):
    s = api.get("priya", "/students/me/dashboard").json()["student"]
    assert s["university_name"] == CMU == s["program"]["university"]["name"]
    assert s["program"]["college"] == "Heinz College" and s["program"]["title"].startswith("Master of Science in")
    assert s["academic_terms"] == 3  # "Term 2 of 3": the 3 comes from the track, not from the screen


def test_caseload_rows_carry_university_and_program(api):
    body = api.get("morgan", "/advisor/caseload").json()
    assert body["university"]["name"] == CMU and body["rows"]
    for row in body["rows"]:
        assert row["university_name"] == CMU and row["program_title"].startswith("Master of Science in")


def test_review_header_data_has_university_college_program_and_term_label(api):
    sid = api.post("priya", "/schedules/build", json={"term_code": "S27"}).json()["schedule_id"]
    assert api.post("priya", f"/schedules/{sid}/submit").status_code == 200
    page = api.get("morgan", f"/advisor/schedules/{sid}").json()
    st = page["student"]
    assert st["university_name"] == CMU and st["program"]["college"] == "Heinz College"
    assert st["program"]["title"].startswith("Master of Science in") and st["student_id"] == PRIYA_ID
    assert page["schedule"]["term_label"] == "Spring 2027" and st["qpa"] is not None  # the rest of the header line
    assert "units_completed" in st


def test_footer_data_is_the_logged_in_users_university(client):
    for email in (PRIYA, "morgan.lee@demo.graydi.local"):
        me = client.get("/auth/me", headers=auth_header(client, email)).json()
        assert me["university"]["name"] == CMU and me["programs"][0]["college"] == "Heinz College"


def test_a_student_of_another_university_gets_that_universitys_name_in_every_email(api):
    db = api.db
    sid = api.post("priya", "/schedules/build", json={"term_code": "S27"}).json()["schedule_id"]
    assert api.post("priya", f"/schedules/{sid}/submit").status_code == 200
    # the program (and Morgan, who advises it) now belong to another university
    db.execute(text("INSERT INTO universities (university_code, name, short_name) VALUES ('ZZU', 'Zeta University', 'ZZU')"))
    db.execute(text("UPDATE programs SET university_code = 'ZZU' WHERE program_code = 'AIM'"))
    db.execute(text("UPDATE advisors SET university_code = 'ZZU' WHERE advisor_id = 'ADV001'"))
    db.expire_all()
    opening = "Dear Priya, Zeta University Artificial Intelligence Systems Management advising has approved your Spring 2027 schedule."
    footer = "Zeta University, Heinz College, Master of Science in Artificial Intelligence Systems Management"

    preview = api.post("morgan", f"/advisor/schedules/{sid}/email-preview", json={"decision": "approve", "comment": "Well done."}).json()
    assert preview["subject"] == "Zeta University: Your Spring 2027 schedule was approved"
    assert preview["text"].startswith(opening) and preview["text"].rstrip().endswith(footer)
    assert "Zeta University" in preview["html"] and "Carnegie" not in preview["html"] + preview["text"]
    rejected = api.post("morgan", f"/advisor/schedules/{sid}/email-preview", json={"decision": "reject", "comment": "Change X."}).json()
    assert rejected["subject"] == "Zeta University: Your Spring 2027 schedule needs changes"
    assert rejected["text"].startswith("Dear Priya, Zeta University Artificial Intelligence Systems Management advising has reviewed")

    done = api.post("morgan", f"/advisor/schedules/{sid}/decision", json={"decision": "approve", "comment": "Well done."})
    assert done.status_code == 200, done.text
    mail = api.sent[-1]
    assert mail["subject"] == "Zeta University: Your Spring 2027 schedule was approved" == done.json()["email_subject"]
    assert mail["body"].startswith(opening) and mail["body"].rstrip().endswith(footer)  # plain-text fallback
    assert "Zeta University · Heinz College" in mail["html"] and "Carnegie" not in mail["html"] + mail["body"]
    assert mail["cc"] == '"Dr. Morgan Lee" <morgan.lee@demo.graydi.local>'  # the advisor copy
