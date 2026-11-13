"""The coding agents: Gemini + file and test tools, looping until their job is done.

Two roles share one loop (run_agent):
  - builder: makes the tests in tests/ pass by writing the app. Never touches tests/.
  - tester:  turns a product description into tests in tests/. Never touches the app.

Used two ways:
  - from the backend:  run_agent(workspace, on_step=save_step, role=...)   (see backend/orchestration.py)
  - from the terminal: backend/.venv/bin/python orchestrator/agent.py                  (builder, fixed tests)
                       backend/.venv/bin/python orchestrator/agent.py "a store with orders"  (tester, then builder)
"""
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from google import genai
from google.genai import types

MODEL = "gemini-3.5-flash-lite"
TEMPLATE = Path(__file__).parent / "agent_template"  # the starter app + its tests (the requirements)

SYSTEM_PROMPT = """You are the backend developer on a small team, working on a Python FastAPI project.
Your job: make every test in tests/ pass.
- Read the tests first: they are the requirements.
- Never change anything in tests/.
- Write real, general code. Never special-case the exact values the tests use.
- Keep data in memory. Use only FastAPI, Pydantic and plain Python: no files, processes, network or environment access.
- Run the tests after every change, and fix what fails. You're done only when all tests pass."""

START_MESSAGE = "Make the tests in tests/ pass."

TESTER_PROMPT = """You are the tester on a small team. A product owner describes a backend; before any code exists,
you write its requirements as pytest tests that a developer will then make pass.
- Write ONE file: tests/test_app.py. You may only write files in tests/; never write app code.
- The app will be a FastAPI app in app/main.py. Use `from fastapi.testclient import TestClient` and
  `from app.main import app`, then `client = TestClient(app)`. Do not import anything else from app/.
- Write 6 to 10 tests covering the main features: creating, listing and fetching the main resources,
  one or two business rules from the description (for example totals or status changes), and 404/422 errors.
- You decide the API: paths, JSON fields and status codes. Make it consistent and unambiguous; a developer
  will implement exactly what the tests demand, and nothing else.
- Data lives in memory. Tests must be independent: each test creates the data it needs, never assumes an id
  is a specific number, and uses unique names so tests can't clash. Use whole numbers for money (cents).
- No randomness, dates or times, files, network, environment variables or other libraries.
- When the file is written, run the tests: they should FAIL (nothing is built yet) but must load without errors.
  Then reply with a short summary of the API you specified."""

# Code that an in-memory web API never needs. Refused in anything either agent writes, because AI-written code
# runs on the server. A speed bump against prompt injection, not a sandbox (that's Story #124).
# Patterns are word-aware so ordinary code passes: "/orders/1/process" is fine, "/proc/self/environ" is not;
# "order_retrieval(" is fine, "eval(" is not.
FORBIDDEN = (
    r"\b(import|from)\s+(os|sys|subprocess|socket|shutil|pathlib|importlib|ctypes|requests|urllib|httpx|http\.client)\b",
    r"\b__import__\b", r"\bsubprocess\b", r"\benviron\b", r"/proc/", r"\bsys\.modules\b", r"\bbuiltins\b",
    r"(?<![\w.])open\s*\(", r"(?<![\w.])eval\s*\(", r"(?<![\w.])exec\s*\(", r"(?<![\w.])globals\s*\(",
)


def tester_message(description):
    """The tester's first message: the product owner's request. (The builder never sees it.)"""
    return f"The product owner's request:\n\n{description}\n\nWrite the tests in tests/test_app.py."

# ---- The tool menu: what the model reads to decide which tool to ask for ----

list_files_tool = types.FunctionDeclaration(
    name="list_files",
    description="List every file in the project (recursively), or inside one folder.",
    parameters_json_schema={"type": "object", "properties": {
        "folder": {"type": "string", "description": "Folder relative to the project root. '.' for everything."}}},
)
read_file_tool = types.FunctionDeclaration(
    name="read_file",
    description="Read a file's full contents.",
    parameters_json_schema={"type": "object", "properties": {
        "path": {"type": "string", "description": "File path relative to the project root, e.g. 'app/main.py'."}},
        "required": ["path"]},
)
write_file_tool = types.FunctionDeclaration(
    name="write_file",
    description="Create or overwrite a file with the complete new contents.",
    parameters_json_schema={"type": "object", "properties": {
        "path": {"type": "string", "description": "File path relative to the project root."},
        "content": {"type": "string", "description": "The full file contents."}},
        "required": ["path", "content"]},
)
run_tests_tool = types.FunctionDeclaration(
    name="run_tests",
    description="Run the test suite with pytest and return its output: failures and the summary.",
    parameters_json_schema={"type": "object", "properties": {}},
)
TOOL_MENU = [list_files_tool, read_file_tool, write_file_tool, run_tests_tool]


