# Gray DI Degree Pathway Advisor — Design Notes

> Living design document. Come back to this file before each work session.
> Last updated: 9 Nov 2026 (final handover: screens, API list, demo modes, assumptions) · Status: **Step 2 done · M1–M9 done (M9b in review) (331 tests pass) → M7 next**
>
> Working rule: after every step, this file is updated (diagrams included as Mermaid) and downloaded to the project's `docs\DESIGN.md`.

---

## 0. How to use this document

| Step | Topic | Status |
|---|---|---|
| 1 | Architecture and the 5 agentic functions | Done (this doc) |
| — | Local setup (Docker Postgres 16 + pgvector 0.8.7 + pg_trgm, Mailpit) | Done, verified 7 Nov |
| 2 | Database design: normalized schema (39 tables), loader, synthetic data, embeddings | Done (section 10), tested on PostgreSQL 16 + pgvector |
| 3 | Code with Claude Code, milestones M1–M9, one prompt per milestone; project rules fixed up front | M1–M9 done; 365 backend tests (user run) + 24 frontend tests pass (section 11) |
| — | AI advisor chat screen | Dropped for MVP, add if time allows |

Section 9 has the Windows setup (PostgreSQL + pgvector in Docker, Mailpit, Ollama, Python, Node).

---

## 1. What we are building

A web portal for **students** and **advisors** in CMU's MS in AI Systems Management (AIM12 and AIM16 tracks).

- The student logs in, confirms interests, and gets **one AI-built schedule** for next semester (Spring 2027).
- Core courses are **locked**. Electives are suggested from saved interests, or from popularity if no interests are saved.
- The student can add, swap or remove electives, then sends the schedule to the advisor.
- The advisor sees the checks, risk flags and how the AI built the schedule, messages the student, then **approves or rejects**. The student gets an email.

### Sources

- **Meeting notes, 8 Oct (Gray DI sync-up)**
- **Mockup screens v2 (post meeting)**, the latest version
- **Courses_data.xlsx**, sample CMU data

### Requirements from the 8 Oct meeting

**Student**

1. Log in and request a schedule for next semester.
2. Core courses are picked automatically. Electives come from the student's *saved* interests.
3. With no saved interests, use the courses that have historically been popular.
4. Only use courses offered next semester that still have seats.
5. Students can search for electives and add them manually.
6. Validate before submit: at most **54 units per semester**; 55–60 only with a QPA above 3.50 (D17).
7. Suggest interests from the resume or undergraduate degree; the student confirms them.
8. Show the course description, syllabus and professor.
9. Elective rules from reference tables:
   - the prerequisite has been taken or is in progress
   - graduate students can't take undergraduate courses
   - some courses need special approval
10. *(Later)* favourite professor as an interest.

**Advisor**

1. Review the request, with the checks and compliance risks highlighted.
2. Explain how the AI built the schedule.
3. Re-check seats before approving.
4. Approving or rejecting sends an email.
5. Message the student before deciding.

### Screens (from mockups v2)

| # | Screen | Role |
|---|---|---|
| 1 | Dashboard (degree progress, advisor, core/electives, interests banner) | Student |
| 2 | Confirm interests (from resume / undergrad degree, or skip → popular) | Student |
| 3 | Draft schedule (locked core, electives, seats, 54-unit bar, course details, search with rule checks) | Student |
| 4 | Suggested electives with no saved interests (popularity) | Student |
| 5 | Caseload (risk flags, status, unread messages) | Advisor |
| 6 | Review (checks, risk flags, AI reasoning, student changes, seat re-check, messages, decision) | Advisor |
| 7 | Approval / rejection email | Advisor → Student |
| — | AI advisor chat | **Dropped for MVP** |

---

## 2. Main design principle

> **AI suggests and explains. Rules decide. Only an advisor approves.**

- **The rule engine is plain Python and SQL, with no AI.** It checks prerequisites, the 54-unit limit, seats, course level, special approval, time clashes and term offering. An AI mistake can never put an ineligible course in a schedule.
- **The LLM** ranks, decides soft trade-offs, reads resumes, and writes explanations and emails.
- **Every agent decision is logged** (`agent_runs`, `audit_log`). This feeds the advisor's "How the AI built this" panel and covers Gray DI's compliance ask.
- **No custom ML model is trained.** Popularity is SQL aggregation; interest matching uses a ready-made embedding model with pgvector; reasoning uses a ready-made LLM with tool calling. This matches the Gemini review (section 6).

---

## 3. Architecture

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

### Layer rules

- **Frontend:** only calls REST routes. It never talks to agents or the database directly.
- **Routes:** thin. They check the role (student or advisor) and call one agent.
- **Agents:** have a goal, choose tools, decide, check and retry. They never write SQL themselves.
- **Tools:** fixed functions. The rule engine lives here, so no agent can skip a hard check.

### Tech stack (runs on a Windows PC)

| Layer | Choice |
|---|---|
| Database | PostgreSQL 16 + pgvector (Docker image `pgvector/pgvector:pg16`) |
| Backend | Python 3.12, FastAPI, SQLAlchemy 2, Alembic, Pydantic |
| Embeddings | `sentence-transformers/all-MiniLM-L6-v2` (384 dimensions), runs locally |
| LLM | Ollama `qwen2.5:3b` for development (no API key); Claude API optional for the demo |
| Frontend | React + TypeScript, Vite, Tailwind CSS |
| Email | Mailpit (local SMTP + web inbox) |
| Agent framework | Plain Python loop (LangGraph optional later) |

---

## 4. The 5 agentic functions

### What "agentic" means here

Every agent follows the same loop: **goal → gather facts (tools) → reason / decide → act → check with the rule engine → retry or hand off → explain**.

Each agent is a Python class with:

- a `run()` method
- a fixed list of allowed tools
- a typed (Pydantic) output
- one row written to `agent_runs`: input, tools called, decision and reason

### Flows

```
BUILD:  Student clicks "Build schedule"
        → Agent 1 Requirements → Agent 2 Electives → Agent 3 Schedule (loops with rule engine) → draft saved
        Each student edit → rule engine re-validates (Agent 3 validate)

SUBMIT: Student clicks "Send to advisor"
        → Agent 4 Risk flags → Agent 5 Compliance (7 checks, audit) → advisor caseload

DECIDE: Advisor approves / rejects
        → Agent 5 Compliance (live seat re-check, audit log, email)
```

### Agent details

| Agent | Triggered by (screen → route) | Tools | LLM part | Saves to |
|---|---|---|---|---|
| **1 Requirements** (degree audit) | Dashboard → `GET /students/{id}/audit` | `get_program_requirements`, `get_student_history`, `get_course_waivers` | Turns free-text course notes (exemption exams, first-year rules) into rules; a human checks them once | `degree_audits` |
| **2 Electives** (interest match) | Confirm interests / Build → `POST /students/{id}/electives` | `get_active_courses`, `search_course_content` (pgvector), `rank_courses_by_popularity`, rule-engine filter | Suggests interests from resume / undergrad degree for the student to confirm; writes "why suggested" | `elective_suggestions` |
| **3 Schedule** (builder) | Build → `POST /schedules/build`; edits → `POST /schedules/{id}/validate` | Agent 1 + 2 outputs, `get_sections`, rule engine (build → validate → swap → retry, max N) | Fills the term to 54 units (D17; no lighter load for a low QPA); writes "How the AI built this" | `schedules`, `schedule_items` |
| **4 Risk flags** | Submit → `POST /schedules/{id}/submit`; caseload refresh | `get_student_history`, `get_seats`, rule engine | Writes each flag's explanation and a suggested advisor action | `risk_flags` |
| **5 Compliance** (the gate) | Submit (after Agent 4), and Approve/Reject → `POST /schedules/{id}/decision` | Rule engine (7 checks), `get_seats` (live), name removal, audit log, email | Writes the approval or rejection email from the advisor's comment | `compliance_checks`, `audit_log`, `notifications` |

### Agent 2: scoring

