"""Tests for orchestrator/decomposer.py, using fake LLM functions (no network, no API key)."""
import pytest

import orchestration  # noqa: F401  (puts the repo root on the import path, as the backend does)
from orchestrator import decomposer

VALID_REPLY = (
    '[{"name": "add function", "description": "adds two numbers", '
    '"function_name": "add", "test_input": [2, 3], "expected": 5}]'
)


def test_without_an_llm_returns_the_sample_module():
    modules = decomposer.decompose("Build me a workout tracker", "balanced")

    assert [m["name"] for m in modules] == ["add function"]


def test_sample_module_is_a_fresh_copy_each_time():
    first = decomposer.decompose("anything")
    first[0]["name"] = "changed"

    assert decomposer.decompose("anything")[0]["name"] == "add function"


def test_llm_receives_a_prompt_with_the_description():
    prompts = []

    def recording_llm(prompt):
        prompts.append(prompt)
        return VALID_REPLY

    decomposer.decompose("Build me a recipe app", "speed", llm=recording_llm)

    assert "Build me a recipe app" in prompts[0]


def test_valid_llm_reply_becomes_modules():
    modules = decomposer.decompose("anything", llm=lambda prompt: VALID_REPLY)

    assert modules[0]["function_name"] == "add"
    assert modules[0]["status"] == "waiting"


def test_json_list_is_found_inside_extra_text():
    modules = decomposer.decompose("anything", llm=lambda prompt: "Here you go:\n" + VALID_REPLY + "\nThanks!")

    assert modules[0]["function_name"] == "add"


def test_reply_without_a_json_list_raises():
    with pytest.raises(ValueError):
        decomposer.decompose("anything", llm=lambda prompt: "sorry, I can't do that")


def test_module_missing_a_field_raises():
    reply = '[{"name": "x", "description": "y", "function_name": "z", "test_input": [1]}]'

    with pytest.raises(ValueError, match="expected"):
        decomposer.decompose("anything", llm=lambda prompt: reply)


def test_llm_errors_are_raised_not_hidden():
    def broken_llm(prompt):
        raise TimeoutError("API took too long")

    with pytest.raises(TimeoutError):
        decomposer.decompose("anything", llm=broken_llm)
