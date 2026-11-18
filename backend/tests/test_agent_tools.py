"""Tests for the agents' tools and done-checks (orchestrator/agent.py). Real files and real pytest; no Gemini."""
import re
import shutil

import pytest

import orchestration  # noqa: F401  (puts the repo root on the import path)
from orchestrator import agent
from orchestrator.executors import LocalExecutor


@pytest.fixture
def workspace(tmp_path):
    ws = tmp_path / "workspace"
    shutil.copytree(agent.TEMPLATE, ws, ignore=shutil.ignore_patterns("__pycache__"))
    return ws


def test_builder_may_not_write_tests_and_tester_may_not_write_the_app(workspace):
    assert agent.make_tools(LocalExecutor(workspace), "builder")["write_file"]("tests/x.py", "x = 1").startswith("REFUSED")
    assert agent.make_tools(LocalExecutor(workspace), "tester")["write_file"]("app/main.py", "x = 1").startswith("REFUSED")
    assert agent.make_tools(LocalExecutor(workspace), "tester")["write_file"]("tests/test_app.py", "x = 1").startswith("Wrote")


def test_builder_cannot_reach_tests_through_an_absolute_path(workspace):
    """The hole found during the executor refactor: an absolute path inside the workspace isn't 'tests/...' as text."""
    target = workspace / "tests" / "test_todos.py"
    original = target.read_text()

    result = agent.make_tools(LocalExecutor(workspace), "builder")["write_file"](str(target), "x = 1")

    assert result.startswith("REFUSED")
    assert target.read_text() == original


@pytest.mark.parametrize("path, inside", [
    ("tests/test_app.py", True), ("./tests/test_app.py", True), ("app/../tests/test_app.py", True),
    ("app/main.py", False), ("testsuite/x.py", False), ("tests/../app/main.py", False),
])
def test_in_folder_decides_from_the_path_text(path, inside):
    assert agent.in_folder(path, "tests") is inside


def test_tools_only_use_the_executor_interface():
    """Any object with the four executor actions works: the tools never touch the disk themselves."""
    class InMemoryExecutor:
        python = "python"

        def __init__(self):
            self.files, self.commands = {}, []

        def list_files(self, folder="."):
            return sorted(self.files)

        def read_file(self, path):
            return self.files[path]

        def write_file(self, path, content):
            self.files[path] = content

        def run(self, command, timeout=60):
            self.commands.append(command)
            return 1, "1 failed in 0.1s"

    executor = InMemoryExecutor()
    tools = agent.make_tools(executor, "builder")

    assert tools["write_file"]("app/main.py", "x = 1").startswith("Wrote")
    assert tools["list_files"]() == "app/main.py"
    assert tools["read_file"]("app/main.py") == "x = 1"
    assert tools["run_tests"]().endswith("1 failed in 0.1s")
    assert executor.commands[0][:3] == ["python", "-m", "pytest"]


def test_paths_outside_the_workspace_are_refused(workspace):
    with pytest.raises(ValueError):
        agent.make_tools(LocalExecutor(workspace))["read_file"]("../../../etc/hosts")


@pytest.mark.parametrize("code", [
    "import os\nprint(os.environ)",
    "import subprocess",
    "secret = open('/etc/hosts').read()",
    "__import__('o' + 's')",
    "data = eval('1 + 1')",
])
def test_guard_refuses_file_process_and_environment_code(workspace, code):
    assert agent.make_tools(LocalExecutor(workspace))["write_file"]("app/main.py", code).startswith("REFUSED")


def test_guard_allows_ordinary_store_code(workspace):
    code = 'ORDERS = {}\n\ndef order_retrieval(order_id):\n    return ORDERS[order_id]\n# POST /orders/{id}/process\n'

    assert agent.make_tools(LocalExecutor(workspace))["write_file"]("app/main.py", code).startswith("Wrote")


def remove_builtin_tests(workspace):
    for test in (workspace / "tests").glob("test_*.py"):
        test.unlink()


def test_tester_is_not_done_without_tests(workspace):
    remove_builtin_tests(workspace)

    assert agent.tester_done(agent.make_tools(LocalExecutor(workspace), "tester"))[0] is False


def write_tester_files(workspace, requirements, tests):
    tools = agent.make_tools(LocalExecutor(workspace), "tester")
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
    tools = agent.make_tools(LocalExecutor(workspace), "tester")
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
    tools = agent.make_tools(LocalExecutor(workspace), "builder")

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


# ---- The database starter template (team mode) ----

