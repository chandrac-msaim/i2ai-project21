"""
Show the vector-search tool on free text, for the term that can be planned today (M3 check).

    python scripts\\demo_search.py                                  # "AI governance in banks"
    python scripts\\demo_search.py "computer vision for retail" --limit 5

"Today" is APP_TODAY from .env (empty = real date); the term comes from term_registration(today).
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.config import get_settings  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.services.terms import plannable_terms  # noqa: E402
from app.tools.vector_search import embed_text, search_course_content  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("text", nargs="?", default="AI governance in banks")
    ap.add_argument("--limit", type=int, default=8)
    args = ap.parse_args()

    today = get_settings().today()
    with SessionLocal() as db:
        terms = plannable_terms(db, today)
        if not terms:
            print(f"No term is open for registration on {today}. Try APP_TODAY=2026-11-07 in .env.")
            return
        term = terms[0].term_code
        matches = search_course_content(db, [embed_text(args.text)], term, limit=args.limit)
    print(f"Today {today} -> planning term {term}. Graduate courses with open seats for {args.text!r}:")
    for m in matches:
        print(f"  {m.course_code}  {m.similarity:.3f}  {m.title}")


if __name__ == "__main__":
    main()
