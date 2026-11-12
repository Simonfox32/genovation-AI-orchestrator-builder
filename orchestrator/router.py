import os

# Model names for each tier. Copy the exact names from Google AI Studio if these don't match.
CHEAP_MODEL = "gemini-3.1-flash-lite"
MEDIUM_MODEL = "gemini-3.5-flash-lite"
STRONG_MODEL = "gemini-3.8-flash"


# ---- Stub tools (fake code, used by tests and by default) ----

def cheap_tool(module):
    return "def add(a, b)\n    return a + b"

def medium_tool(module):
    return "def add(a, b):\n    return a - b"

def strong_tool(module):
    return "def add(a, b):\n    return a + b"

STUB_TOOLS = [cheap_tool, medium_tool, strong_tool]


# ---- Real tools (call an LLM to write the code) ----

def build_code_prompt(module):
    return (
        "Write one Python function named " + module["function_name"] + ".\n"
        "What it should do: " + module["description"] + "\n"
        "It takes " + str(len(module["test_input"])) + " argument(s).\n"
        "Return ONLY the Python code, with no explanation."
    )

def clean_code(text):
    # Models often wrap code in ``` fences. Keep only what is inside the first fence.
    if "```" in text:
        block = text.split("```")[1]
        if block.startswith("python"):
            block = block[len("python"):]
        return block.strip()
    return text.strip()

def make_llm_tool(model, name):
    def tool(module):
        try:
            from .llm import call_llm
        except ImportError:
            from llm import call_llm
        reply = call_llm(build_code_prompt(module), model=model)
        return clean_code(reply)
    tool.__name__ = name  # keep the names the backend and dashboard already use
    return tool

REAL_TOOLS = [
    make_llm_tool(CHEAP_MODEL, "cheap_tool"),
    make_llm_tool(MEDIUM_MODEL, "medium_tool"),
    make_llm_tool(STRONG_MODEL, "strong_tool"),
]

def get_tools():
    # Set ROUTER_TOOLS=real (for example on Railway) to use the real LLM tools.
    if os.getenv("ROUTER_TOOLS") == "real":
        return REAL_TOOLS
    return STUB_TOOLS


SETTINGS = {
    "cheap": {"start": 0, "max_attempts": 3},
    "speed": {"start": 0, "max_attempts": 3},
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

def route(module, optimization="cheap", tools=None):
    settings = SETTINGS[optimization]
    if tools is None:
        tools = get_tools()
    tools = tools[settings["start"]:]
    tools = tools[:settings["max_attempts"]]
    tries = 0
    for tool in tools:
        tries = tries + 1
        try:
            result = tool(module)
        except Exception as error:
            # A failed API call counts as a failed attempt, not a crash.
            print("Tool error:", error)
            result = ""
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
    # tests (#114), using the stub tools so they don't call an API
    assert route(add_module, "cheap", tools=STUB_TOOLS)["tries"] == 3
    assert route(add_module, "balanced", tools=STUB_TOOLS)["tries"] == 2
    assert route(add_module, "speed", tools=STUB_TOOLS)["tries"] == 3
    assert route(add_module, "quality", tools=STUB_TOOLS)["tries"] == 1
    assert route(multiply_module, "quality", tools=STUB_TOOLS)["status"] == "failed"

    # default (no tools given, no ROUTER_TOOLS set) still uses the stubs
    assert route(add_module, "quality")["tool_used"] == "strong_tool"

    # clean_code strips fences
    assert clean_code("```python\ndef add(a, b):\n    return a + b\n```") == "def add(a, b):\n    return a + b"
    assert clean_code("def add(a, b):\n    return a + b") == "def add(a, b):\n    return a + b"

    # a tool that raises counts as a failed attempt
    def broken_tool(module):
        raise TimeoutError("API took too long")
    assert route(add_module, "cheap", tools=[broken_tool, strong_tool, strong_tool])["tries"] == 2

    print("all tests passed")
