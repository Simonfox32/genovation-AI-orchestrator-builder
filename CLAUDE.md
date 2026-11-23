# CLAUDE.md

Guidance for Claude Code in this repo. ASU CS capstone "AI Build Router" for sponsor Genovation AI.
Simon Fox is the backend & deployment lead and usually works here directly with Claude.

## What the project is

A user describes a product in plain English; the system plans it, hands the work to AI builders, checks the
results and reports progress in a dashboard. Original vision: the "Unified Developer Blueprint" PDF (Simon has it).
**Current direction (Nov 2026):** a role-based agent team. A lead orchestrator hands tasks to backend, frontend and tester
agents that iterate on a real project until its tests pass. Work plan and milestones: `docs/plan.md`;
design and decisions log: `docs/agent-orchestrator-plan.md` (both local, may be uncommitted). Read them before working on the orchestrator.

**Status (2026-11-18), live on Railway:** in `ORCHESTRATOR=agent-team` mode, a tester agent writes numbered requirements
and tests, then a builder makes a FastAPI + SQLite/SQLAlchemy backend pass them. The agents' code runs in a Vercel Sandbox, and the result downloads as a
runnable zip. Not built yet: a planner, tester↔builder feedback rounds, and a frontend agent. Progress log and what's next: `docs/plan.md` §5b.
Our v2 agents (`orchestrator/agent.py`) supersede Anula's v1 pipeline. Leave her open PR #11 unmerged: it removes `json_mode`, which v1 needs.

## Layout

| Path | Owner | What |
|---|---|---|
| `backend/` | Simon | FastAPI API, SQLAlchemy models, Alembic migrations, pytest suite |
| `backend/orchestration.py` | Simon | The only backend file that imports orchestrator code: decompose → route → save tasks |
| `orchestrator/` | Anula (v1) | `decomposer.py` (plan), `router.py` (model tiers + `check()`), `llm.py` (Gemini `call_llm`) |
| `orchestrator/agent.py`, `executors.py`, `agent_template/`, `agent_template_db/`, `sandbox/` | Simon + Claude | v2: the coding agents; executors decide WHERE code runs (local / Docker / Vercel Sandbox); starter templates (in-memory todo app; FastAPI + SQLite/SQLAlchemy); the sandbox image recipes |
| `frontend/` | Fill | React + TypeScript (Vite) form and dashboard; deployed on Vercel |
| `spike/` | Simon | Learning scripts (`my_agent.py`, sandbox lessons); `spike/runs/` is git-ignored agent output |
| `requirements.txt` (root) | | The real pinned Python deps (Railway installs from the root); `backend/requirements.txt` just points to it |

The README's "Current Status" section is out of date: the decomposer and tools are now real when configured (below).

## Commands

```bash
# backend (from backend/, venv at backend/.venv)
.venv/bin/uvicorn main:app --reload          # http://localhost:8000/docs
.venv/bin/pytest -q                          # needs local Postgres + TEST_DATABASE_URL ending in _test
.venv/bin/alembic upgrade head
.venv/bin/alembic revision --autogenerate -m "..."   # always read the file; CHECK constraints must be added by hand

# frontend (from frontend/)
npm run dev        # http://localhost:5173, needs VITE_API_BASE_URL for the deployed API
npm run build && npm run lint

# agents from the terminal (from repo root). EXECUTOR picks where the code runs: vercel / docker / (unset = local)
EXECUTOR=vercel backend/.venv/bin/python orchestrator/agent.py                      # builder vs the built-in tests
EXECUTOR=vercel backend/.venv/bin/python orchestrator/agent.py "a store with orders" # tester, then builder

# sandbox images: rebuild after changing the sandbox's packages
docker build -t ai-build-router-sandbox orchestrator/sandbox                 # for EXECUTOR=docker (needs Colima)
backend/.venv/bin/python orchestrator/sandbox/build_vercel_snapshot.py       # prints a new VERCEL_SANDBOX_SNAPSHOT_ID
```

