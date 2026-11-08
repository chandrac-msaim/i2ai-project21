"""M6 API: terms, dashboard, interests, documents, courses, schedules (student side). Happy path + wrong role.

Each test runs inside a rolled-back transaction (the `api` fixture); the LLM and the email sender are fakes.
"""
import uuid

import pytest
from sqlalchemy import func, select

from app.models import AuditLog, Schedule, StudentDocument, StudentInterest

TERM = "S27"
CANDIDATES = ("95-719", "95-819", "95-881", "94-813", "94-728")  # 6-unit electives offered in S27


def build(api, who="priya", replace=False, term=TERM):
    r = api.post(who, "/schedules/build", json={"term_code": term, "replace": replace})
    assert r.status_code == 201, r.text
    return r.json()


def free_course(view) -> str:
    taken = {i["course_code"] for i in view["items"]}
    return next(c for c in CANDIDATES if c not in taken)


def codes(view) -> set[str]:
    return {i["course_code"] for i in view["items"]}


def make_pdf(line: str) -> bytes:
    """A one-page PDF with `line` as its text, built by hand (so the test needs no PDF library)."""
    stream = f"BT /F1 12 Tf 72 720 Td ({line}) Tj ET".encode()
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
               b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
               b"/Resources << /Font << /F1 5 0 R >> >> >>",
               b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
               b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offsets = b"%PDF-1.4\n", []
    for n, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % n + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    return out + b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF" % (len(objects) + 1, xref)


# ---------- terms ----------

def test_terms_plannable_picker(api):
    r = api.get("priya", "/terms/plannable")
    assert r.status_code == 200
    body = r.json()
    assert body["today"] == "2026-11-07"
    assert [t["term_code"] for t in body["plannable"]] == ["S27"]
    assert all(t["status"] == "open" and t["has_offerings"] for t in body["plannable"])
    assert "M27" in [t["term_code"] for t in body["upcoming"]]  # not open yet
    assert "F26" in [t["term_code"] for t in body["recent_closed"]]
    assert api.get("morgan", "/terms/plannable").status_code == 200  # every logged-in role
    assert api.get(None, "/terms/plannable").status_code == 401


# ---------- dashboard ----------

def test_dashboard_for_a_continuing_and_a_new_student(api):
    d = api.get("priya", "/students/me/dashboard").json()
    assert d["student"]["first_name"] == "Priya" and d["is_new_student"] is False
    assert d["advisor"]["name"] == "Morgan Lee" and d["advisor"]["email"] == "morgan.lee@demo.graydi.local"
    assert d["audit"]["track_code"] == "AIM12" and len(d["audit"]["core"]) == 19
    assert float(d["progress"]["units_completed"]) == 24
    assert d["progress"]["below_min_qpa"] is False and d["progress"]["projected_graduation_term"] == "M27"
    assert [i["label"] for i in d["interests"]["confirmed"]] == ["AI governance", "Financial services", "Model risk"]
    assert isinstance(d["unread_messages"], int) and [t["term_code"] for t in d["terms"]["plannable"]] == ["S27"]
    assert d["schedules"] == []  # nothing built yet
    alex = api.get("alex", "/students/me/dashboard").json()
    assert alex["is_new_student"] is True and float(alex["progress"]["units_completed"]) == 0
    assert len(alex["interests"]["suggested"]) == 3 and alex["interests"]["confirmed"] == []
    view = build(api)
    d2 = api.get("priya", "/students/me/dashboard").json()
    assert d2["schedules"] == [{"term_code": TERM, "term_label": "Spring 2027", "schedule_id": view["schedule_id"],
                                "version": 1, "status": "draft"}]


def test_dashboard_is_students_only(api):
    for who in ("morgan", "admin"):
        assert api.get(who, "/students/me/dashboard").status_code == 403
    assert api.get(None, "/students/me/dashboard").status_code == 401


