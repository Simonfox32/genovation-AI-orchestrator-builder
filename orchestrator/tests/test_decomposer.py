import copy

import pytest

from decomposer import (
    FALLBACK_MODULES,
    decompose,
    extract_json,
    fake_llm,
    parse_modules,
    validate_module,
)


def test_decompose_without_llm_returns_fallback():
    modules = decompose("build a calculator")
    assert modules[0]["name"] == "add function"
    assert modules[0]["function_name"] == "add"
    assert modules[0]["status"] == "waiting"


def test_decompose_without_llm_does_not_mutate_the_shared_fallback():
    # decompose() should hand back a copy, not the real FALLBACK_MODULES list --
    # otherwise one test (or one caller) editing its result would corrupt it for everyone else.
    modules = decompose("build a calculator")
    modules[0]["status"] = "changed"
    assert FALLBACK_MODULES[0]["status"] == "waiting"


def test_decompose_with_llm_parses_reply():
    modules = decompose("build a calculator", llm=fake_llm)
    assert modules[0]["function_name"] == "add"


def test_parse_modules_handles_messy_reply_with_extra_text():
    messy = "Here you go:\n" + fake_llm("") + "\nHope that helps!"
    modules = parse_modules(messy)
    assert modules[0]["function_name"] == "add"


def test_decompose_raises_when_llm_reply_has_no_json_list():
    def bad_llm(prompt):
        return "sorry, I can't do that"

    with pytest.raises(ValueError):
        decompose("x", llm=bad_llm)


def test_decompose_raises_when_llm_errors():
    def broken_llm(prompt):
        raise TimeoutError("API took too long")

    with pytest.raises(TimeoutError):
        decompose("x", llm=broken_llm)


def test_parse_modules_rejects_module_missing_required_field():
    bad_reply = '[{"name": "x", "description": "y", "function_name": "z", "test_input": [1]}]'
    with pytest.raises(ValueError):
        parse_modules(bad_reply)


def test_validate_module_sets_status_to_waiting():
    module = {
        "name": "x",
        "description": "y",
        "function_name": "z",
        "test_input": [1],
        "expected": 1,
    }
    validated = validate_module(module)
    assert validated["status"] == "waiting"


def test_extract_json_finds_list_surrounded_by_other_text():
    text = "sure, here's the plan:\n" + fake_llm("") + "\nlet me know if you need anything else"
    extracted = extract_json(text)
    assert extracted.startswith("[") and extracted.endswith("]")