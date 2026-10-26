import os
from datetime import datetime, timezone
from enum import Enum
from typing import Annotated
from uuid import UUID, uuid4

from models import Project

from sqlalchemy.orm import Session
from fastapi import BackgroundTasks, FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import AfterValidator, BaseModel, StringConstraints
from database import get_db, get_session_factory
from orchestration import get_pipeline

app = FastAPI(title="AI Build Router API")

# Browsers block a page on one origin (e.g. the frontend on :5173) from reading responses from
# another (this API on :8000) unless the API explicitly allows that origin.
CORS_ORIGINS = [
    origin.strip()
    for origin in os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["GET", "POST", "PATCH"],
    allow_headers=["Content-Type"],
)


class ProjectStatus(str, Enum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"


class TaskStatus(str, Enum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"


class Optimization(str, Enum):
    balanced = "balanced"
    speed = "speed"
    quality = "quality"


# Postgres returns timestamps in its session time zone (e.g. -07:00 locally); the API always sends UTC.
UTCDatetime = Annotated[datetime, AfterValidator(lambda dt: dt.astimezone(timezone.utc))]


# What the client may send when creating a project.
class ProjectCreate(BaseModel):
    description: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
    optimization: Optimization = Optimization.balanced


# What the client may send when changing a project's status.
class ProjectStatusUpdate(BaseModel):
    status: ProjectStatus


# What the API sends back for one task in a project's plan.
class TaskResponse(BaseModel):
    id: UUID
    position: int
    name: str
    description: str | None
    tool: str | None
    status: TaskStatus
    attempts: int
    output: str | None


# What the API sends back for a project.
class ProjectResponse(BaseModel):
    id: UUID
    description: str
    status: ProjectStatus
    optimization: Optimization
    summary: str | None
    created_at: UTCDatetime
    updated_at: UTCDatetime
    tasks: list[TaskResponse]


NOT_FOUND = {404: {"description": "Project not found"}}


@app.get("/")
def read_root():
    return {"message": "Hello World!"}


@app.get("/health")
def health_check():
    return {"status": "ok"}


@app.post("/projects", status_code=201, response_model=ProjectResponse)
def create_project(
    project: ProjectCreate,
    background_tasks: BackgroundTasks,
    db : Session = Depends(get_db),
    session_factory = Depends(get_session_factory),
    pipeline = Depends(get_pipeline),
):
    project_id = uuid4()
    now = datetime.now(timezone.utc)

    new_project = Project(
        id = project_id,
        description = project.description,
        status = "pending",
        optimization = project.optimization.value,
        created_at = now,
        updated_at = now
    )

    db.add(new_project)
    db.commit()

    # Runs after the 201 response is sent, so the client gets the id immediately and polls GET for progress.
    background_tasks.add_task(pipeline, new_project.id, session_factory)
    return new_project


@app.get("/projects/{project_id}", response_model=ProjectResponse, responses=NOT_FOUND)
def get_project(project_id: UUID, db : Session = Depends(get_db)):
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@app.patch("/projects/{project_id}/status", response_model=ProjectResponse, responses=NOT_FOUND)
def update_project_status(project_id: UUID, update: ProjectStatusUpdate, db : Session = Depends(get_db)):
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    project.status = update.status.value
    project.updated_at = datetime.now(timezone.utc)
    db.commit()
    return project
