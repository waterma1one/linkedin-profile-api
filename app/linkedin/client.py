"""HTTP client for LinkedIn's internal Voyager API."""

import json
from typing import Any, Protocol

import httpx

from app.config import Settings
from app.errors import BotDetected, ProfileNotFound, RateLimited, SessionUnavailable, UpstreamError
from app.linkedin.endpoints import VOYAGER_BASE
from app.linkedin.session import LinkedInSession


class SessionSource(Protocol):
    async def get(self) -> LinkedInSession: ...
    def invalidate(self) -> None: ...


class VoyagerClient:
    """Issues authenticated Voyager requests and maps LinkedIn failures to our errors."""

    def __init__(
        self, settings: Settings, session_provider: SessionSource, http: httpx.AsyncClient
    ) -> None:
        self._settings = settings
        self._sessions = session_provider
        self._http = http

    def _headers(self, session: LinkedInSession, referer_slug: str) -> dict[str, str]:
        track = {
            "clientVersion": self._settings.client_version,
            "mpVersion": self._settings.client_version,
            "osName": "web",
            "timezoneOffset": 0,
            "deviceFormFactor": "DESKTOP",
            "mpName": "voyager-web",
        }
        return {
            "csrf-token": session.csrf_token,
            "x-restli-protocol-version": "2.0.0",
            "accept": "application/vnd.linkedin.normalized+json+2.1",
            "x-li-lang": "en_US",
            "x-li-track": json.dumps(track, separators=(",", ":")),
            "user-agent": self._settings.user_agent,
            "referer": f"https://www.linkedin.com/in/{referer_slug}/",
            # Mandatory outside a browser; LinkedIn returns HTTP 400 without it.
            "host": "www.linkedin.com",
        }

    async def get_json(
        self, path: str, params: dict[str, str], referer_slug: str
    ) -> dict[str, Any]:
        """Fetch and decode a Voyager response, retrying once on a dead session."""
        for attempt in (1, 2):
            session = await self._sessions.get()
            response = await self._http.get(
                f"{VOYAGER_BASE}{path}",
                params=params,
                headers=self._headers(session, referer_slug),
                cookies=session.cookies(),
                follow_redirects=False,
            )

            if self._is_dead_session(response) and attempt == 1:
                self._sessions.invalidate()
                continue

            return self._decode(response)

        raise SessionUnavailable("Session was rejected twice in a row")

    @staticmethod
    def _is_dead_session(response: httpx.Response) -> bool:
        if response.status_code in {401, 403}:
            return True
        location = response.headers.get("location", "")
        return response.is_redirect and ("authwall" in location or "/checkpoint/" in location)

    @staticmethod
    def _decode(response: httpx.Response) -> dict[str, Any]:
        status = response.status_code
        if status == 404:
            raise ProfileNotFound("LinkedIn returned 404 for this profile")
        if status == 429:
            raise RateLimited("LinkedIn rate limited the request")
        if status == 999:
            raise BotDetected("LinkedIn returned 999")
        if status >= 400 or response.is_redirect:
            raise UpstreamError(f"LinkedIn returned HTTP {status}")
        try:
            body = response.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise UpstreamError("Voyager response was not JSON") from exc
        if not isinstance(body, dict):
            raise UpstreamError("Voyager response was not a JSON object")
        return body
