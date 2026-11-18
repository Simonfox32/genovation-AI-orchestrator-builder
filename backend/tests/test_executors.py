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
