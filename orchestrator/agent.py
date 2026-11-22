"""The coding agents: Gemini + file and test tools, looping until their job is done.

Two roles share one loop (run_agent):
  - builder: makes the tests in tests/ pass by writing the app. Never touches tests/.
  - tester:  turns a product description into tests in tests/. Never touches the app.

Used two ways:
  - from the backend:  run_agent(workspace, on_step=save_step, role=...)   (see backend/orchestration.py)
  - from the terminal: backend/.venv/bin/python orchestrator/agent.py                  (builder, fixed tests)
                       backend/.venv/bin/python orchestrator/agent.py "a store with orders"  (tester, then builder)
"""
import ast
import os
import re
import sys
import textwrap
import time
from datetime import datetime
from pathlib import Path

from google import genai
from google.genai import types

try:  # Anula's model tiers from the v1 router, reused so both pipelines agree on what "cheap/strong" means
    from .router import CHEAP_MODEL, MEDIUM_MODEL, STRONG_MODEL
    from .executors import DockerExecutor, LocalExecutor, SandboxExecutor
except ImportError:  # run as a script: python orchestrator/agent.py
    from router import CHEAP_MODEL, MEDIUM_MODEL, STRONG_MODEL
    from executors import DockerExecutor, LocalExecutor, SandboxExecutor

MODEL = MEDIUM_MODEL     # the default
MAX_ATTEMPTS = 6               # model calls per step before the run gives up
REQUEST_TIMEOUT_MS = 60_000  # one model call; past this, give up on it (and fall back, if not already on the fallback)
FALLBACK_MODEL = MEDIUM_MODEL  # used when the chosen model keeps failing (e.g. the strong model returns 503)

# The feedback loop (team mode). Seen 11-18: a builder spent 5 rounds on a test that contradicted the requirements,
# then broke 3 passing tests trying to satisfy it. So: stop a stuck builder early, and get the tests checked.
STUCK_AFTER = 3     # builder test runs in a row without fewer failures than its best so far -> stop it
REVIEW_ROUNDS = 2   # times the tester re-checks the failing tests and the builder tries again
WARNING_NAGS = 2    # times a builder whose tests all pass is sent back to fix warnings; after that, accept the work
REVIEW_STEPS = 10   # steps for one tester review (a real one, replaying 11-18's run, used 8: read, read, fix, test, fix)
RETRY_STEPS = 15    # steps for each extra builder round (a fresh history, so it starts fast again)

# The dashboard's Optimization setting -> which Gemini model the agents use.
MODELS_BY_OPTIMIZATION = {"speed": CHEAP_MODEL, "balanced": MEDIUM_MODEL, "quality": STRONG_MODEL}


def model_for(optimization):
    """The model for an optimization setting ('speed', 'balanced', 'quality'); unknown settings get the default."""
    return MODELS_BY_OPTIMIZATION.get(optimization, MODEL)


TEMPLATE = Path(__file__).parent / "agent_template"        # in-memory todo app + its built-in tests (ORCHESTRATOR=agent)
TEMPLATE_DB = Path(__file__).parent / "agent_template_db"  # FastAPI + SQLite/SQLAlchemy starter (ORCHESTRATOR=agent-team)

# The database template's plumbing. Agents may read these files but never change them: getting them subtly wrong
# breaks every test (see the C1c experiment: foreign keys off, or no StaticPool).
# requirements.txt lists what the sandbox can actually run: the sandbox has no network to install anything else.
PROTECTED = {"app/db.py", "tests/conftest.py", "requirements.txt"}

SYSTEM_PROMPT = """You are the backend developer on a small team, working on a Python FastAPI project.
Your job: make every test in tests/ pass.
- Read the tests first: they are the requirements. If tests/REQUIREMENTS.md exists, it explains them in plain English.
- Never change anything in tests/.
- Write real, general code. Never special-case the exact values the tests use.
- Keep data in memory. Use only FastAPI, Pydantic and plain Python: no files, processes, network or environment access.
- Run the tests after every change, and fix what fails. You're done only when all tests pass with no warnings
  (pytest lists them; they usually mean a deprecated name, e.g. use status.HTTP_422_UNPROCESSABLE_CONTENT)."""

START_MESSAGE = "Make the tests in tests/ pass."

