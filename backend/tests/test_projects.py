from main import app
from models import Project
from fastapi.testclient import TestClient
from uuid import UUID, uuid4
from datetime import datetime



client = TestClient(app)

def test_good_description_returns_project_201():
    
    payload = {
        "description": " This is a test description"
    }
    
    response = client.post("/projects", json=payload)
    
    assert response.status_code == 201
    assert response.json()["description"] == "This is a test description"
    assert response.json()["created_at"] == response.json()["updated_at"]
    assert response.json()["status"] == "pending"
    assert isinstance(response.json()["id"], str)
    assert UUID(response.json()['id'])

def test_bad_description_returns_project_422():
    
    payload = {
            "description": ""
        }
        
    response = client.post("/projects", json=payload)
    
    assert response.status_code == 422
    

def test_stripped_bad_description_returns_project_422():
    
    payload = {
            "description": "     "
        }
        
    response = client.post("/projects", json=payload)

    assert response.status_code == 422


def create_test_project():
    response = client.post("/projects", json={"description": "Test project"})
    assert response.status_code == 201
    return response.json()


def test_get_project_returns_created_project():
    created = create_test_project()

    response = client.get(f"/projects/{created['id']}")

    assert response.status_code == 200
    assert response.json() == created


def test_get_missing_project_returns_404():
    response = client.get(f"/projects/{uuid4()}")

    assert response.status_code == 404
    assert response.json() == {"detail": "Project not found"}


def test_get_malformed_id_returns_422():
    response = client.get("/projects/not-a-uuid")

    assert response.status_code == 422


def test_update_status_changes_status_and_updated_at():
    created = create_test_project()

    response = client.patch(f"/projects/{created['id']}/status", json={"status": "running"})
    body = response.json()

    assert response.status_code == 200
    assert body["status"] == "running"
    assert body["created_at"] == created["created_at"]
    assert datetime.fromisoformat(body["updated_at"]) >= datetime.fromisoformat(created["updated_at"])


def test_update_status_persists():
    created = create_test_project()
    client.patch(f"/projects/{created['id']}/status", json={"status": "completed"})

    response = client.get(f"/projects/{created['id']}")

    assert response.json()["status"] == "completed"


def test_update_status_invalid_value_returns_422():
    created = create_test_project()

    response = client.patch(f"/projects/{created['id']}/status", json={"status": "banana"})

    assert response.status_code == 422


def test_update_status_missing_field_returns_422():
    created = create_test_project()

    response = client.patch(f"/projects/{created['id']}/status", json={})

    assert response.status_code == 422


def test_update_status_missing_project_returns_404():
    response = client.patch(f"/projects/{uuid4()}/status", json={"status": "running"})

    assert response.status_code == 404


def test_created_project_is_stored_in_database(db_session):
    response = client.post("/projects", json={"description": "  Persist me  "})
    project_id = UUID(response.json()["id"])

    # Read the row straight from Postgres with a separate session, bypassing the API.
    stored = db_session.get(Project, project_id)

    assert stored is not None
    assert stored.description == "Persist me"
    assert stored.status == "pending"
    assert stored.created_at == stored.updated_at

def test_missing_description_returns_422():
    response = client.post("/projects", json={})

    assert response.status_code == 422

def test_invalid_status_does_not_change_project():
    created = create_test_project()
    project_id = created["id"]

    response = client.patch(
        f"/projects/{project_id}/status",
        json={"status": "banana"},
    )

    assert response.status_code == 422

    saved = client.get(f"/projects/{project_id}")

    assert saved.status_code == 200
    assert saved.json() == created

def test_updating_one_project_does_not_change_another():
    first = create_test_project()
    second = create_test_project()

    assert first["id"] != second["id"]

    response = client.patch(
        f"/projects/{first['id']}/status",
        json={"status": "running"},
    )
    assert response.status_code == 200

    updated = client.get(f"/projects/{first['id']}")
    unchanged = client.get(f"/projects/{second['id']}")

    assert updated.status_code == 200
    assert updated.json()["status"] == "running"
    assert unchanged.status_code == 200
    assert unchanged.json() == second

def test_invalid_description_types_return_422():
    for description in [123, None]:
        response = client.post(
            "/projects",
            json={"description": description},
        )
        assert response.status_code == 422


def test_update_status_malformed_id_returns_422():
    response = client.patch(
        "/projects/not-a-uuid/status",
        json={"status": "running"},
    )
    assert response.status_code == 422