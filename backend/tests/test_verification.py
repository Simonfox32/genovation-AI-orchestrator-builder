"""Can we trust a result? Real pytest runs and scripted agents; no model is ever called."""
import json

import pytest
from fastapi.testclient import TestClient

import orchestration
from main import app
from orchestrator import agent, model_client
from orchestrator.executors import LocalExecutor
from orchestrator.model_client import ModelReply, ToolCall, Usage, UsageMeter

client = TestClient(app)

APP = ("from fastapi import FastAPI\n\nfrom app import models  # noqa: F401\nfrom app.db import Base, engine\n\n"
       "Base.metadata.create_all(engine)\napp = FastAPI()\n\n\n@app.get('/value')\ndef value():\n    return {'value': 1}\n")
PASSING = "def test_r1_value(client):\n    assert client.get('/value').json()['value'] == 1\n"
FAILING = "def test_r1_value(client):\n    assert client.get('/value').json()['value'] == 2\n"
TWO_TESTS = PASSING + "\n\ndef test_r2_status(client):\n    assert client.get('/value').status_code == 200\n"
FLIP_FAILURES = ("import pytest\n\n\n@pytest.hookimpl(hookwrapper=True)\ndef pytest_runtest_makereport(item, call):\n"
                 "    outcome = yield\n    report = outcome.get_result()\n    report.outcome = 'passed'\n"
                 "    report.longrepr = None\n")
CLAIMS_PASS = {"passed": True, "steps": 3, "summary": "done", "test_output": "1 passed in 0.1s"}


def project(tmp_path, tests=PASSING, code=APP):
    executor = LocalExecutor(tmp_path)
    agent.seed_workspace(executor, include_tests=False, template=agent.TEMPLATE_DB)
    executor.write_file("app/main.py", code)
    executor.write_file("tests/test_app.py", tests)
    return executor


def many_tests(passing, total=80):
    return "\n\n".join(f"def test_r{n}_customer_cannot_cancel_an_order_after_it_has_shipped():\n"
                       f"    assert {1 if n < passing else 2} == 1" for n in range(total)) + "\n"


class ScriptedChat:
    """Stands in for a provider conversation: respond(n) gives the n-th reply."""

    def __init__(self, respond):
        self.respond, self.sent = respond, 0

    def send(self, model):
        self.sent += 1
        return self.respond(self.sent)

    def record(self, reply):
        return reply

    def add_tool_results(self, results):
        pass

    def add_text(self, text):
        pass


# ---- M1: every failure is seen, however long the output is ----

def test_every_failure_is_counted_when_the_output_is_long(tmp_path):
    executor = project(tmp_path, many_tests(0))

    run = agent.make_tools(executor)["run_tests"]()

    assert len(agent.failing_tests(run)) == 80
    assert not agent.tests_pass(run)


def test_a_long_run_stays_short_but_keeps_what_the_model_needs(tmp_path):
    executor = project(tmp_path, many_tests(0))

    run = agent.make_tools(executor)["run_tests"]()

    assert len(run) <= agent.TEST_OUTPUT_LIMIT
    assert "assert 2 == 1" in run                     # the details of the first failures
    assert "FAILED tests/test_app.py::test_r0_" in run  # which tests fail
    assert f"and {80 - agent.FAILURES_LISTED} more failing tests" in run
    assert run.strip().splitlines()[-1].startswith("80 failed")


def test_a_short_run_is_left_exactly_as_pytest_printed_it():
    output = "F.\nFAILED tests/test_app.py::test_x - assert 1 == 2\n1 failed, 1 passed in 0.1s\n"

    assert agent.shorten_test_output(output) == output


def test_a_builder_fixing_many_tests_is_not_called_stuck(tmp_path, monkeypatch):
    # Audit, Nov 23: with 80 failures we only ever saw 45, so a builder fixing 8 per run was stopped as "stuck".
    executor = project(tmp_path, many_tests(0))
    fixed = {"count": 0}

    def after_each_run(event):
        fixed["count"] += 8
        executor.write_file("tests/test_app.py", many_tests(fixed["count"]))

    monkeypatch.setattr(agent.model_client, "start_conversation", lambda *args, **kwargs: ScriptedChat(
        lambda n: ModelReply("", [ToolCall(f"c{n}", "run_tests", {})])))

    outcome = agent.run_agent(tmp_path, on_step=after_each_run, model="groq/test", max_steps=9, executor=executor,
                              reviewable=True)

    assert "stuck" not in outcome


# ---- M2: a pass must be real ----