@pytest.mark.parametrize("role", ["builder", "tester"])
@pytest.mark.parametrize("path", ["app/db.py", "tests/conftest.py", "./app/db.py", "app/../tests/conftest.py"])
def test_template_plumbing_is_protected_from_both_agents(tmp_path, role, path):
    result = agent.make_tools(LocalExecutor(tmp_path), role)["write_file"](path, "x = 1")

    assert result.startswith("REFUSED") and "setup" in result


def test_database_files_are_hidden_from_the_agents(tmp_path):
    executor = LocalExecutor(tmp_path)
    executor.write_file("app/main.py", "x = 1")
    executor.write_file("app.db", "pretend this is binary")

    assert agent.make_tools(executor)["list_files"]() == "app/main.py"


def test_team_mode_seeding_keeps_the_test_setup_but_no_tests(tmp_path):
    executor = LocalExecutor(tmp_path)

    agent.seed_workspace(executor, include_tests=False, template=agent.TEMPLATE_DB)

    files = executor.list_files()
    assert "tests/conftest.py" in files and "app/db.py" in files and "app/models.py" in files
    assert not [f for f in files if f.startswith("tests/test_")]


def test_database_template_gives_each_test_a_working_client(tmp_path):
    """The template's own plumbing works end to end: the client fixture loads, and the app answers."""
    executor = LocalExecutor(tmp_path)
    agent.seed_workspace(executor, include_tests=False, template=agent.TEMPLATE_DB)
    executor.write_file("tests/test_smoke.py", "def test_unknown_route_is_404(client):\n"
                                               "    assert client.get('/nope').status_code == 404\n")

    exit_code, output = executor.run([executor.python, "-m", "pytest", "-q", "-p", "no:cacheprovider"])

    assert exit_code == 0, output


def test_database_template_explains_how_to_run_itself(tmp_path):
    executor = LocalExecutor(tmp_path)
    agent.seed_workspace(executor, include_tests=False, template=agent.TEMPLATE_DB)

    assert "uvicorn app.main:app" in executor.read_file("README.md")
    assert "sqlalchemy==2.1.6" in executor.read_file("requirements.txt")
    assert agent.make_tools(executor, "builder")["write_file"]("requirements.txt", "requests").startswith("REFUSED")


# ---- The feedback loop (C4): stuck builders, reported tests, reviews ----

def pytest_output(*failing):
    """What pytest -q prints when these tests fail."""
    return "".join(f"FAILED tests/test_app.py::{name} - assert 0\n" for name in failing) + f"{len(failing)} failed"


def fake_gemini(monkeypatch, calls_for_turn):
    """Fake Gemini whose n-th reply asks for the tool calls calls_for_turn(n) returns ([(name, args)]), or says it's
    done when that's empty. Returns the configs it was called with (they show which tools were offered)."""
    from types import SimpleNamespace
    from google.genai import types

    configs = []

    class FakeModels:
        def generate_content(self, model, contents, config):
            configs.append(config)
            calls = calls_for_turn(len(configs))
            if not calls:
                content = types.Content(role="model", parts=[types.Part(text="Done.")])
                return SimpleNamespace(candidates=[SimpleNamespace(content=content)], function_calls=None, text="Done.")
            parts = [types.Part(function_call=types.FunctionCall(id=f"c{i}", name=name, args=args))
                     for i, (name, args) in enumerate(calls)]
            asked = [SimpleNamespace(id=f"c{i}", name=name, args=args) for i, (name, args) in enumerate(calls)]
            return SimpleNamespace(candidates=[SimpleNamespace(content=types.Content(role="model", parts=parts))],
                                   function_calls=asked, text=None)

    monkeypatch.setattr(agent.genai, "Client", lambda **settings: SimpleNamespace(models=FakeModels()))
    return configs


def offered_tools(config):
    return [declaration.name for declaration in config.tools[0].function_declarations]


def test_failing_tests_are_read_from_pytest_output(workspace):
    output = agent.make_tools(LocalExecutor(workspace), "builder")["run_tests"]()  # the starter app fails its tests

    assert agent.failing_tests(output) and all(name.startswith("test_") for name in agent.failing_tests(output))
    assert agent.failing_tests("ERROR tests/test_app.py - SyntaxError\n1 error in 0.1s") == set()  # names no test


def test_progress_watch_catches_a_builder_going_round_in_circles():
    # The 11-18 star wars run: 13 failing, a typo, then the same single test failing run after run.
    watch = agent.ProgressWatch(limit=3)

    assert not watch.record(pytest_output(*[f"test_r{n}_x" for n in range(1, 14)]))
    assert not watch.record("ERROR tests/test_app.py - SyntaxError\n1 error")  # a typo isn't being stuck
    assert not watch.record(pytest_output("test_r10_dispatch"))  # 13 -> 1: progress
    assert not watch.record(pytest_output("test_r10_dispatch"))
    assert not watch.record(pytest_output("test_r10_dispatch"))
    assert watch.record(pytest_output("test_r10_dispatch"))  # 3 runs with no fewer failures: stuck
    assert watch.suspects == {"test_r10_dispatch"}


