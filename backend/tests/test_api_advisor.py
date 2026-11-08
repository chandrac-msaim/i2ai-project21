"""M6 API: advisor caseload / review / recheck / decision, messages, and the role matrix over every route.

Each test runs inside a rolled-back transaction (the `api` fixture); the LLM and the email sender are fakes.
"""
import uuid

import pytest
from sqlalchemy import func, select, text

from app.main import app
from app.models import Notification, Schedule, Student

TERM = "S27"
PRIYA_ID = "S0056"
SEEDED = "Hi Priya, Spring 2027 planning is open. Build your schedule when you are ready and I will review it."


def build(api, who="priya"):
    r = api.post(who, "/schedules/build", json={"term_code": TERM})
    assert r.status_code == 201, r.text
    return r.json()


def submitted(api, who="priya") -> str:
    sid = build(api, who)["schedule_id"]
    r = api.post(who, f"/schedules/{sid}/submit")
    assert r.status_code == 200, r.text
    return sid


def decision(api, sid, decision="approve", comment="Looks good, enjoy the term.", who="morgan"):
    return api.post(who, f"/advisor/schedules/{sid}/decision", json={"decision": decision, "comment": comment})


def fill_a_section(api, sid) -> str:
    view = api.get("priya", f"/schedules/{sid}").json()
    section = next(i["section_id"] for i in view["items"] if api.db.scalar(
        text("SELECT capacity FROM sections WHERE section_id = :s"), {"s": i["section_id"]}) is not None)
    api.db.execute(text("UPDATE sections SET enrolled = capacity WHERE section_id = :s"), {"s": section})
    return section


# ---------- decision email (HTML) ----------

def test_email_preview_is_html_nothing_is_sent_or_decided(api):
    sid = submitted(api)
    sent_before = len(api.sent)
    ok = api.post("morgan", f"/advisor/schedules/{sid}/email-preview",
                  json={"decision": "approve", "comment": "Good plan <script>x</script>"})
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["to"].startswith("Priya Raman <") and body["cc"].startswith("Dr. Morgan Lee <")
    assert body["subject"] == "Carnegie Mellon University: Your Spring 2027 schedule was approved"
    assert body["text"].startswith("Dear Priya, Carnegie Mellon University Artificial Intelligence Systems Management advising has approved your Spring 2027 schedule.")
    assert body["cc"] == "Dr. Morgan Lee <morgan.lee@demo.graydi.local>"
    assert "Approved" in body["html"] and "Your courses" in body["html"] and "Open my schedule" in body["html"]
    assert "<script>" not in body["html"] and "&lt;script&gt;" in body["html"]  # the comment is escaped
    assert len(api.sent) == sent_before  # nothing was sent
    assert api.get("priya", f"/schedules/{sid}").json()["status"] == "submitted"  # and nothing was decided
    rejected = api.post("morgan", f"/advisor/schedules/{sid}/email-preview", json={"decision": "reject"}).json()
    assert "Rejected" in rejected["html"] and "Edit my schedule" in rejected["html"]
    assert rejected["subject"].endswith("Your Spring 2027 schedule needs changes") and "Comment from" not in rejected["html"]
    assert "has reviewed your Spring 2027 schedule and it needs changes." in rejected["text"]


def test_email_preview_only_for_the_students_own_advisor(api):
    sid = submitted(api)
    other = api.post("rachel", f"/advisor/schedules/{sid}/email-preview", json={"decision": "approve"})
    assert other.status_code == 403
    assert api.post("priya", f"/advisor/schedules/{sid}/email-preview", json={"decision": "approve"}).status_code == 403
    assert api.post("morgan", f"/advisor/schedules/{sid}/email-preview", json={"decision": "maybe"}).status_code == 422


