"""Resolution, persistence, and invalidation of the LinkedIn session."""

import asyncio
import json
import os
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.config import Settings
from app.errors import SessionUnavailable

LoginFn = Callable[[str, str], Awaitable["LinkedInSession"]]


@dataclass
class LinkedInSession:
    """A usable LinkedIn session.

    ``li_at`` is the real credential. ``jsessionid`` is a double-submit CSRF token:
    LinkedIn only checks that the ``csrf-token`` header equals this cookie value.
    """

    li_at: str
    jsessionid: str
    source: str

    @property
    def csrf_token(self) -> str:
        return self.jsessionid.strip('"')

    def cookies(self) -> dict[str, str]:
        return {"li_at": self.li_at, "JSESSIONID": self.jsessionid}

    def to_dict(self) -> dict[str, str]:
        return {"li_at": self.li_at, "jsessionid": self.jsessionid, "source": self.source}


def _random_jsessionid() -> str:
    return f'"ajax:{random.randint(10**18, 10**19 - 1)}"'


class SessionProvider:
    """Resolves a session from disk, then env cookies, then programmatic login."""

    def __init__(self, settings: Settings, login_fn: LoginFn) -> None:
        self._settings = settings
        self._login_fn = login_fn
        self._session: LinkedInSession | None = None
        self._checkpoint: str | None = None
        self._lock = asyncio.Lock()

    async def get(self) -> LinkedInSession:
        async with self._lock:
            if self._session is not None:
                return self._session
            session = self._from_disk() or self._from_env()
            if session is None:
                session = await self._from_login()
                self._persist(session)
            self._session = session
            return session

    def invalidate(self) -> None:
        self._session = None
        path = Path(self._settings.session_path)
        path.unlink(missing_ok=True)

    def status(self) -> dict[str, Any]:
        return {
            "source": self._session.source if self._session else "unresolved",
            "resolved": self._session is not None,
            "checkpoint_blocking": self._checkpoint is not None,
        }

    def _from_disk(self) -> LinkedInSession | None:
        path = Path(self._settings.session_path)
        try:
            raw = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            return None
        if not raw.get("li_at"):
            return None
        return LinkedInSession(
            li_at=raw["li_at"],
            jsessionid=raw.get("jsessionid") or _random_jsessionid(),
            source="disk_cache",
        )

    def _from_env(self) -> LinkedInSession | None:
        if not self._settings.li_at:
            return None
        return LinkedInSession(
            li_at=self._settings.li_at,
            jsessionid=self._settings.li_jsessionid or _random_jsessionid(),
            source="env_cookie",
        )

    async def _from_login(self) -> LinkedInSession:
        username, password = self._settings.li_username, self._settings.li_password
        if not username or not password:
            raise SessionUnavailable(
                "No session available: set LI_AT, or LI_USERNAME and LI_PASSWORD"
            )
        return await self._login_fn(username, password)

    def _persist(self, session: LinkedInSession) -> None:
        path = Path(self._settings.session_path)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(session.to_dict()))
            os.chmod(path, 0o600)
        except OSError:
            # A read-only filesystem must not take the service down; the session
            # still works for the lifetime of this process.
            pass
