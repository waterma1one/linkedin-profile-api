# LinkedIn Profile API Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and deploy an HTTPS API that accepts a LinkedIn profile URL and returns the profile as structured JSON, fetched by direct HTTP calls to LinkedIn's internal Voyager API with no browser involved.

**Architecture:** A FastAPI service wraps a `VoyagerClient` that authenticates with LinkedIn session cookies. Responses arrive as a flat, normalized object graph which a dedicated normalizer resolves into a nested tree; six pure parsers then map that tree into Pydantic models. A four-tier fetch strategy degrades gracefully rather than erroring when LinkedIn withholds data.

**Tech Stack:** Python 3.12, FastAPI, httpx, Pydantic v2, pydantic-settings, tenacity, respx (tests), pytest, ruff, mypy, Docker, Railway.

**Spec:** `docs/superpowers/specs/2026-08-28-linkedin-profile-api-design.md`

## Global Constraints

Every task's requirements implicitly include this section.

- Python 3.12. Target `python:3.12-slim` in the container.
- **No browser automation.** Playwright, Puppeteer, Selenium, and headless Chrome are prohibited by the challenge brief. Do not add them as dependencies for any reason, including tests.
- **No secrets in the repository.** All credentials come from environment variables. `.env` is git-ignored; `.env.example` holds variable names with empty values.
- **No AI attribution in any commit message, PR body, or repository file.** No `Co-Authored-By` trailers, no "generated with" footers. A `commit-msg` hook in `.git/hooks/` enforces this locally.
- All configuration is read through `app/config.py`. No module reads `os.environ` directly.
- Parsers perform no I/O. They accept a dictionary and return models.
- Every response field is nullable. An absent field is never an error.
- Outbound Voyager calls default to 1 request per 30 seconds sustained, burst 3.
- Tests never make real network calls. `respx` blocks outbound HTTP in CI.
- Commit after every task using Conventional Commits (`feat:`, `fix:`, `test:`, `chore:`, `docs:`).

## File Structure

| File | Responsibility |
| --- | --- |
| `app/config.py` | All settings, loaded from env via pydantic-settings |
| `app/errors.py` | Exception hierarchy shared by every layer |
| `app/main.py` | FastAPI app construction and wiring |
| `app/api/routes.py` | HTTP routes: `/api/v1/profile`, `/health` |
| `app/api/deps.py` | API-key guard, inbound rate limiting |
| `app/linkedin/urls.py` | LinkedIn URL parsing to `public_identifier` |
| `app/linkedin/normalizer.py` | Flat `included[]` graph to nested tree, truncation detection |
| `app/linkedin/session.py` | `SessionProvider`: resolve, validate, invalidate, persist |
| `app/linkedin/login.py` | Programmatic login via `/uas/authenticate` |
| `app/linkedin/client.py` | `VoyagerClient`: headers, retries, LinkedIn error mapping |
| `app/linkedin/endpoints.py` | Endpoint URL and `decorationId` builders |
| `app/linkedin/parsers/*.py` | One pure parser per response section |
| `app/linkedin/public_fallback.py` | Logged-out HTML plus JSON-LD parsing |
| `app/models.py` | Pydantic response schema |
| `app/cache.py` | TTL cache keyed on `public_identifier` |
| `app/ratelimit.py` | Outbound token bucket |
| `app/service.py` | Tier orchestration, completeness and warning assembly |
| `scripts/capture_fixtures.py` | Live capture plus scrubbing of test fixtures |

---

### Task 1: Project scaffold, configuration, and health endpoint

**Files:**
- Create: `pyproject.toml`, `app/__init__.py`, `app/config.py`, `app/errors.py`, `app/main.py`, `app/api/__init__.py`, `app/api/routes.py`, `.env.example`
- Test: `tests/__init__.py`, `tests/test_health.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `app.config.Settings` with fields `api_keys: list[str]`, `li_at: str | None`, `li_jsessionid: str | None`, `li_username: str | None`, `li_password: str | None`, `session_path: str`, `cache_ttl_seconds: int`, `outbound_rate_seconds: float`, `client_version: str`, `user_agent: str`
  - `app.config.get_settings() -> Settings` (cached)
  - `app.errors.LinkedInError` and subclasses `SessionUnavailable`, `CheckpointRequired`, `BadCredentials`, `ProfileNotFound`, `RateLimited`, `BotDetected`, `UpstreamError`, `InvalidProfileURL`
  - `app.main.create_app() -> FastAPI`

- [ ] **Step 1: Create `pyproject.toml`**

```toml
[project]
name = "linkedin-profile-api"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.32",
    "httpx>=0.27",
    "pydantic>=2.9",
    "pydantic-settings>=2.5",
    "tenacity>=9.0",
    "selectolax>=0.3.21",
]

[project.optional-dependencies]
dev = ["pytest>=8.3", "pytest-asyncio>=0.24", "respx>=0.21", "ruff>=0.7", "mypy>=1.13"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "SIM"]

[tool.mypy]
python_version = "3.12"
strict = true
ignore_missing_imports = true
```

- [ ] **Step 2: Write the failing test**

Create `tests/test_health.py`:

```python
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
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_health.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app'`

- [ ] **Step 4: Create `app/errors.py`**

```python
"""Exception hierarchy shared across the LinkedIn client and API layers."""


class LinkedInError(Exception):
    """Base class for every error raised by this service."""

    code = "internal_error"
    http_status = 500
    hint: str | None = None


class InvalidProfileURL(LinkedInError):
    code = "invalid_url"
    http_status = 400
    hint = "Provide a URL of the form https://www.linkedin.com/in/<slug>"


class SessionUnavailable(LinkedInError):
    code = "session_unavailable"
    http_status = 503
    hint = "No usable LinkedIn session. Check /health for the active auth path."


class CheckpointRequired(SessionUnavailable):
    code = "checkpoint_required"
    hint = "LinkedIn issued a CAPTCHA challenge. Log in manually and supply a fresh LI_AT."

    def __init__(self, message: str, challenge_url: str | None = None) -> None:
        super().__init__(message)
        self.challenge_url = challenge_url


class BadCredentials(SessionUnavailable):
    code = "bad_credentials"
    hint = "LI_USERNAME or LI_PASSWORD was rejected by LinkedIn."


class ProfileNotFound(LinkedInError):
    code = "profile_not_found"
    http_status = 404
    hint = "The profile does not exist or is not visible to the backing account."


class RateLimited(LinkedInError):
    code = "rate_limited"
    http_status = 429
    hint = "Slow down and retry after the interval in the Retry-After header."


class BotDetected(LinkedInError):
    code = "bot_detected"
    http_status = 502
    hint = "LinkedIn returned HTTP 999. The backing account may be flagged."


class UpstreamError(LinkedInError):
    code = "upstream_error"
    http_status = 502
    hint = "LinkedIn was unreachable or returned an unexpected response."
```

- [ ] **Step 5: Create `app/config.py`**

```python
"""All configuration for the service. No other module reads os.environ."""

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    api_keys: list[str] = Field(default_factory=list)

    li_at: str | None = None
    li_jsessionid: str | None = None
    li_username: str | None = None
    li_password: str | None = None

    session_path: str = "/data/session.json"
    cache_ttl_seconds: int = 21600
    outbound_rate_seconds: float = 30.0
    inbound_rate_per_minute: int = 20

    # Observed from a live LinkedIn web session during fixture capture, then pinned.
    # An invented or stale value is a known bot signal.
    client_version: str = "1.13.36760"
    user_agent: str = DEFAULT_USER_AGENT

    @field_validator("api_keys", mode="before")
    @classmethod
    def _split_keys(cls, value: object) -> object:
        if isinstance(value, str):
            return [key.strip() for key in value.split(",") if key.strip()]
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

- [ ] **Step 6: Create `app/api/routes.py`**

`app/__init__.py` and `app/api/__init__.py` are empty files.

```python
"""HTTP routes."""

from fastapi import APIRouter

from app.config import Settings, get_settings

router = APIRouter()


def _session_status(settings: Settings) -> dict[str, object]:
    """Describe the auth path without ever revealing a credential value."""
    if settings.li_at:
        source = "env_cookie"
    elif settings.li_username and settings.li_password:
        source = "programmatic_login"
    else:
        source = "unconfigured"
    return {"source": source, "checkpoint_blocking": False}


@router.get("/health")
async def health() -> dict[str, object]:
    return {"status": "ok", "session": _session_status(get_settings())}
```

- [ ] **Step 7: Create `app/main.py`**

```python
"""Application factory."""

from fastapi import FastAPI

from app.api.routes import router


def create_app() -> FastAPI:
    app = FastAPI(
        title="LinkedIn Profile API",
        version="0.1.0",
        description="Returns a LinkedIn profile as structured JSON.",
    )
    app.include_router(router)
    return app


app = create_app()
```

- [ ] **Step 8: Create `.env.example`**

```bash
# Comma-separated keys accepted on the X-API-Key header
API_KEYS=

# Primary auth path: cookies copied from a logged-in session
LI_AT=
LI_JSESSIONID=

# Fallback auth path: programmatic login
LI_USERNAME=
LI_PASSWORD=

SESSION_PATH=/data/session.json
CACHE_TTL_SECONDS=21600
OUTBOUND_RATE_SECONDS=30
INBOUND_RATE_PER_MINUTE=20
```

- [ ] **Step 9: Run tests to verify they pass**

Run: `pytest tests/test_health.py -v`
Expected: PASS, 2 passed

- [ ] **Step 10: Commit**

```bash
git add pyproject.toml app tests .env.example
git commit -m "feat: scaffold FastAPI service with config, errors, and health endpoint"
```

---

### Task 2: LinkedIn URL parsing

**Files:**
- Create: `app/linkedin/__init__.py`, `app/linkedin/urls.py`
- Test: `tests/test_url_parser.py`

**Interfaces:**
- Consumes: `app.errors.InvalidProfileURL`
- Produces: `app.linkedin.urls.parse_profile_url(raw: str) -> str` returning the `public_identifier` slug

- [ ] **Step 1: Write the failing test**

Create `tests/test_url_parser.py`:

```python
import pytest

from app.errors import InvalidProfileURL
from app.linkedin.urls import parse_profile_url


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://www.linkedin.com/in/aditya-singh", "aditya-singh"),
        ("https://www.linkedin.com/in/aditya-singh/", "aditya-singh"),
        ("http://linkedin.com/in/aditya-singh", "aditya-singh"),
        ("www.linkedin.com/in/aditya-singh", "aditya-singh"),
        ("linkedin.com/in/aditya-singh", "aditya-singh"),
        ("https://in.linkedin.com/in/aditya-singh", "aditya-singh"),
        ("https://www.linkedin.com/in/aditya-singh?originalSubdomain=in", "aditya-singh"),
        ("https://www.linkedin.com/in/aditya-singh/en", "aditya-singh"),
        ("https://www.linkedin.com/mwlite/in/aditya-singh", "aditya-singh"),
        ("https://www.linkedin.com/pub/aditya-singh/1/2/3", "aditya-singh"),
        ("  https://www.linkedin.com/in/aditya-singh  ", "aditya-singh"),
        ("https://www.linkedin.com/in/%E5%B1%B1%E7%94%B0", "山田"),
    ],
)
def test_accepts_known_url_forms(raw: str, expected: str) -> None:
    assert parse_profile_url(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "not a url",
        "https://example.com/in/aditya-singh",
        "https://www.linkedin.com/company/tross",
        "https://www.linkedin.com/in/",
        "https://linkedin.com.evil.example/in/aditya-singh",
    ],
)
def test_rejects_invalid_urls(raw: str) -> None:
    with pytest.raises(InvalidProfileURL):
        parse_profile_url(raw)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_url_parser.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.linkedin'`

- [ ] **Step 3: Create `app/linkedin/urls.py`**

`app/linkedin/__init__.py` is an empty file.

