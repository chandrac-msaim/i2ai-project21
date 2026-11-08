-- =====================================================================
-- Gray DI Degree Pathway Advisor - PostgreSQL schema (v1)
-- PostgreSQL 16 + pgvector + pg_trgm
-- Run: psql -U graydi -d graydi -f db/schema.sql   (drops and recreates schema "advisor")
-- =====================================================================

DROP SCHEMA IF EXISTS advisor CASCADE;
CREATE SCHEMA advisor;
SET search_path TO advisor, public;

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
-- gen_random_uuid() is built into PostgreSQL 13+

-- App and psql sessions for this user look in schema 'advisor' first
DO $$ BEGIN EXECUTE format('ALTER ROLE %I SET search_path = advisor, public', current_user); END $$;

-- =====================================================================
-- A. PROGRAM STRUCTURE AND RULES
-- =====================================================================

-- The portal serves several universities. A program belongs to one; advisors belong to one (they see only its students).
CREATE TABLE universities (
    university_code  text PRIMARY KEY,                 -- 'CMU'
    name             text NOT NULL,                    -- 'Carnegie Mellon University'
    short_name       text NOT NULL,                    -- 'CMU'
    email_domain     text UNIQUE                       -- optional: finds the university from a login email
);

CREATE TABLE programs (
    program_code     text PRIMARY KEY,                 -- 'AIM'
    university_code  text NOT NULL REFERENCES universities,
    name             text NOT NULL,
    degree           text NOT NULL,
    college          text NOT NULL,
    handbook_notes   text
);

CREATE TABLE tracks (
    track_code             text PRIMARY KEY,           -- 'AIM12', 'AIM16'
    program_code           text NOT NULL REFERENCES programs,
    name                   text NOT NULL,              -- '12-month'
    duration_months        int  NOT NULL,
    total_units            int  NOT NULL CHECK (total_units > 0),
    academic_terms         int  NOT NULL,
    min_qpa                numeric(3,2) NOT NULL,
    typical_units_per_term int  NOT NULL,
    max_units_per_term     int  NOT NULL,                 -- 54: normal maximum to submit; the AI fills the draft to this
    max_units_extended     int  NOT NULL,                 -- 60: maximum for students whose QPA is above the next value
    extended_units_min_qpa numeric(3,2) NOT NULL,         -- 3.50: cumulative QPA must be ABOVE this for 55-60 units
    internship_required    boolean NOT NULL DEFAULT false,
    CHECK (max_units_extended >= max_units_per_term)
);

-- Order of terms for a cohort (season-based, independent of year)
CREATE TABLE track_term_plan (
    track_code     text NOT NULL REFERENCES tracks,
    term_seq       int  NOT NULL CHECK (term_seq > 0),  -- 1, 2, 3, ...
    season         text NOT NULL CHECK (season IN ('Fall','Spring','Summer')),
    is_academic    boolean NOT NULL DEFAULT true,       -- false = internship-only summer
    PRIMARY KEY (track_code, term_seq)
);

-- Core (required) courses per track - separate table
CREATE TABLE core_requirements (
    track_code            text NOT NULL REFERENCES tracks,
    course_code           text NOT NULL,                -- FK added after courses
    recommended_term_seq  int,
    waivable              boolean NOT NULL DEFAULT false,
    waiver_method         text CHECK (waiver_method IN ('exemption_exam')),
    waiver_adds_elective_units boolean NOT NULL DEFAULT false,
    catalog_prereqs_apply boolean NOT NULL DEFAULT true,  -- false = program sequence replaces catalog prerequisites
    prereq_override_note  text,
    notes                 text,
    PRIMARY KEY (track_code, course_code),
    FOREIGN KEY (track_code, recommended_term_seq) REFERENCES track_term_plan (track_code, term_seq),
    CHECK (NOT waivable OR waiver_method IS NOT NULL),
    CHECK (catalog_prereqs_apply OR prereq_override_note IS NOT NULL)
);

-- Elective rules per track - separate table
CREATE TABLE elective_requirements (
    track_code                text PRIMARY KEY REFERENCES tracks,
    min_units                 int NOT NULL,            -- 18
    max_units_outside_college int NOT NULL,            -- 36 (outside Heinz)
    max_undergrad_units       int NOT NULL,            -- 12, petition needed
    undergrad_min_grade       text NOT NULL,           -- 'B'
    excluded_subject_areas    text[] NOT NULL DEFAULT '{}',  -- language, music, PE
    notes                     text
);

