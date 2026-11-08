"""Shared fixtures. Tests run against the live dev database (python scripts\\load_data.py --reset)."""
from collections.abc import Iterator
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db import SessionLocal, get_db
from app.main import app
from app.services.terms import get_today

DEMO_PASSWORD = "demo1234"
PRIYA = "priya.raman@demo.graydi.local"
MORGAN = "morgan.lee@demo.graydi.local"
ALEX = "alex.rivera@demo.graydi.local"  # new student, entry S27, no history


@pytest.fixture(scope="session")
def client() -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


@pytest.fixture
def db() -> Iterator[Session]:
    with SessionLocal() as session:
        yield session


@pytest.fixture
def pin_today():
    """Pin the app's "today" (like APP_TODAY) for one test: pin_today(date(2026, 11, 7))."""
    def pin(day: date) -> None:
        app.dependency_overrides[get_today] = lambda: day
    yield pin
    app.dependency_overrides.pop(get_today, None)


def login(client: TestClient, email: str, password: str = DEMO_PASSWORD):
    return client.post("/auth/login", json={"email": email, "password": password})


def auth_header(client: TestClient, email: str) -> dict[str, str]:
    token = login(client, email).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


# ---------- API tests: routes commit, so each test runs inside an outer transaction that is rolled back ----------

DEMO_DAY = date(2026, 11, 7)
EMAILS = {"priya": PRIYA, "daniel": "daniel.okafor@demo.graydi.local", "alex": ALEX,
          "morgan": "morgan.lee@demo.graydi.local",  # advises Priya, Daniel and Alex
          "rachel": "rachel.kim@demo.graydi.local",  # another advisor
          "selin": "selin.aksoy@demo.graydi.local",  # Fall 2026 demo admit (APP_TODAY 2026-07-15)
          "admin": "admin@demo.graydi.local"}


class Api:
    """TestClient plus the pieces a test may want to look at or change."""

    def __init__(self, client: TestClient, db: Session, llm, sent: list[dict]) -> None:
        self.client, self.db, self.llm, self.sent = client, db, llm, sent
        self._headers: dict[str, dict[str, str]] = {}

    def headers(self, who: str) -> dict[str, str]:
        if who not in self._headers:
            self._headers[who] = auth_header(self.client, EMAILS[who])
        return self._headers[who]

    def call(self, who: str | None, method: str, path: str, **kwargs):
        headers = self.headers(who) if who else {}
        return self.client.request(method, path, headers=headers, **kwargs)

    def get(self, who, path, **kw):
        return self.call(who, "GET", path, **kw)

    def post(self, who, path, **kw):
        return self.call(who, "POST", path, **kw)


@pytest.fixture
def api(client: TestClient) -> Iterator[Api]:
    from app.api.deps import get_llm
    from app.db import engine
    from app.tools import TOOLS
    from app.tools.email import EmailResult
    from tests.fakes import FakeLLM

    connection = engine.connect()
    outer = connection.begin()
    session = Session(bind=connection, autoflush=False, expire_on_commit=False,
                      join_transaction_mode="create_savepoint")
    llm, sent = FakeLLM(), []

    def fake_send(to, subject, body, **_):
        sent.append({"to": to, "subject": subject, "body": body, "html": _.get("html"), "cc": _.get("cc")})
        return EmailResult(sent=True)

    original = TOOLS["send_email"]
    TOOLS["send_email"] = fake_send  # no real email from API tests
    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[get_llm] = lambda: llm
    app.dependency_overrides[get_today] = lambda: DEMO_DAY
    try:
        yield Api(client, session, llm, sent)
    finally:
        TOOLS["send_email"] = original
        for dep in (get_db, get_llm, get_today):
            app.dependency_overrides.pop(dep, None)
        session.close()
        outer.rollback()
        connection.close()
