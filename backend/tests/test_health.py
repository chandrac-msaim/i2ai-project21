from datetime import date

import pytest



@pytest.mark.parametrize(("day", "plannable"), [(date(2026, 11, 7), ["S27"]), (date(2026, 7, 15), ["F26"])])
def test_health(client, pin_today, day, plannable):
    pin_today(day)
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and body["db"] is True
    assert body["current_term"] == "F26"
    assert body["today"] == day.isoformat()
    assert body["plannable_terms"] == plannable
    assert "planning_term" not in body
    assert isinstance(body["ollama"], bool)
