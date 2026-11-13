"""M0 spike: can one Gemini agent make a failing test suite pass by itself?

LOCAL ONLY. The agent's code runs on this machine (via pytest), so never point this at anything but a
throwaway copy of spike/template/.

Usage (from the repo root):
    backend/.venv/bin/python spike/agent_loop.py [model] [max_steps]
"""
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai import types

SPIKE_DIR = Path(__file__).resolve().parent
TEMPLATE = SPIKE_DIR / "template"
PYTHON = str(SPIKE_DIR.parent / "backend" / ".venv" / "bin" / "python")

MODEL = sys.argv[1] if len(sys.argv) > 1 else "gemini-3.5-flash-lite"
MAX_STEPS = int(sys.argv[2]) if len(sys.argv) > 2 else 25
MAX_OUTPUT = 4000  # characters of tool output sent back to the model

SYSTEM_PROMPT = """You are the backend developer on a small team.
You work in a Python FastAPI project. Your task: make every test in tests/ pass.

Rules:
- Use the tools to look at files, write code, and run the tests. Do not guess file contents: read them.
- You may not change anything in tests/. The tests are the requirements.
- Write real, general code. Never special-case the values used in the tests.
- Run the tests after each change. When all tests pass, call finish with a short summary.
"""

# ---- The workspace: a fresh copy of the template for every run ----

run_dir = SPIKE_DIR / "runs" / datetime.now().strftime("%Y%m%d-%H%M%S")
workspace = run_dir / "workspace"
shutil.copytree(TEMPLATE, workspace)


def resolve(path: str) -> Path:
    """Turn the agent's relative path into a real one, refusing anything outside the workspace."""
    full = (workspace / path).resolve()
    if full != workspace.resolve() and workspace.resolve() not in full.parents:
        raise ValueError(f"path is outside the project: {path}")
    return full


def truncate(text: str) -> str:
    return text if len(text) <= MAX_OUTPUT else text[:MAX_OUTPUT] + "\n...(truncated)"


# ---- Tools the agent can call ----

def list_files(path: str = ".") -> str:
    root = resolve(path)
    files = [str(p.relative_to(workspace)) for p in sorted(root.rglob("*"))
             if p.is_file() and "__pycache__" not in p.parts]
    return "\n".join(files) or "(no files)"


def read_file(path: str) -> str:
    return truncate(resolve(path).read_text())


def write_file(path: str, content: str) -> str:
    full = resolve(path)
    if (workspace / "tests").resolve() in full.parents:
        return "REFUSED: tests/ belongs to the tester. Change the application code instead."
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content)
    return f"wrote {path} ({len(content)} characters)"


