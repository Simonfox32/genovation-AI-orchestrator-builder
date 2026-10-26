# AI Build Router — Backend

FastAPI backend for AI Build Router. It receives project descriptions from the frontend, stores them in PostgreSQL, processes each one through the orchestrator in the background, and serves the resulting tasks.

For running the whole stack (database + backend + frontend), see the [root README](../README.md).

## Prerequisites

- Python 3.12 or newer (developed on 3.14)
- PostgreSQL running locally (developed on 17)
- Git

## Setup

All work is on the `dev` branch.

```bash
git clone https://github.com/Simonfox32/genovation-AI-orchestrator-builder.git
cd genovation-AI-orchestrator-builder
git switch dev
cd backend
```

Create and activate a virtual environment (each person makes their own; `.venv` is not committed):

| macOS / Linux | Windows |
|---|---|
| `python3 -m venv .venv` | `py -m venv .venv` |
| `source .venv/bin/activate` | `.venv\Scripts\activate` |

Your prompt should now start with `(.venv)`. Then install dependencies:

```bash
pip install -r requirements-dev.txt
```

`requirements.txt` holds what the app needs to run; `requirements-dev.txt` adds testing tools.

### Database and `.env`

Create two databases — one for the app, one that tests are allowed to wipe:

```bash
createdb ai_build_router
createdb ai_build_router_test
```

Copy the example settings and fill in your database user (Homebrew Postgres usually uses your Mac username and no password):

```bash
cp .env.example .env
```

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | Database the app uses, e.g. `postgresql+psycopg://yourname@localhost:5432/ai_build_router` |
| `TEST_DATABASE_URL` | Database pytest uses. **Must end in `_test`** — tests drop all its tables |
| `CORS_ORIGINS` | Comma-separated frontend URLs allowed to call the API from a browser (default `http://localhost:5173`) |
| `DEMO_STEP_DELAY_SECONDS` | Demo only: pause between tasks so progress is visible in the UI (default `1`; tests use `0`) |

`.env` is git-ignored. Never commit it.

Create the tables:

```bash
alembic upgrade head
```

## Run the server

```bash
uvicorn main:app --reload
```

- API: http://localhost:8000
- Interactive docs: http://localhost:8000/docs (try every endpoint from the browser)
- Raw OpenAPI spec: http://localhost:8000/openapi.json

`--reload` restarts the server when you save a file. Stop it with `Ctrl+C`.

## Endpoints

| Method | Path | Success | Errors |
|---|---|---|---|
| `GET` | `/health` | `200 {"status": "ok"}` | — |
| `POST` | `/projects` | `201` project (status `pending`) | `422` missing/blank description or invalid optimization |
| `GET` | `/projects/{project_id}` | `200` project with its tasks | `404` not found, `422` malformed ID |
| `PATCH` | `/projects/{project_id}/status` | `200` updated project | `404` not found, `422` invalid status |

### Create a project

```bash
curl -X POST http://localhost:8000/projects \
  -H "Content-Type: application/json" \
  -d '{"description": "Build me a workout tracking website", "optimization": "quality"}'
```

`optimization` is optional: `balanced` (default), `speed`, or `quality`. The response comes back immediately with `"status": "pending"` and `"tasks": []`; processing happens afterward.

### Check progress

Poll `GET /projects/{id}` (the frontend does this every 2 seconds) until `status` is `completed` or `failed`:

```json
{
  "id": "cc6f4ccb-42b0-4e19-b3eb-14b6f32b85dd",
  "description": "Build me a workout tracking website",
  "status": "completed",
  "optimization": "quality",
  "summary": "SAMPLE PLAN (test data): the decomposer is not implemented yet, ...",
  "created_at": "2026-10-26T00:29:48.133784Z",
  "updated_at": "2026-10-26T00:29:50.912401Z",
  "tasks": [
    {
      "id": "…",
      "position": 1,
      "name": "add function",
      "description": "adds two numbers",
      "tool": "strong_tool",
      "status": "completed",
      "attempts": 3,
      "output": "def add(a, b):\n    return a + b"
    },
    {
      "id": "…",
      "position": 2,
      "name": "multiply function",
      "description": "multiplies two numbers",
      "tool": null,
      "status": "failed",
      "attempts": 3,
      "output": null
    }
  ]
}
```

