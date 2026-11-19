"""Tests for LocalExecutor (orchestrator/executors.py): the four actions, the fence, and the stripped environment."""
import pytest

import orchestration  # noqa: F401  (puts the repo root on the import path)
from orchestrator.executors import LocalExecutor


@pytest.fixture
def executor(tmp_path):
    return LocalExecutor(tmp_path)


def test_write_list_and_read(executor):
    executor.write_file("app/main.py", "x = 1")

    assert executor.list_files() == ["app/main.py"]
    assert executor.read_file("app/main.py") == "x = 1"


@pytest.mark.parametrize("path", ["../outside.txt", "app/../../outside.txt", "/etc/hosts"])
def test_paths_outside_the_workspace_are_refused(executor, path):
    with pytest.raises(ValueError):
        executor.read_file(path)


def test_run_reports_the_exit_code_and_output(executor):
    executor.write_file("ok.py", "print('hi')")
    executor.write_file("bad.py", "raise SystemExit(3)")

    assert executor.run([executor.python, "ok.py"]) == (0, "hi\n")
    assert executor.run([executor.python, "bad.py"])[0] == 3


def test_run_stops_code_that_takes_too_long(executor):
    executor.write_file("slow.py", "import time\ntime.sleep(5)")

    exit_code, output = executor.run([executor.python, "slow.py"], timeout=1)

    assert exit_code is None
    assert output.startswith("TIMEOUT")


def test_run_hides_our_environment_variables(executor, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "secret-123")
    executor.write_file("peek.py", "import os\nprint('GEMINI_API_KEY' in os.environ)")

    assert executor.run([executor.python, "peek.py"]) == (0, "False\n")


# ---- SandboxExecutor reports missing files the way LocalExecutor does (no real sandbox: a fake `box`) ----

class FakeSandboxFiles:
    """Stands in for a Vercel sandbox's `box.fs`: holds no files, and fails like the real one."""
    def read_text(self, path):
        raise self.not_found(path)

    def listdir(self, path):
        raise self.not_found(path)

    @staticmethod
    def not_found(path):
        from vercel.sandbox import SandboxPathNotFoundError
        return SandboxPathNotFoundError(path, operation="read", cwd=None, cause=None)


def empty_sandbox():
    from types import SimpleNamespace
    from orchestrator.executors import SandboxExecutor
    sandbox = SandboxExecutor.__new__(SandboxExecutor)  # skip __init__: it would create a real Vercel sandbox
    sandbox.box = SimpleNamespace(fs=FakeSandboxFiles())
    return sandbox


def test_every_executor_raises_file_not_found_for_a_missing_file(executor):
    # Our code catches FileNotFoundError (e.g. the tester's done-check). Vercel's own error isn't one: before
    # this, a missing file crashed that check in the sandbox instead of telling the agent to write the file.
    for each in (executor, empty_sandbox()):
        with pytest.raises(FileNotFoundError):
            each.read_file("app/schemas.py")


def test_every_executor_lists_a_missing_folder_as_empty(executor):
    assert executor.list_files("nope") == []
    assert empty_sandbox().list_files("nope") == []
