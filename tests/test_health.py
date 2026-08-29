from fastapi.testclient import TestClient

from app.main import create_app


def test_health_reports_ok_and_session_source():
    client = TestClient(create_app())
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "session" in body


def test_health_never_leaks_secrets():
    client = TestClient(create_app())
    body = client.get("/health").text
    assert "li_at" not in body.lower()
    assert "password" not in body.lower()
