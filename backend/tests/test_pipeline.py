"""Tests for the background pipeline: stub decomposer -> Anula's router -> stored tasks."""
import importlib
import sys

from fastapi.testclient import TestClient

import orchestration
from main import app

client = TestClient(app)


def submit(description="Build me a workout tracker"):
    response = client.post("/projects", json={"description": description})
    assert response.status_code == 201
    return response.json()


def test_post_returns_pending_before_processing():
    created = submit()

    assert created["status"] == "pending"
    assert created["tasks"] == []


def test_pipeline_completes_project_and_stores_router_results():
    created = submit()

    # TestClient runs background tasks before returning, so processing has finished by now.
    project = client.get(f"/projects/{created['id']}").json()
    tasks = project["tasks"]

    assert project["status"] == "completed"
    assert "test data" in project["summary"].lower()
    assert [task["position"] for task in tasks] == [1, 2]

    add_task, multiply_task = tasks
    assert add_task["name"] == "add function"
    assert add_task["status"] == "completed"
    assert add_task["tool"] == "strong_tool"
    assert add_task["attempts"] == 3
    assert "return a + b" in add_task["output"]

    assert multiply_task["name"] == "multiply function"
    assert multiply_task["status"] == "failed"
    assert multiply_task["tool"] is None
    assert multiply_task["attempts"] == 3
    assert multiply_task["output"] is None


def test_pipeline_updates_updated_at():
    created = submit()

    project = client.get(f"/projects/{created['id']}").json()

    assert project["created_at"] == created["created_at"]
    assert project["updated_at"] > created["updated_at"]


def test_pipeline_crash_marks_project_failed_without_leaking_details(monkeypatch):
    def broken_route(module):
        raise RuntimeError("internal detail: password=hunter2")

    monkeypatch.setattr(orchestration.router, "route", broken_route)

    created = submit()
    response = client.get(f"/projects/{created['id']}")

    assert response.json()["status"] == "failed"
    assert "hunter2" not in response.text
    assert "RuntimeError" not in response.text


def test_unknown_router_status_marks_project_failed(monkeypatch):
    monkeypatch.setattr(
        orchestration.router, "route",
        lambda module: {"name": module["name"], "tool_used": "x", "tries": 1, "status": "maybe"},
    )

    created = submit()

    assert client.get(f"/projects/{created['id']}").json()["status"] == "failed"


def test_importing_router_has_no_side_effects(capsys):
    sys.modules.pop("orchestrator.router", None)
    importlib.import_module("orchestrator.router")

    assert capsys.readouterr().out == ""
