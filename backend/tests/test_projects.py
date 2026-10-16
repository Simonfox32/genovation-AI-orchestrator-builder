from main import app
from fastapi.testclient import TestClient
from uuid import UUID



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
    