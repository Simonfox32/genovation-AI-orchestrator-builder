"""Runs that survive: cut-off replies, permanent errors, unfinished work, crashes and budgets. No model is called."""
import io
import zipfile

import pytest
from fastapi.testclient import TestClient

import orchestration
from main import app
from orchestrator import agent, model_client
from orchestrator.executors import LocalExecutor
from orchestrator.model_client import ModelReply, ModelUnavailable, ProviderError, ToolCall, Usage, UsageMeter

client = TestClient(app)

APP = ("from fastapi import FastAPI\n\nfrom app import models  # noqa: F401\nfrom app.db import Base, engine\n\n"
       "Base.metadata.create_all(engine)\napp = FastAPI()\n\n\n@app.get('/value')\ndef value():\n    return {'value': 1}\n")
PASSING = "def test_r1_value(client):\n    assert client.get('/value').json()['value'] == 1\n"


def project(tmp_path, tests=PASSING, code=APP):
    executor = LocalExecutor(tmp_path)
    agent.seed_workspace(executor, include_tests=False, template=agent.TEMPLATE_DB)
    executor.write_file("app/main.py", code)
    executor.write_file("tests/test_app.py", tests)
    return executor


def failing_suite(count):
    return "\n\n".join(f"def test_r{n}_feature():\n    assert 1 == 2" for n in range(1, count + 1)) + "\n"


class ScriptedChat:
    def __init__(self, respond):
        self.respond, self.sent, self.texts, self.models = respond, 0, [], []

    def send(self, model):
        self.sent += 1
        self.models.append(model)
        return self.respond(self.sent)

    def record(self, reply):
        return reply

    def add_tool_results(self, results):
        pass

    def add_text(self, text):
        self.texts.append(text)


def use_chat(monkeypatch, respond):
    chat = ScriptedChat(respond)
    monkeypatch.setattr(agent.model_client, "start_conversation", lambda *args, **kwargs: chat)
    return chat


@pytest.fixture
def waits(monkeypatch):
    recorded = []
    monkeypatch.setattr(agent.time, "sleep", recorded.append)
    return recorded


def fails_then_works(error, failures=1):
    def respond(n):
        if n <= failures:
            raise error
        return ModelReply("Done.", [])
    return respond


# ---- R1: cut-off replies ----

def test_cut_off_text_is_not_treated_as_finished(tmp_path, monkeypatch):
    executor = project(tmp_path)
    chat = use_chat(monkeypatch, lambda n: ModelReply("I will now write", [], cut_off=True) if n == 1
                    else ModelReply("Done.", []))

    outcome = agent.run_agent(tmp_path, model="groq/test", max_steps=3, executor=executor)

    assert outcome["steps"] == 2
    assert "cut off" in chat.texts[0] and "smaller" in chat.texts[0]


def test_a_cut_off_tool_call_never_runs(tmp_path, monkeypatch):
    executor = project(tmp_path)
    before = executor.read_file("app/main.py")
    use_chat(monkeypatch, lambda n: ModelReply("", [ToolCall("c", "write_file", {}, error="Your reply was cut off")],
                                               cut_off=True) if n == 1 else ModelReply("Done.", []))
    steps = []

    agent.run_agent(tmp_path, on_step=steps.append, model="groq/test", max_steps=3, executor=executor)

    assert steps[0]["result"].startswith("ERROR") and "cut off" in steps[0]["result"]
    assert executor.read_file("app/main.py") == before


# ---- R2: errors that retrying can't fix ----

@pytest.mark.parametrize("status, words", [
    (400, "rejected"), (401, "API key"), (402, "credits"), (403, "refused"), (404, "model"), (413, "too large"),
])
def test_a_permanent_error_stops_after_one_try(tmp_path, monkeypatch, waits, status, words):
    executor = project(tmp_path)
    chat = use_chat(monkeypatch, fails_then_works(ProviderError(status, "from groq: secret provider text"), failures=9))

    with pytest.raises(ModelUnavailable) as error:
        agent.run_agent(tmp_path, model="groq/test", max_steps=1, executor=executor)

    assert chat.sent == 1 and waits == []
    assert words in error.value.public
    assert "secret provider text" not in error.value.public


