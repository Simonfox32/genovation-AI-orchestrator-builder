import json
import copy

REQUIRED_FIELDS = ["name", "description", "function_name", "test_input", "expected"]

FALLBACK_MODULES = [
    {
        "name": "add function",
        "description": "adds two numbers",
        "function_name": "add",
        "test_input": (2, 3),
        "expected": 5,
        "status": "waiting",
    }
]

def build_prompt(request):
    return (
        "Break this request into small Python functions.\n"
        "Return ONLY a JSON list. Each item must have these fields: "
        "name, description, function_name, test_input (a list of arguments), expected.\n"
        "Request: " + request
    )

def validate_module(module):
    for field in REQUIRED_FIELDS:
        if field not in module:
            raise ValueError("missing field: " + field)
    module["status"] = "waiting"
    return module

def extract_json(text):
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1 or end < start:
        raise ValueError("no JSON list found in reply")
    return text[start:end + 1]

def parse_modules(text):
    data = json.loads(extract_json(text))
    return [validate_module(m) for m in data]

def fake_llm(prompt):
    return '[{"name": "add function", "description": "adds two numbers", "function_name": "add", "test_input": [2, 3], "expected": 5}]'

def decompose(description, optimization="balanced", llm=None):
    """Turn a project description into modules for the router.

    Matches the backend's call: decompose(description, optimization). `optimization` isn't used yet.
    With no `llm`, returns the fixed sample module. With an `llm`, errors are raised, not hidden,
    so the backend can mark the project as failed instead of showing a fake plan.
    """
    if llm is None:
        return copy.deepcopy(FALLBACK_MODULES)
    reply = llm(build_prompt(description))
    return parse_modules(reply)

if __name__ == "__main__":
    from router import route

    modules = decompose("build a calculator")
    assert route(modules[0], "cheap")["status"] == "passed"

    modules = decompose("build a calculator", llm=fake_llm)
    assert modules[0]["function_name"] == "add"
    assert route(modules[0], "cheap")["status"] == "passed"

    messy = "Here you go:\n" + fake_llm("") + "\nHope that helps!"
    assert parse_modules(messy)[0]["function_name"] == "add"

    def bad_llm(prompt):
        return "sorry, I can't do that"
    try:
        decompose("x", llm=bad_llm)
        assert False, "a reply with no JSON list should raise"
    except ValueError:
        pass

    def broken_llm(prompt):
        raise TimeoutError("API took too long")
    try:
        decompose("x", llm=broken_llm)
        assert False, "an LLM error should raise"
    except TimeoutError:
        pass

    bad_reply = '[{"name": "x", "description": "y", "function_name": "z", "test_input": [1]}]'
    try:
        parse_modules(bad_reply)
        assert False, "should have been rejected"
    except ValueError:
        pass

    print("decomposer tests passed")
