# Diagrams

Architecture and database diagrams for the Gray DI Degree Pathway Advisor. GitHub draws the diagrams below directly.
Database schema: PostgreSQL, schema `advisor` (see `db/schema.sql`).

## Architecture

How the browser, the API, the five agents, the tools and the database fit together.

```mermaid
flowchart TB
  subgraph FE["Frontend: React + TypeScript (Vite, Tailwind)"]
    SP["Student portal<br/>Dashboard · Interests · Draft"]
    AP["Advisor portal<br/>Caseload · Review · Decide"]
  end
  subgraph BE["Backend: Python FastAPI"]
    API["REST routes + JWT roles"]
    subgraph AG["Agent layer: 5 agentic functions"]
      A1["Agent 1<br/>Requirements"]
      A2["Agent 2<br/>Electives"]
      A3["Agent 3<br/>Schedule"]
      A4["Agent 4<br/>Risk flags"]
      A5["Agent 5<br/>Compliance"]
    end
    subgraph TL["Tools (fixed functions)"]
      SQL["SQL tools"]
      RE["Rule engine"]
      VS["Vector search<br/>MiniLM + pgvector"]
      LLM["LLM client"]
    end
  end
  PG[("PostgreSQL 16<br/>+ pgvector")]
  OL["LLM: Ollama (local)<br/>or Claude API"]
  MP["Mailpit (local email)"]
  SP --> API
  AP --> API
  API --> AG
  AG --> TL
  SQL --> PG
  VS --> PG
  LLM --> OL
  A5 --> MP
```

## Database (1 of 3): programs, tracks and course catalog

Programs, tracks, the core plan, rule settings, terms, courses, offerings and sections.

```mermaid
erDiagram
  COURSES ||--o{ CORE_REQUIREMENTS : ""
  COURSES ||--o{ COURSE_COREQUISITES : ""
  COURSES ||--o{ COURSE_OFFERINGS : ""
  COURSES ||--o{ COURSE_PREREQUISITES : ""
  COURSES ||--o{ COURSE_TERM_STATS : ""
  COURSE_OFFERINGS ||--o{ SECTIONS : ""
  COURSE_TYPES ||--o{ COURSES : ""
  INSTRUCTORS ||--o{ SECTION_INSTRUCTORS : ""
  PROGRAMS ||--o{ TRACKS : ""
  SECTIONS ||--o{ SECTION_INSTRUCTORS : ""
  SECTIONS ||--o{ SECTION_MEETINGS : ""
  SUBJECTS ||--o{ COURSES : ""
  TERMS ||--o{ COURSE_OFFERINGS : ""
  TERMS ||--o{ COURSE_TERM_STATS : ""
  TERMS ||--o{ TERM_SESSIONS : ""
  TERM_SESSIONS ||--o{ SECTIONS : ""
  TRACKS ||--o{ CORE_REQUIREMENTS : ""
  TRACKS ||--o{ ELECTIVE_REQUIREMENTS : ""
  TRACKS ||--o{ TRACK_TERM_PLAN : ""
  TRACK_TERM_PLAN ||--o{ CORE_REQUIREMENTS : ""
  UNIVERSITIES ||--o{ PROGRAMS : ""
  PROGRAMS {
    text program_code PK
    text university_code FK
    text name
    text degree
    text college
    text handbook_notes
  }
  TRACKS {
    text track_code PK
    text program_code FK
    text name
    int duration_months
    int total_units
    int academic_terms
    numeric min_qpa
    int typical_units_per_term
    int max_units_per_term
    int max_units_extended
    numeric extended_units_min_qpa
    bool internship_required
  }
  TRACK_TERM_PLAN {
    text track_code PK,FK
    int term_seq PK
    text season
    bool is_academic
  }
  CORE_REQUIREMENTS {
    text track_code PK,FK
    text course_code PK,FK
    int recommended_term_seq FK
    bool waivable
    text waiver_method
    bool waiver_adds_elective_units
    bool catalog_prereqs_apply
    text prereq_override_note
    text notes
  }
  ELECTIVE_REQUIREMENTS {
    text track_code PK,FK
    int min_units
    int max_units_outside_college
    int max_undergrad_units
    text undergrad_min_grade
    text_array excluded_subject_areas
    text notes
  }
  RULE_SETTINGS {
    text rule_code PK
    jsonb params
    text severity
    text description
    bool active
  }
  TERMS {
    text term_code PK
    text season
    int year
    int seq
    date start_date
    date end_date
    bool is_current
    date registration_opens
    date registration_closes
  }
  TERM_SESSIONS {
    text term_code PK,FK
    text session_code PK
    date start_date
    date end_date
  }
  SUBJECTS {
    text subject_code PK
    text name
    bool is_home_college
  }
  COURSE_TYPES {
    text type_code PK
    text name
    bool needs_approval
  }
  COURSES {
    text course_code PK
    text subject_code FK
    int course_number
    text title
    text description
    text type_code FK
    text units_type
    numeric units_default
    numeric units_min
    numeric units_max
    text units_note
    text level
    text prereq_text
    text typical_offering
    bool in_catalog
    text syllabus_url
    vector embedding
    timestamptz embedded_at
  }
  COURSE_PREREQUISITES {
    text course_code PK,FK
    int option_no PK
    text required_course_code PK,FK
  }
  COURSE_COREQUISITES {
    text course_code PK,FK
    text coreq_course_code PK,FK
  }
  COURSE_OFFERINGS {
    text course_code PK,FK
    text term_code PK,FK
    text status
    int active_sections
    text course_length
    text syllabus_url
    bool is_assumed
  }
  SECTIONS {
    text section_id PK
    text course_code FK
    text term_code FK
    text section_code
    text session_code FK
    text modality
    text location
    text status
    int capacity
    int enrolled
    int waitlisted
    int seats_left
    bool is_time_tba
    text days_times_raw
    text_array synthetic_fields
  }
  SECTION_MEETINGS {
    text section_id PK,FK
    char day_of_week PK
    time start_time PK
    time end_time
  }
  INSTRUCTORS {
    int instructor_id PK
    text full_name
    text email
    text profile_url
    text bio
    int teaching_since
    bool is_synthetic
  }
  SECTION_INSTRUCTORS {
    text section_id PK,FK
    int instructor_id PK,FK
  }
  COURSE_TERM_STATS {
    text course_code PK,FK
    text term_code PK,FK
    int capacity
    int enrolled
    int waitlist_peak
    numeric hours_to_fill
    text source
  }
  UNIVERSITIES {
    text university_code PK
    text name
    text short_name
    text email_domain
  }
```

