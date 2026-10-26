"""Tests for the response contract the frontend relies on: optimization, summary, tasks, UTC, CORS."""
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from main import app
from models import Project, Task

client = TestClient(app)

FRONTEND_ORIGIN = "http://localhost:5173"


def create_project(**extra):
    response = client.post("/projects", json={"description": "Contract test project", **extra})
    assert response.status_code == 201
    return response.json()


def test_optimization_defaults_to_balanced():
    assert create_project()["optimization"] == "balanced"


def test_optimization_is_stored(db_session):
    created = create_project(optimization="quality")

    assert created["optimization"] == "quality"
    assert db_session.get(Project, UUID(created["id"])).optimization == "quality"


def test_invalid_optimization_returns_422():
    response = client.post("/projects", json={"description": "x", "optimization": "fastest"})

    assert response.status_code == 422


def test_new_project_has_no_summary_and_no_tasks():
    created = create_project()

    assert created["summary"] is None
    assert created["tasks"] == []


def test_get_returns_tasks_in_position_order(db_session):
    created = create_project()
    project_id = UUID(created["id"])
    # TEST DATA: inserted directly, out of order, to check the API sorts by position.
    db_session.add_all([
        Task(id=uuid4(), project_id=project_id, position=2, name="Second", tool="medium_tool",
             status="failed", attempts=3),
        Task(id=uuid4(), project_id=project_id, position=1, name="First", tool="strong_tool",
             status="completed", attempts=2, output="def add(a, b):\n    return a + b"),
    ])
    db_session.commit()

    tasks = client.get(f"/projects/{project_id}").json()["tasks"]

    assert [task["name"] for task in tasks] == ["First", "Second"]
    assert tasks[0] == {
        "id": tasks[0]["id"],
        "position": 1,
        "name": "First",
        "description": None,
        "tool": "strong_tool",
        "status": "completed",
        "attempts": 2,
        "output": "def add(a, b):\n    return a + b",
    }


def test_timestamps_are_utc():
    created = create_project()
    fetched = client.get(f"/projects/{created['id']}").json()

    for body in (created, fetched):
        assert body["created_at"].endswith("Z")
        assert body["updated_at"].endswith("Z")


def test_status_update_stores_plain_string(db_session):
    created = create_project()

    client.patch(f"/projects/{created['id']}/status", json={"status": "running"})

    assert db_session.get(Project, UUID(created["id"])).status == "running"


def test_cors_allows_frontend_origin():
    response = client.options(
        "/projects",
        headers={"Origin": FRONTEND_ORIGIN, "Access-Control-Request-Method": "POST"},
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == FRONTEND_ORIGIN


def test_cors_does_not_allow_unknown_origin():
    response = client.get("/health", headers={"Origin": "https://evil.example.com"})

    assert "access-control-allow-origin" not in response.headers
