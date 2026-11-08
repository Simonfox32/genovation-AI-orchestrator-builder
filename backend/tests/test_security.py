"""Tests for protecting the public API: the status API key and the project-creation rate limit."""
import time

from fastapi.testclient import TestClient

from main import app
from models import Project
from security import RateLimiter, project_creation_limiter

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


# ---- Rate limit on POST /projects ----

def test_eleventh_project_in_a_minute_returns_429(enforce_rate_limit, no_pipeline, db_session):
    for _ in range(project_creation_limiter.limit):
        create_project()

    response = client.post("/projects", json={"description": "One too many"})

    assert response.status_code == 429
    assert response.json() == {"detail": "Too many projects created; try again in a minute"}
    # The rejected request never reached the endpoint, so no extra project was saved.
    assert db_session.query(Project).count() == project_creation_limiter.limit


def test_rate_limiter_allows_again_after_the_window_passes():
    limiter = RateLimiter(limit=2, window_seconds=0.2)

    assert limiter.allow("client-a")
    assert limiter.allow("client-a")
    assert not limiter.allow("client-a")

    time.sleep(0.25)  # let the earlier calls fall out of the window

    assert limiter.allow("client-a")


def test_rate_limiter_counts_each_client_separately():
    limiter = RateLimiter(limit=1)

    assert limiter.allow("client-a")
    assert not limiter.allow("client-a")
    assert limiter.allow("client-b")