@pytest.mark.parametrize("summary, passes", [
    ("10 passed in 0.1s", True),
    ("20 passed, 4 warnings in 0.8s", True),
    ("9 passed, 3 skipped in 0.1s", False),
    ("9 passed, 1 xpassed in 0.1s", False),
    ("9 passed, 1 xfailed in 0.1s", False),
    ("9 passed, 2 deselected in 0.1s", False),
    ("2 failed, 8 passed in 0.1s", False),
    ("1 passed, 1 error in 0.1s", False),
    ("no tests ran in 0.1s", False),
])
def test_only_a_clean_summary_counts_as_passing(summary, passes):
    assert agent.tests_pass(summary) is passes


def test_a_printed_summary_cannot_fake_a_pass(tmp_path):
    executor = project(tmp_path, FAILING)
    executor.write_file("app/__init__.py", 'import atexit\natexit.register(print, "5 passed in 0.01s")\n')

    run = agent.make_tools(executor)["run_tests"]()

    assert run.strip().splitlines()[-1] == "5 passed in 0.01s"  # the fake line really is last
    assert not agent.tests_pass(run)


def test_a_run_that_exits_with_an_error_never_passes(tmp_path):
    tests = PASSING + "\n\ndef test_r2_stops(client):\n    import pytest\n    pytest.exit('stop', returncode=3)\n"
    executor = project(tmp_path, tests)

    run = agent.make_tools(executor)["run_tests"]()

    assert run.exit_code == 3 and agent.clean_summary(run.summaries[-1])  # the summary alone looks fine
    assert not agent.tests_pass(run)


@pytest.mark.parametrize("role, path", [
    ("builder", "conftest.py"), ("builder", "app/conftest.py"), ("builder", "pytest.ini"), ("builder", ".pytest.ini"),
    ("builder", "pyproject.toml"), ("builder", "tox.ini"), ("builder", "setup.cfg"), ("builder", "./conftest.py"),
    ("tester", "tests/sub/conftest.py"), ("tester", "tests/pytest.ini"),
])
def test_no_agent_may_change_how_pytest_runs(tmp_path, role, path):
    result = agent.make_tools(LocalExecutor(tmp_path), role)["write_file"](path, "x = 1")

    assert result.startswith("REFUSED") and "how the tests run" in result
    assert not (tmp_path / path).exists()


def test_final_check_accepts_honest_work(tmp_path):
    executor = project(tmp_path, TWO_TESTS)

    outcome = agent.verify_outcome(executor, agent.save_tests(executor), CLAIMS_PASS)

    assert outcome["passed"] is True
    assert outcome["test_output"].strip().splitlines()[-1].startswith("2 passed")


def test_final_check_leaves_a_failed_run_failed(tmp_path):
    executor = project(tmp_path, PASSING)
    failed = {**CLAIMS_PASS, "passed": False, "summary": "Stopped"}

    assert agent.verify_outcome(executor, agent.save_tests(executor), failed) == failed


def test_final_check_uses_our_copy_of_the_tests(tmp_path):
    executor = project(tmp_path, FAILING)
    saved = agent.save_tests(executor)
    executor.write_file("tests/test_app.py", PASSING)  # changed behind our back

    outcome = agent.verify_outcome(executor, saved, CLAIMS_PASS)

    assert outcome["passed"] is False
    assert executor.read_file("tests/test_app.py") == FAILING


def test_final_check_judges_our_saved_tests_not_the_workspace_copy(tmp_path):
    executor = project(tmp_path, TWO_TESTS)
    saved = agent.save_tests(executor)
    executor.write_file("tests/test_app.py", FAILING)  # the workspace copy is wrong, but the app is fine

    outcome = agent.verify_outcome(executor, saved, CLAIMS_PASS)

    assert outcome["passed"] is True
    assert executor.read_file("tests/test_app.py") == TWO_TESTS


@pytest.mark.parametrize("rogue", ["conftest.py", "pytest.ini"])
def test_final_check_removes_files_that_change_how_pytest_runs(tmp_path, rogue):
    executor = project(tmp_path, FAILING)
    saved = agent.save_tests(executor)
    executor.write_file(rogue, FLIP_FAILURES if rogue == "conftest.py" else "[pytest]\naddopts = --co\n")

    outcome = agent.verify_outcome(executor, saved, CLAIMS_PASS)

    assert outcome["passed"] is False
    assert not (tmp_path / rogue).exists()
    assert executor.read_file("tests/conftest.py") == saved["tests/conftest.py"]