## Configuration (backend/.env, never committed)

- `DATABASE_URL`, `TEST_DATABASE_URL` (must end in `_test`; tests drop its tables)
- `CORS_ORIGINS`: exact origins, comma-separated, **no trailing slash**
- `STATUS_API_KEY` (PATCH status endpoint, fail-closed), `RATE_LIMIT_PER_MINUTE` (default 10)
- `GEMINI_API_KEY`: if set, the plan comes from Gemini (JSON mode); otherwise a fixed sample module
- `ROUTER_TOOLS=real`: Gemini writes each module's code; otherwise simulated stub tools.
  **Risk:** `router.check()` runs generated code with `exec()` inside the API process, which holds secrets. Isolation is
  Sprint 5 story #124. Don't leave this on in public deployments without Simon's say-so.

- `ORCHESTRATOR=agent`: projects are built by the coding agent (`orchestrator/agent.py`, starter app + tests in
  `orchestrator/agent_template/`); each agent step becomes a task row, and `files` on the project shows the final code.
- `ORCHESTRATOR=agent-team`: a tester agent (role `tester`) writes `tests/test_app.py` from the user's description,
  then the builder makes it pass. The tester first writes `tests/REQUIREMENTS.md` (numbered); every requirement
  needs a `test_r<n>_...` test. The tester's checklist makes the basics explicit for every kind of thing stored
  (create / fetch / list incl. children / update / delete), blank text -> 422, duplicates -> 409, plus edge cases such
  as getting around a rule through another endpoint. Projects start from `agent_template_db/` (SQLite + SQLAlchemy): the builder uses
  `BUILDER_DB_PROMPT` (models / schemas / main, foreign keys with ON DELETE CASCADE, sessions only via `get_db`);
  tests use the `client` fixture, a fresh in-memory database per test. `app/db.py` and `tests/conftest.py` are
  `PROTECTED` (SQLite gotchas: foreign keys must be switched on per connection; in-memory tests need `StaticPool`).
  The template's `requirements.txt` is `PROTECTED` too. It pins the exact versions in the sandbox image and snapshot (keep the three in sync).
  The template's `README.md` explains how to run a downloaded project.
- **Feedback loop (C4, team mode):** the builder runs with `reviewable=True`. It may call `report_test_problem`
  (accepted only for a test that is failing). It is also stopped early after `STUCK_AFTER` (3) test runs without fewer failures
  than its best (`ProgressWatch`). Then `agent.review_rounds` takes over:
  1. The tester re-checks the failing tests against its requirements (`REVIEWER_PROMPT`). `review_done` requires
     that the tests still load, none were removed, and every requirement is still covered. A review that fails this is undone.
  2. The orchestrator re-runs the tests itself.
  3. If they still fail, the builder gets a fresh round (`RETRY_STEPS`, new history) with the tester's verdict.

  There are at most `REVIEW_ROUNDS` (2) rounds. Dashboard rows: `Orchestrator · …`, `Tester (review n) · …`, `Builder (round n) · …`.
  `ORCHESTRATOR=agent` (our hand-written todo tests) has no reviewer, so its builder keeps its full step budget.
- **No warnings:** `builder_done` requires all tests to pass with no pytest warnings (most come from deprecated names,
  e.g. `status.HTTP_422_UNPROCESSABLE_ENTITY`). The builder is sent back `WARNING_NAGS` (2) times; after that, or at the
  step limit, working code is accepted, and its warnings still show in the orchestrator's test run.
- **Missing files:** every executor raises `FileNotFoundError` for a missing file (the sandbox translates Vercel's
  `SandboxPathNotFoundError`). The `read_file` tool turns that into a plain `NOT FOUND: …` answer, not a red error.