-- Tunable thresholds for the rule engine and agents (not program facts)
CREATE TABLE rule_settings (
    rule_code    text PRIMARY KEY,                     -- 'LOW_SEATS', 'ELECTIVE_SCORE_WEIGHTS', ...
    params       jsonb NOT NULL,
    severity     text CHECK (severity IN ('block','high','medium','low','info')),
    description  text NOT NULL,
    active       boolean NOT NULL DEFAULT true
);

-- =====================================================================
-- B. CALENDAR AND CATALOG
-- =====================================================================

CREATE TABLE terms (
    term_code    text PRIMARY KEY,                     -- 'F26', 'S27', 'M27'
    season       text NOT NULL CHECK (season IN ('Fall','Spring','Summer')),
    year         int  NOT NULL,
    seq          int  NOT NULL UNIQUE,                 -- chronological order
    start_date   date,
    end_date     date,
    is_current   boolean NOT NULL DEFAULT false,            -- term in session (for history/popularity)
    registration_opens   date NOT NULL,                     -- students may plan/register from this date
    registration_closes  date NOT NULL,                     -- ... until this date (last day to add)
    UNIQUE (season, year),
    CHECK (registration_closes >= registration_opens)
);
CREATE UNIQUE INDEX one_current_term  ON terms (is_current)  WHERE is_current;

-- Full semester and mini sessions; date overlap drives the time-clash rule
CREATE TABLE term_sessions (
    term_code     text NOT NULL REFERENCES terms,
    session_code  text NOT NULL CHECK (session_code IN ('FULL','M1','M2','M3','M4','M5','M6')),
    start_date    date NOT NULL,
    end_date      date NOT NULL CHECK (end_date >= start_date),
    PRIMARY KEY (term_code, session_code)
);

CREATE TABLE subjects (
    subject_code      text PRIMARY KEY,                -- '95'
    name              text,
    is_home_college   boolean NOT NULL DEFAULT false   -- true = Heinz College
);

CREATE TABLE course_types (
    type_code        text PRIMARY KEY,                 -- 'regular', 'independent_study', ...
    name             text NOT NULL UNIQUE,             -- 'Regular course'
    needs_approval   boolean NOT NULL DEFAULT false
);

CREATE TABLE courses (
    course_code      text PRIMARY KEY CHECK (course_code ~ '^\d{2}-\d{3}$'),
    subject_code     text NOT NULL REFERENCES subjects,
    course_number    int  NOT NULL,
    title            text NOT NULL,
    description      text,
    type_code        text REFERENCES course_types,     -- NULL for outside stub courses
    units_type       text NOT NULL DEFAULT 'fixed' CHECK (units_type IN ('fixed','variable','range')),
    units_default    numeric(4,1),
    units_min        numeric(4,1),
    units_max        numeric(4,1),
    units_note       text,
    level            text GENERATED ALWAYS AS
                        (CASE WHEN course_number < 600 THEN 'undergraduate' ELSE 'graduate' END) STORED,
    prereq_text      text,                             -- original text, for display
    typical_offering text,                             -- 'Fall', 'Both + Summer' (descriptive)
    in_catalog       boolean NOT NULL DEFAULT true,    -- false = outside course referenced as prerequisite
    syllabus_url     text,                              -- empty until Gray DI provides the data
    embedding        vector(384),                      -- MiniLM(title + description)
    embedded_at      timestamptz,
    CHECK (units_min IS NULL OR units_max IS NULL OR units_min <= units_max)
);
CREATE INDEX courses_title_trgm ON courses USING gin (title gin_trgm_ops);
CREATE INDEX courses_embedding_hnsw ON courses USING hnsw (embedding vector_cosine_ops);

ALTER TABLE core_requirements
    ADD FOREIGN KEY (course_code) REFERENCES courses;

-- Prerequisites in disjunctive normal form:
-- a course is satisfied when ALL rows of ANY one option_no are satisfied.
-- '(A or B) and C'  ->  option 1 = {A, C}, option 2 = {B, C}
CREATE TABLE course_prerequisites (
    course_code           text NOT NULL REFERENCES courses ON DELETE CASCADE,
    option_no             int  NOT NULL,
    required_course_code  text NOT NULL REFERENCES courses,
    PRIMARY KEY (course_code, option_no, required_course_code),
    CHECK (course_code <> required_course_code)
);