def test_final_check_ignores_test_files_it_did_not_save(tmp_path):
    executor = project(tmp_path, TWO_TESTS)
    saved = agent.save_tests(executor)
    executor.write_file("tests/test_extra.py", "def test_extra():\n    assert 1 == 2\n")

    assert agent.verify_outcome(executor, saved, CLAIMS_PASS)["passed"] is True
    assert not (tmp_path / "tests" / "test_extra.py").exists()


def test_final_check_catches_a_run_that_stops_early(tmp_path):
    tests = PASSING + "\n\ndef test_r2_stops(client):\n    import pytest\n    pytest.exit('stop', returncode=0)\n"
    executor = project(tmp_path, tests)

    outcome = agent.verify_outcome(executor, agent.save_tests(executor), CLAIMS_PASS)

    assert outcome["passed"] is False
    assert "1 of 2" in outcome["summary"]


def test_a_fake_summary_cannot_hide_a_run_that_stopped_early(tmp_path):
    tests = PASSING + "\n\ndef test_r2_stops(client):\n    import pytest\n    pytest.exit('stop', returncode=0)\n"
    executor = project(tmp_path, tests)
    executor.write_file("app/__init__.py", 'import atexit\natexit.register(print, "2 passed in 0.01s")\n')

    outcome = agent.verify_outcome(executor, agent.save_tests(executor), CLAIMS_PASS)

    assert outcome["passed"] is False and "1 of 2" in outcome["summary"]


def test_final_check_catches_tests_rewritten_during_the_run(tmp_path):
    executor = project(tmp_path, FAILING)
    saved = agent.save_tests(executor)
    executor.write_file("app/__init__.py", (
        "import io\nwith io.open('tests/test_app.py', 'w') as tests:\n"
        f"    tests.write({PASSING!r})\n"))

    outcome = agent.verify_outcome(executor, saved, CLAIMS_PASS)

    assert outcome["passed"] is False
    assert "changed" in outcome["summary"]
    assert executor.read_file("tests/test_app.py") == FAILING


def test_a_failed_final_check_says_so_on_its_last_line(tmp_path):
    executor = project(tmp_path, FAILING)

    outcome = agent.verify_outcome(executor, agent.save_tests(executor), CLAIMS_PASS)

    assert outcome["test_output"].strip().splitlines()[-1].startswith("Final check failed")
    assert not agent.tests_pass(outcome["test_output"])


def team_that_claims_success(app_code, tests=TWO_TESTS):
    def run_agent(workspace, on_step=None, role="builder", executor=None, meter=None, **kwargs):
        if meter is not None:
            meter.add("groq/test", Usage(100, 40, 10, 0.001), "review" if kwargs.get("review") else role)
        if role == "tester":
            executor.write_file("tests/REQUIREMENTS.md", "1. Value\n2. Status\n")
            executor.write_file("tests/test_app.py", tests)
            return {"passed": True, "steps": 2, "summary": "done", "test_output": "2 failed in 0.1s", "model": "groq/test"}
        executor.write_file("app/main.py", app_code)
        return {"passed": True, "steps": 3, "summary": "done", "test_output": "2 passed in 0.1s", "model": "groq/test"}
    return run_agent


def submit():
    response = client.post("/projects", json={"description": "A value API"})
    assert response.status_code == 201
    return client.get(f"/projects/{response.json()['id']}").json()


