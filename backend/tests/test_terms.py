"""Plannable term decided by date (D12): term_registration(today) + GET /terms."""
from datetime import date

import pytest

from app.config import Settings
from app.services.terms import plannable_terms, registration_status
from tests.conftest import ALEX, MORGAN, PRIYA, auth_header

NOV_7 = date(2026, 11, 7)
JUL_15 = date(2026, 7, 15)


# --- settings.today() ---

def test_app_today_empty_is_real_date():
    assert Settings(app_today="").today() == date.today()
    assert Settings(app_today=None).today() == date.today()


def test_app_today_set():
    assert Settings(app_today="2026-11-07").today() == NOV_7


# --- services ---

@pytest.mark.parametrize(("day", "expected"), [(NOV_7, ["S27"]), (JUL_15, ["F26"])])
def test_only_one_term_plannable(db, day, expected):
    assert [t.term_code for t in plannable_terms(db, day)] == expected


def test_status_on_nov_7(db):
    status = {t.term_code: t for t in registration_status(db, NOV_7)}
    assert status["F26"].status == "closed"
    assert status["S27"].status == "open" and status["S27"].has_offerings
    assert status["M27"].status == "not_open_yet"
    assert status["F27"].status == "not_open_yet" and not status["F27"].has_offerings
    assert not status["F27"].plannable


def test_status_on_jul_15(db):
    status = {t.term_code: t.status for t in registration_status(db, JUL_15)}
    assert status["F26"] == "open"
    assert status["S27"] == "not_open_yet"


def test_registration_status_sorted_and_filtered(db):
    terms = registration_status(db, NOV_7, from_year=2026)
    assert [t.seq for t in terms] == sorted(t.seq for t in terms)
    assert terms and all(t.year >= 2026 for t in terms)
    assert len(terms) < len(registration_status(db, NOV_7))


# --- GET /terms ---

def test_terms_requires_login(client):
    assert client.get("/terms").status_code == 401


@pytest.mark.parametrize("email", [PRIYA, ALEX, MORGAN])
def test_terms_any_logged_in_user(client, pin_today, email):
    pin_today(NOV_7)
    r = client.get("/terms", headers=auth_header(client, email))
    assert r.status_code == 200
    body = r.json()
    assert body["today"] == "2026-11-07"
    codes = [t["term_code"] for t in body["terms"]]
    assert codes[0] == "S26" and "F27" in codes  # from the current year on
    assert [t["term_code"] for t in body["terms"] if t["plannable"]] == ["S27"]
    s27 = next(t for t in body["terms"] if t["term_code"] == "S27")
    assert s27 == s27 | {"status": "open", "has_offerings": True,
                         "registration_opens": "2026-11-01", "registration_closes": "2027-01-22"}


def test_terms_on_jul_15(client, pin_today):
    pin_today(JUL_15)
    body = client.get("/terms", headers=auth_header(client, PRIYA)).json()
    assert [t["term_code"] for t in body["terms"] if t["plannable"]] == ["F26"]
