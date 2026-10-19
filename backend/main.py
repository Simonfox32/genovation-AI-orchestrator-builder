from datetime import datetime, timezone
from enum import Enum
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, StringConstraints

projects = {}  # Temporary in-memory storage: project id (UUID) -> project dict
app = FastAPI(title="AI Build Router API")


class ProjectStatus(str, Enum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"


# What the client may send when creating a project.
class ProjectCreate(BaseModel):
    description: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


# What the client may send when changing a project's status.
class ProjectStatusUpdate(BaseModel):
    status: ProjectStatus


# What the API sends back for a project.
class ProjectResponse(BaseModel):
    id: UUID
    description: str
    status: ProjectStatus
    created_at: datetime
    updated_at: datetime


NOT_FOUND = {404: {"description": "Project not found"}}


@app.get("/")
def read_root():
    return {"message": "Hello World!"}


@app.get("/health")
def health_check():
    return {"status": "ok"}


@app.post("/projects", status_code=201, response_model=ProjectResponse)
def create_project(project: ProjectCreate):
    project_id = uuid4()
    now = datetime.now(timezone.utc)

    new_project = {
        "id": project_id,
        "description": project.description,
        "status": ProjectStatus.pending,
        "created_at": now,
        "updated_at": now,
    }

    projects[project_id] = new_project
    return new_project


@app.get("/projects/{project_id}", response_model=ProjectResponse, responses=NOT_FOUND)
def get_project(project_id: UUID):
    project = projects.get(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@app.patch("/projects/{project_id}/status", response_model=ProjectResponse, responses=NOT_FOUND)
def update_project_status(project_id: UUID, update: ProjectStatusUpdate):
    project = projects.get(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    project["status"] = update.status
    project["updated_at"] = datetime.now(timezone.utc)
    return project