def test_project_is_verified_by_the_final_check(monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR", "agent-team")
    monkeypatch.setattr(orchestration.agent, "run_agent", team_that_claims_success(APP))

    project_json = submit()

    assert project_json["status"] == "completed"
    verified = [t for t in project_json["tasks"] if t["name"].startswith("Verified by the orchestrator")]
    assert verified[0]["name"].startswith("Verified by the orchestrator: 2 passed")


def test_an_agent_claiming_success_cannot_mark_a_broken_project_verified(monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR", "agent-team")
    broken = APP.replace("{'value': 1}", "{'value': 2}")
    monkeypatch.setattr(orchestration.agent, "run_agent", team_that_claims_success(broken))

    project_json = submit()

    assert project_json["status"] == "failed"
    assert "Final check failed" in project_json["summary"]


def test_a_review_fix_becomes_part_of_the_protected_tests(tmp_path):
    executor = project(tmp_path, FAILING)
    executor.write_file("tests/REQUIREMENTS.md", "1. The value is 1\n")
    saved = agent.save_tests(executor)
    outcome = {"passed": False, "steps": 5, "summary": "Reported", "model": "groq/test",
               "test_output": agent.make_tools(executor)["run_tests"](),
               "disputed": {"test": "test_r1_value", "reason": "requirement 1 says 1"}}

    def start(model, system, tools, first, timeout_ms):
        return ScriptedChat(lambda n: ModelReply("", [ToolCall("c", "fix_test", {
            "test": "test_r1_value", "new_code": PASSING, "reason": "requirement 1 says 1"})]))

    original = agent.model_client.start_conversation
    agent.model_client.start_conversation = start
    try:
        final = agent.review_rounds(tmp_path, executor, outcome, model="groq/test", rounds=1, saved_tests=saved)
    finally:
        agent.model_client.start_conversation = original

    assert final["passed"] is True
    assert saved["tests/test_app.py"].strip() == PASSING.strip()
    assert agent.verify_outcome(executor, saved, final)["passed"] is True


def test_a_review_works_from_the_real_tests_not_tampered_ones(tmp_path):
    executor = project(tmp_path, FAILING)
    executor.write_file("tests/REQUIREMENTS.md", "1. The value is 1\n")
    saved = agent.save_tests(executor)
    executor.write_file("tests/test_app.py", PASSING + "\n\n# tampered\n")
    decisions = agent.ReviewDecisions(executor, ["test_r1_value"], min_tests=1, source=saved["tests/test_app.py"])

    assert decisions.fix_test("test_r1_value", PASSING, "requirement 1 says 1").startswith("FIXED")

    assert "tampered" not in decisions.source
    assert "tampered" not in executor.read_file("tests/test_app.py")


# ---- M3: a review may correct a test, never weaken it ----

STATUS_TEST = "def test_r1_status(client):\n    response = client.get('/x')\n    assert response.status_code == 409\n"


@pytest.mark.parametrize("body", [
    "assert response.status_code == response.status_code",
    "assert response.status_code < 600",
    "assert response.status_code in (200, 409)",
    "assert response.status_code == 409 or True",
    "assert response.status_code == 200 or response.status_code == 409",
    "assert 1 == 1",
    "assert not False",
    "assert response.status_code == 200\n    import pytest\n    pytest.skip('later')",
    "assert response.status_code == 200\n    import pytest\n    pytest.xfail('later')",
    "assert response.status_code == 200\n    from pytest import skip\n    skip('later')",
])
def test_a_review_cannot_weaken_a_test(body):
    weakened = f"def test_r1_status(client):\n    response = client.get('/x')\n    {body}\n"

    fixed, problem = agent.replace_test(STATUS_TEST, "test_r1_status", weakened)

    assert fixed is None and problem


def test_or_true_cannot_weaken_a_check_that_was_not_exact():
    source = "def test_r1_status(client):\n    response = client.get('/x')\n    assert response.status_code > 400\n"

    fixed, problem = agent.replace_test(source, "test_r1_status", source.replace("> 400", "> 400 or True"))

    assert fixed is None and "never fail" in problem


def test_a_review_cannot_skip_a_test_with_a_decorator():
    source = "import pytest\n\n\n" + STATUS_TEST
    new = "@pytest.mark.skip(reason='later')\n" + STATUS_TEST.replace("409", "200")

    fixed, problem = agent.replace_test(source, "test_r1_status", new)

    assert fixed is None and "skip" in problem


@pytest.mark.parametrize("body", [
    "assert response.status_code == 404",
    "assert response.status_code == 404\n    assert response.json()['detail'] == 'Not found'",
    "assert response.status_code is not None\n    assert response.status_code == 404",
])
def test_a_review_can_still_correct_a_wrong_value(body):
    corrected = f"def test_r1_status(client):\n    response = client.get('/x')\n    {body}\n"

    fixed, problem = agent.replace_test(STATUS_TEST, "test_r1_status", corrected)

    assert problem is None and "404" in fixed


def sound(tmp_path, tests):
    executor = project(tmp_path, tests)
    executor.write_file("tests/REQUIREMENTS.md", "1. a\n2. b\n3. c\n")
    return agent.tests_are_sound(agent.make_tools(executor, "tester"))


def test_the_tester_cannot_hand_over_skipped_tests(tmp_path):
    tests = "import pytest\n\n\n" + "".join(
        f"@pytest.mark.skip\ndef test_r{n}_x(client):\n    assert client.get('/value').status_code == 200\n\n\n"
        for n in (1, 2, 3))

    ok, _, feedback = sound(tmp_path, tests)

    assert not ok and "skip" in feedback


def test_a_variable_named_skip_is_fine(tmp_path):
    tests = "".join(f"def test_r{n}_page(client):\n    skip = 10\n    assert client.get(f'/value?skip={{skip}}').status_code == 200\n\n\n"
                    for n in (1, 2, 3))

    assert sound(tmp_path, tests)[0]


# ---- M4: tokens and cost are recorded ----

def test_the_agent_loop_records_every_model_reply(tmp_path, monkeypatch):
    executor = project(tmp_path, PASSING)
    meter = UsageMeter()

    def respond(n):
        if n == 1:
            return ModelReply("", [ToolCall("c", "run_tests", {})], Usage(100, 20, 10, 0.001))
        return ModelReply("Done.", [], Usage(200, 100, 5, 0.002))

    monkeypatch.setattr(agent.model_client, "start_conversation", lambda *args, **kwargs: ScriptedChat(respond))

    agent.run_agent(tmp_path, model="groq/test", max_steps=3, executor=executor, meter=meter)

    totals = meter.totals()
    assert totals["requests"] == 2 and totals["input_tokens"] == 300 and totals["output_tokens"] == 15
    assert totals["cost_usd"] == pytest.approx(0.003)
    assert totals["by_stage"]["builder"]["requests"] == 2


def test_failed_model_attempts_are_counted(tmp_path, monkeypatch):
    executor = project(tmp_path, PASSING)
    meter = UsageMeter()

    def respond(n):
        if n == 1:
            raise RuntimeError("503 from groq: overloaded")
        return ModelReply("Done.", [], Usage(10, 0, 1, 0.0001))

    monkeypatch.setattr(agent.model_client, "start_conversation", lambda *args, **kwargs: ScriptedChat(respond))
    monkeypatch.setattr(agent.time, "sleep", lambda seconds: None)

    agent.run_agent(tmp_path, model="groq/test", max_steps=1, executor=executor, meter=meter)

    assert meter.totals()["failed_attempts"] == 1


def test_reviews_are_recorded_in_their_own_stage(tmp_path, monkeypatch):
    executor = project(tmp_path, FAILING)
    executor.write_file("tests/REQUIREMENTS.md", "1. The value is 1\n")
    outcome = {"passed": False, "steps": 5, "summary": "Reported", "model": "groq/test",
               "test_output": agent.make_tools(executor)["run_tests"](),
               "disputed": {"test": "test_r1_value", "reason": "requirement 1 says 1"}}
    meter = UsageMeter()
    monkeypatch.setattr(agent.model_client, "start_conversation", lambda *args, **kwargs: ScriptedChat(
        lambda n: ModelReply("", [ToolCall("c", "fix_test", {"test": "test_r1_value", "new_code": PASSING,
                                                             "reason": "requirement 1 says 1"})], Usage(50, 0, 5, 0.0005))))

    agent.review_rounds(tmp_path, executor, outcome, model="groq/test", rounds=1, meter=meter)

    assert meter.totals()["by_stage"]["review"]["requests"] == 1


def test_a_builder_retry_after_a_review_is_recorded(tmp_path, monkeypatch):
    executor = project(tmp_path, FAILING)
    executor.write_file("tests/REQUIREMENTS.md", "1. The value is 2\n")
    outcome = {"passed": False, "steps": 5, "summary": "Reported", "model": "groq/test",
               "test_output": agent.make_tools(executor)["run_tests"](),
               "disputed": {"test": "test_r1_value", "reason": "I think it is wrong"}}
    meter = UsageMeter()

    def start(model, system, tools, first, timeout_ms):
        if any(tool.name == "uphold_test" for tool in tools):
            return ScriptedChat(lambda n: ModelReply("", [ToolCall("c", "uphold_test", {
                "test": "test_r1_value", "reason": "the code returns 1", "test_expects": "2", "requirement_says": "2",
                "test_agrees_with_requirement": True})], Usage(50, 0, 5, 0.0005)))
        return ScriptedChat(lambda n: ModelReply("Done.", [], Usage(70, 0, 7, 0.0007)))

    monkeypatch.setattr(agent.model_client, "start_conversation", start)

    agent.review_rounds(tmp_path, executor, outcome, model="groq/test", rounds=1, meter=meter)

    stages = meter.totals()["by_stage"]
    assert stages["review"]["requests"] == 1 and stages["builder"]["requests"] >= 1


def test_a_project_records_its_model_usage(monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR", "agent-team")
    monkeypatch.setattr(orchestration.agent, "run_agent", team_that_claims_success(APP))

    project_json = submit()

    usage = [t for t in project_json["tasks"] if t["name"].startswith("Model usage")]
    assert len(usage) == 1
    assert "2 requests" in usage[0]["name"] and "$0.0020" in usage[0]["name"]
    details = json.loads(usage[0]["output"])
    assert details["input_tokens"] == 200 and set(details["by_stage"]) == {"tester", "builder"}
    assert len(details["steps"]) == 2
