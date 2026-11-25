"""Safety for the public site: database connections, the build queue, the rate limit, and where code runs."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

import conftest
import main
import orchestration
import security
from main import app
from models import Project
from security import project_creation_limiter

client = TestClient(app)
# What Railway runs: uvicorn trusting proxy headers from anywhere (--forwarded-allow-ips="*").
proxied = TestClient(ProxyHeadersMiddleware(app, trusted_hosts="*"))


def create(via=None, **headers):
    return (via or client).post("/projects", json={"description": "A store"}, headers=headers)


# ---- P1: a build never holds a database connection, and the queue has a limit ----

def test_a_build_never_holds_the_requests_database_connection():
    # Nov 23 audit: each queued or running build kept its request's connection, and at 15 the dashboard froze.
    seen = []
    app.dependency_overrides[orchestration.get_pipeline] = lambda: (
        lambda project_id, session_factory: seen.append(conftest.test_engine.pool.checkedout()))
    try:
        response = create()
    finally:
        app.dependency_overrides.pop(orchestration.get_pipeline, None)

    assert response.status_code == 201
    assert response.json()["status"] == "pending" and response.json()["description"] == "A store"
    assert seen == [0]


def add_projects(db_session, count, status="pending", minutes_ago=0):
    when = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    for _ in range(count):
        db_session.add(Project(id=uuid4(), description="queued", status=status, optimization="balanced",
                               created_at=when, updated_at=when))
    db_session.commit()


def test_a_full_queue_refuses_new_builds(no_pipeline, db_session, monkeypatch):
    monkeypatch.setenv("MAX_ACTIVE_BUILDS", "3")
    add_projects(db_session, 3)

    response = create()

    assert response.status_code == 503 and "busy" in response.json()["detail"]
    assert db_session.query(Project).count() == 3


def test_running_builds_count_toward_the_queue(no_pipeline, db_session, monkeypatch):
    monkeypatch.setenv("MAX_ACTIVE_BUILDS", "2")
    add_projects(db_session, 1, status="running")
    add_projects(db_session, 1, status="pending")

    assert create().status_code == 503


@pytest.mark.parametrize("status, minutes_ago", [("completed", 0), ("failed", 0), ("pending", 90), ("running", 90)])
def test_finished_and_stranded_builds_do_not_block_the_queue(no_pipeline, db_session, monkeypatch, status, minutes_ago):
    monkeypatch.setenv("MAX_ACTIVE_BUILDS", "2")
    add_projects(db_session, 2, status=status, minutes_ago=minutes_ago)

    assert create().status_code == 201


def test_there_is_always_a_queue_limit(monkeypatch):
    monkeypatch.delenv("MAX_ACTIVE_BUILDS", raising=False)

    assert main.max_active_builds() == main.DEFAULT_MAX_ACTIVE_BUILDS == 20


# ---- P2: the rate limit counts real clients, not a header anyone can fake ----

def test_a_fake_forwarded_for_header_cannot_dodge_the_rate_limit(enforce_rate_limit, no_pipeline):
    codes = [create(proxied, **{"X-Real-IP": "198.51.100.7", "X-Forwarded-For": f"203.0.113.{n}"}).status_code
             for n in range(project_creation_limiter.limit + 1)]

    assert codes == [201] * project_creation_limiter.limit + [429]


def test_each_real_client_has_its_own_limit(enforce_rate_limit, no_pipeline):
    for _ in range(project_creation_limiter.limit):
        create(proxied, **{"X-Real-IP": "198.51.100.7"})

    assert create(proxied, **{"X-Real-IP": "198.51.100.7"}).status_code == 429
    assert create(proxied, **{"X-Real-IP": "198.51.100.8"}).status_code == 201


def test_invalid_requests_still_count_toward_the_limit(enforce_rate_limit, no_pipeline):
    # Lets us check the live limit with requests that create nothing and cost nothing.
    codes = [client.post("/projects", json={}).status_code for _ in range(project_creation_limiter.limit + 1)]

    assert codes == [422] * project_creation_limiter.limit + [429]


def request_from(address, headers=()):
    return Request({"type": "http", "headers": [(k.lower().encode(), v.encode()) for k, v in headers],
                    "client": (address, 1234)})


def test_the_proxy_header_names_the_client(monkeypatch):
    monkeypatch.delenv("CLIENT_IP_HEADER", raising=False)

    assert security.client_address(request_from("10.0.0.1", [("X-Real-IP", " 198.51.100.7 ")])) == "198.51.100.7"
    assert security.client_address(request_from("10.0.0.1")) == "10.0.0.1"


def test_the_proxy_header_can_be_changed_or_switched_off(monkeypatch):
    request = request_from("10.0.0.1", [("X-Real-IP", "198.51.100.7"), ("CF-Connecting-IP", "192.0.2.4")])

    monkeypatch.setenv("CLIENT_IP_HEADER", "CF-Connecting-IP")
    assert security.client_address(request) == "192.0.2.4"
    monkeypatch.setenv("CLIENT_IP_HEADER", "")
    assert security.client_address(request) == "10.0.0.1"


# ---- P3: AI-written code only runs where it was chosen to run ----

@pytest.mark.parametrize("value", [None, "", "docker", "Vercel", "vercel ", "sandbox"])
def test_unsafe_executor_settings_are_refused(monkeypatch, tmp_path, value):
    if value is None:
        monkeypatch.delenv("EXECUTOR", raising=False)
    else:
        monkeypatch.setenv("EXECUTOR", value)

    with pytest.raises(orchestration.BuildRefused) as error:
        orchestration.make_executor(tmp_path)

    assert "EXECUTOR" in error.value.public


def test_running_on_this_machine_must_be_chosen_on_purpose(monkeypatch, tmp_path):
    monkeypatch.setenv("EXECUTOR", "local")

    assert isinstance(orchestration.make_executor(tmp_path), orchestration.LocalExecutor)


def test_a_build_without_a_safe_executor_fails_clearly_and_runs_nothing(monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR", "agent-team")
    monkeypatch.delenv("EXECUTOR", raising=False)
    calls = []
    monkeypatch.setattr(orchestration.agent, "run_agent", lambda *args, **kwargs: calls.append(1))

    response = create()
    project = client.get(f"/projects/{response.json()['id']}").json()

    assert calls == []
    assert project["status"] == "failed"
    assert "EXECUTOR" in project["summary"]