# ---------- interests ----------

def test_add_a_typed_interest(api):
    r = api.post("alex", "/students/me/interests", json={"label": "   Robotics   and   vision "})
    assert r.status_code == 201
    body = r.json()
    assert (body["label"], body["source"], body["status"]) == ("Robotics and vision", "manual", "confirmed")
    assert body["confirmed_at"]
    row = api.db.get(StudentInterest, body["interest_id"])
    assert row.student_id == "S0126" and row.embedding is not None and len(row.embedding) == 384
    audit = api.db.scalar(select(AuditLog).where(AuditLog.action == "interest_added",
                                                 AuditLog.entity_id == str(row.interest_id)))
    assert audit and audit.details["label"] == "Robotics and vision" and audit.actor_id == "S0126"
    listed = api.get("alex", "/students/me/interests").json()
    assert "Robotics and vision" in [i["label"] for i in listed["confirmed"]]


def test_typed_interest_validation_and_duplicates(api):
    post = lambda label: api.post("alex", "/students/me/interests", json={"label": label})  # noqa: E731
    assert post("   ").status_code == 422 and post("").status_code == 422
    assert post("x" * 61).status_code == 422
    assert post("y" * 60).status_code == 201  # exactly the limit
    assert post("Cloud").status_code == 201
    dup = post("  cLoUd ")
    assert dup.status_code == 409 and dup.json()["code"] == "duplicate_interest"
    suggested = post("ai AGENTS")  # Alex already has the suggestion 'AI agents'
    assert suggested.status_code == 409 and suggested.json()["status"] == "suggested"
    assert api.post("morgan", "/students/me/interests", json={"label": "x"}).status_code == 403
    assert api.post("alex", "/students/me/interests", json={}).status_code == 422


def test_confirm_and_remove_interests_never_delete_rows(api):
    suggested = api.get("alex", "/students/me/interests").json()["suggested"]
    first = suggested[0]
    r = api.call("alex", "PATCH", f"/students/me/interests/{first['interest_id']}", json={"action": "confirm"})
    assert r.status_code == 200 and r.json()["status"] == "confirmed" and r.json()["confirmed_at"]
    row = api.db.get(StudentInterest, first["interest_id"])
    assert row.embedding is not None and row.confirmed_at is not None
    second = suggested[1]
    r = api.call("alex", "PATCH", f"/students/me/interests/{second['interest_id']}", json={"action": "remove"})
    assert r.status_code == 200 and r.json()["status"] == "removed"
    assert api.db.get(StudentInterest, second["interest_id"]).status == "removed"  # the row is kept
    listed = api.get("alex", "/students/me/interests").json()
    assert second["label"] not in [i["label"] for i in listed["suggested"] + listed["confirmed"]]
    assert first["label"] in [i["label"] for i in listed["confirmed"]]
    actions = set(api.db.scalars(select(AuditLog.action).where(AuditLog.entity_type == "student_interest")))
    assert {"interest_confirmed", "interest_removed"} <= actions
    # typing a removed interest again brings it back (it is not a duplicate of an active one)
    again = api.post("alex", "/students/me/interests", json={"label": second["label"].upper()})
    assert again.status_code == 201 and again.json()["interest_id"] == second["interest_id"]


def test_interest_errors_and_wrong_roles(api):
    other = api.db.scalar(select(StudentInterest.interest_id).where(StudentInterest.student_id == "S0056"))
    assert api.call("alex", "PATCH", f"/students/me/interests/{other}", json={"action": "confirm"}).status_code == 404
    assert api.call("alex", "PATCH", "/students/me/interests/99999999", json={"action": "remove"}).status_code == 404
    assert api.call("alex", "PATCH", f"/students/me/interests/{other}", json={"action": "delete"}).status_code == 422
    for who in ("morgan", "admin"):
        assert api.get(who, "/students/me/interests").status_code == 403
        assert api.call(who, "PATCH", f"/students/me/interests/{other}", json={"action": "remove"}).status_code == 403


