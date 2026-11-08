/** Shapes of the FastAPI responses (backend/app/services/student_portal.py, terms.py, agents/requirements_agent.py).
 *  Decimals arrive as JSON numbers or strings: always go through num(). */
export type Decimalish = number | string

export const num = (v: Decimalish | null | undefined): number => (v == null ? 0 : Number(v))

export interface CurrentUser {
  user_id: string
  email: string
  role: 'student' | 'advisor' | 'admin'
  student_id: string | null
  advisor_id: string | null
}

export interface LoginResponse {
  access_token: string
  token_type: string
  user: CurrentUser
}

export interface TermStatus {
  term_code: string
  term_label: string // "Spring 2027": always show this, never the code
  season: string
  year: number
  seq: number
  registration_opens: string
  registration_closes: string
  status: 'open' | 'closed' | 'not_open_yet'
  has_offerings: boolean
}

export interface TermPicker {
  today: string
  plannable: TermStatus[]
  upcoming: TermStatus[]
  recent_closed: TermStatus[]
}

export interface InterestView {
  interest_id: number
  label: string
  source: 'resume' | 'undergrad' | 'manual' | 'ai_suggested'
  status: 'suggested' | 'confirmed' | 'removed'
  created_at: string
  confirmed_at: string | null
}

export interface InterestLists {
  suggested: InterestView[]
  confirmed: InterestView[]
}

export interface UploadResult {
  document_id: string
  doc_type: string
  filename: string | null
  characters: number
  method: 'llm' | 'keywords' | 'none'
  suggested: InterestView[]
}

export type CoreState = 'completed' | 'in_progress' | 'waived' | 'remaining'

export interface CoreStatus {
  course_code: string
  title: string
  units: Decimalish
  recommended_term_seq: number | null
  status: CoreState
}

export interface DegreeAudit {
  student_id: string
  track_code: string
  entry_term: string
  qpa: Decimalish | null
  min_qpa: Decimalish
  below_min_qpa: boolean
  total_units: number
  units_completed: Decimalish
  units_in_progress: Decimalish
  units_remaining: Decimalish
  core: CoreStatus[]
  core_units_remaining: Decimalish
  elective_units_required: Decimalish
  elective_units_done: Decimalish
  elective_units_left: Decimalish
  plan_position: number
  next_term: string | null
  projected_graduation_term: string | null
  units_per_term: number
}

export interface Progress {
  units_completed: Decimalish
  units_in_progress: Decimalish
  units_remaining: Decimalish
  total_units: number
  qpa: Decimalish | null
  min_qpa: Decimalish
  below_min_qpa: boolean
  next_term: string | null
  next_term_label: string | null
  projected_graduation_term: string | null
  projected_graduation_label: string | null
  elective_units_left: Decimalish
}

export interface Dashboard {
  student: {
    student_id: string
    first_name: string
    last_name: string
    email: string
    track_code: string
    entry_term: string
    entry_term_label: string
    university_name: string
    academic_terms: number // terms in the program, for "Term 2 of 3"
    program: ProgramInfo
  }
  is_new_student: boolean
  audit: DegreeAudit
  progress: Progress
  advisor: { advisor_id: string; name: string; email: string; title: string } | null
  interests: InterestLists
  unread_messages: number
  terms: TermPicker
  schedules: { term_code: string; term_label: string; schedule_id: string; version: number; status: string }[]
}

export interface PrerequisiteView {
  options: string[][]
  text: string | null
  catalog_applies: boolean
}

export interface CourseDetail {
  course_code: string
  title: string
  prerequisites: PrerequisiteView
}

// ---------- schedules (backend/app/services/schedule_views.py, catalog_search.py) ----------
export interface ScheduleItem {
  course_code: string
  title: string
  units: Decimalish
  item_type: 'core' | 'elective'
  is_locked: boolean
  added_by: 'ai' | 'student'
  section_id: string | null
  days_times: string | null
  modality: string | null
  seats_left: number | null
  is_conditional: boolean
  needs_approval: boolean
  match_score: number | null
  reason: string | null
  in_ai_draft: boolean | null
  ai_original_course: string | null
  issues: string[]
}

export interface ScheduleCheck {
  code: string
  passed: boolean
  detail: string
  advisor_flag: boolean
}

export interface ScheduleView {
  schedule_id: string
  student_id: string
  term_code: string
  term_label: string
  version: number
  status: 'draft' | 'submitted' | 'approved' | 'rejected' | 'discarded' | 'superseded'
  previous_schedule_id: string | null
  edited_by_student: boolean
  advisor_comment: string | null
  ai_explanation: string | null
  load_rationale: string | null
  total_units: Decimalish
  core_units: Decimalish
  elective_units: Decimalish
  max_units: number
  max_units_extended: number | null
  extended_units_min_qpa: Decimalish | null
  extended_allowed: boolean
  items: ScheduleItem[]
  checks: ScheduleCheck[]
  blocking: string[]
  valid: boolean
  flags: FlagView[]
  submitted_at: string | null
  decided_at: string | null
  can_edit: boolean
  can_submit: boolean
  can_reset: boolean
  can_discard: boolean
  can_withdraw: boolean
  can_resubmit: boolean
}

