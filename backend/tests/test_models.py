"""Proves the ORM mapping matches db/schema.sql: every model can SELECT, and columns + nullability match."""
import pytest
from sqlalchemy import func, select, text

from app.models import TABLE_MODELS, VIEW_MODELS

ALL_MODELS = TABLE_MODELS + VIEW_MODELS


def test_39_tables_mapped():
    assert len(TABLE_MODELS) == 39
    assert len({m.__tablename__ for m in TABLE_MODELS}) == 39


def test_every_schema_table_is_mapped(db):
    db_tables = set(db.scalars(text(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'advisor' AND table_type = 'BASE TABLE'"
    )))
    assert db_tables == {m.__tablename__ for m in TABLE_MODELS}


@pytest.mark.parametrize("model", ALL_MODELS, ids=lambda m: m.__tablename__)
def test_select_one_row(db, model):
    db.execute(select(model).limit(1)).scalars().all()


def _db_columns(db, table: str) -> dict[str, bool]:
    """column name -> is nullable, via pg_attribute (also covers materialized views)."""
    rows = db.execute(text(
        "SELECT a.attname, NOT a.attnotnull FROM pg_attribute a "
        "WHERE a.attrelid = to_regclass(:t) AND a.attnum > 0 AND NOT a.attisdropped"
    ), {"t": f"advisor.{table}"}).all()
    return dict(rows)


@pytest.mark.parametrize("model", ALL_MODELS, ids=lambda m: m.__tablename__)
def test_columns_match_schema(db, model):
    db_cols = _db_columns(db, model.__tablename__)
    orm_cols = {c.name: c for c in model.__table__.columns}
    assert set(orm_cols) == set(db_cols)
    if model in TABLE_MODELS:  # views report every column as nullable
        wrong = [n for n, c in orm_cols.items() if c.nullable != db_cols[n]]
        assert not wrong, f"nullability differs for {wrong}"


def test_generated_columns_are_read_only():
    from app.models import Course, Section

    assert Section.__table__.c.seats_left.computed is not None
    assert Course.__table__.c.level.computed is not None


def test_terms_have_registration_window_not_is_planning(db):
    from app.models import Term

    assert "is_planning" not in Term.__table__.c
    s27 = db.get(Term, "S27")
    assert (s27.registration_opens.isoformat(), s27.registration_closes.isoformat()) == ("2026-11-01", "2027-01-22")


def test_new_student_alex(db):
    from app.models import Enrollment, Student, StudentInterest

    assert db.scalar(select(func.count()).select_from(Student)) == 129  # 126 + the 3 Fall 2026 demo admits (M9a)
    alex = db.get(Student, "S0126")
    assert alex is not None and alex.entry_term == "S27"
    assert db.scalar(select(func.count()).where(Enrollment.student_id == "S0126")) == 0
    statuses = db.scalars(select(StudentInterest.status).where(StudentInterest.student_id == "S0126")).all()
    assert statuses == ["suggested"] * 3
