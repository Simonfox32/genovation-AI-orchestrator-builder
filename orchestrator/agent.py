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

try:  # Anula's model tiers from the v1 router, reused so both pipelines agree on what "cheap/strong" means
    from .router import CHEAP_MODEL, MEDIUM_MODEL, STRONG_MODEL
except ImportError:  # run as a script: python orchestrator/agent.py
    from router import CHEAP_MODEL, MEDIUM_MODEL, STRONG_MODEL

MODEL = MEDIUM_MODEL     # the default
MAX_ATTEMPTS = 6               # model calls per step before the run gives up
REQUEST_TIMEOUT_MS = 60_000  # one model call; past this, give up on it (and fall back, if not already on the fallback)
FALLBACK_MODEL = MEDIUM_MODEL  # used when the chosen model keeps failing (e.g. the strong model returns 503)

# The dashboard's Optimization setting -> which Gemini model the agents use.
MODELS_BY_OPTIMIZATION = {"speed": CHEAP_MODEL, "balanced": MEDIUM_MODEL, "quality": STRONG_MODEL}


def model_for(optimization):
    """The model for an optimization setting ('speed', 'balanced', 'quality'); unknown settings get the default."""
    return MODELS_BY_OPTIMIZATION.get(optimization, MODEL)


TEMPLATE = Path(__file__).parent / "agent_template"  # the starter app + its tests (the requirements)

SYSTEM_PROMPT = """You are the backend developer on a small team, working on a Python FastAPI project.
Your job: make every test in tests/ pass.
- Read the tests first: they are the requirements. If tests/REQUIREMENTS.md exists, it explains them in plain English.
- Never change anything in tests/.
- Write real, general code. Never special-case the exact values the tests use.
- Keep data in memory. Use only FastAPI, Pydantic and plain Python: no files, processes, network or environment access.
- Run the tests after every change, and fix what fails. You're done only when all tests pass."""

START_MESSAGE = "Make the tests in tests/ pass."

TESTER_PROMPT = """You are the tester on a small team. A product owner describes a backend; before any code exists,
you write its requirements as pytest tests that a developer will then make pass. You may only write files in tests/.

Work in two steps.

STEP 1: turn the request into a proper system. Requests are often vague ("a grocery store with transactions");
your job is to expand them into what a real system of that kind needs. Write tests/REQUIREMENTS.md:
- A numbered list of requirements, one per line, in plain English, most important first, 10 to 18 of them:
  - the things to store, including the ones a real system of this kind needs even if the request didn't name
    them (e.g. a store needs products with server-side prices and stock; a transaction references products)
  - the actions on them (create, list, fetch, and domain actions like pay, refund, cancel)
  - the business rules that make it correct (e.g. "the server computes totals from product prices",
    "a purchase can't exceed stock", "a refund restores stock")
  - the edge cases of those rules, each as its OWN numbered requirement, because bugs hide there. Think like
    someone trying to break the rules:
      - the same item twice in one request (e.g. 3 + 3 of a product with stock 5 must be rejected, as 6 > 5)
      - exact boundaries (buying exactly the remaining stock succeeds; one more fails)
      - doing an action twice (refunding twice), or in the wrong state (refunding a cancelled order)
      - one user's data never showing up in another user's results
      - a rejected request changes nothing (stock and totals stay as they were)
  - errors: unknown ids return 404, invalid input returns 422
- Then a section "## Assumptions" listing, one per line, what you added or decided that the request didn't say.
Stay within the domain: no generic extras like a root or health endpoint.

STEP 2: write tests/test_app.py with a separate test for each requirement. Put the requirement's number in the
test's name: `def test_r3_order_reduces_stock():` covers requirement 3. A test may cover at most 2 requirements
(e.g. `test_r4_r12_...`), so one failing check never hides another.
- Use `from fastapi.testclient import TestClient`, `from app.main import app`, `client = TestClient(app)`.
  The app will be a FastAPI app in app/main.py. Import nothing else from app/.
- You decide the API: paths, JSON fields and status codes. Make it consistent and unambiguous; a developer
  will implement exactly what the tests demand, and nothing else.
- Check behaviour, not just status codes: assert on the returned JSON fields and on what changed afterwards
  (e.g. fetch the product again and check its stock went down; check a total is the exact expected number).
- No weak checks: not `len(items) >= 1` or `isinstance(x, list)` on their own. Check that the exact item
  you created is in the list, with the right values.
- Data lives in memory. Tests must be independent: each test creates the data it needs, never assumes an id
  is a specific number, and uses unique names so tests can't clash. Use whole numbers for money (cents).
- No randomness, dates or times, files, network, environment variables or other libraries.

When both files are written, run the tests: they should FAIL (nothing is built yet) but must load without errors.
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
    return (f"The product owner's request:\n\n{description}\n\n"
            "First list the requirements in tests/REQUIREMENTS.md, then write the tests in tests/test_app.py.")

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


def requirement_numbers(requirements_text):
    """The numbered requirement lines ('3. ...' or '3) ...'), ignoring anything under an Assumptions heading."""
    body = re.split(r"^#+\s*Assumptions", requirements_text, flags=re.MULTILINE | re.IGNORECASE)[0]
    return re.findall(r"^\s*(\d+)[.)]", body, re.MULTILINE)


def test_labels(tests_text):
    """Each test function's name -> the requirement numbers in it: 'test_r1_r12_order' -> ['1', '12']."""
    names = re.findall(r"^\s*def (test_\w+)", tests_text, re.MULTILINE)
    return {name: re.findall(r"(?<![A-Za-z0-9])[rR](\d+)(?=_|$)", name) for name in names}


