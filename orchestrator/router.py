def cheap_tool(module_name):
    return "def add(a, b)\n    return a + b"

def medium_tool(module_name):
    return "def add(a, b):\n    return a - b"

def strong_tool(module_name):
    return "def add(a, b):\n    return a + b"

SETTINGS = {
    "cheap": {"start": 0, "max_attempts": 3},
    "balanced": {"start": 1, "max_attempts": 2},
    "quality": {"start": 2, "max_attempts": 1},
}

def check(code, module):
    try:
        namespace = {}
        exec(code, namespace)
        func = namespace[module["function_name"]]
        return func(*module["test_input"]) == module["expected"]
    except Exception:
        return False

def route(module, optimization="cheap"):
    settings = SETTINGS[optimization]
    tools = [cheap_tool, medium_tool, strong_tool]
    tools = tools[settings["start"]:]
    tools = tools[:settings["max_attempts"]]
    tries = 0
    for tool in tools:
        tries = tries + 1
        result = tool(module["name"])
        ok = check(result, module)
        print("Tried", tool.__name__, "->", "passed" if ok else "failed")
        if ok:
            return {
                "name": module["name"],
                "tool_used": tool.__name__,
                "tries": tries,
                "status": "passed",
                "code": result,
            }
    return {
        "name": module["name"],
        "tool_used": None,
        "tries": tries,
        "status": "failed",
    }


add_module = {
    "name": "add function",
    "description": "adds two numbers",
    "function_name": "add",
    "test_input": (2, 3),
    "expected": 5,
    "status": "waiting",
}

multiply_module = {
    "name": "multiply function",
    "description": "multiplies two numbers",
    "function_name": "multiply",
    "test_input": (2, 3),
    "expected": 6,
    "status": "waiting",
}

# Simon's backend (backend/orchestration.py) reads router.modules, so keep this list.
modules = [add_module, multiply_module]

if __name__ == "__main__":
    # tests (#114)
    assert route(add_module, "cheap")["tries"] == 3
    assert route(add_module, "balanced")["tries"] == 2
    assert route(add_module, "quality")["tries"] == 1
    assert route(multiply_module, "quality")["status"] == "failed"
    print("all tests passed")