## Database (2 of 3): people and academic record

Students, advisors, login users, enrollments, waivers, interests and uploaded documents.

```mermaid
erDiagram
  ADVISORS ||--o{ COURSE_WAIVERS : ""
  ADVISORS ||--o{ STUDENTS : ""
  ADVISORS ||--o{ USERS : ""
  COURSES ||--o{ COURSE_WAIVERS : ""
  COURSES ||--o{ ENROLLMENTS : ""
  GRADE_SCALE ||--o{ ENROLLMENTS : ""
  SECTIONS ||--o{ ENROLLMENTS : ""
  STUDENTS ||--o{ COURSE_WAIVERS : ""
  STUDENTS ||--o{ ENROLLMENTS : ""
  STUDENTS ||--o{ STUDENT_DOCUMENTS : ""
  STUDENTS ||--o{ STUDENT_INTERESTS : ""
  STUDENTS ||--o{ USERS : ""
  TERMS ||--o{ ENROLLMENTS : ""
  TERMS ||--o{ STUDENTS : ""
  TRACKS ||--o{ ADVISORS : ""
  TRACKS ||--o{ STUDENTS : ""
  UNIVERSITIES ||--o{ ADVISORS : ""
  ADVISORS {
    text advisor_id PK
    text first_name
    text last_name
    text honorific
    text email
    text title
    text advisor_type
    text scope_track FK
    text university_code FK
  }
  STUDENTS {
    text student_id PK
    text first_name
    text last_name
    text email
    text track_code FK
    text advisor_id FK
    text entry_term FK
    text status
    text undergrad_institution
    text undergrad_major
    text preferred_time_of_day
    text preferred_modality
    bool is_demo
  }
  USERS {
    uuid user_id PK
    text email
    text password_hash
    text role
    text student_id FK
    text advisor_id FK
    bool is_active
    timestamptz last_login_at
  }
  GRADE_SCALE {
    text grade PK
    numeric quality_points
    bool counts_units
    bool is_passing
  }
  ENROLLMENTS {
    bigint enrollment_id PK
    text student_id FK
    text course_code FK
    text term_code FK
    text section_id FK
    numeric units
    text status
    text grade FK
    text source
  }
  COURSE_WAIVERS {
    bigint waiver_id PK
    text student_id FK
    text course_code FK
    text method
    text status
    text decided_by FK
    timestamptz decided_at
    text note
  }
  STUDENT_DOCUMENTS {
    uuid document_id PK
    text student_id FK
    text doc_type
    text filename
    text text_content
    timestamptz uploaded_at
  }
  STUDENT_INTERESTS {
    bigint interest_id PK
    text student_id FK
    text label
    text source
    text status
    vector embedding
    timestamptz created_at
    timestamptz confirmed_at
  }
  COURSES {
    text course_code PK
  }
  SECTIONS {
    text section_id PK
  }
  TERMS {
    text term_code PK
  }
  TRACKS {
    text track_code PK
  }
  UNIVERSITIES {
    text university_code PK
  }
```

## Database (3 of 3): schedules, agents and audit

Schedules and their items, agent runs, audits, flags, compliance checks, messages and notifications.