```python
"""Parsing of LinkedIn profile URLs into a public identifier slug."""

import re
from urllib.parse import unquote, urlsplit

from app.errors import InvalidProfileURL

# Accepts linkedin.com and any regional subdomain such as in.linkedin.com.
_ALLOWED_HOST = re.compile(r"^([a-z0-9-]+\.)*linkedin\.com$")

# /in/<slug> and /mwlite/in/<slug> for modern URLs, /pub/<slug>/... for legacy ones.
_PATH = re.compile(r"^/(?:mwlite/)?(?:in|pub)/(?P<slug>[^/]+)")

_SLUG = re.compile(r"^[\w\-.%]+$", re.UNICODE)


def parse_profile_url(raw: str) -> str:
    """Return the public identifier for a LinkedIn profile URL.

    Raises InvalidProfileURL if the input is not a LinkedIn profile URL.
    """
    candidate = (raw or "").strip()
    if not candidate:
        raise InvalidProfileURL("URL is empty")

    # urlsplit needs a scheme to populate netloc, so supply one when absent.
    if "://" not in candidate:
        candidate = f"https://{candidate}"

    parts = urlsplit(candidate)
    host = parts.netloc.split("@")[-1].split(":")[0].lower()
    if not _ALLOWED_HOST.match(host):
        raise InvalidProfileURL(f"Host {host!r} is not a LinkedIn domain")

    match = _PATH.match(parts.path)
    if not match:
        raise InvalidProfileURL("URL path is not a profile path")

    slug = unquote(match.group("slug")).strip()
    if not slug or not _SLUG.match(slug):
        raise InvalidProfileURL("Profile slug is missing or malformed")
    return slug
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_url_parser.py -v`
Expected: PASS, 18 passed

- [ ] **Step 5: Commit**

```bash
git add app/linkedin tests/test_url_parser.py
git commit -m "feat: parse LinkedIn profile URLs into a public identifier"
```

---

### Task 3: Normalizer for the flat URN graph

This is the core of the project. LinkedIn returns `{"data": {...}, "included": [...]}` where entries in `included` are identified by `entityUrn`, and references appear as star-prefixed keys: `*field` holds a single URN string, `**field` holds a list of URN strings.

**Files:**
- Create: `app/linkedin/normalizer.py`
- Test: `tests/test_normalizer.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `app.linkedin.normalizer.Truncation` dataclass with fields `section: str`, `returned: int`, `total: int`
  - `app.linkedin.normalizer.NormalizedResult` dataclass with fields `data: dict[str, object]`, `truncations: list[Truncation]`
  - `app.linkedin.normalizer.normalize(payload: dict, max_depth: int = 12) -> NormalizedResult`

- [ ] **Step 1: Write the failing test**

Create `tests/test_normalizer.py`:

```python
from app.linkedin.normalizer import normalize


def test_resolves_single_reference():
    payload = {
        "data": {"*profile": "urn:li:fsd_profile:1"},
        "included": [{"entityUrn": "urn:li:fsd_profile:1", "firstName": "Ada"}],
    }
    result = normalize(payload)
    assert result.data["profile"]["firstName"] == "Ada"
    assert "*profile" not in result.data


def test_resolves_collection_reference():
    payload = {
        "data": {"**positions": ["urn:li:pos:1", "urn:li:pos:2"]},
        "included": [
            {"entityUrn": "urn:li:pos:1", "title": "Engineer"},
            {"entityUrn": "urn:li:pos:2", "title": "Manager"},
        ],
    }
    result = normalize(payload)
    assert [item["title"] for item in result.data["positions"]] == ["Engineer", "Manager"]


def test_unresolvable_urn_becomes_none_not_an_error():
    payload = {"data": {"*profile": "urn:li:fsd_profile:missing"}, "included": []}
    result = normalize(payload)
    assert result.data["profile"] is None


def test_unresolvable_urns_are_dropped_from_collections():
    payload = {
        "data": {"**positions": ["urn:li:pos:1", "urn:li:pos:missing"]},
        "included": [{"entityUrn": "urn:li:pos:1", "title": "Engineer"}],
    }
    result = normalize(payload)
    assert len(result.data["positions"]) == 1


def test_cycles_do_not_recurse_forever():
    payload = {
        "data": {"*position": "urn:li:pos:1"},
        "included": [
            {"entityUrn": "urn:li:pos:1", "title": "Engineer", "*company": "urn:li:co:1"},
            {"entityUrn": "urn:li:co:1", "name": "Tross", "**positions": ["urn:li:pos:1"]},
        ],
    }
    result = normalize(payload)
    position = result.data["position"]
    assert position["company"]["name"] == "Tross"
    # The back-reference is cut rather than expanded again.
    assert position["company"]["positions"][0] == "urn:li:pos:1"


def test_records_truncation_from_paging_metadata():
    payload = {
        "data": {
            "**elements": ["urn:li:skill:1"],
            "paging": {"start": 0, "count": 1, "total": 47},
            "entityUrn": "urn:li:fsd_profileSkill:section",
        },
        "included": [{"entityUrn": "urn:li:skill:1", "name": "Python"}],
    }
    result = normalize(payload)
    assert result.truncations
    assert result.truncations[0].returned == 1
    assert result.truncations[0].total == 47


def test_no_truncation_when_all_items_returned():
    payload = {
        "data": {
            "**elements": ["urn:li:skill:1"],
            "paging": {"start": 0, "count": 1, "total": 1},
        },
        "included": [{"entityUrn": "urn:li:skill:1", "name": "Python"}],
    }
    assert normalize(payload).truncations == []


def test_depth_cap_stops_runaway_nesting():
    included = [
        {"entityUrn": f"urn:li:n:{i}", "*next": f"urn:li:n:{i + 1}"} for i in range(30)
    ]
    payload = {"data": {"*next": "urn:li:n:0"}, "included": included}
    result = normalize(payload, max_depth=3)
    # Should complete without RecursionError.
    assert result.data["next"] is not None


