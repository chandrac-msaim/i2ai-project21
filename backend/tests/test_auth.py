from typing import Annotated

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api import auth as auth_routes
from app.auth import CurrentUser, require_role
from app.models import AuditLog
from tests.conftest import MORGAN, PRIYA, auth_header, login


def test_login_priya(client):
    r = login(client, PRIYA)
    assert r.status_code == 200
    body = r.json()
    assert body["token_type"] == "bearer" and body["access_token"]
    assert body["user"]["role"] == "student"
    assert body["user"]["student_id"] == "S0056"
    assert body["user"]["advisor_id"] is None


def test_login_is_case_insensitive(client):
    assert login(client, PRIYA.upper()).status_code == 200


def test_login_writes_audit_log(client, db):
    user_id = login(client, MORGAN).json()["user"]["user_id"]
    row = db.scalar(
        select(AuditLog).where(AuditLog.action == "login", AuditLog.actor_id == user_id)
        .order_by(AuditLog.log_id.desc())
    )
    assert row is not None and row.details == {"role": "advisor"}


@pytest.mark.parametrize("email,password", [(PRIYA, "wrong"), ("nobody@demo.graydi.local", "demo1234")])
def test_login_fails(client, email, password):
    r = login(client, email, password)
    assert r.status_code == 401
    assert "access_token" not in r.json()


def test_me(client):
    r = client.get("/auth/me", headers=auth_header(client, PRIYA))
    assert r.status_code == 200
    assert r.json()["email"] == PRIYA and r.json()["role"] == "student"


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer not-a-jwt"}])
def test_me_requires_valid_token(client, headers):
    assert client.get("/auth/me", headers=headers).status_code == 401


@pytest.fixture(scope="module")
def probe() -> TestClient:
    """A tiny app with role-protected routes, to test require_role without touching the real app."""
    probe_app = FastAPI()
    probe_app.include_router(auth_routes.router)

    @probe_app.get("/advisor-only")
    def advisor_only(user: Annotated[CurrentUser, Depends(require_role("advisor"))]):
        return {"advisor_id": user.advisor_id}

    @probe_app.get("/student-only")
    def student_only(user: Annotated[CurrentUser, Depends(require_role("student"))]):
        return {"student_id": user.student_id}

    return TestClient(probe_app)


def test_require_role(probe):
    priya, morgan = auth_header(probe, PRIYA), auth_header(probe, MORGAN)
    assert probe.get("/advisor-only", headers=priya).status_code == 403
    assert probe.get("/student-only", headers=morgan).status_code == 403
    assert probe.get("/student-only", headers=priya).json() == {"student_id": "S0056"}
    r = probe.get("/advisor-only", headers=morgan)
    assert r.status_code == 200 and r.json()["advisor_id"]
    assert probe.get("/advisor-only").status_code == 401
