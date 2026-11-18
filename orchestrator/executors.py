"""Where the agents' actions actually happen. The agent decides WHAT to do; an executor decides WHERE.

Every executor offers the same four actions, so the agent never needs to know which one it has:
    list_files(folder)        -> list of paths, relative to the workspace
    read_file(path)           -> the file's text
    write_file(path, content) -> None
    run(command, timeout)     -> (exit_code, output); exit_code is None if it timed out
plus `python`: the command that starts Python where the code runs.
"""
import os
import subprocess
import sys
from pathlib import Path
import uuid
import time

class LocalExecutor:
    """Runs everything on THIS machine, inside one folder.

    Not a sandbox: code it runs can reach anything this machine's user can (see spike/sandbox_lesson1.py).
    """

    python = sys.executable  # the Python running us, which has pytest installed

    def __init__(self, workspace):
        # Resolve once, so every path uses the same spelling (macOS: /var is a link to /private/var).
        self.workspace = Path(workspace).resolve()

        # `with executor:` support, so every executor can be used the same way. A local folder needs no cleanup.
        
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        pass

    def _path(self, path):
        """The model's relative path -> a real path, refusing anything outside the workspace."""
        full = (self.workspace / path).resolve()
        if not full.is_relative_to(self.workspace):
            raise ValueError(f"{path} is outside the project")
        return full

    def list_files(self, folder="."):
        root = self._path(folder)
        return [str(p.relative_to(self.workspace)) for p in sorted(root.rglob("*"))
                if p.is_file() and "__pycache__" not in p.parts]

    def read_file(self, path):
        return self._path(path).read_text()

    def write_file(self, path, content):
        full = self._path(path)
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content)

    def run(self, command, timeout=60):
        # Strip our environment (it holds the Gemini key) before running anything.
        env = {"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", "")}
        try:
            result = subprocess.run(command, cwd=self.workspace, env=env,
                                    capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return None, f"TIMEOUT: took longer than {timeout} seconds."
        return result.returncode, result.stdout + result.stderr
class DockerExecutor(LocalExecutor):
    """Files live in a folder on this machine; commands run INSIDE a locked-down container that sees only that folder.

    A real sandbox: the code can't see this machine's files or environment variables, has no network,
    isn't root, and is capped at 512 MB of memory, 1 CPU and 64 processes.
    """

    python = "python"                    # the Python INSIDE the image
    image = "ai-build-router-sandbox"    # built from orchestrator/sandbox/Dockerfile

    def __init__(self, workspace):
        super().__init__(workspace)
        # Colima only shares folders under your home folder. Anything else appears EMPTY inside the
        # container, silently. Refuse it loudly instead.
        if not self.workspace.is_relative_to(Path.home().resolve()):
            raise ValueError(f"DockerExecutor needs a workspace under {Path.home()}, got {self.workspace}")

    def run(self, command, timeout=60):
        name = f"sandbox-{uuid.uuid4().hex[:12]}"  # a unique name, so we can kill this exact container
        docker_command = [
            "docker", "run", "--rm", "--name", name,
            "--network", "none",                           # no internet
            "--user", "65534:65534",                       # not root
            "--memory", "512m", "--memory-swap", "512m",   # memory cap
            "--cpus", "1", "--pids-limit", "64",           # CPU and process caps
            "--read-only", "--tmpfs", "/tmp",              # system read-only; scratch space in /tmp
            "-v", f"{self.workspace}:/workspace", "-w", "/workspace",  # share ONLY the workspace
            self.image, *command,
        ]
        try:
            result = subprocess.run(docker_command, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            # Stopping the `docker run` command does NOT stop the container. Kill it by name.
            subprocess.run(["docker", "kill", name], capture_output=True)
            return None, f"TIMEOUT: took longer than {timeout} seconds."
        output = result.stdout + result.stderr
        if result.returncode == 137:  # 128 + 9: killed by the out-of-memory killer
            output += "\nKILLED: the code used more than 512 MB of memory."
        return result.returncode, output

class SandboxExecutor(LocalExecutor):
    """Everything happens in a Vercel Sandbox: a Firecracker microVM in Vercel's data centre.

    One sandbox per agent run: `with SandboxExecutor() as executor: ...` creates it and destroys it afterwards.
    It starts from our snapshot (FastAPI + pytest preinstalled, see orchestrator/sandbox/build_vercel_snapshot.py)
    with the network OFF. Files live in a `workspace/` folder inside the sandbox.
    Needs VERCEL_TOKEN, VERCEL_TEAM_ID, VERCEL_PROJECT_ID and VERCEL_SANDBOX_SNAPSHOT_ID in the environment.
    """

    python = "python"
    root = "workspace"  # inside the sandbox, away from the home folder's dotfiles

    def __init__(self, snapshot_id=None, lifetime=1800):
        # Imported here, not at the top: only code that really uses a Vercel sandbox needs the package installed.
        from vercel.sandbox import NetworkPolicy, SnapshotSource
        from vercel.sandbox import sync as sandbox

        self.box = sandbox.create_sandbox(
            source=SnapshotSource(snapshot_id=snapshot_id or os.environ["VERCEL_SANDBOX_SNAPSHOT_ID"]),
            network_policy=NetworkPolicy.deny_all(),  # no internet, like --network none
            execution_time_limit=lifetime,            # the sandbox can't outlive this (seconds)
            persistent=False,
        )
        self.box.fs.mkdir(self.root, recursive=True)

    def close(self):
        self.box.destroy()  # explicit destroy keeps snapshots (see the note in Lesson 5b)

    def _path(self, path):
        """The model's relative path -> a path inside workspace/, refusing anything that would leave it."""
        normalized = os.path.normpath(path)
        if os.path.isabs(normalized) or normalized == ".." or normalized.startswith("../"):
            raise ValueError(f"{path} is outside the project")
        return self.root if normalized == "." else f"{self.root}/{normalized}"

    def list_files(self, folder="."):
        files, folders = [], [self._path(folder)]
        while folders:  # walk the folder tree, one listdir call (= one network request) per folder
            current = folders.pop()
            for entry in self.box.fs.listdir(current):
                full = f"{current}/{entry.path}"
                if entry.kind == "directory" and entry.path != "__pycache__":
                    folders.append(full)
                elif entry.kind == "file":
                    files.append(full.removeprefix(self.root + "/"))
        return sorted(files)

    def read_file(self, path):
        return self.box.fs.read_text(self._path(path))

    def write_file(self, path, content):
        full = self._path(path)
        if "/" in full:
            self.box.fs.mkdir(full.rsplit("/", 1)[0], recursive=True)  # make missing folders, like mkdir -p
        self.box.fs.write_text(full, content)

    def run(self, command, timeout=60):
        started = time.monotonic()
        result = self.box.run_command(command[0], list(command[1:]), cwd=self.root,
                                      capture_output=True, kill_after=timeout)
        output = result.stdout.decode(errors="replace") + result.stderr.decode(errors="replace")
        if time.monotonic() - started >= timeout:  # Vercel killed it (kill_after)
            return None, f"TIMEOUT: took longer than {timeout} seconds."
        return result.returncode, output