# ---------- documents ----------

def test_upload_a_text_resume_returns_new_suggestions(api):
    api.llm.reply = "Robotics\nComputer vision\nEdge computing"
    text_ = "Built perception software for warehouse robots and trained vision models."
    r = api.post("alex", "/students/me/documents", data={"doc_type": "resume"},
                 files={"file": ("resume.txt", text_.encode(), "text/plain")})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["doc_type"] == "resume" and body["method"] == "llm" and body["characters"] == len(text_)
    assert [i["label"] for i in body["suggested"]] == ["Robotics", "Computer vision", "Edge computing"]
    assert all(i["status"] == "suggested" and i["source"] in ("resume", "undergrad") for i in body["suggested"])
    doc = api.db.scalar(select(StudentDocument).where(StudentDocument.student_id == "S0126",
                                                      StudentDocument.filename == "resume.txt"))
    assert doc.text_content == text_ and doc.doc_type == "resume"
    assert api.db.scalar(select(AuditLog).where(AuditLog.action == "document_uploaded"))
    confirmed = api.get("alex", "/students/me/interests").json()["confirmed"]
    assert confirmed == []  # an upload never confirms anything
    assert len(api.llm.calls) == 1 and "Alex" not in api.llm.calls[0][1]


def test_upload_a_pdf(api):
    api.llm.reply = "Cloud security"
    pdf = make_pdf("Security engineer with cloud experience")
    r = api.post("alex", "/students/me/documents", data={"doc_type": "undergrad_transcript"},
                 files={"file": ("transcript.pdf", pdf, "application/pdf")})
    assert r.status_code == 201, r.text
    doc = api.db.scalar(select(StudentDocument).where(StudentDocument.filename == "transcript.pdf"))
    assert "Security engineer" in doc.text_content and doc.doc_type == "undergrad_transcript"
    assert [i["label"] for i in r.json()["suggested"]] == ["Cloud security"]


def test_upload_rejections(api):
    def up(name, data, who="alex", doc_type="resume"):
        return api.post(who, "/students/me/documents", data={"doc_type": doc_type},
                        files={"file": (name, data, "application/octet-stream")})
    assert up("notes.docx", b"hello").status_code == 415
    assert up("empty.txt", b"   \n").status_code == 422
    assert up("fake.pdf", b"this is not a pdf").status_code == 415
    assert up("broken.pdf", b"%PDF-1.4\ngarbage without structure").status_code == 422
    assert up("big.txt", b"a" * (5 * 1024 * 1024 + 1)).status_code == 413
    assert up("ok.txt", b"text", doc_type="passport").status_code == 422
    assert up("ok.txt", b"text", who="morgan").status_code == 403
    assert api.post("alex", "/students/me/documents").status_code == 422  # no file at all
    assert api.llm.calls == []  # nothing reached the LLM


# ---------- courses ----------

def test_course_search_with_the_rule_verdict(api):
    r = api.get("priya", "/courses/search", params={"q": "governance"})
    assert r.status_code == 200
    body = r.json()
    assert body["term_code"] == TERM and body["q"] == "governance"
    hits = {h["course_code"]: h for h in body["results"]}
    assert "95-841" in hits and hits["95-841"]["title"] == "Responsible AI Governance"
    assert hits["95-841"]["check"]["eligible"] is True and hits["95-841"]["check"]["blocking"] == []
    assert hits["95-841"]["offered_sections"] >= 1
    code = api.get("priya", "/courses/search", params={"q": "94-413"}).json()["results"][0]
    assert code["course_code"] == "94-413" and code["level"] == "undergraduate"  # exact code first
    assert "needs_petition" in code["check"]["flags"] and code["check"]["eligible"] is True
    assert any("Undergraduate" in n for n in code["check"]["notes"])
    blocked = api.get("alex", "/courses/search", params={"q": "Cybersecurity for Artificial"}).json()["results"]
    sec = next(h for h in blocked if h["course_code"] == "95-767")  # prerequisites Alex does not have
    assert sec["check"]["eligible"] is False and any("PREREQ" in b for b in sec["check"]["blocking"])


