import json

import httpx
import respx
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.main import create_app

_PERSON = {
    "@type": "Person",
    "name": "Ada Lovelace",
    "description": "Mathematician",
    "url": "https://www.linkedin.com/in/ada",
}
_HTML = (
    '<html><script type="application/ld+json">'
    f'{json.dumps({"@graph": [_PERSON]})}</script></html>'
)

PROFILE_URL = "https://www.linkedin.com/in/ada"


def _client() -> TestClient:
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None, api_keys=[])
    return TestClient(app)


@respx.mock
def test_returns_a_profile():
    respx.get(PROFILE_URL).mock(return_value=httpx.Response(200, text=_HTML))
    response = _client().get("/api/v1/profile", params={"url": PROFILE_URL})
    assert response.status_code == 200
    body = response.json()
    assert body["profile"]["full_name"] == "Ada Lovelace"
    assert body["meta"]["data_source"] == "public_jsonld"
    assert body["skills"] == []


@respx.mock
def test_a_bad_url_is_a_400_with_the_uniform_error_body():
    response = _client().get("/api/v1/profile", params={"url": "https://example.com/in/x"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_url"


@respx.mock
def test_a_missing_profile_is_a_404():
    respx.get(PROFILE_URL).mock(return_value=httpx.Response(404))
    response = _client().get("/api/v1/profile", params={"url": PROFILE_URL})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "profile_not_found"


@respx.mock
def test_an_upstream_failure_is_a_502():
    respx.get(PROFILE_URL).mock(return_value=httpx.Response(503))
    response = _client().get("/api/v1/profile", params={"url": PROFILE_URL})
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "upstream_error"


def test_a_missing_url_parameter_is_a_400_not_a_422():
    response = _client().get("/api/v1/profile")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_request"


def test_every_error_body_has_the_same_shape():
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: Settings(
        _env_file=None, api_keys=["secret"]
    )
    # A 401 travels through the HTTPException handler rather than the LinkedInError one,
    # so it is the case most likely to drift from the contract.
    body = TestClient(app).get("/api/v1/profile", params={"url": "x"}).json()
    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message", "hint"}


def test_health_reports_the_active_data_source():
    body = _client().get("/health").json()
    assert body["status"] == "ok"
    assert body["data_source"] == "public_jsonld"
