"""
Load the Gray DI advisor database.

Catalog (courses, offerings, sections) comes from the seed CSVs in data/seed/ (made once from the
client spreadsheet by scripts/import_excel.py). Program rules, core plan, people and history come from
scripts/reference_data.py and a fixed-seed generator, so every run produces the same data.

Usage (from the project folder, with the virtual environment active):
    python scripts/load_data.py --reset                 # drop + create schema, then load
"""
from __future__ import annotations

import argparse
import csv
import math
import os
import random
import re
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

import bcrypt
import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent))
import reference_data as ref  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SEED = 42
AS_OF = date(2026, 11, 7)           # "today" for deciding completed vs in-progress mini courses
DEMO_PASSWORD = "demo1234"
MAX_UNITS = 54                      # per term (tracks.max_units_per_term)
EMAIL_DOMAIN = "demo.graydi.local"

SEED_DIR = ROOT / "data" / "seed"
SYNTHETIC_INSTRUCTORS = [
    "Elena Park", "Marcus Reid", "Sofia Alvarez", "David Chen", "Amira Haddad", "Jonathan Pryce",
    "Nadia Kowalski", "Victor Osei", "Laura Bennett", "Rahul Menon", "Grace Holloway", "Tobias Klein",
    "Mei Tanaka", "Andre Silva", "Claire Dubois", "Ibrahim Saleh", "Hannah Weiss", "Oscar Lindqvist",
    "Ritu Sharma", "Patrick Doyle", "Yuki Mori", "Esther Nwosu", "Felix Romero", "Catherine Shaw",
]
AI_WORDS = ("artificial intelligence", " ai", "machine learning", "deep learning", "data", "agent",
            "generative", "language model", "analytics", "cloud")


# ------------------------------------------------------------------ helpers
def database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    env_file = ROOT / ".env"
    if not url and env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("DATABASE_URL="):
                url = line.split("=", 1)[1].strip()
    url = url or "postgresql://graydi:graydi_dev_pw@localhost:5432/graydi"
    return url.replace("postgresql+psycopg://", "postgresql://")


def none_if_nan(v):
    if v is None:
        return None
    if isinstance(v, float) and math.isnan(v):
        return None
    if isinstance(v, str) and not v.strip():
        return None
    return v