def test_course_search_for_an_advisor_has_no_verdict_and_validates_input(api):
    hits = api.get("morgan", "/courses/search", params={"q": "governance"}).json()["results"]
    assert hits and all(h["check"] is None for h in hits)
    assert api.get("priya", "/courses/search", params={"q": "a"}).status_code == 422
    assert api.get("priya", "/courses/search").status_code == 422
    assert api.get(None, "/courses/search", params={"q": "governance"}).status_code == 401
    closed = api.get("priya", "/courses/search", params={"q": "governance", "term": "F26"})
    assert closed.status_code == 200 and closed.json()["term_code"] == "F26"


def test_course_detail(api):
    r = api.get("priya", "/courses/95-841")
    assert r.status_code == 200
    c = r.json()
    assert c["course_code"] == "95-841" and c["title"] == "Responsible AI Governance" and c["offered"] is True
    assert c["term_code"] == TERM and c["level"] == "graduate" and c["sections"]
    sec = c["sections"][0]
    assert {"section_id", "section_code", "days_times", "modality", "seats_left", "instructors"} <= set(sec)
    assert sec["instructors"] and all(isinstance(n, str) for n in sec["instructors"])
    assert c["syllabus_url"] is None or isinstance(c["syllabus_url"], str)  # courses.syllabus_url, else the offering's
    assert set(c["prerequisites"]) == {"options", "text", "catalog_applies"}
    assert api.get("morgan", "/courses/95-841", params={"term": "F26"}).json()["term_code"] == "F26"
    assert api.get("priya", "/courses/00-000").status_code == 404
    assert api.get(None, "/courses/95-841").status_code == 401


# ---------- build, current, history, suggestions ----------

def test_build_creates_the_draft_with_live_checks(api):
    v = build(api)
    assert (v["term_code"], v["version"], v["status"]) == (TERM, 1, "draft")
    assert float(v["total_units"]) == 48 == float(v["core_units"]) + float(v["elective_units"])
    assert v["load_rationale"].startswith("48 of 54 units: 30 core + 18 electives.") and v["ai_explanation"]
    assert v["term_label"] == "Spring 2027" and "S27" not in v["ai_explanation"] + v["load_rationale"]
    assert [c["code"] for c in v["checks"]] == ["UNIT_LIMIT", "PREREQ", "OFFERED", "LEVEL", "APPROVAL",
                                                "TIME_CONFLICT", "PRIVACY"]
    assert v["valid"] and v["blocking"] == [] and v["can_submit"] and v["can_edit"] and v["can_discard"]
    assert not v["can_reset"] and not v["can_withdraw"] and not v["can_resubmit"]
    core = [i for i in v["items"] if i["item_type"] == "core"]
    assert len(core) == 5 and all(i["is_locked"] and i["days_times"] and i["section_code"] for i in core)
    assert all(i["in_ai_draft"] is True and i["ai_original_course"] is None for i in v["items"])
    schedule = api.db.get(Schedule, uuid.UUID(v["schedule_id"]))
    assert schedule.ai_baseline and {b["course_code"] for b in schedule.ai_baseline} == codes(v)


def test_build_needs_confirmation_to_replace_an_open_draft(api):
    first = build(api)
    again = api.post("priya", "/schedules/build", json={"term_code": TERM})
    assert again.status_code == 409
    assert again.json()["code"] == "draft_exists" and again.json()["schedule_id"] == first["schedule_id"]
    assert api.get("priya", f"/schedules/{first['schedule_id']}").json()["status"] == "draft"
    second = build(api, replace=True)
    assert second["version"] == 2 and second["previous_schedule_id"] == first["schedule_id"]
    assert api.get("priya", f"/schedules/{first['schedule_id']}").json()["status"] == "superseded"