def test_a_temporary_402_is_retried(tmp_path, monkeypatch, waits):
    # OpenRouter's in-flight budget check: a 402 that says when to try again.
    executor = project(tmp_path)
    chat = use_chat(monkeypatch, fails_then_works(ProviderError(402, "from openrouter: busy", retry_after=4)))

    agent.run_agent(tmp_path, model="openrouter/x/y", max_steps=1, executor=executor)

    assert chat.sent == 2 and waits == [4]


def test_a_number_inside_an_error_is_not_mistaken_for_a_rate_limit(tmp_path, monkeypatch, waits):
    executor = project(tmp_path)
    use_chat(monkeypatch, fails_then_works(ProviderError(500, "from openrouter: request id 84291")))

    agent.run_agent(tmp_path, model="openrouter/x/y", max_steps=1, executor=executor)

    assert waits == [3]  # a short server-error wait, not the 10s rate-limit wait


def test_the_providers_retry_time_is_honored(tmp_path, monkeypatch, waits):
    executor = project(tmp_path)
    use_chat(monkeypatch, fails_then_works(ProviderError(429, "from groq: slow down", retry_after=7)))

    agent.run_agent(tmp_path, model="groq/test", max_steps=1, executor=executor)

    assert waits == [7]


def test_a_quota_that_resets_in_hours_stops_right_away(tmp_path, monkeypatch, waits):
    executor = project(tmp_path)
    chat = use_chat(monkeypatch, fails_then_works(ProviderError(429, "from groq: daily", retry_after=36000), failures=9))

    with pytest.raises(ModelUnavailable) as error:
        agent.run_agent(tmp_path, model="groq/test", max_steps=1, executor=executor)

    assert chat.sent == 1 and waits == []
    assert "quota" in error.value.public and "10.0 hours" in error.value.public


def test_a_permanent_error_on_the_strong_gemini_model_still_falls_back(tmp_path, monkeypatch, waits):
    executor = project(tmp_path)
    chat = use_chat(monkeypatch, fails_then_works(ProviderError(404, "model retired")))

    outcome = agent.run_agent(tmp_path, model=agent.STRONG_MODEL, max_steps=1, executor=executor)

    assert chat.models == [agent.STRONG_MODEL, agent.FALLBACK_MODEL]
    assert outcome["model"] == agent.FALLBACK_MODEL


def test_a_long_retry_time_is_capped_when_another_model_can_take_over(tmp_path, monkeypatch, waits):
    executor = project(tmp_path)
    use_chat(monkeypatch, fails_then_works(ProviderError(503, "down", retry_after=9999)))

    agent.run_agent(tmp_path, model=agent.STRONG_MODEL, max_steps=1, executor=executor)

    assert waits == [agent.MAX_RETRY_WAIT]


def test_running_out_of_retries_is_a_clear_error(tmp_path, monkeypatch, waits):
    executor = project(tmp_path)
    monkeypatch.setattr(agent, "MAX_ATTEMPTS", 2)
    use_chat(monkeypatch, fails_then_works(ProviderError(503, "from groq: down"), failures=9))

    with pytest.raises(ModelUnavailable) as error:
        agent.run_agent(tmp_path, model="groq/test", max_steps=1, executor=executor)

    assert "groq/test unavailable" in error.value.public


# ---- R3: unfinished work keeps building instead of triggering a review ----

def review_setup(tmp_path, monkeypatch, tests):
    executor = project(tmp_path, tests)
    count = tests.count("def test_")
    executor.write_file("tests/REQUIREMENTS.md", "\n".join(f"{n}. Feature {n}." for n in range(1, count + 1)) + "\n")
    counters = {"review": 0, "builder": 0, "first": []}

    def start(model, system, tools, first, timeout_ms):
        if any(tool.name == "uphold_test" for tool in tools):
            def respond(n):
                counters["review"] += 1
                return ModelReply("I checked it.", [])
            return ScriptedChat(respond)
        counters["first"].append(first)

        def respond(n):
            counters["builder"] += 1
            return ModelReply("Done.", [])
        return ScriptedChat(respond)

    monkeypatch.setattr(agent.model_client, "start_conversation", start)
    return executor, counters