def test_the_real_email_has_a_text_part_an_html_part_and_the_advisor_in_cc(api):
    sid = submitted(api)
    assert decision(api, sid, "approve", "See you in week 3.").status_code == 200
    mail = api.sent[-1]
    assert mail["body"].startswith("Dear Priya") and "See you in week 3." in mail["body"]  # plain text stays
    assert mail["html"] and "See you in week 3." in mail["html"] and "Approved" in mail["html"]
    assert mail["cc"] == '"Dr. Morgan Lee" <morgan.lee@demo.graydi.local>'  # the advisor gets a copy, with the name
    assert mail["body"].startswith(f"Dear Priya, {'Carnegie Mellon University'} Artificial Intelligence Systems Management advising")
    assert "Carnegie Mellon University" in mail["html"] and "Carnegie Mellon University" in mail["body"]
    assert "Spring 2027" in mail["html"] and "Spring 2027" in mail["body"]


# ---------- caseload ----------

def test_caseload_for_the_advisor(api):
    empty = api.get("morgan", "/advisor/caseload").json()
    active = api.db.scalar(select(func.count()).select_from(Student).where(Student.advisor_id == "ADV001",
                                                                           Student.status == "active"))
    assert empty["advisor_name"] == "Morgan Lee" and empty["metrics"]["students"] == len(empty["rows"]) == active
    assert 3 <= active < 17  # her graduated students are not on the caseload
    assert empty["metrics"]["awaiting_review"] == 0 and empty["metrics"]["with_risk_flags"] == 0
    assert {"S0056", "S0057", "S0126"} <= {r["student_id"] for r in empty["rows"]}
    assert all(r["schedule"] is None for r in empty["rows"] if r["student_id"] in ("S0056", "S0057", "S0126"))

    priya_sid, daniel_sid = submitted(api), submitted(api, "daniel")
    build(api, "alex")  # a draft, not submitted
    api.post("priya", f"/messages/{PRIYA_ID}", json={"body": "Can we talk about my electives?"})
    body = api.get("morgan", "/advisor/caseload").json()
    rows = {r["student_id"]: r for r in body["rows"]}
    assert body["metrics"]["awaiting_review"] == 2 and body["metrics"]["with_risk_flags"] >= 1
    assert body["metrics"]["unread_messages"] == 1
    daniel = rows["S0057"]
    assert daniel["schedule"]["schedule_id"] == daniel_sid and daniel["schedule"]["status"] == "submitted"
    assert float(daniel["schedule"]["total_units"]) == 48 and daniel["below_min_qpa"] is True
    assert "LOW_QPA" in daniel["flag_codes"] and daniel["top_severity"] == "high" and daniel["open_flags"] >= 1
    priya = rows["S0056"]
    assert priya["schedule"]["schedule_id"] == priya_sid and priya["unread_messages"] == 1
    assert float(priya["qpa"]) > 3.5 and priya["below_min_qpa"] is False and priya["track_code"] == "AIM12"
    assert rows["S0126"]["schedule"]["status"] == "draft" and float(rows["S0126"]["units_completed"]) == 0


def test_caseload_scope_by_role(api):
    total = api.db.scalar(select(func.count()).select_from(Student).where(Student.status == "active"))
    admin = api.get("admin", "/advisor/caseload").json()
    assert admin["metrics"]["students"] == total > 50 and admin["advisor_name"] is None
    rachel = api.get("rachel", "/advisor/caseload").json()
    assert rachel["advisor_name"] == "Rachel Kim" and "S0056" not in {r["student_id"] for r in rachel["rows"]}
    assert api.get("priya", "/advisor/caseload").status_code == 403
    assert api.get(None, "/advisor/caseload").status_code == 401


# ---------- the review page ----------

