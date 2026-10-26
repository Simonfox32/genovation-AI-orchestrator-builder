"""Connects the backend to the orchestrator. This is the only file that knows about orchestrator code.

Flow for one project:  decompose (STUB) -> route each module (Anula's router) -> save each result as a task.

What is real and what is not, as of Sprint 4:
  - Real:  status tracking, task storage, and Anula's routing/validation/escalation logic.
  - STUB:  the decomposer returns fixed sample modules (TEST DATA) for every description.
  - STUB:  the router's "tools" return hard-coded code strings; no AI model is called yet.
"""
import copy
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

# orchestrator/ lives beside backend/, so put the repo root on the import path.
# Revisit when deploying: the deployed service must include the orchestrator/ folder.
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from orchestrator import router  # noqa: E402  (must come after the sys.path change)

from models import Project, Task  # noqa: E402

logger = logging.getLogger(__name__)

SAMPLE_SUMMARY = (
    "SAMPLE PLAN (test data): the decomposer is not implemented yet, so every project gets the same "
    "sample modules. Tool results come from the orchestrator's router using simulated tools."
)

# The router reports "passed"/"failed"; the API and database use the shared status vocabulary.
ROUTER_STATUS_TO_TASK_STATUS = {"passed": "completed", "failed": "failed"}


def decompose(description: str, optimization: str) -> list[dict]:
    """STUB decomposer. Returns the router's own sample modules (TEST DATA), ignoring the inputs."""
    return copy.deepcopy(router.modules)


def run_pipeline(project_id: UUID, session_factory) -> None:
    """Process one project in the background. Never raises: failures mark the project as failed."""
    db = session_factory()
    try:
        project = db.get(Project, project_id)
        if project is None:
            logger.warning("Pipeline started for missing project %s", project_id)
            return

        modules = decompose(project.description, project.optimization)
        project.status = "running"
        project.summary = SAMPLE_SUMMARY
        project.tasks = [
            Task(id=uuid4(), position=position, name=module["name"], description=module.get("description"))
            for position, module in enumerate(modules, start=1)
        ]
        _touch(project)
        db.commit()

        delay = float(os.getenv("DEMO_STEP_DELAY_SECONDS", "1"))  # DEMO ONLY: makes progress visible in the UI
        for task, module in zip(project.tasks, modules):
            task.status = "running"
            _touch(project)
            db.commit()
            time.sleep(delay)

            result = router.route(module)
            task.tool = result["tool_used"]
            task.attempts = result["tries"]
            task.status = ROUTER_STATUS_TO_TASK_STATUS[result["status"]]
            task.output = result.get("code")
            _touch(project)
            db.commit()

        project.status = "completed"
        _touch(project)
        db.commit()
    except Exception:
        # Full details go to the server log only; the API just reports status "failed".
        logger.exception("Pipeline failed for project %s", project_id)
        db.rollback()
        _mark_failed(db, project_id)
    finally:
        db.close()


def get_pipeline():
    """Dependency so tests can swap the pipeline out."""
    return run_pipeline


def _touch(project: Project) -> None:
    project.updated_at = datetime.now(timezone.utc)


def _mark_failed(db, project_id: UUID) -> None:
    try:
        project = db.get(Project, project_id)
        if project is not None:
            project.status = "failed"
            _touch(project)
            db.commit()
    except Exception:
        logger.exception("Could not mark project %s as failed", project_id)
        db.rollback()
