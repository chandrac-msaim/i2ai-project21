"""Interest matching: MiniLM embeddings + pgvector cosine distance (<=>).

Eligibility is in the SQL WHERE clause, so ranking never sees ineligible courses: graduate level, offered in
the given term with an offered section that has seats (NULL capacity = no limit, as in the rule engine),
and not excluded.
"""
from collections.abc import Iterable, Sequence
import threading
from typing import TYPE_CHECKING

from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import get_settings

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer


class CourseMatch(BaseModel):
    course_code: str
    title: str
    course_number: int
    similarity: float  # 1 - cosine distance to the closest interest
    best_interest: int  # index into interest_vectors of that closest interest


_model: "SentenceTransformer | None" = None
_model_lock = threading.Lock()


def get_embedding_model() -> "SentenceTransformer":
    """Loaded once per process (slow import, ~90 MB model) and shared by every request.

    Thread-safe: API routes run in a thread pool, so two requests that arrive before the first load has finished wait for
    that one load instead of each loading their own copy (plain lru_cache does not lock). `app.main` also warms it at startup.
    """
    global _model
    if _model is None:
        with _model_lock:
            if _model is None:  # re-check: another thread may have loaded it while this one waited
                from sentence_transformers import SentenceTransformer

                _model = SentenceTransformer(get_settings().embedding_model)
    return _model


def _reset_embedding_model() -> None:
    """Forget the loaded model (tests only)."""
    global _model
    with _model_lock:
        _model = None


def embed_texts(texts: Sequence[str]) -> list[list[float]]:
    """Normalised 384-dim vectors, same model and settings as scripts/embed_courses.py."""
    if not texts:
        return []
    vectors = get_embedding_model().encode(list(texts), normalize_embeddings=True)
    return [[float(x) for x in v] for v in vectors]


def embed_text(value: str) -> list[float]:
    return embed_texts([value])[0]


_SEARCH = text("""
WITH q AS (
    SELECT (u.ord - 1)::int AS idx, u.v::vector AS v
    FROM unnest(CAST(:vectors AS text[])) WITH ORDINALITY AS u(v, ord)
),
scored AS (
    SELECT c.course_code, c.title, c.course_number, q.idx,
           1 - (c.embedding <=> q.v) AS similarity,
           row_number() OVER (PARTITION BY c.course_code ORDER BY c.embedding <=> q.v, q.idx) AS rn
    FROM courses c
    CROSS JOIN q
    WHERE c.level = 'graduate'
      AND c.embedding IS NOT NULL
      AND c.course_code <> ALL(CAST(:exclude AS text[]))
      AND EXISTS (SELECT 1 FROM course_offerings o
                  JOIN sections s ON s.course_code = o.course_code AND s.term_code = o.term_code
                  WHERE o.course_code = c.course_code AND o.term_code = :term_code
                    AND o.status = 'offered' AND s.status = 'offered'
                    AND (s.capacity IS NULL OR s.seats_left > 0))
)
SELECT course_code, title, course_number, idx, similarity
FROM scored WHERE rn = 1
ORDER BY similarity DESC, course_code
LIMIT :limit
""")


def search_course_content(db: Session, interest_vectors: Sequence[Sequence[float]], term_code: str,
                          exclude: Iterable[str] = (), limit: int = 10) -> list[CourseMatch]:
    """Eligible courses in `term_code` closest to any of the interest vectors, best first."""
    if not interest_vectors:
        return []
    rows = db.execute(_SEARCH, {
        "vectors": [_vector_literal(v) for v in interest_vectors],
        "term_code": term_code,
        "exclude": sorted(set(exclude)),
        "limit": limit,
    })
    return [CourseMatch(course_code=r.course_code, title=r.title, course_number=r.course_number,
                        similarity=round(float(r.similarity), 3), best_interest=r.idx) for r in rows]


class CoursePair(BaseModel):
    course_a: str
    course_b: str  # course_a < course_b
    similarity: float  # cosine similarity of the two course embeddings


def course_similarities(db: Session, course_codes: Sequence[str]) -> list[CoursePair]:
    """Cosine similarity of every pair of the given courses (to skip near-duplicate suggestions)."""
    rows = db.execute(text("""
        SELECT a.course_code AS ca, b.course_code AS cb, 1 - (a.embedding <=> b.embedding) AS sim
        FROM courses a JOIN courses b ON a.course_code < b.course_code
        WHERE a.course_code = ANY(CAST(:codes AS text[])) AND b.course_code = ANY(CAST(:codes AS text[]))
          AND a.embedding IS NOT NULL AND b.embedding IS NOT NULL"""), {"codes": sorted(set(course_codes))})
    return [CoursePair(course_a=r.ca, course_b=r.cb, similarity=round(float(r.sim), 3)) for r in rows]


def _vector_literal(vector: Sequence[float]) -> str:
    return "[" + ",".join(f"{float(x):.6f}" for x in vector) + "]"
