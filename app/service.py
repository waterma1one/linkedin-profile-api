"""Orchestrates a profile fetch: URL parse, cache, throttle, fetch, assemble meta.

Revised 2026-08-29. The public logged-out page is the primary source. It needs no session,
so unlike Voyager it cannot be rate limited out of existence mid-demo.

Voyager enrichment is deliberately not wired in. Fetching profile content over Voyager
needs GraphQL queryIds that could not be recovered, so there is nothing here to switch on.
docs/design.md sections 8d and 8e record what was tried. The Voyager client, session
provider and normalizer stay in the tree as the reverse engineering record and remain under
test, but the serving path does not use them.
"""

import asyncio
import random
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

import httpx

from app.cache import TTLCache
from app.config import Settings
from app.errors import BotDetected, ProfileNotFound, UpstreamError
from app.linkedin.public_profile import parse_public_profile
from app.linkedin.urls import parse_profile_url
from app.models import ProfileResponse
from app.ratelimit import TokenBucket

PUBLIC_PROFILE = "https://www.linkedin.com/in/{slug}"

# LinkedIn answers 999 when it thinks it is talking to a bot. It is intermittent rather
# than a lasting block: the same URL measured 999, then 200 roughly ten seconds later, from
# the same client and IP. Unlike the Voyager path there is no session at stake here, so
# waiting costs nothing but latency, and patience converts most failures into successes.
#
# The waits are deliberately longer than a first guess would suggest. An earlier version
# gave up after 1.5 and 4 seconds and still returned 502s against a block that cleared in
# about ten. Worst case here is roughly 17 seconds plus jitter before reporting failure,
# which is a better trade than a fast error.
BOT_RETRIES = 3
BOT_BACKOFF_SECONDS = (2.0, 5.0, 10.0)


class ProfileService:
    def __init__(
        self,
        cache: TTLCache,
        bucket: TokenBucket,
        settings: Settings,
        http: httpx.AsyncClient,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._cache = cache
        self._bucket = bucket
        self._settings = settings
        self._http = http
        self._sleep = sleep

    async def _get_page(self, slug: str) -> httpx.Response:
        return await self._http.get(
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

    async def _fetch_public(self, slug: str) -> ProfileResponse:
        for attempt in range(BOT_RETRIES + 1):
            page = await self._get_page(slug)
            if page.status_code != 999:
                break
            if attempt == BOT_RETRIES:
                raise BotDetected(
                    "LinkedIn returned HTTP 999 for this profile after retrying"
                )
            base = BOT_BACKOFF_SECONDS[min(attempt, len(BOT_BACKOFF_SECONDS) - 1)]
            # Jitter so concurrent callers do not retry in lockstep.
            await self._sleep(base * (1 + random.random() * 0.3))

        if page.status_code == 404:
            raise ProfileNotFound("No public profile at this identifier")
        if "authwall" in str(page.url):
            raise UpstreamError("LinkedIn served an authwall for this profile")
        if page.status_code >= 400:
            raise UpstreamError(f"Public page unavailable (HTTP {page.status_code})")
        return parse_public_profile(page.text)

    async def fetch(self, url: str) -> ProfileResponse:
        slug = parse_profile_url(url)
        started = time.monotonic()

        cached = self._cache.get(slug)
        if cached is not None:
            response = ProfileResponse.model_validate(cached)
            response.meta.cache_hit = True
            # Report how long this call took, not how long the original fetch took.
            # Reusing the stored duration would tell a caller a cache hit cost seconds.
            response.meta.duration_ms = int((time.monotonic() - started) * 1000)
            return response

        await self._bucket.acquire()
        response = await self._fetch_public(slug)

        response.meta.requested_url = url
        response.meta.public_identifier = slug
        response.meta.fetched_at = datetime.now(UTC)
        response.meta.duration_ms = int((time.monotonic() - started) * 1000)
        response.meta.cache_hit = False

        self._cache.set(slug, response.model_dump(mode="json"))
        return response
