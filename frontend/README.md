# AI Build Router — Frontend

React + TypeScript single-page app (built with Vite). Users describe a project, submit it to the backend, and watch the orchestration dashboard update as tasks are processed.

For running the whole stack (database + backend + frontend), see the [root README](../README.md).

## Prerequisites

- **Node.js 20.19+ or 22.12+** (developed on 24). Older versions fail because Vite 8 requires them. Check with `node -v`; with nvm: `nvm install 24 && nvm alias default 24`.

## Setup and run

From `frontend/`:

```bash
npm install
npm run dev
```

Open http://localhost:5173.

| Script | What it does |
|---|---|
| `npm run dev` | Development server with hot reload |
| `npm run build` | Type-checks with TypeScript, then builds to `dist/` |
| `npm run lint` | Runs ESLint |
| `npm run preview` | Serves the built `dist/` locally |

## Backend address

The app calls the backend at `VITE_API_BASE_URL`, defaulting to `http://localhost:8000`. To change it, copy `.env.example` to `.env.local` and edit it. Everything in these files is bundled into the browser's JavaScript, so never put secrets in them.

The backend must allow this app's origin in its `CORS_ORIGINS` setting (it allows `http://localhost:5173` by default).

## Data sources

The **Data Source** menu picks where results come from:

| Mode | Behavior |
|---|---|
| **Local JSON (Demo)** | Loads `public/demo-response.json` — **demo data** — with no backend needed |
| **Backend API** | `POST /projects`, then polls `GET /projects/{id}` every 2 seconds until the project is `completed` or `failed` |

Errors show the backend's own message (for example `Project not found`), or "Could not reach …" when the backend isn't running.

## API contract

The request and response shapes are defined by the backend; see [backend/README.md](../backend/README.md#endpoints) or http://localhost:8000/docs. The TypeScript types at the top of `src/App.tsx` mirror them.

The **Preferred AI Model** and **Include automated tests** controls are hidden until the orchestrator can use them. `files` and `metrics` are not returned by the backend yet; the dashboard shows placeholders for them.
