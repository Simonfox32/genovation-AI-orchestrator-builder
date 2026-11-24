"""The one place that knows how to talk to an AI provider.

The agent loop (agent.py) only ever does this:

    chat = start_conversation("groq/openai/gpt-oss-120b", system_prompt, tools, first_message, timeout_ms=60_000)
    reply = chat.record(chat.send("groq/openai/gpt-oss-120b"))    # reply.text, reply.calls
    chat.add_tool_results([(call, "what the tool returned"), ...])
    chat.add_text("Not done: 2 tests still fail.")

A model name is "provider/model". A bare name, like "gemini-3.5-flash-lite", means Gemini.
OpenRouter can serve one model from many hosts: "openrouter/vendor/model@deepinfra" pins it to one host.

Each provider formats messages and tool calls differently, so each gets a small conversation class that translates
to and from these neutral shapes. Adding a provider means adding a class here, with no change to the agent loop.
Providers that copy OpenAI's chat format only need one line in OPENAI_STYLE.

send() and record() are separate so a failed request can be retried without touching the conversation.
This file isn't called models.py because backend/models.py already owns that import name.
"""
import json
import os
import re
from dataclasses import dataclass

import httpx2
from google import genai
from google.genai import types

# provider -> (API base URL, the environment variable holding its key)
OPENAI_STYLE = {
    "openai": ("https://api.openai.com/v1", "OPENAI_API_KEY"),
    "groq": ("https://api.groq.com/openai/v1", "GROQ_API_KEY"),
    "openrouter": ("https://openrouter.ai/api/v1", "OPEN_ROUTER_API_KEY"),
    "cerebras": ("https://api.cerebras.ai/v1", "CEREBRAS_API_KEY"),
}
OPENROUTER_MAX_OUTPUT = 16_000  # most tokens one reply may use: limits a runaway reply's cost on a prepaid key
DEFAULT_MAX_PROJECT_COST_USD = 0.50  # one build stops here. Override with MAX_PROJECT_COST_USD
DEFAULT_MAX_PROJECT_TOKENS = 3_000_000  # for providers that don't report a price. Override with MAX_PROJECT_TOKENS
PERMANENT_STATUSES = {400, 401, 403, 404, 413, 422}  # sending the same request again can't fix these
CUT_OFF_CALL = ("Your reply was cut off before this tool call was complete, so it did not run. "
                "Write less at once: use smaller files.")


class ProviderError(RuntimeError):
    """An error answer from a provider, keeping the facts the retry logic needs."""

    def __init__(self, status, message, retry_after=None):
        super().__init__(f"{status} {message}")
        self.status, self.retry_after = status, retry_after


class ModelUnavailable(RuntimeError):
    """A build can't continue with this model. `public` is safe to show users: no keys or raw provider text."""

    def __init__(self, public):
        super().__init__(public)
        self.public = public


def error_status(error):
    """The HTTP status behind an error, from the error's fields or the start of its message. Only the start counts:
    a request id containing '429' in the middle of a message is not a rate limit."""
    for field in ("status", "code"):
        value = getattr(error, field, None)
        if isinstance(value, int):
            return value
    match = re.match(r"\s*(\d{3})\b", str(error))
    if match:
        return int(match.group(1))
    return 429 if "RESOURCE_EXHAUSTED" in str(error) else None


def retry_delay(error):
    """How many seconds the provider asked us to wait, or None if it didn't say."""
    value = getattr(error, "retry_after", None)
    if value is not None:
        return value
    try:  # Gemini: {"error": {"details": [{"retryDelay": "35784s"}]}}
        for item in error.details["error"]["details"]:
            if "retryDelay" in item:
                return float(item["retryDelay"].rstrip("s"))
    except (AttributeError, KeyError, TypeError, ValueError):
        pass
    return None


def is_permanent(error):
    """Would retrying the same request be pointless? A 402 with a retry time is OpenRouter's temporary budget check."""
    status = error_status(error)
    if status == 402:
        return retry_delay(error) is None
    return status in PERMANENT_STATUSES


def _retry_after(headers):
    try:
        return float(headers.get("retry-after"))
    except (TypeError, ValueError):
        return None


def split_model(name):
    """'groq/openai/gpt-oss-120b' -> ('groq', 'openai/gpt-oss-120b'). Only the first slash counts, because some
    providers' model ids contain slashes of their own."""
    provider, slash, model = name.partition("/")
    if not slash:
        return "gemini", name
    if provider != "gemini" and provider not in OPENAI_STYLE:
        raise ValueError(f"Unknown model provider '{provider}' in '{name}'")
    if not model:
        raise ValueError(f"No model named after the provider in '{name}'")
    return provider, model


def same_provider(first, second):
    return split_model(first)[0] == split_model(second)[0]


@dataclass(frozen=True)
class ToolSpec:
    """A tool the model may ask for. `parameters` is plain JSON Schema, which every major provider understands."""
    name: str
    description: str
    parameters: dict


