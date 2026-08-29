"""Shared FastAPI dependencies."""

from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request

from app.config import Settings, get_settings


async def require_api_key(
    x_api_key: Annotated[str | None, Header()] = None,
    settings: Annotated[Settings, Depends(get_settings)] = None,  # type: ignore[assignment]
) -> None:
    """Reject requests without a configured key.

    When no keys are configured the API is open, which is correct for local development
    and must be avoided in production by always setting API_KEYS.
    """
    if not settings.api_keys:
        return
    if x_api_key not in settings.api_keys:
        raise HTTPException(
            status_code=401,
            detail={
                "error": {
                    "code": "unauthorized",
                    "message": "Missing or invalid API key",
                    "hint": "Send the key in the X-API-Key header",
                }
            },
        )


async def enforce_inbound_limit(
    request: Request,
    x_api_key: Annotated[str | None, Header()] = None,
) -> None:
    """Cap how fast one caller may call us, keyed by API key or client address."""
    limiter = getattr(request.app.state, "inbound_limiter", None)
    if limiter is None:
        return
    key = x_api_key or (request.client.host if request.client else "anonymous")
    retry_after = limiter.check(key)
    if retry_after is not None:
        raise HTTPException(
            status_code=429,
            detail={
                "error": {
                    "code": "rate_limited",
                    "message": "Too many requests",
                    "hint": f"Retry after {retry_after} seconds",
                }
            },
            headers={"Retry-After": str(retry_after)},
        )
