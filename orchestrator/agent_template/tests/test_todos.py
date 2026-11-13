"""Requirements for the todo API, written as tests. The agent may read these but never change them."""
from itertools import count

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)
numbers = count(1)


def create(title=None, **extra):
    """Create a todo. Without a title, uses a unique one so tests don't clash."""
    return client.post("/todos", json={"title": title or f"Task {next(numbers)}", **extra})


def test_create_returns_the_todo_with_an_id():
    response = create("Buy milk")

    assert response.status_code == 201
    todo = response.json()
    assert isinstance(todo["id"], int)
    assert todo["title"] == "Buy milk"
    assert todo["done"] is False


def test_list_includes_created_todos():
    todo = create().json()

    assert todo in client.get("/todos").json()


def test_get_one_todo():
    todo = create().json()

    assert client.get(f"/todos/{todo['id']}").json() == todo


def test_mark_done():
    todo = create().json()

    response = client.patch(f"/todos/{todo['id']}", json={"done": True})

    assert response.status_code == 200
    assert response.json()["done"] is True


def test_delete_then_gone():
    todo = create().json()

    assert client.delete(f"/todos/{todo['id']}").status_code == 204
    assert client.get(f"/todos/{todo['id']}").status_code == 404


def test_missing_todo_is_404():
    assert client.get("/todos/999999").status_code == 404
    assert client.patch("/todos/999999", json={"done": True}).status_code == 404
    assert client.delete("/todos/999999").status_code == 404


def test_title_is_trimmed_and_cannot_be_empty():
    assert create("   Plan trip   ").json()["title"] == "Plan trip"
    assert create("   ").status_code == 422
    assert client.post("/todos", json={}).status_code == 422


def test_priority_defaults_to_medium_and_must_be_valid():
    assert create().json()["priority"] == "medium"
    assert create(priority="high").json()["priority"] == "high"
    assert create(priority="urgent").status_code == 422


def test_duplicate_open_title_is_rejected():
    assert create("Call Mom").status_code == 201

    response = create("call mom")

    assert response.status_code == 409
    assert response.json()["detail"] == "A todo with this title is already open"


def test_filter_by_done():
    finished = create().json()
    finished = client.patch(f"/todos/{finished['id']}", json={"done": True}).json()
    still_open = create().json()

    done_list = client.get("/todos", params={"done": "true"}).json()

    assert finished in done_list
    assert still_open not in done_list
