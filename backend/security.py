import os
import secrets
import threading
import time
from collections import defaultdict, deque
from typing import Annotated

from fastapi import Header, HTTPException, Request, status


def require_status_api_key(x_api_key: Annotated[str | None, Header()] = None) -> None:
    """Verifies that the provided API key matches the server secret.

    The key is read on each request (not at import), so a missing STATUS_API_KEY only blocks
    this endpoint instead of stopping the whole app from starting. With no key configured,
    every request is rejected (fail closed).
    """
    expected = os.getenv("STATUS_API_KEY")

    # compare_digest takes the same time whether or not the keys match, so timing can't leak the key.
    if not expected or x_api_key is None or not secrets.compare_digest(x_api_key.encode(), expected.encode()):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API secret key"
        )


class RateLimiter:
    """Allow at most `limit` calls per client within a sliding time window.

    Kept in memory, so it counts per server process and resets when the server restarts.
    That's fine for one small server; several servers would need a shared store like Redis.
    """

    def __init__(self, limit: int, window_seconds: float = 60.0):
        self.limit = limit
        self.window_seconds = window_seconds
        self._calls: dict[str, deque[float]] = defaultdict(deque)  # client -> times of recent calls
        self._lock = threading.Lock()  # requests can run on several threads at once

    def allow(self, client: str) -> bool:
        now = time.monotonic()  # a clock that never jumps backward, unlike wall-clock time
        with self._lock:
            calls = self._calls[client]
            while calls and now - calls[0] >= self.window_seconds:
                calls.popleft()  # forget calls that have left the window
            if len(calls) >= self.limit:
                return False
            calls.append(now)
            return True

    def reset(self) -> None:
        with self._lock:
            self._calls.clear()


project_creation_limiter = RateLimiter(limit=int(os.getenv("RATE_LIMIT_PER_MINUTE", "10")))


def client_address(request: Request) -> str:
    """Who is asking, for the rate limit.

    Railway's edge sets X-Real-IP to the caller's address. X-Forwarded-For can't be used: anyone can send their own,
    and with --forwarded-allow-ips="*" uvicorn takes its first entry as the client (Nov 23 audit: 15 of 15 requests
    with made-up addresses got past a limit of 10). Set CLIENT_IP_HEADER to another header for a different host,
    or to an empty value to use the connection's address.
    """
    header = os.getenv("CLIENT_IP_HEADER", "X-Real-IP")
    if header:
        value = request.headers.get(header, "").strip()
        if value:
            return value
    return request.client.host if request.client else "unknown"


def limit_project_creation(request: Request) -> None:
    """Each new project triggers AI work, so cap how often one client can create them."""
    if not project_creation_limiter.allow(client_address(request)):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many projects created; try again in a minute"
        )