def test_build_for_a_term_that_cannot_be_planned_is_a_400_with_the_reason(api):
    for term, why in (("F26", "closed"), ("M27", "opens on")):
        r = api.post("priya", "/schedules/build", json={"term_code": term})
        assert r.status_code == 400 and r.json()["code"] == "term_not_plannable" and why in r.json()["detail"]
    assert api.post("priya", "/schedules/build", json={"term_code": "X99"}).status_code == 400
    assert api.post("priya", "/schedules/build", json={}).status_code == 422
    for who in ("morgan", "admin"):
        assert api.post(who, "/schedules/build", json={"term_code": TERM}).status_code == 403


def test_alex_new_student_builds_a_first_term_draft(api):
    v = build(api, "alex")
    assert float(v["total_units"]) == 42 and float(v["core_units"]) == 24 and v["valid"]
    assert v["load_rationale"].startswith("42 of 54 units: 24 core + 18 electives.")


def test_current_history_and_suggestions(api):
    assert api.get("priya", "/schedules/current").json() is None  # nothing yet
    assert api.get("priya", "/schedules/history").json() == []
    first = build(api)
    second = build(api, replace=True)
    cur = api.get("priya", "/schedules/current").json()
    assert cur["schedule_id"] == second["schedule_id"] and cur["version"] == 2
    assert api.get("priya", "/schedules/current", params={"term": "F26"}).json() is None
    hist = api.get("priya", "/schedules/history", params={"term": TERM}).json()  # not mistaken for {schedule_id}
    assert [(h["version"], h["status"]) for h in hist] == [(2, "draft"), (1, "superseded")]
    assert hist[0]["previous_schedule_id"] == first["schedule_id"] and hist[0]["courses"] == len(second["items"])
    assert float(hist[0]["total_units"]) == 48
    assert api.get("priya", "/schedules/history", params={"term": "F26"}).json() == []

    s = api.get("priya", f"/schedules/{second['schedule_id']}/suggestions").json()
    assert s["basis"] == "interests" and s["term_code"] == TERM
    assert s["interest_match"] and s["popular"]  # both groups, interest matches first in the UI
    assert all(x["group_code"] == "interest_match" and x["matched_interest"] for x in s["interest_match"])
    assert all(x["group_code"] == "popular" for x in s["popular"])
    first_row = s["interest_match"][0]
    assert {"fit", "flags", "reason", "similarity", "popularity", "final_score", "in_schedule"} <= set(first_row)
    in_draft = {x["course_code"] for x in s["interest_match"] + s["popular"] if x["in_schedule"]}
    assert in_draft <= codes(second) and in_draft
    alex = build(api, "alex")
    only = api.get("alex", f"/schedules/{alex['schedule_id']}/suggestions").json()
    assert only["basis"] == "popularity" and only["interest_match"] == [] and only["popular"]


def test_schedule_reads_are_limited_to_the_student_and_their_advisor(api):
    v = build(api)
    sid = v["schedule_id"]
    for path in (f"/schedules/{sid}", f"/schedules/{sid}/suggestions"):
        assert api.get("priya", path).status_code == 200
        assert api.get("morgan", path).status_code == 200  # her advisor
        assert api.get("admin", path).status_code == 200
        assert api.get("daniel", path).status_code == 403
        assert api.get("rachel", path).status_code == 403  # an advisor of other students
        assert api.get(None, path).status_code == 401
    assert api.get("priya", f"/schedules/{uuid.uuid4()}").status_code == 404
    for who in ("morgan", "admin"):
        for path in ("/schedules/current", "/schedules/history"):
            assert api.get(who, path).status_code == 403


# ---------- editing: add, remove, swap, reset ----------

