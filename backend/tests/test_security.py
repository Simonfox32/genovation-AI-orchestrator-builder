"""Tests for the API key that protects PATCH /projects/{id}/status."""
from fastapi.testclient import TestClient

from main import app

client = TestClient(app)


def create_project():
    """Arrange step shared by every test: make a project to update."""
    response = client.post("/projects", json={"description": "Security test project"})
    assert response.status_code == 201
    return response.json()["id"]


def update_status(project_id, headers=None):
    return client.patch(
        f"/projects/{project_id}/status",
        json={"status": "running"},
        headers=headers or {},
    )


def test_status_update_without_key_returns_401(enforce_api_key, no_pipeline):
    project_id = create_project()

    response = update_status(project_id)

    assert response.status_code == 401
    assert client.get(f"/projects/{project_id}").json()["status"] == "pending"


def test_status_update_with_wrong_key_returns_401(enforce_api_key, no_pipeline):
    project_id = create_project()

    response = update_status(project_id, headers={"X-API-Key": "wrong-key"})

    assert response.status_code == 401
    assert client.get(f"/projects/{project_id}").json()["status"] == "pending"


def test_status_update_with_correct_key_succeeds(enforce_api_key, no_pipeline):
    project_id = create_project()

    response = update_status(project_id, headers={"X-API-Key": "test-key"})

    assert response.status_code == 200
    assert response.json()["status"] == "running"
    assert client.get(f"/projects/{project_id}").json()["status"] == "running"


def test_status_update_rejected_when_server_has_no_key(enforce_api_key, no_pipeline, monkeypatch):
    # The fixture sets STATUS_API_KEY; remove it to simulate a server that was never given a key.
    monkeypatch.delenv("STATUS_API_KEY")
    project_id = create_project()

    response = update_status(project_id, headers={"X-API-Key": "test-key"})

    assert response.status_code == 401
    assert client.get(f"/projects/{project_id}").json()["status"] == "pending"
