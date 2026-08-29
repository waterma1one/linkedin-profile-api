import httpx
import pytest
import respx

from app.config import Settings
from app.errors import BadCredentials, CheckpointRequired, UpstreamError
from app.linkedin.login import login


@respx.mock
async def test_successful_login_returns_session():
    respx.get("https://www.linkedin.com/uas/login").mock(
        return_value=httpx.Response(
            200, headers=[("set-cookie", 'JSESSIONID="ajax:9999"; Path=/; Domain=.linkedin.com')]
        )
    )
    respx.post("https://www.linkedin.com/uas/authenticate").mock(
        return_value=httpx.Response(
            200,
            json={"login_result": "PASS"},
            headers=[("set-cookie", "li_at=AQEDtoken; Path=/; Domain=.linkedin.com")],
        )
    )
    async with httpx.AsyncClient() as client:
        session = await login("u", "p", Settings(api_keys=[]), client)
    assert session.li_at == "AQEDtoken"
    assert session.source == "programmatic_login"
    assert session.csrf_token == "ajax:9999"


@respx.mock
async def test_challenge_raises_checkpoint_required():
    respx.get("https://www.linkedin.com/uas/login").mock(return_value=httpx.Response(200))
    respx.post("https://www.linkedin.com/uas/authenticate").mock(
        return_value=httpx.Response(200, json={"login_result": "CHALLENGE"})
    )
    async with httpx.AsyncClient() as client:
        with pytest.raises(CheckpointRequired):
            await login("u", "p", Settings(api_keys=[]), client)


@respx.mock
async def test_bad_password_raises_bad_credentials():
    respx.get("https://www.linkedin.com/uas/login").mock(return_value=httpx.Response(200))
    respx.post("https://www.linkedin.com/uas/authenticate").mock(
        return_value=httpx.Response(200, json={"login_result": "BAD_PASSWORD"})
    )
    async with httpx.AsyncClient() as client:
        with pytest.raises(BadCredentials):
            await login("u", "p", Settings(api_keys=[]), client)


@respx.mock
async def test_pass_without_li_at_cookie_is_an_upstream_error():
    respx.get("https://www.linkedin.com/uas/login").mock(return_value=httpx.Response(200))
    respx.post("https://www.linkedin.com/uas/authenticate").mock(
        return_value=httpx.Response(200, json={"login_result": "PASS"})
    )
    async with httpx.AsyncClient() as client:
        with pytest.raises(UpstreamError):
            await login("u", "p", Settings(api_keys=[]), client)


@respx.mock
async def test_non_json_response_is_an_upstream_error():
    respx.get("https://www.linkedin.com/uas/login").mock(return_value=httpx.Response(200))
    respx.post("https://www.linkedin.com/uas/authenticate").mock(
        return_value=httpx.Response(200, text="<html>blocked</html>")
    )
    async with httpx.AsyncClient() as client:
        with pytest.raises(UpstreamError):
            await login("u", "p", Settings(api_keys=[]), client)
