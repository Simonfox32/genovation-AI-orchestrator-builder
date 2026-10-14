from datetime import datetime, timezone
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

app = FastAPI(title="AI Build Router API")
projects = {}  # Temporary in-memory storage; replace with PostgreSQL.


class ProjectRequest(BaseModel):
    description: str = Field(min_length=1)


class ProjectStatusUpdate(BaseModel):
    status: str


@app.get("/health")
def health_check():
    return {"status": "ok"}


@app.post("/projects", status_code=201)
def create_project(request: ProjectRequest):
    description = request.description.strip()
    if not description:
        raise HTTPException(status_code=422, detail="Project description cannot be empty")
    project_id = str(uuid4())
    project = {
        "id": project_id,
        "description": description,
        "status": "pending",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    projects[project_id] = project
    return project


@app.get("/projects/{project_id}")
def get_project(project_id: str):
    project = projects.get(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@app.patch("/projects/{project_id}/status")
def update_project_status(project_id: str, request: ProjectStatusUpdate):
    project = projects.get(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    if request.status not in {"pending", "running", "completed", "failed"}:
        raise HTTPException(status_code=422, detail="Invalid project status")
    project["status"] = request.status
    return project