CREATE TABLE course_corequisites (
    course_code        text NOT NULL REFERENCES courses ON DELETE CASCADE,
    coreq_course_code  text NOT NULL REFERENCES courses,
    PRIMARY KEY (course_code, coreq_course_code)
);

CREATE TABLE course_offerings (
    course_code      text NOT NULL REFERENCES courses,
    term_code        text NOT NULL REFERENCES terms,
    status           text NOT NULL CHECK (status IN ('offered','cancelled')),
    active_sections  int  NOT NULL DEFAULT 0,
    course_length    text,                             -- original: 'Mini 3; Mini 4'
    syllabus_url     text,
    is_assumed       boolean NOT NULL DEFAULT false,   -- true = copied/assumed (e.g. M27 from M26)
    PRIMARY KEY (course_code, term_code)
);
CREATE INDEX course_offerings_term ON course_offerings (term_code, status);

CREATE TABLE instructors (
    instructor_id   serial PRIMARY KEY,
    full_name       text NOT NULL UNIQUE,
    email           text UNIQUE,
    profile_url     text,
    bio             text,
    teaching_since  int,
    is_synthetic    boolean NOT NULL DEFAULT false
);

CREATE TABLE sections (
    section_id     text PRIMARY KEY,                   -- '95-864-S27-A3'
    course_code    text NOT NULL,
    term_code      text NOT NULL,
    section_code   text NOT NULL,                      -- 'A3'
    session_code   text NOT NULL,
    modality       text NOT NULL CHECK (modality IN ('in_person','remote','hybrid')),
    location       text,
    status         text NOT NULL CHECK (status IN ('offered','cancelled')),
    capacity       int  CHECK (capacity >= 0),
    enrolled       int  NOT NULL DEFAULT 0 CHECK (enrolled >= 0),
    waitlisted     int  NOT NULL DEFAULT 0 CHECK (waitlisted >= 0),
    seats_left     int  GENERATED ALWAYS AS (GREATEST(capacity - enrolled, 0)) STORED,
    is_time_tba    boolean NOT NULL DEFAULT false,
    days_times_raw text,
    synthetic_fields text[] NOT NULL DEFAULT '{}',     -- which values were generated, e.g. {capacity,time}
    UNIQUE (course_code, term_code, section_code),
    FOREIGN KEY (course_code, term_code) REFERENCES course_offerings,
    FOREIGN KEY (term_code, session_code) REFERENCES term_sessions
);
CREATE INDEX sections_term_course ON sections (term_code, course_code);

CREATE TABLE section_meetings (
    section_id   text NOT NULL REFERENCES sections ON DELETE CASCADE,
    day_of_week  char(1) NOT NULL CHECK (day_of_week IN ('M','T','W','R','F','S','U')),
    start_time   time NOT NULL,
    end_time     time NOT NULL CHECK (end_time > start_time),
    PRIMARY KEY (section_id, day_of_week, start_time)
);

CREATE TABLE section_instructors (
    section_id     text NOT NULL REFERENCES sections ON DELETE CASCADE,
    instructor_id  int  NOT NULL REFERENCES instructors,
    PRIMARY KEY (section_id, instructor_id)
);

-- Past demand per course and term (popularity input)
CREATE TABLE course_term_stats (
    course_code    text NOT NULL REFERENCES courses,
    term_code      text NOT NULL REFERENCES terms,
    capacity       int  NOT NULL CHECK (capacity > 0),
    enrolled       int  NOT NULL CHECK (enrolled >= 0),
    waitlist_peak  int  NOT NULL DEFAULT 0,
    hours_to_fill  numeric(6,1),
    source         text NOT NULL DEFAULT 'synthetic' CHECK (source IN ('synthetic','registrar')),
    PRIMARY KEY (course_code, term_code)
);

-- =====================================================================
-- C. PEOPLE AND LOGIN
-- =====================================================================

CREATE TABLE advisors (
    advisor_id      text PRIMARY KEY,                  -- 'ADV001'
    first_name      text NOT NULL,
    last_name       text NOT NULL,
    honorific       text,                              -- 'Dr.'
    email           text NOT NULL UNIQUE,
    title           text NOT NULL,
    advisor_type    text NOT NULL CHECK (advisor_type IN ('program','general')),
    scope_track     text REFERENCES tracks,
    university_code text NOT NULL REFERENCES universities   -- an advisor sees only students of this university
);

