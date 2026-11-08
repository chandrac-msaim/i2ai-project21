"""
Reference data for the Gray DI advisor database.

Program facts come from the AIM handbook text (in Courses_data.xlsx, 'program requirements' sheet):
144 units over 3 academic terms, 126 core units, 18 elective units, max 54 units per term,
QPA 3.0 minimum, up to 36 units outside Heinz, up to 12 undergraduate units (petition, grade B or better).
Edit this file, then re-run:  python scripts/load_data.py --reset
"""
from datetime import date

UNIVERSITIES = [
    # university_code, name, short_name, email_domain (the demo accounts all use this domain)
    ("CMU", "Carnegie Mellon University", "CMU", "demo.graydi.local"),
]

PROGRAMS = [
    # program_code, name, degree, college, university_code
    ("AIM", "Artificial Intelligence Systems Management", "Master of Science", "Heinz College", "CMU"),
]

TRACKS = [
    # track_code, program_code, name, months, total_units, academic_terms, min_qpa, typical, max, extended max,
    # QPA must be above this for the extended max, internship
    ("AIM12", "AIM", "12-month", 12, 144, 3, 3.00, 48, 54, 60, 3.50, False),
    ("AIM16", "AIM", "16-month", 16, 144, 3, 3.00, 48, 54, 60, 3.50, True),
]

TRACK_TERM_PLAN = {
    # term_seq: (season, is_academic)
    "AIM12": {1: ("Fall", True), 2: ("Spring", True), 3: ("Summer", True)},
    "AIM16": {1: ("Fall", True), 2: ("Spring", True), 3: ("Summer", False), 4: ("Fall", True)},
}

# Core courses and the term they are normally taken in (balanced to 48 / 42 / 36 core units).
# AIM12: term 1 = Fall, 2 = Spring, 3 = Summer. AIM16: summer-only courses move to term 4 (Fall, assumed).
CORE_PLAN_AIM12 = {
    1: ["94-804", "94-879", "95-762", "95-796", "95-815", "95-888", "95-891", "95-886"],
    2: ["94-815", "94-844", "95-767", "95-864", "95-897"],
    3: ["95-724", "95-728", "95-731", "94-700", "94-885", "94-739"],  # 94-739 Capstone moved to Summer (7 Nov)
}
CORE_PLAN_AIM16 = {
    1: CORE_PLAN_AIM12[1],
    2: CORE_PLAN_AIM12[2] + ["94-739"],  # AIM16 unchanged: Capstone stays in term 2 (confirm with Gray DI)
    3: ["95-900"],                     # summer internship (0 units)
    4: [c for c in CORE_PLAN_AIM12[3] if c != "94-739"],
}
WAIVABLE_BY_EXAM = {"95-796", "95-888"}  # handbook: exemption exam, units move to electives

# Core courses whose catalog prerequisites are replaced by the program sequence (assumption A11 - confirm).
# 95-864 lists only outside ML courses (95-828, 10-601, ...) that AIM students never take;
# 95-891 lists 95-796, which AIM students take in the same first term.
PREREQ_OVERRIDES = {
    "95-864": "AIM sequence: 95-891 Intro to AI (term 1) replaces the catalog ML prerequisite.",
    "95-891": "AIM sequence: taken in term 1 together with 95-796 Statistics.",
}

ELECTIVE_REQUIREMENTS = {
    # min_units, max_outside_college, max_undergrad, undergrad_min_grade, excluded areas
    "AIM12": (18, 36, 12, "B", ["language", "music", "physical_education"]),
    "AIM16": (18, 36, 12, "B", ["language", "music", "physical_education"]),
}

