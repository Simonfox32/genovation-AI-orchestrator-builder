from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai import types
from datetime import datetime
import shutil
import os
import subprocess
import time

load_dotenv(Path(__file__).parent.parent / "backend" / ".env")


client = genai.Client()
MODEL = "gemini-3.5-flash-lite"

SPIKE = Path(__file__).parent
TEMPLATE = SPIKE / "template"

PYTHON = str(SPIKE.parent / "backend" / ".venv" / "bin" / "python")  # the Python that has pytest

workspace = SPIKE / "runs" / datetime.now().strftime("%Y%m%d-%H%M%S") / "workspace"
shutil.copytree(TEMPLATE, workspace)
print("Workspace:", workspace)

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
    # load_dotenv put your Gemini key into this program's environment, and child processes
    # inherit it. So we run the tests with a stripped-down environment: the agent's code
    # can't read the key from there.
    env = {"PATH": os.environ["PATH"], "HOME": os.environ["HOME"]}
    try:
        result = subprocess.run(
            [PYTHON, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
            cwd=workspace, env=env, capture_output=True, text=True, timeout=60,
        )
    except subprocess.TimeoutExpired:
        return "TIMEOUT: the tests took longer than 60 seconds."
    output = result.stdout + result.stderr
    return output[-4000:]  # the end has the failures and the summary; keeps what we send small

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

SYSTEM_PROMPT = """You are the backend developer on a small team, working on a Python FastAPI project.
Your job: make every test in tests/ pass.
- Read the tests first: they are the requirements.
- Never change anything in tests/.
- Write real, general code. Never special-case the exact values the tests use.
- Run the tests after every change, and fix what fails. You're done only when all tests pass."""

config = types.GenerateContentConfig(
    system_instruction=SYSTEM_PROMPT,
    tools=[types.Tool(function_declarations=[list_files_tool, read_file_tool, write_file_tool, run_tests_tool])],
    automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
)

TOOLS = {"list_files": list_files, "read_file": read_file, "write_file": write_file, "run_tests":run_tests}

def ask_model(history):
    """Call Gemini, retrying a few times: under load it often answers 503 'overloaded'."""
    for attempt in range(1, 5):
        try:
            return client.models.generate_content(model=MODEL, contents=history, config=config)
        except Exception as error:
            print(f"   (Gemini error, retrying in {attempt * 3}s: {str(error)[:60]})")
            time.sleep(attempt * 3)
    raise RuntimeError("Gemini unavailable after 4 attempts")


def tests_pass(output):
    """pytest's last line is the summary, e.g. '10 passed in 0.3s' or '2 failed, 8 passed in 0.4s'."""
    summary = output.strip().splitlines()[-1] if output.strip() else ""
    return "passed" in summary and "failed" not in summary and "error" not in summary


history = [types.Content(role="user", parts=[types.Part(
    text="Read the tests and write app/main.py so that they pass. Make the tests in tests/ pass.")])]

MAX_STEPS = 15
started = time.time()
for step in range(1, MAX_STEPS + 1):
    response = ask_model(history)
    history.append(response.candidates[0].content)

    calls = response.function_calls
    if not calls:
        # The model says it's finished. Don't take its word for it: run the tests ourselves.
        output = run_tests()
        if tests_pass(output):
            print(f"\nDONE after {step} steps. Verified by our own test run: {output.strip().splitlines()[-1]}")
            print("Agent's summary:", response.text)
            break
        print(f"Step {step}: agent said it was done, but the tests fail. Sending it back.")
        history.append(types.Content(role="user", parts=[types.Part(
            text="Not done: the tests still fail. Here is the output:\n" + output)]))
        continue  # skip the tool handling below and go round the loop again

    results = []
    for call in calls:
        try:
            result = TOOLS[call.name](**call.args)
        except Exception as error:
            result = f"ERROR: {type(error).__name__}: {error}"
        # Short log line: hide file contents, show just the last line of each result.
        shown = {k: (f"<{len(v)} chars>" if k == "content" else v) for k, v in call.args.items()}
        last_line = str(result).strip().splitlines()[-1][:80] if str(result).strip() else ""
        print(f"Step {step}: {call.name}({shown}) -> {last_line}")
        results.append(types.Part(function_response=types.FunctionResponse(
            id=call.id, name=call.name, response={"result": result})))

    history.append(types.Content(role="user", parts=results))
else:
    print("Stopped: hit the step limit")

print(f"Time: {time.time() - started:.0f}s    Code: {workspace / 'app' / 'main.py'}")