def test_review_page_shows_the_ai_draft_and_what_the_student_changed(api):
    v = build(api)
    sid = v["schedule_id"]
    ai_elective = next(i["course_code"] for i in v["items"] if not i["is_locked"])
    new = next(c for c in ("95-719", "95-819", "95-881", "94-813") if c not in {i["course_code"] for i in v["items"]})
    api.post("priya", f"/schedules/{sid}/items", json={"action": "swap", "replace_course_code": ai_elective,
                                                      "course_code": new})
    api.post("priya", f"/schedules/{sid}/submit")
    api.post("priya", f"/messages/{PRIYA_ID}", json={"body": "I swapped one elective.", "schedule_id": sid})

    r = api.get("morgan", f"/advisor/schedules/{sid}")
    assert r.status_code == 200, r.text
    page = r.json()
    assert page["student"]["first_name"] == "Priya" and page["student"]["advisor_name"] == "Morgan Lee"
    assert page["student"]["below_min_qpa"] is False and page["schedule"]["status"] == "submitted"
    swapped = next(i for i in page["schedule"]["items"] if i["course_code"] == new)
    assert swapped["added_by"] == "student" and swapped["in_ai_draft"] is False
    assert swapped["ai_original_course"] == ai_elective  # the AI's original course for the swapped row
    assert [x["course_code"] for x in page["removed_from_ai_draft"]] == [ai_elective]
    swap = next(c for c in page["changes"] if c["action"] == "swap")
    assert (swap["course_code"], swap["replaced_course_code"], swap["actor"]) == (new, ai_elective, "student")
    assert swap["course_title"] and swap["replaced_course_title"]
    assert [c["action"] for c in page["changes"]] == ["build", "swap", "submit"]
    assert page["recorded_checks"]["stage"] == "submit" and len(page["recorded_checks"]["checks"]) == 7
    assert len(page["schedule"]["checks"]) == 7 and page["schedule"]["valid"] is True  # live
    assert page["ai_explanation"] and page["load_rationale"].startswith("48 of 54 units")
    assert page["ai_run"]["agent_name"] == "schedule" and "save_schedule" in page["ai_run"]["tools"]
    assert page["ai_run"]["llm_provider"] == "fake"
    assert [m["body"] for m in page["messages"]] == [SEEDED, "I swapped one elective."]
    assert [(x["version"], x["status"]) for x in page["versions"]] == [(1, "submitted")]
    assert isinstance(page["schedule"]["flags"], list)


def test_review_page_access(api):
    sid = submitted(api)
    assert api.get("admin", f"/advisor/schedules/{sid}").status_code == 200
    assert api.get("rachel", f"/advisor/schedules/{sid}").status_code == 403  # not her student
    assert api.get("priya", f"/advisor/schedules/{sid}").status_code == 403
    assert api.get("morgan", f"/advisor/schedules/{uuid.uuid4()}").status_code == 404
    assert api.get(None, f"/advisor/schedules/{sid}").status_code == 401


# ---------- recheck ----------

def test_recheck_reads_seats_again_and_refreshes_flags(api):
    sid = submitted(api, "daniel")
    ok = api.post("morgan", f"/advisor/schedules/{sid}/recheck")
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["status"] == "submitted" and body["can_approve"] is True and body["blocking"] == []
    assert len(body["checks"]) == 7 and "LOW_QPA" in [f["flag_code"] for f in body["flags"]]
    assert api.db.scalar(text("SELECT count(*) FROM risk_flags WHERE schedule_id = :s AND flag_code = 'LOW_QPA' "
                              "AND status = 'open'"), {"s": sid}) == 1  # refreshed, not duplicated

    api.db.execute(text("UPDATE sections SET enrolled = capacity WHERE section_id IN "
                        "(SELECT section_id FROM schedule_items WHERE schedule_id = :s) AND capacity IS NOT NULL"),
                   {"s": sid})
    full = api.post("morgan", f"/advisor/schedules/{sid}/recheck").json()
    assert full["can_approve"] is False and any("is full" in b for b in full["blocking"])
    assert not next(c for c in full["checks"] if c["code"] == "OFFERED")["passed"]
    assert api.get("daniel", f"/schedules/{sid}").json()["status"] == "submitted"  # recheck changes nothing


