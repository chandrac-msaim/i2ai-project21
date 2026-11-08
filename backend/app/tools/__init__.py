"""Tools: the only place agents touch data (CLAUDE.md rule 2). Agents call them by name via Agent.call_tool."""
from collections.abc import Callable
from typing import Any

from app.tools.catalog import (
    get_active_courses,
    get_course_prerequisites,
    get_sections,
    get_term_label,
    get_term_sections,
)
from app.tools.email import send_email
from app.tools.popularity import rank_courses_by_popularity
from app.tools.records import (
    get_current_audit,
    get_elective_suggestions,
    get_open_schedule,
    save_degree_audit,
    save_elective_suggestions,
    save_schedule,
    save_suggested_interests,
)
from app.tools.schedules import (
    apply_decision,
    apply_submission,
    create_notification,
    get_rule_details,
    get_schedule,
    get_decision_email_facts,
    get_student_contact,
    mark_notification,
    save_compliance_checks,
    save_risk_flags,
)
from app.tools.rules import check_course_eligibility, get_rule_settings, get_term_registration, validate_schedule
from app.tools.student import (
    get_confirmed_interests,
    get_course_waivers,
    get_interest_labels,
    get_program_requirements,
    get_student_documents,
    get_student_history,
)
from app.tools.vector_search import course_similarities, embed_texts, search_course_content

# name -> function. Every function except send_email and embed_texts takes the db Session first.
TOOLS: dict[str, Callable[..., Any]] = {
    "get_active_courses": get_active_courses,
    "get_sections": get_sections,
    "get_term_sections": get_term_sections,
    "get_term_label": get_term_label,
    "get_course_prerequisites": get_course_prerequisites,
    "get_student_history": get_student_history,
    "get_course_waivers": get_course_waivers,
    "get_program_requirements": get_program_requirements,
    "get_confirmed_interests": get_confirmed_interests,
    "rank_courses_by_popularity": rank_courses_by_popularity,
    "search_course_content": search_course_content,
    "course_similarities": course_similarities,
    "embed_texts": embed_texts,
    "send_email": send_email,
    # rule engine, terms, settings
    "validate_schedule": validate_schedule,
    "check_course_eligibility": check_course_eligibility,
    "get_term_registration": get_term_registration,
    "get_rule_settings": get_rule_settings,
    # student documents and interests
    "get_student_documents": get_student_documents,
    "get_interest_labels": get_interest_labels,
    # agent outputs (the only write tools)
    "save_degree_audit": save_degree_audit,
    "get_current_audit": get_current_audit,
    "get_open_schedule": get_open_schedule,
    "save_elective_suggestions": save_elective_suggestions,
    "get_elective_suggestions": get_elective_suggestions,
    "save_suggested_interests": save_suggested_interests,
    "save_schedule": save_schedule,
    # Agents 4 and 5: schedules, risk flags, compliance, notifications
    "get_schedule": get_schedule,
    "get_rule_details": get_rule_details,
    "get_student_contact": get_student_contact,
    "get_decision_email_facts": get_decision_email_facts,
    "save_risk_flags": save_risk_flags,
    "save_compliance_checks": save_compliance_checks,
    "apply_submission": apply_submission,
    "apply_decision": apply_decision,
    "create_notification": create_notification,
    "mark_notification": mark_notification,
}
NO_DB_TOOLS = frozenset({"send_email", "embed_texts"})
