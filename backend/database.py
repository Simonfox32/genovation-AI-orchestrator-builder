import os

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

load_dotenv()  # Reads backend/.env into environment variables (no-op if the file doesn't exist)

DATABASE_URL = os.environ["DATABASE_URL"]  # Crash at startup if missing, rather than on the first request

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)


def get_db():
    """Give one request its own database session, and always close it afterward."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