CREATE TABLE students (
    student_id             text PRIMARY KEY,           -- 'S0001'
    first_name             text NOT NULL,
    last_name              text NOT NULL,
    email                  text NOT NULL UNIQUE,
    track_code             text NOT NULL REFERENCES tracks,
    advisor_id             text REFERENCES advisors,
    entry_term             text NOT NULL REFERENCES terms,
    status                 text NOT NULL DEFAULT 'active' CHECK (status IN ('active','graduated','leave','withdrawn')),
    undergrad_institution  text,
    undergrad_major        text,
    preferred_time_of_day  text CHECK (preferred_time_of_day IN ('morning','afternoon','evening')),
    preferred_modality     text CHECK (preferred_modality IN ('in_person','remote','hybrid')),
    is_demo                boolean NOT NULL DEFAULT false
);
CREATE INDEX students_advisor ON students (advisor_id);

CREATE TABLE users (
    user_id        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    email          text NOT NULL UNIQUE,
    password_hash  text NOT NULL,
    role           text NOT NULL CHECK (role IN ('student','advisor','admin')),
    student_id     text UNIQUE REFERENCES students,
    advisor_id     text UNIQUE REFERENCES advisors,
    is_active      boolean NOT NULL DEFAULT true,
    last_login_at  timestamptz,
    CHECK ( (role = 'student' AND student_id IS NOT NULL AND advisor_id IS NULL)
         OR (role = 'advisor' AND advisor_id IS NOT NULL AND student_id IS NULL)
         OR (role = 'admin'   AND student_id IS NULL     AND advisor_id IS NULL) )
);

-- =====================================================================
-- D. STUDENT ACADEMIC RECORD
-- =====================================================================

CREATE TABLE grade_scale (
    grade           text PRIMARY KEY,                  -- 'A', 'B+', 'P', 'W'
    quality_points  numeric(3,2),                      -- NULL = not in QPA
    counts_units    boolean NOT NULL,                  -- units earned toward degree
    is_passing      boolean NOT NULL
);

CREATE TABLE enrollments (
    enrollment_id  bigserial PRIMARY KEY,
    student_id     text NOT NULL REFERENCES students,
    course_code    text NOT NULL REFERENCES courses,
    term_code      text NOT NULL REFERENCES terms,
    section_id     text REFERENCES sections,
    units          numeric(4,1) NOT NULL CHECK (units >= 0),
    status         text NOT NULL CHECK (status IN ('completed','in_progress','dropped','withdrawn')),
    grade          text REFERENCES grade_scale,
    source         text NOT NULL DEFAULT 'synthetic',
    UNIQUE (student_id, course_code, term_code),
    CHECK (status <> 'completed' OR grade IS NOT NULL),
    CHECK (status <> 'in_progress' OR grade IS NULL)
);
CREATE INDEX enrollments_student ON enrollments (student_id);
CREATE INDEX enrollments_course_term ON enrollments (course_code, term_code);

-- Core courses waived by exemption exam (handbook: the waived units move to electives).
-- Transfer credit is out of scope.
CREATE TABLE course_waivers (
    waiver_id      bigserial PRIMARY KEY,
    student_id     text NOT NULL REFERENCES students,
    course_code    text NOT NULL REFERENCES courses,
    method         text NOT NULL DEFAULT 'exemption_exam' CHECK (method IN ('exemption_exam')),
    status         text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','approved','rejected')),
    decided_by     text REFERENCES advisors,
    decided_at     timestamptz,
    note           text,
    UNIQUE (student_id, course_code),
    CHECK ((status = 'pending') = (decided_at IS NULL))
);

