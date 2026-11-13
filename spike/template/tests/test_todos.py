"""Hand-written acceptance tests for the todo API. The agent may read these but never edit them."""
from fastapi.testclient import TestClient

from itertools import count

from app.main import app

client = TestClient(app)
_unique = count(1)


def create(title=None):
    title = title or f"Task {next(_unique)}"  # unique, so the duplicate-title rule never interferes
    response = client.post("/todos", json={"title": title})
    assert response.status_code == 201
    return response.json()


def test_create_todo_returns_it_with_an_id_and_not_done():
    todo = create("Buy milk")

    assert isinstance(todo["id"], int)
    assert todo["title"] == "Buy milk"
    assert todo["done"] is False


def test_ids_are_unique():
    assert create()["id"] != create()["id"]


def test_list_includes_created_todos():
    todo = create("Walk the dog")

    listed = client.get("/todos").json()

    assert todo in listed


def test_get_one_todo():
    todo = create("Read a book")

    assert client.get(f"/todos/{todo['id']}").json() == todo


def test_mark_todo_done():
    todo = create()

    response = client.patch(f"/todos/{todo['id']}", json={"done": True})

    assert response.status_code == 200
    assert response.json()["done"] is True
    assert client.get(f"/todos/{todo['id']}").json()["done"] is True


def test_delete_todo_then_it_is_gone():
    todo = create()

    assert client.delete(f"/todos/{todo['id']}").status_code == 204
    assert client.get(f"/todos/{todo['id']}").status_code == 404


def test_missing_todo_returns_404():
    assert client.get("/todos/999999").status_code == 404
    assert client.patch("/todos/999999", json={"done": True}).status_code == 404
    assert client.delete("/todos/999999").status_code == 404


def test_empty_title_is_rejected():
    assert client.post("/todos", json={"title": ""}).status_code == 422
    assert client.post("/todos", json={}).status_code == 422