- `id` is a UUID **string**.
- Project and task `status` are one of `pending`, `running`, `completed`, `failed`.
- A project is `completed` when processing finished, even if some tasks failed; it is `failed` only if processing itself crashed.
- `tool` is `null` until a tool passes; `output` is the code that passed.
- Timestamps are ISO 8601 in UTC (`Z`); convert to local time for display.
- Errors use FastAPI's format: `{"detail": "Project not found"}` for 404, `{"detail": [{"loc": [...], "msg": "..."}]}` for 422.

> `PATCH /projects/{project_id}/status` is currently unprotected. It will require an API key before public deployment.

## How processing works

`POST /projects` saves the project and schedules `run_pipeline` ([orchestration.py](orchestration.py)) as a background task, then returns `201` right away. The pipeline:

1. Sets the project to `running` and gets modules from the **stub decomposer** — the orchestrator's own sample modules (**test data**), the same for every description.
2. Runs each module through the orchestrator's `route()` ([../orchestrator/router.py](../orchestrator/router.py)), which tries tools cheapest-first, checks each result against the module's test, and escalates on failure.
3. Saves each result as a task (`tool_used` → `tool`, `tries` → `attempts`, `passed`/`failed` → `completed`/`failed`).
4. Sets the project to `completed`. If anything crashes, the project is set to `failed` and the details go only to the server log.

`orchestration.py` is the only backend file that knows about orchestrator code.

**Known limitation:** background tasks run inside the web server process. If the server restarts mid-run, that project stays `running`.

## Migrations

The schema is managed with Alembic. After changing `models.py`:

```bash
alembic revision --autogenerate -m "describe the change"
# read the generated file in alembic/versions/ before applying it
alembic upgrade head
alembic check        # "No new upgrade operations detected" means models and database match
```

Always review autogenerated migrations. In particular, **autogenerate does not detect CHECK constraints added to an existing table** — add those by hand with `op.create_check_constraint(...)` (see `alembic/versions/e30901b0fba4_*.py`).

## Run the tests

```bash
pytest -v
```

Tests use `TEST_DATABASE_URL` and recreate the tables before every test, so they never touch your app data. They refuse to run if that database's name doesn't end in `_test`. Tests build tables from the models (not from migrations), so they won't catch a broken migration.

## Project structure

| File | Purpose |
|---|---|
| `main.py` | App setup, CORS, API schemas (Pydantic), and endpoints |
| `models.py` | Database tables (SQLAlchemy): `projects`, `tasks` |
| `database.py` | Engine and session setup from `DATABASE_URL` |
| `orchestration.py` | Background pipeline connecting the API to the orchestrator |
| `alembic/` | Migrations |
| `tests/` | `conftest.py` (test database fixtures), API, contract, and pipeline tests |

## Troubleshooting

| Problem | Fix |
|---|---|
| Only `README.md` in the repo | You're on `main` — run `git switch dev` |
| `command not found: uvicorn` / `pytest` / `alembic` | Activate the virtual environment |
| `ModuleNotFoundError: No module named 'main'` | Run commands from inside `backend/` |
| `KeyError: 'DATABASE_URL'` | Create `.env` from `.env.example` |
| `relation "projects" does not exist` | Run `alembic upgrade head` |
| `psql` connects to the wrong port | Check for `PGPORT` in your shell config; pass `-p 5432` |
| Frontend shows "Could not reach http://localhost:8000…" | The backend isn't running, or `CORS_ORIGINS` doesn't include the frontend's URL |
| `Address already in use` | Another server is on port 8000 — stop it or use `--port 8001` |