def test_progress_watch_leaves_an_improving_builder_alone():
    watch = agent.ProgressWatch(limit=3)

    for remaining in range(10, 0, -1):
        assert not watch.record(pytest_output(*[f"test_r{n}_x" for n in range(remaining)]))


def test_a_builder_can_only_report_a_test_that_is_failing(workspace):
    tools = agent.make_tools(LocalExecutor(workspace), "builder")
    failing = sorted(agent.failing_tests(tools["run_tests"]()))[0]

    assert tools["report_test_problem"](failing, "contradicts requirement 8") == f"REPORTED: {failing}"
    assert tools["report_test_problem"]("test_that_does_not_exist", "x").startswith("REFUSED")
    assert "report_test_problem" not in agent.make_tools(LocalExecutor(workspace), "tester")


def test_a_stuck_builder_is_stopped_early_in_team_mode(workspace, monkeypatch):
    configs = fake_gemini(monkeypatch, lambda turn: [("run_tests", {})])  # never changes anything

    outcome = agent.run_agent(workspace, max_steps=10, reviewable=True)

    assert outcome["passed"] is False
    assert outcome["steps"] == 4  # its best at run 1, then 3 runs without fewer failures
    assert outcome["stuck"] and outcome["summary"].startswith("Stopped: no progress in 3 test runs")
    assert "report_test_problem" in offered_tools(configs[0])


def test_without_a_tester_the_builder_keeps_its_whole_budget(workspace, monkeypatch):
    # ORCHESTRATOR=agent: the tests are our hand-written ones, so there's no one to review them and no reason to stop.
    configs = fake_gemini(monkeypatch, lambda turn: [("run_tests", {})])

    outcome = agent.run_agent(workspace, max_steps=6)

    assert outcome["steps"] == 6 and "stuck" not in outcome
    assert "report_test_problem" not in offered_tools(configs[0])


def test_a_reported_test_ends_the_builders_turn(workspace, monkeypatch):
    failing = sorted(agent.failing_tests(agent.make_tools(LocalExecutor(workspace))["run_tests"]()))[0]
    fake_gemini(monkeypatch, lambda turn: [("report_test_problem", {"test": failing, "reason": "contradicts R8"})])

    outcome = agent.run_agent(workspace, max_steps=10, reviewable=True)

    assert outcome["steps"] == 1
    assert outcome["disputed"] == {"test": failing, "reason": "contradicts R8"}


def test_a_review_may_fix_tests_but_never_delete_them(tmp_path):
    executor = LocalExecutor(tmp_path)
    agent.seed_workspace(executor, include_tests=False, template=agent.TEMPLATE_DB)
    executor.write_file("tests/REQUIREMENTS.md", "1. a\n2. b\n3. c\n")
    three = "".join(f"def test_r{n}_x(client):\n    assert True\n\n\n" for n in (1, 2, 3))
    executor.write_file("tests/test_app.py", three)
    tools = agent.make_tools(executor, "tester")

    assert agent.review_done(tools, min_tests=3)[0]  # unlike the first tester run, passing tests are fine now

    executor.write_file("tests/test_app.py", three.replace("def test_r3_x(client):\n    assert True\n", ""))
    done, _, feedback = agent.review_done(tools, min_tests=3)
    assert not done and "at least 3 tests" in feedback


def test_prompts_cover_missing_parents():
    # Nov 18 bug: creating a character in a faction that doesn't exist came back as 409 "already exists". The builder
    # turned EVERY IntegrityError into 409 (a missing parent raises one too), and no test tried a missing parent,
    # so every test passed. These rules close both gaps; keep them.
    assert "MISSING PARENTS" in agent.TESTER_PROMPT
    assert "Never turn every IntegrityError into 409" in agent.BUILDER_DB_PROMPT
    assert "must be looked up" in agent.BUILDER_DB_PROMPT
    assert "or catch\n    IntegrityError" not in agent.BUILDER_DB_PROMPT  # the old shortcut


def test_database_template_readme_commands_are_safe_to_paste():
    # People copy these boxes straight into a terminal. One command per box, so copying a box never brings along
    # a half-run sequence; and no "# comment", because zsh (the macOS default) passes a typed # on as an argument.
    readme = (agent.TEMPLATE_DB / "README.md").read_text()
    commands = re.findall(r"```bash\n(.*?)```", readme, re.S)

    assert len(commands) >= 5
    for command in commands:
        assert len(command.strip().splitlines()) == 1, command
        assert "#" not in command, command
