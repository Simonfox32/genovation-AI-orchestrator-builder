"""Database setup: provided by the starter template. The agents can read this file but not change it.

SQLite: the whole database is one file, built into Python, no server needed, so it works in the no-network sandbox.
"""
from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

DATABASE_URL = "sqlite:///./app.db"

# check_same_thread=False: FastAPI may use the session from a different thread than the one that opened it.
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine)


class Base(DeclarativeBase):
    """Every model in app/models.py inherits from this, so Base.metadata knows all the tables."""


def enable_foreign_keys(engine):
    """SQLite ignores foreign keys, and ON DELETE CASCADE, unless each new connection switches them on."""
    @event.listens_for(engine, "connect")
    def _switch_on(dbapi_connection, connection_record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")


enable_foreign_keys(engine)


def get_db():
    """Give one request its own database session, and always close it afterwards."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()