@dataclass(frozen=True)
class ToolCall:
    """The model asking us to run one tool. `id` links the result back to this call. `error` means its arguments
    couldn't be read, so the tool must not run and the model should be told why."""
    id: str | None
    name: str
    args: dict
    error: str | None = None


@dataclass(frozen=True)
class Usage:
    """What one model reply used. `cost_usd` is None when the provider doesn't report a price."""
    input_tokens: int = 0
    cached_tokens: int = 0  # the part of input_tokens served from the provider's cache, usually far cheaper
    output_tokens: int = 0
    cost_usd: float | None = None


@dataclass(frozen=True)
class ModelReply:
    """What the model said: some text, and any tools it wants run. No calls means it thinks it's finished."""
    text: str
    calls: list[ToolCall]
    usage: Usage | None = None
    cut_off: bool = False  # the reply hit its length limit, so it may stop mid-sentence or mid-tool-call


class UsageMeter:
    """Adds up what the models used across one project: per request, per stage (tester, builder, review), and total.
    With limits set, check_budget() stops a build before it can spend more."""

    def __init__(self, max_cost_usd=None, max_tokens=None):
        self.steps, self.failed_attempts = [], 0
        self.max_cost_usd, self.max_tokens = max_cost_usd, max_tokens

    @classmethod
    def for_project(cls):
        return cls(max_cost_usd=float(os.getenv("MAX_PROJECT_COST_USD", DEFAULT_MAX_PROJECT_COST_USD)),
                   max_tokens=int(os.getenv("MAX_PROJECT_TOKENS", DEFAULT_MAX_PROJECT_TOKENS)))

    def check_budget(self):
        if self.max_cost_usd is not None and sum(step["cost"] or 0.0 for step in self.steps) >= self.max_cost_usd:
            raise ModelUnavailable(f"This build reached its ${self.max_cost_usd:.2f} spending limit.")
        if self.max_tokens is not None and sum(step["input"] + step["output"] for step in self.steps) >= self.max_tokens:
            raise ModelUnavailable(f"This build reached its {self.max_tokens:,} token limit.")

    def add(self, model, usage, stage):
        used = usage or Usage()
        self.steps.append({"stage": stage, "model": model, "input": used.input_tokens, "cached": used.cached_tokens,
                           "output": used.output_tokens, "cost": used.cost_usd})

    def failed(self):
        """A model call that errored and was retried. Providers normally don't bill these, but they cost time."""
        self.failed_attempts += 1

    def totals(self):
        by_stage = {}
        for step in self.steps:
            stage = by_stage.setdefault(step["stage"], {"requests": 0, "input_tokens": 0, "cached_tokens": 0,
                                                        "output_tokens": 0, "cost_usd": 0.0})
            stage["requests"] += 1
            stage["input_tokens"] += step["input"]
            stage["cached_tokens"] += step["cached"]
            stage["output_tokens"] += step["output"]
            stage["cost_usd"] += step["cost"] or 0.0
        return {
            "requests": len(self.steps),
            "failed_attempts": self.failed_attempts,
            "input_tokens": sum(step["input"] for step in self.steps),
            "cached_tokens": sum(step["cached"] for step in self.steps),
            "output_tokens": sum(step["output"] for step in self.steps),
            "cost_usd": sum(step["cost"] or 0.0 for step in self.steps),
            "cost_complete": bool(self.steps) and all(step["cost"] is not None for step in self.steps),
            "largest_request": max((step["input"] + step["output"] for step in self.steps), default=0),
            "by_stage": by_stage,
            "steps": self.steps,
        }


def _gemini_usage(response):
    usage = getattr(response, "usage_metadata", None)
    if usage is None:
        return None
    return Usage(input_tokens=usage.prompt_token_count or 0, cached_tokens=usage.cached_content_token_count or 0,
                 output_tokens=(usage.candidates_token_count or 0) + (usage.thoughts_token_count or 0))


