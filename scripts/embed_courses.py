"""
Create pgvector embeddings for courses (title + description) and for student interests.

Run after load_data.py (first run downloads the ~90 MB MiniLM model):
    python scripts/embed_courses.py
    python scripts/embed_courses.py --test "AI governance in banks"     # try a similarity search
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date
from pathlib import Path

import psycopg
from sentence_transformers import SentenceTransformer

sys.path.insert(0, str(Path(__file__).resolve().parent))
from load_data import database_url  # noqa: E402

MODEL = "sentence-transformers/all-MiniLM-L6-v2"   # 384 dimensions = courses.embedding vector(384)


def vec(v) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in v) + "]"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", help="free-text interest to search with after embedding")
    args = ap.parse_args()

    model = SentenceTransformer(MODEL)
    with psycopg.connect(database_url()) as conn:
        conn.execute("SET search_path TO advisor, public")

        rows = conn.execute("""SELECT course_code, title, coalesce(description, '') FROM courses
                               WHERE in_catalog ORDER BY course_code""").fetchall()
        texts = [f"{title}. {desc}" for _, title, desc in rows]
        embs = model.encode(texts, batch_size=32, normalize_embeddings=True, show_progress_bar=True)
        with conn.cursor() as cur:
            cur.executemany("UPDATE courses SET embedding = %s::vector, embedded_at = now() WHERE course_code = %s",
                            [(vec(e), code) for (code, _, _), e in zip(rows, embs)])
        print(f"embedded {len(rows)} courses")

        rows = conn.execute("SELECT interest_id, label FROM student_interests WHERE embedding IS NULL").fetchall()
        if rows:
            embs = model.encode([label for _, label in rows], normalize_embeddings=True)
            with conn.cursor() as cur:
                cur.executemany("UPDATE student_interests SET embedding = %s::vector WHERE interest_id = %s",
                                [(vec(e), iid) for (iid, _), e in zip(rows, embs)])
        print(f"embedded {len(rows)} student interests")
        conn.commit()

        if args.test:
            q = vec(model.encode([args.test], normalize_embeddings=True)[0])
            today = date.fromisoformat(os.environ["APP_TODAY"]) if os.environ.get("APP_TODAY") else date.today()
            row = conn.execute("""SELECT term_code FROM term_registration(%s)
                                  WHERE status = 'open' AND has_offerings ORDER BY seq LIMIT 1""", (today,)).fetchone()
            if not row:
                print(f"\nNo term with open registration on {today}; set APP_TODAY=2026-11-07 to test.")
                return
            term = row[0]
            print(f"\nTop graduate courses offered in {term} (registration open on {today}) for: {args.test!r}")
            for code, title, sim in conn.execute("""
                    SELECT c.course_code, c.title, round((1 - (c.embedding <=> %s::vector))::numeric, 3)
                    FROM courses c
                    WHERE c.level = 'graduate'
                      AND EXISTS (SELECT 1 FROM course_offerings o WHERE o.course_code = c.course_code
                                  AND o.term_code = %s AND o.status = 'offered')
                    ORDER BY c.embedding <=> %s::vector LIMIT 8""", (q, term, q)):
                print(f"  {code}  {sim}  {title}")


if __name__ == "__main__":
    main()
