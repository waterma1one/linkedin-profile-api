"""Application factory."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.routes import router
from app.cache import TTLCache
from app.config import get_settings
from app.errors import LinkedInError
from app.ratelimit import InboundLimiter, TokenBucket
from app.service import ProfileService


def create_app() -> FastAPI:
    settings = get_settings()
    http = httpx.AsyncClient(timeout=30)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        await http.aclose()

    app = FastAPI(
        title="LinkedIn Profile API",
        version="0.1.0",
        description="Returns a LinkedIn profile as structured JSON.",
        lifespan=lifespan,
    )

    # Built eagerly rather than in lifespan so the app is usable under a TestClient that
    # is not entered as a context manager.
    app.state.inbound_limiter = InboundLimiter(per_minute=settings.inbound_rate_per_minute)
    app.state.profile_service = ProfileService(
        TTLCache(ttl_seconds=settings.cache_ttl_seconds),
        TokenBucket(rate_seconds=settings.outbound_rate_seconds, burst=3),
        settings,
        http,
    )

    # Every error leaves through one of these three handlers so that the body shape in
    # docs/design.md section 8 holds for all of them. Without the HTTPException handler,
    # FastAPI would nest our body under "detail" and the 401 would not match the contract.

    @app.exception_handler(LinkedInError)
    async def linkedin_error(request: Request, exc: LinkedInError) -> JSONResponse:
        headers = {}
        if exc.code == "rate_limited":
            headers["Retry-After"] = str(getattr(exc, "retry_after", 60))
        return JSONResponse(
            status_code=exc.http_status,
            content={"error": {"code": exc.code, "message": str(exc), "hint": exc.hint}},
            headers=headers,
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        detail = exc.detail
        # deps.py already raises a fully formed body; pass it through unwrapped.
        if isinstance(detail, dict) and "error" in detail:
            content = detail
        else:
            content = {
                "error": {
                    "code": "not_found" if exc.status_code == 404 else "http_error",
                    "message": str(detail),
                    "hint": None,
                }
            }
        return JSONResponse(
            status_code=exc.status_code, content=content, headers=getattr(exc, "headers", None)
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        """A malformed query string is a client error, so report 400 rather than 422."""
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "code": "invalid_request",
                    "message": "Request parameters were invalid",
                    "hint": "GET /api/v1/profile requires a url query parameter",
                }
            },
        )

    app.include_router(router)
    return app


app = create_app()