def test_recheck_needs_a_submitted_schedule_and_an_advisor(api):
    draft = build(api)["schedule_id"]
    assert api.post("morgan", f"/advisor/schedules/{draft}/recheck").status_code == 409
    sid = submitted(api, "daniel")
    for who in ("daniel", "admin", "rachel"):
        assert api.post(who, f"/advisor/schedules/{sid}/recheck").status_code == 403
    assert api.post("morgan", f"/advisor/schedules/{uuid.uuid4()}/recheck").status_code == 404


# ---------- decision ----------

def test_approve_saves_the_decision_and_emails_the_student(api):
    sid = submitted(api)
    r = decision(api, sid)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "approved" and body["email_sent"] is True and body["notification_id"]
    assert body["email_subject"] == "Carnegie Mellon University: Your Spring 2027 schedule was approved"
    schedule = api.db.get(Schedule, uuid.UUID(sid))
    assert (schedule.status, schedule.decided_by, schedule.advisor_comment) == ("approved", "ADV001",
                                                                                "Looks good, enjoy the term.")
    (mail,) = api.sent
    assert mail["to"] == "priya.raman@demo.graydi.local" and mail["subject"] == body["email_subject"]
    text_ = mail["body"]
    assert text_.startswith("Dear Priya,") and "your" in text_.lower()  # written to the student
    assert "the student" not in text_.lower() and "student's" not in text_.lower()
    assert "Advisor comment: Looks good, enjoy the term." in text_
    note = api.db.scalar(select(Notification).where(Notification.schedule_id == uuid.UUID(sid)))
    assert note.status == "sent" and note.template == "schedule_approved"
    approved = api.get("priya", f"/schedules/{sid}").json()
    assert approved["status"] == "approved" and approved["advisor_comment"] and not approved["can_edit"]
    assert decision(api, sid).status_code == 409  # approved is locked
    assert decision(api, sid, "reject").json()["code"] == "schedule_locked"
    assert api.post("priya", "/schedules/build", json={"term_code": TERM}).status_code == 409  # and stays so


def test_reject_emails_the_student_too(api):
    sid = submitted(api)
    r = decision(api, sid, "reject", "Please add a second elective.")
    assert r.status_code == 200 and r.json()["status"] == "rejected"
    (mail,) = api.sent
    assert mail["subject"] == "Carnegie Mellon University: Your Spring 2027 schedule needs changes"
    assert "Please add a second elective." in mail["body"] and "the student" not in mail["body"].lower()
    assert api.get("priya", f"/schedules/{sid}").json()["advisor_comment"] == "Please add a second elective."


def test_approve_is_blocked_when_a_section_became_full(api):
    sid = submitted(api)
    section = fill_a_section(api, sid)
    r = decision(api, sid)
    assert r.status_code == 409
    body = r.json()
    assert body["code"] == "compliance_blocked" and body["stage"] == "approve"
    assert any(x.startswith("OFFERED") and "is full" in x for x in body["reasons"])
    assert api.sent == []  # no email
    assert api.get("priya", f"/schedules/{sid}").json()["status"] == "submitted"
    api.db.execute(text("UPDATE sections SET enrolled = 0 WHERE section_id = :s"), {"s": section})
    assert decision(api, sid).status_code == 200  # the seat is back


def test_decision_permissions_and_errors(api):
    draft = build(api)["schedule_id"]
    assert decision(api, draft).status_code == 409  # still a draft
    sid = submitted(api, "daniel")
    for who in ("daniel", "admin"):
        for kind in ("approve", "reject"):
            assert decision(api, sid, kind, who=who).status_code == 403
    assert decision(api, sid, who="rachel").status_code == 403  # an advisor, but not Daniel's
    assert api.get("daniel", f"/schedules/{sid}").json()["status"] == "submitted"
    assert api.post("morgan", f"/advisor/schedules/{sid}/decision", json={"decision": "maybe"}).status_code == 422
    assert api.post("morgan", f"/advisor/schedules/{sid}/decision", json={}).status_code == 422
    assert decision(api, str(uuid.uuid4())).status_code == 404
    assert api.call(None, "POST", f"/advisor/schedules/{sid}/decision",
                    json={"decision": "approve"}).status_code == 401
    assert api.sent == []