def uncovered_requirements(requirements_text, tests_text):
    """Requirement numbers that no test is named after. Only test names count, not comments."""
    covered = {number for labels in test_labels(tests_text).values() for number in labels}
    return [n for n in requirement_numbers(requirements_text) if n not in covered]


def overloaded_tests(tests_text, limit=2):
    """Tests named after more than `limit` requirements: one giant test hides which requirement failed."""
    return [name for name, labels in test_labels(tests_text).items() if len(set(labels)) > limit]


def tester_done(tools):
    """Done when: the tests load cleanly (3+ of them); tests/REQUIREMENTS.md lists the requirements and every one
    has a test named after it; and the tests FAIL on the empty starter app (so they actually test something)."""
    collected = tools["collect_tests"]()
    summary = collected.strip().splitlines()[-1] if collected.strip() else ""
    match = re.search(r"(\d+) tests? collected", summary)
    if "error" in summary or not match or int(match.group(1)) < 3:
        return False, collected, ("Not done: tests/test_app.py must load without errors and contain at least "
                                  "3 tests. pytest says:\n" + collected)
    try:
        requirements = tools["read_file"]("tests/REQUIREMENTS.md")
        tests = tools["read_file"]("tests/test_app.py")
    except (FileNotFoundError, ValueError) as error:
        return False, collected, f"Not done: write both tests/REQUIREMENTS.md and tests/test_app.py ({error})."
    if not requirement_numbers(requirements):
        return False, collected, "Not done: tests/REQUIREMENTS.md must list the requirements as numbered lines (1. ...)."
    missing = uncovered_requirements(requirements, tests)
    if missing:
        return False, collected, (f"Not done: requirement(s) {', '.join(missing)} have no test. Add a separate "
                                  f"test named after each, like test_r{missing[0]}_...")
    crowded = overloaded_tests(tests)
    if crowded:
        return False, collected, (f"Not done: split {', '.join(crowded)} into separate tests. A test may cover at "
                                  "most 2 requirements, so one failing check never hides another.")
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
    Returns {"passed": bool, "steps": int, "summary": str, "test_output": str, "model": the model that finished}.
    """
    if role not in ("builder", "tester"):
        raise ValueError(f"unknown role: {role}")
    is_tester = role == "tester"
    system_prompt = system_prompt or (TESTER_PROMPT if is_tester else SYSTEM_PROMPT)
    start_message = start_message or START_MESSAGE
    done_check = tester_done if is_tester else builder_done

    # Created here, not at import time, so importing this file never needs a key. The timeout matters: without it,
    # a model that never answers (seen with the strong model under load) hangs the run forever and no fallback starts.
    client = genai.Client(http_options=types.HttpOptions(timeout=REQUEST_TIMEOUT_MS))
    tools = make_tools(workspace, role=role)
    config = types.GenerateContentConfig(
        system_instruction=system_prompt,
        tools=[types.Tool(function_declarations=TOOL_MENU)],
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
    )

    current = {"model": model}  # a dict so ask_model can change it (switch to the fallback model)

    def ask_model(history):
        """Call Gemini, retrying when it fails. Two kinds of failure, handled differently:
        - 429 RESOURCE_EXHAUSTED: the free tier's per-minute request limit. Waiting fixes it, so wait longer
          (10s, 20s, 30s... about 2.5 minutes in total) before giving up.
        - Anything else (503 overloaded, timeouts): short waits. And after 1 failure on a model other than the
          fallback, switch to the fallback for the rest of the run (the strong model under load tends to keep
          failing, and a demo cannot wait minutes)."""
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                return client.models.generate_content(model=current["model"], contents=history, config=config)
            except Exception as error:
                rate_limited = "429" in str(error) or "RESOURCE_EXHAUSTED" in str(error)
                wait = attempt * (10 if rate_limited else 3)
                print(f"   (Gemini error on {current['model']}, retrying in {wait}s: {str(error)[:60]})")
                if not rate_limited and current["model"] != FALLBACK_MODEL:
                    print(f"   (switching to the fallback model {FALLBACK_MODEL})")
                    current["model"] = FALLBACK_MODEL
                if attempt < MAX_ATTEMPTS:
                    time.sleep(wait)
        raise RuntimeError(f"Gemini unavailable after {MAX_ATTEMPTS} attempts")

    history = [types.Content(role="user", parts=[types.Part(text=start_message)])]

    for step in range(1, max_steps + 1):
        response = ask_model(history)
        history.append(response.candidates[0].content)

        calls = response.function_calls
        if not calls:
            # The model says it's finished. Don't take its word for it: check ourselves.
            done, output, feedback = done_check(tools)
            if done:
                return {"passed": True, "steps": step, "summary": response.text or "", "test_output": output,
                        "model": current["model"]}
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
    return {"passed": done, "steps": max_steps, "summary": "Stopped: hit the step limit", "test_output": output,
            "model": current["model"]}


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
