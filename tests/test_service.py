import json

import httpx
import pytest
import respx

from app.cache import TTLCache
from app.config import Settings
from app.errors import InvalidProfileURL, ProfileNotFound, UpstreamError
from app.ratelimit import TokenBucket
from app.service import ProfileService

_PERSON = {
    "@type": "Person",
    "name": "Ada Lovelace",
    "description": "Mathematician",
    "address": {"addressLocality": "London", "addressCountry": "GB"},
    "url": "https://www.linkedin.com/in/ada",
}
_HTML = (
    '<html><script type="application/ld+json">'
    f'{json.dumps({"@graph": [_PERSON]})}</script></html>'
)

PROFILE_URL = "https://www.linkedin.com/in/ada"


def _service() -> ProfileService:
    async def no_sleep(_: float) -> None:
        return None

    return ProfileService(
        TTLCache(ttl_seconds=60),
        TokenBucket(rate_seconds=1, burst=10, sleep=no_sleep, clock=lambda: 0.0),
        Settings(_env_file=None, api_keys=[]),
        httpx.AsyncClient(),
    )


@respx.mock
async def test_returns_a_parsed_profile():
    respx.get(PROFILE_URL).mock(return_value=httpx.Response(200, text=_HTML))
    response = await _service().fetch(PROFILE_URL)
    assert response.profile.full_name == "Ada Lovelace"
    assert response.meta.public_identifier == "ada"
    assert response.meta.data_source == "public_jsonld"


@respx.mock
async def test_meta_records_the_request():
    respx.get(PROFILE_URL).mock(return_value=httpx.Response(200, text=_HTML))
    meta = (await _service().fetch(PROFILE_URL)).meta
    assert meta.requested_url == PROFILE_URL
    assert meta.fetched_at is not None
    assert meta.duration_ms is not None
    assert meta.cache_hit is False


@respx.mock
async def test_second_call_is_served_from_cache():
    route = respx.get(PROFILE_URL).mock(return_value=httpx.Response(200, text=_HTML))
    service = _service()
    await service.fetch(PROFILE_URL)
    second = await service.fetch(PROFILE_URL)
    assert route.call_count == 1
    assert second.meta.cache_hit is True
    assert second.profile.full_name == "Ada Lovelace"


@respx.mock
async def test_missing_sections_are_reported_as_unavailable():
    respx.get(PROFILE_URL).mock(return_value=httpx.Response(200, text=_HTML))
    response = await _service().fetch(PROFILE_URL)
    assert response.meta.completeness["skills"] == "unavailable"


@respx.mock
async def test_404_raises_profile_not_found():
    respx.get(PROFILE_URL).mock(return_value=httpx.Response(404))
    with pytest.raises(ProfileNotFound):
        await _service().fetch(PROFILE_URL)


@respx.mock
async def test_authwall_redirect_raises_upstream_error():
    respx.get(PROFILE_URL).mock(
        return_value=httpx.Response(
            302, headers={"location": "https://www.linkedin.com/authwall"}
        )
    )
    respx.get("https://www.linkedin.com/authwall").mock(
        return_value=httpx.Response(200, text="<html>sign in</html>")
    )
    with pytest.raises(UpstreamError):
        await _service().fetch(PROFILE_URL)


@respx.mock
async def test_server_error_raises_upstream_error():
    respx.get(PROFILE_URL).mock(return_value=httpx.Response(503))
    with pytest.raises(UpstreamError):
        await _service().fetch(PROFILE_URL)


async def test_a_non_linkedin_url_is_rejected_before_any_request():
    with pytest.raises(InvalidProfileURL):
        await _service().fetch("https://example.com/in/ada")