def test_add_remove_swap_then_reset_gives_back_the_ai_draft(api):
    v = build(api)
    sid, ai_codes = v["schedule_id"], codes(v)
    url = f"/schedules/{sid}/items"
    elective = next(i["course_code"] for i in v["items"] if not i["is_locked"])
    extra = free_course(v)

    swapped = api.post("priya", url, json={"action": "swap", "replace_course_code": elective,
                                           "course_code": extra})
    assert swapped.status_code == 200, swapped.text
    s = swapped.json()
    assert extra in codes(s) and elective not in codes(s) and s["edited_by_student"] and s["can_reset"]
    new_item = next(i for i in s["items"] if i["course_code"] == extra)
    assert new_item["added_by"] == "student" and new_item["in_ai_draft"] is False
    assert new_item["ai_original_course"] == elective and new_item["section_id"]  # a section was chosen
    assert len(s["checks"]) == 7  # re-validated

    other = next(c for c in CANDIDATES if c not in codes(s))
    added = api.post("priya", url, json={"action": "add", "course_code": other}).json()
    assert other in codes(added) and float(added["total_units"]) == float(s["total_units"]) + 6
    removed = api.post("priya", url, json={"action": "remove", "course_code": other}).json()
    assert other not in codes(removed)

    reset = api.post("priya", f"/schedules/{sid}/reset")
    assert reset.status_code == 200
    r = reset.json()
    assert codes(r) == ai_codes and r["edited_by_student"] is False and not r["can_reset"]
    assert {(i["course_code"], i["section_id"]) for i in r["items"]} == {(i["course_code"], i["section_id"])
                                                                         for i in v["items"]}
    assert all(i["added_by"] == "ai" and i["ai_original_course"] is None for i in r["items"])


def test_edit_errors(api):
    v = build(api)
    url = f"/schedules/{v['schedule_id']}/items"
    core = next(i["course_code"] for i in v["items"] if i["is_locked"])
    elective = next(i["course_code"] for i in v["items"] if not i["is_locked"])
    post = lambda **body: api.post("priya", url, json=body)  # noqa: E731
    assert post(action="remove", course_code=core).status_code == 409  # locked AI core
    assert post(action="swap", replace_course_code=core, course_code=free_course(v)).status_code == 409
    assert post(action="add", course_code=elective).status_code == 409  # already in the schedule
    assert post(action="remove", course_code=free_course(v)).status_code == 404  # not in the schedule
    assert post(action="add", course_code="00-000").status_code == 400  # not offered
    assert post(action="add", course_code=free_course(v), section_id="95-719-S27-NOPE").status_code == 400
    assert post(action="add").status_code == 400 and post(action="rename").status_code == 422
    assert api.post("daniel", url, json={"action": "remove", "course_code": elective}).status_code == 403
    assert api.post("morgan", url, json={"action": "remove", "course_code": elective}).status_code == 403
    assert api.post("priya", f"/schedules/{uuid.uuid4()}/items", json={"action": "add",
                                                                       "course_code": "95-719"}).status_code == 404
    assert api.post("priya", f"/schedules/{v['schedule_id']}/reset").status_code == 200  # nothing to undo is fine
    assert api.get("priya", f"/schedules/{v['schedule_id']}").json()["edited_by_student"] is False


def test_an_edit_over_the_unit_limit_is_refused_and_the_draft_is_unchanged(api):
    v = build(api)
    sid = v["schedule_id"]
    url = f"/schedules/{sid}/items"
    first = api.post("priya", url, json={"action": "add", "course_code": free_course(v)})
    assert first.status_code == 200 and float(first.json()["total_units"]) == 54
    mid = api.post("priya", url, json={"action": "add", "course_code": free_course(first.json())})
    assert mid.status_code == 200 and float(mid.json()["total_units"]) == 60  # QPA above 3.50: up to 60 is allowed
    before = api.get("priya", f"/schedules/{sid}").json()
    second = api.post("priya", url, json={"action": "add", "course_code": free_course(mid.json())})
    body = second.json()
    assert second.status_code == 409 and body["code"] == "edit_blocked"
    assert any(r.startswith("UNIT_LIMIT") for r in body["reasons"]) and body["detail"]
    after = api.get("priya", f"/schedules/{sid}").json()
    assert codes(after) == codes(before) and float(after["total_units"]) == 60 and after["valid"] is True
    assert after["can_submit"] is True


