from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.main import create_app


def _client(keys: list[str]) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None, api_keys=keys)
    return TestClient(app)


def test_health_does_not_require_a_key():
    assert _client(["secret"]).get("/health").status_code == 200


def test_profile_rejects_a_missing_key():
    response = _client(["secret"]).get("/api/v1/profile", params={"url": "x"})
    assert response.status_code == 401


def test_profile_rejects_a_wrong_key():
    response = _client(["secret"]).get(
        "/api/v1/profile", params={"url": "x"}, headers={"X-API-Key": "wrong"}
    )
    assert response.status_code == 401