CREATE TABLE student_documents (
    document_id   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    student_id    text NOT NULL REFERENCES students,
    doc_type      text NOT NULL CHECK (doc_type IN ('resume','undergrad_transcript')),
    filename      text,
    text_content  text NOT NULL,
    uploaded_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE student_interests (
    interest_id   bigserial PRIMARY KEY,
    student_id    text NOT NULL REFERENCES students,
    label         text NOT NULL,
    source        text NOT NULL CHECK (source IN ('resume','undergrad','manual','ai_suggested')),
    status        text NOT NULL DEFAULT 'suggested' CHECK (status IN ('suggested','confirmed','removed')),
    embedding     vector(384),
    created_at    timestamptz NOT NULL DEFAULT now(),
    confirmed_at  timestamptz,
    CHECK (status <> 'confirmed' OR confirmed_at IS NOT NULL)
);
CREATE UNIQUE INDEX student_interests_unique ON student_interests (student_id, lower(label));

-- =====================================================================
-- E. SCHEDULES (planned term)
-- =====================================================================

CREATE TABLE schedules (
    schedule_id        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    student_id         text NOT NULL REFERENCES students,
    term_code          text NOT NULL REFERENCES terms,
    version            int  NOT NULL DEFAULT 1,
    status             text NOT NULL DEFAULT 'draft'
                         CHECK (status IN ('draft','submitted','approved','rejected','superseded','discarded')),
    previous_schedule_id uuid REFERENCES schedules,     -- version this one replaced or was resubmitted from
    edited_by_student  boolean NOT NULL DEFAULT false,
    ai_explanation     text,
    load_rationale     text,                            -- why this many units (shown as 'Units this term')
    built_by_run_id    uuid,                            -- FK added after agent_runs
    created_at         timestamptz NOT NULL DEFAULT now(),
    submitted_at       timestamptz,
    decided_at         timestamptz,
    decided_by         text REFERENCES advisors,
    advisor_comment    text,
    discarded_at       timestamptz,                     -- set when the student discards a draft (row is kept)
    ai_baseline        jsonb,                           -- the draft exactly as the AI built it (reset target):
                                                        -- [{course_code, section_id, item_type, units, is_locked,
                                                        --   is_conditional, needs_approval, match_score, reason}]
    UNIQUE (student_id, term_code, version),
    CHECK ((status IN ('approved','rejected')) = (decided_by IS NOT NULL AND decided_at IS NOT NULL)),
    CHECK ((status = 'discarded') = (discarded_at IS NOT NULL))
);
CREATE UNIQUE INDEX one_open_schedule ON schedules (student_id, term_code)
    WHERE status IN ('draft','submitted');

CREATE TABLE schedule_items (
    schedule_id     uuid NOT NULL REFERENCES schedules ON DELETE CASCADE,
    course_code     text NOT NULL REFERENCES courses,
    section_id      text REFERENCES sections,
    units           numeric(4,1) NOT NULL,
    item_type       text NOT NULL CHECK (item_type IN ('core','elective')),
    is_locked       boolean NOT NULL DEFAULT false,
    added_by        text NOT NULL CHECK (added_by IN ('ai','student')),
    is_conditional  boolean NOT NULL DEFAULT false,      -- prerequisite still in progress
    needs_approval  boolean NOT NULL DEFAULT false,
    match_score     numeric(4,3),
    reason          text,
    PRIMARY KEY (schedule_id, course_code)
);

CREATE TABLE schedule_changes (
    change_id            bigserial PRIMARY KEY,
    schedule_id          uuid NOT NULL REFERENCES schedules ON DELETE CASCADE,
    action               text NOT NULL CHECK (action IN
                           ('build','rebuild','add','remove','swap','reset','submit','withdraw','discard','resubmit')),
    course_code          text REFERENCES courses,
    replaced_course_code text REFERENCES courses,
    actor                text NOT NULL CHECK (actor IN ('student','ai','advisor')),
    changed_at           timestamptz NOT NULL DEFAULT now()
);

-- =====================================================================
-- F. AGENT OUTPUTS, AUDIT, MESSAGES
-- =====================================================================

CREATE TABLE agent_runs (
    run_id        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_name    text NOT NULL CHECK (agent_name IN ('requirements','electives','schedule','risk','compliance')),
    student_id    text REFERENCES students,
    schedule_id   uuid REFERENCES schedules,
    status        text NOT NULL CHECK (status IN ('running','ok','retried','failed')),
    input         jsonb,                               -- personal names removed
    tool_calls    jsonb NOT NULL DEFAULT '[]',
    decision      jsonb,
    explanation   text,
    llm_provider  text,
    llm_model     text,
    llm_ms        int,
    error         text,
    started_at    timestamptz NOT NULL DEFAULT now(),
    finished_at   timestamptz
);
CREATE INDEX agent_runs_student ON agent_runs (student_id, started_at DESC);

ALTER TABLE schedules ADD FOREIGN KEY (built_by_run_id) REFERENCES agent_runs;

CREATE TABLE degree_audits (                          -- Agent 1
    audit_id      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    student_id    text NOT NULL REFERENCES students,
    run_id        uuid REFERENCES agent_runs,
    result        jsonb NOT NULL,
    is_current    boolean NOT NULL DEFAULT true,
    created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX one_current_audit ON degree_audits (student_id) WHERE is_current;

CREATE TABLE elective_suggestions (                   -- Agent 2
    suggestion_id  bigserial PRIMARY KEY,
    student_id     text NOT NULL REFERENCES students,
    term_code      text NOT NULL REFERENCES terms,
    run_id         uuid REFERENCES agent_runs,
    course_code    text NOT NULL REFERENCES courses,
    rank           int  NOT NULL,
    similarity     numeric(4,3),
    popularity     numeric(4,3),
    fit            numeric(4,3),                         -- time/format preference + no clash (0..1)
    final_score    numeric(4,3) NOT NULL,
    basis          text NOT NULL CHECK (basis IN ('interests','popularity')),
    group_code     text NOT NULL DEFAULT 'popular'
                     CHECK (group_code IN ('interest_match','popular')),   -- UI shows two groups
    matched_interest text,                               -- confirmed interest label this course covers
    flags          text[] NOT NULL DEFAULT '{}',          -- e.g. {time_clash, few_seats}
    reason         text,
    created_at     timestamptz NOT NULL DEFAULT now(),
    CHECK (group_code <> 'interest_match' OR (basis = 'interests' AND matched_interest IS NOT NULL))
);

CREATE TABLE risk_flags (                             -- Agent 4
    flag_id           bigserial PRIMARY KEY,
    student_id        text NOT NULL REFERENCES students,
    schedule_id       uuid REFERENCES schedules,
    run_id            uuid REFERENCES agent_runs,
    flag_code         text NOT NULL CHECK (flag_code IN
                        ('LOW_QPA','BEHIND_PACE','CONDITIONAL_PREREQ','LOW_SEATS','NEEDS_APPROVAL','UNDERGRAD_CAP','UNIT_OVERLOAD')),
    severity          text NOT NULL CHECK (severity IN ('high','medium','low')),
    message           text NOT NULL,
    rule_text         text,
    suggested_action  text,
    status            text NOT NULL DEFAULT 'open' CHECK (status IN ('open','resolved')),
    created_at        timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE compliance_checks (                      -- Agent 5
    check_id     bigserial PRIMARY KEY,
    schedule_id  uuid NOT NULL REFERENCES schedules ON DELETE CASCADE,
    run_id       uuid REFERENCES agent_runs,
    stage        text NOT NULL CHECK (stage IN ('submit','approve')),
    check_code   text NOT NULL CHECK (check_code IN
                   ('UNIT_LIMIT','PREREQ','OFFERED','LEVEL','APPROVAL','TIME_CONFLICT','PRIVACY')),
    passed       boolean NOT NULL,
    detail       text,
    checked_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE audit_log (                              -- append-only
    log_id       bigserial PRIMARY KEY,
    at           timestamptz NOT NULL DEFAULT now(),
    actor_type   text NOT NULL CHECK (actor_type IN ('user','agent','system')),
    actor_id     text,
    action       text NOT NULL,
    entity_type  text NOT NULL,
    entity_id    text,
    details      jsonb
);
CREATE FUNCTION audit_log_no_change() RETURNS trigger LANGUAGE plpgsql AS
$$ BEGIN RAISE EXCEPTION 'audit_log is append-only'; END $$;
CREATE TRIGGER audit_log_append_only BEFORE UPDATE OR DELETE ON audit_log
    FOR EACH ROW EXECUTE FUNCTION audit_log_no_change();

CREATE TABLE messages (
    message_id      bigserial PRIMARY KEY,
    student_id      text NOT NULL REFERENCES students,    -- one thread per student
    schedule_id     uuid REFERENCES schedules,
    sender_user_id  uuid NOT NULL REFERENCES users,
    body            text NOT NULL,
    sent_at         timestamptz NOT NULL DEFAULT now(),
    read_at         timestamptz
);
CREATE INDEX messages_thread ON messages (student_id, sent_at);

CREATE TABLE notifications (
    notification_id  bigserial PRIMARY KEY,
    student_id       text NOT NULL REFERENCES students,
    schedule_id      uuid REFERENCES schedules,
    template         text NOT NULL CHECK (template IN ('schedule_approved','schedule_rejected')),
    to_email         text NOT NULL,
    subject          text NOT NULL,
    body             text NOT NULL,
    status           text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','sent','failed')),
    sent_at          timestamptz,
    error            text
);

-- =====================================================================
-- G. VIEWS
-- =====================================================================

-- Registration status of every term on a given day ("today" comes from the app: APP_TODAY or the real date).
-- A term can be planned when status = 'open' AND has_offerings.
CREATE FUNCTION term_registration(p_today date DEFAULT current_date)
RETURNS TABLE (term_code text, season text, year int, seq int,
               registration_opens date, registration_closes date,
               status text, has_offerings boolean)
LANGUAGE sql STABLE AS $$
    SELECT t.term_code, t.season, t.year, t.seq, t.registration_opens, t.registration_closes,
           CASE WHEN p_today < t.registration_opens  THEN 'not_open_yet'
                WHEN p_today > t.registration_closes THEN 'closed'
                ELSE 'open' END,
           EXISTS (SELECT 1 FROM course_offerings o WHERE o.term_code = t.term_code AND o.status = 'offered')
    FROM terms t
    ORDER BY t.seq
$$;

-- Units and QPA per student (computed, never stored twice)
CREATE VIEW v_student_progress AS
SELECT s.student_id,
       s.track_code,
       COALESCE(SUM(e.units) FILTER (WHERE e.status = 'completed' AND g.counts_units), 0)        AS units_completed,
       COALESCE(SUM(e.units) FILTER (WHERE e.status = 'in_progress'), 0)                         AS units_in_progress,
       ROUND(SUM(e.units * g.quality_points) FILTER (WHERE e.status = 'completed' AND g.quality_points IS NOT NULL)
             / NULLIF(SUM(e.units) FILTER (WHERE e.status = 'completed' AND g.quality_points IS NOT NULL
                                                 AND e.units > 0), 0), 2)                        AS qpa
FROM students s
LEFT JOIN enrollments e ON e.student_id = s.student_id
LEFT JOIN grade_scale g ON g.grade = e.grade
GROUP BY s.student_id, s.track_code;

-- Popularity per track and course: share of past students in the same track
-- (recency-weighted) + historical demand (fill ratio and waitlist, recency-weighted)
CREATE MATERIALIZED VIEW course_popularity AS
WITH cur AS (SELECT seq AS cur_seq, year AS cur_year FROM terms WHERE is_current),
share AS (
    SELECT s.track_code, e.course_code,
           SUM(EXP(-0.25 * (cur.cur_seq - t.seq))) AS w
    FROM enrollments e
    JOIN students s ON s.student_id = e.student_id
    JOIN terms t    ON t.term_code  = e.term_code
    CROSS JOIN cur
    WHERE e.status = 'completed'
    GROUP BY s.track_code, e.course_code
),
demand AS (
    SELECT st.course_code,
           SUM(w.wt * (LEAST(1, st.enrolled::numeric / st.capacity)
                       + 0.5 * LEAST(1, st.waitlist_peak::numeric / st.capacity)))
           / NULLIF(SUM(w.wt), 0) / 1.5 AS d
    FROM course_term_stats st
    JOIN terms t USING (term_code)
    CROSS JOIN cur
    CROSS JOIN LATERAL (SELECT EXP(-0.5 * (cur.cur_year - t.year)) AS wt) w
    GROUP BY st.course_code
),
base AS (
    SELECT tr.track_code, c.course_code,
           COALESCE(sh.w / NULLIF(MAX(sh.w) OVER (PARTITION BY tr.track_code), 0), 0) AS track_share,
           COALESCE(dm.d, 0)                                                         AS demand
    FROM tracks tr
    CROSS JOIN courses c
    LEFT JOIN share  sh ON sh.track_code = tr.track_code AND sh.course_code = c.course_code
    LEFT JOIN demand dm ON dm.course_code = c.course_code
    WHERE c.in_catalog
)
SELECT track_code, course_code,
       ROUND(track_share::numeric, 3) AS track_share,
       ROUND(demand::numeric, 3)      AS demand,
       ROUND((0.6 * track_share + 0.4 * demand)::numeric, 3) AS popularity
FROM base;
CREATE UNIQUE INDEX course_popularity_pk ON course_popularity (track_code, course_code);