# ---------- messages ----------

def test_message_thread_between_student_and_advisor(api):
    assert api.get("priya", "/students/me/dashboard").json()["unread_messages"] == 1  # the seeded advisor message
    first = api.get("priya", f"/messages/{PRIYA_ID}").json()
    assert first["unread"] == 1 and [m["body"] for m in first["messages"]] == [SEEDED]
    assert first["messages"][0]["sender_role"] == "advisor" and first["messages"][0]["mine"] is False
    assert api.get("priya", "/students/me/dashboard").json()["unread_messages"] == 0  # reading cleared it

    sent = api.post("priya", f"/messages/{PRIYA_ID}", json={"body": "  Hello advisor  "})
    assert sent.status_code == 201
    assert sent.json()["body"] == "Hello advisor" and sent.json()["sender_role"] == "student"
    assert sent.json()["mine"] is True and sent.json()["sender_name"] == "Priya Raman"
    assert api.get("priya", "/students/me/dashboard").json()["unread_messages"] == 0  # her own message

    seen = api.get("morgan", f"/messages/{PRIYA_ID}").json()
    assert seen["unread"] == 1 and [m["body"] for m in seen["messages"]] == [SEEDED, "Hello advisor"]
    hello = seen["messages"][1]
    assert hello["mine"] is False and hello["read_at"] is None  # the state before this read
    again = api.get("morgan", f"/messages/{PRIYA_ID}").json()
    assert again["unread"] == 0 and again["messages"][1]["read_at"]  # marked read by the first read

    reply = api.post("morgan", f"/messages/{PRIYA_ID}", json={"body": "Yes, come by on Tuesday."})
    assert reply.status_code == 201 and reply.json()["sender_role"] == "advisor"
    assert reply.json()["sender_name"] == "Morgan Lee"
    assert api.get("priya", "/students/me/dashboard").json()["unread_messages"] == 1
    thread = api.get("priya", f"/messages/{PRIYA_ID}").json()
    assert thread["unread"] == 1 and [m["sender_role"] for m in thread["messages"]] == [
        "advisor", "student", "advisor"]
    assert api.get("priya", "/students/me/dashboard").json()["unread_messages"] == 0  # reading cleared it
    assert api.get("admin", f"/messages/{PRIYA_ID}").status_code == 200  # an admin may read


def test_message_validation_and_access(api):
    post = lambda who, student, **body: api.post(who, f"/messages/{student}", json=body)  # noqa: E731
    assert post("priya", PRIYA_ID, body="   ").status_code == 422
    assert post("priya", PRIYA_ID, body="x" * 2001).status_code == 422
    assert post("priya", PRIYA_ID, body="x" * 2000).status_code == 201
    other = build(api, "daniel")["schedule_id"]
    assert post("priya", PRIYA_ID, body="hi", schedule_id=other).status_code == 422  # not her schedule
    assert post("priya", "S0057", body="hi").status_code == 403  # Daniel's thread
    assert api.get("priya", "/messages/S0057").status_code == 403
    assert post("rachel", PRIYA_ID, body="hi").status_code == 403  # not Priya's advisor
    assert api.get("rachel", f"/messages/{PRIYA_ID}").status_code == 403
    assert post("admin", PRIYA_ID, body="hi").status_code == 403  # an admin reads, never writes
    assert api.get("priya", "/messages/S9999").status_code == 403
    assert api.get(None, f"/messages/{PRIYA_ID}").status_code == 401


# ---------- every route: wrong role = 403, no token = 401 ----------

