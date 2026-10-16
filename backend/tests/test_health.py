from main import app
from fastapi.testclient import TestClient

client = TestClient(app)

def test_health_returns_ok():
    response = client.get("/health")
    
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

def test_unknown_path_returns_404():
    response = client.get("/fake_path")
    assert response.status_code == 404
    
def test_good_description_returns_project_201():
    
    payload = {
        "description": "This is a test description"
    }
    
    response = client.post("/projects", json=payload)
    
    assert response.status_code == 201

def test_bad_description_returns_project_422():
    
    payload = {
            "description": ""
        }
        
    response = client.post("/projects", json=payload)
        
    assert response.status_code == 422