def test_running_out_of_steps_keeps_building_with_no_review(tmp_path, monkeypatch):
    executor, counters = review_setup(tmp_path, monkeypatch, failing_suite(30))
    outcome = {"passed": False, "steps": 25, "summary": "Stopped: hit the step limit", "model": "groq/test",
               "test_output": agent.make_tools(executor)["run_tests"]()}
    notes = []

    final = agent.review_rounds(tmp_path, executor, outcome, model="groq/test", rounds=1,
                                on_note=lambda text, output="": notes.append(text))

    assert counters["review"] == 0 and counters["builder"] >= 1
    assert final["reviews"] == []
    assert "30 tests failing" in notes[0] and "keeps building" in notes[0]
    assert "30 tests still fail" in counters["first"][0] and "test_r1_feature" in counters["first"][0]


def test_many_stuck_tests_keep_building_instead_of_a_review(tmp_path, monkeypatch):
    executor, counters = review_setup(tmp_path, monkeypatch, failing_suite(agent.REVIEW_MAX_TESTS + 1))
    run = agent.make_tools(executor)["run_tests"]()
    outcome = {"passed": False, "steps": 5, "summary": "Stopped", "model": "groq/test", "test_output": run,
               "stuck": sorted(agent.failing_tests(run))}

    agent.review_rounds(tmp_path, executor, outcome, model="groq/test", rounds=1)

    assert counters["review"] == 0 and counters["builder"] >= 1


def test_a_few_stuck_tests_still_get_a_review(tmp_path, monkeypatch):
    executor, counters = review_setup(tmp_path, monkeypatch, failing_suite(agent.REVIEW_MAX_TESTS))
    run = agent.make_tools(executor)["run_tests"]()
    outcome = {"passed": False, "steps": 5, "summary": "Stopped", "model": "groq/test", "test_output": run,
               "stuck": sorted(agent.failing_tests(run))}

    agent.review_rounds(tmp_path, executor, outcome, model="groq/test", rounds=1)

    assert counters["review"] >= 1


def test_a_reported_test_always_gets_a_review(tmp_path, monkeypatch):
    executor, counters = review_setup(tmp_path, monkeypatch, failing_suite(30))
    outcome = {"passed": False, "steps": 5, "summary": "Reported", "model": "groq/test",
               "test_output": agent.make_tools(executor)["run_tests"](),
               "disputed": {"test": "test_r1_feature", "reason": "contradicts requirement 2"}}

    agent.review_rounds(tmp_path, executor, outcome, model="groq/test", rounds=1)

    assert counters["review"] >= 1


def test_code_that_does_not_load_keeps_building(tmp_path, monkeypatch):
    executor, counters = review_setup(tmp_path, monkeypatch, PASSING)
    executor.write_file("app/main.py", "def broken(:\n")
    outcome = {"passed": False, "steps": 25, "summary": "Stopped: hit the step limit", "model": "groq/test",
               "test_output": agent.make_tools(executor)["run_tests"]()}

    agent.review_rounds(tmp_path, executor, outcome, model="groq/test", rounds=1)

    assert counters["builder"] >= 1 and "don't load" in counters["first"][0]


def spy_on_budgets(monkeypatch):
    budgets, real = [], agent.run_agent

    def spy(*args, **kwargs):
        budgets.append(kwargs["max_steps"])
        return real(*args, **kwargs)

    monkeypatch.setattr(agent, "run_agent", spy)
    return budgets


def test_a_continued_build_counts_its_steps_and_cost_with_a_bigger_budget(tmp_path, monkeypatch):
    executor = project(tmp_path, failing_suite(40))
    saved, meter, budgets = agent.save_tests(executor), UsageMeter(), spy_on_budgets(monkeypatch)
    monkeypatch.setattr(agent.model_client, "start_conversation", lambda *args, **kwargs: ScriptedChat(
        lambda n: ModelReply("Done.", [], Usage(10, 0, 1, 0.001))))
    outcome = {"passed": False, "steps": 25, "summary": "Stopped: hit the step limit", "model": "groq/test",
               "test_output": agent.make_tools(executor)["run_tests"]()}

    final = agent.review_rounds(tmp_path, executor, outcome, model="groq/test", rounds=1, saved_tests=saved, meter=meter)

    assert budgets == [agent.retry_steps(40)]
    assert final["steps"] > 25
    assert meter.totals()["by_stage"]["builder"]["requests"] >= 1