def run_tests() -> str:
    # A minimal environment: the agent's code must not see our API keys or database URLs.
    env = {"PATH": os.environ["PATH"], "HOME": os.environ["HOME"]}
    try:
        result = subprocess.run([PYTHON, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
                                cwd=workspace, env=env, capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        return "TIMEOUT: the tests took longer than 60 seconds."
    return truncate(result.stdout + result.stderr)


def tests_pass() -> tuple[bool, str]:
    output = run_tests()
    last_line = output.strip().splitlines()[-1] if output.strip() else ""
    return ("passed" in last_line and "failed" not in last_line and "error" not in last_line), output


TOOLS = {"list_files": list_files, "read_file": read_file, "write_file": write_file, "run_tests": run_tests}

DECLARATIONS = [
    types.FunctionDeclaration(
        name="list_files", description="List the files in the project (or a folder of it).",
        parameters_json_schema={"type": "object", "properties": {"path": {"type": "string"}}}),
    types.FunctionDeclaration(
        name="read_file", description="Read a file's contents.",
        parameters_json_schema={"type": "object", "properties": {"path": {"type": "string"}},
                                "required": ["path"]}),
    types.FunctionDeclaration(
        name="write_file", description="Create or overwrite a file with the full new contents.",
        parameters_json_schema={"type": "object",
                                "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                                "required": ["path", "content"]}),
    types.FunctionDeclaration(
        name="run_tests", description="Run the test suite with pytest and return its output.",
        parameters_json_schema={"type": "object", "properties": {}}),
    types.FunctionDeclaration(
        name="finish", description="Call when all tests pass. Give a short summary of what you built.",
        parameters_json_schema={"type": "object", "properties": {"summary": {"type": "string"}},
                                "required": ["summary"]}),
]

# ---- The loop ----

client = None
log = []


def call_model(contents, config):
    """generate_content with a few retries, since Gemini often returns 503/504 under load."""
    for attempt in range(4):
        try:
            return client.models.generate_content(model=MODEL, contents=contents, config=config)
        except Exception as error:  # noqa: BLE001 - spike: retry anything, then give up
            print(f"   (model error, retrying: {str(error)[:80]})")
            time.sleep(3 * (attempt + 1))
    raise RuntimeError("model unavailable after 4 attempts")


def main():
    global client
    load_dotenv(SPIKE_DIR.parent / "backend" / ".env")
    client = genai.Client(http_options=types.HttpOptions(timeout=60000))
    config = types.GenerateContentConfig(
        system_instruction=SYSTEM_PROMPT,
        tools=[types.Tool(function_declarations=DECLARATIONS)],
        # We run the tools ourselves, so we can log, limit and check every call.
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
    )
    contents = [types.Content(role="user", parts=[types.Part(text="Start. Make the tests in tests/ pass.")])]

    print(f"Model: {MODEL}   max steps: {MAX_STEPS}   workspace: {workspace}")
    started = time.time()
    model_calls = 0
    outcome = "ran out of steps"

    for step in range(1, MAX_STEPS + 1):
        response = call_model(contents, config)
        model_calls += 1
        contents.append(response.candidates[0].content)  # keep the model's turn (includes its thinking state)
        calls = response.function_calls or []

        if not calls:
            print(f"{step:>2}. (text) {(response.text or '').strip()[:150]}")
            contents.append(types.Content(role="user", parts=[types.Part(
                text="Keep going with the tools. Call finish only when all tests pass.")]))
            continue

        replies = []
        for call in calls:
            args = dict(call.args or {})
            shown = {k: (v if k != "content" else f"<{len(v)} chars>") for k, v in args.items()}
            if call.name == "finish":
                # Rule: an agent saying "done" counts for nothing. We run the tests ourselves.
                ok, output = tests_pass()
                print(f"{step:>2}. finish -> verified: {'ALL PASS' if ok else 'STILL FAILING'}")
                if ok:
                    outcome = "passed"
                    log.append({"step": step, "tool": "finish", "args": args})
                    break
                result = "Not done: the tests still fail.\n" + output
            elif call.name in TOOLS:
                try:
                    result = TOOLS[call.name](**args)
                except Exception as error:  # noqa: BLE001 - report tool errors back to the agent
                    result = f"ERROR: {error}"
                summary = result.strip().splitlines()[-1][:90] if result.strip() else ""
                print(f"{step:>2}. {call.name}({shown}) -> {summary}")
            else:
                result = f"ERROR: unknown tool {call.name}"
            log.append({"step": step, "tool": call.name, "args": shown, "result": result[:500]})
            replies.append(types.Part(function_response=types.FunctionResponse(
                id=call.id, name=call.name, response={"result": result})))

        if outcome == "passed":
            break
        contents.append(types.Content(role="user", parts=replies))

    elapsed = time.time() - started
    ok, output = tests_pass()
    final_line = output.strip().splitlines()[-1] if output.strip() else "(no output)"
    print(f"\nOUTCOME: {outcome}   final tests: {final_line}")
    print(f"steps: {step}   model calls: {model_calls}   time: {elapsed:.0f}s")
    print(f"code: {workspace / 'app'}")

    (run_dir / "log.json").write_text(json.dumps(
        {"model": MODEL, "outcome": outcome, "final_tests": final_line, "steps": step,
         "model_calls": model_calls, "seconds": round(elapsed), "log": log}, indent=2))


if __name__ == "__main__":
    main()
