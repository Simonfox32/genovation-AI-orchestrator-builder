# AI Build Router

## Overview

AI Build Router is a system designed to take a product idea, break it into development work, and route that work to AI development tools based on the requirements of each task.

Our capstone project explores an extension to the existing AI Build Router architecture using role-based agent orchestration. Instead of relying only on module decomposition and tool routing, the proposed design investigates how specialized agent roles could coordinate development work while continuing to use the Build Router's existing routing system underneath.

## Current Status (Sprint 4)

A first end-to-end prototype runs locally:

```
Frontend (React/Vite) → Backend (FastAPI) → PostgreSQL
                              ↓
               Orchestrator router (orchestrator/router.py)
```

A user submits a project description, the backend stores it and processes it in the background, and the frontend shows each task's tool, attempts, status, and output as it updates.

**What is real and what is sample data:**

| Part | Status |
|---|---|
| Frontend, API, database, status tracking | Real |
| Routing with test-based validation and escalation (cheapest tool first) | Real logic |
| Decomposer (description → modules) | **Stub** — every project gets the same sample modules (test data) |
| AI tools | **Simulated** — the router's tools return hard-coded code; no AI model is called yet |

Not deployed yet (Railway/Supabase is next).

## Run the Full Stack Locally

You need:

- **Python** 3.12 or newer (developed on 3.14)
- **PostgreSQL** running locally (developed on 17)
- **Node.js** 20.19+ or 22.12+ (developed on 24) — older versions can't run the frontend's build tool

All work is on the `dev` branch:

```bash
git clone https://github.com/Simonfox32/genovation-AI-orchestrator-builder.git
cd genovation-AI-orchestrator-builder
git switch dev
```

### 1. Databases (once)

```bash
createdb ai_build_router
createdb ai_build_router_test
```

### 2. Backend (terminal 1)

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env          # then edit .env with your database user
alembic upgrade head          # creates the tables
uvicorn main:app --reload
```

The API runs at http://localhost:8000 (interactive docs at http://localhost:8000/docs). Details: [backend/README.md](backend/README.md).

### 3. Frontend (terminal 2)

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:5173, set **Data Source** to **Backend API**, type a description, and click **Generate Project**. Details: [frontend/README.md](frontend/README.md).

### What you should see

Within about 5 seconds the dashboard moves from `pending` → `running` → `completed`, with two tasks:

- **add function** — `completed` by `strong_tool` after 3 attempts, with the generated code shown
- **multiply function** — `failed` after 3 attempts (none of the simulated tools write a multiply function)

The `multiply` failure is expected: it demonstrates the router's failure path.

## Repository Layout

| Folder | Owner | Contents |
|---|---|---|
| `backend/` | Simon | FastAPI API, PostgreSQL models, Alembic migrations, tests |
| `frontend/` | Fill | React + TypeScript (Vite) submission form and results dashboard |
| `orchestrator/` | Anula | Routing engine prototype (`router.py`) |

**Branches:** `dev` is the integration branch; work happens on feature branches merged into `dev`; `main` receives tested work from `dev` through pull requests.

## Project Goals

The project will investigate how agent orchestration can be integrated into the existing architecture without replacing its core components. Areas of research include:

- Defining useful agent roles and their responsibilities
- Designing workflows and handoffs between agent roles
- Coordinating changes between agents working on dependent components
- Maintaining shared context as work moves between agents
- Integrating agent-created tasks with the existing AI tool-routing system
- Preserving the Build Router's existing validation, rerouting, and artifact-management systems

## Proposed Architecture

The existing Build Router follows a general workflow of:

```
Product Request
      ↓
Requirement Parser / Product Analyzer
      ↓
Project Graph / Module Decomposer
      ↓
Routing Engine
      ↓
Task Orchestrator
      ↓
AI Tool Adapters
      ↓
Artifact Registry / Shared Contract
      ↓
Assembly & Validation
      ↓
Approval & Deployment
```

Our research explores adding role-based development coordination to this architecture.
Agent roles determine what work needs to be performed and how development tasks interact, while the existing Routing Engine remains responsible for determining which AI tool should execute the work.

```
Agent Role
    ↓
Development Task
    ↓
Existing Routing Engine
    ↓
Selected AI Tool
    ↓
Generated Artifact
    ↓
Review / Validation
    ↓
Agent Workflow Continues
```

## Sprint 2

Sprint 2 focused on research and architecture design, not implementation.
The sprint included six areas of work:

1. Compare Orchestration Approaches — Compare the existing Build Router architecture with the proposed role-based orchestration approach.
2. Research Possible Agent Roles — Evaluate possible planning, development, review, testing, and debugging roles.
3. Design Agent Workflow — Define how work moves between agent roles and how revisions are handled.
4. Design Coordination Between Agent Roles — Research how agents working on dependent components should request and coordinate changes.
5. Define Shared Context Between Agent Roles — Determine what project and task information needs to move between agents.
6. Integrate Agent Roles with Tool Routing — Research how tasks produced by agent roles could interact with the existing module-based Routing Engine.

## Team

- Simon Fox
- Aaron Romero
- Anula Dinesh
- Fill Sanpanawat
