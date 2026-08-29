"""Orchestrates a profile fetch: URL parse, cache, throttle, fetch, assemble meta.

Revised 2026-08-29. The public logged-out page is the primary source. It needs no session,
so unlike Voyager it cannot be rate limited out of existence mid-demo.

Voyager enrichment is deliberately not wired in here yet. It needs the section parsers
from Tasks 9 to 11, which need a captured fixture, which needs a live session. See
docs/design.md section 8d for why a session cannot be depended on in production. The
switch that will gate it already exists as settings.voyager_enabled.
"""

import time
from datetime import UTC, datetime

import httpx

from app.cache import TTLCache
from app.config import Settings
from app.errors import ProfileNotFound, UpstreamError
from app.linkedin.public_profile import parse_public_profile
from app.linkedin.urls import parse_profile_url
from app.models import ProfileResponse
from app.ratelimit import TokenBucket

PUBLIC_PROFILE = "https://www.linkedin.com/in/{slug}"


class ProfileService:
    def __init__(
        self,
        cache: TTLCache,
        bucket: TokenBucket,
        settings: Settings,
        http: httpx.AsyncClient,
    ) -> None:
        self._cache = cache
        self._bucket = bucket
        self._settings = settings
        self._http = http

    async def _fetch_public(self, slug: str) -> ProfileResponse:
        page = await self._http.get(
            PUBLIC_PROFILE.format(slug=slug),
            # A real desktop UA. The probe in design.md 8c returned HTTP 200 with this,
            # and the page is served differently to an obvious bot string.
            headers={
                "user-agent": self._settings.user_agent,
                "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "accept-language": "en-US,en;q=0.9",
            },
            follow_redirects=True,
        )
        if page.status_code == 404:
            raise ProfileNotFound("No public profile at this identifier")
        if "authwall" in str(page.url):
            raise UpstreamError("LinkedIn served an authwall for this profile")
        if page.status_code >= 400:
            raise UpstreamError(f"Public page unavailable (HTTP {page.status_code})")
        return parse_public_profile(page.text)

    async def fetch(self, url: str) -> ProfileResponse:
        slug = parse_profile_url(url)

        cached = self._cache.get(slug)
        if cached is not None:
            response = ProfileResponse.model_validate(cached)
            response.meta.cache_hit = True
            return response

        started = time.monotonic()
        await self._bucket.acquire()
        response = await self._fetch_public(slug)

        response.meta.requested_url = url
        response.meta.public_identifier = slug
        response.meta.fetched_at = datetime.now(UTC)
        response.meta.duration_ms = int((time.monotonic() - started) * 1000)
        response.meta.cache_hit = False

        self._cache.set(slug, response.model_dump(mode="json"))
        return response