export interface HistoryItem {
  schedule_id: string
  term_code: string
  term_label: string
  version: number
  status: string
  courses: number
  total_units: Decimalish
  edited_by_student: boolean
  created_at: string
  advisor_comment: string | null
}

export interface Suggestion {
  course_code: string
  title: string
  units: Decimalish | null
  rank: number
  group_code: 'interest_match' | 'popular'
  matched_interest: string | null
  final_score: number
  flags: string[]
  reason: string | null
  in_schedule: boolean
}

export interface SuggestionGroups {
  schedule_id: string
  term_code: string
  term_label: string
  basis: 'interests' | 'popularity' | null
  interest_match: Suggestion[]
  popular: Suggestion[]
}

export interface RuleVerdict {
  eligible: boolean
  blocking: string[]
  flags: string[]
  notes: string[]
}

export interface CourseHit {
  course_code: string
  title: string
  units: Decimalish | null
  level: string | null
  offered_sections: number
  seats_left: number | null
  check: RuleVerdict | null
}

export interface CourseSection {
  section_id: string
  days_times: string
  modality: string
  seats_left: number | null
  instructors: string[]
}

export interface CourseFull {
  course_code: string
  title: string
  description: string | null
  syllabus_url: string | null
  sections: CourseSection[]
}

// ---------- advisor (backend/app/services/advisor_portal.py, messaging.py) ----------
export interface FlagView {
  flag_id: number
  flag_code: string
  severity: 'high' | 'medium' | 'low'
  message: string
  rule_text: string | null
  suggested_action: string | null
  status: string
}

export interface CaseloadRow {
  student_id: string
  first_name: string
  last_name: string
  track_code: string
  entry_term: string
  units_completed: Decimalish
  units_in_progress: Decimalish
  qpa: Decimalish | null
  min_qpa: Decimalish
  total_units: number
  university_name: string
  program_title: string
  below_min_qpa: boolean
  open_flags: number
  flag_codes: string[]
  top_severity: 'high' | 'medium' | 'low' | null
  schedule: { schedule_id: string; term_code: string; term_label: string; version: number; status: string; total_units: Decimalish } | null
  unread_messages: number
}

export interface Caseload {
  advisor_name: string | null
  advisor_title: string | null
  university: UniversityInfo | null
  programs: ProgramInfo[]
  metrics: { students: number; with_risk_flags: number; awaiting_review: number; unread_messages: number }
  rows: CaseloadRow[]
}

export interface MessageView {
  message_id: number
  sender_role: string
  sender_name: string
  body: string
  sent_at: string
  read_at: string | null
  schedule_id: string | null
  mine: boolean
}

export interface AdvisorScheduleView {
  student: {
    student_id: string
    first_name: string
    last_name: string
    email: string
    track_code: string
    qpa: Decimalish | null
    min_qpa: Decimalish
    below_min_qpa: boolean
    units_completed: Decimalish
    units_in_progress: Decimalish
    advisor_name: string | null
    university_name: string
    program: ProgramInfo
  }
  schedule: ScheduleView
  ai_explanation: string | null
  load_rationale: string | null
  ai_run: { agent_name: string; llm_provider: string | null; llm_model: string | null; tools: string[] } | null
  removed_from_ai_draft: { course_code: string; title: string }[]
  messages: MessageView[]
  changes: { action: string; course_code: string | null; course_title: string | null; replaced_course_code: string | null; replaced_course_title: string | null }[]
}

export interface Recheck {
  status: string
  checks: ScheduleCheck[]
  blocking: string[]
  can_approve: boolean
  flags: FlagView[]
}

export interface DecisionResult {
  schedule_id: string
  status: string
  email_sent: boolean | null
  email_subject: string | null
  email_error: string | null
  advisor_flags: string[]
}

export interface EmailPreview {
  to: string
  cc: string | null
  from_addr: string
  subject: string
  html: string
  text: string
  note: string
}

// ---------- university and program (backend/app/services/program_info.py): names are never hard-coded ----------
export interface UniversityInfo {
  university_code: string
  name: string // "Carnegie Mellon University"
  short_name: string
}

export interface ProgramInfo {
  program_code: string
  name: string
  degree: string // "Master of Science"
  college: string // "Heinz College"
  title: string // "Master of Science in Artificial Intelligence Systems Management"
  university: UniversityInfo
}

/** GET /auth/me: who is logged in and which university and program their screens are named after. */
export interface Profile {
  university: UniversityInfo | null
  programs: ProgramInfo[]
}
