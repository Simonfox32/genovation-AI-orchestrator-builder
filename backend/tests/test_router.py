"""Tests for the router's checking and prompt (orchestrator/router.py). No API calls."""
import pytest

import orchestration
from orchestrator import router


ONE_REP_MAX = {
    "name": "Estimate one-rep max",
    "description": "Epley formula: weight * (1 + reps / 30)",
    "function_name": "estimate_one_rep_max",
    "test_input": [100, 10],
    "expected": 133.33,
}


@pytest.mark.parametrize(
    "actual, expected",
    [
        (133.3333, 133.33),          # JSON plans round floats
        (5, 5.0),
        (("Squat", 200, 5), ["Squat", 200, 5]),  # JSON has no tuples
        ({"Bench": 1840.0}, {"Bench": 1840}),
        ("hello", "hello"),
        (True, True),
    ],
)
def test_matches_accepts_equal_values(actual, expected):
    assert router.matches(actual, expected)


@pytest.mark.parametrize(
    "actual, expected",
    [
        (133.4, 133.33),
        (1, True),                    # a number is not a yes/no answer
        (["a", "b"], ["a"]),
        ({"a": 1, "b": 2}, {"a": 1}),
        ("Hello", "hello"),
        (None, 0),
    ],
)
def test_matches_rejects_different_values(actual, expected):
    assert not router.matches(actual, expected)


def test_check_passes_code_with_float_rounding_difference():
    code = "def estimate_one_rep_max(weight, reps):\n    return weight * (1 + reps / 30)"

    assert router.check(code, ONE_REP_MAX)


def test_check_fails_wrong_code():
    code = "def estimate_one_rep_max(weight, reps):\n    return weight"

    assert not router.check(code, ONE_REP_MAX)


def test_code_prompt_shows_the_example_it_will_be_checked_against():
    prompt = router.build_code_prompt(ONE_REP_MAX)

    assert "estimate_one_rep_max(*[100, 10])" in prompt
    assert "133.33" in prompt


def test_plan_llm_asks_gemini_for_json(monkeypatch):
    calls = []

    def fake_call_llm(prompt, **kwargs):
        calls.append(kwargs)
        return "[]"

    import orchestrator.llm
    monkeypatch.setattr(orchestrator.llm, "call_llm", fake_call_llm)
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")

    orchestration.get_llm()("plan this")

    assert calls == [{"json_mode": True}]
