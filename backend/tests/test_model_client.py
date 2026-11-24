"""Tests for the provider-neutral model layer (orchestrator/model_client.py). No real model is called."""
import ast
import shutil
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import httpx2
import pytest
from google.genai import types

import orchestration  # noqa: F401  (puts the repo root on the import path)
from orchestrator import agent, model_client
from orchestrator.executors import LocalExecutor
from orchestrator.model_client import ToolCall, ToolSpec, Usage, UsageMeter

READ_FILE = ToolSpec(
    name="read_file",
    description="Read a file.",
    parameters={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
)


def gemini_reply(*parts):
    content = types.Content(role="model", parts=list(parts))
    return types.GenerateContentResponse(candidates=[types.Candidate(content=content)])


def tool_part(call_id="call-1", name="read_file", args=None, signature=None):
    call = types.FunctionCall(id=call_id, name=name, args=args if args is not None else {"path": "app/main.py"})
    return types.Part(function_call=call, thought_signature=signature)


@pytest.fixture
def gemini(monkeypatch):
    """A fake Gemini. Put replies (or exceptions) in .replies; each request is recorded in .requests."""
    fake = SimpleNamespace(replies=[], requests=[], client_settings=[])

    class FakeModels:
        def generate_content(self, model, contents, config):
            fake.requests.append(SimpleNamespace(model=model, contents=list(contents), config=config))
            reply = fake.replies.pop(0)
            if isinstance(reply, Exception):
                raise reply
            return reply

    def make_client(**settings):
        fake.client_settings.append(settings)
        return SimpleNamespace(models=FakeModels())

    monkeypatch.setattr(model_client.genai, "Client", make_client)
    return fake


def conversation():
    return model_client.start_conversation("some-model", "You are a builder.", [READ_FILE], "Build it.", timeout_ms=1234)


def test_a_gemini_tool_call_becomes_a_neutral_reply(gemini):
    gemini.replies = [gemini_reply(tool_part())]
    chat = conversation()

    reply = chat.record(chat.send("some-model"))

    assert reply.text == ""
    assert reply.calls == [ToolCall(id="call-1", name="read_file", args={"path": "app/main.py"})]


def test_a_gemini_text_answer_has_no_calls(gemini):
    gemini.replies = [gemini_reply(types.Part(text="All done."))]
    chat = conversation()

    reply = chat.record(chat.send("some-model"))

    assert reply.text == "All done."
    assert reply.calls == []


def test_a_tool_call_without_arguments_gets_an_empty_dict(gemini):
    gemini.replies = [gemini_reply(types.Part(function_call=types.FunctionCall(id="c", name="run_tests")))]
    chat = conversation()

    assert chat.record(chat.send("some-model")).calls[0].args == {}


def test_the_request_carries_the_model_prompt_tools_and_timeout(gemini):
    gemini.replies = [gemini_reply(types.Part(text="ok"))]
    chat = conversation()

    chat.send("some-model")

    request = gemini.requests[0]
    assert request.model == "some-model"
    assert request.config.system_instruction == "You are a builder."
    assert request.config.automatic_function_calling.disable is True  # our loop runs the tools, not the SDK
    declaration = request.config.tools[0].function_declarations[0]
    assert (declaration.name, declaration.description) == ("read_file", "Read a file.")
    assert declaration.parameters_json_schema == READ_FILE.parameters
    assert gemini.client_settings[0]["http_options"].timeout == 1234
    assert request.contents[0].role == "user" and request.contents[0].parts[0].text == "Build it."


def test_the_models_turn_goes_back_exactly_as_it_came(gemini):
    """Gemini attaches a thought signature to tool calls and rejects a later request that loses it."""
    gemini.replies = [gemini_reply(tool_part(signature=b"keep-me")), gemini_reply(types.Part(text="ok"))]
    chat = conversation()
    reply = chat.record(chat.send("some-model"))
    chat.add_tool_results([(reply.calls[0], "file contents")])

    chat.send("some-model")

    assert gemini.requests[1].contents[1].parts[0].thought_signature == b"keep-me"


def test_tool_results_return_with_the_same_id_and_name(gemini):
    gemini.replies = [gemini_reply(tool_part("first"), tool_part("second", args={"path": "b.py"})),
                      gemini_reply(types.Part(text="ok"))]
    chat = conversation()
    reply = chat.record(chat.send("some-model"))

    chat.add_tool_results([(reply.calls[0], "A"), (reply.calls[1], "B")])
    chat.send("some-model")

    turn = gemini.requests[1].contents[-1]
    assert turn.role == "user"
    assert [(p.function_response.id, p.function_response.name, p.function_response.response) for p in turn.parts] == [
        ("first", "read_file", {"result": "A"}), ("second", "read_file", {"result": "B"})]


def test_feedback_is_added_as_a_user_turn(gemini):
    gemini.replies = [gemini_reply(types.Part(text="Finished.")), gemini_reply(types.Part(text="ok"))]
    chat = conversation()
    chat.record(chat.send("some-model"))

    chat.add_text("Not done: 2 tests fail.")
    chat.send("some-model")

    turn = gemini.requests[1].contents[-1]
    assert turn.role == "user" and turn.parts[0].text == "Not done: 2 tests fail."


def test_a_failed_request_adds_nothing_to_the_conversation(gemini):
    gemini.replies = [RuntimeError("503 UNAVAILABLE"), gemini_reply(types.Part(text="ok"))]
    chat = conversation()

    with pytest.raises(RuntimeError):
        chat.send("some-model")
    chat.send("some-model")

    assert len(gemini.requests[1].contents) == 1


def test_the_agent_loop_contains_no_provider_code():
    """Every provider lives behind model_client, so adding one never means editing the agent loop."""
    source = Path(agent.__file__).read_text()

    imported = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)

    assert not [name for name in imported if name.split(".")[0] in {"google", "openai", "anthropic"}]