BUILDER_DB_PROMPT = """You are the backend developer on a small team, working on a FastAPI project with a SQLite
database through SQLAlchemy 2. Your job: make every test in tests/ pass.
- Read the tests first: they are the requirements. tests/REQUIREMENTS.md explains them in plain English.
- Also read app/db.py and tests/conftest.py: they set up the database and the test client. Never change them, or
  anything else in tests/.
- Database models go in app/models.py: SQLAlchemy 2 classes inheriting from `Base` (from app.db), written with
  `Mapped[...]` and `mapped_column(...)`. Let the database enforce rules where it can:
  - ForeignKey(..., ondelete="CASCADE") where deleting a parent must delete its children. If you also define a
    relationship() on the parent, give it cascade="all, delete-orphan", passive_deletes=True.
  - unique=True or UniqueConstraint(...) for "no duplicates" rules. Query for the duplicate before inserting and
    return 409 only when you found one. Never turn every IntegrityError into 409: a missing parent row raises one
    too, and would then be reported as a "duplicate".
- Anything that points at another row (a project_id on a task, a faction_id on a character) must be looked up
  before you create or change it: if that row doesn't exist, return 404 "<Thing> not found" and save nothing.
- Error messages must say what actually went wrong. People will also use this API by hand through its /docs page,
  so the cases the tests don't show must behave just as sensibly as the ones they do.
- Validate input in the Pydantic schemas: Field(min_length=1) does NOT reject "   ", so strip blank text in a
  field_validator; Literal[...] for fixed sets of values; Field(ge=0) for numbers that can't be negative.
- Endpoints get a database session ONLY through `db: Session = Depends(get_db)` (get_db from app.db). Never create
  sessions or engines yourself: the tests swap get_db for a fresh test database.
- Keep `from app import models`, `Base.metadata.create_all(engine)` and `app = FastAPI()` in app/main.py.
- Split the code sensibly: models in app/models.py, request/response shapes (Pydantic) in app/schemas.py,
  endpoints in app/main.py.
- Write real, general code. Never special-case the exact values the tests use.
- Use only FastAPI, Pydantic, SQLAlchemy and the Python standard library (requirements.txt lists what's installed;
  don't change it): no files, processes, network or environment access.
- Run the tests after every change, and fix what fails. You're done only when all tests pass with NO warnings
  (pytest lists them under "warnings summary"). Warnings usually mean a deprecated name, so use the current ones:
  status.HTTP_422_UNPROCESSABLE_CONTENT or plain 422 (HTTP_422_UNPROCESSABLE_ENTITY is deprecated); Pydantic v2's
  model_dump() and model_config = ConfigDict(from_attributes=True) (not .dict(), orm_mode or class Config);
  datetime.now(timezone.utc) (not datetime.utcnow()).
- Tests are written by a person, and can be wrong. If a test can't pass without breaking a numbered requirement or
  another test (e.g. it expects a purchase that requirement 8 says must be refused), don't bend the app to fit it:
  call report_test_problem with the test's name and the contradiction. A tester will check it. Never use it just
  because a test is hard: only for a real contradiction or a wrong expected value."""

TESTER_PROMPT = """You are the tester on a small team. A product owner describes a backend; before any code exists,
you write its requirements as pytest tests that a developer will then make pass. You may only write files in tests/.

Work in two steps.

STEP 1: turn the request into a proper system. Requests are often vague ("a grocery store with transactions");
your job is to expand them into what a real system of that kind needs. Write tests/REQUIREMENTS.md:
- A numbered list of requirements, one per line, in plain English, most important first, 12 to 22 of them:
  - the things to store, including the ones a real system of this kind needs even if the request didn't name
    them (e.g. a store needs products with server-side prices and stock; a transaction references products)
  - the BASICS, for every kind of thing stored, so they are never missing. One requirement per kind of thing,
    e.g. "3. Tasks can be created in a project, fetched by id, listed per project, and reassigned":
      - create it, fetch one by id, and list them, including listing children through their parent
        (e.g. a project's tasks, a task's comments, a workspace's members)
      - change every field the request implies can change (e.g. reassigning a task), and delete where it makes sense
  - VALIDATION, as its own requirements: required text fields reject empty or blank values ("" and "   ") with 422;
    numbers that can't be negative reject negatives with 422; values from a fixed set (like a status) reject
    anything else with 422
  - NO DUPLICATES, as its own requirement wherever something must be unique (e.g. the same member twice in one
    workspace): the duplicate is rejected with 409
  - MISSING PARENTS, as its own requirement wherever one thing points at another: creating or moving a child
    under a parent id that doesn't exist (e.g. a task in project 999) returns 404 and creates nothing. This must
    NOT come back as 409: it isn't a duplicate
  - the domain actions (like pay, refund, cancel)
  - the business rules that make it correct (e.g. "the server computes totals from product prices",
    "a purchase can't exceed stock", "a refund restores stock")
  - the edge cases of those rules, each as its OWN numbered requirement, because bugs hide there. Think like
    someone trying to break the rules:
      - the same item twice in one request (e.g. 3 + 3 of a product with stock 5 must be rejected, as 6 > 5)
      - exact boundaries (buying exactly the remaining stock succeeds; one more fails)
      - doing an action twice (refunding twice), or in the wrong state (refunding a cancelled order)
      - getting around a rule through a different endpoint (e.g. CREATING a task directly as "done" when status
        may only move todo -> doing -> done: new things must start in their first state)
      - one user's data never showing up in another user's results
      - a rejected request changes nothing (stock and totals stay as they were)
  - errors: unknown ids return 404 (in the URL or in the request body), invalid input returns 422, duplicates return 409
- Then a section "## Assumptions" listing, one per line, what you added or decided that the request didn't say.
Stay within the domain: no generic extras like a root or health endpoint.

STEP 2: write tests/test_app.py with a separate test for each requirement. Put the requirement's number in the
test's name: `def test_r3_order_reduces_stock(client):` covers requirement 3. A test may cover at most 2
requirements (e.g. `test_r4_r12_...`), so one failing check never hides another.
- Every test takes the `client` fixture, provided by tests/conftest.py (read it; never change it). Don't create your
  own TestClient, and import nothing from app/. The app will be a FastAPI app backed by a database.
- You decide the API: paths, JSON fields and status codes. Make it consistent and unambiguous; a developer
  will implement exactly what the tests demand, and nothing else.
- Check behaviour, not just status codes: assert on the returned JSON fields and on what changed afterwards
  (e.g. fetch the product again and check its stock went down; check a total is the exact expected number).
- No weak checks: not `len(items) >= 1` or `isinstance(x, list)` on their own. Check that the exact item
  you created is in the list, with the right values.
- Each test starts with a brand-new, empty database, so tests are independent: each test creates the data it
  needs. Never assume an id is a specific number: read it from the response. Use whole numbers for money (cents).
- No randomness, dates or times, files, network, environment variables or other libraries.

When both files are written, run the tests: they should FAIL (nothing is built yet) but must load without errors.
Then reply with a short summary of the API you specified."""

