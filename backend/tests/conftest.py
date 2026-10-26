import os

import pytest
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import get_db
from main import app
from models import Base

load_dotenv()

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


# Every request made through TestClient now gets a test-database session instead of a dev one.
app.dependency_overrides[get_db] = override_get_db


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