```mermaid
erDiagram
  ADVISORS ||--o{ SCHEDULES : ""
  AGENT_RUNS ||--o{ COMPLIANCE_CHECKS : ""
  AGENT_RUNS ||--o{ DEGREE_AUDITS : ""
  AGENT_RUNS ||--o{ ELECTIVE_SUGGESTIONS : ""
  AGENT_RUNS ||--o{ RISK_FLAGS : ""
  AGENT_RUNS ||--o{ SCHEDULES : ""
  COURSES ||--o{ ELECTIVE_SUGGESTIONS : ""
  COURSES ||--o{ SCHEDULE_CHANGES : ""
  COURSES ||--o{ SCHEDULE_ITEMS : ""
  SCHEDULES ||--o{ AGENT_RUNS : ""
  SCHEDULES ||--o{ COMPLIANCE_CHECKS : ""
  SCHEDULES ||--o{ MESSAGES : ""
  SCHEDULES ||--o{ NOTIFICATIONS : ""
  SCHEDULES ||--o{ RISK_FLAGS : ""
  SCHEDULES ||--o{ SCHEDULE_CHANGES : ""
  SCHEDULES ||--o{ SCHEDULE_ITEMS : ""
  SECTIONS ||--o{ SCHEDULE_ITEMS : ""
  STUDENTS ||--o{ AGENT_RUNS : ""
  STUDENTS ||--o{ DEGREE_AUDITS : ""
  STUDENTS ||--o{ ELECTIVE_SUGGESTIONS : ""
  STUDENTS ||--o{ MESSAGES : ""
  STUDENTS ||--o{ NOTIFICATIONS : ""
  STUDENTS ||--o{ RISK_FLAGS : ""
  STUDENTS ||--o{ SCHEDULES : ""
  TERMS ||--o{ ELECTIVE_SUGGESTIONS : ""
  TERMS ||--o{ SCHEDULES : ""
  USERS ||--o{ MESSAGES : ""
  SCHEDULES {
    uuid schedule_id PK
    text student_id FK
    text term_code FK
    int version
    text status
    uuid previous_schedule_id FK
    bool edited_by_student
    text ai_explanation
    text load_rationale
    uuid built_by_run_id FK
    timestamptz created_at
    timestamptz submitted_at
    timestamptz decided_at
    text decided_by FK
    text advisor_comment
    timestamptz discarded_at
    jsonb ai_baseline
  }
  SCHEDULE_ITEMS {
    uuid schedule_id PK,FK
    text course_code PK,FK
    text section_id FK
    numeric units
    text item_type
    bool is_locked
    text added_by
    bool is_conditional
    bool needs_approval
    numeric match_score
    text reason
  }
  SCHEDULE_CHANGES {
    bigint change_id PK
    uuid schedule_id FK
    text action
    text course_code FK
    text replaced_course_code FK
    text actor
    timestamptz changed_at
  }
  AGENT_RUNS {
    uuid run_id PK
    text agent_name
    text student_id FK
    uuid schedule_id FK
    text status
    jsonb input
    jsonb tool_calls
    jsonb decision
    text explanation
    text llm_provider
    text llm_model
    int llm_ms
    text error
    timestamptz started_at
    timestamptz finished_at
  }
  DEGREE_AUDITS {
    uuid audit_id PK
    text student_id FK
    uuid run_id FK
    jsonb result
    bool is_current
    timestamptz created_at
  }
  ELECTIVE_SUGGESTIONS {
    bigint suggestion_id PK
    text student_id FK
    text term_code FK
    uuid run_id FK
    text course_code FK
    int rank
    numeric similarity
    numeric popularity
    numeric fit
    numeric final_score
    text basis
    text group_code
    text matched_interest
    text_array flags
    text reason
    timestamptz created_at
  }
  RISK_FLAGS {
    bigint flag_id PK
    text student_id FK
    uuid schedule_id FK
    uuid run_id FK
    text flag_code
    text severity
    text message
    text rule_text
    text suggested_action
    text status
    timestamptz created_at
  }
  COMPLIANCE_CHECKS {
    bigint check_id PK
    uuid schedule_id FK
    uuid run_id FK
    text stage
    text check_code
    bool passed
    text detail
    timestamptz checked_at
  }
  AUDIT_LOG {
    bigint log_id PK
    timestamptz at
    text actor_type
    text actor_id
    text action
    text entity_type
    text entity_id
    jsonb details
  }
  MESSAGES {
    bigint message_id PK
    text student_id FK
    uuid schedule_id FK
    uuid sender_user_id FK
    text body
    timestamptz sent_at
    timestamptz read_at
  }
  NOTIFICATIONS {
    bigint notification_id PK
    text student_id FK
    uuid schedule_id FK
    text template
    text to_email
    text subject
    text body
    text status
    timestamptz sent_at
    text error
  }
  ADVISORS {
    text advisor_id PK
  }
  COURSES {
    text course_code PK
  }
  SECTIONS {
    text section_id PK
  }
  STUDENTS {
    text student_id PK
  }
  TERMS {
    text term_code PK
  }
  USERS {
    uuid user_id PK
  }
```
