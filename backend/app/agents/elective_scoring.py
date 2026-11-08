"""Elective scoring (D16): similarity per interest, popularity, fit, two groups, diversity. Pure functions.

Every number comes from rule_settings (ELECTIVE_SCORE_WEIGHTS, ELECTIVE_MATCH_THRESHOLD, ELECTIVE_DIVERSITY,
ELECTIVE_FIT) and is passed in as `Settings`; nothing is hard-coded here.

    score = w_similarity x similarity + w_popularity x popularity + w_fit x fit
    (no confirmed interests: (w_popularity x popularity + w_fit x fit) / (w_popularity + w_fit))
"""
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import time

from app.agents.core_plan import clashes_with, usable
from app.tools.catalog import SectionOut


@dataclass(frozen=True)
class Settings:
    w_similarity: float
    w_popularity: float
    w_fit: float
    min_similarity: float  # a course is in group 'interest_match' from this similarity on
    top_n: int
    one_per_interest_first: bool
    max_similarity_between_picks: float  # a pick this similar to an earlier pick is skipped
    fit_time_of_day: float
    fit_modality: float
    fit_no_clash: float
    evening_from: time
    morning_before: time
    few_seats_below: int

    @classmethod
    def from_rule_settings(cls, rules: Mapping[str, Mapping]) -> "Settings":
        try:
            w, m, d, f = (rules["ELECTIVE_SCORE_WEIGHTS"], rules["ELECTIVE_MATCH_THRESHOLD"],
                          rules["ELECTIVE_DIVERSITY"], rules["ELECTIVE_FIT"])
            return cls(
                w_similarity=float(w["similarity"]), w_popularity=float(w["popularity"]), w_fit=float(w["fit"]),
                min_similarity=float(m["min_similarity"]), top_n=int(d["top_n"]),
                one_per_interest_first=bool(d["one_per_interest_first"]),
                max_similarity_between_picks=float(d["max_similarity_between_picks"]),
                fit_time_of_day=float(f["time_of_day"]), fit_modality=float(f["modality"]),
                fit_no_clash=float(f["no_clash"]), evening_from=time.fromisoformat(f["evening_from"]),
                morning_before=time.fromisoformat(f["morning_before"]), few_seats_below=int(f["few_seats_below"]))
        except KeyError as exc:
            raise ValueError(f"rule_settings is missing {exc} for the elective scoring") from exc


@dataclass
class Candidate:
    course_code: str
    sims: list[float]  # similarity to each confirmed interest (same order as the interests); [] = none
    popularity: float
    fit: float = 0.0
    flags: list[str] = field(default_factory=list)

    def best(self) -> tuple[int, float]:
        """(index, similarity) of the closest interest; (-1, 0.0) without interests."""
        if not self.sims:
            return -1, 0.0
        i = max(range(len(self.sims)), key=lambda k: (self.sims[k], -k))
        return i, max(self.sims[i], 0.0)


@dataclass(frozen=True)
class Pick:
    course_code: str
    similarity: float | None
    popularity: float
    fit: float
    final_score: float
    interest: int | None  # index of the matched (closest) interest; None without confirmed interests
    group_code: str  # interest_match (similarity >= threshold) | popular
    flags: tuple[str, ...]


def score(c: Candidate, similarity: float | None, s: Settings) -> float:
    if similarity is None:
        total = s.w_popularity + s.w_fit
        return (s.w_popularity * c.popularity + s.w_fit * c.fit) / total if total else 0.0
    return s.w_similarity * similarity + s.w_popularity * c.popularity + s.w_fit * c.fit


# ---------- fit and flags ----------

def bucket(start: time, s: Settings) -> str:
    """morning < morning_before (12:00), evening >= evening_from (17:00), else afternoon."""
    return "morning" if start < s.morning_before else "evening" if start >= s.evening_from else "afternoon"


