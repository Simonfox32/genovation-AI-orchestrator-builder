"""Tests for the agents' tools and done-checks (orchestrator/agent.py). Real files and real pytest; no Gemini."""
import shutil

import pytest

import orchestration  # noqa: F401  (puts the repo root on the import path)
from orchestrator import agent


@pytest.fixture
def workspace(tmp_path):
    ws = tmp_path / "workspace"
    shutil.copytree(agent.TEMPLATE, ws, ignore=shutil.ignore_patterns("__pycache__"))
    return ws


def test_builder_may_not_write_tests_and_tester_may_not_write_the_app(workspace):
    assert agent.make_tools(workspace, "builder")["write_file"]("tests/x.py", "x = 1").startswith("REFUSED")
    assert agent.make_tools(workspace, "tester")["write_file"]("app/main.py", "x = 1").startswith("REFUSED")
    assert agent.make_tools(workspace, "tester")["write_file"]("tests/test_app.py", "x = 1").startswith("Wrote")


def test_paths_outside_the_workspace_are_refused(workspace):
    with pytest.raises(ValueError):
        agent.make_tools(workspace)["read_file"]("../../../etc/hosts")


@pytest.mark.parametrize("code", [
    "import os\nprint(os.environ)",
    "import subprocess",
    "secret = open('/etc/hosts').read()",
    "__import__('o' + 's')",
    "data = eval('1 + 1')",
])
def test_guard_refuses_file_process_and_environment_code(workspace, code):
    assert agent.make_tools(workspace)["write_file"]("app/main.py", code).startswith("REFUSED")


def test_guard_allows_ordinary_store_code(workspace):
    code = 'ORDERS = {}\n\ndef order_retrieval(order_id):\n    return ORDERS[order_id]\n# POST /orders/{id}/process\n'

    assert agent.make_tools(workspace)["write_file"]("app/main.py", code).startswith("Wrote")


def remove_builtin_tests(workspace):
    for test in (workspace / "tests").glob("test_*.py"):
        test.unlink()


def test_tester_is_not_done_without_tests(workspace):
    remove_builtin_tests(workspace)

    assert agent.tester_done(agent.make_tools(workspace, "tester"))[0] is False


def test_tester_is_not_done_when_tests_check_nothing(workspace):
    remove_builtin_tests(workspace)
    tools = agent.make_tools(workspace, "tester")
    tools["write_file"]("tests/test_app.py", "".join(f"def test_{i}():\n    assert True\n" for i in range(3)))

    done, _, feedback = agent.tester_done(tools)

    assert done is False
    assert "already passes" in feedback


def test_tester_is_not_done_when_tests_do_not_load(workspace):
    remove_builtin_tests(workspace)
    tools = agent.make_tools(workspace, "tester")
    tools["write_file"]("tests/test_app.py", "from app.main import Order\n\ndef test_a():\n    pass\n")

    assert agent.tester_done(tools)[0] is False


def test_tester_is_done_with_real_tests_that_fail_before_the_app_exists(workspace):
    remove_builtin_tests(workspace)
    tools = agent.make_tools(workspace, "tester")
    tests = ("from fastapi.testclient import TestClient\nfrom app.main import app\n\nclient = TestClient(app)\n"
             + "".join(f"\n\ndef test_{i}():\n    assert client.get('/orders').status_code == 200\n" for i in range(3)))
    tools["write_file"]("tests/test_app.py", tests)

    assert agent.tester_done(tools)[0] is True


def test_builder_is_done_only_when_every_test_passes(workspace):
    tools = agent.make_tools(workspace, "builder")

    done, output, feedback = agent.builder_done(tools)

    assert done is False
    assert "failed" in output
    assert feedback.startswith("Not done")
