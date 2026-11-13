# CLAUDE.md

Guidance for Claude Code in this repo. ASU CS capstone "AI Build Router" for sponsor Genovation AI.
Simon Fox is the backend & deployment lead and usually works here directly with Claude.

## What the project is

A user describes a product in plain English; the system plans it, hands the work to AI builders, checks the
results and reports progress in a dashboard. Original vision: the "Unified Developer Blueprint" PDF (Simon has it).
**Current direction (Nov 2026):** a role-based agent team. A lead orchestrator hands tasks to backend, frontend and tester
agents that iterate on a real project until its tests pass. Work plan and milestones: `docs/plan.md`;
design and decisions log: `docs/agent-orchestrator-plan.md` (both local, may be uncommitted). Read them before working on the orchestrator.

## Layout

| Path | Owner | What |
|---|---|---|
| `backend/` | Simon | FastAPI API, SQLAlchemy models, Alembic migrations, pytest suite |
| `backend/orchestration.py` | Simon | The only backend file that imports orchestrator code: decompose → route → save tasks |
| `orchestrator/` | Anula | `decomposer.py` (plan), `router.py` (model tiers + `check()`), `llm.py` (Gemini `call_llm`) |
| `frontend/` | Fill | React + TypeScript (Vite) form and dashboard; deployed on Vercel |
| `spike/` | Simon + Claude | M0 agent-loop experiment (`agent_loop.py`, `template/`); `spike/runs/` is git-ignored output |
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

# agent spike (from repo root; LOCAL ONLY, runs AI-written code on this machine)
backend/.venv/bin/python spike/agent_loop.py [model] [max_steps]
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
  Same risk as above (runs AI-written code via pytest): demo only until #124.
- `ORCHESTRATOR=agent-team`: a tester agent (role `tester`) writes `tests/test_app.py` from the user's description,
  then the builder makes it pass. User text steers server-run code here; `agent.FORBIDDEN` is only a speed bump.
  Rehearse from the terminal: `backend/.venv/bin/python orchestrator/agent.py "a store with orders"`.

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