def test_a_retry_after_a_review_also_gets_the_bigger_budget(tmp_path, monkeypatch):
    executor = project(tmp_path, failing_suite(40))
    executor.write_file("tests/REQUIREMENTS.md", "\n".join(f"{n}. Feature {n}." for n in range(1, 41)) + "\n")
    saved, budgets = agent.save_tests(executor), spy_on_budgets(monkeypatch)
    outcome = {"passed": False, "steps": 5, "summary": "Reported", "model": "groq/test",
               "test_output": agent.make_tools(executor)["run_tests"](),
               "disputed": {"test": "test_r1_feature", "reason": "I think it is wrong"}}

    def start(model, system, tools, first, timeout_ms):
        if any(tool.name == "uphold_test" for tool in tools):
            return ScriptedChat(lambda n: ModelReply("", [ToolCall("c", "uphold_test", {
                "test": "test_r1_feature", "reason": "the code is missing", "test_expects": "1", "requirement_says": "1",
                "test_agrees_with_requirement": True})]))
        return ScriptedChat(lambda n: ModelReply("Done.", []))

    monkeypatch.setattr(agent.model_client, "start_conversation", start)

    agent.review_rounds(tmp_path, executor, outcome, model="groq/test", rounds=1, saved_tests=saved)

    assert budgets == [agent.REVIEW_STEPS, agent.retry_steps(40)]


# ---- R4: a crash leaves an honest project with its files ----

def crashing_team(error):
    def run_agent(workspace, on_step=None, role="builder", executor=None, meter=None, **kwargs):
        if role == "tester":
            if meter is not None:
                meter.add("groq/test", Usage(100, 0, 10, 0.001), "tester")
            executor.write_file("tests/REQUIREMENTS.md", "1. Value\n")
            executor.write_file("tests/test_app.py", PASSING)
            return {"passed": True, "steps": 2, "summary": "done", "test_output": "1 failed in 0.1s", "model": "groq/test"}
        executor.write_file("app/partial.py", "PARTIAL = True\n")
        raise error
    return run_agent


def submit():
    response = client.post("/projects", json={"description": "A value API"})
    assert response.status_code == 201
    return client.get(f"/projects/{response.json()['id']}").json()


