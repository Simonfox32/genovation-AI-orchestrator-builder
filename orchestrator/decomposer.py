import json

REQUIRED_FIELDS = ["name", "description", "function_name", "test_input", "expected"]


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


def parse_modules(text):
    data = json.loads(text)
    return [validate_module(m) for m in data]


def decompose(request):
    # TEMPORARY: returns one fixed module.
    # Later: send build_prompt(request) to an LLM, then return parse_modules(reply).
    return [
        {
            "name": "add function",
            "description": "adds two numbers",
            "function_name": "add",
            "test_input": (2, 3),
            "expected": 5,
            "status": "waiting",
        }
    ]


if __name__ == "__main__":
    from router import route

    modules = decompose("build a calculator")
    assert route(modules[0], "cheap")["status"] == "passed"

    fake_reply = '[{"name": "multiply function", "description": "multiplies two numbers", "function_name": "multiply", "test_input": [2, 3], "expected": 6}]'
    parsed = parse_modules(fake_reply)
    assert parsed[0]["status"] == "waiting"

    bad_reply = '[{"name": "x", "description": "y", "function_name": "z", "test_input": [1]}]'
    try:
        parse_modules(bad_reply)
        assert False, "should have been rejected"
    except ValueError:
        pass

    print("decomposer tests passed")