def test_an_edit_that_creates_a_time_clash_is_refused(api):
    v = build(api)
    sid = v["schedule_id"]
    url = f"/schedules/{sid}/items"
    before = codes(v)
    refused = []
    for course in CANDIDATES:  # at least one candidate overlaps a locked core course or another elective
        if course in before:
            continue
        r = api.post("priya", url, json={"action": "swap", "replace_course_code":
                                         next(i["course_code"] for i in v["items"] if not i["is_locked"]),
                                         "course_code": course})
        if r.status_code == 409:
            refused.append(r.json())
        else:
            api.post("priya", f"/schedules/{sid}/reset")  # keep the next try on the AI draft
    for body in refused:
        assert body["code"] == "edit_blocked" and body["reasons"]
    assert codes(api.get("priya", f"/schedules/{sid}").json()) <= before | set(CANDIDATES)


def test_55_to_60_units_is_allowed_for_priya_with_an_advisor_flag(api):
    v = build(api)
    sid = v["schedule_id"]
    one = api.post("priya", f"/schedules/{sid}/items", json={"action": "add", "course_code": free_course(v)}).json()
    view = api.post("priya", f"/schedules/{sid}/items",
                    json={"action": "add", "course_code": free_course(one)}).json()  # 60 units, QPA above 3.50
    assert float(view["total_units"]) == 60 and view["valid"] is True
    sub = api.post("priya", f"/schedules/{sid}/submit")
    assert sub.status_code == 200
    assert "UNIT_OVERLOAD" in [f["flag_code"] for f in sub.json()["flags"]]
    # more than 54 units is refused for Daniel (QPA below 3.50)
    d = build(api, "daniel")
    url = f"/schedules/{d['schedule_id']}/items"
    ok = api.post("daniel", url, json={"action": "add", "course_code": free_course(d)})
    assert ok.status_code == 200 and float(ok.json()["total_units"]) == 54  # up to 54 is fine
    over = api.post("daniel", url, json={"action": "add", "course_code": free_course(ok.json())})
    assert over.status_code == 409 and over.json()["code"] == "edit_blocked"
    assert "Above 54 units needs a QPA above 3.50" in " ".join(over.json()["reasons"])
    assert codes(api.get("daniel", f"/schedules/{d['schedule_id']}").json()) == codes(ok.json())  # unchanged by the refused edit


# ---------- discard, submit, withdraw, resubmit ----------

def test_discard_a_draft_is_soft_and_a_new_build_works(api):
    v = build(api)
    sid = v["schedule_id"]
    r = api.call("priya", "DELETE", f"/schedules/{sid}")
    assert r.status_code == 200 and r.json()["status"] == "discarded" and r.json()["discarded_at"]
    assert api.db.get(Schedule, uuid.UUID(sid)).status == "discarded"  # the row is kept
    assert api.call("priya", "DELETE", f"/schedules/{sid}").status_code == 409  # not a draft any more
    assert api.get("priya", "/schedules/current").json() is None
    fresh = build(api)  # no confirmation needed: nothing is open
    assert fresh["version"] == 2 and fresh["previous_schedule_id"] is None
    hist = api.get("priya", "/schedules/history").json()
    assert [(h["version"], h["status"]) for h in hist] == [(2, "draft"), (1, "discarded")]


