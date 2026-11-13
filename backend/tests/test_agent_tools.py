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


def write_tester_files(workspace, requirements, tests):
    tools = agent.make_tools(workspace, "tester")
    tools["write_file"]("tests/REQUIREMENTS.md", requirements)
    tools["write_file"]("tests/test_app.py", tests)
    return tools


REQUIREMENTS = "1. List orders\n2. Create orders\n3. Unknown ids return 404\n"
REAL_TESTS = ("from fastapi.testclient import TestClient\nfrom app.main import app\n\nclient = TestClient(app)\n"
              + "".join(f"\n\ndef test_r{i}_orders():\n    assert client.get('/orders').status_code == 200\n"
                        for i in (1, 2, 3)))


def test_tester_is_not_done_when_tests_check_nothing(workspace):
    remove_builtin_tests(workspace)
    trivial = "".join(f"def test_r{i}_x():\n    assert True\n" for i in (1, 2, 3))
    tools = write_tester_files(workspace, REQUIREMENTS, trivial)

    done, _, feedback = agent.tester_done(tools)

    assert done is False
    assert "already passes" in feedback


def test_tester_is_not_done_when_tests_do_not_load(workspace):
    remove_builtin_tests(workspace)
    tools = write_tester_files(workspace, REQUIREMENTS, "from app.main import Order\n\ndef test_r1_a():\n    pass\n")

    assert agent.tester_done(tools)[0] is False


def test_tester_is_not_done_without_a_requirements_list(workspace):
    remove_builtin_tests(workspace)
    tools = agent.make_tools(workspace, "tester")
    tools["write_file"]("tests/test_app.py", REAL_TESTS)

    done, _, feedback = agent.tester_done(tools)

    assert done is False
    assert "REQUIREMENTS.md" in feedback


def test_tester_is_not_done_when_a_requirement_has_no_test(workspace):
    remove_builtin_tests(workspace)
    tools = write_tester_files(workspace, REQUIREMENTS + "4. Orders reduce stock\n", REAL_TESTS)

    done, _, feedback = agent.tester_done(tools)

    assert done is False
    assert "requirement(s) 4 have no test" in feedback


def test_tester_is_done_when_every_requirement_has_a_failing_test(workspace):
    remove_builtin_tests(workspace)
    tools = write_tester_files(workspace, REQUIREMENTS, REAL_TESTS)

    assert agent.tester_done(tools)[0] is True


@pytest.mark.parametrize("requirements, tests, missing", [
    ("1. a\n2. b\n", "def test_r1(): ...\ndef test_r2(): ...", []),
    ("1. a\n10. b\n", "def test_r1_x(): ...", ["10"]),                       # r1 doesn't count as r10
    ("1) a\n 2. b\n", "def test_R1_x(): ...\ndef test_r2_y(): ...", []),    # "1)" style, capital R
    ("1. a\n2. b\n", "def test_r1_x(): ...\n# this also covers r2", ["2"]),   # comments don't count
    ("1. a\n## Assumptions\n1. we assumed cents\n2. no auth\n", "def test_r1_x(): ...", []),  # assumptions ignored
])
def test_uncovered_requirements(requirements, tests, missing):
    assert agent.uncovered_requirements(requirements, tests) == missing


def test_one_giant_test_for_many_requirements_is_rejected(workspace):
    remove_builtin_tests(workspace)
    giant = ("from fastapi.testclient import TestClient\nfrom app.main import app\n\nclient = TestClient(app)\n\n\n"
             "def test_r1_r2_r3_everything():\n    assert client.get('/orders').status_code == 200\n\n\n"
             "def test_r1_list():\n    assert client.get('/orders').status_code == 200\n\n\n"
             "def test_r2_create():\n    assert client.post('/orders').status_code == 201\n")
    tools = write_tester_files(workspace, REQUIREMENTS, giant)

    done, _, feedback = agent.tester_done(tools)

    assert done is False
    assert "split test_r1_r2_r3_everything" in feedback
    assert agent.overloaded_tests("def test_r1_r12_ok(): ...") == []  # 2 requirements per test is allowed


def test_optimization_picks_the_model_tier():
    assert agent.model_for("speed") == agent.CHEAP_MODEL
    assert agent.model_for("balanced") == agent.MEDIUM_MODEL
    assert agent.model_for("quality") == agent.STRONG_MODEL
    assert agent.model_for("something else") == agent.MODEL


def test_overloaded_model_falls_back_to_the_medium_model(workspace, monkeypatch):
    """The strong model fails: the agent switches to the fallback right away and carries on."""
    from types import SimpleNamespace
    from google.genai import types

    asked = []

    class FakeModels:
        def generate_content(self, model, contents, config):
            asked.append(model)
            if model == agent.STRONG_MODEL:
                raise RuntimeError("503 UNAVAILABLE")
            content = types.Content(role="model", parts=[types.Part(text="All done.")])
            return SimpleNamespace(candidates=[SimpleNamespace(content=content)], function_calls=None, text="All done.")

    monkeypatch.setattr(agent.genai, "Client", lambda **settings: SimpleNamespace(models=FakeModels()))
    monkeypatch.setattr(agent.time, "sleep", lambda seconds: None)

    outcome = agent.run_agent(workspace, model=agent.STRONG_MODEL, max_steps=1)

    assert asked[:2] == [agent.STRONG_MODEL, agent.FALLBACK_MODEL]
    assert outcome["model"] == agent.FALLBACK_MODEL


def test_builder_is_done_only_when_every_test_passes(workspace):
    tools = agent.make_tools(workspace, "builder")

    done, output, feedback = agent.builder_done(tools)

    assert done is False
    assert "failed" in output
    assert feedback.startswith("Not done")


def test_rate_limit_waits_longer_and_keeps_the_model(workspace, monkeypatch):
    """429 = the free tier's per-minute limit: wait 10s, 20s... and stay on the same model (it isn't broken)."""
    from types import SimpleNamespace
    from google.genai import types

    asked, waits = [], []

    class FakeModels:
        def generate_content(self, model, contents, config):
            asked.append(model)
            if len(asked) <= 2:
                raise RuntimeError("429 RESOURCE_EXHAUSTED. You exceeded your current quota")
            content = types.Content(role="model", parts=[types.Part(text="All done.")])
            return SimpleNamespace(candidates=[SimpleNamespace(content=content)], function_calls=None, text="All done.")

    monkeypatch.setattr(agent.genai, "Client", lambda **settings: SimpleNamespace(models=FakeModels()))
    monkeypatch.setattr(agent.time, "sleep", waits.append)

    agent.run_agent(workspace, model=agent.MEDIUM_MODEL, max_steps=1)

    assert waits[:2] == [10, 20]
    assert asked[:3] == [agent.MEDIUM_MODEL] * 3