RULE_SETTINGS = [
    ("UNIT_LIMIT", {"standard_max": "tracks.max_units_per_term", "extended_max": "tracks.max_units_extended",
                    "extended_requires_qpa_above": "tracks.extended_units_min_qpa"},
     "block", "Up to 54 units: can submit. 55-60 units: can submit only if cumulative QPA > 3.50 (shown to the "
     "advisor as UNIT_OVERLOAD); otherwise blocked. Over 60: blocked. No QPA yet (new student) = max 54."),
    ("SCHEDULE_FILL", {"fill_to": "tracks.max_units_per_term", "never_exceed": "tracks.max_units_per_term"}, None,
     "Agent 3 builds ONE draft: locked core courses, then electives by rank until the total reaches 54 units "
     "(never above 54; going to 55-60 is only the student's choice and needs QPA > 3.50). No lighter load."),
    ("LOW_QPA", {"source": "tracks.min_qpa"}, "high",
     "Cumulative QPA below the track minimum."),
    ("BEHIND_PACE", {"min_units_per_term": 42}, "medium",
     "Completed units are below the pace needed to finish in the planned terms."),
    ("LOW_SEATS", {"seats_left_below": 3}, "low",
     "A chosen section has fewer seats left than this threshold."),
    ("UNDERGRAD_CAP", {"max_course_number": 599, "max_units": 12, "needs_petition": True}, "medium",
     "Undergraduate courses need a petition and at most 12 units count toward the degree."),
    ("NEEDS_APPROVAL", {"course_types": ["independent_study", "internship", "study_abroad", "thesis", "residency"]},
     "info", "Course type requires advisor approval; the student may still add it."),
    ("CONDITIONAL_PREREQ", {}, "low",
     "Prerequisite is in progress in the current term; the course stays conditional until it is passed."),
    ("ELECTIVE_SCORE_WEIGHTS", {"similarity": 0.7, "popularity": 0.2, "fit": 0.1}, None,
     "Agent 2 score = 0.7 x interest similarity + 0.2 x popularity + 0.1 x fit. With no confirmed interests: "
     "popularity and fit only, re-normalised (0.67 / 0.33)."),
    ("ELECTIVE_MATCH_THRESHOLD", {"min_similarity": 0.40}, None,
     "A course counts as 'Matches your interests' only if its best similarity to a confirmed interest >= this; "
     "other courses go to the 'Popular with AIM students' group."),
    ("ELECTIVE_DIVERSITY", {"one_per_interest_first": True, "max_similarity_between_picks": 0.85, "top_n": 8},
     None, "Pick the best course for each confirmed interest first, then fill by score, skipping a course whose "
     "embedding is too similar (cosine similarity > max) to one already picked."),
    ("ELECTIVE_FIT", {"time_of_day": 0.4, "modality": 0.3, "no_clash": 0.3,
                      "morning_before": "12:00", "evening_from": "17:00", "few_seats_below": 5}, None,
     "fit (0..1) = 0.4 if a section meets in the student's preferred time of day + 0.3 if preferred modality + "
     "0.3 if a section has no clash with the locked core sections. Clashing courses are kept but flagged "
     "'time_clash'; sections with seats_left < few_seats_below are flagged 'few_seats'."),
    ("POPULARITY_WEIGHTS", {"track_share": 0.6, "demand": 0.4}, None,
     "Documented weights used in the course_popularity view."),
    ("SCHEDULE_MAX_RETRIES", {"n": 5}, None, "Agent 3 build -> validate -> swap loop limit."),
]

COURSE_TYPES = {
    # Excel value: (type_code, needs_approval)
    "Regular course": ("regular", False),
    "Independent Study": ("independent_study", True),
    "Internship": ("internship", True),
    "Professional Seminar": ("seminar", False),
    "Capstone / Project": ("capstone", False),
    "Residency": ("residency", True),
    "Study Abroad": ("study_abroad", True),
    "Thesis": ("thesis", True),
}

# CMU subject (department) codes. Heinz College uses 90-95; others are outside the college.
SUBJECTS = {
    "90": ("Heinz College - Public Policy and Management", True),
    "91": ("Heinz College", True),
    "93": ("Heinz College", True),
    "94": ("Heinz College", True),
    "95": ("Heinz College - Information Systems", True),
}

GRADE_SCALE = [
    # grade, quality_points, counts_units, is_passing
    ("A+", 4.33, True, True), ("A", 4.00, True, True), ("A-", 3.67, True, True),
    ("B+", 3.33, True, True), ("B", 3.00, True, True), ("B-", 2.67, True, True),
    ("C+", 2.33, True, True), ("C", 2.00, True, True), ("C-", 1.67, True, True),
    ("D", 1.00, True, True), ("R", 0.00, False, False),
    ("P", None, True, True), ("N", None, False, False), ("W", None, False, False),
    ("I", None, False, False),
]

# Terms: history F21..M26 (for popularity), current F26, planning S27, then M27, F27.
def _terms():
    out = [("F21", "Fall", 2021)]
    for year in range(2022, 2028):
        for season, code in (("Spring", "S"), ("Summer", "M"), ("Fall", "F")):
            out.append((f"{code}{year % 100:02d}", season, year))
    return [(code, season, year, i + 1) for i, (code, season, year) in enumerate(out)]

TERMS = _terms()                     # F21, S22, M22, F22, ..., F27
CURRENT_TERM = "F26"          # term in session on the demo date (7 Nov 2026)
PLANNING_TERM = "S27"         # registration open on the demo date; used only to fill synthetic seats