def test_plain_payload_without_included_is_passed_through():
    payload = {"data": {"firstName": "Ada"}}
    assert normalize(payload).data == {"firstName": "Ada"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_normalizer.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.linkedin.normalizer'`

- [ ] **Step 3: Create `app/linkedin/normalizer.py`**

```python
"""Resolve LinkedIn's normalized response format into a nested tree.

LinkedIn returns ``{"data": ..., "included": [...]}`` when asked for
``application/vnd.linkedin.normalized+json+2.1``. Objects in ``included`` are keyed by
``entityUrn`` and reference each other through star-prefixed keys:

    "*company":   "urn:li:fsd_company:1"        single reference
    "**elements": ["urn:li:pos:1", "urn:li:pos:2"]  collection reference

The graph is genuinely cyclic, so resolution tracks the URNs on the current branch and
leaves a raw URN string in place rather than expanding a reference twice.
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Truncation:
    """A collection LinkedIn returned only partially."""

    section: str
    returned: int
    total: int


@dataclass
class NormalizedResult:
    data: dict[str, Any]
    truncations: list[Truncation] = field(default_factory=list)


def _index(included: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        item["entityUrn"]: item
        for item in included
        if isinstance(item, dict) and isinstance(item.get("entityUrn"), str)
    }


def normalize(payload: dict[str, Any], max_depth: int = 12) -> NormalizedResult:
    """Return the payload with every URN reference resolved in place."""
    index = _index(payload.get("included") or [])
    truncations: list[Truncation] = []

    def resolve(node: Any, branch: frozenset[str], depth: int) -> Any:
        if depth > max_depth:
            return node
        if isinstance(node, list):
            return [resolve(item, branch, depth + 1) for item in node]
        if not isinstance(node, dict):
            return node

        _record_truncation(node, truncations)
        output: dict[str, Any] = {}

        for key, value in node.items():
            if key.startswith("**"):
                output[key[2:]] = _resolve_many(value, index, branch, depth, resolve)
            elif key.startswith("*"):
                output[key[1:]] = _resolve_one(value, index, branch, depth, resolve)
            else:
                output[key] = resolve(value, branch, depth + 1)
        return output

    def _resolve_one(
        urn: Any, idx: dict[str, dict[str, Any]], branch: frozenset[str], depth: int, rec: Any
    ) -> Any:
        if not isinstance(urn, str):
            return rec(urn, branch, depth + 1)
        if urn in branch:
            return urn  # cycle: leave the raw URN rather than expanding again
        target = idx.get(urn)
        if target is None:
            return None
        return rec(target, branch | {urn}, depth + 1)

    def _resolve_many(
        urns: Any, idx: dict[str, dict[str, Any]], branch: frozenset[str], depth: int, rec: Any
    ) -> Any:
        if not isinstance(urns, list):
            return rec(urns, branch, depth + 1)
        resolved: list[Any] = []
        for urn in urns:
            if not isinstance(urn, str):
                resolved.append(rec(urn, branch, depth + 1))
            elif urn in branch:
                resolved.append(urn)
            elif urn in idx:
                resolved.append(rec(idx[urn], branch | {urn}, depth + 1))
            # Unresolvable URNs are dropped rather than yielding a None hole.
        return resolved

    data = payload.get("data", payload)
    return NormalizedResult(data=resolve(data, frozenset(), 0), truncations=truncations)


def _record_truncation(node: dict[str, Any], sink: list[Truncation]) -> None:
    paging = node.get("paging")
    if not isinstance(paging, dict):
        return
    total = paging.get("total")
    count = paging.get("count")
    if not isinstance(total, int) or not isinstance(count, int):
        return
    if total > count:
        section = str(node.get("entityUrn") or node.get("$type") or "unknown")
        sink.append(Truncation(section=section, returned=count, total=total))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_normalizer.py -v`
Expected: PASS, 9 passed

- [ ] **Step 5: Commit**

```bash
git add app/linkedin/normalizer.py tests/test_normalizer.py
git commit -m "feat: resolve LinkedIn normalized URN graph into a nested tree"
```

---

### Task 4: Session provider

**Files:**
- Create: `app/linkedin/session.py`
- Test: `tests/test_session.py`

**Interfaces:**
- Consumes: `app.config.Settings`, `app.errors.SessionUnavailable`
- Produces:
  - `app.linkedin.session.LinkedInSession` dataclass with fields `li_at: str`, `jsessionid: str`, `source: str`, and property `csrf_token: str` (the `jsessionid` with surrounding double quotes stripped)
  - `app.linkedin.session.SessionProvider(settings, login_fn)` with `async get() -> LinkedInSession`, `invalidate() -> None`, `status() -> dict[str, object]`
  - `login_fn` has signature `async (username: str, password: str) -> LinkedInSession`

- [ ] **Step 1: Write the failing test**

Create `tests/test_session.py`:

```python
import json

import pytest

from app.config import Settings
from app.errors import SessionUnavailable
from app.linkedin.session import LinkedInSession, SessionProvider


def _settings(tmp_path, **overrides) -> Settings:
    base = {"session_path": str(tmp_path / "session.json"), "api_keys": []}
    return Settings(**{**base, **overrides})


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_session.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.linkedin.session'`

- [ ] **Step 3: Create `app/linkedin/session.py`**

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_session.py -v`
Expected: PASS, 10 passed

- [ ] **Step 5: Commit**

```bash
git add app/linkedin/session.py tests/test_session.py
git commit -m "feat: resolve LinkedIn session from disk, env, or programmatic login"
```

---

### Task 5: Programmatic login via `/uas/authenticate`

**Files:**
- Create: `app/linkedin/login.py`
- Test: `tests/test_login.py`

**Interfaces:**
- Consumes: `app.linkedin.session.LinkedInSession`, `app.errors.CheckpointRequired`, `app.errors.BadCredentials`, `app.errors.UpstreamError`, `app.config.Settings`
- Produces: `app.linkedin.login.login(username: str, password: str, settings: Settings, client: httpx.AsyncClient) -> LinkedInSession`

- [ ] **Step 1: Write the failing test**

Create `tests/test_login.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_login.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.linkedin.login'`

- [ ] **Step 3: Create `app/linkedin/login.py`**

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_login.py -v`
Expected: PASS, 5 passed

- [ ] **Step 5: Commit**

```bash
git add app/linkedin/login.py tests/test_login.py
git commit -m "feat: add programmatic login via the uas/authenticate endpoint"
```

---

### Task 6: Voyager client with header construction and error mapping

**Files:**
- Create: `app/linkedin/endpoints.py`, `app/linkedin/client.py`
- Test: `tests/test_client.py`

**Interfaces:**
- Consumes: `LinkedInSession`, `SessionProvider`, `app.errors.*`
- Produces:
  - `app.linkedin.endpoints.VOYAGER_BASE = "https://www.linkedin.com/voyager/api"`
  - `app.linkedin.endpoints.FULL_PROFILE_DECORATION` constant
  - `app.linkedin.endpoints.dash_profile(slug: str) -> tuple[str, dict[str, str]]` returning path and query params
  - `app.linkedin.endpoints.legacy_profile_view(slug: str) -> tuple[str, dict[str, str]]`
  - `app.linkedin.client.VoyagerClient(settings, session_provider, http)` with `async get_json(path: str, params: dict[str, str], referer_slug: str) -> dict`

- [ ] **Step 1: Write the failing test**

Create `tests/test_client.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_client.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.linkedin.client'`

- [ ] **Step 3: Create `app/linkedin/endpoints.py`**

```python
"""Voyager endpoint paths and decoration identifiers."""

VOYAGER_BASE = "https://www.linkedin.com/voyager/api"

FULL_PROFILE_DECORATION = (
    "com.linkedin.voyager.dash.deco.identity.profile.FullProfileWithEntities-93"
)


def dash_profile(slug: str) -> tuple[str, dict[str, str]]:
    """Tier 1: the modern dash endpoint returning most of the profile in one call."""
    return "/identity/dash/profiles", {
        "q": "memberIdentity",
        "memberIdentity": slug,
        "decorationId": FULL_PROFILE_DECORATION,
    }


def legacy_profile_view(slug: str) -> tuple[str, dict[str, str]]:
    """Tier 3: the older endpoint, a differently shaped secondary source."""
    return f"/identity/profileView/{slug}", {}
```

- [ ] **Step 4: Create `app/linkedin/client.py`**

```python
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
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_client.py -v`
Expected: PASS, 8 passed

- [ ] **Step 6: Commit**

```bash
git add app/linkedin/endpoints.py app/linkedin/client.py tests/test_client.py
git commit -m "feat: add Voyager client with header construction and error mapping"
```

---

### Task 7: Fixture capture script

This task requires a live LinkedIn session and cannot be completed offline. Run it before starting Task 9.

**Files:**
- Create: `scripts/capture_fixtures.py`
- Create: `tests/fixtures/.gitkeep`

**Interfaces:**
- Consumes: `VoyagerClient`, `SessionProvider`, `login`, `dash_profile`
- Produces: scrubbed fixture files at `tests/fixtures/<name>.json`; raw files at `tests/fixtures/raw/<name>.json` (git-ignored)

- [ ] **Step 1: Create `scripts/capture_fixtures.py`**

```python
"""Capture real Voyager payloads and write scrubbed copies for use as test fixtures.

Usage:
    python -m scripts.capture_fixtures sparse=<slug> dense=<slug> intl=<slug>

Raw payloads land in tests/fixtures/raw/ (git-ignored). Scrubbed payloads land in
tests/fixtures/ and are safe to commit: member URNs, tracking identifiers, signed image
URLs, and contact details are replaced with stable synthetic values.
"""

import asyncio
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

import httpx

from app.config import get_settings
from app.linkedin.client import VoyagerClient
from app.linkedin.endpoints import dash_profile
from app.linkedin.login import login
from app.linkedin.session import SessionProvider

RAW_DIR = Path("tests/fixtures/raw")
OUT_DIR = Path("tests/fixtures")

_MEMBER_ID = re.compile(r"ACoAA[A-Za-z0-9_-]+")
_TRACKING = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b")
_SIGNED_URL = re.compile(r"https://media\.licdn\.com/dms/image/[^\"\\s]+")
_EMAIL = re.compile(r"[\\w.+-]+@[\\w-]+\\.[\\w.]+")


def _stable(prefix: str, value: str) -> str:
    digest = hashlib.sha256(value.encode()).hexdigest()[:16]
    return f"{prefix}{digest}"


def scrub(payload: Any) -> Any:
    """Replace identifying values with stable synthetic ones, preserving structure."""
    text = json.dumps(payload)
    text = _MEMBER_ID.sub(lambda m: _stable("ACoAA", m.group(0)), text)
    text = _TRACKING.sub("00000000-0000-0000-0000-000000000000", text)
    text = _SIGNED_URL.sub("https://media.licdn.com/dms/image/REDACTED", text)
    text = _EMAIL.sub("redacted@example.com", text)
    return json.loads(text)


async def capture(name: str, slug: str) -> None:
    settings = get_settings()
    async with httpx.AsyncClient(timeout=30) as http:
        provider = SessionProvider(
            settings, lambda u, p: login(u, p, settings, http)
        )
        client = VoyagerClient(settings, provider, http)
        path, params = dash_profile(slug)
        payload = await client.get_json(path, params, referer_slug=slug)

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (RAW_DIR / f"{name}.json").write_text(json.dumps(payload, indent=2))
    (OUT_DIR / f"{name}.json").write_text(json.dumps(scrub(payload), indent=2))
    print(f"captured {name} from {slug}")


async def main() -> None:
    targets = dict(arg.split("=", 1) for arg in sys.argv[1:])
    if not targets:
        print(__doc__)
        raise SystemExit(1)
    for name, slug in targets.items():
        await capture(name, slug)
        await asyncio.sleep(35)  # respect the outbound rate ceiling


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: Obtain a live session**

Log in to LinkedIn in a browser using the throwaway account. Copy the `li_at` and `JSESSIONID` cookie values into a local `.env`. In the browser devtools Network tab, find any `voyager/api` request and copy the `clientVersion` value out of its `x-li-track` header into `.env` as `CLIENT_VERSION`.

- [ ] **Step 3: Capture three profiles**

Run: `python -m scripts.capture_fixtures sparse=<slug> dense=<slug> intl=<slug>`

Choose a sparse profile, a dense profile with more than 10 positions or 30 skills so collection truncation appears, and a profile with non-English content.

Expected: three files in `tests/fixtures/` and three in `tests/fixtures/raw/`.

- [ ] **Step 4: Verify the scrubbed fixtures contain no personal data**

Run: `grep -riE 'ACoAA[A-Za-z0-9_-]{20,}|@gmail|@outlook' tests/fixtures/*.json`
Expected: no matches.

- [ ] **Step 5: Verify raw fixtures are not staged**

Run: `git status --short tests/fixtures/`
Expected: only the scrubbed `tests/fixtures/*.json` files appear. `tests/fixtures/raw/` must not.

- [ ] **Step 6: Commit**

```bash
git add scripts/capture_fixtures.py tests/fixtures/*.json
git commit -m "chore: add fixture capture script and scrubbed profile fixtures"
```

---

### Task 8: Response models

**Files:**
- Create: `app/models.py`
- Test: `tests/test_models.py`

**Interfaces:**
- Consumes: nothing
- Produces: `LinkedInDate`, `Image`, `ImageSet`, `Location`, `Company`, `School`, `Position`, `Education`, `Skill`, `Certification`, `Language`, `Profile`, `SectionWarning`, `Meta`, `ProfileResponse`

- [ ] **Step 1: Write the failing test**

Create `tests/test_models.py`:

```python
from app.models import LinkedInDate, Position, Profile, ProfileResponse


def test_every_profile_field_is_optional():
    profile = Profile()
    assert profile.full_name is None
    assert profile.images.profile == []


def test_partial_dates_are_allowed():
    assert LinkedInDate(year=2024).month is None


def test_iso_is_none_without_a_year():
    assert LinkedInDate().iso is None


def test_iso_uses_available_precision_only():
    assert LinkedInDate(year=2024).iso == "2024"
    assert LinkedInDate(year=2024, month=3).iso == "2024-03"
    assert LinkedInDate(year=2024, month=3, day=9).iso == "2024-03-09"


def test_response_serialises_with_empty_sections():
    payload = ProfileResponse(meta={"requested_url": "x"}, profile=Profile()).model_dump()
    assert payload["experience"] == []
    assert payload["meta"]["requested_url"] == "x"


def test_position_defaults_to_not_current():
    assert Position(title="Engineer").is_current is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_models.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.models'`

- [ ] **Step 3: Create `app/models.py`**

```python
"""Public response schema. Every field is optional because LinkedIn omits or gates
arbitrary sections, and an absent field is not an error."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

Completeness = Literal["full", "partial", "unavailable"]


class LinkedInDate(BaseModel):
    year: int | None = None
    month: int | None = None
    day: int | None = None

    @property
    def iso(self) -> str | None:
        """Render at the precision LinkedIn actually supplied, never more."""
        if self.year is None:
            return None
        if self.month is None:
            return f"{self.year:04d}"
        if self.day is None:
            return f"{self.year:04d}-{self.month:02d}"
        return f"{self.year:04d}-{self.month:02d}-{self.day:02d}"


class Image(BaseModel):
    url: str
    width: int | None = None
    height: int | None = None
    expires_at: datetime | None = None


class ImageSet(BaseModel):
    profile: list[Image] = Field(default_factory=list)
    background: list[Image] = Field(default_factory=list)


class Location(BaseModel):
    full: str | None = None
    country: str | None = None
    city: str | None = None


class Company(BaseModel):
    name: str | None = None
    urn: str | None = None
    linkedin_url: str | None = None
    logo: str | None = None


class School(BaseModel):
    name: str | None = None
    urn: str | None = None
    linkedin_url: str | None = None
    logo: str | None = None


class Position(BaseModel):
    title: str | None = None
    employment_type: str | None = None
    company: Company = Field(default_factory=Company)
    location: str | None = None
    description: str | None = None
    start_date: LinkedInDate | None = None
    end_date: LinkedInDate | None = None
    is_current: bool = False
    duration_months: int | None = None
    group_id: str | None = None


class Education(BaseModel):
    school: School = Field(default_factory=School)
    degree: str | None = None
    field_of_study: str | None = None
    grade: str | None = None
    activities: str | None = None
    description: str | None = None
    start_date: LinkedInDate | None = None
    end_date: LinkedInDate | None = None


class Skill(BaseModel):
    name: str | None = None
    endorsement_count: int | None = None


class Certification(BaseModel):
    name: str | None = None
    issuer: str | None = None
    issue_date: LinkedInDate | None = None
    expiration_date: LinkedInDate | None = None
    credential_id: str | None = None
    credential_url: str | None = None


class Language(BaseModel):
    name: str | None = None
    proficiency: str | None = None


class Profile(BaseModel):
    urn: str | None = None
    public_identifier: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    full_name: str | None = None
    headline: str | None = None
    about: str | None = None
    location: Location = Field(default_factory=Location)
    industry: str | None = None
    pronouns: str | None = None
    follower_count: int | None = None
    connection_count: int | None = None
    # LinkedIn reports 500 for anyone with 500 or more, so the raw number would lie.
    connection_count_capped: bool = False
    is_premium: bool = False
    is_influencer: bool = False
    is_open_to_work: bool = False
    images: ImageSet = Field(default_factory=ImageSet)


class SectionWarning(BaseModel):
    section: str
    reason: str
    detail: str | None = None


class Meta(BaseModel):
    requested_url: str | None = None
    public_identifier: str | None = None
    fetched_at: datetime | None = None
    data_source: str | None = None
    cache_hit: bool = False
    duration_ms: int | None = None
    completeness: dict[str, Completeness] = Field(default_factory=dict)
    warnings: list[SectionWarning] = Field(default_factory=list)


class ProfileResponse(BaseModel):
    meta: Meta = Field(default_factory=Meta)
    profile: Profile = Field(default_factory=Profile)
    experience: list[Position] = Field(default_factory=list)
    education: list[Education] = Field(default_factory=list)
    skills: list[Skill] = Field(default_factory=list)
    certifications: list[Certification] = Field(default_factory=list)
    languages: list[Language] = Field(default_factory=list)
    # Best-effort sections: populated when present, never scored for completeness.
    honors: list[dict] = Field(default_factory=list)
    publications: list[dict] = Field(default_factory=list)
    projects: list[dict] = Field(default_factory=list)
    volunteer: list[dict] = Field(default_factory=list)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_models.py -v`
Expected: PASS, 6 passed

- [ ] **Step 5: Commit**

```bash
git add app/models.py tests/test_models.py
git commit -m "feat: add Pydantic response schema for profile data"
```

---

### Task 9: Identity parser and image URL assembly

**Files:**
- Create: `app/linkedin/parsers/__init__.py`, `app/linkedin/parsers/images.py`, `app/linkedin/parsers/profile.py`
- Test: `tests/test_parsers/__init__.py`, `tests/test_parsers/test_images.py`, `tests/test_parsers/test_profile.py`

**Interfaces:**
- Consumes: `app.models.Profile`, `app.models.Image`, `app.models.Location`
- Produces:
  - `app.linkedin.parsers.images.build_images(vector: dict | None) -> list[Image]`
  - `app.linkedin.parsers.profile.parse_profile(node: dict) -> Profile`

- [ ] **Step 1: Write the failing test for image assembly**

Create `tests/test_parsers/test_images.py` (`tests/test_parsers/__init__.py` is empty):

```python
from app.linkedin.parsers.images import build_images


def test_assembles_urls_from_root_and_artifacts():
    vector = {
        "rootUrl": "https://media.licdn.com/dms/image/ABC/",
        "artifacts": [
            {
                "fileIdentifyingUrlPathSegment": "100_100/0/1.jpg?e=1735689600&v=beta",
                "width": 100,
                "height": 100,
                "expiresAt": 1735689600000,
            },
            {
                "fileIdentifyingUrlPathSegment": "400_400/0/1.jpg?e=1735689600&v=beta",
                "width": 400,
                "height": 400,
            },
        ],
    }
    images = build_images(vector)
    assert images[0].url == (
        "https://media.licdn.com/dms/image/ABC/100_100/0/1.jpg?e=1735689600&v=beta"
    )
    assert images[0].width == 100
    assert images[0].expires_at is not None
    assert images[1].expires_at is None


def test_missing_vector_returns_empty_list():
    assert build_images(None) == []
    assert build_images({}) == []


def test_artifacts_without_a_path_segment_are_skipped():
    vector = {"rootUrl": "https://x/", "artifacts": [{"width": 100}]}
    assert build_images(vector) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_parsers/test_images.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.linkedin.parsers'`

- [ ] **Step 3: Create `app/linkedin/parsers/images.py`**

`app/linkedin/parsers/__init__.py` is an empty file.

```python
"""Assembly of LinkedIn image URLs.

Images arrive as a ``vectorImage``: a ``rootUrl`` plus one artifact per resolution, each
holding a path segment. The full URL is the concatenation. These URLs are signed and
expire, which is why the expiry is surfaced to callers.
"""

from datetime import UTC, datetime
from typing import Any

from app.models import Image


def build_images(vector: dict[str, Any] | None) -> list[Image]:
    if not vector:
        return []
    root = vector.get("rootUrl")
    artifacts = vector.get("artifacts")
    if not isinstance(root, str) or not isinstance(artifacts, list):
        return []

    images: list[Image] = []
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            continue
        segment = artifact.get("fileIdentifyingUrlPathSegment")
        if not isinstance(segment, str):
            continue
        expires_ms = artifact.get("expiresAt")
        images.append(
            Image(
                url=f"{root}{segment}",
                width=artifact.get("width"),
                height=artifact.get("height"),
                expires_at=(
                    datetime.fromtimestamp(expires_ms / 1000, tz=UTC)
                    if isinstance(expires_ms, int)
                    else None
                ),
            )
        )
    return images
```

- [ ] **Step 4: Write the failing test for the profile parser**

Create `tests/test_parsers/test_profile.py`:

```python
from app.linkedin.parsers.profile import parse_profile


def test_parses_core_identity_fields():
    node = {
        "entityUrn": "urn:li:fsd_profile:ABC",
        "publicIdentifier": "ada",
        "firstName": "Ada",
        "lastName": "Lovelace",
        "headline": "Mathematician",
        "summary": "About text",
        "industry": {"name": "Software Development"},
        "geoLocation": {"geo": {"defaultLocalizedName": "London, England, United Kingdom"}},
    }
    profile = parse_profile(node)
    assert profile.full_name == "Ada Lovelace"
    assert profile.headline == "Mathematician"
    assert profile.about == "About text"
    assert profile.location.full == "London, England, United Kingdom"
    assert profile.industry == "Software Development"


def test_caps_connection_count_at_500():
    profile = parse_profile({"connections": {"paging": {"total": 500}}})
    assert profile.connection_count == 500
    assert profile.connection_count_capped is True


def test_does_not_cap_below_500():
    profile = parse_profile({"connections": {"paging": {"total": 342}}})
    assert profile.connection_count_capped is False


def test_empty_node_yields_an_empty_profile():
    profile = parse_profile({})
    assert profile.full_name is None
    assert profile.images.profile == []


def test_full_name_omits_missing_parts():
    assert parse_profile({"firstName": "Ada"}).full_name == "Ada"
```

- [ ] **Step 5: Run test to verify it fails**

Run: `pytest tests/test_parsers/test_profile.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.linkedin.parsers.profile'`

- [ ] **Step 6: Create `app/linkedin/parsers/profile.py`**

```python
"""Parse the identity portion of a normalized profile node. Performs no I/O."""

from typing import Any

from app.linkedin.parsers.images import build_images
from app.models import ImageSet, Location, Profile


def _text(node: Any, *path: str) -> str | None:
    """Walk a chain of dict keys, returning None if any link is missing."""
    current = node
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current if isinstance(current, str) else None


def parse_profile(node: dict[str, Any]) -> Profile:
    first = node.get("firstName") if isinstance(node.get("firstName"), str) else None
    last = node.get("lastName") if isinstance(node.get("lastName"), str) else None
    full = " ".join(part for part in (first, last) if part) or None

    total = None
    connections = node.get("connections")
    if isinstance(connections, dict):
        paging = connections.get("paging")
        if isinstance(paging, dict) and isinstance(paging.get("total"), int):
            total = paging["total"]

    location_text = _text(node, "geoLocation", "geo", "defaultLocalizedName") or _text(
        node, "geoLocationName"
    )

    return Profile(
        urn=node.get("entityUrn") if isinstance(node.get("entityUrn"), str) else None,
        public_identifier=node.get("publicIdentifier"),
        first_name=first,
        last_name=last,
        full_name=full,
        headline=node.get("headline") if isinstance(node.get("headline"), str) else None,
        about=node.get("summary") if isinstance(node.get("summary"), str) else None,
        location=Location(full=location_text),
        industry=_text(node, "industry", "name"),
        pronouns=_text(node, "pronoun", "standardizedPronoun"),
        follower_count=node.get("followerCount"),
        connection_count=total,
        # LinkedIn reports exactly 500 for everyone at or above 500 connections.
        connection_count_capped=total == 500,
        is_premium=bool(node.get("premium")),
        is_influencer=bool(node.get("influencer")),
        is_open_to_work=bool(node.get("openToWork")),
        images=ImageSet(
            profile=build_images(_vector(node, "profilePicture")),
            background=build_images(_vector(node, "backgroundImage")),
        ),
    )


def _vector(node: dict[str, Any], key: str) -> dict[str, Any] | None:
    """Unwrap the displayImageReference wrapper LinkedIn puts around vector images."""
    container = node.get(key)
    if not isinstance(container, dict):
        return None
    reference = container.get("displayImageReference")
    if isinstance(reference, dict) and isinstance(reference.get("vectorImage"), dict):
        return reference["vectorImage"]
    if isinstance(container.get("vectorImage"), dict):
        return container["vectorImage"]
    return None
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `pytest tests/test_parsers -v`
Expected: PASS, 8 passed

- [ ] **Step 8: Commit**

```bash
git add app/linkedin/parsers tests/test_parsers
git commit -m "feat: parse profile identity fields and assemble image URLs"
```

---

### Task 10: Experience and education parsers

**Files:**
- Create: `app/linkedin/parsers/dates.py`, `app/linkedin/parsers/experience.py`, `app/linkedin/parsers/education.py`
- Test: `tests/test_parsers/test_dates.py`, `tests/test_parsers/test_experience.py`, `tests/test_parsers/test_education.py`

**Interfaces:**
- Consumes: `app.models.Position`, `app.models.Education`, `app.models.LinkedInDate`, `app.models.Company`, `app.models.School`
- Produces:
  - `app.linkedin.parsers.dates.parse_date(node: dict | None) -> LinkedInDate | None`
  - `app.linkedin.parsers.dates.months_between(start: LinkedInDate | None, end: LinkedInDate | None, today: date | None = None) -> int | None`
  - `app.linkedin.parsers.experience.parse_experience(nodes: list[dict]) -> list[Position]`
  - `app.linkedin.parsers.education.parse_education(nodes: list[dict]) -> list[Education]`

- [ ] **Step 1: Write the failing date tests**

Create `tests/test_parsers/test_dates.py`:

```python
from datetime import date

from app.linkedin.parsers.dates import months_between, parse_date
from app.models import LinkedInDate


def test_parses_year_only():
    parsed = parse_date({"year": 2024})
    assert parsed is not None
    assert (parsed.year, parsed.month, parsed.day) == (2024, None, None)


def test_parses_year_and_month():
    parsed = parse_date({"year": 2024, "month": 3})
    assert parsed is not None
    assert parsed.month == 3


def test_missing_or_empty_node_returns_none():
    assert parse_date(None) is None
    assert parse_date({}) is None


def test_months_between_treats_missing_end_as_today():
    result = months_between(LinkedInDate(year=2024, month=1), None, today=date(2024, 7, 1))
    assert result == 6


def test_months_between_uses_january_when_month_is_absent():
    result = months_between(LinkedInDate(year=2023), LinkedInDate(year=2024), today=None)
    assert result == 12


def test_months_between_returns_none_without_a_start():
    assert months_between(None, None, today=date(2024, 7, 1)) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_parsers/test_dates.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.linkedin.parsers.dates'`

- [ ] **Step 3: Create `app/linkedin/parsers/dates.py`**

```python
"""Date handling. LinkedIn often supplies year only, so precision is preserved
rather than synthesised."""

from datetime import date
from typing import Any

from app.models import LinkedInDate


def parse_date(node: dict[str, Any] | None) -> LinkedInDate | None:
    if not isinstance(node, dict):
        return None
    year, month, day = node.get("year"), node.get("month"), node.get("day")
    if not isinstance(year, int):
        return None
    return LinkedInDate(
        year=year,
        month=month if isinstance(month, int) else None,
        day=day if isinstance(day, int) else None,
    )


def months_between(
    start: LinkedInDate | None, end: LinkedInDate | None, today: date | None = None
) -> int | None:
    """Whole months from start to end, treating an absent end as ongoing."""
    if start is None or start.year is None:
        return None
    now = today or date.today()
    end_year = end.year if end and end.year else now.year
    end_month = (end.month if end and end.month else (1 if end and end.year else now.month))
    return (end_year - start.year) * 12 + (end_month - (start.month or 1))
```

- [ ] **Step 4: Write the failing experience test**

Create `tests/test_parsers/test_experience.py`:

```python
from app.linkedin.parsers.experience import parse_experience


def _position(**overrides):
    node = {
        "title": "Software Engineer",
        "companyName": "Tross",
        "employmentTypeUrn": None,
        "locationName": "Remote",
        "description": "Built things",
        "dateRange": {"start": {"year": 2024, "month": 3}, "end": None},
        "company": {
            "entityUrn": "urn:li:fsd_company:1",
            "name": "Tross",
            "url": "https://www.linkedin.com/company/tross",
        },
    }
    node.update(overrides)
    return node


def test_parses_a_current_position():
    [position] = parse_experience([_position()])
    assert position.title == "Software Engineer"
    assert position.company.name == "Tross"
    assert position.company.urn == "urn:li:fsd_company:1"
    assert position.is_current is True
    assert position.location == "Remote"


def test_end_date_marks_position_as_past():
    node = _position(dateRange={"start": {"year": 2020}, "end": {"year": 2022}})
    [position] = parse_experience([node])
    assert position.is_current is False
    assert position.duration_months == 24


def test_falls_back_to_company_name_string_when_company_object_is_absent():
    node = _position(company=None)
    [position] = parse_experience([node])
    assert position.company.name == "Tross"


def test_roles_at_the_same_company_share_a_group_id():
    nodes = [_position(title="Engineer"), _position(title="Senior Engineer")]
    grouped = parse_experience(nodes)
    assert grouped[0].group_id == grouped[1].group_id


def test_roles_at_different_companies_do_not_share_a_group_id():
    other = _position(companyName="Other", company={"entityUrn": "urn:li:fsd_company:2"})
    grouped = parse_experience([_position(), other])
    assert grouped[0].group_id != grouped[1].group_id


def test_non_dict_entries_are_ignored():
    assert parse_experience([None, "junk", _position()]) != []
```

- [ ] **Step 5: Run test to verify it fails**

Run: `pytest tests/test_parsers/test_experience.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.linkedin.parsers.experience'`

- [ ] **Step 6: Create `app/linkedin/parsers/experience.py`**

```python
"""Parse position history. Performs no I/O."""

from typing import Any

from app.linkedin.parsers.dates import months_between, parse_date
from app.linkedin.parsers.images import build_images
from app.models import Company, Position


def _company(node: dict[str, Any]) -> Company:
    company = node.get("company")
    if not isinstance(company, dict):
        # Some payloads carry only the denormalized company name.
        return Company(name=node.get("companyName"))
    logo = build_images(_logo_vector(company))
    return Company(
        name=company.get("name") or node.get("companyName"),
        urn=company.get("entityUrn"),
        linkedin_url=company.get("url"),
        logo=logo[0].url if logo else None,
    )


def _logo_vector(company: dict[str, Any]) -> dict[str, Any] | None:
    logo = company.get("logo")
    if isinstance(logo, dict):
        reference = logo.get("displayImageReference")
        if isinstance(reference, dict) and isinstance(reference.get("vectorImage"), dict):
            return reference["vectorImage"]
        if isinstance(logo.get("vectorImage"), dict):
            return logo["vectorImage"]
    return None


def parse_experience(nodes: list[Any]) -> list[Position]:
    positions: list[Position] = []
    groups: dict[str, str] = {}

    for node in nodes:
        if not isinstance(node, dict):
            continue
        date_range = node.get("dateRange") if isinstance(node.get("dateRange"), dict) else {}
        start = parse_date(date_range.get("start"))
        end = parse_date(date_range.get("end"))
        company = _company(node)

        key = company.urn or company.name or "unknown"
        group_id = groups.setdefault(key, f"grp_{len(groups) + 1}")

        positions.append(
            Position(
                title=node.get("title"),
                employment_type=node.get("employmentType") or node.get("employmentTypeUrn"),
                company=company,
                location=node.get("locationName"),
                description=node.get("description"),
                start_date=start,
                end_date=end,
                is_current=end is None and start is not None,
                duration_months=months_between(start, end),
                group_id=group_id,
            )
        )
    return positions
```

- [ ] **Step 7: Write the failing education test**

Create `tests/test_parsers/test_education.py`:

```python
from app.linkedin.parsers.education import parse_education


def test_parses_an_education_entry():
    node = {
        "schoolName": "Cambridge",
        "degreeName": "BSc",
        "fieldOfStudy": "Computer Science",
        "grade": "First",
        "activities": "Rowing",
        "description": "Notes",
        "dateRange": {"start": {"year": 2016}, "end": {"year": 2019}},
        "school": {"entityUrn": "urn:li:fsd_school:1", "name": "Cambridge"},
    }
    [education] = parse_education([node])
    assert education.school.name == "Cambridge"
    assert education.school.urn == "urn:li:fsd_school:1"
    assert education.degree == "BSc"
    assert education.field_of_study == "Computer Science"
    assert education.start_date is not None
    assert education.start_date.year == 2016


def test_falls_back_to_school_name_string():
    [education] = parse_education([{"schoolName": "Cambridge"}])
    assert education.school.name == "Cambridge"


def test_non_dict_entries_are_ignored():
    assert parse_education([None, 7]) == []
```

- [ ] **Step 8: Run test to verify it fails**

Run: `pytest tests/test_parsers/test_education.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.linkedin.parsers.education'`

- [ ] **Step 9: Create `app/linkedin/parsers/education.py`**

```python
"""Parse education history. Performs no I/O."""

from typing import Any

from app.linkedin.parsers.dates import parse_date
from app.models import Education, School


def parse_education(nodes: list[Any]) -> list[Education]:
    entries: list[Education] = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        school = node.get("school")
        if isinstance(school, dict):
            resolved = School(
                name=school.get("name") or node.get("schoolName"),
                urn=school.get("entityUrn"),
                linkedin_url=school.get("url"),
            )
        else:
            resolved = School(name=node.get("schoolName"))

        date_range = node.get("dateRange") if isinstance(node.get("dateRange"), dict) else {}
        entries.append(
            Education(
                school=resolved,
                degree=node.get("degreeName"),
                field_of_study=node.get("fieldOfStudy"),
                grade=node.get("grade"),
                activities=node.get("activities"),
                description=node.get("description"),
                start_date=parse_date(date_range.get("start")),
                end_date=parse_date(date_range.get("end")),
            )
        )
    return entries
```

- [ ] **Step 10: Run tests to verify they pass**

Run: `pytest tests/test_parsers -v`
Expected: PASS, all tests in the directory

- [ ] **Step 11: Commit**

```bash
git add app/linkedin/parsers tests/test_parsers
git commit -m "feat: parse experience and education sections"
```

---

### Task 11: Skills, certifications, and languages parsers

**Files:**
- Create: `app/linkedin/parsers/skills.py`, `app/linkedin/parsers/certifications.py`, `app/linkedin/parsers/languages.py`
- Test: `tests/test_parsers/test_skills.py`, `tests/test_parsers/test_certifications.py`, `tests/test_parsers/test_languages.py`

**Interfaces:**
- Consumes: `app.models.Skill`, `app.models.Certification`, `app.models.Language`, `app.linkedin.parsers.dates.parse_date`
- Produces:
  - `app.linkedin.parsers.skills.parse_skills(nodes: list[dict]) -> list[Skill]`
  - `app.linkedin.parsers.certifications.parse_certifications(nodes: list[dict]) -> list[Certification]`
  - `app.linkedin.parsers.languages.parse_languages(nodes: list[dict]) -> list[Language]`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_parsers/test_skills.py`:

```python
from app.linkedin.parsers.skills import parse_skills


def test_parses_name_and_endorsement_count():
    nodes = [{"name": "Python", "endorsementCount": 32}]
    [skill] = parse_skills(nodes)
    assert skill.name == "Python"
    assert skill.endorsement_count == 32


def test_missing_endorsement_count_is_none_not_zero():
    [skill] = parse_skills([{"name": "Go"}])
    assert skill.endorsement_count is None


def test_entries_without_a_name_are_skipped():
    assert parse_skills([{"endorsementCount": 3}, None]) == []
```

Create `tests/test_parsers/test_certifications.py`:

```python
from app.linkedin.parsers.certifications import parse_certifications


def test_parses_a_certification():
    nodes = [
        {
            "name": "AWS Solutions Architect",
            "authority": "Amazon Web Services",
            "licenseNumber": "ABC-123",
            "url": "https://example.com/cert",
            "dateRange": {"start": {"year": 2023, "month": 5}, "end": {"year": 2026}},
        }
    ]
    [cert] = parse_certifications(nodes)
    assert cert.name == "AWS Solutions Architect"
    assert cert.issuer == "Amazon Web Services"
    assert cert.credential_id == "ABC-123"
    assert cert.credential_url == "https://example.com/cert"
    assert cert.issue_date is not None
    assert cert.issue_date.month == 5
    assert cert.expiration_date is not None


def test_certification_without_expiry_has_none():
    [cert] = parse_certifications([{"name": "X", "dateRange": {"start": {"year": 2023}}}])
    assert cert.expiration_date is None


def test_entries_without_a_name_are_skipped():
    assert parse_certifications([{"authority": "X"}, 5]) == []
```

Create `tests/test_parsers/test_languages.py`:

```python
from app.linkedin.parsers.languages import parse_languages


def test_parses_name_and_proficiency():
    [language] = parse_languages([{"name": "English", "proficiency": "NATIVE_OR_BILINGUAL"}])
    assert language.name == "English"
    assert language.proficiency == "Native or bilingual"


def test_unknown_proficiency_is_passed_through_unchanged():
    [language] = parse_languages([{"name": "Klingon", "proficiency": "SOMETHING_NEW"}])
    assert language.proficiency == "SOMETHING_NEW"


def test_missing_proficiency_is_none():
    [language] = parse_languages([{"name": "Hindi"}])
    assert language.proficiency is None


def test_entries_without_a_name_are_skipped():
    assert parse_languages([{"proficiency": "NATIVE_OR_BILINGUAL"}, None]) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_parsers/test_skills.py tests/test_parsers/test_certifications.py tests/test_parsers/test_languages.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Create `app/linkedin/parsers/skills.py`**

```python
"""Parse the skills section. Performs no I/O."""

from typing import Any

from app.models import Skill


def parse_skills(nodes: list[Any]) -> list[Skill]:
    skills: list[Skill] = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        name = node.get("name")
        if not isinstance(name, str) or not name:
            continue
        count = node.get("endorsementCount")
        skills.append(
            Skill(name=name, endorsement_count=count if isinstance(count, int) else None)
        )
    return skills
```

- [ ] **Step 4: Create `app/linkedin/parsers/certifications.py`**

```python
"""Parse the certifications section. Performs no I/O."""

from typing import Any

from app.linkedin.parsers.dates import parse_date
from app.models import Certification


def parse_certifications(nodes: list[Any]) -> list[Certification]:
    certifications: list[Certification] = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        name = node.get("name")
        if not isinstance(name, str) or not name:
            continue
        date_range = node.get("dateRange") if isinstance(node.get("dateRange"), dict) else {}
        certifications.append(
            Certification(
                name=name,
                issuer=node.get("authority") or node.get("companyName"),
                issue_date=parse_date(date_range.get("start")),
                expiration_date=parse_date(date_range.get("end")),
                credential_id=node.get("licenseNumber"),
                credential_url=node.get("url"),
            )
        )
    return certifications
```

- [ ] **Step 5: Create `app/linkedin/parsers/languages.py`**

```python
"""Parse the languages section. Performs no I/O."""

from typing import Any

from app.models import Language

# LinkedIn returns screaming-snake enum values. Unknown values pass through unchanged
# so that a new enum member degrades to a raw string instead of vanishing.
_PROFICIENCY = {
    "NATIVE_OR_BILINGUAL": "Native or bilingual",
    "FULL_PROFESSIONAL": "Full professional",
    "PROFESSIONAL_WORKING": "Professional working",
    "LIMITED_WORKING": "Limited working",
    "ELEMENTARY": "Elementary",
}


def parse_languages(nodes: list[Any]) -> list[Language]:
    languages: list[Language] = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        name = node.get("name")
        if not isinstance(name, str) or not name:
            continue
        raw = node.get("proficiency")
        languages.append(
            Language(
                name=name,
                proficiency=_PROFICIENCY.get(raw, raw) if isinstance(raw, str) else None,
            )
        )
    return languages
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_parsers -v`
Expected: PASS, all tests in the directory

- [ ] **Step 7: Commit**

```bash
git add app/linkedin/parsers tests/test_parsers
git commit -m "feat: parse skills, certifications, and languages sections"
```

---

### Task 12: Cache, outbound rate limiter, and API key guard

**Files:**
- Create: `app/cache.py`, `app/ratelimit.py`, `app/api/deps.py`
- Test: `tests/test_cache.py`, `tests/test_ratelimit.py`, `tests/test_deps.py`

**Interfaces:**
- Consumes: `app.config.Settings`
- Produces:
  - `app.cache.TTLCache(ttl_seconds: float)` with `get(key: str) -> Any | None`, `set(key: str, value: Any) -> None`, `clear() -> None`
  - `app.ratelimit.TokenBucket(rate_seconds: float, burst: int)` with `async acquire() -> None`
  - `app.ratelimit.InboundLimiter(per_minute: int)` with `check(key: str) -> int | None` returning seconds to wait, or None when allowed
  - `app.api.deps.require_api_key` FastAPI dependency raising `HTTPException(401)`
  - `app.api.deps.enforce_inbound_limit` FastAPI dependency raising `HTTPException(429)` with a `Retry-After` header

- [ ] **Step 1: Write the failing tests**

Create `tests/test_cache.py`:

```python
from app.cache import TTLCache


def test_returns_stored_value():
    cache = TTLCache(ttl_seconds=60)
    cache.set("a", {"x": 1})
    assert cache.get("a") == {"x": 1}


def test_missing_key_returns_none():
    assert TTLCache(ttl_seconds=60).get("nope") is None


def test_expired_entry_returns_none():
    # First value is consumed by set(), the second by get().
    clock = iter([0.0, 100.0])
    cache = TTLCache(ttl_seconds=60, clock=lambda: next(clock))
    cache.set("a", 1)
    assert cache.get("a") is None


def test_clear_removes_everything():
    cache = TTLCache(ttl_seconds=60)
    cache.set("a", 1)
    cache.clear()
    assert cache.get("a") is None
```

Create `tests/test_ratelimit.py`:

```python
from app.ratelimit import TokenBucket


async def test_burst_requests_do_not_sleep():
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    bucket = TokenBucket(rate_seconds=30, burst=3, sleep=fake_sleep, clock=lambda: 0.0)
    for _ in range(3):
        await bucket.acquire()
    assert slept == []


async def test_fourth_request_sleeps():
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    bucket = TokenBucket(rate_seconds=30, burst=3, sleep=fake_sleep, clock=lambda: 0.0)
    for _ in range(4):
        await bucket.acquire()
    assert len(slept) == 1
    assert slept[0] > 0


def test_inbound_limiter_allows_up_to_the_quota():
    from app.ratelimit import InboundLimiter

    limiter = InboundLimiter(per_minute=3, clock=lambda: 0.0)
    assert [limiter.check("k") for _ in range(3)] == [None, None, None]


def test_inbound_limiter_blocks_beyond_the_quota():
    from app.ratelimit import InboundLimiter

    limiter = InboundLimiter(per_minute=2, clock=lambda: 0.0)
    limiter.check("k")
    limiter.check("k")
    assert limiter.check("k") == 60


def test_inbound_limiter_tracks_keys_independently():
    from app.ratelimit import InboundLimiter

    limiter = InboundLimiter(per_minute=1, clock=lambda: 0.0)
    assert limiter.check("a") is None
    assert limiter.check("b") is None


def test_inbound_limiter_window_expires():
    from app.ratelimit import InboundLimiter

    now = [0.0]
    limiter = InboundLimiter(per_minute=1, clock=lambda: now[0])
    limiter.check("k")
    now[0] = 61.0
    assert limiter.check("k") is None
```

Create `tests/test_deps.py`:

```python
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.main import create_app


def _client(keys: list[str]) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: Settings(api_keys=keys)
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_cache.py tests/test_ratelimit.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.cache'`

Note: `tests/test_deps.py` will fail until Task 13 adds the route. Run it at the end of Task 13.

- [ ] **Step 3: Create `app/cache.py`**

```python
"""In-process TTL cache. A single instance is sufficient for one container; the clock is
injectable so expiry can be tested without sleeping."""

import time
from collections.abc import Callable
from typing import Any


class TTLCache:
    def __init__(self, ttl_seconds: float, clock: Callable[[], float] = time.monotonic) -> None:
        self._ttl = ttl_seconds
        self._clock = clock
        self._entries: dict[str, tuple[float, Any]] = {}

    def get(self, key: str) -> Any | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        stored_at, value = entry
        if self._clock() - stored_at > self._ttl:
            del self._entries[key]
            return None
        return value

    def set(self, key: str, value: Any) -> None:
        self._entries[key] = (self._clock(), value)

    def clear(self) -> None:
        self._entries.clear()
```

- [ ] **Step 4: Create `app/ratelimit.py`**

```python
"""Outbound token bucket.

LinkedIn tolerates roughly one to two Voyager requests per minute per account before
flagging it, so the default is deliberately slow. A small burst allowance lets a single
profile fetch issue its supplementary tier-2 calls without stalling.
"""

import asyncio
import time
from collections.abc import Awaitable, Callable


class TokenBucket:
    def __init__(
        self,
        rate_seconds: float,
        burst: int,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._rate = rate_seconds
        self._burst = burst
        self._sleep = sleep
        self._clock = clock
        self._tokens = float(burst)
        self._updated = clock()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = self._clock()
            self._tokens = min(
                self._burst, self._tokens + (now - self._updated) / self._rate
            )
            self._updated = now
            if self._tokens < 1:
                wait = (1 - self._tokens) * self._rate
                await self._sleep(wait)
                self._tokens = 1
            self._tokens -= 1


class InboundLimiter:
    """Fixed-window limiter for callers of this API, keyed by API key.

    Distinct from TokenBucket: that one paces our calls out to LinkedIn, this one caps
    how fast a client may call us so a single caller cannot monopolise the account's
    limited outbound budget.
    """

    def __init__(
        self, per_minute: int, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._quota = per_minute
        self._clock = clock
        self._hits: dict[str, list[float]] = {}

    def check(self, key: str) -> int | None:
        """Record a hit. Returns seconds to wait when over quota, otherwise None."""
        now = self._clock()
        window = [hit for hit in self._hits.get(key, []) if now - hit < 60]
        if len(window) >= self._quota:
            self._hits[key] = window
            return max(1, int(60 - (now - window[0])))
        window.append(now)
        self._hits[key] = window
        return None
```

- [ ] **Step 5: Create `app/api/deps.py`**

```python
"""Shared FastAPI dependencies."""

from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request

from app.config import Settings, get_settings


async def require_api_key(
    x_api_key: Annotated[str | None, Header()] = None,
    settings: Annotated[Settings, Depends(get_settings)] = None,  # type: ignore[assignment]
) -> None:
    """Reject requests without a configured key.

    When no keys are configured the API is open, which is correct for local development
    and must be avoided in production by always setting API_KEYS.
    """
    if not settings.api_keys:
        return
    if x_api_key not in settings.api_keys:
        raise HTTPException(
            status_code=401,
            detail={
                "error": {
                    "code": "unauthorized",
                    "message": "Missing or invalid API key",
                    "hint": "Send the key in the X-API-Key header",
                }
            },
        )


async def enforce_inbound_limit(
    request: Request,
    x_api_key: Annotated[str | None, Header()] = None,
) -> None:
    """Cap how fast one caller may call us, keyed by API key or client address."""
    limiter = getattr(request.app.state, "inbound_limiter", None)
    if limiter is None:
        return
    key = x_api_key or (request.client.host if request.client else "anonymous")
    retry_after = limiter.check(key)
    if retry_after is not None:
        raise HTTPException(
            status_code=429,
            detail={
                "error": {
                    "code": "rate_limited",
                    "message": "Too many requests",
                    "hint": f"Retry after {retry_after} seconds",
                }
            },
            headers={"Retry-After": str(retry_after)},
        )
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_cache.py tests/test_ratelimit.py -v`
Expected: PASS, 6 passed

- [ ] **Step 7: Commit**

```bash
git add app/cache.py app/ratelimit.py app/api/deps.py tests/test_cache.py tests/test_ratelimit.py
git commit -m "feat: add TTL cache, outbound token bucket, and API key guard"
```

---

### Task 13: Service orchestration and the profile route

**Files:**
- Create: `app/service.py`
- Modify: `app/api/routes.py`, `app/main.py`
- Test: `tests/test_service.py`, `tests/test_api.py`

**Interfaces:**
- Consumes: `VoyagerClient`, `normalize`, all parsers, `TTLCache`, `TokenBucket`, `parse_profile_url`, all models
- Produces:
  - `app.service.ProfileService(client, cache, bucket)` with `async fetch(url: str) -> ProfileResponse`
  - `app.service.SECTION_PARSERS` mapping section name to a parser callable
  - `GET /api/v1/profile?url=...` returning `ProfileResponse`

- [ ] **Step 1: Write the failing service test**

Create `tests/test_service.py`:

```python
import pytest

from app.cache import TTLCache
from app.errors import ProfileNotFound, UpstreamError
from app.ratelimit import TokenBucket
from app.service import ProfileService


class StubClient:
    def __init__(self, payload=None, error=None):
        self.payload = payload
        self.error = error
        self.calls = 0

    async def get_json(self, path, params, referer_slug):
        self.calls += 1
        if self.error:
            raise self.error
        return self.payload


def _service(client) -> ProfileService:
    async def no_sleep(_: float) -> None:
        return None

    return ProfileService(
        client,
        TTLCache(ttl_seconds=60),
        TokenBucket(rate_seconds=1, burst=10, sleep=no_sleep, clock=lambda: 0.0),
    )


_PAYLOAD = {
    "data": {"**elements": ["urn:li:fsd_profile:1"]},
    "included": [
        {
            "entityUrn": "urn:li:fsd_profile:1",
            "publicIdentifier": "ada",
            "firstName": "Ada",
            "lastName": "Lovelace",
            "headline": "Mathematician",
            "**profilePositionGroups": [],
        }
    ],
}


async def test_returns_a_parsed_profile():
    response = await _service(StubClient(_PAYLOAD)).fetch(
        "https://www.linkedin.com/in/ada"
    )
    assert response.profile.full_name == "Ada Lovelace"
    assert response.meta.public_identifier == "ada"
    assert response.meta.data_source == "voyager_dash"


async def test_second_call_is_served_from_cache():
    client = StubClient(_PAYLOAD)
    service = _service(client)
    await service.fetch("https://www.linkedin.com/in/ada")
    second = await service.fetch("https://www.linkedin.com/in/ada")
    assert client.calls == 1
    assert second.meta.cache_hit is True


async def test_missing_sections_are_reported_as_unavailable():
    response = await _service(StubClient(_PAYLOAD)).fetch(
        "https://www.linkedin.com/in/ada"
    )
    assert response.meta.completeness["skills"] == "unavailable"


async def test_profile_not_found_propagates():
    with pytest.raises(ProfileNotFound):
        await _service(StubClient(error=ProfileNotFound("gone"))).fetch(
            "https://www.linkedin.com/in/ada"
        )


async def test_empty_payload_raises_upstream_error():
    with pytest.raises(UpstreamError):
        await _service(StubClient({"data": {}, "included": []})).fetch(
            "https://www.linkedin.com/in/ada"
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_service.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.service'`

- [ ] **Step 3: Create `app/service.py`**

```python
"""Orchestrates a profile fetch: rate limit, cache, fetch, normalize, parse, assemble."""

import time
from datetime import UTC, datetime
from typing import Any, Protocol

from app.cache import TTLCache
from app.errors import UpstreamError
from app.linkedin.endpoints import dash_profile
from app.linkedin.normalizer import normalize
from app.linkedin.parsers.certifications import parse_certifications
from app.linkedin.parsers.education import parse_education
from app.linkedin.parsers.experience import parse_experience
from app.linkedin.parsers.languages import parse_languages
from app.linkedin.parsers.profile import parse_profile
from app.linkedin.parsers.skills import parse_skills
from app.linkedin.urls import parse_profile_url
from app.models import Meta, ProfileResponse, SectionWarning
from app.ratelimit import TokenBucket

# Where each section lives in the normalized tree, which parser consumes it, and the
# lowercase fragment that identifies its truncation markers. Truncation markers are
# entityUrns such as "urn:li:fsd_profilePositionGroup:(ACoAA...,123)", so matching is
# done on a stem rather than on the tree key.
SECTION_PARSERS = {
    "experience": ("profilePositionGroups", parse_experience, "position"),
    "education": ("profileEducations", parse_education, "education"),
    "skills": ("profileSkills", parse_skills, "skill"),
    "certifications": ("profileCertifications", parse_certifications, "certification"),
    "languages": ("profileLanguages", parse_languages, "language"),
}


class Fetcher(Protocol):
    async def get_json(
        self, path: str, params: dict[str, str], referer_slug: str
    ) -> dict[str, Any]: ...


def _flatten_positions(groups: list[Any]) -> list[Any]:
    """Position groups nest the actual roles one level down."""
    positions: list[Any] = []
    for group in groups:
        if not isinstance(group, dict):
            continue
        inner = group.get("profilePositionInPositionGroup")
        if isinstance(inner, dict) and isinstance(inner.get("elements"), list):
            positions.extend(inner["elements"])
        elif isinstance(group.get("elements"), list):
            positions.extend(group["elements"])
        else:
            positions.append(group)
    return positions


def _section_nodes(root: dict[str, Any], key: str) -> list[Any] | None:
    """Return the element list for a section, or None when LinkedIn omitted it."""
    container = root.get(key)
    if container is None:
        return None
    if isinstance(container, list):
        return container
    if isinstance(container, dict) and isinstance(container.get("elements"), list):
        return container["elements"]
    return None


class ProfileService:
    def __init__(self, client: Fetcher, cache: TTLCache, bucket: TokenBucket) -> None:
        self._client = client
        self._cache = cache
        self._bucket = bucket

    async def fetch(self, url: str) -> ProfileResponse:
        slug = parse_profile_url(url)

        cached = self._cache.get(slug)
        if cached is not None:
            response = ProfileResponse.model_validate(cached)
            response.meta.cache_hit = True
            return response

        started = time.monotonic()
        await self._bucket.acquire()
        path, params = dash_profile(slug)
        payload = await self._client.get_json(path, params, referer_slug=slug)

        result = normalize(payload)
        root = self._profile_root(result.data)

        response = ProfileResponse(profile=parse_profile(root))
        warnings: list[SectionWarning] = []
        completeness: dict[str, str] = {}

        truncated = [t.section.lower() for t in result.truncations]

        for section, (key, parser, stem) in SECTION_PARSERS.items():
            nodes = _section_nodes(root, key)
            if nodes is None:
                completeness[section] = "unavailable"
                warnings.append(SectionWarning(section=section, reason="not_returned"))
                continue
            if section == "experience":
                nodes = _flatten_positions(nodes)
            setattr(response, section, parser(nodes))
            partial = any(stem in marker for marker in truncated)
            completeness[section] = "partial" if partial else "full"

        for truncation in result.truncations:
            warnings.append(
                SectionWarning(
                    section=truncation.section,
                    reason="paged_truncated",
                    detail=f"{truncation.returned} of {truncation.total} returned",
                )
            )

        response.meta = Meta(
            requested_url=url,
            public_identifier=root.get("publicIdentifier") or slug,
            fetched_at=datetime.now(UTC),
            data_source="voyager_dash",
            cache_hit=False,
            duration_ms=int((time.monotonic() - started) * 1000),
            completeness=completeness,  # type: ignore[arg-type]
            warnings=warnings,
        )

        self._cache.set(slug, response.model_dump(mode="json"))
        return response

    @staticmethod
    def _profile_root(data: dict[str, Any]) -> dict[str, Any]:
        """The dash endpoint wraps the profile in an elements list of length one."""
        elements = data.get("elements")
        if isinstance(elements, list) and elements and isinstance(elements[0], dict):
            return elements[0]
        if data.get("firstName") or data.get("publicIdentifier"):
            return data
        raise UpstreamError("Voyager response contained no profile element")
```

- [ ] **Step 4: Write the failing API test**

Create `tests/test_api.py`:

```python
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.errors import ProfileNotFound
from app.api.routes import get_service
from app.main import create_app
from app.models import Meta, Profile, ProfileResponse


class StubService:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error

    async def fetch(self, url: str) -> ProfileResponse:
        if self.error:
            raise self.error
        # The route validates the URL before delegating, which is what lets
        # test_invalid_url_returns_400 pass while this stub returns unconditionally.
        return self.response


def _client(service) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: Settings(api_keys=["secret"])
    # Override the dependency callable the route actually declares, not the class.
    app.dependency_overrides[get_service] = lambda: service
    return TestClient(app)


_OK = ProfileResponse(meta=Meta(requested_url="x"), profile=Profile(full_name="Ada"))


def test_returns_a_profile_with_a_valid_key():
    response = _client(StubService(_OK)).get(
        "/api/v1/profile",
        params={"url": "https://www.linkedin.com/in/ada"},
        headers={"X-API-Key": "secret"},
    )
    assert response.status_code == 200
    assert response.json()["profile"]["full_name"] == "Ada"


def test_invalid_url_returns_400():
    response = _client(StubService(_OK)).get(
        "/api/v1/profile", params={"url": "https://example.com"}, headers={"X-API-Key": "secret"}
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_url"


def test_missing_profile_returns_404():
    response = _client(StubService(error=ProfileNotFound("gone"))).get(
        "/api/v1/profile",
        params={"url": "https://www.linkedin.com/in/ada"},
        headers={"X-API-Key": "secret"},
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "profile_not_found"


def test_error_body_always_carries_a_hint():
    response = _client(StubService(error=ProfileNotFound("gone"))).get(
        "/api/v1/profile",
        params={"url": "https://www.linkedin.com/in/ada"},
        headers={"X-API-Key": "secret"},
    )
    assert response.json()["error"]["hint"]
```

- [ ] **Step 5: Run test to verify it fails**

Run: `pytest tests/test_api.py -v`
Expected: FAIL with 404 on the route, since `/api/v1/profile` does not exist yet

- [ ] **Step 6: Rewrite `app/api/routes.py`**

Replace the file created in Task 1 with:

```python
"""HTTP routes."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from app.api.deps import enforce_inbound_limit, require_api_key
from app.config import Settings, get_settings
from app.linkedin.urls import parse_profile_url
from app.models import ProfileResponse
from app.service import ProfileService

router = APIRouter()


def get_service(request: Request) -> ProfileService:
    return request.app.state.service


@router.get("/health")
async def health(
    settings: Annotated[Settings, Depends(get_settings)],
    request: Request,
) -> dict[str, object]:
    provider = getattr(request.app.state, "session_provider", None)
    session = provider.status() if provider else {"source": "unconfigured"}
    return {"status": "ok", "session": session, "version": "0.1.0"}


@router.get(
    "/api/v1/profile",
    response_model=ProfileResponse,
    dependencies=[Depends(require_api_key), Depends(enforce_inbound_limit)],
    summary="Fetch a LinkedIn profile as structured JSON",
)
async def get_profile(
    url: Annotated[str, Query(description="A LinkedIn profile URL")],
    service: Annotated[ProfileService, Depends(get_service)],
) -> ProfileResponse:
    # Validate at the HTTP boundary so a malformed URL is rejected before any
    # rate-limit token or cache lookup is spent on it.
    parse_profile_url(url)
    return await service.fetch(url)
```

- [ ] **Step 7: Rewrite `app/main.py`**

```python
"""Application factory and lifespan wiring."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.routes import router
from app.cache import TTLCache
from app.config import get_settings
from app.errors import LinkedInError
from app.linkedin.client import VoyagerClient
from app.linkedin.login import login
from app.linkedin.session import SessionProvider
from app.ratelimit import InboundLimiter, TokenBucket
from app.service import ProfileService


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    http = httpx.AsyncClient(timeout=30)

    async def login_fn(username: str, password: str):
        return await login(username, password, settings, http)

    provider = SessionProvider(settings, login_fn)
    app.state.session_provider = provider
    app.state.inbound_limiter = InboundLimiter(per_minute=settings.inbound_rate_per_minute)
    app.state.service = ProfileService(
        VoyagerClient(settings, provider, http),
        TTLCache(ttl_seconds=settings.cache_ttl_seconds),
        TokenBucket(rate_seconds=settings.outbound_rate_seconds, burst=3),
    )
    try:
        yield
    finally:
        await http.aclose()


def create_app() -> FastAPI:
    app = FastAPI(
        title="LinkedIn Profile API",
        version="0.1.0",
        description="Returns a LinkedIn profile as structured JSON.",
        lifespan=lifespan,
    )
    app.include_router(router)

    @app.exception_handler(LinkedInError)
    async def handle_linkedin_error(_: Request, exc: LinkedInError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.http_status,
            content={
                "error": {"code": exc.code, "message": str(exc), "hint": exc.hint}
            },
        )

    return app


app = create_app()
```

- [ ] **Step 8: Run the full suite**

Run: `pytest -v`
Expected: PASS, all tests including `tests/test_deps.py` from Task 12

- [ ] **Step 9: Commit**

```bash
git add app tests
git commit -m "feat: orchestrate profile fetching and expose the profile route"
```

---

### Task 14: Public fallback tier

**Files:**
- Create: `app/linkedin/public_fallback.py`
- Modify: `app/service.py`
- Test: `tests/test_public_fallback.py`

**Interfaces:**
- Consumes: `app.models.Profile`, `app.models.Position`, `app.models.Education`
- Produces: `app.linkedin.public_fallback.parse_public_profile(html: str) -> ProfileResponse`; `ProfileService.fetch` falls back to it when Voyager fails

- [ ] **Step 1: Write the failing test**

Create `tests/test_public_fallback.py`:

```python
import json

from app.linkedin.public_fallback import parse_public_profile

_LD = {
    "@graph": [
        {
            "@type": "Person",
            "name": "Ada Lovelace",
            "jobTitle": ["Mathematician"],
            "address": {"addressLocality": "London", "addressCountry": "GB"},
            "description": "About text",
            "image": {"contentUrl": "https://media.licdn.com/x.jpg"},
            "worksFor": [{"name": "Analytical Engines"}],
            "alumniOf": [{"name": "Cambridge"}],
        }
    ]
}


def _html(payload: dict) -> str:
    return f'<html><script type="application/ld+json">{json.dumps(payload)}</script></html>'


def test_parses_person_from_json_ld():
    response = parse_public_profile(_html(_LD))
    assert response.profile.full_name == "Ada Lovelace"
    assert response.profile.headline == "Mathematician"
    assert response.profile.about == "About text"
    assert response.profile.location.full == "London"
    assert response.experience[0].company.name == "Analytical Engines"
    assert response.education[0].school.name == "Cambridge"


def test_marks_the_data_source_as_public():
    assert parse_public_profile(_html(_LD)).meta.data_source == "public_jsonld"


def test_sections_absent_from_json_ld_are_unavailable():
    response = parse_public_profile(_html(_LD))
    assert response.meta.completeness["skills"] == "unavailable"


def test_html_without_json_ld_yields_an_empty_profile():
    response = parse_public_profile("<html><body>authwall</body></html>")
    assert response.profile.full_name is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_public_fallback.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.linkedin.public_fallback'`

- [ ] **Step 3: Create `app/linkedin/public_fallback.py`**

```python
"""Degraded fallback: parse the logged-out public profile page.

Still browserless - this is a plain HTTP GET plus HTML parsing. LinkedIn embeds a
schema.org Person object in a JSON-LD script tag on public profile pages. The data is
much thinner than Voyager's, but returning something beats returning an error.
"""

import json
from typing import Any

from selectolax.parser import HTMLParser

from app.models import (
    Company,
    Education,
    Image,
    Meta,
    Position,
    Profile,
    ProfileResponse,
    School,
    SectionWarning,
)

PUBLIC_SECTIONS = ("experience", "education", "skills", "certifications", "languages")


def _person(html: str) -> dict[str, Any] | None:
    tree = HTMLParser(html)
    for node in tree.css('script[type="application/ld+json"]'):
        try:
            payload = json.loads(node.text())
        except json.JSONDecodeError:
            continue
        for candidate in payload.get("@graph", []) if isinstance(payload, dict) else []:
            if isinstance(candidate, dict) and candidate.get("@type") == "Person":
                return candidate
        if isinstance(payload, dict) and payload.get("@type") == "Person":
            return payload
    return None


def _first(value: Any) -> str | None:
    if isinstance(value, list):
        return next((item for item in value if isinstance(item, str)), None)
    return value if isinstance(value, str) else None


def parse_public_profile(html: str) -> ProfileResponse:
    person = _person(html)
    if person is None:
        return ProfileResponse(
            meta=Meta(
                data_source="public_jsonld",
                completeness=dict.fromkeys(PUBLIC_SECTIONS, "unavailable"),  # type: ignore[arg-type]
                warnings=[SectionWarning(section="all", reason="no_json_ld_found")],
            )
        )

    address = person.get("address") if isinstance(person.get("address"), dict) else {}
    image = person.get("image") if isinstance(person.get("image"), dict) else {}

    profile = Profile(
        full_name=_first(person.get("name")),
        headline=_first(person.get("jobTitle")),
        about=_first(person.get("description")),
    )
    profile.location.full = address.get("addressLocality")
    profile.location.country = address.get("addressCountry")
    if isinstance(image.get("contentUrl"), str):
        profile.images.profile.append(Image(url=image["contentUrl"]))

    experience = [
        Position(company=Company(name=item.get("name")))
        for item in person.get("worksFor", [])
        if isinstance(item, dict) and item.get("name")
    ]
    education = [
        Education(school=School(name=item.get("name")))
        for item in person.get("alumniOf", [])
        if isinstance(item, dict) and item.get("name")
    ]

    completeness: dict[str, str] = dict.fromkeys(PUBLIC_SECTIONS, "unavailable")
    if experience:
        completeness["experience"] = "partial"
    if education:
        completeness["education"] = "partial"

    return ProfileResponse(
        meta=Meta(
            data_source="public_jsonld",
            completeness=completeness,  # type: ignore[arg-type]
            warnings=[
                SectionWarning(
                    section="all",
                    reason="degraded_source",
                    detail="Served from the logged-out public page; most fields unavailable",
                )
            ],
        ),
        profile=profile,
        experience=experience,
        education=education,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_public_fallback.py -v`
Expected: PASS, 4 passed

- [ ] **Step 5: Wire the fallback into `ProfileService.fetch`**

In `app/service.py`, add to the imports:

```python
import httpx

from app.errors import LinkedInError, ProfileNotFound
from app.linkedin.endpoints import legacy_profile_view
from app.linkedin.public_fallback import parse_public_profile
```

Change the `ProfileService.__init__` signature to accept the HTTP client, and add the two
fallback helpers:

```python
    def __init__(
        self, client: Fetcher, cache: TTLCache, bucket: TokenBucket,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self._client = client
        self._cache = cache
        self._bucket = bucket
        self._http = http

    async def _fetch_payload(self, slug: str) -> tuple[dict[str, Any], str]:
        """Try tier 1, then tier 3. Returns the payload and the tier that served it.

        Tier 2 (targeted supplementary calls for truncated collections) is not
        implemented; truncation is reported through meta.completeness instead.
        """
        path, params = dash_profile(slug)
        try:
            return await self._client.get_json(path, params, referer_slug=slug), "voyager_dash"
        except ProfileNotFound:
            raise
        except LinkedInError:
            await self._bucket.acquire()
            legacy_path, legacy_params = legacy_profile_view(slug)
            payload = await self._client.get_json(
                legacy_path, legacy_params, referer_slug=slug
            )
            return payload, "voyager_legacy"

    async def _public_fallback(self, slug: str, url: str) -> ProfileResponse:
        if self._http is None:
            raise UpstreamError("All Voyager tiers failed and no HTTP client is configured")
        page = await self._http.get(
            f"https://www.linkedin.com/in/{slug}",
            headers={"user-agent": "Mozilla/5.0 (compatible; profile-api/0.1)"},
            follow_redirects=True,
        )
        response = parse_public_profile(page.text)
        response.meta.requested_url = url
        response.meta.public_identifier = slug
        return response
```

In `fetch`, replace the direct call with the tier chain, and use the returned tier name
where `data_source` was previously hard-coded to `"voyager_dash"`:

```python
        try:
            payload, tier = await self._fetch_payload(slug)
        except ProfileNotFound:
            raise
        except LinkedInError:
            return await self._public_fallback(slug, url)
```

Then in the `Meta(...)` construction change `data_source="voyager_dash"` to
`data_source=tier`.

In `app/main.py`, pass the client through: `ProfileService(VoyagerClient(...), TTLCache(...), TokenBucket(...), http)`.

- [ ] **Step 6: Adapt the legacy payload shape**

The legacy `profileView` endpoint returns a different top-level shape: identity fields sit
under `profile`, and each section sits under its own `*View.elements` list. The element
shapes themselves are close enough to the dash shapes that the existing parsers handle
them, so only the top level needs remapping.

Add to `app/service.py`:

```python
# Legacy profileView nests each section under its own view object. The element shapes
# inside are close enough to the dash shapes that the same parsers apply.
_LEGACY_VIEWS = {
    "positionView": "profilePositionGroups",
    "educationView": "profileEducations",
    "skillView": "profileSkills",
    "certificationView": "profileCertifications",
    "languageView": "profileLanguages",
}


def _adapt_legacy(data: dict[str, Any]) -> dict[str, Any]:
    """Remap a legacy profileView payload onto the dash key names."""
    profile = data.get("profile")
    root: dict[str, Any] = dict(profile) if isinstance(profile, dict) else {}
    for view_key, dash_key in _LEGACY_VIEWS.items():
        view = data.get(view_key)
        if isinstance(view, dict) and isinstance(view.get("elements"), list):
            root[dash_key] = view["elements"]
    return root
```

In `fetch`, select the root according to the tier that served the payload:

```python
        result = normalize(payload)
        root = (
            _adapt_legacy(result.data)
            if tier == "voyager_legacy"
            else self._profile_root(result.data)
        )
```

- [ ] **Step 7: Test the legacy adapter**

Append to `tests/test_service.py`:

```python
def test_legacy_payload_is_remapped_onto_dash_keys():
    from app.service import _adapt_legacy

    legacy = {
        "profile": {"firstName": "Ada", "lastName": "Lovelace", "headline": "Mathematician"},
        "positionView": {"elements": [{"title": "Engineer", "companyName": "Tross"}]},
        "educationView": {"elements": [{"schoolName": "Cambridge"}]},
        "skillView": {"elements": [{"name": "Python"}]},
    }
    root = _adapt_legacy(legacy)
    assert root["firstName"] == "Ada"
    assert root["profilePositionGroups"][0]["title"] == "Engineer"
    assert root["profileEducations"][0]["schoolName"] == "Cambridge"
    assert root["profileSkills"][0]["name"] == "Python"
    # Sections absent from the legacy payload stay absent, so they score as unavailable.
    assert "profileLanguages" not in root


async def test_legacy_tier_is_used_when_dash_fails():
    from app.errors import BotDetected

    class TwoTierClient:
        def __init__(self) -> None:
            self.paths: list[str] = []

        async def get_json(self, path, params, referer_slug):
            self.paths.append(path)
            if path == "/identity/dash/profiles":
                raise BotDetected("999")
            return {
                "data": {"profile": {"firstName": "Ada", "lastName": "Lovelace"}},
                "included": [],
            }

    client = TwoTierClient()
    response = await _service(client).fetch("https://www.linkedin.com/in/ada")
    assert response.profile.full_name == "Ada Lovelace"
    assert response.meta.data_source == "voyager_legacy"
    assert len(client.paths) == 2
```

- [ ] **Step 8: Add a service test covering the public fallback**

Append to `tests/test_service.py`:

```python
async def test_falls_back_to_the_public_page_on_upstream_error(monkeypatch):
    import httpx

    from app.errors import BotDetected

    ld = (
        '<html><script type="application/ld+json">'
        '{"@graph":[{"@type":"Person","name":"Ada Lovelace"}]}'
        "</script></html>"
    )
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text=ld))
    async with httpx.AsyncClient(transport=transport) as http:
        async def no_sleep(_: float) -> None:
            return None

        service = ProfileService(
            StubClient(error=BotDetected("999")),
            TTLCache(ttl_seconds=60),
            TokenBucket(rate_seconds=1, burst=10, sleep=no_sleep, clock=lambda: 0.0),
            http,
        )
        response = await service.fetch("https://www.linkedin.com/in/ada")

    assert response.profile.full_name == "Ada Lovelace"
    assert response.meta.data_source == "public_jsonld"
```

- [ ] **Step 9: Run the full suite**

Run: `pytest -v`
Expected: PASS, all tests

- [ ] **Step 10: Commit**

```bash
git add app tests
git commit -m "feat: add legacy and public profile fallback tiers"
```

---

### Task 15: Fixture-backed integration test

Confirms the parsers work against real captured payloads rather than only hand-written ones.

**Files:**
- Test: `tests/test_fixtures_integration.py`

**Interfaces:**
- Consumes: `ProfileService`, fixtures from Task 7

- [ ] **Step 1: Write the test**

Create `tests/test_fixtures_integration.py`:

```python
import json
from pathlib import Path

import pytest

from app.cache import TTLCache
from app.ratelimit import TokenBucket
from app.service import ProfileService

FIXTURES = sorted(Path("tests/fixtures").glob("*.json"))


class FixtureClient:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    async def get_json(self, path, params, referer_slug):
        return self.payload


def _service(payload: dict) -> ProfileService:
    async def no_sleep(_: float) -> None:
        return None

    return ProfileService(
        FixtureClient(payload),
        TTLCache(ttl_seconds=60),
        TokenBucket(rate_seconds=1, burst=10, sleep=no_sleep, clock=lambda: 0.0),
    )


@pytest.mark.skipif(not FIXTURES, reason="run scripts/capture_fixtures.py first")
@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.stem)
async def test_fixture_produces_a_named_profile(path: Path):
    response = await _service(json.loads(path.read_text())).fetch(
        "https://www.linkedin.com/in/example"
    )
    assert response.profile.full_name
    assert response.meta.data_source == "voyager_dash"


@pytest.mark.skipif(not FIXTURES, reason="run scripts/capture_fixtures.py first")
@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.stem)
async def test_every_section_is_scored(path: Path):
    response = await _service(json.loads(path.read_text())).fetch(
        "https://www.linkedin.com/in/example"
    )
    expected = {"experience", "education", "skills", "certifications", "languages"}
    assert expected <= set(response.meta.completeness)


@pytest.mark.skipif(not FIXTURES, reason="run scripts/capture_fixtures.py first")
def test_the_dense_fixture_reports_truncation():
    dense = Path("tests/fixtures/dense.json")
    if not dense.exists():
        pytest.skip("no dense fixture captured")
    from app.linkedin.normalizer import normalize

    assert normalize(json.loads(dense.read_text())).truncations
```

- [ ] **Step 2: Run the test**

Run: `pytest tests/test_fixtures_integration.py -v`
Expected: PASS. If the parsers do not handle the real payload shape, these tests fail — fix the parsers, then rerun.

- [ ] **Step 3: Commit**

```bash
git add tests/test_fixtures_integration.py
git commit -m "test: verify parsers against captured LinkedIn fixtures"
```

---

### Task 16: Container, CI, and deployment

**Files:**
- Create: `Dockerfile`, `.dockerignore`, `.github/workflows/ci.yml`, `railway.toml`

**Interfaces:**
- Consumes: the completed application
- Produces: a deployed HTTPS URL

- [ ] **Step 1: Create `Dockerfile`**

```dockerfile
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

WORKDIR /srv

COPY pyproject.toml ./
RUN pip install --no-cache-dir .

COPY app ./app

# Run unprivileged. /data is the mount point for the session cache volume.
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /data \
    && chown appuser:appuser /data
USER appuser

EXPOSE 8000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
```

- [ ] **Step 2: Create `.dockerignore`**

```
.git
.github
tests
docs
scripts
.venv
__pycache__
*.pyc
.env
.env.*
data
*.pdf
CLAUDE.md
```

- [ ] **Step 3: Create `.github/workflows/ci.yml`**

```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install -e ".[dev]"
      - run: ruff check app tests
      - run: mypy app
      - run: pytest -v
```

- [ ] **Step 4: Create `railway.toml`**

```toml
[build]
builder = "dockerfile"

[deploy]
healthcheckPath = "/health"
healthcheckTimeout = 30
restartPolicyType = "on_failure"
```

- [ ] **Step 5: Verify the container builds and serves**

Run:
```bash
docker build -t linkedin-profile-api .
docker run --rm -p 8000:8000 -e API_KEYS=devkey linkedin-profile-api &
sleep 3 && curl -s localhost:8000/health
```
Expected: `{"status":"ok",...}`

- [ ] **Step 6: Deploy to Railway**

Create the project, connect the GitHub repository, add a volume mounted at `/data`, and set the environment variables from `.env.example` as service variables. Set `API_KEYS` to a freshly generated key.

- [ ] **Step 7: Verify the live deployment**

Run:
```bash
curl -s https://<your-app>.up.railway.app/health
curl -s -H "X-API-Key: <key>" \
  "https://<your-app>.up.railway.app/api/v1/profile?url=https://www.linkedin.com/in/<slug>"
```
Expected: health returns `status: ok`; the profile call returns populated JSON.

- [ ] **Step 8: Commit**

```bash
git add Dockerfile .dockerignore .github railway.toml
git commit -m "chore: add container, CI workflow, and Railway deployment config"
```

---

### Task 17: README

**Files:**
- Create: `README.md`

- [ ] **Step 1: Write `README.md`**

Include these sections:

1. **Overview** — one paragraph: what the API does and that it reverse engineers LinkedIn's internal Voyager API with no browser involved.
2. **Live demo** — the Railway URL, the demo API key, a copy-pasteable `curl`, and a link to `/docs` for interactive OpenAPI documentation.
3. **API documentation** — the `GET /api/v1/profile` contract, the `X-API-Key` header, a full example response, the status code table from the spec, and the error body shape.
4. **How it works** — the auth model (`li_at` is the credential, `JSESSIONID` is a double-submit CSRF token), the four fetch tiers, and the normalized URN graph with a worked before/after example. This is the section that demonstrates understanding; give it real detail.
5. **Setup** — clone, `pip install -e ".[dev]"`, copy `.env.example` to `.env`, how to obtain `li_at` and the `clientVersion`, `uvicorn app.main:app --reload`, and `pytest`.
6. **Configuration** — the environment variable table from the spec.
7. **Known limitations** — copy section 12 of the spec verbatim, including the Terms of Service note.

- [ ] **Step 2: Verify no secrets are present**

Run: `grep -iE 'li_at=[A-Za-z0-9]|AQED|ajax:[0-9]{10}' README.md`
Expected: no matches.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: add README with setup, API reference, approach, and limitations"
```

---

### Task 18: Pre-submission verification

- [ ] **Step 1: Confirm no AI attribution anywhere in history**

Run:
```bash
git log --format='%an <%ae>%n%cn <%ce>%n%B' \
  | grep -iE 'claude|anthropic|co-authored-by|generated with' \
  && echo "BLOCKED" || echo "clean"
```
Expected: `clean`

- [ ] **Step 2: Confirm no secrets are tracked**

Run:
```bash
git ls-files | xargs grep -lIE 'li_at=[A-Za-z0-9]{20}|AQED[A-Za-z0-9]{20}|ajax:[0-9]{15}' \
  && echo "BLOCKED" || echo "clean"
git ls-files | grep -E '^\.env$|^data/|\.pdf$|CLAUDE\.md' && echo "BLOCKED" || echo "clean"
```
Expected: `clean` for both

- [ ] **Step 3: Confirm the full suite passes**

Run: `ruff check app tests && mypy app && pytest -v`
Expected: all pass

- [ ] **Step 4: Confirm the live deployment answers**

Run the two `curl` commands from Task 16 Step 7 against the production URL.
Expected: HTTP 200 with populated JSON.

- [ ] **Step 5: Push and submit**

```bash
git push -u origin main
```

Then confirm the repository is public, and submit the repository URL and the live API URL at https://tally.so/r/KYK6qg.
