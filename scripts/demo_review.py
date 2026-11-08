"""
Submit and approve a schedule end to end (M5 check): build Priya's S27 draft, she submits it (Agent 4 risk flags,
Agent 5 gate), her advisor approves it (live re-check, audit, notification) and the approval email goes to Mailpit.

    python scripts\\demo_review.py                  # approve Priya's draft; LLM from .env (Ollama by default)
    python scripts\\demo_review.py --student S0057  # Daniel: shows LOW_QPA
    python scripts\\demo_review.py --reject "Please add a second elective."
    python scripts\\demo_review.py --no-llm --save

Dry run by default: the database is rolled back afterwards, but the email IS sent. Open the inbox at
http://localhost:8025. --save keeps the approved schedule (it then blocks building again for that term).
"""
import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.agents import build_draft  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.llm import LLMClient  # noqa: E402
from app.models import Student  # noqa: E402
from app.services.schedule_review import ComplianceBlocked, decide, submit_for_review  # noqa: E402
from app.services.terms import plannable_terms  # noqa: E402


@dataclass
class Who:
    role: str
    student_id: str | None = None
    advisor_id: str | None = None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--student", default="S0056")
    ap.add_argument("--reject", metavar="COMMENT", help="reject with this comment instead of approving")
    ap.add_argument("--comment", default="Looks good, enjoy the term.")
    ap.add_argument("--no-llm", action="store_true")
    ap.add_argument("--save", action="store_true", help="commit (default: roll back; the email is sent anyway)")
    args = ap.parse_args()

    today = get_settings().today()
    llm = LLMClient(provider="template") if args.no_llm else LLMClient()
    with SessionLocal() as db:
        term = next((t.term_code for t in plannable_terms(db, today)), None)
        if term is None:
            print(f"No term is open for registration on {today}. Try APP_TODAY=2026-11-07 in .env.")
            return
        student = db.get(Student, args.student)
        kw = {"llm": llm, "today": today, "commit": args.save}
        print(f"Today {today}, term {term}, LLM {llm.provider}. {'Saving' if args.save else 'Dry run (rolled back)'}.")

        schedule_id = build_draft(db, args.student, term, replace=True, **kw).schedule.output.schedule_id
        print(f"\n1. Draft built ({schedule_id})")

        try:
            sub = submit_for_review(db, Who("student", student_id=args.student), schedule_id, **kw)
        except ComplianceBlocked as exc:
            print(f"2. Submit BLOCKED: {exc}")
            return
        print(f"2. Submitted -> {sub.status}. Risk flags ({len(sub.risk.flags)}):")
        for f in sub.risk.flags:
            print(f"     [{f.severity:6}] {f.flag_code:18} {f.message}\n{'':32}-> {f.suggested_action}")
        print("   Compliance checks (submit):")
        for c in sub.compliance.checks:
            print(f"     {'PASS' if c.passed else 'FLAG'} {c.code:14} {c.detail[:110]}")

        decision = "reject" if args.reject else "approve"
        res = decide(db, Who("advisor", advisor_id=student.advisor_id), schedule_id, decision,
                     args.reject or args.comment, **kw)
        print(f"\n3. Advisor {decision}d -> {res.status}")
        print(f"   Email '{res.compliance.email_subject}' to {student.email}: "
              f"{'sent' if res.compliance.email_sent else 'NOT sent: ' + str(res.compliance.email_error)}")
        print("   Open http://localhost:8025 to read it.")
        if not args.save:
            db.rollback()


if __name__ == "__main__":
    main()