# ---- The real tools: plain Python, fenced to one workspace ----

def make_tools(workspace, role="builder"):
    """Build the tools for ONE workspace and role. Each run gets its own set, fenced to its own folder."""
    # Resolve once, so every path uses the same spelling. (On macOS the temp folder /var/... is a link
    # to /private/var/...; mixing the two made relative_to() fail.)
    workspace = Path(workspace).resolve()

    def safe_path(path):
        """Turn the model's path into a real path, refusing anything outside the workspace."""
        full = (workspace / path).resolve()  # resolve() turns "a/../../b" into the real location
        if not full.is_relative_to(workspace.resolve()):
            raise ValueError(f"{path} is outside the project")
        return full

    def list_files(folder="."):
        root = safe_path(folder)
        files = [str(p.relative_to(workspace)) for p in sorted(root.rglob("*"))
                 if p.is_file() and "__pycache__" not in p.parts]
        return "\n".join(files) or "(no files)"

    def read_file(path):
        return safe_path(path).read_text()

    def write_file(path, content):
        full = safe_path(path)
        in_tests = full.is_relative_to((workspace / "tests").resolve())
        if role == "builder" and in_tests:
            return "REFUSED: tests/ holds the requirements. Change the app code instead."
        if role == "tester" and not in_tests:
            return "REFUSED: the tester only writes tests, in tests/. The developer writes the app."
        blocked = [match.group(0) for pattern in FORBIDDEN if (match := re.search(pattern, content))]
        if blocked:
            return (f"REFUSED: this project may not use {', '.join(blocked)}. Keep to FastAPI, Pydantic and "
                    "plain Python data structures: no files, processes, network or environment access.")
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content)
        return f"Wrote {path} ({len(content)} characters)"

    def pytest(*extra):
        # Our own environment holds the Gemini key (and on Railway, the database URL). Child processes
        # inherit it, so the tests run with a stripped-down environment instead.
        env = {"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", "")}
        try:
            result = subprocess.run(
                [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *extra],  # the Python running us
                cwd=workspace, env=env, capture_output=True, text=True, timeout=60,
            )
        except subprocess.TimeoutExpired:
            return "TIMEOUT: the tests took longer than 60 seconds."
        output = result.stdout + result.stderr
        return output[-4000:]  # the end has the failures and the summary; keeps what we send small

    def run_tests():
        return pytest()

    def collect_tests():
        """Load the tests without running them: catches broken test files. Used by our checks, not the model."""
        return pytest("--collect-only")

    return {"list_files": list_files, "read_file": read_file, "write_file": write_file, "run_tests": run_tests,
            "collect_tests": collect_tests}


def tests_pass(output):
    """pytest's last line is the summary, e.g. '10 passed in 0.3s' or '2 failed, 8 passed in 0.4s'."""
    summary = output.strip().splitlines()[-1] if output.strip() else ""
    return "passed" in summary and "failed" not in summary and "error" not in summary


# ---- "Done" checks: our code decides when a role is finished, not the model ----

def builder_done(tools):
    """Done when every test passes. Returns (done, test_output, message for the agent if not done)."""
    output = tools["run_tests"]()
    if tests_pass(output):
        return True, output, ""
    return False, output, "Not done: the tests still fail. Here is the output:\n" + output


def tester_done(tools):
    """Done when there are 3+ tests that load cleanly and FAIL on the empty starter app (so they test something)."""
    collected = tools["collect_tests"]()
    summary = collected.strip().splitlines()[-1] if collected.strip() else ""
    match = re.search(r"(\d+) tests? collected", summary)
    if "error" in summary or not match or int(match.group(1)) < 3:
        return False, collected, ("Not done: tests/test_app.py must load without errors and contain at least "
                                  "3 tests. pytest says:\n" + collected)
    output = tools["run_tests"]()
    if tests_pass(output):
        return False, output, ("Not done: every test already passes on the empty starter app, so the tests "
                               "don't check anything. Make them test the features from the request.")
    return True, output, ""


# ---- The agent loop ----

