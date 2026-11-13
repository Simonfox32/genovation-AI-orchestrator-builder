"""The coding agent: Gemini + file and test tools, looping until the tests pass.

Used two ways:
  - from the backend:  run_agent(workspace, on_step=save_step)   (see backend/orchestration.py)
  - from the terminal: backend/.venv/bin/python orchestrator/agent.py
"""
import os
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
- Run the tests after every change, and fix what fails. You're done only when all tests pass."""

START_MESSAGE = "Make the tests in tests/ pass."

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

def make_tools(workspace):
    """Build the tools for ONE workspace. Each run gets its own set, fenced to its own folder."""
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
        if full.is_relative_to((workspace / "tests").resolve()):
            return "REFUSED: tests/ holds the requirements. Change the app code instead."
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content)
        return f"Wrote {path} ({len(content)} characters)"

    def run_tests():
        # Our own environment holds the Gemini key (and on Railway, the database URL). Child processes
        # inherit it, so the tests run with a stripped-down environment instead.
        env = {"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", "")}
        try:
            result = subprocess.run(
                [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"],  # the Python running us
                cwd=workspace, env=env, capture_output=True, text=True, timeout=60,
            )
        except subprocess.TimeoutExpired:
            return "TIMEOUT: the tests took longer than 60 seconds."
        output = result.stdout + result.stderr
        return output[-4000:]  # the end has the failures and the summary; keeps what we send small

    return {"list_files": list_files, "read_file": read_file, "write_file": write_file, "run_tests": run_tests}


def tests_pass(output):
    """pytest's last line is the summary, e.g. '10 passed in 0.3s' or '2 failed, 8 passed in 0.4s'."""
    summary = output.strip().splitlines()[-1] if output.strip() else ""
    return "passed" in summary and "failed" not in summary and "error" not in summary


# ---- The agent loop ----

def run_agent(workspace, on_step=None, model=MODEL, max_steps=15, system_prompt=SYSTEM_PROMPT):
    """Run the agent in `workspace` until the tests pass or `max_steps` runs out.

    on_step(event) is called after every tool call with
        {"step": int, "tool": str, "args": dict, "result": str}
    Returns {"passed": bool, "steps": int, "summary": str, "test_output": str}.
    """
    client = genai.Client()  # created here, not at import time, so importing this file never needs a key
    tools = make_tools(workspace)
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

    history = [types.Content(role="user", parts=[types.Part(text=START_MESSAGE)])]

    for step in range(1, max_steps + 1):
        response = ask_model(history)
        history.append(response.candidates[0].content)

        calls = response.function_calls
        if not calls:
            # The model says it's finished. Don't take its word for it: run the tests ourselves.
            output = tools["run_tests"]()
            if tests_pass(output):
                return {"passed": True, "steps": step, "summary": response.text or "", "test_output": output}
            history.append(types.Content(role="user", parts=[types.Part(
                text="Not done: the tests still fail. Here is the output:\n" + output)]))
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

    return {"passed": False, "steps": max_steps, "summary": "Stopped: hit the step limit",
            "test_output": tools["run_tests"]()}


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
    outcome = run_agent(workspace, on_step=print_step)
    verdict = "DONE, verified" if outcome["passed"] else "NOT DONE"
    print(f"\n{verdict} after {outcome['steps']} steps: {outcome['test_output'].strip().splitlines()[-1]}")
    print(f"Time: {time.time() - started:.0f}s    Code: {workspace / 'app' / 'main.py'}")