def test_a_model_failure_leaves_a_clear_reason_and_the_files(monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR", "agent-team")
    monkeypatch.setattr(orchestration.agent, "run_agent",
                        crashing_team(ModelUnavailable("The AI provider rejected the API key for groq/test.")))

    project_json = submit()

    assert project_json["status"] == "failed"
    assert "rejected the API key" in project_json["summary"]
    assert "are working" not in project_json["summary"]
    assert project_json["downloadable"] is True
    names = zipfile.ZipFile(io.BytesIO(client.get(f"/projects/{project_json['id']}/download").content)).namelist()
    assert any(name.endswith("app/partial.py") for name in names)
    task_names = [task["name"] for task in project_json["tasks"]]
    assert any(name.startswith("Build stopped: The AI provider rejected") for name in task_names)
    assert any(name.startswith("Model usage: 1 requests") for name in task_names)  # what it cost before stopping


def test_an_internal_error_never_shows_its_details(monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR", "agent-team")
    monkeypatch.setattr(orchestration.agent, "run_agent", crashing_team(RuntimeError("api_key=secret-123")))

    response = client.post("/projects", json={"description": "A value API"})
    project_response = client.get(f"/projects/{response.json()['id']}")

    assert project_response.json()["status"] == "failed"
    assert "secret-123" not in project_response.text
    assert "internal error" in project_response.json()["summary"]


def test_a_crash_still_fails_cleanly_when_files_cannot_be_read(monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR", "agent-team")
    monkeypatch.setattr(orchestration.agent, "run_agent", crashing_team(ModelUnavailable("The model stopped.")))
    monkeypatch.setattr(orchestration, "_collect_files", lambda executor: (_ for _ in ()).throw(ConnectionError("gone")))

    project_json = submit()

    assert project_json["status"] == "failed"
    assert "The model stopped." in project_json["summary"]
    assert "in the download" not in project_json["summary"]


# ---- R5: budgets follow the size of the work, with a spending cap ----

@pytest.mark.parametrize("tests, steps", [(0, 25), (13, 25), (15, 25), (16, 26), (40, 50), (50, 60), (500, 60)])
def test_builder_steps_grow_with_the_tests_up_to_a_limit(tests, steps):
    assert agent.builder_steps(tests) == steps


@pytest.mark.parametrize("tests, steps", [(0, 15), (20, 15), (40, 25), (500, 30)])
def test_extra_rounds_grow_with_the_tests(tests, steps):
    assert agent.retry_steps(tests) == steps


@pytest.mark.parametrize("length, steps", [(10, 12), (2500, 14), (5000, 17), (100000, 30)])
def test_tester_steps_grow_with_the_request(length, steps):
    assert agent.tester_steps("x" * length) == steps


def test_the_pipeline_gives_a_bigger_project_more_steps(monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR", "agent-team")
    monkeypatch.delenv("MAX_PROJECT_COST_USD", raising=False)
    budgets, limits = {}, set()
    tests = "".join(f"def test_r{n}_x(client):\n    assert client.get('/value').status_code == 200\n\n\n" for n in range(1, 41))

    def run_agent(workspace, on_step=None, role="builder", executor=None, **kwargs):
        budgets[role] = kwargs["max_steps"]
        limits.add(kwargs["meter"].max_cost_usd)
        if role == "tester":
            executor.write_file("tests/REQUIREMENTS.md", "1. Value\n")
            executor.write_file("tests/test_app.py", tests)
            return {"passed": True, "steps": 2, "summary": "done", "test_output": "40 failed in 0.1s", "model": "m"}
        return {"passed": False, "steps": 3, "summary": "Stopped", "test_output": "PLAIN OUTPUT", "model": "m"}

    monkeypatch.setattr(orchestration.agent, "run_agent", run_agent)
    monkeypatch.setattr(orchestration.agent, "review_rounds", lambda workspace, executor, outcome, **kwargs: outcome)

    submit()

    assert budgets == {"tester": 12, "builder": 50}
    assert limits == {model_client.DEFAULT_MAX_PROJECT_COST_USD}  # real projects always run with a spending limit


def test_a_build_stops_at_its_spending_limit(tmp_path, monkeypatch):
    executor = project(tmp_path)
    meter = UsageMeter(max_cost_usd=0.01)
    chat = use_chat(monkeypatch, lambda n: ModelReply("", [ToolCall(f"c{n}", "list_files", {})], Usage(100, 0, 10, 0.006)))

    with pytest.raises(ModelUnavailable) as error:
        agent.run_agent(tmp_path, model="openrouter/x/y", max_steps=9, executor=executor, meter=meter)

    assert chat.sent == 2  # $0.006, then $0.012: over the limit, so no third request
    assert "$0.01 spending limit" in error.value.public


def test_a_build_stops_at_its_token_limit_when_cost_is_unknown(tmp_path, monkeypatch):
    executor = project(tmp_path)
    meter = UsageMeter(max_tokens=1000)
    chat = use_chat(monkeypatch, lambda n: ModelReply("", [ToolCall(f"c{n}", "list_files", {})], Usage(600, 0, 10, None)))

    with pytest.raises(ModelUnavailable) as error:
        agent.run_agent(tmp_path, model="gemini-3.5-flash-lite", max_steps=9, executor=executor, meter=meter)

    assert chat.sent == 2
    assert "1,000 token limit" in error.value.public


def test_project_limits_come_from_settings(monkeypatch):
    monkeypatch.setenv("MAX_PROJECT_COST_USD", "0.25")
    monkeypatch.setenv("MAX_PROJECT_TOKENS", "5000")

    meter = UsageMeter.for_project()

    assert (meter.max_cost_usd, meter.max_tokens) == (0.25, 5000)


def test_every_project_has_default_limits(monkeypatch):
    monkeypatch.delenv("MAX_PROJECT_COST_USD", raising=False)
    monkeypatch.delenv("MAX_PROJECT_TOKENS", raising=False)

    meter = UsageMeter.for_project()

    assert meter.max_cost_usd == model_client.DEFAULT_MAX_PROJECT_COST_USD
    assert meter.max_tokens == model_client.DEFAULT_MAX_PROJECT_TOKENS


def test_reaching_the_limit_keeps_the_files_and_says_why(monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR", "agent-team")
    monkeypatch.setattr(orchestration.agent, "run_agent",
                        crashing_team(ModelUnavailable("This build reached its $0.50 spending limit.")))

    project_json = submit()

    assert project_json["status"] == "failed"
    assert "$0.50 spending limit" in project_json["summary"] and project_json["downloadable"] is True
