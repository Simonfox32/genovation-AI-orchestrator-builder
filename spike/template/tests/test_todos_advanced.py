"""Harder requirements, to see whether the agent can iterate from failing tests."""
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def create(title, **extra):
    return client.post("/todos", json={"title": title, **extra})


def test_titles_are_trimmed_and_limited_to_100_characters():
    assert create("   Plan trip   ").json()["title"] == "Plan trip"
    assert create("   ").status_code == 422          # only spaces counts as empty
    assert create("x" * 101).status_code == 422


def test_duplicate_open_title_is_rejected_with_409_case_insensitive():
    assert create("Call Mom").status_code == 201
    response = create("call mom")

    assert response.status_code == 409
    assert response.json()["detail"] == "A todo with this title is already open"


def test_duplicate_title_allowed_once_the_first_is_done():
    first = create("Water plants").json()
    client.patch(f"/todos/{first['id']}", json={"done": True})

    assert create("Water plants").status_code == 201


def test_priority_defaults_to_medium_and_must_be_valid():
    assert create("Default priority").json()["priority"] == "medium"
    assert create("Urgent thing", priority="high").json()["priority"] == "high"
    assert create("Bad priority", priority="urgent").status_code == 422


def test_filter_by_done_and_priority():
    low = create("Low one", priority="low").json()
    low = client.patch(f"/todos/{low['id']}", json={"done": True}).json()

    done_low = client.get("/todos", params={"done": "true", "priority": "low"}).json()

    assert low in done_low
    assert all(t["done"] is True and t["priority"] == "low" for t in done_low)


def test_list_is_sorted_high_priority_first_then_oldest_first():
    listed = client.get("/todos").json()
    order = {"high": 0, "medium": 1, "low": 2}
    keys = [(order[t["priority"]], t["id"]) for t in listed]

    assert keys == sorted(keys)


def test_pagination_with_limit_and_offset():
    for i in range(5):
        create(f"Page item {i}")
    everything = client.get("/todos").json()

    page = client.get("/todos", params={"limit": 2, "offset": 1}).json()

    assert page == everything[1:3]
    assert client.get("/todos", params={"limit": 0}).status_code == 422


def test_stats_endpoint():
    stats = client.get("/todos/stats").json()
    everything = client.get("/todos").json()

    assert stats["total"] == len(everything)
    assert stats["done"] == sum(t["done"] for t in everything)
    assert stats["open_by_priority"] == {
        p: sum(1 for t in everything if not t["done"] and t["priority"] == p) for p in ("high", "medium", "low")
    }
