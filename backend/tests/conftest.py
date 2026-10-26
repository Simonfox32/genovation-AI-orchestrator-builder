import os

import pytest
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import get_db, get_session_factory
from main import app
from models import Base
from orchestration import get_pipeline

load_dotenv()

os.environ["DEMO_STEP_DELAY_SECONDS"] = "0"  # No artificial delay in tests

TEST_DATABASE_URL = os.environ["TEST_DATABASE_URL"]

# Safety check: these fixtures drop every table, so refuse to run against anything that
# isn't clearly a test database (e.g. if TEST_DATABASE_URL is copied from DATABASE_URL by mistake).
if not TEST_DATABASE_URL.rsplit("/", 1)[-1].endswith("_test"):
    raise RuntimeError(f"TEST_DATABASE_URL must point to a database ending in '_test', got: {TEST_DATABASE_URL}")

test_engine = create_engine(TEST_DATABASE_URL)
TestSessionLocal = sessionmaker(bind=test_engine)


def override_get_db():
    db = TestSessionLocal()
    try:
        yield db
    finally:
        db.close()


# Every request made through TestClient now gets a test-database session instead of a dev one,
# and the background pipeline opens its sessions on the test database too.
app.dependency_overrides[get_db] = override_get_db
app.dependency_overrides[get_session_factory] = lambda: TestSessionLocal


@pytest.fixture
def no_pipeline():
    """For tests about the API itself: create projects without running the background pipeline."""
    app.dependency_overrides[get_pipeline] = lambda: (lambda project_id, session_factory: None)
    yield
    app.dependency_overrides.pop(get_pipeline, None)


@pytest.fixture(autouse=True)
def clean_database():
    """Give every test empty tables, so no test depends on what another test left behind."""
    Base.metadata.drop_all(test_engine)
    Base.metadata.create_all(test_engine)
    yield


@pytest.fixture
def db_session():
    """A separate session for tests that want to look at the database directly."""
    db = TestSessionLocal()
    try:
        yield db
    finally:
        db.close()