def section_fit(section: SectionOut, core_sections: Sequence[SectionOut], time_pref: str | None,
                modality_pref: str | None, s: Settings) -> float:
    """0..1: preferred time of day + preferred modality + no clash with the locked core (weights from ELECTIVE_FIT).
    No stated preference counts as a match; a section with no meeting times matches the time preference by half."""
    if time_pref is None:
        tod = 1.0
    elif section.is_time_tba or not section.meetings:
        tod = 0.5
    else:
        tod = sum(bucket(m.start, s) == time_pref for m in section.meetings) / len(section.meetings)
    modality = 1.0 if modality_pref is None or section.modality == modality_pref else 0.0
    no_clash = 0.0 if clashes_with(section, core_sections) else 1.0
    weights = s.fit_time_of_day + s.fit_modality + s.fit_no_clash
    if not weights:
        return 0.0
    return (s.fit_time_of_day * tod + s.fit_modality * modality + s.fit_no_clash * no_clash) / weights


def fit_and_flags(sections: Sequence[SectionOut], core_sections: Sequence[SectionOut], time_pref: str | None,
                  modality_pref: str | None, s: Settings) -> tuple[float, list[str]]:
    """Course fit = its best usable section. Flags: time_clash (every usable section clashes with the locked core:
    kept, not removed) and few_seats (the most seats in any usable section is below few_seats_below)."""
    options = [x for x in sections if usable(x)]
    if not options:
        return 0.0, []
    flags: list[str] = []
    if all(clashes_with(x, core_sections) for x in options):
        flags.append("time_clash")
    if all(x.seats_left is not None for x in options) and max(x.seats_left for x in options) < s.few_seats_below:
        flags.append("few_seats")
    return max(section_fit(x, core_sections, time_pref, modality_pref, s) for x in options), flags


# ---------- selection ----------

def select(cands: Sequence[Candidate], n_interests: int, similar: Mapping[tuple[str, str], float],
           s: Settings, top_n: int | None = None) -> list[Pick]:
    """The top suggestions, best first.

    With confirmed interests: first the best course for each interest (so every interest is covered, even one
    with no course above the threshold), then fill by score; a course whose embedding similarity to an already
    picked course is above max_similarity_between_picks is skipped. matched_interest is always the closest
    interest; group_code is 'interest_match' only from the similarity threshold on, else 'popular'.
    Without interests: popularity + fit only, all in group 'popular'.
    """
    top_n = top_n or s.top_n
    picked: list[Pick] = []

    def dup(code: str) -> bool:
        return any(similar.get(_key(code, p.course_code), 0.0) > s.max_similarity_between_picks for p in picked)

    def taken(code: str) -> bool:
        return any(p.course_code == code for p in picked)

    def make(c: Candidate, interest: int | None, sim: float | None) -> Pick:
        matched = sim is not None and sim >= s.min_similarity
        return Pick(course_code=c.course_code, similarity=sim, popularity=c.popularity, fit=c.fit,
                    final_score=score(c, sim, s), interest=interest,
                    group_code="interest_match" if matched else "popular", flags=tuple(c.flags))

    if n_interests == 0:
        for c in sorted(cands, key=lambda c: (-score(c, None, s), c.course_code)):
            if len(picked) < top_n and not dup(c.course_code):
                picked.append(make(c, None, None))
        return picked

    if s.one_per_interest_first:
        order = sorted(range(n_interests), key=lambda i: -max((score(c, c.sims[i], s) for c in cands), default=0.0))
        for i in order:  # contested courses go to the interest that scores them highest
            ranked = sorted(cands, key=lambda c: (-score(c, c.sims[i], s), c.course_code))
            best = next((c for c in ranked if not taken(c.course_code) and not dup(c.course_code)), None)
            if best is not None and len(picked) < top_n:
                picked.append(make(best, i, best.sims[i]))

    for c in sorted(cands, key=lambda c: (-score(c, c.best()[1], s), c.course_code)):
        if len(picked) >= top_n:
            break
        if not taken(c.course_code) and not dup(c.course_code):
            i, sim = c.best()
            picked.append(make(c, i, sim))
    return sorted(picked, key=lambda p: (-p.final_score, p.course_code))


def _key(a: str, b: str) -> tuple[str, str]:
    return (a, b) if a < b else (b, a)
