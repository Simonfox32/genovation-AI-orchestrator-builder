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