- **Similarity:** the student's interest vectors compared with course vectors, using pgvector cosine distance `<=>`. Course vectors come from title + description.
- **Popularity:** recency-weighted enrollment count, normalised to 0–1 against the top course in the same program.
- **Similarity:** computed per confirmed interest; the closest interest is the course's `matched_interest`.
- **Fit (D16):** 0–1 from the student's preferred time of day (morning < 12:00, evening ≥ 17:00, else afternoon), preferred modality, and no clash with the locked core sections. A course's fit is its best usable section.
- **Final score** = `w_similarity × similarity + w_popularity × popularity + w_fit × fit`, all read from `rule_settings.ELECTIVE_SCORE_WEIGHTS` (0.7 / 0.2 / 0.1; never hard-coded). No confirmed interests: popularity + fit only, divided by the weights' sum, `basis = 'popularity'`, every course in group `popular`.
- **Two groups:** `interest_match` when the similarity reaches `ELECTIVE_MATCH_THRESHOLD` (0.40), else `popular`. `matched_interest` is always the closest interest.
- **Diversity** (`ELECTIVE_DIVERSITY`): the best course for each confirmed interest first (so every interest is covered), then fill by score up to `top_n` (8), skipping any course whose embedding similarity to an already picked course is above 0.85.
- **Flags:** `time_clash` (every usable section clashes with the locked core: kept in the list, never removed; the schedule builder skips it) and `few_seats` (the most seats in any section is below `few_seats_below`).
- Fit weights and thresholds live in `ELECTIVE_FIT`; `elective_suggestions` stores `fit`, `group_code`, `matched_interest`, `flags`.
- Eligibility filters (offered in S27, seats left, graduate level, not already taken, prerequisites) go in the SQL `WHERE` clause. The ranking never sees ineligible courses.

```sql
SELECT c.course_code, c.title, 1 - (c.embedding <=> :interest_vec) AS similarity
FROM courses c
WHERE c.level = 'graduate'
  AND c.course_code <> ALL(:completed_or_core)
  AND EXISTS (SELECT 1 FROM sections s WHERE s.course_code = c.course_code
              AND s.term_code = :term_code AND s.status = 'offered' AND s.seats_left > 0)
ORDER BY c.embedding <=> :interest_vec
LIMIT 10;
```

### Rule engine: the 7 checks (from the advisor review mockup)

1. **Unit limit (D17):** up to 54 units per semester; 55–60 only if the cumulative QPA (`v_student_progress.qpa`) is **strictly above 3.50** (no QPA yet = 54); otherwise the check fails with "Above 54 units needs a QPA above 3.50". Over 60 always fails. A passed 55–60 result carries the flag `unit_overload` for the advisor (risk flag `UNIT_OVERLOAD`).
2. **Prerequisites:** completed, or in progress this term. An in-progress prerequisite makes the course a *Conditional course*.
3. **Offered:** in the target term (S27), section not cancelled, seats left.
4. **Course level:** undergraduate courses (number < 600) need a petition (advisor approval), and at most 12 undergraduate units count toward the degree (AIM handbook).
5. **Special approval:** the student may add the course; the advisor decides.
6. **Time conflicts:** no overlapping sections.
7. **Data and privacy:** name removed before any LLM call, and every view is logged.

### Planning term and new students (D12)

- **Which term can be planned:** a term can be planned when `term_registration(today)` returns `status = 'open'` and the term has offerings.
- **Example dates** (approximate registration windows: Fall 1 Apr – 4 Sep, Spring 1 Nov – 22 Jan, Summer 1 Mar – 28 May):

  | Demo date | Fall 2026 | Spring 2027 | Summer 2027 |
  |---|---|---|---|
  | **7 Nov 2026** | closed | **open** | not open yet |
  | **15 Jul 2026** | **open** | not open yet | — |

- **Term picker:** the dashboard shows the open terms with a "Build my schedule" button; other terms show "Closed" or "Not open yet".
- **New student** (demo: Alex Rivera, entry Spring 2027, no history): every core course is "Remaining". Agent 3 locks the remaining core courses **offered in the chosen term**, earliest recommended term first, with prerequisites respected. So a spring start works even though the AIM plan is written fall-first.
- **No data yet:** terms without offerings (for example Fall 2027) show "No course data yet".

### Interests (D13)

| Source | Saved as | Used for matching? |
|---|---|---|
| Resume / undergrad transcript (uploaded or seeded) → InterestExtractor (1 LLM call, keyword fallback) | `suggested`, source `resume` / `undergrad` | No, until confirmed |
| Student keeps a suggestion | `confirmed` + `confirmed_at` + embedding | Yes |
| Student removes one | `removed` (row kept for history) | No |
| Student types one (`POST /students/me/interests`) | `manual`, `confirmed`, embedding computed by the backend, `audit_log` entry | Yes |

### Schedule lifecycle (D15) and units per term (D17)