def registration_window(season: str, year: int):
    """Approximate CMU registration window: plan/register from 'opens' until the last day to add."""
    if season == "Fall":
        return date(year, 4, 1), date(year, 9, 4)
    if season == "Spring":
        return date(year - 1, 11, 1), date(year, 1, 22)
    return date(year, 3, 1), date(year, 5, 28)              # Summer


def term_dates(season: str, year: int):
    """Approximate CMU calendar; FULL plus mini sessions."""
    if season == "Fall":
        full = (date(year, 8, 24), date(year, 12, 11))
        minis = {"M1": (date(year, 8, 24), date(year, 10, 9)), "M2": (date(year, 10, 19), date(year, 12, 11))}
    elif season == "Spring":
        full = (date(year, 1, 11), date(year, 5, 1))
        minis = {"M3": (date(year, 1, 11), date(year, 3, 2)), "M4": (date(year, 3, 16), date(year, 5, 1))}
    else:
        full = (date(year, 5, 17), date(year, 8, 6))
        minis = {"M5": (date(year, 5, 17), date(year, 6, 25)), "M6": (date(year, 6, 28), date(year, 8, 6))}
    return full, minis


# ---------------------------------------------------------------- synthetic people (fixed seed)
ADVISORS = [
    # id, first, last, honorific, title, type, scope_track
    ("ADV001", "Morgan", "Lee", "Dr.", "AIM Program Advisor", "program", "AIM12"),
    ("ADV002", "Rachel", "Kim", None, "AIM Program Advisor", "program", "AIM12"),
    ("ADV003", "Samuel", "Ortiz", "Dr.", "AIM Program Advisor", "program", "AIM16"),
    ("ADV004", "Hannah", "Fischer", None, "Academic Advisor", "general", None),
    ("ADV005", "Kwame", "Mensah", None, "Academic Advisor", "general", None),
    ("ADV006", "Li", "Wei", "Dr.", "Academic Advisor", "general", None),
]

ADVISOR_UNIVERSITY = "CMU"  # every demo advisor belongs to this university

FIRST_NAMES = ["Aisha", "Ben", "Carmen", "Dev", "Elena", "Farah", "Gabriel", "Hiro", "Ines", "Jamal", "Kavya",
               "Liam", "Maya", "Noah", "Olu", "Pooja", "Quinn", "Rafael", "Sara", "Tomas", "Uma", "Victor",
               "Wen", "Ximena", "Yusuf", "Zoe", "Arjun", "Bianca", "Chen", "Diego", "Emma", "Fatima", "Grace",
               "Hassan", "Ivy", "Jun", "Karim", "Lucia", "Mateo", "Nina", "Omar", "Priyanka", "Rohan", "Sofia",
               "Tariq", "Valeria", "Wei", "Yara", "Zain", "Amara"]
LAST_NAMES = ["Adeyemi", "Bauer", "Castillo", "Das", "Evans", "Fernandes", "Gupta", "Hoffman", "Ibrahim", "Jensen",
              "Kapoor", "Lopez", "Martins", "Nakamura", "Okoro", "Petrov", "Qureshi", "Rossi", "Sato", "Tan",
              "Usman", "Varga", "Wong", "Xu", "Yilmaz", "Zhang", "Alvarez", "Brennan", "Choi", "Dubois", "Eze",
              "Farouk", "Garcia", "Haddad", "Iyer", "Jung", "Khan", "Liu", "Mehta", "Nguyen", "Owusu", "Park",
              "Reyes", "Singh", "Torres", "Vasquez", "Walsh", "Yamamoto", "Zhou", "Ali"]