@pytest.mark.parametrize("menu", [agent.TOOL_MENU, agent.REVIEWABLE_TOOL_MENU, agent.REVIEW_TOOL_MENU])
def test_every_agent_tool_is_a_neutral_spec(menu):
    assert all(isinstance(tool, ToolSpec) and tool.parameters["type"] == "object" for tool in menu)


# ---- Choosing a provider: "provider/model" ----

@pytest.mark.parametrize("name, expected", [
    ("gemini-3.5-flash-lite", ("gemini", "gemini-3.5-flash-lite")),
    ("gemini/gemini-3.8-flash", ("gemini", "gemini-3.8-flash")),
    ("openai/some-model", ("openai", "some-model")),
    ("groq/openai/gpt-oss-120b", ("groq", "openai/gpt-oss-120b")),
    ("openrouter/qwen/some-model:free", ("openrouter", "qwen/some-model:free")),
    ("cerebras/some-model", ("cerebras", "some-model")),
])
def test_a_model_name_says_which_provider_runs_it(name, expected):
    assert model_client.split_model(name) == expected


@pytest.mark.parametrize("name", ["nobody/some-model", "groq/", "gemini/", "/some-model"])
def test_an_unknown_provider_is_refused(name):
    with pytest.raises(ValueError, match="provider|model"):
        model_client.split_model(name)


# ---- The OpenAI-style adapter: OpenAI, Groq, OpenRouter, Cerebras ----

def api_reply(content=None, tool_calls=None, **extra):
    message = {"role": "assistant", **extra}
    if content is not None:
        message["content"] = content
    if tool_calls:
        message["tool_calls"] = tool_calls
    return httpx2.Response(200, json={"choices": [{"message": message}]})


def api_call(call_id="call-1", name="read_file", arguments='{"path": "app/main.py"}'):
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}}


