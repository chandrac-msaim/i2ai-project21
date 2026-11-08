"""
Put the demo back to its starting state. Does not drop tables and never touches the catalog.

    python scripts/demo_reset.py                    # default demo: same as --mode spring
    python scripts/demo_reset.py --mode spring      # Spring 2027: Priya, Daniel, Alex (APP_TODAY=2026-11-07)
    python scripts/demo_reset.py --mode fall        # optional second demo, Fall 2026: Selin, Jonas, Imani (APP_TODAY=2026-07-15)
    python scripts/demo_reset.py --fill-section 94-830-S27-A4   # make a section full (for the full-section demo step)

Removed for the mode's demo students: schedules (+ items, changes, compliance checks), risk flags, messages,
notifications, elective suggestions, and the interests / documents created after the initial load. Seeded interests go
back to their seeded status. Mailpit is emptied. agent_runs, degree_audits and audit_log stay (audit trail).
Safe to run twice: the second run removes nothing.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent))
from load_data import ROOT, database_url, seed_demo_message  # noqa: E402

MAILPIT_URL = os.environ.get("MAILPIT_URL", "http://localhost:8025") + "/api/v1/messages"  # MAILPIT_URL: another Mailpit web address
SEATS_FILE = ROOT / "data" / "demo" / ".seats.json"
MODES = {"spring": ("2026-11-07", "Spring 2027"), "fall": ("2026-07-15", "Fall 2026")}  # (APP_TODAY, term label)
SPRING_STUDENTS = [("Priya", "Raman"), ("Daniel", "Okafor"), ("Alex", "Rivera")]
FALL_STUDENTS = [("Selin", "Aksoy"), ("Jonas", "Lindgren"), ("Imani", "Thompson")]


def student_ids(conn, mode: str) -> list[str]:
    names = SPRING_STUDENTS if mode == "spring" else FALL_STUDENTS
    ids = [r[0] for r in conn.execute(
        "SELECT student_id FROM students WHERE is_demo AND (first_name, last_name) IN (SELECT * FROM unnest(%s::text[], %s::text[]))",
        ([f for f, _ in names], [l for _, l in names]))]
    return sorted(ids)


def initial_load_time(conn):
    row = conn.execute("SELECT max(at) FROM audit_log WHERE action = 'initial_load'").fetchone()
    if not row or row[0] is None:
        sys.exit("No initial_load row in audit_log: run scripts/load_data.py --reset first.")
    return row[0]


def reset_students(conn, ids: list[str], mode: str) -> dict[str, int]:
    """The database part of the reset. Returns rows removed per table."""
    removed: dict[str, int] = {}

    def run(label: str, sql: str, params=()):
        removed[label] = conn.execute(sql, params).rowcount

    loaded_at = initial_load_time(conn)
    # break the schedules <-> agent_runs circle before deleting
    conn.execute("""UPDATE agent_runs SET schedule_id = NULL
                    WHERE schedule_id IN (SELECT schedule_id FROM schedules WHERE student_id = ANY(%s))""", (ids,))
    conn.execute("UPDATE schedules SET built_by_run_id = NULL, previous_schedule_id = NULL WHERE student_id = ANY(%s)",
                 (ids,))
    run("messages", "DELETE FROM messages WHERE student_id = ANY(%s)", (ids,))
    run("notifications", "DELETE FROM notifications WHERE student_id = ANY(%s)", (ids,))
    run("risk_flags", "DELETE FROM risk_flags WHERE student_id = ANY(%s)", (ids,))
    run("elective_suggestions", "DELETE FROM elective_suggestions WHERE student_id = ANY(%s)", (ids,))
    run("schedules", "DELETE FROM schedules WHERE student_id = ANY(%s)", (ids,))
    run("interests", "DELETE FROM student_interests WHERE student_id = ANY(%s) AND created_at > %s", (ids, loaded_at))
    run("documents", "DELETE FROM student_documents WHERE student_id = ANY(%s) AND uploaded_at > %s", (ids, loaded_at))

    # seeded interests: Alex = suggested; Priya and Daniel = confirmed; (fall admits have none)
    # Rows that are already in their seeded state are not counted.
    removed["interests_restored"] = conn.execute("""
        UPDATE student_interests i SET
            status = CASE WHEN s.entry_term = 'S27' THEN 'suggested' ELSE 'confirmed' END,
            confirmed_at = CASE WHEN s.entry_term = 'S27' THEN NULL ELSE coalesce(i.confirmed_at, %s) END
        FROM students s
        WHERE s.student_id = i.student_id AND i.student_id = ANY(%s) AND i.source = 'resume'
          AND i.status IS DISTINCT FROM CASE WHEN s.entry_term = 'S27' THEN 'suggested' ELSE 'confirmed' END""",
                                                (loaded_at, ids)).rowcount
    if mode == "spring":
        conn.execute("DELETE FROM messages WHERE student_id = ANY(%s)", (ids,))
        seed_demo_message(conn.cursor(), datetime(2026, 11, 7, 9, 0, tzinfo=timezone.utc))  # as load_data.py
    return removed


def fix_message_count(conn, removed: dict[str, int], ids: list[str], mode: str) -> None:
    """The seeded Priya message is put back, so it does not count as 'removed' on the second run."""
    if mode == "spring":
        removed["messages"] = max(0, removed["messages"] - 1)


def restore_seats(conn) -> int:
    if not SEATS_FILE.exists():
        return 0
    saved = json.loads(SEATS_FILE.read_text(encoding="utf-8"))
    for section_id, enrolled in saved.items():
        conn.execute("UPDATE sections SET enrolled = %s WHERE section_id = %s", (enrolled, section_id))
    SEATS_FILE.unlink()
    return len(saved)


def fill_section(conn, section_id: str) -> None:
    row = conn.execute("SELECT enrolled, capacity FROM sections WHERE section_id = %s", (section_id,)).fetchone()
    if row is None or row[1] is None:
        sys.exit(f"Unknown section or no capacity: {section_id}")
    saved = json.loads(SEATS_FILE.read_text(encoding="utf-8")) if SEATS_FILE.exists() else {}
    saved.setdefault(section_id, row[0])           # keep the first (original) value if run twice
    SEATS_FILE.write_text(json.dumps(saved), encoding="utf-8")
    conn.execute("UPDATE sections SET enrolled = capacity WHERE section_id = %s", (section_id,))
    print(f"{section_id} is now full ({row[1]} of {row[1]}); it goes back to {saved[section_id]} on the next reset.")


def empty_mailpit() -> str:
    try:
        req = urllib.request.Request(MAILPIT_URL, method="DELETE")
        urllib.request.urlopen(req, timeout=5).read()
        return "Mailpit emptied"
    except (urllib.error.URLError, OSError) as exc:
        return f"WARNING: Mailpit not emptied ({exc}); is `docker compose up -d` running?"


def env_app_today() -> str:
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("APP_TODAY="):
                return line.split("=", 1)[1].split("#")[0].strip()
    return ""


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", choices=sorted(MODES), help="which demo to reset (default: spring, the Spring 2027 demo)")
    ap.add_argument("--fill-section", metavar="SECTION_ID", help="make this section full for the seat re-check demo")
    args = ap.parse_args()
    if not args.mode and not args.fill_section:
        args.mode = "spring"  # the default demo; --fill-section alone still only fills the section

    with psycopg.connect(database_url()) as conn:
        conn.execute("SET search_path TO advisor, public")
        if args.fill_section:
            fill_section(conn, args.fill_section)
            conn.commit()
            if not args.mode:
                return
        ids = student_ids(conn, args.mode)
        expected = len(SPRING_STUDENTS if args.mode == "spring" else FALL_STUDENTS)
        if len(ids) != expected:
            sys.exit(f"Found {len(ids)} of {expected} demo students for mode '{args.mode}'. "
                     "Run scripts/load_data.py --reset (and embed_courses.py) first.")
        removed = reset_students(conn, ids, args.mode)
        fix_message_count(conn, removed, ids, args.mode)
        seats = restore_seats(conn)
        conn.commit()

    print(f"Reset mode '{args.mode}' for {', '.join(ids)}:")
    for label, n in removed.items():
        print(f"  {label:<22}{n:>4}")
    print(f"  {'seats restored':<22}{seats:>4}")
    print(empty_mailpit())
    today, term = MODES[args.mode]  # term is the label shown to people
    current = env_app_today()
    if current != today:
        print(f"NOTE: this demo needs APP_TODAY={today} ({term} open); .env has '{current}'. "
              "Edit .env and restart the backend.")
    else:
        print(f"APP_TODAY={today} OK.")


if __name__ == "__main__":
    main()