def run_agent(workspace, on_step=None, model=MODEL, max_steps=15, system_prompt=None,
              role="builder", start_message=None):
    """Run one agent (role "builder" or "tester") in `workspace` until its done-check passes or `max_steps` runs out.

    on_step(event) is called after every tool call with
        {"step": int, "tool": str, "args": dict, "result": str}
    Returns {"passed": bool, "steps": int, "summary": str, "test_output": str}.
    """
    if role not in ("builder", "tester"):
        raise ValueError(f"unknown role: {role}")
    is_tester = role == "tester"
    system_prompt = system_prompt or (TESTER_PROMPT if is_tester else SYSTEM_PROMPT)
    start_message = start_message or START_MESSAGE
    done_check = tester_done if is_tester else builder_done

    client = genai.Client()  # created here, not at import time, so importing this file never needs a key
    tools = make_tools(workspace, role=role)
    config = types.GenerateContentConfig(
        system_instruction=system_prompt,
        tools=[types.Tool(function_declarations=TOOL_MENU)],
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
    )

    def ask_model(history):
        """Call Gemini, retrying a few times: under load it often answers 503 'overloaded'."""
        for attempt in range(1, 5):
            try:
                return client.models.generate_content(model=model, contents=history, config=config)
            except Exception as error:
                print(f"   (Gemini error, retrying in {attempt * 3}s: {str(error)[:60]})")
                time.sleep(attempt * 3)
        raise RuntimeError("Gemini unavailable after 4 attempts")

    history = [types.Content(role="user", parts=[types.Part(text=start_message)])]

    for step in range(1, max_steps + 1):
        response = ask_model(history)
        history.append(response.candidates[0].content)

        calls = response.function_calls
        if not calls:
            # The model says it's finished. Don't take its word for it: check ourselves.
            done, output, feedback = done_check(tools)
            if done:
                return {"passed": True, "steps": step, "summary": response.text or "", "test_output": output}
            history.append(types.Content(role="user", parts=[types.Part(text=feedback)]))
            continue

        results = []
        for call in calls:
            args = dict(call.args or {})
            try:
                result = str(tools[call.name](**args))
            except Exception as error:
                result = f"ERROR: {type(error).__name__}: {error}"
            if on_step:
                on_step({"step": step, "tool": call.name, "args": args, "result": result})
            results.append(types.Part(function_response=types.FunctionResponse(
                id=call.id, name=call.name, response={"result": result})))
        history.append(types.Content(role="user", parts=results))

    # Out of steps. Check once more: the work may be done even though the agent never said so.
    done, output, _ = done_check(tools)
    return {"passed": done, "steps": max_steps, "summary": "Stopped: hit the step limit", "test_output": output}


# ---- Terminal version: python orchestrator/agent.py ----

def print_step(event):
    """Short log line: hide file contents, show just the last line of each result."""
    shown = {k: (f"<{len(v)} chars>" if k == "content" else v) for k, v in event["args"].items()}
    result = event["result"].strip()
    last_line = result.splitlines()[-1][:80] if result else ""
    print(f"Step {event['step']}: {event['tool']}({shown}) -> {last_line}")


if __name__ == "__main__":
    from dotenv import load_dotenv

    repo = Path(__file__).parent.parent
    load_dotenv(repo / "backend" / ".env")
    workspace = repo / "spike" / "runs" / datetime.now().strftime("%Y%m%d-%H%M%S") / "workspace"
    shutil.copytree(TEMPLATE, workspace, ignore=shutil.ignore_patterns("__pycache__"))
    print("Workspace:", workspace)
    started = time.time()

    description = " ".join(sys.argv[1:])
    if description:  # team mode: the tester writes the tests first, replacing the built-in ones
        for old_test in (workspace / "tests").glob("test_*.py"):
            old_test.unlink()
        print("\n=== Tester ===")
        tester = run_agent(workspace, on_step=print_step, role="tester",
                           start_message=tester_message(description), max_steps=12)
        print(f"Tester {'done' if tester['passed'] else 'FAILED'}: {tester['test_output'].strip().splitlines()[-1]}")
        if not tester["passed"]:
            sys.exit(1)
        print("\n=== Builder ===")

    outcome = run_agent(workspace, on_step=print_step, max_steps=20 if description else 15)
    verdict = "DONE, verified" if outcome["passed"] else "NOT DONE"
    print(f"\n{verdict} after {outcome['steps']} steps: {outcome['test_output'].strip().splitlines()[-1]}")
    print(f"Time: {time.time() - started:.0f}s    Code: {workspace / 'app' / 'main.py'}")
