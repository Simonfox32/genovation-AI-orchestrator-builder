import pytest

from router import (
    STUB_TOOLS,
    REAL_TOOLS,
    add_module,
    multiply_module,
    check,
    clean_code,
    get_tools,
    matches,
    route,
)


# ---- route() across optimization tiers ----

def test_route_cheap_tries_all_three_stub_tools():
    assert route(add_module, "cheap", tools=STUB_TOOLS)["tries"] == 3


def test_route_speed_is_the_same_as_cheap():
    assert route(add_module, "speed", tools=STUB_TOOLS)["tries"] == 3


def test_route_balanced_skips_the_first_tool():
    assert route(add_module, "balanced", tools=STUB_TOOLS)["tries"] == 2


def test_route_quality_only_tries_the_strong_tool():
    assert route(add_module, "quality", tools=STUB_TOOLS)["tries"] == 1


def test_route_reports_failed_status_when_no_tool_passes():
    result = route(multiply_module, "quality", tools=STUB_TOOLS)
    assert result["status"] == "failed"
    assert result["tool_used"] is None


def test_route_with_no_tools_given_defaults_to_stub_tools():
    # No tools= argument and ROUTER_TOOLS unset -> get_tools() falls back to STUB_TOOLS.
    result = route(add_module, "quality")
    assert result["tool_used"] == "strong_tool"
    assert result["status"] == "passed"


# ---- a failing tool counts as a failed attempt, not a crash ----

def test_a_tool_that_raises_counts_as_one_failed_attempt():
    def broken_tool(module):
        raise TimeoutError("API took too long")

    result = route(add_module, "cheap", tools=[broken_tool, STUB_TOOLS[2], STUB_TOOLS[2]])
    assert result["tries"] == 2
    assert result["status"] == "passed"


# ---- clean_code ----

def test_clean_code_strips_a_python_labeled_fence():
    code = "```python\ndef add(a, b):\n    return a + b\n```"
    assert clean_code(code) == "def add(a, b):\n    return a + b"


def test_clean_code_strips_a_bare_fence():
    code = "```\ndef add(a, b):\n    return a + b\n```"
    assert clean_code(code) == "def add(a, b):\n    return a + b"


def test_clean_code_leaves_unfenced_code_alone():
    code = "def add(a, b):\n    return a + b"
    assert clean_code(code) == code


# ---- matches() edge cases ----

def test_matches_exact_equal_values():
    assert matches(5, 5)


def test_matches_floats_within_tolerance():
    assert matches(5.001, 5.0)


def test_matches_floats_outside_tolerance_fail():
    assert not matches(5.5, 5.0)


def test_matches_tuple_equals_list():
    assert matches((2, 3), [2, 3])


def test_matches_bool_is_not_treated_as_int():
    # In plain Python, 1 == True. matches() deliberately rejects that, since a function
    # returning 1 shouldn't be accepted as a correct boolean result, or vice versa.
    assert not matches(True, 1)
    assert not matches(1, True)


def test_matches_nested_dicts():
    assert matches({"a": 1, "b": [2, 3]}, {"a": 1, "b": [2, 3]})


# ---- check() ----

def test_check_returns_true_for_correct_code():
    code = "def add(a, b):\n    return a + b"
    assert check(code, add_module) is True


def test_check_returns_false_for_wrong_code():
    code = "def add(a, b):\n    return a - b"
    assert check(code, add_module) is False


def test_check_returns_false_instead_of_raising_on_broken_code():
    code = "def add(a, b):\n    raise ValueError('oops')"
    assert check(code, add_module) is False


# ---- get_tools() respects ROUTER_TOOLS ----

def test_get_tools_defaults_to_stub_tools(monkeypatch):
    monkeypatch.delenv("ROUTER_TOOLS", raising=False)
    assert get_tools() is STUB_TOOLS


def test_get_tools_returns_real_tools_when_env_var_is_set(monkeypatch):
    monkeypatch.setenv("ROUTER_TOOLS", "real")
    assert get_tools() is REAL_TOOLS

def test_route_raises_on_unknown_optimization_setting():
    with pytest.raises(KeyError):
        route(add_module, "nonsense_setting", tools=STUB_TOOLS)