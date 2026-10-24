# AI Build Router — Backend

FastAPI backend for AI Build Router. It receives project descriptions from the frontend, stores them, and tracks their processing status.

> **Current state (Sprint 3, in progress):** projects are stored **in memory** and disappear when the server restarts. PostgreSQL storage is the next milestone; these setup steps will change when it lands (you'll also need a database and a `.env` file).

## Prerequisites

- Python 3.12 or newer (developed on 3.14)
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

## Run the server

From `backend/` with the virtual environment active:

```bash
uvicorn main:app --reload
```

- API: http://localhost:8000
- Interactive docs: http://localhost:8000/docs (try every endpoint from the browser)
- Raw OpenAPI spec: http://localhost:8000/openapi.json

`--reload` restarts the server when you save a file. Stop it with `Ctrl+C`. If port 8000 is taken, add `--port 8001`.

## Run the tests

```bash
pytest -v
```

All tests should pass. If they do, your setup matches everyone else's.

## Endpoints

| Method | Path | Success | Errors |
|---|---|---|---|
| `GET` | `/health` | `200 {"status": "ok"}` | — |
| `POST` | `/projects` | `201` project | `422` missing/blank description |
| `GET` | `/projects/{project_id}` | `200` project | `404` not found, `422` malformed ID |
| `PATCH` | `/projects/{project_id}/status` | `200` updated project | `404` not found, `422` invalid status |

Create a project:

```bash
curl -X POST http://localhost:8000/projects \
  -H "Content-Type: application/json" \
  -d '{"description": "Build me a workout tracking website"}'
```

Example response:

```json
{
  "id": "4047222a-c79c-415b-ae8d-c9a6e52661fa",
  "description": "Build me a workout tracking website",
  "status": "pending",
  "created_at": "2026-10-19T22:28:31.615142Z",
  "updated_at": "2026-10-19T22:28:31.615142Z"
}
```

- `id` is a UUID **string**.
- `status` is one of `pending`, `running`, `completed`, `failed`.
- Timestamps are ISO 8601 in UTC; convert to local time for display.

> `PATCH /projects/{project_id}/status` is currently unprotected. It will require an API key before public deployment.

## Troubleshooting

| Problem | Fix |
|---|---|
| Only `README.md` in the repo | You're on `main` — run `git switch dev` |
| `command not found: uvicorn` / `pytest` | Activate the virtual environment |
| `ModuleNotFoundError: No module named 'main'` | Run commands from inside `backend/` |
| `Address already in use` | Another server is on port 8000 — stop it or use `--port 8001` |