@pytest.fixture
def api(monkeypatch):
    """A fake OpenAI-style API. Put responses (or exceptions) in .replies; each POST is recorded in .requests."""
    fake = SimpleNamespace(replies=[], requests=[])

    def post(url, headers, json, timeout):
        fake.requests.append(SimpleNamespace(url=url, headers=headers, timeout=timeout, body=deepcopy(json)))
        reply = fake.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(model_client.httpx2, "post", post)
    for name in ("OPENAI_API_KEY", "GROQ_API_KEY", "OPEN_ROUTER_API_KEY", "CEREBRAS_API_KEY"):
        monkeypatch.setenv(name, f"secret-{name}")
    return fake


def groq_chat():
    return model_client.start_conversation("groq/test-model", "You are a builder.", [READ_FILE], "Build it.",
                                           timeout_ms=1500)


@pytest.mark.parametrize("provider, url, key", [
    ("openai", "https://api.openai.com/v1/chat/completions", "OPENAI_API_KEY"),
    ("groq", "https://api.groq.com/openai/v1/chat/completions", "GROQ_API_KEY"),
    ("openrouter", "https://openrouter.ai/api/v1/chat/completions", "OPEN_ROUTER_API_KEY"),
    ("cerebras", "https://api.cerebras.ai/v1/chat/completions", "CEREBRAS_API_KEY"),
])
def test_each_provider_gets_its_own_url_and_key(api, provider, url, key):
    api.replies = [api_reply("ok")]
    chat = model_client.start_conversation(f"{provider}/vendor/test-model", "S", [READ_FILE], "Go", timeout_ms=1500)

    chat.send(f"{provider}/vendor/test-model")

    request = api.requests[0]
    assert request.url == url
    assert request.headers["Authorization"] == f"Bearer secret-{key}"
    assert request.body["model"] == "vendor/test-model"
    assert request.timeout == 1.5


def test_the_request_uses_the_openai_format(api):
    api.replies = [api_reply("ok")]
    chat = groq_chat()

    chat.send("groq/test-model")

    body = api.requests[0].body
    assert body["messages"] == [{"role": "system", "content": "You are a builder."},
                                {"role": "user", "content": "Build it."}]
    assert body["tools"] == [{"type": "function", "function": {
        "name": "read_file", "description": "Read a file.", "parameters": READ_FILE.parameters}}]