def read_seed(name: str) -> list[dict]:
    with open(SEED_DIR / name, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def num(v):
    v = none_if_nan(v)
    return float(v) if v is not None else None


def season_set(text) -> set[str]:
    text = none_if_nan(text) or ""
    if text.startswith("None"):
        return set()
    out = set()
    if "Both" in text:
        out |= {"Fall", "Spring"}
    for s in ("Fall", "Spring", "Summer"):
        if s in text:
            out.add(s)
    return out


# ---- prerequisites: text -> disjunctive normal form (list of AND-sets)
TOKEN = re.compile(r"\d{2}-\d{3}|\(|\)|\band\b|\bor\b", re.I)


def parse_prereq(text: str) -> list[frozenset[str]]:
    leftover = TOKEN.sub("", text).strip()
    if leftover:
        raise ValueError(f"unexpected text in prerequisite: {leftover!r}")
    tokens = [t.lower() for t in TOKEN.findall(text)]
    pos = 0

    def expr():
        nonlocal pos
        options = term()
        while pos < len(tokens) and tokens[pos] == "or":
            pos += 1
            options = options + term()
        return options

    def term():
        nonlocal pos
        options = factor()
        while pos < len(tokens) and tokens[pos] == "and":
            pos += 1
            right = factor()
            options = [a | b for a in options for b in right]
        return options

    def factor():
        nonlocal pos
        tok = tokens[pos]
        pos += 1
        if tok == "(":
            inner = expr()
            assert tokens[pos] == ")", "missing )"
            pos += 1
            return inner
        return [frozenset([tok])]

    result = expr()
    assert pos == len(tokens), "trailing tokens"
    unique = []
    for opt in result:                      # drop duplicates and supersets
        if not any(o <= opt for o in unique):
            unique = [o for o in unique if not opt <= o] + [opt]
    return unique


def prereq_ok(options: list[frozenset[str]], taken: set[str]) -> bool:
    return not options or any(opt <= taken for opt in options)


# ---- meeting times
SLOT = re.compile(r"^([MTWRFSU]+)\s+(\d{1,2}):(\d{2})(AM|PM)-(\d{1,2}):(\d{2})(AM|PM)$")


def to_time(h, m, ap) -> time:
    h = int(h) % 12 + (12 if ap == "PM" else 0)
    return time(h, int(m))


def parse_meetings(raw) -> list[tuple[str, time, time]]:
    raw = none_if_nan(raw)
    if not raw or raw == "TBA":
        return []
    out = []
    for part in raw.split(";"):
        if part.strip() == "TBA":           # e.g. 'MW 02:00PM-03:20PM; TBA'
            continue
        m = SLOT.match(part.strip())
        if not m:
            raise ValueError(f"cannot parse meeting time {raw!r}")
        days, h1, m1, a1, h2, m2, a2 = m.groups()
        for d in days:
            out.append((d, to_time(h1, m1, a1), to_time(h2, m2, a2)))
    return out


def seed_demo_message(cur, sent_at) -> None:
    """The one seeded message, Dr. Lee -> Priya (also used by scripts/demo_reset.py)."""
    cur.execute("""
        INSERT INTO messages (student_id, sender_user_id, body, sent_at)
        SELECT s.student_id, u.user_id,
               'Hi Priya, Spring 2027 planning is open. Build your schedule when you are ready and I will review it.',
               %s
        FROM students s, users u WHERE s.first_name='Priya' AND s.last_name='Raman' AND u.advisor_id='ADV001'""",
                (sent_at,))


# ------------------------------------------------------------------ loader
class Loader:
    def __init__(self, conn: psycopg.Connection):
        self.conn = conn
        self.cur = conn.cursor()
        self.rng = random.Random(SEED)
        self.courses: dict[str, dict] = {}
        self.prereqs: dict[str, list[frozenset[str]]] = {}
        self.sessions: dict[tuple[str, str], tuple[date, date]] = {}
        self.sections: dict[str, dict] = {}                 # section_id -> info
        self.sections_by_course_term: dict[tuple[str, str], list[str]] = defaultdict(list)
        self.offered: dict[str, set[str]] = defaultdict(set)  # term -> course codes offered
        self.term_seq = {code: seq for code, _, _, seq in ref.TERMS}
        self.term_season = {code: season for code, season, _, _ in ref.TERMS}
        self.latent_demand: dict[str, float] = {}
        self.core_by_track: dict[str, dict[int, list[str]]] = {
            "AIM12": ref.CORE_PLAN_AIM12, "AIM16": ref.CORE_PLAN_AIM16}
        self.core_set = {t: {c for cs in plan.values() for c in cs} for t, plan in self.core_by_track.items()}
        self.counts = Counter()

    def many(self, sql: str, rows: list, table: str):
        if rows:
            self.cur.executemany(sql, rows)
        self.counts[table] += len(rows)

    # -------------------------------------------------- A. reference
    def load_reference(self):
        self.many("INSERT INTO universities (university_code, name, short_name, email_domain) VALUES (%s,%s,%s,%s)",
                  ref.UNIVERSITIES, "universities")
        self.many("INSERT INTO programs (program_code, name, degree, college, university_code) VALUES (%s,%s,%s,%s,%s)",
                  ref.PROGRAMS, "programs")
        self.many("""INSERT INTO tracks (track_code, program_code, name, duration_months, total_units,
                     academic_terms, min_qpa, typical_units_per_term, max_units_per_term, max_units_extended,
                     extended_units_min_qpa, internship_required) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""", ref.TRACKS, "tracks")
        rows = [(t, seq, season, acad) for t, plan in ref.TRACK_TERM_PLAN.items()
                for seq, (season, acad) in plan.items()]
        self.many("INSERT INTO track_term_plan VALUES (%s,%s,%s,%s)", rows, "track_term_plan")
        rows = [(t, *v[:4], v[4]) for t, v in ref.ELECTIVE_REQUIREMENTS.items()]
        self.many("""INSERT INTO elective_requirements (track_code, min_units, max_units_outside_college,
                     max_undergrad_units, undergrad_min_grade, excluded_subject_areas) VALUES (%s,%s,%s,%s,%s,%s)""",
                  rows, "elective_requirements")
        rows = [(code, psycopg.types.json.Jsonb(p), sev, desc) for code, p, sev, desc in ref.RULE_SETTINGS]
        self.many("INSERT INTO rule_settings (rule_code, params, severity, description) VALUES (%s,%s,%s,%s)",
                  rows, "rule_settings")
        self.many("INSERT INTO grade_scale VALUES (%s,%s,%s,%s)", ref.GRADE_SCALE, "grade_scale")
        rows = [(code, name, appr) for name, (code, appr) in ref.COURSE_TYPES.items()]
        self.many("INSERT INTO course_types (type_code, name, needs_approval) VALUES (%s,%s,%s)",
                  rows, "course_types")

        term_rows, session_rows = [], []
        for code, season, year, seq in ref.TERMS:
            (fs, fe), minis = ref.term_dates(season, year)
            ro, rc = ref.registration_window(season, year)
            term_rows.append((code, season, year, seq, fs, fe, code == ref.CURRENT_TERM, ro, rc))
            session_rows.append((code, "FULL", fs, fe))
            self.sessions[(code, "FULL")] = (fs, fe)
            for mcode, (ms, me) in minis.items():
                session_rows.append((code, mcode, ms, me))
                self.sessions[(code, mcode)] = (ms, me)
        self.many("""INSERT INTO terms (term_code, season, year, seq, start_date, end_date, is_current,
                     registration_opens, registration_closes) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""", term_rows, "terms")
        self.many("INSERT INTO term_sessions VALUES (%s,%s,%s,%s)", session_rows, "term_sessions")

    # -------------------------------------------------- B. catalog
    def load_catalog(self):
        df = read_seed("courses.csv")
        referenced = set()
        for r in df:
            code = r["course_code"]
            text = none_if_nan(r["prereq_text"])
            if text:
                self.prereqs[code] = parse_prereq(text)
                referenced |= {c for opt in self.prereqs[code] for c in opt}
            coreq = none_if_nan(r["coreq_text"])
            if coreq:
                referenced |= set(re.findall(r"\d{2}-\d{3}", coreq))

        catalog_codes = {r["course_code"] for r in df}
        all_codes = catalog_codes | referenced
        subj_rows = []
        for s in sorted({c[:2] for c in all_codes}):
            name, home = ref.SUBJECTS.get(s, (None, False))
            subj_rows.append((s, name, home))
        self.many("INSERT INTO subjects VALUES (%s,%s,%s)", subj_rows, "subjects")

        type_map = {k: v[0] for k, v in ref.COURSE_TYPES.items()}
        rows = []
        for r in df:
            code = r["course_code"]
            typ = type_map[r["course_type"]]
            u_def, u_min, u_max = num(r["units_default"]), num(r["units_min"]), num(r["units_max"])
            info = dict(code=code, title=r["title"], desc=none_if_nan(r["description"]), type=typ,
                        units=u_def, seasons=season_set(r["typical_offering"]))
            self.courses[code] = info
            rows.append((code, code[:2], int(code[3:]), info["title"], info["desc"], typ,
                         r["units_type"], u_def, u_min, u_max, none_if_nan(r["units_note"]),
                         none_if_nan(r["prereq_text"]), none_if_nan(r["typical_offering"]), True))
        for code in sorted(referenced - catalog_codes):          # outside courses used as prerequisites
            rows.append((code, code[:2], int(code[3:]), f"Course {code} (outside catalog)", None, None,
                         "fixed", None, None, None, None, None, None, False))
        self.many("""INSERT INTO courses (course_code, subject_code, course_number, title, description, type_code,
                     units_type, units_default, units_min, units_max, units_note, prereq_text, typical_offering,
                     in_catalog) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""", rows, "courses")

        rows = [(c, i + 1, req) for c, opts in self.prereqs.items() for i, opt in enumerate(opts) for req in sorted(opt)]
        self.many("INSERT INTO course_prerequisites VALUES (%s,%s,%s)", rows, "course_prerequisites")
        rows = [(r["course_code"], cq) for r in df if none_if_nan(r["coreq_text"])
                for cq in re.findall(r"\d{2}-\d{3}", r["coreq_text"])]
        self.many("INSERT INTO course_corequisites VALUES (%s,%s)", rows, "course_corequisites")

        # core requirements (needs courses)
        rows = []
        for track, plan in self.core_by_track.items():
            for seq, codes in plan.items():
                for c in codes:
                    waiv = c in ref.WAIVABLE_BY_EXAM
                    note = ref.PREREQ_OVERRIDES.get(c)
                    rows.append((track, c, seq, waiv, "exemption_exam" if waiv else None, waiv, note is None, note))
        self.many("""INSERT INTO core_requirements (track_code, course_code, recommended_term_seq, waivable,
                     waiver_method, waiver_adds_elective_units, catalog_prereqs_apply, prereq_override_note)
                     VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                  rows, "core_requirements")

        # latent demand per course (drives synthetic history and seat fill)
        for code, info in self.courses.items():
            text = f" {info['title']} {info['desc'] or ''}".lower()
            boost = 0.25 if any(w in text for w in AI_WORDS) else 0.0
            self.latent_demand[code] = min(0.98, self.rng.betavariate(2.2, 2.2) * 0.8 + boost)

    def load_offerings_and_sections(self):
        off = read_seed("course_offerings.csv")
        rows = []
        for r in off:
            course, term, status = r["course_code"], r["term_code"], r["status"]
            url = f"/syllabi/{course}-{term}.pdf" if status == "offered" else None
            rows.append((course, term, status, int(r["active_sections"]), none_if_nan(r["course_length"]), url, False))
            if status == "offered":
                self.offered[term].add(course)
        # assumption A8: Summer 2027 offered like Summer 2026
        for r in off:
            if r["term_code"] == "M26" and r["status"] == "offered":
                rows.append((r["course_code"], "M27", "offered", int(r["active_sections"]),
                             none_if_nan(r["course_length"]), f"/syllabi/{r['course_code']}-M27.pdf", True))
                self.offered["M27"].add(r["course_code"])
        self.many("""INSERT INTO course_offerings (course_code, term_code, status, active_sections, course_length,
                     syllabus_url, is_assumed) VALUES (%s,%s,%s,%s,%s,%s,%s)""", rows, "course_offerings")

        sec = read_seed("sections.csv")
        # common meeting patterns per season, used to fill TBA times
        patterns = defaultdict(list)
        for r in sec:
            if r["days_times"] != "TBA" and ";" not in r["days_times"]:
                patterns[r["term_code"][0]].append(r["days_times"])

        instructor_ids: dict[str, int] = {}

        def instructor(name: str, synthetic: bool) -> int:
            if name not in instructor_ids:
                if synthetic:
                    slug = name.lower().replace(" ", ".")
                    self.cur.execute(
                        """INSERT INTO instructors (full_name, email, profile_url, bio, teaching_since, is_synthetic)
                           VALUES (%s,%s,%s,%s,%s,true) RETURNING instructor_id""",
                        (name, f"{slug}@{EMAIL_DOMAIN}", f"/instructors/{slug}",
                         "Synthetic instructor profile for the demo.", self.rng.randint(2008, 2023)))
                else:
                    self.cur.execute("INSERT INTO instructors (full_name) VALUES (%s) RETURNING instructor_id", (name,))
                instructor_ids[name] = self.cur.fetchone()[0]
                self.counts["instructors"] += 1
            return instructor_ids[name]

        def add_section(sid, course, term, scode, session, modality, location, status, meetings, raw,
                        synthetic_fields, names):
            typ = self.courses[course]["type"]
            if status == "offered":
                cap = (self.rng.choice([25, 30, 35, 40, 45, 50, 60, 70]) if typ == "regular"
                       else 120 if typ == "seminar" else 60 if typ == "capstone" else 15)
                synthetic_fields = synthetic_fields + ["capacity"]
            else:
                cap = None
            self.cur.execute(
                """INSERT INTO sections (section_id, course_code, term_code, section_code, session_code, modality,
                   location, status, capacity, enrolled, is_time_tba, days_times_raw, synthetic_fields)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,0,%s,%s,%s)""",
                (sid, course, term, scode, session, modality, location, status, cap, not meetings, raw,
                 synthetic_fields))
            self.counts["sections"] += 1
            for d, s, e in meetings:
                self.cur.execute("INSERT INTO section_meetings VALUES (%s,%s,%s,%s)", (sid, d, s, e))
                self.counts["section_meetings"] += 1
            for name, synth in names:
                self.cur.execute("INSERT INTO section_instructors VALUES (%s,%s)", (sid, instructor(name, synth)))
                self.counts["section_instructors"] += 1
            self.sections[sid] = dict(course=course, term=term, session=session, meetings=meetings,
                                      status=status, capacity=cap, modality=modality)
            if status == "offered":
                self.sections_by_course_term[(course, term)].append(sid)

        m26_rows = []
        for r in sec:
            sid, course, term, scode = r["section_id"], r["course_code"], r["term_code"], r["section_code"]
            status, session = r["status"], r["session_code"]
            meetings = parse_meetings(r["days_times"])
            synthetic = []
            typ = self.courses[course]["type"]
            if not meetings and status == "offered" and typ in ("regular", "seminar"):
                meetings = parse_meetings(self.rng.choice(patterns[term[0]]))
                synthetic.append("time")
            names = [(n.strip(), False) for n in r["instructors"].split(";") if n.strip()]
            if not names and status == "offered" and typ in ("regular", "seminar", "capstone"):
                names = [(self.rng.choice(SYNTHETIC_INSTRUCTORS), True)]
                synthetic.append("instructor")
            loc, modality, raw = none_if_nan(r["location"]), r["modality"], none_if_nan(r["days_times"])
            add_section(sid, course, term, scode, session, modality, loc, status, meetings, raw, synthetic, names)
            if term == "M26" and status == "offered":
                m26_rows.append((sid.replace("-M26-", "-M27-"), course, "M27", scode, session, modality, loc,
                                 status, meetings, raw, synthetic + ["copied_from_M26"], names))
        for row in m26_rows:
            add_section(*row)

    def generate_history(self):
        current_seq = self.term_seq[ref.CURRENT_TERM]
        rows = []
        for code, season, year, seq in ref.TERMS:
            if seq >= current_seq:
                continue
            for course, info in self.courses.items():
                if info["type"] not in ("regular", "seminar", "capstone") or season not in info["seasons"]:
                    continue
                d = self.latent_demand[course] + (0.04 * (year - 2021) if self.latent_demand[course] > 0.5 else 0)
                cap = self.rng.choice([30, 40, 45, 50, 60, 70, 80])
                fill = max(0.15, min(1.0, self.rng.gauss(d * 1.1, 0.12)))
                enrolled = round(cap * fill)
                wait = round(cap * self.rng.uniform(0.05, 0.35)) if fill >= 0.98 and d > 0.7 else 0
                hours = round(self.rng.uniform(2, 48) / max(d, 0.3), 1) if fill >= 0.98 else None
                rows.append((course, code, cap, enrolled, wait, hours))
        self.many("""INSERT INTO course_term_stats (course_code, term_code, capacity, enrolled, waitlist_peak,
                     hours_to_fill) VALUES (%s,%s,%s,%s,%s,%s)""", rows, "course_term_stats")

    # -------------------------------------------------- C/D. people and records
    def clash(self, a: str, b: str) -> bool:
        sa, sb = self.sections[a], self.sections[b]
        (a1, a2), (b1, b2) = self.sessions[(sa["term"], sa["session"])], self.sessions[(sb["term"], sb["session"])]
        if a2 < b1 or b2 < a1:
            return False
        for d1, s1, e1 in sa["meetings"]:
            for d2, s2, e2 in sb["meetings"]:
                if d1 == d2 and s1 < e2 and s2 < e1:
                    return True
        return False

    def pick_section(self, course: str, term: str, chosen: list[str]) -> str | None:
        options = list(self.sections_by_course_term.get((course, term), []))
        self.rng.shuffle(options)
        for sid in options:
            if not any(self.clash(sid, c) for c in chosen):
                return sid
        return None

    def term_session_end(self, sid: str | None, term: str) -> date:
        if sid is None:
            return self.sessions[(term, "FULL")][1]
        s = self.sections[sid]
        return self.sessions[(term, s["session"])][1]

    def grade_for(self, ability: float) -> str:
        scale = [(g, q) for g, q, *_ in ref.GRADE_SCALE if q is not None and q >= 1.67]
        x = self.rng.gauss(ability, 0.35)
        return min(scale, key=lambda gq: abs(gq[1] - x))[0]

    def elective_candidates(self, term: str, track: str, taken: set[str], persona: dict) -> list[str]:
        season = self.term_season[term]
        if term in self.offered:
            pool = self.offered[term]
        else:
            pool = {c for c, i in self.courses.items() if season in i["seasons"]}  # sorted below
        scored = []
        for c in sorted(pool):
            info = self.courses[c]
            if (info["type"] != "regular" or c in self.core_set[track] or c in taken
                    or int(c[3:]) < 600 or info["units"] not in (6.0, 12.0)
                    or not prereq_ok(self.prereqs.get(c, []), taken)):
                continue
            if term in self.offered and not self.sections_by_course_term.get((c, term)):
                continue
            text = f"{info['title']} {info['desc'] or ''}".lower()
            match = sum(1 for k in persona["keywords"] if k in text)
            score = match + self.latent_demand[c] * 1.5 + self.rng.random() * 0.8
            scored.append((score, c))
        return [c for _, c in sorted(scored, reverse=True)]

    def generate_people(self):
        # advisors
        rows = [(a, f, l, h, f"{f}.{l}@{EMAIL_DOMAIN}".lower(), t, ty, sc, ref.ADVISOR_UNIVERSITY)
                for a, f, l, h, t, ty, sc in ref.ADVISORS]
        self.many("""INSERT INTO advisors (advisor_id, first_name, last_name, honorific, email, title, advisor_type,
                     scope_track, university_code) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""", rows, "advisors")

        # cohorts: (entry_term, track, count, status)
        cohorts = [("F25", "AIM12", 30, "graduated"), ("F25", "AIM16", 25, "active"),
                   ("F26", "AIM12", 40, "active"), ("F26", "AIM16", 30, "active")]
        used_names = {(f, l) for f, l, *_ in ref.DEMO_STUDENTS}
        advisors_for = {"AIM12": ["ADV001", "ADV002", "ADV004", "ADV005", "ADV006"],
                        "AIM16": ["ADV003", "ADV004", "ADV005", "ADV006"]}
        personas = list(ref.PERSONAS)
        students = []
        n = 0
        for entry, track, count, status in cohorts:
            demo = [d for d in ref.DEMO_STUDENTS if d[2] == track] if entry == "F26" else []
            for i in range(count):
                n += 1
                if i < len(demo):
                    first, last, _, adv, persona, profile = demo[i]
                    is_demo = True
                else:
                    while True:
                        first, last = self.rng.choice(ref.FIRST_NAMES), self.rng.choice(ref.LAST_NAMES)
                        if (first, last) not in used_names:
                            break
                    adv = advisors_for[track][n % len(advisors_for[track])]
                    persona = self.rng.choice(personas)
                    profile = self.rng.choices(["strong", "normal", "low_qpa"], [0.3, 0.6, 0.1])[0]
                    is_demo = False
                used_names.add((first, last))
                students.append(dict(
                    id=f"S{n:04d}", first=first, last=last, track=track, advisor=adv, entry=entry, status=status,
                    persona=persona, profile=profile, is_demo=is_demo,
                    major=self.rng.choice(ref.PERSONAS[persona]["majors"]),
                    school=self.rng.choice(ref.UNDERGRAD_INSTITUTIONS),
                    tod=self.rng.choice(["morning", "afternoon", "evening"]),
                    mod=self.rng.choice(["in_person", "in_person", "hybrid", "remote"])))
        for first, last, track, adv, persona, entry, school, major in ref.NEW_STUDENTS:
            n += 1
            students.append(dict(id=f"S{n:04d}", first=first, last=last, track=track, advisor=adv, entry=entry,
                                 status="active", persona=persona, profile="normal", is_demo=True, is_new=True,
                                 major=major, school=school, tod="afternoon", mod="in_person"))
        for first, last, track, adv, persona, entry, school, major, _resume in ref.FALL_NEW_STUDENTS:
            n += 1       # no rng calls here: existing students must stay byte-identical
            students.append(dict(id=f"S{n:04d}", first=first, last=last, track=track, advisor=adv, entry=entry,
                                 status="active", persona=persona, profile="normal", is_demo=True, is_new=True,
                                 no_history=True, major=major, school=school, tod="afternoon", mod="in_person"))
        rows = [(s["id"], s["first"], s["last"], f"{s['first']}.{s['last']}@{EMAIL_DOMAIN}".lower(), s["track"],
                 s["advisor"], s["entry"], s["status"], s["school"], s["major"], s["tod"], s["mod"], s["is_demo"])
                for s in students]
        self.many("""INSERT INTO students (student_id, first_name, last_name, email, track_code, advisor_id,
                     entry_term, status, undergrad_institution, undergrad_major, preferred_time_of_day,
                     preferred_modality, is_demo) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                  rows, "students")

        term_by_seq = {seq: code for code, _, _, seq in ref.TERMS}
        cur_seq = self.term_seq[ref.CURRENT_TERM]
        enroll_rows, waiver_rows, doc_rows, interest_rows = [], [], [], []
        section_load = Counter()
        now = datetime(2026, 11, 7, 9, 0, tzinfo=timezone.utc)

        for s in students:
            persona = ref.PERSONAS[s["persona"]]
            ability = {"strong": 3.75, "normal": 3.4, "low_qpa": 2.55}[s["profile"]]
            entry_seq = self.term_seq[s["entry"]]
            plan_terms = ref.TRACK_TERM_PLAN[s["track"]]

            # exemption-exam waivers (transfer credit is out of scope)
            excused: set[str] = set()
            if not s["is_demo"]:
                roll = self.rng.random()
                if roll < 0.15:
                    excused.add("95-796")
                elif roll < 0.20:
                    excused.add("95-888")
                for c in sorted(excused):
                    waiver_rows.append((s["id"], c, "approved", s["advisor"],
                                        datetime(2000 + int(s["entry"][1:]), 8, 15, tzinfo=timezone.utc),
                                        "Passed exemption exam before program start"))
            elective_budget = 18 + 6 * len(excused)

            taken: set[str] = set(excused)
            for seq, (season, academic) in plan_terms.items():
                if s.get("no_history"):
                    break
                term = term_by_seq.get(entry_seq + {"AIM12": [0, 1, 2], "AIM16": [0, 1, 2, 3]}[s["track"]][seq - 1])
                if term is None or self.term_seq[term] > cur_seq:
                    break
                core = [c for c in self.core_by_track[s["track"]][seq] if c not in excused]
                core_units = sum(self.courses[c]["units"] or 0 for c in core)
                target = 48 if not academic else self.rng.choices([48, 54, 42], [0.7, 0.15, 0.15])[0]
                if s["profile"] == "low_qpa":
                    target = 42
                last_academic = seq == max(k for k, (_, a) in plan_terms.items() if a)
                room = MAX_UNITS - core_units if last_academic else target - core_units
                elective_units = 0 if not academic else max(0, min(room, elective_budget))
                chosen_sections: list[str] = []
                picks: list[tuple[str, str | None]] = []
                for c in core:
                    sid = self.pick_section(c, term, chosen_sections) if term in self.offered else None
                    if sid:
                        chosen_sections.append(sid)
                    picks.append((c, sid))
                got = 0
                for c in self.elective_candidates(term, s["track"], taken | {p for p, _ in picks}, persona):
                    u = self.courses[c]["units"]
                    if got + u > elective_units:
                        continue
                    sid = None
                    if term in self.offered:
                        sid = self.pick_section(c, term, chosen_sections)
                        if not sid:
                            continue
                        chosen_sections.append(sid)
                    picks.append((c, sid))
                    got += u
                    if got >= elective_units:
                        break
                elective_budget -= got
                for c, sid in picks:
                    units = self.courses[c]["units"] or 0
                    done = self.term_seq[term] < cur_seq or self.term_session_end(sid, term) < AS_OF
                    if done:
                        grade = "P" if units == 0 else self.grade_for(ability)
                        enroll_rows.append((s["id"], c, term, sid, units, "completed", grade))
                    else:
                        enroll_rows.append((s["id"], c, term, sid, units, "in_progress", None))
                    if sid:
                        section_load[sid] += 1
                    taken.add(c)

            # documents and interests (current students)
            if s["status"] == "active" and not s.get("no_history"):   # Fall admits upload their own resume
                doc_rows.append((s["id"], "resume", f"{s['first']}_{s['last']}_resume.txt", persona["resume"]))
                grad_year = (2000 + int(s['entry'][1:]) - 2) if s.get("is_new") \
                    else (2000 + int(s['entry'][1:]) - self.rng.randint(1, 5))
                doc_rows.append((s["id"], "undergrad_transcript", f"{s['first']}_{s['last']}_undergrad.txt",
                                 f"Bachelor's degree in {s['major']}, {s['school']}. Graduated {grad_year}."))
                if s.get("is_new"):
                    bucket = "suggested"          # new student must still confirm interests
                elif s["is_demo"]:
                    bucket = "confirmed"
                else:
                    bucket = self.rng.choices(["confirmed", "suggested", "none"], [0.4, 0.3, 0.3])[0]
                if bucket != "none":
                    for label in persona["interests"]:
                        interest_rows.append((s["id"], label, "resume", bucket,
                                              now - timedelta(days=30) if bucket == "confirmed" else None))

        self.many("""INSERT INTO enrollments (student_id, course_code, term_code, section_id, units, status, grade)
                     VALUES (%s,%s,%s,%s,%s,%s,%s)""", enroll_rows, "enrollments")
        self.many("""INSERT INTO course_waivers (student_id, course_code, status, decided_by, decided_at, note)
                     VALUES (%s,%s,%s,%s,%s,%s)""", waiver_rows, "course_waivers")
        self.many("""INSERT INTO student_documents (student_id, doc_type, filename, text_content)
                     VALUES (%s,%s,%s,%s)""", doc_rows, "student_documents")
        self.many("""INSERT INTO student_interests (student_id, label, source, status, confirmed_at)
                     VALUES (%s,%s,%s,%s,%s)""", interest_rows, "student_interests")

        # seats: our students + other registrants; S27 partly filled with a few nearly full sections
        for sid, info in self.sections.items():
            if info["status"] != "offered":
                continue
            cap, ours = info["capacity"], section_load[sid]
            term = info["term"]
            if term in ("F26", "M26"):
                enrolled = ours + round(max(0, cap - ours) * self.rng.uniform(0.5, 0.95))
            elif term == ref.PLANNING_TERM:
                enrolled = (cap - self.rng.randint(0, 2)) if self.rng.random() < 0.08 \
                    else round(cap * self.rng.uniform(0.0, 0.6) * (0.5 + self.latent_demand[info["course"]]))
            else:
                enrolled = round(cap * self.rng.uniform(0.0, 0.3))
            cap = max(cap, ours, enrolled)
            enrolled = min(max(enrolled, ours), cap)
            self.cur.execute("UPDATE sections SET capacity=%s, enrolled=%s WHERE section_id=%s", (cap, enrolled, sid))

        # logins (one bcrypt hash per user; same demo password for everyone)
        users = [("admin@" + EMAIL_DOMAIN, "admin", None, None)]
        users += [(f"{f}.{l}@{EMAIL_DOMAIN}".lower(), "advisor", None, a) for a, f, l, *_ in ref.ADVISORS]
        users += [(f"{s['first']}.{s['last']}@{EMAIL_DOMAIN}".lower(), "student", s["id"], None)
                  for s in students if s["status"] == "active"]
        rows = [(e, bcrypt.hashpw(DEMO_PASSWORD.encode(), bcrypt.gensalt(rounds=10)).decode(), r, sid, aid)
                for e, r, sid, aid in users]
        self.many("INSERT INTO users (email, password_hash, role, student_id, advisor_id) VALUES (%s,%s,%s,%s,%s)",
                  rows, "users")

        seed_demo_message(self.cur, now)
        self.counts["messages"] += 1

    def finish(self):
        self.cur.execute("REFRESH MATERIALIZED VIEW course_popularity")
        self.cur.execute("""INSERT INTO audit_log (actor_type, actor_id, action, entity_type, details)
                            VALUES ('system','load_data.py','initial_load','database', %s)""",
                         (psycopg.types.json.Jsonb(dict(self.counts)),))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--reset", action="store_true", help="drop and recreate schema 'advisor' first")
    args = ap.parse_args()

    missing = [n for n in ("courses.csv", "course_offerings.csv", "sections.csv") if not (SEED_DIR / n).exists()]
    if missing:
        sys.exit(f"Seed files missing in {SEED_DIR}: {missing}")

    with psycopg.connect(database_url()) as conn:
        if args.reset:
            conn.execute((ROOT / "db" / "schema.sql").read_text(encoding="utf-8"))
            print("schema 'advisor' recreated")
        conn.execute("SET search_path TO advisor, public")
        if conn.execute("SELECT count(*) FROM courses").fetchone()[0]:
            sys.exit("Tables already contain data. Run with --reset to reload from scratch.")
        loader = Loader(conn)
        loader.load_reference()
        loader.load_catalog()
        loader.load_offerings_and_sections()
        loader.generate_history()
        loader.generate_people()
        loader.finish()
        conn.commit()

    print("\nLoaded rows:")
    for table, count in sorted(loader.counts.items()):
        print(f"  {table:<24}{count:>7}")
    print(f"\nDemo logins: password '{DEMO_PASSWORD}', e.g. priya.raman@{EMAIL_DOMAIN}, morgan.lee@{EMAIL_DOMAIN}")


if __name__ == "__main__":
    main()
