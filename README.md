# Gray DI Degree Pathway Advisor

A web portal where a **student** gets a draft course schedule for the next term, edits it, and sends it to an
**advisor**, who approves or rejects it. Built as an MVP for the client Gray DI (graydi.us) as part of the
CMU MS in AI Systems Management program.

> **Principle: rules decide, AI suggests, only an advisor approves.**

## What it does

Five agents share one loop: plan, decide, check (retry up to 3 times), save, explain. Every run is logged in `agent_runs` and `audit_log`.

| # | Agent | What the rules do (no AI) | Where AI is used |
|---|-------|---------------------------|------------------|
| 1 | Requirements | Computes completed / remaining core and elective units, QPA and earliest graduation term from the student's record | None. Pure arithmetic, so the dashboard never waits on a model |
| 2 | Electives | Filters out courses already taken or not offered; ranks by weights stored in `rule_settings` | **MiniLM embeddings + pgvector** match each student interest to courses. **Local LLM** extracts up to 5 interest labels from the resume and undergrad text (keyword fallback) and writes a one-line reason for each top course |
| 3 | Schedule builder | Builds the draft to 54 units, validates with the rule engine, drops or swaps courses and retries; re-checks every student change | **Local LLM** writes the "How the AI built this" explanation (template fallback) |
| 4 | Risk flags | Rules and thresholds from `rule_settings` decide which flags exist | **Local LLM** words each flag and suggests an advisor action. No flags means no LLM call |
| 5 | Compliance | Submission rules (54 units; 55 to 60 only with QPA above 3.50; over 60 blocked) and approval (only the student's own advisor can decide) | **Local LLM** writes the opening paragraph of the approval / rejection email. The student's name is added in code afterwards |

### Why AI is used only for language and similarity

Rules decide, AI suggests, only an advisor approves. The AI never changes a unit count, a flag, a QPA or a decision.

- At most one LLM call per agent run.
- Names are removed before any text reaches the model.
- If the answer contains numbers that are not in the facts, or is malformed, the system uses the template text instead.
- With Ollama switched off, everything still works with templates and keyword matching.

**How decisions stay safe**

- Anything that must be correct (unit limits, prerequisites, QPA, offered terms, time clashes) is plain Python and
  database settings. No AI makes a pass/fail decision.
- Unit rule: a draft is filled to 54 units. A student may submit up to 54 units, or 55 to 60 only with a cumulative
  QPA above 3.50. More than 60 is blocked.
- Only the student's own advisor can approve or reject a schedule. Other advisors get an error.
- The LLM runs locally through Ollama (model `qwen2.5:3b`), so no student data leaves the machine. There is at most
  one LLM call per agent run, student names are removed first, and the LLM only writes explanations and email
  wording. As a safeguard, a fixed template is used if Ollama is not running.
- Every agent run and every decision is logged (`agent_runs`, `audit_log`).

## Tech stack and the AI components

| Layer | Technology |
|---|---|
| Frontend | React, TypeScript, Vite, Tailwind |
| Backend | Python, FastAPI (JWT login with student, advisor and admin roles) |
| Database | PostgreSQL 16 with the **pgvector** extension (rules, data, audit log and vectors in one place) |
| **Embeddings** | **`sentence-transformers/all-MiniLM-L6-v2` (MiniLM)**, a small local model (about 90 MB, 384-dimension vectors). It turns course descriptions and student interests into vectors, and pgvector finds the closest matches. This is how electives are matched to interests |
| **LLM** | **Ollama** running `qwen2.5:3b` locally: extracts interests from a resume, and writes explanations, risk wording and email text. It never makes a pass/fail decision |
| Email | Mailpit, a local inbox for the approval and rejection emails |

Course embeddings are created by `scripts/embed_courses.py` after the data is loaded. A student's interest is
embedded when the student confirms it ("Keep"). By default both models run on the local machine and nothing is sent to an
outside service. Setting `LLM_PROVIDER=claude` in `.env` is an optional switch that sends the LLM prompts to the Claude API
instead (student names are removed first).

## Status and limits (MVP)

- Working: student and advisor portals, the five functions, email notices (local inbox), 30 API routes,
  backend and frontend tests.
- Data is **synthetic** (students, grades, section capacity, course popularity). The course catalog is shaped like
  the CMU Heinz catalog; the portal is built to serve several universities and programs later.
- Assumptions still to confirm with the client: whether students may also start in Spring (not only Fall); the grade
  scale (A+ = 4.33); undergraduate courses allowed with a petition (at most 12 units); which programs and tracks go
  live first (rules are stored per program or track, so adding one is data, not code); the Capstone placement in
  the AIM16 core plan; and whether a hosted model may replace the local one (student names are removed either way).
- Not in the MVP: students replying to advisor messages in the portal, an add-course screen that embeds new courses
  automatically, and an agent framework such as LangGraph (the control flow is fixed on purpose).

This project was built with AI coding assistance (Claude Code), reviewed and tested by the author.

## Requirements

| Tool | Notes |
|---|---|
| Docker Desktop | Start it and wait for "Engine running" |
| Python 3.14.3 | For the backend and the data scripts (the version this project was tested on) |
| Node.js 20 or newer | For the React frontend |
| Git | To clone the repository |
| MiniLM embedding model | Downloaded automatically the first time you run `embed_courses.py` (about 90 MB, then cached). Needs internet once |
| Ollama | Local LLM for explanations, risk wording, interest extraction and email text. Install it, then run `ollama pull qwen2.5:3b` (about 2 GB) |

A laptop with 16 GB RAM is comfortable.

### Install the tools (first time only)

Skip any tool you already have. After installing, **open a new terminal** so the new programs are found.

**Windows**

1. **Git:** download from git-scm.com and accept the defaults.
2. **Python 3.14.3:** download from python.org. On the first installer screen, tick **"Add python.exe to PATH"**.
3. **Node.js (LTS, version 20 or newer):** download from nodejs.org and accept the defaults.
4. **Docker Desktop:** download "Docker Desktop for Windows" from docker.com.
   - Keep **"Use WSL 2 instead of Hyper-V"** ticked (the default). WSL 2 is a lightweight Linux that Docker needs inside Windows.
   - If the installer asks to enable WSL or "Virtual Machine Platform", agree, then **restart** the computer.
   - Open Docker Desktop and wait until it says **"Engine running"** (about one minute; the first start can take longer).
   - If Docker Desktop reports a WSL or virtualization error, run `wsl --update` in PowerShell (as administrator) and open Docker Desktop again. If it still fails, turn on virtualization in the BIOS ("Intel Virtualization Technology" or "SVM Mode").
5. **Ollama:** download from ollama.com and run the installer. It runs in the background (tray icon).

**macOS** (with Homebrew, https://brew.sh)

```bash
brew install git node
brew install python@3.14            # or download Python from python.org
brew install --cask docker          # Docker Desktop; open it once and wait for "Engine running"
brew install ollama                 # or download the Ollama app from ollama.com
```

Docker Desktop and the images used here work on both Apple Silicon and Intel Macs.

**Check the installation** (all five must print a version; `docker version` must show both Client and Server):

```
git --version
python --version        # macOS: python3 --version
node --version
docker version
ollama --version
```

## Get the code

The repository is private: you must be added as a collaborator, then sign in to GitHub when Git asks (browser
sign-in, or a personal access token as the password).

```
git clone https://github.com/chandrac-msaim/i2ai-project21.git graydi-advisor
cd graydi-advisor
```

You can use any folder name. All commands below are run from this project folder.

## Setup (first time only)

Open a terminal in the project folder.

**Windows (PowerShell)**

```powershell
copy .env.example .env
docker compose up -d                      # Postgres 16 + pgvector, and Mailpit (local email inbox)
python -m venv .venv
.\.venv\Scripts\Activate.ps1              # if blocked: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
pip install -r requirements.txt
pip install -r backend\requirements-backend.txt
python scripts\load_data.py --reset       # creates the tables and loads the data (about 1 minute)
python scripts\embed_courses.py           # course embeddings (first run downloads a ~90 MB model)
cd frontend; npm install; cd ..
ollama pull qwen2.5:3b                   # local LLM (about 2 GB, one time)
```

**macOS (Terminal)**

```bash
cp .env.example .env
docker compose up -d
python3.14 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -r backend/requirements-backend.txt
python scripts/load_data.py --reset
python scripts/embed_courses.py
(cd frontend && npm install)
ollama pull qwen2.5:3b                   # local LLM (about 2 GB, one time)
```

Check that it worked:

```
docker exec -it graydi-postgres psql -U graydi -d graydi -c "SELECT count(*) FROM advisor.students;"
```

Expected: **129** students.

### First-time download of the embedding model (Hugging Face)

The MiniLM embedding model (`sentence-transformers/all-MiniLM-L6-v2`, about 90 MB) is downloaded from Hugging Face
**once**, then cached on your machine. No account or token is needed.

1. **Connect to the internet** and make sure `HF_HUB_OFFLINE` is not set: `.env` must not contain `HF_HUB_OFFLINE=1`
   (the sample `.env.example` does not), and `$env:HF_HUB_OFFLINE` (Windows) or `echo $HF_HUB_OFFLINE` (macOS) must
   print nothing. That setting blocks downloads. In an old terminal session, clear it with
   `Remove-Item Env:HF_HUB_OFFLINE` (Windows) or `unset HF_HUB_OFFLINE` (macOS), or open a new terminal.
2. With the virtual environment active, run the download and a quick test:

   ```
   python -c "from sentence_transformers import SentenceTransformer as S; m = S('sentence-transformers/all-MiniLM-L6-v2'); print(m.encode(['test']).shape)"
   ```

   Expected output ends with `(1, 384)`. The first run shows a download progress bar and takes a minute or two.
   (Running `python scripts/embed_courses.py` also triggers the same download.)
3. Check that it is cached. The folder `models--sentence-transformers--all-MiniLM-L6-v2` should exist in:
   - Windows: `C:\Users\<your name>\.cache\huggingface\hub`
   - macOS: `~/.cache/huggingface/hub`
4. After that you may add `HF_HUB_OFFLINE=1` to `.env` to stop network lookups on every start. Do not set it before
   the first download.

If the download fails:

| Problem | Fix |
|---|---|
| "offline mode is enabled" or "Cannot reach huggingface.co" | Remove `HF_HUB_OFFLINE=1` from `.env` and from your shell (`$env:HF_HUB_OFFLINE` on Windows, `echo $HF_HUB_OFFLINE` on macOS), then run step 2 again |
| Timeout or SSL error on a campus, company or VPN network | Switch to another network (for example a phone hotspot) for this one download, or turn the VPN off |
| Download stops halfway | Run step 2 again; it resumes. If it keeps failing, delete the `models--sentence-transformers--all-MiniLM-L6-v2` folder and retry |
| Hugging Face is blocked on your network | Ask a classmate who already has the model to zip their `models--sentence-transformers--all-MiniLM-L6-v2` folder and put it into your `.cache/huggingface/hub` folder; then set `HF_HUB_OFFLINE=1` |

## Run it (every time)

Docker Desktop must be running (wait for "Engine running"). The app needs **two programs running at the same time,
each in its own terminal window**. **Keep both windows open** while you use the app: closing a window stops that
program. Use a third window for any other command (tests, `demo_reset.py`).

```
docker compose up -d                       # window 0: database and local email inbox

# window 1: backend (activate the virtual environment first)
cd backend
uvicorn app.main:app --port 8000           # API docs: http://localhost:8000/docs

# window 2: frontend
cd frontend
npm run dev                                # open the address it prints (usually http://localhost:5173)
```

- **Wait until the backend window shows "Application startup complete"** (a few seconds; the embedding model loads
  in the background) before you log in. Check http://localhost:8000/docs opens.
- Local email inbox (approval and rejection emails): http://localhost:8025
- The backend loads the MiniLM model when it starts, so the first few seconds are slow; after that, "Keep" and uploads are quick.
- Start Ollama before the demo (open the Ollama app, or run `ollama serve`) and check it with `ollama list`. If it is
  not running, the app still works but falls back to fixed templates for the wording.
- The app uses a fixed "today" for the demo. In `.env` set `APP_TODAY` and **restart the backend** after changing it.

## Demo

Password for all demo logins: `demo1234`. Domain: `demo.graydi.local`.

| Mode | `APP_TODAY` | Open term | Students (login) | Advisor |
|---|---|---|---|---|
| **Spring 2027 (default)** | `2026-11-07` | Spring 2027 | `priya.raman`, `daniel.okafor`, `alex.rivera` | `morgan.lee` |
| Fall 2026 (optional) | `2026-07-15` | Fall 2026 | `selin.aksoy`, `jonas.lindgren`, `imani.thompson` (new admits, no history) | `morgan.lee` |

Log in with the full address, for example `priya.raman@demo.graydi.local`.

| Login | Role | What it shows |
|---|---|---|
| `priya.raman` | Student, strong QPA | Normal flow, a draft of up to 54 units |
| `daniel.okafor` | Student, low QPA | Low-QPA risk flag, rejection, edit and resubmit |
| `alex.rivera` | New student | First schedule, no history |
| `morgan.lee` | Advisor | Caseload, review, approve or reject |
| `selin.aksoy`, `jonas.lindgren`, `imani.thompson` | New admits (Fall 2026 mode) | Upload a resume, interests, first schedule |

### First test: log in as Priya

1. Open http://localhost:5173 and log in as `priya.raman@demo.graydi.local` / `demo1234`. The dashboard shows her
   progress and the term **Spring 2027**.
2. Open **Interests** and **upload** a resume (text or PDF). Sample resumes are in `data/demo/`; any of them works.
   Press **Keep** on the suggested interests and wait for each to show "Kept".
3. Open **Build my schedule**. Expect a draft of up to 54 units, the rule checks, and electives in two groups that
   follow the interests you just kept.
4. Change an elective (swap or remove), then press **Send to advisor**.
5. Log in as `morgan.lee@demo.graydi.local` to review it. Approve or reject; the email appears in the inbox at
   http://localhost:8025.
6. When you are done, put the demo back to its starting state: `python scripts/demo_reset.py`.

**Walkthrough** (use the students of the mode you started):

1. Log in as a student. Open interests: add some, or upload a resume (text or PDF; Fall 2026 sample resumes are in
   `data/demo/`) and press **Keep** on the suggested interests. Expect each to change to "Kept".
2. Press **Build my schedule**. Expect a draft within 54 units, the rule checks, and electives in two groups.
3. Swap, remove or add an elective (core courses are locked). Adding too many units is refused. **Reset to AI suggestion**
   returns to the AI draft.
4. **Send to advisor.** Log in as `morgan.lee`: the caseload shows the schedule waiting. Open it, try
   **Check again** (re-checks the seats) and **Preview email**.
5. **Approve schedule** (the student sees a locked green notice; an email with the advisor in Cc appears in the inbox at
   http://localhost:8025) or **Reject with comment** (the student sees the reason and **Edit and resubmit**).
6. The advisor can send a message from the review page; the student reads it on the schedule page ("New" badge).

Put the demo back to its starting state (removes schedules, flags, messages, notifications and interests added
during a run, and empties the email inbox; the course catalog is never touched):

```
python scripts/demo_reset.py                 # Spring 2027 (default)
python scripts/demo_reset.py --mode fall     # Fall 2026
```

It is safe to run twice, and the backend can stay running.

## Tests

```
cd backend
python -m pytest -q                          # about 4 to 5 minutes
python -m pytest -q -m "not llm"             # skips the one test that calls the LLM (use this if Ollama is off)

cd ../frontend
npm test                                     # frontend tests
```

Expected: backend **365 passed** with Ollama running (364 with `-m "not llm"`), frontend 24 passed.

Run the backend tests on freshly loaded data. Schedules you create by hand in the app can make a few tests fail;
run `python scripts/demo_reset.py` (and `--mode fall`) first. If you run Mailpit on another port, set
`MAILPIT_API_URL` before running the tests.

## Project layout

| Path | What |
|---|---|
| `backend/` | FastAPI app: routes, the five agents, rule engine, email, tests |
| `frontend/` | React + TypeScript (Vite, Tailwind): student and advisor portals |
| `db/schema.sql` | Database schema (PostgreSQL, schema `advisor`) |
| `db/migrations/` | Re-runnable changes made after the first schema |
| `scripts/reference_data.py` | Program rules, core plan, rule settings, demo people |
| `scripts/load_data.py` | Creates the tables and loads the seed data (**deletes all data**) |
| `scripts/embed_courses.py` | Creates the course embeddings (pgvector) |
| `scripts/demo_reset.py` | Resets the demo to its starting state |
| `data/seed/*.csv` | Course catalog: courses, offerings, sections |
| `data/demo/` | Sample resumes for the Fall 2026 demo |
| `docs/DIAGRAMS.md` | Architecture and database diagrams (GitHub draws them) |

**Diagrams:** see [docs/DIAGRAMS.md](docs/DIAGRAMS.md).

**To change the rules:** limits and thresholds are stored in the database (`rule_settings`, `tracks`,
`core_requirements`, `elective_requirements`), seeded from `scripts/reference_data.py`. After a change there, run
`load_data.py --reset` and `embed_courses.py` again.

## Troubleshooting

| Problem | Fix |
|---|---|
| `docker` not recognised, or the engine is not running | Start Docker Desktop and wait for "Engine running" |
| Port 5432 busy (another PostgreSQL) | Set `POSTGRES_PORT=5433` in `.env` and in `DATABASE_URL` |
| Port 8000 or 5173 busy | Close the old window, or use another port (`--port 8001`) |
| Cannot log in | Check that the backend is running and http://localhost:8000/docs opens |
| Pages show Fall instead of Spring (or the reverse) | Check `APP_TODAY` in `.env`, then restart the backend |
| Explanations look generic (template wording) | Ollama is not running. Start it (`ollama serve`), check `ollama list` shows `qwen2.5:3b`, then try again |
| First "Keep" or resume upload is slow | The embedding model is loading. Wait a few seconds |
| `Activate.ps1` is blocked | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` |
| Database looks wrong or has leftover test data | Stop the servers, then `python scripts/load_data.py --reset` and `python scripts/embed_courses.py` |

## Rules for contributors

- Never commit `.env`, passwords or API keys. Use `.env.example` for new settings.
- Do not run `load_data.py --reset` while the backend is running or while someone else is testing.
- Limits and numbers live in the database, not hidden in code.
- Run the tests before you push, and keep the diagrams in `docs/DIAGRAMS.md` in step with `db/schema.sql`.