def test_a_missing_key_is_a_clear_error_before_any_request(api, monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY")

    with pytest.raises(RuntimeError, match="GROQ_API_KEY"):
        groq_chat()

    assert api.requests == []


def test_an_api_tool_call_becomes_a_neutral_reply(api):
    api.replies = [api_reply(tool_calls=[api_call()])]
    chat = groq_chat()

    reply = chat.record(chat.send("groq/test-model"))

    assert reply.text == ""
    assert reply.calls == [ToolCall(id="call-1", name="read_file", args={"path": "app/main.py"})]


def test_an_api_text_answer_has_no_calls(api):
    api.replies = [api_reply("All done.")]
    chat = groq_chat()

    reply = chat.record(chat.send("groq/test-model"))

    assert (reply.text, reply.calls) == ("All done.", [])


def test_tool_results_follow_the_turn_that_asked_for_them(api):
    calls = [api_call("first"), api_call("second", arguments='{"path": "b.py"}')]
    api.replies = [api_reply(tool_calls=calls), api_reply("ok")]
    chat = groq_chat()
    reply = chat.record(chat.send("groq/test-model"))

    chat.add_tool_results([(reply.calls[0], "A"), (reply.calls[1], "B")])
    chat.add_text("Keep going.")
    chat.send("groq/test-model")

    assert api.requests[1].body["messages"][2:] == [
        {"role": "assistant", "content": "", "tool_calls": calls},
        {"role": "tool", "tool_call_id": "first", "content": "A"},
        {"role": "tool", "tool_call_id": "second", "content": "B"},
        {"role": "user", "content": "Keep going."},
    ]


def test_private_reasoning_stays_out_of_the_history_but_openrouter_details_stay_in(api):
    api.replies = [api_reply("Thinking done.", reasoning="private notes", reasoning_details=[{"type": "kept"}]),
                   api_reply("ok")]
    chat = groq_chat()
    chat.record(chat.send("groq/test-model"))

    chat.send("groq/test-model")

    turn = api.requests[1].body["messages"][2]
    assert "reasoning" not in turn
    assert turn["reasoning_details"] == [{"type": "kept"}]


@pytest.mark.parametrize("arguments", ['{"path": ', "[1, 2]", '"text"'])
def test_bad_tool_arguments_become_an_error_for_the_model(api, arguments):
    api.replies = [api_reply(tool_calls=[api_call(arguments=arguments)]), api_reply("ok")]
    chat = groq_chat()

    call = chat.record(chat.send("groq/test-model")).calls[0]
    chat.send("groq/test-model")

    assert call.args == {} and "JSON" in call.error
    assert api.requests[1].body["messages"][2]["tool_calls"][0]["function"]["arguments"] == "{}"


@pytest.mark.parametrize("arguments", ["", None])
def test_a_tool_without_arguments_is_fine(api, arguments):
    api.replies = [api_reply(tool_calls=[api_call(name="run_tests", arguments=arguments)])]
    chat = groq_chat()

    call = chat.record(chat.send("groq/test-model")).calls[0]

    assert call.args == {} and call.error is None


def test_an_http_error_keeps_its_status_hides_the_key_and_changes_nothing(api):
    api.replies = [httpx2.Response(429, text="limit reached for secret-GROQ_API_KEY"), api_reply("ok")]
    chat = groq_chat()

    with pytest.raises(RuntimeError) as error:
        chat.send("groq/test-model")
    chat.send("groq/test-model")

    assert "429" in str(error.value) and "secret-GROQ_API_KEY" not in str(error.value)
    assert len(api.requests[1].body["messages"]) == 2


def test_an_error_inside_a_200_response_is_still_an_error(api):
    """OpenRouter can answer 200 with {"error": ...} when the upstream provider fails."""
    api.replies = [httpx2.Response(200, json={"error": {"code": 503, "message": "upstream failed"}})]
    chat = groq_chat()

    with pytest.raises(RuntimeError, match="503|upstream"):
        chat.send("groq/test-model")


# ---- OpenRouter: one key, many models, many hosts ----

def openrouter_chat(model="openrouter/deepseek/test-model"):
    return model_client.start_conversation(model, "S", [READ_FILE], "Go", timeout_ms=1500)


def test_openrouter_only_uses_hosts_with_tools_and_caps_output(api):
    api.replies = [api_reply("ok")]
    chat = openrouter_chat()

    chat.send("openrouter/deepseek/test-model")

    body = api.requests[0].body
    assert body["model"] == "deepseek/test-model"
    assert body["provider"] == {"require_parameters": True}  # otherwise a host may silently ignore our tools
    assert body["max_tokens"] == model_client.OPENROUTER_MAX_OUTPUT


def test_openrouter_can_pin_one_host_with_no_fallback(api):
    model = "openrouter/deepseek/test-model@deepinfra"
    api.replies = [api_reply("ok")]
    chat = openrouter_chat(model)

    chat.send(model)

    body = api.requests[0].body
    assert body["model"] == "deepseek/test-model"
    assert body["provider"] == {"require_parameters": True, "order": ["deepinfra"], "allow_fallbacks": False}


def test_other_providers_get_no_openrouter_settings(api):
    model = "groq/vendor/test-model@keep"
    api.replies = [api_reply("ok")]
    chat = model_client.start_conversation(model, "S", [READ_FILE], "Go", timeout_ms=1500)

    chat.send(model)

    body = api.requests[0].body
    assert body["model"] == "vendor/test-model@keep"
    assert "provider" not in body and "max_tokens" not in body


def test_an_error_on_the_choice_is_an_error_and_keeps_no_partial_text(api):
    """OpenRouter can answer 200 with partial content and an error next to it."""
    partial = httpx2.Response(200, json={"choices": [{
        "message": {"role": "assistant", "content": "x" * 500}, "finish_reason": "error",
        "error": {"code": 502, "message": "Provider disconnected"}}]})
    api.replies = [partial, api_reply("ok")]
    chat = openrouter_chat()

    with pytest.raises(RuntimeError, match="502"):
        chat.send("openrouter/deepseek/test-model")
    chat.send("openrouter/deepseek/test-model")

    assert len(api.requests[1].body["messages"]) == 2


# ---- Cut-off replies ----

def cut_off_reply(**message):
    return httpx2.Response(200, json={"choices": [{"finish_reason": "length", "message": {"role": "assistant", **message}}]})


def test_a_cut_off_tool_call_tells_the_model_what_happened(api):
    api.replies = [cut_off_reply(tool_calls=[api_call(name="write_file", arguments='{"path": "app/main.py", "content": "def')])]
    chat = groq_chat()

    reply = chat.record(chat.send("groq/test-model"))

    assert reply.cut_off is True
    assert "cut off" in reply.calls[0].error and "smaller" in reply.calls[0].error


def test_a_complete_call_in_a_cut_off_reply_can_still_run(api):
    api.replies = [cut_off_reply(tool_calls=[api_call("first"), api_call("second", arguments='{"path": ')])]
    chat = groq_chat()

    calls = chat.record(chat.send("groq/test-model")).calls

    assert calls[0].error is None and "cut off" in calls[1].error


def test_cut_off_text_is_flagged(api):
    api.replies = [cut_off_reply(content="I will now write")]
    chat = groq_chat()

    assert chat.record(chat.send("groq/test-model")).cut_off is True


def test_a_normal_reply_is_not_cut_off(api):
    api.replies = [api_reply("All done.")]
    chat = groq_chat()

    assert chat.record(chat.send("groq/test-model")).cut_off is False


def test_gemini_max_tokens_is_cut_off(gemini):
    candidate = types.Candidate(content=types.Content(role="model", parts=[types.Part(text="partial")]),
                                finish_reason=types.FinishReason.MAX_TOKENS)
    gemini.replies = [types.GenerateContentResponse(candidates=[candidate])]
    chat = conversation()

    assert chat.record(chat.send("some-model")).cut_off is True


def test_an_empty_gemini_turn_never_breaks_the_next_request(gemini):
    empty = types.GenerateContentResponse(candidates=[types.Candidate(finish_reason=types.FinishReason.MAX_TOKENS)])
    gemini.replies = [empty, gemini_reply(types.Part(text="ok"))]
    chat = conversation()
    reply = chat.record(chat.send("some-model"))

    chat.add_text("Your reply was cut off.")
    chat.send("some-model")

    assert reply.cut_off is True and reply.calls == []
    model_turn = gemini.requests[1].contents[1]
    assert model_turn.role == "model" and model_turn.parts


def test_a_gemini_reply_with_no_candidates_is_a_retryable_failure(gemini):
    gemini.replies = [types.GenerateContentResponse(candidates=[]), gemini_reply(types.Part(text="ok"))]
    chat = conversation()

    with pytest.raises(RuntimeError, match="no reply"):
        chat.send("some-model")
    chat.record(chat.send("some-model"))

    assert len(gemini.requests[1].contents) == 1


# ---- Reading provider errors ----

class GeminiStyleError(Exception):
    code = 503
    status = "UNAVAILABLE"
    details = {"error": {"details": [{"retryDelay": "35784s"}]}}


@pytest.mark.parametrize("error, status", [
    (RuntimeError("503 UNAVAILABLE"), 503),
    (RuntimeError("429 RESOURCE_EXHAUSTED. Quota"), 429),
    (RuntimeError("quota RESOURCE_EXHAUSTED"), 429),
    (RuntimeError("500 from openrouter: request id 84291"), 500),
    (RuntimeError("connection reset"), None),
    (model_client.ProviderError(413, "from groq: too large"), 413),
    (GeminiStyleError("whatever"), 503),
])
def test_the_status_is_read_from_the_error(error, status):
    assert model_client.error_status(error) == status


def test_retry_after_is_read_from_the_response(api):
    api.replies = [httpx2.Response(429, headers={"Retry-After": "7"}, text="slow down")]
    chat = groq_chat()

    with pytest.raises(model_client.ProviderError) as error:
        chat.send("groq/test-model")

    assert error.value.status == 429 and error.value.retry_after == 7


def test_an_unreadable_retry_after_is_ignored(api):
    api.replies = [httpx2.Response(429, headers={"Retry-After": "Wed, 21 Oct 2026"}, text="slow down")]
    chat = groq_chat()

    with pytest.raises(model_client.ProviderError) as error:
        chat.send("groq/test-model")

    assert error.value.retry_after is None


def test_geminis_retry_delay_is_read():
    assert model_client.retry_delay(GeminiStyleError("x")) == 35784
    assert model_client.retry_delay(RuntimeError("x")) is None


def test_an_error_inside_a_200_keeps_its_real_status(api):
    api.replies = [httpx2.Response(200, json={"error": {"code": 429, "message": "busy"}})]
    chat = groq_chat()

    with pytest.raises(model_client.ProviderError) as error:
        chat.send("groq/test-model")

    assert error.value.status == 429


@pytest.mark.parametrize("error, permanent", [
    (model_client.ProviderError(400, "x"), True),
    (model_client.ProviderError(401, "x"), True),
    (model_client.ProviderError(402, "x"), True),
    (model_client.ProviderError(402, "x", retry_after=3), False),
    (model_client.ProviderError(403, "x"), True),
    (model_client.ProviderError(404, "x"), True),
    (model_client.ProviderError(413, "x"), True),
    (model_client.ProviderError(422, "x"), True),
    (model_client.ProviderError(408, "x"), False),
    (model_client.ProviderError(429, "x"), False),
    (model_client.ProviderError(500, "x"), False),
    (model_client.ProviderError(503, "x"), False),
    (RuntimeError("connection reset"), False),
])
def test_permanent_errors_are_recognized(error, permanent):
    assert model_client.is_permanent(error) is permanent


# ---- Token and cost tracking ----

def test_gemini_usage_is_read_including_thinking_tokens(gemini):
    reply = gemini_reply(types.Part(text="ok"))
    reply.usage_metadata = types.GenerateContentResponseUsageMetadata(
        prompt_token_count=100, cached_content_token_count=40, candidates_token_count=10, thoughts_token_count=5)
    gemini.replies = [reply]
    chat = conversation()

    usage = chat.record(chat.send("some-model")).usage

    assert usage == Usage(input_tokens=100, cached_tokens=40, output_tokens=15, cost_usd=None)


def test_a_gemini_reply_without_usage_is_fine(gemini):
    gemini.replies = [gemini_reply(types.Part(text="ok"))]
    chat = conversation()

    assert chat.record(chat.send("some-model")).usage is None


def test_openai_style_usage_includes_cached_tokens_and_cost(api):
    response = api_reply("ok")
    data = response.json()
    data["usage"] = {"prompt_tokens": 200, "completion_tokens": 20, "cost": 0.0012,
                     "prompt_tokens_details": {"cached_tokens": 150}}
    api.replies = [httpx2.Response(200, json=data)]
    chat = groq_chat()

    usage = chat.record(chat.send("groq/test-model")).usage

    assert usage == Usage(input_tokens=200, cached_tokens=150, output_tokens=20, cost_usd=0.0012)


def test_openai_style_reply_without_usage_is_fine(api):
    api.replies = [api_reply("ok")]
    chat = groq_chat()

    assert chat.record(chat.send("groq/test-model")).usage is None


def test_the_meter_adds_up_a_whole_project():
    meter = UsageMeter()
    meter.add("cheap", Usage(100, 40, 10, 0.001), "tester")
    meter.add("cheap", Usage(300, 200, 20, 0.002), "builder")
    meter.failed()

    totals = meter.totals()

    assert (totals["requests"], totals["failed_attempts"]) == (2, 1)
    assert (totals["input_tokens"], totals["cached_tokens"], totals["output_tokens"]) == (400, 240, 30)
    assert totals["cost_usd"] == pytest.approx(0.003) and totals["cost_complete"] is True
    assert totals["largest_request"] == 320
    assert totals["by_stage"]["tester"]["input_tokens"] == 100
    assert totals["steps"][1] == {"stage": "builder", "model": "cheap", "input": 300, "cached": 200, "output": 20,
                                  "cost": 0.002}


def test_the_meter_says_when_a_cost_is_unknown():
    meter = UsageMeter()
    meter.add("priced", Usage(10, 0, 1, 0.001), "builder")
    meter.add("free", Usage(10, 0, 1, None), "builder")
    meter.add("silent", None, "builder")

    totals = meter.totals()

    assert totals["cost_usd"] == pytest.approx(0.001) and totals["cost_complete"] is False
    assert totals["requests"] == 3


# ---- The real agent loop running on an OpenAI-style provider ----

@pytest.fixture
def workspace(tmp_path):
    ws = tmp_path / "workspace"
    shutil.copytree(agent.TEMPLATE, ws, ignore=shutil.ignore_patterns("__pycache__"))
    return ws


def test_the_agent_loop_runs_tools_on_an_openai_style_provider(api, workspace):
    api.replies = [api_reply(tool_calls=[api_call(arguments='{"path": "app/main.py"}')]), api_reply("Finished.")]
    steps = []

    agent.run_agent(workspace, on_step=steps.append, model="groq/test-model", max_steps=2,
                    executor=LocalExecutor(workspace))

    assert steps[0]["tool"] == "read_file" and "FastAPI" in steps[0]["result"]
    tool_turn = api.requests[1].body["messages"][3]
    assert tool_turn["role"] == "tool" and "FastAPI" in tool_turn["content"]


def test_bad_arguments_never_run_the_tool(api, workspace):
    api.replies = [api_reply(tool_calls=[api_call(name="write_file", arguments='{"path": "app/main.py", "content"')]),
                   api_reply("Finished.")]
    before = (workspace / "app" / "main.py").read_text()
    steps = []

    agent.run_agent(workspace, on_step=steps.append, model="groq/test-model", max_steps=2,
                    executor=LocalExecutor(workspace))

    assert steps[0]["result"].startswith("ERROR") and "JSON" in steps[0]["result"]
    assert (workspace / "app" / "main.py").read_text() == before


def test_a_failing_provider_never_falls_back_to_a_different_provider(api, workspace, monkeypatch):
    """A conversation's history is in one provider's format, so it can't move to Gemini halfway through."""
    monkeypatch.setattr(agent.time, "sleep", lambda seconds: None)
    api.replies = [httpx2.Response(503, text="overloaded"), api_reply("Finished.")]

    outcome = agent.run_agent(workspace, model="groq/test-model", max_steps=1, executor=LocalExecutor(workspace))

    assert [request.url for request in api.requests] == ["https://api.groq.com/openai/v1/chat/completions"] * 2
    assert outcome["model"] == "groq/test-model"


def test_an_error_message_names_the_model_not_a_provider(api, workspace, monkeypatch):
    monkeypatch.setattr(agent.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(agent, "MAX_ATTEMPTS", 2)
    api.replies = [httpx2.Response(503, text="down"), httpx2.Response(503, text="down")]

    with pytest.raises(RuntimeError, match="groq/test-model unavailable"):
        agent.run_agent(workspace, model="groq/test-model", max_steps=1, executor=LocalExecutor(workspace))
