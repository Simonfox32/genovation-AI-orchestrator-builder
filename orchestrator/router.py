def cheap_tool(module_name):
    return "def add(a, b)\n    return a + b"

def medium_tool(module_name):
    return "def add(a, b):\n    return a - b"

def strong_tool(module_name):
    return "def add(a, b):\n    return a + b"

def check(code, module):
    try:
        namespace = {}
        exec(code, namespace)
        func = namespace[module["function_name"]]
        return func(*module["test_input"]) == module["expected"]
    except Exception:
        return False

def route(module):
    tools = [cheap_tool, medium_tool, strong_tool]
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
            }
    return {
        "name": module["name"],
        "tool_used": None,
        "tries": tries,
        "status": "failed",
    }


modules = [
    {
        "name": "add function",
        "description": "adds two numbers",
        "function_name": "add",
        "test_input": (2, 3),
        "expected": 5,
        "status": "waiting",
    },
    {
        "name": "multiply function",
        "description": "multiplies two numbers",
        "function_name": "multiply",
        "test_input": (2, 3),
        "expected": 6,
        "status": "waiting",
    },
]

for module in modules:
    print(route(module))