def test_submit_withdraw_and_submit_again(api):
    v = build(api)
    sid = v["schedule_id"]
    assert api.post("priya", f"/schedules/{sid}/withdraw").status_code == 409  # still a draft
    sub = api.post("priya", f"/schedules/{sid}/submit")
    assert sub.status_code == 200, sub.text
    s = sub.json()
    assert s["status"] == "submitted" and s["submitted_at"] and s["can_withdraw"] and not s["can_edit"]
    assert len(s["checks"]) == 7 and isinstance(s["flags"], list)
    assert api.post("priya", f"/schedules/{sid}/submit").status_code == 409  # already submitted
    assert api.post("priya", f"/schedules/{sid}/items", json={"action": "add",
                                                              "course_code": free_course(v)}).status_code == 409
    assert api.call("priya", "DELETE", f"/schedules/{sid}").status_code == 409
    back = api.post("priya", f"/schedules/{sid}/withdraw")
    assert back.status_code == 200 and back.json()["status"] == "draft" and back.json()["submitted_at"] is None
    assert api.post("priya", f"/schedules/{sid}/submit").json()["status"] == "submitted"


def test_daniels_submit_carries_the_low_qpa_flag(api):
    v = build(api, "daniel")
    s = api.post("daniel", f"/schedules/{v['schedule_id']}/submit").json()
    low = [f for f in s["flags"] if f["flag_code"] == "LOW_QPA"]
    assert len(low) == 1 and low[0]["severity"] == "high" and low[0]["rule_text"] and low[0]["suggested_action"]


def test_resubmit_after_a_rejection_makes_a_new_draft(api):
    v = build(api)
    sid = v["schedule_id"]
    assert api.post("priya", f"/schedules/{sid}/resubmit").status_code == 409  # not rejected
    api.post("priya", f"/schedules/{sid}/submit")
    assert api.post("morgan", f"/advisor/schedules/{sid}/decision",
                    json={"decision": "reject", "comment": "Add an elective."}).status_code == 200
    rejected = api.get("priya", f"/schedules/{sid}").json()
    assert rejected["status"] == "rejected" and rejected["can_resubmit"] and rejected["advisor_comment"]
    new = api.post("priya", f"/schedules/{sid}/resubmit")
    assert new.status_code == 201
    n = new.json()
    assert (n["version"], n["status"], n["previous_schedule_id"]) == (2, "draft", sid)
    assert codes(n) == codes(v)
    assert api.post("priya", f"/schedules/{sid}/resubmit").status_code == 409  # a draft is open now


def test_schedule_changes_are_for_the_owner_only(api):
    v = build(api)
    sid = v["schedule_id"]
    calls = [("POST", f"/schedules/{sid}/reset"), ("DELETE", f"/schedules/{sid}"),
             ("POST", f"/schedules/{sid}/withdraw"), ("POST", f"/schedules/{sid}/resubmit"),
             ("POST", f"/schedules/{sid}/submit")]
    for method, path in calls:
        assert api.call("daniel", method, path).status_code == 403, path  # someone else's schedule
        for who in ("morgan", "rachel", "admin"):
            assert api.call(who, method, path).status_code == 403, (who, path)
        assert api.call(None, method, path).status_code == 401
        assert api.call("priya", method, path.replace(sid, str(uuid.uuid4()))).status_code == 404  # unknown id
    assert api.get("priya", f"/schedules/{sid}").json()["status"] == "draft"  # nothing happened


def test_api_tests_leave_nothing_behind(db):
    """Runs after the tests above on a separate connection: every route commit was rolled back."""
    assert db.scalar(select(func.count()).select_from(Schedule)) == 0
    assert db.scalar(select(func.count()).select_from(StudentDocument).where(
        StudentDocument.student_id == "S0126")) == 2  # only the two seeded ones
    assert db.scalar(select(func.count()).select_from(StudentInterest).where(
        StudentInterest.student_id == "S0126", StudentInterest.source == "manual")) == 0