ID = str(uuid.uuid4())
STUDENT, ADVISOR, ADMIN = "priya", "morgan", "admin"
# (method, path, roles that may call it)
ROUTES = [
    ("GET", "/terms", {STUDENT, ADVISOR, ADMIN}),
    ("GET", "/terms/plannable", {STUDENT, ADVISOR, ADMIN}),
    ("GET", "/students/me/dashboard", {STUDENT}),
    ("GET", "/students/me/interests", {STUDENT}),
    ("POST", "/students/me/interests", {STUDENT}),
    ("PATCH", "/students/me/interests/1", {STUDENT}),
    ("POST", "/students/me/documents", {STUDENT}),
    ("POST", "/schedules/build", {STUDENT}),
    ("GET", "/schedules/current", {STUDENT}),
    ("GET", "/schedules/history", {STUDENT}),
    ("GET", f"/schedules/{ID}", {STUDENT, ADVISOR, ADMIN}),
    ("GET", f"/schedules/{ID}/suggestions", {STUDENT, ADVISOR, ADMIN}),
    ("POST", f"/schedules/{ID}/items", {STUDENT}),
    ("POST", f"/schedules/{ID}/reset", {STUDENT}),
    ("DELETE", f"/schedules/{ID}", {STUDENT}),
    ("POST", f"/schedules/{ID}/withdraw", {STUDENT}),
    ("POST", f"/schedules/{ID}/resubmit", {STUDENT}),
    ("POST", f"/schedules/{ID}/submit", {STUDENT}),
    ("GET", "/courses/search?q=governance", {STUDENT, ADVISOR, ADMIN}),
    ("GET", "/courses/95-841", {STUDENT, ADVISOR, ADMIN}),
    ("GET", "/advisor/caseload", {ADVISOR, ADMIN}),
    ("POST", f"/advisor/schedules/{ID}/email-preview", {ADVISOR}),
    ("GET", f"/advisor/schedules/{ID}", {ADVISOR, ADMIN}),
    ("POST", f"/advisor/schedules/{ID}/recheck", {ADVISOR}),
    ("POST", f"/advisor/schedules/{ID}/decision", {ADVISOR}),
    ("GET", "/messages/S0056", {STUDENT, ADVISOR, ADMIN}),
    ("POST", "/messages/S0056", {STUDENT, ADVISOR}),
]


@pytest.mark.parametrize("method, path, allowed", ROUTES, ids=[f"{m} {p}" for m, p, _ in ROUTES])
def test_role_matrix(api, method, path, allowed):
    assert api.call(None, method, path).status_code == 401  # no token
    for who in (STUDENT, ADVISOR, ADMIN):
        status = api.call(who, method, path).status_code
        if who in allowed:
            assert status not in (401, 403), f"{who} {method} {path} -> {status}"
        else:
            assert status == 403, f"{who} {method} {path} -> {status}"


def _norm(path: str) -> str:
    import re
    path = path.split("?")[0]
    path = re.sub(r"\{[^}]+\}", "{x}", path)  # documented parameters
    return "/".join("{x}" if re.fullmatch(r"[0-9a-f-]{36}|\d+|\d{2}-\d{3}|S\d{4}", seg) else seg
                    for seg in path.split("/"))


def test_every_api_route_is_in_the_role_matrix_and_documented():
    spec = app.openapi()["paths"]
    documented = {(m.upper(), _norm(p)) for p, methods in spec.items() for m in methods}
    covered = {(m, _norm(p)) for m, p, _ in ROUTES}
    skip = {("POST", "/auth/login"), ("GET", "/auth/me"), ("GET", "/health")}  # public or any-user, tested elsewhere
    assert documented - covered - skip == set(), "routes without a role test"
    assert covered - documented == set(), "role tests for routes that do not exist"
    for needed in ("/students/me/dashboard", "/schedules/build", "/advisor/caseload", "/courses/search",
                   "/messages/{x}", "/terms/plannable", "/schedules/{x}/reset"):
        assert any(p == needed for _, p in documented), needed