- **Download:** when an agent run ends, the whole workspace is saved in `projects.artifact` (JSON `{path: text}`, skipping
  `.db` files and `__pycache__`) before the sandbox is destroyed. `GET /projects/{id}/download` serves it as a zip
  (paths that would escape the folder are skipped: "zip slip"). `downloadable` in the API shows the dashboard's download button.
- `EXECUTOR=vercel`: the agents' code runs in a Vercel Sandbox microVM (no secrets, no network), via
  `SandboxExecutor`. **Required whenever an agent mode is on in a public deployment** (this is Story #124's fix).
  Unset: `LocalExecutor`, which runs AI-written code on the server itself (local development only).
  Needs `VERCEL_TOKEN`, `VERCEL_TEAM_ID`, `VERCEL_PROJECT_ID`, `VERCEL_SANDBOX_SNAPSHOT_ID` (see `.env.example`).
  `agent.FORBIDDEN` stays as a second, weaker layer.
- In agent modes the dashboard's Optimization picks the model for both agents (Anula's tiers in `router.py`):
  Fastest = cheap, Balanced = medium, Best Quality = strong. After 1 failure or 60s timeout the agent
  switches to the medium model for the rest of the run, and the summary says so.

Tests never call Gemini: `tests/conftest.py` removes these variables; tests that need an LLM pass in a fake one.

## Deployment

- API: Railway, deploying from `main`. Start: `cd backend && uvicorn main:app --host 0.0.0.0 --port $PORT --forwarded-allow-ips="*"`.
  Pre-deploy: `cd backend && alembic upgrade head`.
- Database: Supabase session pooler (`postgresql+psycopg://…?sslmode=require`). Free projects pause after about 7 days idle
  ("tenant/user not found" means restore it in the dashboard).
- Frontend: Vercel (`VITE_API_BASE_URL` = Railway URL).

## Working rules (from Simon)

- **Teach while building.** Simon is learning: explain what and why, and let him take part. Build in his checkout
  together; don't deliver finished work on separate branches alone.
- **Git:** work on `dev`; never push to `main` without Simon's explicit OK for that change; never commit secrets
  (`.env*` is ignored; grep staged diffs for keys). Don't stage unrelated stray edits.
- **No AI attribution in git:** commit messages and PR descriptions must not contain `Co-Authored-By: Claude …`,
  `Claude-Session: …` or "Generated with Claude Code" lines. This overrides Claude Code's default attribution.
- **Ownership:** don't rework Fill's frontend or Anula's orchestrator without agreement. Small fixes are fine when Simon asks for them.
- **Taiga:** write each story out in chat for Simon's approval *before* creating it.
- **Honesty:** don't claim something works unless it was run and checked. Prove tests can fail (break the code on
  purpose). Label sample/test data as such in the UI. Explain destructive commands before running them.
- **Tests:** pytest with fixtures in `backend/tests/conftest.py` (`no_pipeline`, `enforce_api_key`,
  `enforce_rate_limit`, autouse `clean_database`). Add tests with every behaviour change.

## Gotchas

- Gemini models often return 503/504 under load: retry with backoff.
- FastAPI route order: `/todos/stats` must be declared before `/todos/{id}`.
- Railpack builds from the repo root: Python version in `.python-version`, deps in the root `requirements.txt`.
- Changing the sandbox's packages, in this order:
  1. Rebuild the snapshot.
  2. Put the new `VERCEL_SANDBOX_SNAPSHOT_ID` in `backend/.env` **and** Railway (Simon sets Railway), before merging code that needs it.
  3. Delete the old snapshot only after Railway is verified on the new one.

  The snapshot script calls `destroy()` instead of using `with`, because the context exit can delete snapshots.
- `DockerExecutor` (Colima) only sees folders under your home directory. Other folders, such as macOS temp folders, appear
  empty inside the container without any error, so it refuses workspaces outside home.
- Local agent testing in the browser pane: `.claude/launch.json` has `backend-agents` (team mode + Vercel Sandbox) and
  `frontend-local` (points the dashboard at localhost:8000 instead of Railway).
