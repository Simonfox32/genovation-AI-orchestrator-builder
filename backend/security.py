import os
import secrets
from typing import Annotated

from fastapi import Header, HTTPException, status


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
