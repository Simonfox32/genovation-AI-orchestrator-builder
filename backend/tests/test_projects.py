from main import app
from fastapi.testclient import TestClient

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

def test_bad_description_returns_project_422():
    
    payload = {
            "description": ""
        }
        
    response = client.post("/projects", json=payload)
        
    assert response.status_code == 422