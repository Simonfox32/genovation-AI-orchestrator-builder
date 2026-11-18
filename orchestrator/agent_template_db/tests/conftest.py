"""Test setup: provided by the starter template. The agents can read this file but not change it.

Every test gets a brand-new, empty in-memory database, so tests can never affect each other.
Tests use the `client` fixture:   def test_something(client): ...
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, enable_foreign_keys, get_db
from app.main import app


@pytest.fixture
def client():
    # "sqlite://" = an in-memory database. Each NEW connection to it would be a separate, empty database,
    # so StaticPool makes every session share this ONE connection (the one where we create the tables).
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    enable_foreign_keys(engine)
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine)

    def override_get_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client          # the test runs here
    app.dependency_overrides.clear()
    engine.dispose()               # close the connection: the in-memory database disappears