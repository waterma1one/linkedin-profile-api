import json

import pytest

from app.config import Settings
from app.errors import SessionUnavailable
from app.linkedin.session import LinkedInSession, SessionProvider


def _settings(tmp_path, **overrides) -> Settings:
    base = {"session_path": str(tmp_path / "session.json"), "api_keys": []}
    return Settings(_env_file=None, **{**base, **overrides})


async def _never_called(username: str, password: str) -> LinkedInSession:
    raise AssertionError("login should not have been attempted")


def test_csrf_token_strips_surrounding_quotes():
    session = LinkedInSession(li_at="a", jsessionid='"ajax:123"', source="env")
    assert session.csrf_token == "ajax:123"


async def test_prefers_disk_cache_over_env(tmp_path):
    path = tmp_path / "session.json"
    path.write_text(json.dumps({"li_at": "from_disk", "jsessionid": '"ajax:1"'}))
    provider = SessionProvider(_settings(tmp_path, li_at="from_env"), _never_called)
    assert (await provider.get()).li_at == "from_disk"


async def test_falls_back_to_env_cookies(tmp_path):
    settings = _settings(tmp_path, li_at="from_env", li_jsessionid='"ajax:2"')
    provider = SessionProvider(settings, _never_called)
    session = await provider.get()
    assert session.li_at == "from_env"
    assert session.source == "env_cookie"


async def test_generates_jsessionid_when_env_omits_it(tmp_path):
    provider = SessionProvider(_settings(tmp_path, li_at="from_env"), _never_called)
    assert (await provider.get()).csrf_token.startswith("ajax:")


async def test_falls_back_to_login(tmp_path):
    async def login(username: str, password: str) -> LinkedInSession:
        assert (username, password) == ("u", "p")
        return LinkedInSession(li_at="from_login", jsessionid='"ajax:3"', source="login")

    settings = _settings(tmp_path, li_username="u", li_password="p")
    provider = SessionProvider(settings, login)
    assert (await provider.get()).li_at == "from_login"


async def test_login_result_is_persisted_to_disk(tmp_path):
    async def login(username: str, password: str) -> LinkedInSession:
        return LinkedInSession(li_at="from_login", jsessionid='"ajax:3"', source="login")

    settings = _settings(tmp_path, li_username="u", li_password="p")
    await SessionProvider(settings, login).get()
    stored = json.loads((tmp_path / "session.json").read_text())
    assert stored["li_at"] == "from_login"


async def test_persisted_session_file_is_owner_only(tmp_path):
    async def login(username: str, password: str) -> LinkedInSession:
        return LinkedInSession(li_at="x", jsessionid='"ajax:3"', source="login")

    settings = _settings(tmp_path, li_username="u", li_password="p")
    await SessionProvider(settings, login).get()
    assert (tmp_path / "session.json").stat().st_mode & 0o777 == 0o600


async def test_raises_when_nothing_is_configured(tmp_path):
    provider = SessionProvider(_settings(tmp_path), _never_called)
    with pytest.raises(SessionUnavailable):
        await provider.get()


async def test_invalidate_clears_cache_and_disk(tmp_path):
    path = tmp_path / "session.json"
    path.write_text(json.dumps({"li_at": "from_disk", "jsessionid": '"ajax:1"'}))
    provider = SessionProvider(_settings(tmp_path), _never_called)
    await provider.get()
    provider.invalidate()
    assert not path.exists()
    with pytest.raises(SessionUnavailable):
        await provider.get()


async def test_status_never_includes_credential_values(tmp_path):
    provider = SessionProvider(_settings(tmp_path, li_at="supersecret"), _never_called)
    await provider.get()
    assert "supersecret" not in json.dumps(provider.status())
