"""Tests for the provider-neutral model layer (orchestrator/model_client.py). No real model is called."""
import ast
from pathlib import Path
from types import SimpleNamespace

import pytest
from google.genai import types

import orchestration  # noqa: F401  (puts the repo root on the import path)
from orchestrator import agent, model_client
from orchestrator.model_client import ToolCall, ToolSpec

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
    return model_client.start_conversation("You are a builder.", [READ_FILE], "Build it.", timeout_ms=1234)


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
