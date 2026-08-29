import httpx
import pytest
import respx

from app.config import Settings
from app.errors import BotDetected, ProfileNotFound, RateLimited, UpstreamError
from app.linkedin.client import VoyagerClient
from app.linkedin.session import LinkedInSession


class StubProvider:
    def __init__(self) -> None:
        self.invalidated = 0

    async def get(self) -> LinkedInSession:
        return LinkedInSession(li_at="token", jsessionid='"ajax:42"', source="test")

    def invalidate(self) -> None:
        self.invalidated += 1


def _client(http: httpx.AsyncClient) -> VoyagerClient:
    return VoyagerClient(Settings(api_keys=[]), StubProvider(), http)


@respx.mock
async def test_sends_required_headers():
    route = respx.get("https://www.linkedin.com/voyager/api/identity/me").mock(
        return_value=httpx.Response(200, json={"data": {}})
    )
    async with httpx.AsyncClient() as http:
        await _client(http).get_json("/identity/me", {}, referer_slug="ada")

    headers = route.calls[0].request.headers
    assert headers["csrf-token"] == "ajax:42"
    assert headers["x-restli-protocol-version"] == "2.0.0"
    assert headers["accept"] == "application/vnd.linkedin.normalized+json+2.1"
    assert headers["host"] == "www.linkedin.com"
    assert headers["referer"] == "https://www.linkedin.com/in/ada/"
    assert "x-li-track" in headers


@respx.mock
async def test_sends_session_cookies():
    route = respx.get("https://www.linkedin.com/voyager/api/identity/me").mock(
        return_value=httpx.Response(200, json={"data": {}})
    )
    async with httpx.AsyncClient() as http:
        await _client(http).get_json("/identity/me", {}, referer_slug="ada")
    assert "li_at=token" in route.calls[0].request.headers["cookie"]


@respx.mock
async def test_404_raises_profile_not_found_without_retrying():
    route = respx.get("https://www.linkedin.com/voyager/api/identity/me").mock(
        return_value=httpx.Response(404)
    )
    async with httpx.AsyncClient() as http:
        with pytest.raises(ProfileNotFound):
            await _client(http).get_json("/identity/me", {}, referer_slug="ada")
    assert route.call_count == 1


@respx.mock
async def test_999_raises_bot_detected():
    respx.get("https://www.linkedin.com/voyager/api/identity/me").mock(
        return_value=httpx.Response(999)
    )
    async with httpx.AsyncClient() as http:
        with pytest.raises(BotDetected):
            await _client(http).get_json("/identity/me", {}, referer_slug="ada")


@respx.mock
async def test_429_raises_rate_limited():
    respx.get("https://www.linkedin.com/voyager/api/identity/me").mock(
        return_value=httpx.Response(429)
    )
    async with httpx.AsyncClient() as http:
        with pytest.raises(RateLimited):
            await _client(http).get_json("/identity/me", {}, referer_slug="ada")


@respx.mock
async def test_401_invalidates_session_and_retries_once():
    provider = StubProvider()
    route = respx.get("https://www.linkedin.com/voyager/api/identity/me")
    route.side_effect = [httpx.Response(401), httpx.Response(200, json={"data": {"ok": True}})]
    async with httpx.AsyncClient() as http:
        client = VoyagerClient(Settings(api_keys=[]), provider, http)
        result = await client.get_json("/identity/me", {}, referer_slug="ada")
    assert result["data"]["ok"] is True
    assert provider.invalidated == 1
    assert route.call_count == 2


@respx.mock
async def test_authwall_redirect_is_treated_as_a_dead_session():
    provider = StubProvider()
    route = respx.get("https://www.linkedin.com/voyager/api/identity/me")
    route.side_effect = [
        httpx.Response(302, headers={"location": "https://www.linkedin.com/authwall"}),
        httpx.Response(200, json={"data": {"ok": True}}),
    ]
    async with httpx.AsyncClient() as http:
        client = VoyagerClient(Settings(api_keys=[]), provider, http)
        await client.get_json("/identity/me", {}, referer_slug="ada")
    assert provider.invalidated == 1


@respx.mock
async def test_non_json_body_raises_upstream_error():
    respx.get("https://www.linkedin.com/voyager/api/identity/me").mock(
        return_value=httpx.Response(200, text="<html>nope</html>")
    )
    async with httpx.AsyncClient() as http:
        with pytest.raises(UpstreamError):
            await _client(http).get_json("/identity/me", {}, referer_slug="ada")
