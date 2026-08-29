"""Programmatic LinkedIn login.

Uses the ``/uas/authenticate`` endpoint rather than the web form POST. It returns JSON
with an explicit ``login_result`` code instead of an HTML page that has to be scraped,
and it names the failure mode directly.
"""

import json

import httpx

from app.config import Settings
from app.errors import BadCredentials, CheckpointRequired, UpstreamError
from app.linkedin.session import LinkedInSession, _random_jsessionid

LOGIN_PAGE = "https://www.linkedin.com/uas/login"
AUTHENTICATE = "https://www.linkedin.com/uas/authenticate"


async def login(
    username: str, password: str, settings: Settings, client: httpx.AsyncClient
) -> LinkedInSession:
    """Log in and return a usable session. Raises on challenge or bad credentials."""
    seed_headers = {"user-agent": settings.user_agent, "accept": "text/html"}
    seed = await client.get(LOGIN_PAGE, headers=seed_headers, follow_redirects=True)
    jsessionid = seed.cookies.get("JSESSIONID") or _random_jsessionid()

    response = await client.post(
        AUTHENTICATE,
        data={
            "session_key": username,
            "session_password": password,
            "JSESSIONID": jsessionid,
        },
        headers={
            "user-agent": settings.user_agent,
            "content-type": "application/x-www-form-urlencoded",
            "origin": "https://www.linkedin.com",
            "referer": LOGIN_PAGE,
            "csrf-token": jsessionid.strip('"'),
            "x-li-user-agent": "LIAuthLibrary:0.0.3 com.linkedin.android:4.1.881 Model;",
        },
        cookies={"JSESSIONID": jsessionid},
    )

    try:
        body = response.json()
    except (json.JSONDecodeError, ValueError) as exc:
        raise UpstreamError(
            f"Login response was not JSON (HTTP {response.status_code})"
        ) from exc

    result = body.get("login_result")
    if result == "CHALLENGE":
        raise CheckpointRequired(
            "LinkedIn issued a challenge during login",
            challenge_url=body.get("challenge_url"),
        )
    if result in {"BAD_PASSWORD", "BAD_EMAIL"}:
        raise BadCredentials(f"LinkedIn rejected the credentials: {result}")
    if result != "PASS":
        raise UpstreamError(f"Unexpected login_result: {result!r}")

    li_at = response.cookies.get("li_at")
    if not li_at:
        raise UpstreamError("Login reported PASS but no li_at cookie was returned")

    return LinkedInSession(
        li_at=li_at,
        jsessionid=response.cookies.get("JSESSIONID") or jsessionid,
        source="programmatic_login",
    )