# Code a generated web API never needs. Refused in anything either agent writes. With EXECUTOR=vercel the code
# runs in a sandbox anyway; this is a second, weaker layer for when it doesn't (local runs).
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


REVIEWER_PROMPT = """You are the tester on a small team. Earlier you wrote tests/REQUIREMENTS.md (numbered
requirements) and tests/test_app.py (a test per requirement). A developer then built the app in app/, but could not
make some tests pass. Your job now: decide, for each of those tests, whether the TEST is wrong or the CODE is.

A test is wrong when it contradicts a numbered requirement or another test, expects a value that doesn't follow from
the requirements (check the arithmetic step by step), or relies on something no requirement says. Example: a test
buys a 300-credit ship with 200 credits and expects the purchase to go through, while requirement 8 says a purchase
the character can't afford is refused.

- Read tests/REQUIREMENTS.md and the failing tests. Read app/ too if it helps you see what the code does.
- For each test, compare what its failing assert expects with what the requirements say should happen.
- If they DISAGREE, the TEST is wrong, however clear the requirement is: call fix_test with its name, the complete
  corrected function, and what was wrong. Keep its name (test_r<n>_...) and keep it checking its requirement:
  correct the mistake, never delete the check or weaken it until anything passes.
- If they AGREE, the CODE is wrong: call uphold_test with what the test expects, what the requirement says, and what
  the code gets wrong. A developer's complaint is not evidence: check it against the requirements yourself.
- Make one of those two calls for every test you were asked about. Saying a test is wrong in a message changes
  nothing: only fix_test changes a test. The developer reads the reasons you give."""


def review_message(failing, test_output, dispute=None):
    """The tester's message for a review: which tests failed, the developer's complaint (if any), the evidence."""
    lines = [f"The developer could not make these tests pass: {', '.join(failing)}."]
    if dispute:
        lines.append(f"The developer says {dispute['test']} is wrong: \"{dispute['reason']}\"")
    lines.append(f"The latest test output:\n{test_output[-3000:]}")
    lines.append("Check each of these tests against tests/REQUIREMENTS.md and the other tests, then call fix_test or "
                 "uphold_test for each one.")
    return "\n\n".join(lines)


def retry_message(failing, verdict, tests_changed, decided=True):
    """The builder's first message in an extra round: what the tester decided about the tests it was stuck on."""
    if tests_changed:
        change = "The tester corrected the tests: read tests/test_app.py again."
    elif decided:
        change = "The tester says the tests are right, so the code must change."
    else:
        change = "Nobody has confirmed whether these tests are right."
    return (f"You worked on this project before, but these tests kept failing: {', '.join(failing)}.\n"
            f"{change} The tester's verdict: {verdict or '(none given)'}\n\n"
            "The files are as you left them. Read what you need, then make every test pass.")

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
report_test_problem_tool = types.FunctionDeclaration(
    name="report_test_problem",
    description=("Report a test that can't pass without breaking a numbered requirement or another test, or whose "
                 "expected value is wrong. This ends your turn: a tester checks the test, then you continue."),
    parameters_json_schema={"type": "object", "properties": {
        "test": {"type": "string", "description": "The failing test's function name, e.g. 'test_r10_dispatch'."},
        "reason": {"type": "string", "description": "The contradiction, citing requirement numbers."}},
        "required": ["test", "reason"]},
)
fix_test_tool = types.FunctionDeclaration(
    name="fix_test",
    description=("The test under review is wrong: replace it with a corrected version. Every other test stays exactly "
                 "as it is."),
    parameters_json_schema={"type": "object", "properties": {
        "test": {"type": "string", "description": "The test function's name, e.g. 'test_r10_dispatch'."},
        "new_code": {"type": "string", "description": "The complete corrected function, with the same name."},
        "reason": {"type": "string", "description": "What was wrong, citing requirement numbers."}},
        "required": ["test", "new_code", "reason"]},
)
uphold_test_tool = types.FunctionDeclaration(
    name="uphold_test",
    description=("Use only when the failing test's expected result AGREES with the requirements, so the code must "
                 "change. Leaves the test as it is. If they disagree, call fix_test instead."),
    parameters_json_schema={"type": "object", "properties": {
        "test": {"type": "string", "description": "The test function's name, e.g. 'test_r10_dispatch'."},
        "test_expects": {"type": "string", "description": "The exact result the failing assert expects."},
        "requirement_says": {"type": "string", "description": "What the numbered requirement says should happen here."},
        "test_agrees_with_requirement": {"type": "boolean",
                                         "description": "True only if test_expects and requirement_says match."},
        "reason": {"type": "string", "description": "What the code gets wrong, citing requirement numbers."}},
        "required": ["test", "test_expects", "requirement_says", "test_agrees_with_requirement", "reason"]},
)
TOOL_MENU = [list_files_tool, read_file_tool, write_file_tool, run_tests_tool]
REVIEWABLE_TOOL_MENU = TOOL_MENU + [report_test_problem_tool]  # a builder whose tests a tester can re-check (team mode)
REVIEW_TOOL_MENU = [list_files_tool, read_file_tool, run_tests_tool, fix_test_tool, uphold_test_tool]  # no write_file