# Interest personas: keywords pick electives; text becomes a synthetic resume.
PERSONAS = {
    "ai_governance_finance": {
        "interests": ["AI governance", "Financial services", "Model risk"],
        "keywords": ["risk", "financ", "governance", "policy", "ethic", "responsible", "audit", "bank", "account"],
        "majors": ["Finance", "Economics"],
        "resume": "Analyst at a regional bank (2 years) working on credit risk models and model validation. "
                  "Built reporting on model performance for the risk committee. Interested in AI governance and "
                  "regulation in financial services.",
    },
    "healthcare_analytics": {
        "interests": ["Healthcare analytics", "Data science", "Public health"],
        "keywords": ["health", "medic", "clinical", "care", "analytics", "data"],
        "majors": ["Biology", "Public Health"],
        "resume": "Data coordinator at a hospital network. Cleaned patient outcome data and built dashboards "
                  "for readmission rates. Wants to apply machine learning to healthcare operations.",
    },
    "cybersecurity": {
        "interests": ["Cybersecurity", "AI security", "Privacy"],
        "keywords": ["secur", "cyber", "privacy", "network", "crypt", "threat", "forensic"],
        "majors": ["Computer Science", "Information Technology"],
        "resume": "Security operations analyst for 3 years: incident response, SIEM tuning, vulnerability "
                  "management. Curious about attacks on ML models and securing AI systems.",
    },
    "product_data_science": {
        "interests": ["Product management", "Data science", "Experimentation"],
        "keywords": ["product", "data", "analytic", "market", "experiment", "customer", "visual"],
        "majors": ["Business Administration", "Statistics"],
        "resume": "Associate product manager at an e-commerce startup. Ran A/B tests and defined product "
                  "metrics with the data team. Wants to lead AI-powered products.",
    },
    "public_policy": {
        "interests": ["Public policy", "Government technology", "AI ethics"],
        "keywords": ["policy", "public", "government", "law", "ethic", "social", "urban"],
        "majors": ["Political Science", "Public Policy"],
        "resume": "Policy fellow at a city government digital services team. Wrote guidance on algorithm use "
                  "in public services. Interested in responsible AI in government.",
    },
    "ai_agents_engineering": {
        "interests": ["AI agents", "Software engineering", "LLM applications"],
        "keywords": ["agent", "software", "machine learning", "deep", "language", "cloud", "develop", "web"],
        "majors": ["Computer Science", "Software Engineering"],
        "resume": "Software engineer (3 years) building backend APIs in Python. Shipped an internal chatbot "
                  "using an LLM API. Wants hands-on work building AI agents.",
    },
    "strategy_consulting": {
        "interests": ["Strategy", "Consulting", "Change management"],
        "keywords": ["consult", "strateg", "lead", "organiz", "negotiat", "manag", "innovation"],
        "majors": ["Management", "Economics"],
        "resume": "Business analyst at a consulting firm supporting digital transformation projects. "
                  "Facilitated workshops and process redesign. Plans a career in AI strategy consulting.",
    },
    "mlops_data_engineering": {
        "interests": ["MLOps", "Data engineering", "Cloud"],
        "keywords": ["data", "cloud", "pipeline", "database", "distributed", "big data", "engineer", "devops"],
        "majors": ["Computer Engineering", "Information Systems"],
        "resume": "Data engineer maintaining batch and streaming pipelines on cloud infrastructure. "
                  "Supported data scientists deploying models. Wants to specialise in MLOps.",
    },
}

UNDERGRAD_INSTITUTIONS = ["Northfield University", "Lakeshore State University", "Riverside Institute of Technology",
                          "Eastbrook College", "Westmont University", "Harborview University"]

# New admit with no history (starts Spring 2027): shows the new-student path and the confirm-interests flow.
# first, last, track, advisor, persona, entry term, undergrad institution, major
NEW_STUDENTS = [
    ("Alex", "Rivera", "AIM12", "ADV001", "ai_agents_engineering", "S27", "Riverside Institute of Technology",
     "Computer Science"),
]

# Fall 2026 demo mode (APP_TODAY=2026-07-15): three new admits with no history, no documents and no interests.
# The demo uploads the resume file from data/demo/. Names are not in FIRST_NAMES / LAST_NAMES (no clash with
# generated students). first, last, track, advisor, persona (only used for the loader's lookups), entry term,
# undergrad institution, major, resume file in data/demo/
FALL_NEW_STUDENTS = [
    ("Selin", "Aksoy", "AIM12", "ADV001", "ai_governance_finance", "F26", "Eastbrook College",
     "Law and Economics", "resume_ai_governance.txt"),
    ("Jonas", "Lindgren", "AIM12", "ADV001", "ai_agents_engineering", "F26", "Riverside Institute of Technology",
     "Computer Science", "resume_ai_agents.txt"),
    ("Imani", "Thompson", "AIM12", "ADV001", "strategy_consulting", "F26", "Westmont University",
     "Business Management", "resume_strategy_consulting.txt"),
]

# Named demo students (match the mockups)
DEMO_STUDENTS = [
    # first, last, track, advisor, persona, profile
    ("Priya", "Raman", "AIM12", "ADV001", "ai_governance_finance", "strong"),
    ("Daniel", "Okafor", "AIM12", "ADV001", "strategy_consulting", "low_qpa"),
]
