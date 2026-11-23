"""The one place that knows how to talk to an AI provider.

The agent loop (agent.py) only ever does this:

    chat = start_conversation(system_prompt, tools, first_message, timeout_ms=60_000)
    reply = chat.record(chat.send("some-model"))    # reply.text, reply.calls
    chat.add_tool_results([(call, "what the tool returned"), ...])
    chat.add_text("Not done: 2 tests still fail.")

Each provider formats messages and tool calls differently, so each gets a small conversation class that translates
to and from these neutral shapes. Adding a provider means adding a class here, with no change to the agent loop.

send() and record() are separate so a failed request can be retried without touching the conversation.
This file isn't called models.py because backend/models.py already owns that import name.
"""
from dataclasses import dataclass

from google import genai
from google.genai import types


@dataclass(frozen=True)
class ToolSpec:
    """A tool the model may ask for. `parameters` is plain JSON Schema, which every major provider understands."""
    name: str
    description: str
    parameters: dict


@dataclass(frozen=True)
class ToolCall:
    """The model asking us to run one tool. `id` links the result back to this call."""
    id: str | None
    name: str
    args: dict


@dataclass(frozen=True)
class ModelReply:
    """What the model said: some text, and any tools it wants run. No calls means it thinks it's finished."""
    text: str
    calls: list[ToolCall]


class GeminiConversation:
    def __init__(self, system_prompt, tools, first_message, timeout_ms):
        self._client = genai.Client(http_options=types.HttpOptions(timeout=timeout_ms))
        declarations = [types.FunctionDeclaration(name=tool.name, description=tool.description,
                                                  parameters_json_schema=tool.parameters) for tool in tools]
        self._config = types.GenerateContentConfig(
            system_instruction=system_prompt,
            tools=[types.Tool(function_declarations=declarations)],
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),  # our loop runs the tools
        )
        self._history = [types.Content(role="user", parts=[types.Part(text=first_message)])]

    def send(self, model):
        """One request to the model. Raises if it fails, leaving the conversation unchanged."""
        return self._client.models.generate_content(model=model, contents=self._history, config=self._config)

    def record(self, response):
        """Add the model's turn to the conversation and return it in the neutral shape."""
        # Keep Gemini's own object: it can carry thought signatures that later requests must send back unchanged.
        self._history.append(response.candidates[0].content)
        calls = [ToolCall(id=call.id, name=call.name, args=dict(call.args or {}))
                 for call in response.function_calls or []]
        return ModelReply(text="" if calls else response.text or "", calls=calls)

    def add_tool_results(self, results):
        """results: [(ToolCall, result text), ...], all sent back together as one turn."""
        parts = [types.Part(function_response=types.FunctionResponse(id=call.id, name=call.name,
                                                                     response={"result": result}))
                 for call, result in results]
        self._history.append(types.Content(role="user", parts=parts))

    def add_text(self, text):
        self._history.append(types.Content(role="user", parts=[types.Part(text=text)]))


def start_conversation(system_prompt, tools, first_message, timeout_ms):
    """Open a conversation with the configured provider. Gemini is the only one so far."""
    return GeminiConversation(system_prompt, tools, first_message, timeout_ms)