# ---- The real tools: plain Python, fenced to one workspace ----

DATABASE_FILES = (".db", ".sqlite", ".sqlite3")  # binary files the agents shouldn't see or download


def in_folder(path, folder):
    """Is the relative `path` inside `folder`? Decided from the text alone (no disk), so it works for any executor."""
    parts = Path(os.path.normpath(path)).parts
    return len(parts) > 0 and parts[0] == folder


def make_tools(executor, role="builder"):
    """Build the agent's tools for one role. The RULES live here; the executor does the actual work, wherever it runs."""

    def list_files(folder="."):
        # Hide database files: importing the app creates app.db, which is binary and useless to read.
        files = [path for path in executor.list_files(folder) if not path.endswith(DATABASE_FILES)]
        return "\n".join(files) or "(no files)"

    def read_file(path):
        try:
            return executor.read_file(path)
        except FileNotFoundError:  # every executor raises this for a missing file
            # Normal while exploring (e.g. app/schemas.py before anyone wrote it): say so plainly, not as an error.
            return f"NOT FOUND: {path} doesn't exist yet. Use list_files to see the files, or write_file to create it."

    def write_file(path, content):
        if os.path.isabs(path):
            return "REFUSED: use a path relative to the project, like app/main.py."
        if os.path.normpath(path) in PROTECTED:
            return f"REFUSED: {path} is part of the project's setup. Read it, but don't change it."
        in_tests = in_folder(path, "tests")
        if role == "builder" and in_tests:
            return "REFUSED: tests/ holds the requirements. Change the app code instead."
        if role == "tester" and not in_tests:
            return "REFUSED: the tester only writes tests, in tests/. The developer writes the app."
        blocked = [match.group(0) for pattern in FORBIDDEN if (match := re.search(pattern, content))]
        if blocked:
            return (f"REFUSED: this project may not use {', '.join(blocked)}. Keep to FastAPI, Pydantic and "
                    "plain Python data structures: no files, processes, network or environment access.")
        executor.write_file(path, content)
        return f"Wrote {path} ({len(content)} characters)"

    def pytest(*extra):
        # -B: never cache bytecode. Vercel Sandbox gives every written file mtime 0, and Python reuses a .pyc whose source
        # has the same mtime and size, so a same-size edit (1 -> 2) would silently keep running the old code.
        exit_code, output = executor.run([executor.python, "-B", "-m", "pytest", "-q", "-p", "no:cacheprovider", *extra],
                                         timeout=60)
        if exit_code is None:  # the executor stopped it: it ran too long
            return "TIMEOUT: the tests took longer than 60 seconds."
        return output[-4000:]  # the end has the failures and the summary; keeps what we send small

    def run_tests():
        return pytest()

    def collect_tests():
        """Load the tests without running them: catches broken test files. Used by our checks, not the model."""
        return pytest("--collect-only")

    def report_test_problem(test, reason=""):
        # Only a test that fails right now can be reported; otherwise "report it" is an easy way out of the work.
        name = str(test).split("::")[-1].strip()
        if name not in failing_tests(run_tests()):
            return f"REFUSED: {name} isn't failing right now, so there is nothing to report. Keep working."
        return f"REPORTED: {name}"

    tools = {"list_files": list_files, "read_file": read_file, "write_file": write_file, "run_tests": run_tests,
             "collect_tests": collect_tests}
    if role == "builder":
        tools["report_test_problem"] = report_test_problem
    return tools

def seed_workspace(executor, include_tests=True, template=TEMPLATE):
    """Put a starter template into the workspace: its app files, its test setup (conftest.py), and its built-in
    tests (tests/test_*.py) unless the tester will write its own.

    Goes through the executor, so it works wherever the workspace lives: a local folder or a remote sandbox.
    """
    for source in sorted(template.rglob("*")):
        if not source.is_file() or "__pycache__" in source.parts or source.name.endswith(DATABASE_FILES):
            continue
        relative = source.relative_to(template).as_posix()  # e.g. "app/main.py"
        if not include_tests and relative.startswith("tests/test_"):
            continue  # skip the built-in tests only: tests/conftest.py is setup, and is always needed
        executor.write_file(relative, source.read_text())

def tests_pass(output):
    """pytest's last line is the summary, e.g. '10 passed in 0.3s' or '2 failed, 8 passed in 0.4s'."""
    summary = output.strip().splitlines()[-1] if output.strip() else ""
    return "passed" in summary and "failed" not in summary and "error" not in summary


def failing_tests(output):
    """The names of the failing tests in pytest's output: 'FAILED tests/test_app.py::test_r3_x - assert ...' ->
    {'test_r3_x'}. A file that doesn't even load ('ERROR tests/test_app.py') names no test, so it isn't counted."""
    return set(re.findall(r"^(?:FAILED|ERROR) \S+::(\w+)", output, re.MULTILINE))