- **One open schedule** per student and term (draft or submitted). Code: `app/services/schedule_lifecycle.py` (no FastAPI imports; the API and the agents' `save_schedule` tool both call it). Every step writes `schedule_changes` and `audit_log`.

  | Step | Allowed from | Result |
  |---|---|---|
  | `build` | no open schedule | new draft, `version + 1`, change `build` |
  | `build(replace=True)` | an open **draft**, after the student confirms | old draft `superseded`, new draft with `previous_schedule_id`, change `rebuild`. Without `replace`: `DraftExists` |
  | `discard` | draft | `discarded` + `discarded_at` (row kept) |
  | `withdraw` | submitted, no decision yet | back to `draft`, `submitted_at` cleared |
  | `resubmit_from_rejected` | rejected | new draft version copying the items, `previous_schedule_id` = the rejected one, change `resubmit` |

- **Approved is locked:** no step changes it, and a new build for that term is refused (`ScheduleLocked`). A submitted schedule is never replaced (withdraw it first).
- **Draft size:** locked core courses **due this term** (`recommended_term_seq` ≤ the student's place in `track_term_plan`, offered in the term, prerequisites met, earliest first; core from later terms is never pulled forward), then electives by rank until the total reaches **54** (`SCHEDULE_FILL`), never above. The draft stops earlier when no more core is due and the student needs fewer elective units, or nothing else fits without a clash; `load_rationale` says so, for example "54 of 54 units: 42 core + 12 electives" or "42 of 54 units: 24 core + 18 electives. … No more required this term". A low QPA does **not** reduce the load; it stays a risk flag (`LOW_QPA`).

### Submit and decide (M5)

- **Code:** `app/services/schedule_review.py` (`submit_for_review`, `decide`), `agents/risk_agent.py`, `agents/compliance_agent.py`, tools in `tools/schedules.py`. The routes (M6) only call the service.
- **Submit** (the schedule's own student, a draft): Agent 4 saves `risk_flags`, then Agent 5 stage `submit` runs the 7 checks and records `compliance_checks`. Any blocking failure raises `ComplianceBlocked`; the draft stays a draft. A non-blocking failure or flag (petition, special approval, conditional prerequisite, unit overload) goes through and is shown to the advisor.
- **Decide** (**advisor role only**, a submitted schedule): Agent 5 stage `approve` re-runs the 7 checks live. Approving needs them to pass, so a section that filled up since the student submitted raises `ComplianceBlocked`; the schedule stays `submitted`, the checks and reason are recorded, and no email is sent. Rejecting never needs seats. When allowed, the decision is applied (`decided_by`, `decided_at`, `advisor_comment`, audit), a `notifications` row is created and the email is sent (Mailpit in development). If the email fails, the notification is marked `failed` and the decision stands.
- **Who may decide is enforced three times:** the service (role `advisor` with an advisor id), `lifecycle.approve/reject` and the `apply_decision` tool (the id must exist in `advisors`). Students, admins and the AI cannot approve.
- **Risk flags:** rules decide which flags exist (rule engine flags + `rule_settings` thresholds); the one LLM call only words each flag and suggests an advisor action, with a template fallback and the same invented-number guard as Agent 3. No flags = no LLM call. Flag severities come from `rule_settings` (info shown as low); `UNIT_OVERLOAD` is informational (low).
- **Email:** one LLM call writes the opening paragraph (template fallback). The greeting with the student's name, the course list and the signature are added in code after the call, so no name reaches the LLM.
- `compliance_checks` stores one row per check code; `passed` is false for any failed result, and a non-blocking one is marked "Flag for the advisor:" in the detail.

### REST API (M6)

All routes need a JWT; the role is checked by a dependency, and ownership (a student's own data, an advisor's own students) by `app/services/access.py`. Routes are thin: they call the services and commit. Domain errors become one JSON shape `{detail, code, ...}` in `app/api/errors.py`: 400 term not plannable / edit cannot be built, 403 wrong role or not your data, 404 unknown id, 409 wrong state (`draft_exists` with `schedule_id`, `compliance_blocked` with `reasons`, `schedule_locked`, `invalid_state`, `item_conflict`, `duplicate_interest`), 413 / 415 / 422 for bad input and uploads.

| Area | Routes | Role |
|---|---|---|
| Terms | `GET /terms/plannable` (open + offerings; upcoming; last two closed) | any |
| Student | `GET /students/me/dashboard`, `GET/POST /students/me/interests`, `PATCH /students/me/interests/{id}` (confirm / remove), `POST /students/me/documents` (PDF or .txt, 5 MB, pypdf, then InterestExtractor) | student |
| Schedules | `POST /schedules/build` (400 term closed, 409 `draft_exists`), `GET /schedules/current`, `GET /schedules/history?term=`, `GET /schedules/{id}`, `GET /schedules/{id}/suggestions` (two groups), `POST /schedules/{id}/items` (add / remove / swap, re-validated live), `POST .../reset`, `DELETE /schedules/{id}` (discard), `POST .../withdraw`, `.../resubmit`, `.../submit` | student (reads: also their advisor, admin) |
| Courses | `GET /courses/search?q=&term=` (pg_trgm; a student gets the rule verdict per course), `GET /courses/{code}` (sections, instructors, `syllabus_url` may be null) | any |
| Advisor | `GET /advisor/caseload`, `GET /advisor/schedules/{id}` (live checks, flags, AI run, changes with the AI's original course for a swapped row, messages, versions), `POST .../recheck`, `POST .../decision` | advisor (own students); admin reads |
| Messages | `GET/POST /messages/{student_id}` (GET marks the other side's messages read) | the student, their advisor; admin reads |

- **Edits:** the student can add, remove or swap unlocked courses; locked AI core cannot be removed or swapped. Each edit writes `schedule_changes` (a swap records both courses) and sets `edited_by_student`; `reset` restores `schedules.ai_baseline`.
- **Interests:** a typed interest is trimmed, 1 to 60 characters, saved as `manual` / `confirmed` with its embedding; a duplicate (case-insensitive, active) is 409. A removed one typed again is brought back.
- **Decisions:** an advisor can decide only for students they advise; approving re-checks seats live (409 `compliance_blocked`, schedule stays `submitted`).
- **Tests:** each API test runs in a rolled-back transaction with a fake LLM and a fake email sender (`tests/conftest.py`, fixture `api`); `test_role_matrix` calls every route as student / advisor / admin / no token and fails if a route is missing from the matrix.

### LLM usage limits (needed for CPU-only)

- At most **one LLM call per agent run**, with short prompts.
- Explanations are generated once, saved in the database and reused.
- Rule checks never wait on the LLM.
- If the LLM is unavailable, the app falls back to template text, so the rules and the schedule still work.

---

## 5. Decisions log

| # | Decision | Reason |
|---|---|---|
| D1 | One AI-built schedule plus suggested electives, not 3 options | Agreed at the 8 Oct sync-up; light/standard/maximum options added nothing |
| D2 | Chat screen dropped for MVP | Time; it can be added later as one route plus an orchestrator calling Agents 1–3 as tools |
| D3 | Rule engine is deterministic; the LLM never decides eligibility | Safety, explainability, compliance |
| D4 | Interest matching = local embeddings + pgvector | Free, no API key, runs in Postgres |
| D5 | LLM = Ollama `qwen2.5:3b` for development; Claude API optional for the demo, switched in `.env` | Dev PC: 16 GB RAM, Intel Iris Xe (no NVIDIA GPU) → runs on the CPU |
| D6 | Plain Anthropic / Ollama tool-calling loop, not the Claude Agent SDK | The Agent SDK runs the Claude Code program behind the scenes, which is too heavy for a web backend |
| D7 | No custom ML model | Popularity is SQL; matching is embeddings; reasoning is a ready-made LLM (agrees with the Gemini review) |
| D8 | Approve endpoint accepts the advisor role only | "Only an advisor can approve; the AI cannot" |
| D9 | **Transfer credit dropped from scope** (7 Nov). Only exemption-exam waivers remain (`course_waivers`) | Simplify MVP; handbook waivers still needed for 95-796 / 95-888 |
| D10 | **Spreadsheet no longer used at runtime** (7 Nov). The catalog was cleaned once into `data/seed/*.csv`; core requirements live in `reference_data.py` | One source of truth in the repo; no Excel copy step; readable diffs in Git |
| D11 | Code is generated with **Claude Code**, one milestone at a time (M1–M9), with the project rules given at the start of every session | Small, reviewable steps; the golden rules stay in front of the coding agent every session |
| D12 | **The plannable term is decided by date, not a fixed flag.** A term can be planned while its registration is open (`term_registration(today)`); "today" = `APP_TODAY` or the real date. `terms.is_planning` removed | Matches how registration really works; also supports new students and demos at other dates |
| D13 | **Interest flow:** resume / undergrad text → LLM suggests labels (status *suggested*); the student keeps, removes or adds; a typed interest is saved by the backend as *manual, confirmed* with its embedding; only *confirmed* ones are used | "Nothing is saved until you say yes" (8 Oct meeting); manual entry must persist |
| D14 | **UI follows the v2 mockups.** Images in `docs/mockups/`, rules and tokens in `docs/UI_SPEC.md` (colors measured from the mockups: primary `#0e5a5c`, advisor bar `#0b3b3d`, page `#f3f4f6`; IBM Plex Sans + Mono). The spec lists intended differences (no chat, no transfer credit, term picker, upload, two suggestion groups, discard/withdraw) | Claude Code can read the images; one written source for differences |
| D15 | **Schedule lifecycle with soft discard.** draft → submitted → approved / rejected; student can discard a draft (status `discarded`, row kept), withdraw a submitted one before the decision, build again (old → `superseded`, `previous_schedule_id`), resubmit after rejection. Approved is locked | Students need to start over; history and audit trail must stay intact for compliance |
| D16 | **Elective recommendations: two groups, coverage, fit.** score = 0.7 similarity + 0.2 popularity + 0.1 fit; "Matches your interests" only if similarity ≥ 0.40, else "Popular with AIM students"; best course per confirmed interest first, no near-duplicates (> 0.85); fit = preferred time of day + modality + no clash with locked core; clashes flagged not removed; weights in `rule_settings` | Clearer reasons for each suggestion, covers all interests, respects schedule and preferences |
| D17 | **One draft, filled to 54 units; 55–60 only with QPA above 3.50; no lighter load.** The AI builds a single draft (locked core + electives by rank) up to `tracks.max_units_per_term` = 54. A schedule can be submitted with up to 54 units, or up to `tracks.max_units_extended` = 60 only if the cumulative QPA is above `tracks.extended_units_min_qpa` = 3.50 (no QPA yet = 54). Over 60 is blocked. The advisor sees UNIT_OVERLOAD for 55–60. Low QPA no longer reduces the load; it stays a risk flag | Client decision 7 Nov |

---

## 6. Gemini review — what we use

| Gemini idea | Verdict |
|---|---|
| No custom model; SQL plus LLM with tool calling | **Use** |
| Tools `get_active_courses`, `rank_courses_by_popularity`, `get_course_prerequisites`, `search_course_content` | **Use** (Agent 2 tools) |
| Plan → execute → validate → explain loop | **Use** (all agents) |
| Explanations use only facts returned by tools | **Use** |
| Popularity from fill ratio, waitlist and velocity over 5 years | **Change**: our data has no capacity, waitlist or history, so we generate synthetic history (A1) |
| Recency decay e^(−λ·age) | **Use** |
| "The agent validates prerequisites" | **Change**: the rule engine validates; the agent only reads pass or fail |
| Free-text intent parsing | **Later** (chat screen) |
| LangGraph or CrewAI | Optional; plain loop for MVP |
| Collaborative filtering | Later, as a simple SQL count of courses often taken together |

The final popularity view is `course_popularity` in `db/schema.sql` (section 10.2).

---

## 7. Sample data (Courses_data.xlsx) — what's there and the gaps

> **Decision (7 Nov):** only the **Courses**, **Courses Terms** and **Course section - schedule** sheets are used as source data. The program, student and advisor sheets are **not** used; that data is created cleanly by `scripts/reference_data.py` and the loader (section 10). Gaps G4–G10 below are therefore solved by our own data.

| Sheet | Rows | Notes |
|---|---|---|
| Courses | 187 | 94-xxx and 95-xxx only. Units fixed / variable / range; prerequisites as text ("A and (B or C)") |
| Courses Terms S27,F26,M26 | 264 | Offered or cancelled per term; full semester vs mini |
| Course section - schedule | 582 | S27: 253 sections |
| program requirements | 39 | AIM12: 19 core, AIM16: 20 core (+95-900); 18 elective units; 144 units total; QPA ≥ 3.0 |
| student data | 100 | 50 AIM12 + 50 AIM16. Completed courses, enrollment history and preferences are JSON |
| advisor data | 6 | Caseload and assigned population are JSON |

### Gaps found

| Gap | Detail |
|---|---|
| G1 | `capacity` and `seats_filled` are "Not Available" for every section |
| G2 | 80 of the 253 S27 sections are "TBA" (no time); 71 have no instructor listed |
| G3 | 33 of the 56 prerequisite course IDs are outside the catalog (90-, 36-, 10-, 17-…) |
| G4 | No resume or undergraduate data for students |
| G5 | No list of courses needing special approval |
| G6 | History is only 2 terms (S26, M26): 1,001 completions across 181 courses; the top course has 16 students |
| G7 | Data has 19–20 core courses + 18 elective units; the mockup shows 11 core + "choose 5" |
| G8 | No Summer 2027 offerings (needed for AIM12 graduation projection) |
| G9 | `credits_in_progress` is given (48–60 units) but **which** courses are in progress for F26 is not listed |
| G10 | Student histories don't line up with the program plan: AIM12 runs F26 → S27 → M27, but students already have S26/M26 completions, and some have 120 completed + 48 in progress (> 144 total) |

### Assumptions (defaults — confirm with team / Gray DI)

- **A1** Generate synthetic capacity, enrollment, missing times and 5 years of enrollment history, with a fixed random seed.
- **A2** ~~Course number < 600 = blocked~~ → **updated:** undergraduate = number < 600; allowed only with petition, max 12 units, grade B or better (AIM handbook text).
- **A3** Special topics, independent study and internship = needs approval.
- **A4** Follow the spreadsheet (18 elective units), not the mockup.
- **A5** Prerequisites outside the catalog count as met if they appear in the student's history; otherwise "missing prerequisite".
- **A6** Write short synthetic resume / undergrad summaries per student, plus a resume upload in the UI.
- **A7** Demo login with seeded student and advisor accounts.
- **A8** Copy **M26** offerings to M27 (summer courses differ from spring) as an assumption for Summer 2027 projection.
- **A10** Keep the 100 Excel students as loaded (`source='excel'`), and add about 8 curated demo students with consistent histories for the demo scenarios (see G9, G10).
- **A9** QPA minimum 3.0 is still a placeholder until program rules are confirmed.

---

## 8. Next steps

### Step 2 — Database design ✅ done 7 Nov

- [x] Normalized schema `db/schema.sql` (39 tables, 1 view, 1 materialized view)
- [x] Reference data `scripts/reference_data.py` (program, tracks, core plan, rules, people)
- [x] Seed CSVs `data/seed/` (catalog cleaned once from Excel; spreadsheet no longer needed)
- [x] Loader `scripts/load_data.py` (seed CSVs + fixed-seed synthetic data)
- [x] Embeddings `scripts/embed_courses.py` (MiniLM → pgvector)
- [x] Checks `db/checks.sql`
- [ ] Run on your PC (section 10.8) and confirm assumptions A11–A13

### Step 3 — Code (Claude Code, one prompt per milestone)

- [x] M1 Backend skeleton, DB models, login (7 Nov, 93 tests pass)
- [x] M1b Catch up with registration windows (7 Nov, 108 tests pass): `Term` model has `registration_opens/closes`; `settings.today()` (APP_TODAY); `app/services/terms.py` (`registration_status`, `plannable_terms`); `GET /health` lists `plannable_terms`; `GET /terms` (any logged-in user, from the current year on)
- [x] M2 Rule engine (7 Nov, 146 tests pass): `app/rules/engine.py` (pure checks, `validate`, `is_valid`), `app/rules/repository.py` (`load_context`, `validate_schedule`), `tests/test_rules.py` (38 tests). Convention: failed + severity `block` blocks; failed + other severity = advisor flag (petition, approval); conditional prerequisite = passed with flag `conditional`. NULL section capacity = no seat limit
- [x] M3 LLM client, tools, agent base (7 Nov, 171 tests pass): `app/llm/client.py` (`LLMClient.complete` → text, provider, model, ms; Ollama via httpx, Claude via lazily imported `anthropic` only when `LLM_PROVIDER=claude`, template fallback; never raises), `app/llm/redact.py` (`Redactor`: student/advisor names and emails → "the student" / "the advisor"); `app/tools/` (`catalog`, `student`, `popularity`, `vector_search` with MiniLM cached once per process, `email`; registry `TOOLS`; term always a parameter); `app/agents/base.py` (`Agent.run`: plan → decide → check, retry up to 3 → save → explain; tool allow-list; at most one LLM call; one `agent_runs` row + one `audit_log` row per run, `failed` row kept on error). Tests: `test_llm.py`, `test_tools.py` (Priya S0056), `test_agent_base.py` (no name reaches the LLM). Demo: `python scripts\demo_search.py`. Convention: a section with NULL capacity counts as having seats, as in the rule engine
- [x] M4 Agents 1–3 (7 Nov, 194 tests pass incl. 1 real-Ollama test): `requirements_agent.py` (audit, no LLM, one current `degree_audits`), `elective_agent.py` (`ElectiveAgent`: weights from rule_settings, fit = 0 for now, excludes core / taken / waived / draft courses, one LLM call for reasons; `InterestExtractor`: one LLM call → up to 5 `suggested` labels, keyword fallback, skips labels the student already has incl. removed, never confirms), `schedule_agent.py` (term must be plannable today; remaining core OFFERED in the term by `recommended_term_seq`, prerequisites via the rule engine, locked; electives by rank; clash-free sections; validate → drop/swap → retry up to `SCHEDULE_MAX_RETRIES`; lighter load when QPA < min; `ai_explanation` + `load_rationale`), `pipeline.build_draft`. Rebuild sets `previous_schedule_id` and writes `schedule_changes` build|rebuild. LLM guard: an explanation or reason with numbers not in the facts, or one that only repeats the prompt, falls back to the template (logged as `llm_guard` in tool_calls). ORM now maps `schedules.previous_schedule_id/discarded_at` and `elective_suggestions.fit/group_code/matched_interest/flags`. Tests: `test_agents.py` (Priya valid 48-unit S27 draft with all 6 S27 core; Daniel 42-unit lighter load with rationale; popularity basis without confirmed interests; Alex core-only S27 draft, no prerequisite failures; F26/M27 refused on 2026-11-07; extractor suggests, never confirms; agent_runs rows with tool calls; no names reach the LLM). Demo: `python scripts\demo_agents.py` (`--no-llm`, `--save`)
- [x] M4b Schedule lifecycle, elective fit / two groups / diversity, 54-unit fill (7 Nov, 234 tests pass): `app/services/schedule_lifecycle.py` (`build`, `discard`, `withdraw`, `resubmit_from_rejected`; `DraftExists`, `ScheduleConflict`, `ScheduleLocked`, `InvalidTransition`; each step writes `schedule_changes` + `audit_log`; the `save_schedule` tool and `ScheduleAgent` use it, `build_draft(..., replace=False)` raises `DraftExists` before any agent runs); `app/agents/elective_scoring.py` (pure: similarity per interest, fit, groups, diversity, flags; every number from rule_settings), `app/agents/core_plan.py` (the locked-core selection shared by Agents 2 and 3, so clash flags match the draft); tools `get_term_sections`, `course_similarities`, `get_open_schedule`; rule engine UNIT_LIMIT 54 / 60 with QPA > 3.50 (`RuleContext.qpa`, flag `unit_overload`); ScheduleAgent fills to `SCHEDULE_FILL` (54) with no lighter load (`light_load` removed) and a rationale like "54 of 54 units: 42 core + 12 electives."; ORM maps `tracks.max_units_extended` / `extended_units_min_qpa`. Tests: `test_lifecycle.py` (15), `test_elective_scoring.py` (9), `test_rules.py` (UNIT_LIMIT 54/55–60/61 for Priya, Daniel, Alex), `test_agents.py` (every interest covered, no near-duplicates, clash flagged not removed, Alex only `popular`, never above 54, no core course as an elective, weights read from rule_settings)
- [x] M5 Agents 4–5 + approval email (7 Nov, 255 tests pass): `RiskAgent` (LOW_QPA, BEHIND_PACE, CONDITIONAL_PREREQ, LOW_SEATS, NEEDS_APPROVAL, UNDERGRAD_CAP, UNIT_OVERLOAD from rule results and `rule_settings`; one LLM call for wording + advisor action; saves `risk_flags`), `ComplianceAgent` (stages `submit` / `approve`; `compliance_checks`, `audit_log`, `notifications`, email via Mailpit), `schedule_lifecycle.submit/approve/reject` with `NotAdvisor`, `schedule_review.submit_for_review/decide`. Tests: `test_review.py` (21): Priya submit passes; Daniel gets LOW_QPA; approve and reject emails are in Mailpit (tests delete their messages); approve fails cleanly if a section became full; only an advisor decides (service, lifecycle, tool); email failure keeps the decision; no name reaches the LLM. Demo: `python scripts\demo_review.py` (dry run; the email is sent for real, read it at http://localhost:8025)
- [x] M6 API routes for the screens (7 Nov, 331 tests pass). Part A: `schedules.ai_baseline`, `courses.syllabus_url` (`db/migrations/2026-11-07_m6_ai_baseline_syllabus.sql`, no reset), lifecycle `reset` and item edits, email written to "you". Part B: 29 routes (`app/api/`: students, schedules, courses, advisor, messages, terms; `errors.py`, `deps.py`), services `schedule_views`, `student_portal`, `catalog_search`, `advisor_portal`, `messaging`, `access`; `rules.repository.course_check_results`; `decide` now also requires the student's own advisor. New dependencies: `pypdf` (PDF text), `python-multipart` (FastAPI file uploads). Tests: `test_api_student.py`, `test_api_advisor.py` (happy path + wrong role for every route), `test_lifecycle.py`, `test_review.py`
- [x] M7 React student screens (dashboard, interests, draft schedule)
- [x] M8 React advisor screens (caseload, review), HTML decision email, term labels, universities
- [x] M9 Demo modes (Spring 2027 and Fall 2026), `scripts/demo_reset.py`, `docs/DEMO.md`, `tests/test_e2e_demo.py`, handover docs

### Dev PC status (7 Nov)

- Docker Postgres + pgvector, Mailpit: running and healthy
- Python **3.14** venv (`.venv`) — 3.12 not installed; scripts run on 3.11+
- Data loaded (`load_data.py --reset`), embeddings created
- Ollama installed; model `qwen2.5:3b`

---

## 9. Windows setup (Docker PostgreSQL + pgvector, Mailpit, Ollama)

**Dev PC:** Windows, 16 GB RAM, Intel Iris Xe (no NVIDIA GPU).

### 9.1 Install tools

1. **Turn on WSL2** (needed by Docker). In PowerShell **as Administrator**:
   ```powershell
   wsl --install
   ```
   Restart the PC when asked.
   - If it complains about virtualization, enable *Intel VT-x / Virtualization Technology* in the BIOS.
2. **Docker Desktop:** install from docker.com. Keep *Use WSL 2 based engine* ticked, then start Docker Desktop.
3. **Limit Docker's memory** so it doesn't use all 16 GB.
   - Create `C:\Users\<you>\.wslconfig` with:
     ```ini
     [wsl2]
     memory=4GB
     ```
   - Then run `wsl --shutdown` and restart Docker Desktop.
4. **Python 3.12** from python.org. Tick **"Add python.exe to PATH"**.
5. **Node.js 20 LTS** from nodejs.org.
6. **Git** and **VS Code**.
7. **Ollama** from ollama.com (Windows installer).
8. Optional: **DBeaver** or **pgAdmin** to browse the database.

Check the installs in a new PowerShell window:

```powershell
docker --version
docker compose version
python --version
node --version
git --version
ollama --version
```

### 9.2 Project folder

```powershell
mkdir C:\dev\graydi-advisor
cd C:\dev\graydi-advisor
mkdir db\init
```

Expected layout (it will grow in Step 3):

```
C:\dev\graydi-advisor\
  docker-compose.yml
  .env
  db\init\01-extensions.sql
  data\seed\*.csv          (catalog, in the repo)
  backend\      (Step 3)
  frontend\     (Step 3)
```

### 9.3 `docker-compose.yml`

```yaml
services:
  postgres:
    image: pgvector/pgvector:pg16
    container_name: graydi-postgres
    restart: unless-stopped
    environment:
      POSTGRES_USER: ${POSTGRES_USER:-graydi}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-graydi_dev_pw}
      POSTGRES_DB: ${POSTGRES_DB:-graydi}
      TZ: America/New_York
    ports:
      - "${POSTGRES_PORT:-5432}:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data
      - ./db/init:/docker-entrypoint-initdb.d:ro
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER:-graydi} -d ${POSTGRES_DB:-graydi}"]
      interval: 5s
      timeout: 5s
      retries: 10

  mailpit:
    image: axllent/mailpit:latest
    container_name: graydi-mailpit
    restart: unless-stopped
    ports:
      - "1025:1025"   # SMTP (backend sends here)
      - "8025:8025"   # Web inbox: http://localhost:8025

volumes:
  pgdata:
```

### 9.4 `db/init/01-extensions.sql`

This runs once, the first time the database volume is created.

```sql
CREATE EXTENSION IF NOT EXISTS vector;     -- pgvector for interest matching
CREATE EXTENSION IF NOT EXISTS pg_trgm;    -- fuzzy text search for "Search all electives"
```

### 9.5 `.env`

```ini
# Database
POSTGRES_USER=graydi
POSTGRES_PASSWORD=graydi_dev_pw
POSTGRES_DB=graydi
POSTGRES_PORT=5432
DATABASE_URL=postgresql+psycopg://graydi:graydi_dev_pw@localhost:5432/graydi

# LLM (ollama | claude)
LLM_PROVIDER=ollama
OLLAMA_URL=http://localhost:11434
OLLAMA_MODEL=qwen2.5:3b
ANTHROPIC_API_KEY=            # only if LLM_PROVIDER=claude

# Embeddings
EMBEDDING_MODEL=sentence-transformers/all-MiniLM-L6-v2

# Email
SMTP_HOST=localhost
SMTP_PORT=1025

# Auth
JWT_SECRET=change-me-dev-only
```

Add `.env` to `.gitignore`. Never commit passwords or API keys.

### 9.6 Start and verify

```powershell
cd C:\dev\graydi-advisor
docker compose up -d
docker compose ps        # both containers "running"; postgres "healthy"
```

Check that pgvector is installed:

```powershell
docker exec -it graydi-postgres psql -U graydi -d graydi -c "SELECT extname, extversion FROM pg_extension;"
```

You should see `vector` and `pg_trgm` in the list.

Quick vector test:

```powershell
docker exec -it graydi-postgres psql -U graydi -d graydi -c "SELECT '[1,2,3]'::vector <=> '[1,2,4]'::vector AS cosine_distance;"
```

Mailpit inbox: open **http://localhost:8025**.

**GUI connection** (DBeaver / pgAdmin):

| Setting | Value |
|---|---|
| Host | `localhost` |
| Port | `5432` |
| Database | `graydi` |
| User | `graydi` |
| Password | `graydi_dev_pw` |

### 9.7 Ollama

```powershell
ollama pull qwen2.5:3b
ollama run qwen2.5:3b "Say hello in one sentence"
ollama pull qwen2.5:7b        # optional, to compare quality (slower, about 5–6 GB RAM)
```

Ollama runs in the background at `http://localhost:11434`. Test the API:

```powershell
Invoke-RestMethod -Uri http://localhost:11434/api/generate -Method Post `
  -Body '{"model":"qwen2.5:3b","prompt":"Reply with OK","stream":false}' -ContentType "application/json"
```

### 9.8 Python environment and connection test

If PowerShell blocks the virtual environment script, allow it once:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

Then create the environment and install packages:

```powershell
cd C:\dev\graydi-advisor
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install "psycopg[binary]" pgvector sentence-transformers pandas openpyxl python-dotenv
```

Save as `test_setup.py` and run `python test_setup.py`:

```python
import psycopg
from sentence_transformers import SentenceTransformer

model = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")  # first run downloads ~90 MB
v1, v2 = model.encode(["AI governance in banks", "Model risk management in financial services"])

with psycopg.connect("postgresql://graydi:graydi_dev_pw@localhost:5432/graydi") as conn:
    vec = lambda v: "[" + ",".join(f"{x:.6f}" for x in v) + "]"
    sim = conn.execute(
        "SELECT 1 - (%s::vector <=> %s::vector)", (vec(v1), vec(v2))
    ).fetchone()[0]
    print(f"Postgres OK, pgvector OK, similarity = {sim:.3f}")
```

A similarity of roughly 0.4–0.7 means everything works.

### 9.9 Daily commands

| Task | Command |
|---|---|
| Start database + mail | `docker compose up -d` |
| Stop (keeps data) | `docker compose stop` |
| Logs | `docker compose logs -f postgres` |
| SQL shell | `docker exec -it graydi-postgres psql -U graydi -d graydi` |
| **Wipe and start fresh** (deletes all data) | `docker compose down -v` then `docker compose up -d` |

### 9.10 Troubleshooting

| Problem | Fix |
|---|---|
| Port 5432 already in use (a local PostgreSQL is installed) | Set `POSTGRES_PORT=5433` in `.env` and use 5433 in `DATABASE_URL` and the GUI |
| `docker` not recognised / engine not running | Start Docker Desktop; wait for "Engine running" |
| WSL / virtualization error | `wsl --update`; enable virtualization in BIOS |
| `vector` extension missing | The init script runs only on a new volume: `docker compose down -v` then `docker compose up -d` |
| PC slow / out of memory | Lower `.wslconfig` memory to 3GB; use `qwen2.5:3b`, not 7b; close Chrome tabs |
| `Activate.ps1` blocked | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` |
| Ollama very slow | Normal on CPU; keep prompts short; the app caches explanations |

---

## 10. Database design (Step 2 — done, tested)

### 10.1 Design approach

- **Proper normalized design.** We don't copy the Excel layout. Each fact is stored once, lists in JSON cells became child tables, lookups are real tables, and rules are enforced with keys and `CHECK` constraints.
- **Only the catalog came from Excel**, and only once: the `Courses`, `Courses Terms` and `Course section - schedule` sheets were cleaned into **seed CSV files in `data/seed/`** (`courses.csv`, `course_offerings.csv`, `sections.csv`). The project no longer needs the spreadsheet. The CSVs open in Excel if someone wants to edit the catalog.
- **Program facts** (144 units, 126 core, 18 elective, max 54 per term, QPA 3.0, outside-college and undergraduate caps) come from the AIM handbook text. They're kept in `scripts/reference_data.py`, which is easy to edit.
- **People and history** (students, advisors, enrollments, demand history) are made by a **fixed-seed generator**, so every run gives the same data. Generated values are marked (`synthetic_fields`, `is_synthetic`, `source`).
- **Core and elective requirements are separate tables** (`core_requirements`, `elective_requirements`).
- **Everything lives in the Postgres schema `advisor`.** The schema file sets `search_path = advisor, public` for the database user. In DBeaver, look under *graydi → Schemas → advisor*.

**Files:**

| File | Purpose |
|---|---|
| `db/schema.sql` | Drops and recreates schema `advisor` (39 tables, view `v_student_progress`, materialized view `course_popularity`) |
| `scripts/reference_data.py` | Editable program facts, core plan, rule settings, grade scale, terms, people lists, personas |
| `data/seed/*.csv` | Clean catalog: 187 courses, 264 course × term offerings, 582 sections (mini session and modality already mapped) |
| `scripts/import_excel.py` | One-time converter, spreadsheet → seed CSVs (only if Gray DI sends a new catalog) |
| `scripts/load_data.py` | Loads the seed CSVs plus generated data |
| `scripts/embed_courses.py` | MiniLM embeddings → `courses.embedding`, `student_interests.embedding` |
| `db/checks.sql` | Sanity checks after loading |

### 10.2 Tables (39 after M8b: `universities` added to group A)

| Group | Table | What it holds |
|---|---|---|
| **A. Program** | `programs` | Degree program (AIM) |
| | `tracks` | AIM12 / AIM16: total units, terms, min QPA, max units per term, internship |
| | `track_term_plan` | Season order per track (AIM12: Fall → Spring → Summer; AIM16: Fall → Spring → Summer internship → Fall) |
| | `core_requirements` | **Core courses per track**, recommended term, waivable by exam, prerequisite override |
| | `elective_requirements` | **Elective rules per track**: 18 units, 36 outside Heinz, 12 undergraduate (grade B or better) |
| | `rule_settings` | Rule engine and agent thresholds as JSON (low seats, pace, score weights, retries) |
| **B. Catalog** | `terms` | F21 … F27, with `is_current` (F26 in session) and **`registration_opens` / `registration_closes`**. Function `term_registration(today)` gives each term's status (open / closed / not_open_yet) |
| | `term_sessions` | FULL and mini sessions with dates. Date overlap drives the time-clash check |
| | `subjects` | Department code (94, 95, 90, …), with whether it is Heinz College |
| | `course_types` | Regular, independent study, internship, …, with `needs_approval` |
| | `courses` | Catalog plus outside prerequisite stubs; `level` is a generated column; `embedding vector(384)` |
| | `course_prerequisites` | Prerequisites in **OR-of-ANDs** form (option_no groups) |
| | `course_corequisites` | Co-requisites |
| | `course_offerings` | Course × term: offered or cancelled, course length, syllabus, `is_assumed` (M27) |
| | `sections` | Section, session, modality, capacity / enrolled / waitlisted; `seats_left` is generated |
| | `section_meetings` | One row per day and time (a section can meet MW plus F) |
| | `instructors`, `section_instructors` | Instructors (a section can have several) |
| | `course_term_stats` | Past demand (capacity, enrolled, waitlist, hours to fill), the popularity input |
| **C. People** | `advisors`, `students`, `users` | People and logins. `users` CHECK: a student user links to a student, an advisor user to an advisor |
| **D. Record** | `grade_scale` | Grade → quality points; counts toward units or not |
| | `enrollments` | Completed / in-progress courses (completed must have a grade; in-progress must not) |
| | `course_waivers` | Core courses waived by exemption exam (95-796, 95-888); the waived units move to electives. **Transfer credit is out of scope.** |
| | `student_documents` | Resume / undergrad transcript text |
| | `student_interests` | Interests with source, status (suggested → confirmed) and embedding |
| **E. Schedules** | `schedules` | One per student, term and version. Only one open (draft or submitted) per term; decision fields must be set when approved or rejected |
| | `schedule_items` | Course and section in the schedule: locked core, added by AI or student, conditional, needs approval, score, reason |
| | `schedule_changes` | What the student (or AI or advisor) changed |
| **F. Agents and audit** | `agent_runs` | Every agent run: input, tool calls, decision, explanation, LLM used |
| | `degree_audits` | Agent 1 result (one current audit per student) |
| | `elective_suggestions` | Agent 2 ranked list |
| | `risk_flags` | Agent 4 flags |
| | `compliance_checks` | Agent 5: the 7 checks at submit and at approve |
| | `audit_log` | **Append-only** (a trigger blocks UPDATE and DELETE) |
| | `messages`, `notifications` | Advisor ↔ student messages; approval / rejection emails |
| **Views** | `v_student_progress` | Units completed / in progress and QPA, **computed, never stored** |
| | `course_popularity` (materialized) | 0.6 × track share (recency-weighted) + 0.4 × demand (fill ratio + waitlist, recency-weighted) |

### 10.3 ER diagrams (generated from the live schema)

`PK` = primary key, `FK` = foreign key. In the second and third diagrams, tables from another group show only their key.

#### A+B. Program structure and catalog

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

#### C+D. People and academic record

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

#### E+F. Schedules, agents and audit

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

### 10.3b Changes added after the first design (7 Nov, D15–D17)

- `tracks`: new `max_units_extended` (60) and `extended_units_min_qpa` (3.50), CHECK `max_units_extended` ≥ `max_units_per_term`.
- `risk_flags.flag_code`: adds `UNIT_OVERLOAD`.
- `schedules`: status adds `discarded`; new `previous_schedule_id`, `discarded_at` (CHECK: discarded ⇔ `discarded_at` set).
- `schedule_changes.action`: build, rebuild, add, remove, swap, reset, submit, withdraw, discard, resubmit.
- `elective_suggestions`: new `fit`, `group_code` (interest_match | popular), `matched_interest`, `flags`.
- `rule_settings` (14 rows): `ELECTIVE_SCORE_WEIGHTS` {0.7, 0.2, 0.1}; new `ELECTIVE_MATCH_THRESHOLD`, `ELECTIVE_DIVERSITY`, `ELECTIVE_FIT`, `SCHEDULE_FILL`; `UNIT_LIMIT` updated.
- Tested 7 Nov: one open schedule enforced; discarded requires `discarded_at`; discard → rebuild → submit → withdraw → supersede chain works; existing data unchanged.
- **M6 (8 Nov, no reset; `db/migrations/2026-11-07_m6_ai_baseline_syllabus.sql`, re-runnable `ADD COLUMN IF NOT EXISTS`):**
  `schedules.ai_baseline` jsonb: the draft exactly as the AI built it, `[{course_code, section_id, item_type, units, is_locked, is_conditional, needs_approval, match_score, reason}]`, written on every build and rebuild (a resubmitted draft keeps the rejected one's baseline); it is the target of `POST /schedules/{id}/reset`. `courses.syllabus_url` text, nullable, empty until Gray DI provides the data (`course_offerings` already has its own per-term `syllabus_url`).
- **M8b (7 Nov, no reset; `db/migrations/2026-11-07_m8b_universities_term_labels.sql`, re-runnable):**
  new table `universities` (`university_code` PK, `name`, `short_name`, `email_domain` nullable unique). `programs.university_code` (FK, NOT NULL) and
  `advisors.university_code` (FK, NOT NULL: an advisor sees only students of their own university; `services/access.can_access_student` and the caseload
  query both check it). The migration inserts CMU ("Carnegie Mellon University"; `email_domain` = the demo domain), points the AIM program and all demo advisors at it,
  and replaces term codes in text stored earlier (`schedules.ai_explanation` / `load_rationale`, `risk_flags`, `elective_suggestions.reason`,
  `compliance_checks.detail`) with labels ("S27" becomes "Spring 2027"; ids such as `94-830-S27-A4` and the logs `agent_runs` / `audit_log` are left alone).
  `scripts/reference_data.py` has `UNIVERSITIES`, `PROGRAMS` with a university and `ADVISOR_UNIVERSITY`; `scripts/load_data.py` seeds them (so `--reset` gives the same result).
  **Term labels:** one helper, `services/terms.term_label(term)` = `season + " " + year`. Every API response that has a `term_code` also has `term_label`
  (term picker, dashboard schedules, schedule view, history, suggestions, caseload, review); `next_term_label` / `projected_graduation_label` on the dashboard progress;
  rule-engine details, agent texts, the facts given to the LLM (so the number guard accepts "2027"), lifecycle errors and the emails use the label. Codes stay in URLs, the
  database and logs. **Names:** `ProgramInfo` (university, college, degree, program title) comes with the student (dashboard, review) and the advisor (caseload); `GET /auth/me`
  returns `university` and `programs` for the footer; the login page is generic (no university or program before login). `APP_ENV` (in `/health` as `env`) controls the demo logins.

### 10.4 Rules the schema enforces or supports

| Rule | How |
|---|---|
| **Prerequisites with mixed AND/OR** | `course_prerequisites` in OR-of-ANDs form. `(95-712) or (17-683 and 17-681) or (95-807)` → option 1 {95-712}, option 2 {17-681, 17-683}, option 3 {95-807}. A course passes if **every** row of **any one** option is completed or credited. If the prerequisite is still in progress, the course is *Conditional*. |
| **Program sequence over catalog prerequisites** | `core_requirements.catalog_prereqs_apply = false` with a note (A11). |
| **Time clash** | Sessions overlap by date (`term_sessions`), the same `day_of_week` is shared, and the times overlap. M3 and M4 never clash; FULL overlaps both. TBA sections never clash. |
| **Seats** | `seats_left` is a generated column (capacity − enrolled); the approve step reads it live. |
| **Undergraduate courses** | `courses.level` is generated from the course number (< 600). The cap and petition come from `elective_requirements` and `rule_settings.UNDERGRAD_CAP`. |
| **Needs approval** | `course_types.needs_approval` (independent study, internship, residency, study abroad, thesis). |
| **One open schedule per term** | Partial unique index `one_open_schedule`. |
| **Discard keeps the row** | `schedules.status = 'discarded'` ⇔ `discarded_at` set (CHECK). `previous_schedule_id` links a version to the one it replaced or was resubmitted from. |
| **Units per term (D17)** | `tracks.max_units_per_term` = 54, `max_units_extended` = 60 (CHECK ≥), `extended_units_min_qpa` = 3.50; rule_settings `UNIT_LIMIT`, `SCHEDULE_FILL`. `risk_flags.flag_code` adds `UNIT_OVERLOAD`. |
| **Only advisors decide** | `schedules` CHECK: approved or rejected ⇔ `decided_by` and `decided_at` are set. The API also checks the advisor role. |
| **Only open terms can be planned** | `terms.registration_opens/closes` + function `term_registration(today)`; checked by the rule engine (OFFERED) and the build route. |
| **Audit can't be changed** | Trigger on `audit_log`. |
| **QPA is never out of date** | Computed in `v_student_progress` from `enrollments` + `grade_scale`. |

### 10.5 Where the data comes from

| Data | Source |
|---|---|
| 187 courses, descriptions, units, prerequisites | `data/seed/courses.csv` (from Excel `Courses`) |
| Offered / cancelled per term (F26, S27, M26) | `data/seed/course_offerings.csv` (from Excel `Courses Terms`) |
| 582 sections, days and times, instructors, modality | `data/seed/sections.csv` (from Excel `Course section - schedule`) |
| 33 outside prerequisite courses (90-, 10-, 36-, …) | Created as stubs (`in_catalog = false`) |
| M27 offerings and sections | Copied from M26 (`is_assumed`, A8) |
| Program, tracks, core plan, elective rules, grade scale | `reference_data.py` (from the AIM handbook text) |
| Term and mini-session dates | Approximate CMU calendar (A12) |
| Capacity / enrolled for all sections; 37 TBA times; 20 instructor profiles | Generated (marked) |
| 126 students (incl. 1 new student), 6 advisors, 1,794 enrollments, 21 exemption-exam waivers, interests, resumes, logins | Generated (seed 42) |
| 1,170 rows of demand history 2021–2026 | Generated |

**Generated population:**

| Cohort | Track | Students | Status | Purpose |
|---|---|---|---|---|
| F25 | AIM12 | 30 | graduated (144 units each) | Popularity history (what past students took) |
| F25 | AIM16 | 25 | active, term 4 in progress | History; graduating Dec 2026 |
| F26 | AIM12 | 40 | active, term 1: Mini 1 graded, Mini 2 and full-semester in progress | **Plan Spring 2027** |
| F26 | AIM16 | 30 | active, same | **Plan Spring 2027** |
| S27 | AIM12 | 1 | active, **new student**, no history | **Alex Rivera**: new-student path; 3 suggested interests to confirm |

Other generated details:

- **Demo students:** **Priya Raman** (AIM12, QPA ≈ 3.8, confirmed interests, message from Dr. Lee) and **Daniel Okafor** (AIM12, QPA ≈ 2.6 → LOW_QPA flag, interests strategy / consulting). Both are advised by **Dr. Morgan Lee** (ADV001).
- **Interests** (current students): 111 confirmed, 93 suggested (waiting for the confirm-interests screen), and 27 students with none (popularity path).
- **Spring 2027 seats:** 23 of 253 sections are nearly full, for LOW_SEATS demos.
- **Logins:** all demo users have password `demo1234`, for example `priya.raman@demo.graydi.local`, `alex.rivera@demo.graydi.local` (new student), `daniel.okafor@demo.graydi.local`, `morgan.lee@demo.graydi.local`, `admin@demo.graydi.local`.

### 10.6 Test results (7 Nov, PostgreSQL 16 + pgvector)

- Schema creates without errors, and the loader runs in about 15 seconds.
- Core units are 126 for both tracks. Every graduated AIM12 student has exactly 144 units.
- F26 students carry about 48 units: about 19 completed in Mini 1 and about 30 in progress.
- **0 time clashes** in any student's term.
- All 6 Spring 2027 core courses for AIM12 term 2 have open sections.
- 8 current students are below QPA 3.0, which feeds the LOW_QPA demos.
- **Same data every run:** the generator gives identical data whatever Python's hash seed (checked with an md5 of all enrollments across 3 runs).
- The embedding script was tested with stand-in vectors. The real MiniLM model downloads on your PC on the first run.

### 10.7 New assumptions (confirm with team / Gray DI)

- **A11** The core courses 95-864 and 95-891 follow the AIM sequence, not their catalog prerequisites. Read strictly, 95-864 needs ML courses that AIM students never take.
- **A12** Term and mini-session dates are approximate, but good enough for clash and "completed vs in progress" logic.
- **A14** Registration windows are approximate (Fall 1 Apr – 4 Sep, Spring 1 Nov – 22 Jan, Summer 1 Mar – 28 May); edit `registration_window()` in `reference_data.py` when real dates are known.
- **A13** The core plan by term is balanced 48 / 42 / 36 units (`CORE_PLAN_AIM12`). For AIM16, the summer-only courses are assumed to move to the final Fall term. Fall 2027 offerings are not in the data.

### 10.8 Run it on your PC

From the project folder, with Docker running:

```powershell
cd "C:\Users\voidmain\Work and Learn\MasterStudy\CMU\courses\MSAIM\fall-semester\AI Introduction\coding\graydi-advisor"

# 1. Python environment (once). The catalog is already in data\seed\, so no Excel file is needed.
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt          # sentence-transformers also installs PyTorch (CPU), a few minutes

# 2. Create the tables and load the data (safe to repeat; --reset rebuilds everything)
python scripts\load_data.py --reset

# 3. Embeddings for pgvector (first run downloads the ~90 MB model)
python scripts\embed_courses.py --test "AI governance in banks"

# 4. Checks
Get-Content db\checks.sql | docker exec -i graydi-postgres psql -U graydi -d graydi
```

If `python` points to the wrong version, use `py -3.12` instead. If `Activate.ps1` is blocked, run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once.

### 10.9 Next: Step 3 (code with Claude Code)

1. Project rules for the coding agent: rules from section 2, stack, folders, and "never change `db/schema.sql` without updating this doc".
2. Backend skeleton: FastAPI, SQLAlchemy models **mapped to the existing schema** (reflect `advisor`), login with `users` + JWT.
3. Rule engine + unit tests: the 7 checks, using `course_prerequisites`, `term_sessions` and `section_meetings`.
4. Agents 1 → 5, then the React screens.

---

## 11. Final status (handover, 9 Nov 2026)

**Tests:** backend `pytest -q` = 365 passed on a freshly loaded database (364 without the one LLM test; figure reported by the user after the last changes); frontend `npm test` = 24 passed; `tsc -b` clean.
Keep this number current when tests are added.

### 11.1 Screens (React, `frontend/src/pages/`)
| Route | Page | Role | Notes |
|---|---|---|---|
| `/login` | Login | all | generic; demo logins only when `APP_ENV=development` |
| `/` | StudentDashboard | student | progress, term picker (by date), core / electives, interests |
| `/interests` | ConfirmInterests | student | resume / transcript upload, keep / remove, add own interest |
| `/schedule/:term` | DraftSchedule | student | status-aware: draft, submitted, approved, rejected, previous versions |
| `/advisor` | AdvisorCaseload | advisor | filters, search, program filter only if the advisor has 2+ programs |
| `/advisor/schedules/:id` | AdvisorReview | advisor | checks, risk flags, how the AI built it, messages, decision, email preview |

All names (university, college, program, term label, advisor title) come from the data; `docs/UI_SPEC.md` lists the intended
differences from the mockups.

### 11.2 API (FastAPI, interactive docs at `/docs`; 30 operations)
| Route | Summary |
|---|---|
| `GET /advisor/caseload` | Caseload |
| `GET /advisor/schedules/{schedule_id}` | Review |
| `POST /advisor/schedules/{schedule_id}/decision` | Decision |
| `POST /advisor/schedules/{schedule_id}/email-preview` | Email Preview |
| `POST /advisor/schedules/{schedule_id}/recheck` | Recheck |
| `POST /auth/login` | Login |
| `GET /auth/me` | Me |
| `GET /courses/search` | Search |
| `GET /courses/{course_code}` | Course |
| `GET /health` | Health |
| `GET /messages/{student_id}` | Get Thread |
| `POST /messages/{student_id}` | Post Message |
| `POST /schedules/build` | Build |
| `GET /schedules/current` | Current |
| `GET /schedules/history` | History |
| `DELETE /schedules/{schedule_id}` | Discard |
| `GET /schedules/{schedule_id}` | Get Schedule |
| `POST /schedules/{schedule_id}/items` | Edit Items |
| `POST /schedules/{schedule_id}/reset` | Reset |
| `POST /schedules/{schedule_id}/resubmit` | Resubmit |
| `POST /schedules/{schedule_id}/submit` | Submit |
| `GET /schedules/{schedule_id}/suggestions` | Suggestions |
| `POST /schedules/{schedule_id}/withdraw` | Withdraw |
| `GET /students/me/dashboard` | Dashboard |
| `POST /students/me/documents` | Upload Document |
| `GET /students/me/interests` | Interests |
| `POST /students/me/interests` | Add Interest |
| `PATCH /students/me/interests/{interest_id}` | Update Interest |
| `GET /terms` | List Terms |
| `GET /terms/plannable` | Plannable |

Domain errors are JSON with a `code` (mapped to 400, 403, 404, 409). Every route is in the role matrix test.

### 11.3 Diagrams
`docs/diagrams.html` (open in a browser) holds the architecture, agent flow, schedule lifecycle and database diagrams; the
Mermaid sources are in sections 3, 4 and 10 of this file.

### 11.4 Open points
`docs/ASSUMPTIONS.md` (questions for Gray DI). Known gaps: the pages were checked as text only and not compared pixel by pixel with the mockups; students read advisor messages but cannot reply in the portal.
