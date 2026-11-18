from fastapi import FastAPI

from app import models  # noqa: F401  (importing the models registers their tables on Base.metadata)
from app.db import Base, engine

Base.metadata.create_all(engine)  # create any missing tables when the app starts

app = FastAPI()