class ProgressWatch:
    """Notices a builder going round in circles: `limit` test runs in a row without fewer failing tests than its best
    so far. Runs where nothing loads (a syntax error) don't count either way: that's a typo, not being stuck."""

    def __init__(self, limit=STUCK_AFTER):
        self.limit, self.best, self.flat, self.suspects = limit, None, 0, set()

    def record(self, output):
        """Feed one test run's output. True when the builder is stuck."""
        failing = failing_tests(output)
        if not failing:
            return False
        if self.best is None or len(failing) < self.best:
            self.best, self.flat, self.suspects = len(failing), 0, set(failing)  # progress: start counting again
            return False
        self.flat += 1
        self.suspects = (self.suspects & failing) or failing  # the tests that failed every time, if there are any
        return self.flat >= self.limit


# ---- "Done" checks: our code decides when a role is finished, not the model ----

def warning_count(output):
    """How many warnings pytest's summary line reports: '20 passed, 4 warnings in 0.8s' -> 4."""
    summary = output.strip().splitlines()[-1] if output.strip() else ""
    match = re.search(r"(\d+) warnings?\b", summary)
    return int(match.group(1)) if match else 0


def only_warnings_left(output):
    """Every test passes, but pytest still reports warnings."""
    return tests_pass(output) and warning_count(output) > 0


def builder_done(tools):
    """Done when every test passes with no warnings. Returns (done, test_output, message for the agent if not)."""
    output = tools["run_tests"]()
    if not tests_pass(output):
        return False, output, "Not done: the tests still fail. Here is the output:\n" + output
    if warning_count(output):
        return False, output, (f"Not done: every test passes, but pytest shows {warning_count(output)} warning(s). "
                               "Fix the code that causes them (usually a deprecated name: the output says which):\n"
                               + output)
    return True, output, ""


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


def collected_count(collected):
    """How many tests `pytest --collect-only` found, or None if they didn't load."""
    summary = collected.strip().splitlines()[-1] if collected.strip() else ""
    match = re.search(r"(\d+) tests? collected", summary)
    return None if "error" in summary or not match else int(match.group(1))


def tests_are_sound(tools, min_tests=3):
    """The shape every tester's work must keep: the tests load cleanly (at least `min_tests` of them);
    tests/REQUIREMENTS.md lists the requirements; every one has a test named after it; no test covers more than 2.
    Returns (ok, output, message for the agent if not ok)."""
    collected = tools["collect_tests"]()
    count = collected_count(collected)
    if count is None or count < min_tests:
        return False, collected, (f"Not done: tests/test_app.py must load without errors and contain at least "
                                  f"{min_tests} tests. pytest says:\n" + collected)
    requirements = tools["read_file"]("tests/REQUIREMENTS.md")
    tests = tools["read_file"]("tests/test_app.py")
    missing = [path for path, text in (("tests/REQUIREMENTS.md", requirements), ("tests/test_app.py", tests))
               if text.startswith("NOT FOUND")]
    if missing:
        return False, collected, (f"Not done: write both tests/REQUIREMENTS.md and tests/test_app.py "
                                  f"({' and '.join(missing)} doesn't exist yet).")
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
    return True, collected, ""


def tester_done(tools):
    """Done when the tests are sound (see tests_are_sound) and they FAIL on the empty starter app, so they actually
    test something."""
    sound, output, feedback = tests_are_sound(tools)
    if not sound:
        return False, output, feedback
    output = tools["run_tests"]()
    if tests_pass(output):
        return False, output, ("Not done: every test already passes on the empty starter app, so the tests "
                               "don't check anything. Make them test the features from the request.")
    return True, output, ""


def review_done(tools, min_tests):
    """A review is done when the tests are still sound and none were removed: fixing a wrong test must never mean
    deleting it. (The app now exists, so unlike tester_done the tests may pass.)"""
    return tests_are_sound(tools, min_tests=min_tests)


# ---- The agent loop ----

