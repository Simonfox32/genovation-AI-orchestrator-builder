"""Build the sandbox snapshot ONCE: a Vercel Sandbox with FastAPI + pytest installed, saved for reuse.

The hosted version of `docker build`: install packages while the network is on, then freeze the result.
Every agent sandbox starts from this snapshot with the network OFF.

Run:   backend/.venv/bin/python orchestrator/sandbox/build_vercel_snapshot.py
Then:  put the printed VERCEL_SANDBOX_SNAPSHOT_ID=... line in backend/.env
"""
from pathlib import Path

from dotenv import load_dotenv
from vercel.sandbox import sync as sandbox

# Same pins as requirements.txt and the Dockerfile. sqlalchemy: the database starter template (SQLite is built in).
PACKAGES = ["fastapi==0.143.3", "pytest==9.1.1", "httpx2==2.13.1", "sqlalchemy==2.1.6"]

load_dotenv(Path(__file__).resolve().parents[2] / "backend" / ".env")

# No `with` here: its cleanup would delete the snapshot we're about to make (see the docs on destroy).
box = sandbox.create_sandbox(execution_time_limit=600, persistent=False)  # default network: on, for pip
try:
    print("installing:", " ".join(PACKAGES))
    result = box.run_command("python", ["-m", "pip", "install", "--user", "-q", *PACKAGES],
                             capture_output=True, kill_after=300)
    print("pip exit code:", result.returncode)
    if result.returncode != 0:
        raise SystemExit(result.stderr.decode()[-2000:])

    check = box.run_command("python", ["-c", "import fastapi, pytest, sqlalchemy, sqlite3; print('fastapi', fastapi.__version__, "
                                       "'| pytest', pytest.__version__, '| sqlalchemy', sqlalchemy.__version__, "
                                       "'| sqlite', sqlite3.sqlite_version)"],
                            capture_output=True)
    print(check.stdout.decode().strip())

    snapshot = box.snapshot(expiration=0)  # 0 = never expires
    print(f"\nVERCEL_SANDBOX_SNAPSHOT_ID={snapshot.id}")
finally:
    box.destroy()  # throw away the build sandbox; the snapshot stays