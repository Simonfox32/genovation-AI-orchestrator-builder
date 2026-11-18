# The sandbox image: just enough to run a FastAPI app's tests. Nothing else, and no secrets.
FROM python:3.14-slim

# Installed while BUILDING the image (which needs the internet). Running containers get no network.
RUN pip install --no-cache-dir fastapi==0.143.3 pytest==9.1.1 httpx2==2.13.1

# Don't try to write .pyc cache files into the workspace.
ENV PYTHONDONTWRITEBYTECODE=1

# Run as "nobody" by default, starting in the workspace folder.
USER 65534:65534
WORKDIR /workspace