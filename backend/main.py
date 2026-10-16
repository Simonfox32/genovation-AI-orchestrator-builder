from fastapi import FastAPI
from typing import Annotated
from pydantic import BaseModel, StringConstraints
from uuid import uuid4
from datetime import datetime, timezone


projects = {}
app = FastAPI()

class ProjectCreate(BaseModel):
    description: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
    
@app.get("/")

def read_root():
    return{"message": "Hello World!"}

@app.get("/health")
def health_check():
    return {"status": "ok"}

@app.post("/projects", status_code=201)

def create_project(project : ProjectCreate):
    project_id = str(uuid4())
    
    now = datetime.now(timezone.utc)
    new_project = {
        "id": project_id,
        "description": project.description,
        "status": "pending",
        "created_at": now,
        "updated_at": now
    }
    
    projects[project_id] = new_project
    return new_project  