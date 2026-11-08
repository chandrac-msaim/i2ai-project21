"""
One-time import: Courses_data.xlsx -> clean seed CSVs in data/seed/.

Only needed again if Gray DI sends a new catalog spreadsheet:
    pip install pandas openpyxl
    python scripts/import_excel.py path/to/Courses_data.xlsx
The loader (load_data.py) reads the CSVs, never the spreadsheet.
"""
from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "seed"

MODALITY = {
    "In-person Expectation": "in_person",
    "In-person Expectation Rotation": "in_person",
    "Remote only": "remote",
    "In-person + remote": "hybrid",
    "In-Person + Technology": "hybrid",
}
NO_INSTRUCTOR = {"Not listed", "Unverified (term unavailable)", "Unverified (cancelled section)"}


def clean(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


def session_for(term_code: str, mini_flag: str, section_code: str, course_length: str) -> str:
    if mini_flag != "Yes":
        return "FULL"
    allowed = {"F": "12", "S": "34", "M": "56"}[term_code[0]]
    if section_code[-1].isdigit() and section_code[-1] in allowed:
        return "M" + section_code[-1]
    minis = re.findall(r"Mini (\d)", course_length or "")
    return "M" + minis[0] if len(minis) == 1 else "FULL"


def write(name: str, header: list[str], rows: list[list]):
    OUT.mkdir(parents=True, exist_ok=True)
    with open(OUT / name, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)
    print(f"  {name:<24}{len(rows):>5} rows")


def main(xlsx: Path):
    xl = pd.ExcelFile(xlsx)

    c = xl.parse("Courses")
    c = c[c.course_id.notna()]
    write("courses.csv",
          ["course_code", "title", "course_type", "units_type", "units_default", "units_min", "units_max",
           "units_note", "prereq_text", "coreq_text", "typical_offering", "description"],
          [[clean(r.course_id), clean(r.title), clean(r.course_type), clean(r.units_type).lower(),
            clean(r.units_typical), clean(r.units_min), clean(r.units_max), clean(r.units_note),
            clean(r.prerequisites), clean(r.corequisites), clean(r.typical_terms_offered), clean(r.description)]
           for _, r in c.iterrows()])

    o = xl.parse("Courses Terms S27,F26,M26")
    write("course_offerings.csv",
          ["course_code", "term_code", "status", "active_sections", "course_length"],
          [[clean(r.course_id), clean(r.term_code), clean(r.status).lower(), clean(r.active_sections),
            clean(r.course_length)] for _, r in o.iterrows()])
    length = {(r.course_id, r.term_code): clean(r.course_length) for _, r in o.iterrows()}

    s = xl.parse("Course section - schedule")
    rows = []
    for _, r in s.iterrows():
        names = [n.strip() for n in clean(r.instructor_name).split(";") if n.strip() and n.strip() not in NO_INSTRUCTOR]
        loc = clean(r.teaching_location)
        rows.append([clean(r.section_id), clean(r.course_id), clean(r.term), clean(r.section_code),
                     session_for(r.term, r["mini"], clean(r.section_code), length.get((r.course_id, r.term), "")),
                     MODALITY.get(clean(r.modality), "in_person"), "" if loc == "Unknown Location" else loc,
                     clean(r.section_status).lower(), clean(r.days_times), "; ".join(names)])
    write("sections.csv",
          ["section_id", "course_code", "term_code", "section_code", "session_code", "modality", "location",
           "status", "days_times", "instructors"], rows)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(Path(sys.argv[1]))
