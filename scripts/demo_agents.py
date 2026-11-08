"""
Run the BUILD flow (Agent 1 audit -> Agent 2 electives -> Agent 3 schedule) for Priya, Daniel and Alex (M4 check).

    python scripts\\demo_agents.py                 # term open today, LLM from .env (Ollama by default)
    python scripts\\demo_agents.py --no-llm        # template text only (fast, no Ollama needed)
    python scripts\\demo_agents.py --term S27 --save

By default everything is rolled back afterwards (agent_runs included); --save commits the drafts.
"Today" is APP_TODAY from .env (empty = real date); the term must be plannable today (term_registration).
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.agents import build_draft  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.llm import LLMClient  # noqa: E402
from app.services.terms import TermNotPlannable, plannable_terms  # noqa: E402

STUDENTS = {"S0056": "Priya (continuing, AIM12)", "S0057": "Daniel (low QPA)", "S0126": "Alex (new, entry S27)"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--term", help="term to plan (default: the first term open today)")
    ap.add_argument("--no-llm", action="store_true", help="use template text instead of the LLM")
    ap.add_argument("--save", action="store_true", help="commit the drafts (default: roll back)")
    args = ap.parse_args()

    today = get_settings().today()
    llm = LLMClient(provider="template") if args.no_llm else LLMClient()
    with SessionLocal() as db:
        term = args.term or next((t.term_code for t in plannable_terms(db, today)), None)
        if term is None:
            print(f"No term is open for registration on {today}. Try APP_TODAY=2026-11-07 in .env.")
            return
        print(f"Today {today} -> planning {term}. LLM: {llm.provider}. {'Saving' if args.save else 'Dry run'}.")
        for student_id, who in STUDENTS.items():
            try:
                built = build_draft(db, student_id, term, llm=llm, today=today, commit=args.save)
            except TermNotPlannable as exc:
                print(f"Refused: {exc}")
                return
            show(student_id, who, built)
        if not args.save:
            db.rollback()


def show(student_id: str, who: str, built) -> None:
    print(f"\n{'=' * 100}\n{student_id}  {who}\n{'=' * 100}")
    print(f"AUDIT    {built.audit.explanation}")

    e = built.electives.output
    print(f"\nELECTIVES (basis: {e.basis}; weights {e.weights}) - {len(e.suggestions)} suggestions")
    titles = {"interest_match": "Matches your interests", "popular": "Popular with AIM students"}
    for group, title in titles.items():
        rows = [s for s in e.suggestions if s.group_code == group]
        if not rows:
            continue
        print(f"  {title}")
        print(f"    {'#':>2} {'course':7} {'score':>5} {'sim':>5} {'pop':>5} {'fit':>5}  {'matched interest':20} "
              f"{'flags':18} title / reason")
        for s in rows:
            sim = f"{s.similarity:.2f}" if s.similarity is not None else "-"
            print(f"    {s.rank:>2} {s.course_code:7} {s.final_score:>5.3f} {sim:>5} {s.popularity:>5.2f} "
                  f"{s.fit:>5.2f}  {(s.matched_interest or '-')[:20]:20} {','.join(s.flags) or '-':18} "
                  f"{s.title[:30]}: {s.reason}")

    d = built.schedule.output
    print(f"\nDRAFT {d.term_code} v{d.version}  valid={d.valid}  "
          f"(attempts {built.schedule.attempts}, LLM {built.schedule.llm.provider if built.schedule.llm else '-'})")
    print(f"  {'course':8} {'type':8} {'sect':5} {'session':8} {'days/times':34} {'units':>5}  title")
    for i in d.items:
        flags = "".join([" [locked]" if i.is_locked else "", " [conditional]" if i.is_conditional else "",
                         " [needs approval]" if i.needs_approval else ""])
        print(f"  {i.course_code:8} {i.item_type:8} {i.section_code:5} {i.session_code:8} {i.days_times[:34]:34} "
              f"{i.units:>5}  {i.title[:40]}{flags}")
    print(f"  {'':68} {d.total_units:>5}  total ({d.core_units} core + {d.elective_units} elective, "
          f"limit {d.max_units})")
    print(f"LOAD     {d.load_rationale}")
    if d.skipped:
        print("SKIPPED  " + "\n         ".join(f"{c}: {why}" for c, why in d.skipped.items()))
    print(f"WHY      {d.ai_explanation}")


if __name__ == "__main__":
    main()
