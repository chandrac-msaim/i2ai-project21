"""Popularity tool: reads the materialized view course_popularity (recency-weighted share + demand)."""
from collections.abc import Iterable
from decimal import Decimal

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.views import CoursePopularity


class PopularityOut(BaseModel):
    course_code: str
    popularity: Decimal  # raw value from the view
    score: float  # 0-1, normalised against the most popular course in the track (DESIGN section 4)
    track_share: Decimal
    demand: Decimal


def rank_courses_by_popularity(db: Session, track_code: str, course_codes: Iterable[str] | None = None,
                               limit: int | None = None) -> list[PopularityOut]:
    """Courses ranked by popularity for a track, optionally only `course_codes`, most popular first."""
    top = db.scalar(select(func.max(CoursePopularity.popularity))
                    .where(CoursePopularity.track_code == track_code)) or Decimal(0)
    query = (select(CoursePopularity).where(CoursePopularity.track_code == track_code)
             .order_by(CoursePopularity.popularity.desc(), CoursePopularity.course_code))
    if course_codes is not None:
        query = query.where(CoursePopularity.course_code.in_(list(course_codes)))
    if limit is not None:
        query = query.limit(limit)
    return [PopularityOut(course_code=p.course_code, popularity=p.popularity,
                          score=round(float(p.popularity / top), 3) if top else 0.0,
                          track_share=p.track_share, demand=p.demand)
            for p in db.scalars(query)]