def _openai_usage(response):
    usage = response.get("usage")
    if not usage:
        return None
    cost = usage.get("cost")  # OpenRouter reports the real price of each request
    return Usage(input_tokens=usage.get("prompt_tokens") or 0,
                 cached_tokens=(usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0,
                 output_tokens=usage.get("completion_tokens") or 0,
                 cost_usd=float(cost) if cost is not None else None)


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
        response = self._client.models.generate_content(model=split_model(model)[1], contents=self._history,
                                                        config=self._config)
        if not response.candidates:  # e.g. blocked by a safety filter: treat it like any other failed request
            raise RuntimeError("Gemini returned no reply")
        return response

    def record(self, response):
        """Add the model's turn to the conversation and return it in the neutral shape."""
        # Keep Gemini's own object: it can carry thought signatures that later requests must send back unchanged.
        candidate = response.candidates[0]
        content = candidate.content
        if content is None or not content.parts:  # an empty turn in the history would make the next request invalid
            content = types.Content(role="model", parts=[types.Part(text="(no reply)")])
        self._history.append(content)
        calls = [ToolCall(id=call.id, name=call.name, args=dict(call.args or {}))
                 for call in response.function_calls or []]
        return ModelReply(text="" if calls else response.text or "", calls=calls, usage=_gemini_usage(response),
                          cut_off=getattr(candidate, "finish_reason", None) == types.FinishReason.MAX_TOKENS)

    def add_tool_results(self, results):
        """results: [(ToolCall, result text), ...], all sent back together as one turn."""
        parts = [types.Part(function_response=types.FunctionResponse(id=call.id, name=call.name,
                                                                     response={"result": result}))
                 for call, result in results]
        self._history.append(types.Content(role="user", parts=parts))

    def add_text(self, text):
        self._history.append(types.Content(role="user", parts=[types.Part(text=text)]))


def _read_tool_call(raw, cut_off=False):
    """OpenAI-style tool arguments arrive as JSON text, and models sometimes write it badly or run out of room."""
    function = raw["function"]
    arguments = function.get("arguments") or "{}"
    try:
        args = json.loads(arguments) if isinstance(arguments, str) else arguments
    except json.JSONDecodeError:
        args = None
    if not isinstance(args, dict):
        return ToolCall(id=raw.get("id"), name=function["name"], args={},
                        error=CUT_OFF_CALL if cut_off else "The tool arguments were not a valid JSON object")
    return ToolCall(id=raw.get("id"), name=function["name"], args=args)


class OpenAIStyleConversation:
    """OpenAI's chat format, which Groq, OpenRouter, Cerebras and many others also speak."""

    def __init__(self, provider, system_prompt, tools, first_message, timeout_ms):
        base_url, key_name = OPENAI_STYLE[provider]
        self._key = os.getenv(key_name)
        if not self._key:
            raise RuntimeError(f"{key_name} is not set, so {provider} models can't be used")
        self._provider = provider
        self._url = f"{base_url}/chat/completions"
        self._timeout = timeout_ms / 1000
        self._tools = [{"type": "function", "function": {"name": tool.name, "description": tool.description,
                                                         "parameters": tool.parameters}} for tool in tools]
        self._messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": first_message}]

    def _failure(self, status, text, retry_after=None):
        """An error that keeps the status and retry time (the retry logic reads them) and can never contain the key."""
        return ProviderError(status, f"from {self._provider}: {text.replace(self._key, '[key]')[:300]}", retry_after)

    def _request_body(self, model):
        name = split_model(model)[1]
        body = {"model": name, "messages": self._messages, "tools": self._tools}
        if self._provider == "openrouter":
            name, _, host = name.partition("@")
            routing = {"require_parameters": True}  # without this, a host that lacks tool calling can still be chosen
            if host:
                routing.update(order=[host], allow_fallbacks=False)
            body.update(model=name, provider=routing, max_tokens=OPENROUTER_MAX_OUTPUT)
        return body

    def send(self, model):
        """One request to the model. Raises if it fails, leaving the conversation unchanged."""
        response = httpx2.post(self._url, headers={"Authorization": f"Bearer {self._key}"}, timeout=self._timeout,
                               json=self._request_body(model))
        if response.status_code != 200:
            raise self._failure(response.status_code, response.text, _retry_after(response.headers))
        data = response.json()
        # OpenRouter can answer 200 with an error at the top level, or on the choice next to partial content.
        choices = data.get("choices") or []
        error = data.get("error") or (choices[0].get("error") if choices else None)
        if error or not choices:
            code = error.get("code") if isinstance(error, dict) else None
            raise self._failure(code if isinstance(code, int) else 502, json.dumps(error) if error else response.text,
                                _retry_after(response.headers))
        return data

    def record(self, response):
        """Add the model's turn to the conversation and return it in the neutral shape."""
        choice = response["choices"][0]
        message = choice["message"]
        cut_off = choice.get("finish_reason") == "length"
        turn = {"role": "assistant", "content": message.get("content") or ""}
        if message.get("reasoning_details"):  # OpenRouter needs these back; other private reasoning stays out
            turn["reasoning_details"] = message["reasoning_details"]
        calls, sent_back = [], []
        for raw in message.get("tool_calls") or []:
            call = _read_tool_call(raw, cut_off)
            calls.append(call)
            # Providers can reject a history containing broken or empty argument text.
            usable = not call.error and raw["function"].get("arguments")
            sent_back.append(raw if usable else {**raw, "function": {**raw["function"], "arguments": "{}"}})
        if sent_back:
            turn["tool_calls"] = sent_back
        self._messages.append(turn)
        return ModelReply(text="" if calls else turn["content"], calls=calls, usage=_openai_usage(response),
                          cut_off=cut_off)

    def add_tool_results(self, results):
        """results: [(ToolCall, result text), ...], one message per tool call."""
        self._messages.extend({"role": "tool", "tool_call_id": call.id, "content": result} for call, result in results)

    def add_text(self, text):
        self._messages.append({"role": "user", "content": text})


def start_conversation(model, system_prompt, tools, first_message, timeout_ms):
    """Open a conversation with whichever provider runs `model`."""
    provider, _ = split_model(model)
    if provider == "gemini":
        return GeminiConversation(system_prompt, tools, first_message, timeout_ms)
    return OpenAIStyleConversation(provider, system_prompt, tools, first_message, timeout_ms)