def run_agent(workspace, on_step=None, model=MODEL, max_steps=15, system_prompt=None,
              role="builder", start_message=None, executor=None, reviewable=False, done_check=None, review=None):
    """Run one agent (role "builder" or "tester") in `workspace` until its done-check passes or `max_steps` runs out.

    on_step(event) is called after every tool call with
        {"step": int, "tool": str, "args": dict, "result": str}
    reviewable=True (a builder in team mode, where a tester can re-check the tests): the builder may call
    report_test_problem, and is stopped early when it stops making progress (see ProgressWatch).
    done_check(tools) -> (done, output, feedback) replaces the role's usual check (a tester's review uses this).
    review=ReviewDecisions (a tester's review): swaps write_file for fix_test and uphold_test, and ends the run as
    soon as every test under review is decided.
    Returns {"passed": bool, "steps": int, "summary": str, "test_output": str, "model": the model that finished},
    plus "stuck": [test names] if it was stopped for making no progress, or "disputed": {"test", "reason"} if it
    reported a test.
    """
    if role not in ("builder", "tester"):
        raise ValueError(f"unknown role: {role}")
    is_tester = role == "tester"
    system_prompt = system_prompt or (TESTER_PROMPT if is_tester else SYSTEM_PROMPT)
    start_message = start_message or START_MESSAGE
    done_check = done_check or (tester_done if is_tester else builder_done)
    reviewable = reviewable and not is_tester
    watch = ProgressWatch() if reviewable else None

    # Created here, not at import time, so importing this file never needs a key. The timeout matters: without it,
    # a model that never answers (seen with the strong model under load) hangs the run forever and no fallback starts.
    client = genai.Client(http_options=types.HttpOptions(timeout=REQUEST_TIMEOUT_MS))

    executor = executor or LocalExecutor(workspace)  # where the work happens; local unless told otherwise
    tools = make_tools(executor, role=role)
    menu = REVIEWABLE_TOOL_MENU if reviewable else TOOL_MENU
    if review:
        del tools["write_file"]  # a review changes tests only through fix_test, one test at a time
        tools.update(fix_test=review.fix_test, uphold_test=review.uphold_test)
        menu = REVIEW_TOOL_MENU

    config = types.GenerateContentConfig(
        system_instruction=system_prompt,
        tools=[types.Tool(function_declarations=menu)],
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

    def stopped(step, output, summary, **extra):
        return {"passed": False, "steps": step, "summary": summary, "test_output": output,
                "model": current["model"], **extra}

    def stuck(step, output):
        names = sorted(watch.suspects)
        return stopped(step, output, f"Stopped: no progress in {watch.limit} test runs, still failing "
                                     f"{', '.join(names)}", stuck=names)

    # Warnings: the builder is sent back to fix them, but only WARNING_NAGS times. One it can't fix (say, caused by
    # a test, which it may not edit) must not burn the whole budget: then the work is accepted, warnings and all.
    warning_nags = 0

    for step in range(1, max_steps + 1):
        response = ask_model(history)
        history.append(response.candidates[0].content)

        calls = response.function_calls
        if not calls:
            # The model says it's finished. Don't take its word for it: check ourselves.
            done, output, feedback = done_check(tools)
            if not done and done_check is builder_done and only_warnings_left(output):
                if warning_nags >= WARNING_NAGS:
                    done = True
                warning_nags += 1
            if done:
                return {"passed": True, "steps": step, "summary": response.text or "", "test_output": output,
                        "model": current["model"]}
            if watch and watch.record(output):
                return stuck(step, output)
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
            if call.name == "report_test_problem" and result.startswith("REPORTED"):
                test = result.removeprefix("REPORTED: ")
                return stopped(step, tools["run_tests"](), f"Reported {test} as contradicting the requirements",
                               disputed={"test": test, "reason": str(args.get("reason", ""))})
            if watch and call.name == "run_tests" and watch.record(result):
                return stuck(step, result)
            results.append(types.Part(function_response=types.FunctionResponse(
                id=call.id, name=call.name, response={"result": result})))
        history.append(types.Content(role="user", parts=results))

        if review and review.complete:  # every test under review has a decision: the review is over
            done, output, feedback = done_check(tools)
            if done:
                return {"passed": True, "steps": step, "summary": review.verdict, "test_output": output,
                        "model": current["model"]}
            history.append(types.Content(role="user", parts=[types.Part(text=feedback)]))

    # Out of steps. Check once more: the work may be done even though the agent never said so.
    done, output, _ = done_check(tools)
    if not done and done_check is builder_done and only_warnings_left(output):
        done = True  # every test passes; the warnings it didn't get to still show in the orchestrator's test run
    return {"passed": done, "steps": max_steps, "summary": "Stopped: hit the step limit", "test_output": output,
            "model": current["model"]}


# ---- The feedback loop (C4): when the builder can't pass, is the test wrong or the code? ----

def snapshot_tests(executor):
    """The tester's files, {path: text}: everything in tests/ a review may change (conftest.py is protected)."""
    return {path: executor.read_file(path) for path in executor.list_files("tests")
            if "__pycache__" not in path and not path.endswith(DATABASE_FILES) and path not in PROTECTED}


TESTS_FILE = "tests/test_app.py"


def replace_test(source, name, new_code):
    """`source` with the test function `name` replaced by `new_code`. Returns (new_source, problem); one is None.

    Requiring the tester to act makes it tempting to "fix" a test by gutting it, so a fix must keep the test's name and
    its number of checks, and can't assert a constant (`assert True` checks nothing).
    """
    new_code = textwrap.dedent(new_code).strip()
    try:
        replacement = ast.parse(new_code).body
    except SyntaxError as error:
        return None, f"that code doesn't parse: {error.msg}."
    if len(replacement) != 1 or not isinstance(replacement[0], ast.FunctionDef) or replacement[0].name != name:
        return None, f"send exactly one complete function named {name}, and nothing else."
    new = replacement[0]
    old = next((node for node in ast.parse(source).body if isinstance(node, ast.FunctionDef) and node.name == name), None)
    if old is None:
        return None, f"{name} isn't a top-level test in {TESTS_FILE}."
    if ast.dump(new) == ast.dump(old):
        return None, "that is the same test. If the test is right, call uphold_test instead."
    old_checks = [node for node in ast.walk(old) if isinstance(node, ast.Assert)]
    new_checks = [node for node in ast.walk(new) if isinstance(node, ast.Assert)]
    if len(new_checks) < len(old_checks):
        return None, f"keep every check: the test had {len(old_checks)} assert(s), and this has {len(new_checks)}."
    if any(isinstance(check.test, ast.Constant) for check in new_checks):
        return None, "an assert on a constant checks nothing."
    first = old.decorator_list[0].lineno if old.decorator_list else old.lineno
    lines = source.splitlines()
    lines[first - 1:old.end_lineno] = new_code.splitlines()
    return "\n".join(lines) + "\n", None


class ReviewDecisions:
    """What one tester review decided. Every test under review must end up fixed (the test was wrong, and really
    changed) or upheld (the test is right, so the code must change).

    Seen 11-22: reviewers wrote "fixed: this test is wrong" without changing it, and the builder was then told the
    tests were right. So a decision is a tool call our code can check, never a sentence.
    """

    def __init__(self, executor, tests, min_tests, on_write=None):
        self.executor, self.tests, self.min_tests, self.on_write = executor, list(tests), min_tests, on_write
        self.rules = make_tools(executor, "tester")  # the tester's write rules and code guard still apply
        self.decided = {}  # test name -> (was it fixed?, reason)

    @property
    def complete(self):
        return all(test in self.decided for test in self.tests)

    @property
    def fixed(self):
        return [test for test in self.tests if test in self.decided and self.decided[test][0]]

    @property
    def upheld(self):
        return [test for test in self.tests if test in self.decided and not self.decided[test][0]]

    @property
    def verdict(self):
        return " ".join(f"{test}: the test was wrong and is now fixed ({reason or 'no reason given'})." if was_fixed
                        else f"{test}: the test is right, so the code must change ({reason})."
                        for test, (was_fixed, reason) in self.decided.items())

    def _refusal(self, test):
        if test not in self.tests:
            return f"REFUSED: {test} isn't under review. The tests under review are: {', '.join(self.tests)}."
        if test in self.decided:
            return f"REFUSED: {test} is already decided."
        return None

    def fix_test(self, test, new_code, reason=""):
        test = str(test).split("::")[-1].strip()
        refusal = self._refusal(test)
        if refusal:
            return refusal
        before = self.executor.read_file(TESTS_FILE)
        fixed, problem = replace_test(before, test, str(new_code))
        if problem:
            return f"REFUSED: {problem}"
        wrote = self.rules["write_file"](TESTS_FILE, fixed)  # applies the code guard
        if wrote.startswith("REFUSED"):
            return wrote
        sound, _, feedback = tests_are_sound(self.rules, min_tests=self.min_tests)
        if not sound:
            self.executor.write_file(TESTS_FILE, before)
            return f"REFUSED: that change was undone because it breaks the tests. {feedback}"
        if self.on_write:  # logged like a write, so the dashboard's files show what's really there
            self.on_write({"step": 0, "tool": "write_file", "args": {"path": TESTS_FILE, "content": fixed},
                           "result": f"Wrote {TESTS_FILE}"})
        self.decided[test] = (True, str(reason).strip())
        return f"FIXED: {test}"

    def uphold_test(self, test, reason="", test_expects="", requirement_says="", test_agrees_with_requirement=False):
        test = str(test).split("::")[-1].strip()
        refusal = self._refusal(test)
        if refusal:
            return refusal
        # Seen 11-22: reviewers wrote "this test contradicts requirement 8" and then upheld it. Making them state the
        # comparison turns that slip into something our code can refuse.
        if test_agrees_with_requirement is not True:
            return (f"REFUSED: you said {test} disagrees with the requirements, so the test is wrong. Call fix_test with "
                    "the corrected function.")
        reason = str(reason).strip()
        if not reason or not str(test_expects).strip() or not str(requirement_says).strip():
            return "REFUSED: say what the test expects, what the requirement says, and what the code gets wrong."
        self.decided[test] = (False, reason)
        return f"UPHELD: {test}"

    def done(self, tools):
        """The review's done-check: every test decided, and the tests still sound."""
        if not self.complete:
            left = [test for test in self.tests if test not in self.decided]
            return False, "", (f"Not done: decide {', '.join(left)}. Call fix_test if a test is wrong, or uphold_test "
                               "if the code is. Saying so in a message changes nothing.")
        return review_done(tools, self.min_tests)


def review_rounds(workspace, executor, outcome, model=MODEL, step_logger=None, on_note=None, rounds=REVIEW_ROUNDS):
    """Team mode, after the builder's run. While it hasn't passed (it got stuck, reported a test, or ran out of
    steps) and rounds remain:
      1. the tester re-checks the failing tests against its own requirements: it fixes a wrong test, or leaves a
         right one alone and says what the code gets wrong. A review that leaves the tests broken is undone.
      2. the orchestrator runs the tests itself: if fixing the tests was all it took, the work is done.
      3. otherwise the builder gets another round, with a fresh history and the tester's verdict.

    step_logger(label, role) returns the on_step callback for one agent run; on_note(text, output) reports the
    orchestrator's own decisions. Returns the final outcome, with "steps" counting every builder round and
    "reviews": [{"round", "tests", "changed", "verdict", "model"}].
    """
    note = on_note or (lambda text, output="": None)
    logger = step_logger or (lambda label, role: None)
    reviews, steps = [], outcome["steps"]

    for round_number in range(1, rounds + 1):
        if outcome["passed"]:
            break
        dispute = outcome.get("disputed")
        failing = [dispute["test"]] if dispute else (outcome.get("stuck") or sorted(failing_tests(outcome["test_output"])))
        if not failing:
            note("No test to review: the code itself doesn't load", outcome["test_output"])
            break
        why = (f"the developer reported {dispute['test']}" if dispute else
               "the developer stopped making progress" if outcome.get("stuck") else "the developer ran out of steps")
        note(f"Review {round_number}: {why}, so the tester re-checks {', '.join(failing)}", outcome["test_output"])

        before = snapshot_tests(executor)
        min_tests = collected_count(make_tools(executor, "tester")["collect_tests"]()) or 3  # a review may not delete tests
        label = f"Tester (review {round_number}) · "
        decisions = ReviewDecisions(executor, failing, min_tests, on_write=logger(label, "tester"))
        review = run_agent(workspace, on_step=logger(label, "tester"), model=model,
                           max_steps=REVIEW_STEPS, system_prompt=REVIEWER_PROMPT, role="tester", executor=executor,
                           start_message=review_message(failing, outcome["test_output"], dispute),
                           done_check=decisions.done, review=decisions)
        if review["passed"] and decisions.complete:
            fixed, upheld, verdict = decisions.fixed, decisions.upheld, decisions.verdict
        else:  # it didn't decide every test, or left them broken: put them back exactly as they were
            undo = logger(f"Tester (review {round_number}, undone) · ", "tester")
            for path in snapshot_tests(executor):
                content = before.get(path, "")  # a file it added is emptied (executors can't delete)
                executor.write_file(path, content)
                if undo:  # logged like a write, so the dashboard's files show what's really there
                    undo({"step": 0, "tool": "write_file", "args": {"path": path, "content": content},
                          "result": f"Restored {path}"})
            fixed, upheld, verdict = [], [], "The review didn't finish, so the tests were left as they were."
        changed = bool(fixed)
        reviews.append({"round": round_number, "tests": failing, "changed": changed, "fixed": fixed, "upheld": upheld,
                        "verdict": verdict, "model": review.get("model", model)})

        output = make_tools(executor, "builder")["run_tests"]()  # our own check, not the tester's word
        if tests_pass(output):
            note(f"Review {round_number}: the tester corrected the tests, and every test passes now", output)
            outcome = {**outcome, "passed": True, "test_output": output,
                       "summary": f"Passed after the tester corrected {', '.join(failing)}"}
            break

        outcome = run_agent(workspace, on_step=logger(f"Builder (round {round_number + 1}) · ", "builder"),
                            model=model, max_steps=RETRY_STEPS, system_prompt=BUILDER_DB_PROMPT, executor=executor,
                            start_message=retry_message(failing, verdict, changed, bool(fixed or upheld)),
                            reviewable=True)
        steps += outcome["steps"]

    return {**outcome, "steps": steps, "reviews": reviews}


# ---- Terminal version: python orchestrator/agent.py ----

def print_step(event, label=""):
    """Short log line: hide file contents, show just the last line of each result."""
    shown = {k: (f"<{len(v)} chars>" if k == "content" else v) for k, v in event["args"].items()}
    result = event["result"].strip()
    last_line = result.splitlines()[-1][:80] if result else ""
    print(f"{label}Step {event['step']}: {event['tool']}({shown}) -> {last_line}")

if __name__ == "__main__":
    from dotenv import load_dotenv

    repo = Path(__file__).parent.parent
    load_dotenv(repo / "backend" / ".env")
    workspace = repo / "spike" / "runs" / datetime.now().strftime("%Y%m%d-%H%M%S") / "workspace"
    description = " ".join(sys.argv[1:])

    # EXECUTOR picks WHERE the code runs: "vercel" (remote microVM), "docker" (local container), else this machine.
    choice = os.getenv("EXECUTOR", "local")
    if choice == "vercel":
        executor = SandboxExecutor()
    elif choice == "docker":
        executor = DockerExecutor(workspace)
    else:
        executor = LocalExecutor(workspace)
    print("Executor:", type(executor).__name__)
    started = time.time()

    with executor:  # a Vercel sandbox is destroyed when this block ends, even after an error
        # Team mode: the database starter, and the tester writes the tests. Otherwise: the todo app + built-in tests.
        seed_workspace(executor, include_tests=not description, template=TEMPLATE_DB if description else TEMPLATE)

        if description:
            print("\n=== Tester ===")
            tester = run_agent(workspace, on_step=print_step, role="tester",
                               start_message=tester_message(description), max_steps=12, executor=executor)
            print(f"Tester {'done' if tester['passed'] else 'FAILED'}: {tester['test_output'].strip().splitlines()[-1]}")
            if not tester["passed"]:
                sys.exit(1)
            print("\n=== Builder ===")

        outcome = run_agent(workspace, on_step=print_step, max_steps=25 if description else 15, executor=executor,
                            system_prompt=BUILDER_DB_PROMPT if description else None, reviewable=bool(description))
        if description:  # the feedback loop: a stuck builder gets its failing tests re-checked by the tester
            outcome = review_rounds(workspace, executor, outcome,
                                    step_logger=lambda label, role: (lambda event: print_step(event, label)),
                                    on_note=lambda text, output="": print(f"\n=== Orchestrator: {text} ==="))

        # Keep a local copy of the result: a Vercel sandbox (and its files) disappears when the block ends.
        for path in executor.list_files():
            if path.endswith(DATABASE_FILES):
                continue  # binary database file: not text, and not part of the code
            local = workspace / path
            local.parent.mkdir(parents=True, exist_ok=True)
            local.write_text(executor.read_file(path))

    verdict = "DONE, verified" if outcome["passed"] else "NOT DONE"
    print(f"\n{verdict} after {outcome['steps']} steps: {outcome['test_output'].strip().splitlines()[-1]}")
    print(f"Time: {time.time() - started:.0f}s    Code: {workspace / 'app' / 'main.py'}")
