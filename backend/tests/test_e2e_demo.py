"""M9a end-to-end demo: the two demo modes through the real API (build -> submit -> advisor decision -> email).

Spring 2027 mode (APP_TODAY 2026-11-07): Alex builds a first schedule, Morgan approves, Alex sees it locked.
Fall 2026 mode (APP_TODAY 2026-07-15): Selin (a new admit) builds F26, Morgan rejects with a comment, Selin can resubmit.
Each test runs in the rolled-back `api` transaction; the LLM and email are fakes.
"""
from datetime import date

from app.main import app
from app.services.terms import get_today

FALL_DAY = date(2026, 7, 15)


def _decide(api, sid, decision, comment):
    return api.post("morgan", f"/advisor/schedules/{sid}/decision", json={"decision": decision, "comment": comment})


def test_spring_demo_new_student_build_submit_approve(api):
    dash = api.get("alex", "/students/me/dashboard").json()
    assert dash["student"]["student_id"] == "S0126"
    built = api.post("alex", "/schedules/build", json={"term_code": "S27"})
    assert built.status_code == 201, built.text
    sid = built.json()["schedule_id"]
    assert float(built.json()["total_units"]) <= 54

    assert api.post("alex", f"/schedules/{sid}/submit").status_code == 200
    row = next(r for r in api.get("morgan", "/advisor/caseload").json()["rows"] if r["student_id"] == "S0126")
    assert row["schedule"]["status"] == "submitted"

    decided = _decide(api, sid, "approve", "Welcome, this plan looks right.")
    assert decided.status_code == 200 and decided.json()["status"] == "approved"
    (mail,) = api.sent
    assert mail["to"] == "alex.rivera@demo.graydi.local" and "Spring 2027" in mail["subject"]
    final = api.get("alex", f"/schedules/{sid}").json()
    assert final["status"] == "approved" and not final["can_edit"]


def test_fall_demo_new_admit_build_submit_reject_resubmit(api):
    app.dependency_overrides[get_today] = lambda: FALL_DAY
    dash = api.get("selin", "/students/me/dashboard").json()
    assert dash["student"]["student_id"] == "S0127"
    built = api.post("selin", "/schedules/build", json={"term_code": "F26"})
    assert built.status_code == 201, built.text
    sid = built.json()["schedule_id"]
    assert float(built.json()["total_units"]) <= 54

    assert api.post("selin", f"/schedules/{sid}/submit").status_code == 200
    rejected = _decide(api, sid, "reject", "Please review the elective choice.")
    assert rejected.status_code == 200 and rejected.json()["status"] == "rejected"
    (mail,) = api.sent
    assert mail["to"] == "selin.aksoy@demo.graydi.local" and "Fall 2026" in mail["subject"]
    assert "Please review the elective choice." in mail["body"]

    again = api.post("selin", f"/schedules/{sid}/resubmit")  # new draft version from the rejected one
    assert again.status_code == 201, again.text
    new = again.json()
    assert new["status"] == "draft" and new["schedule_id"] != sid
    assert api.post("selin", f"/schedules/{new['schedule_id']}/submit").json()["status"] == "submitted"
