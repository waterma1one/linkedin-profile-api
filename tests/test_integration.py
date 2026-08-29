"""End to end test against markup captured from a real LinkedIn profile page.

The JSON-LD in tests/fixtures/public_profile.html was taken from a live logged-out fetch
and scrubbed of signed image URLs. It keeps the quirks that a hand-written fixture would
never reproduce: asterisk-masked job titles, masked company names on every employer except
the current one, one of them carrying a trailing space, and an alumniOf entry whose dates
are bare integer years.

A green run here means the parser handles what LinkedIn actually returns, not what we
imagine it returns.
"""

from pathlib import Path

import httpx
import respx
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.linkedin.public_profile import parse_public_profile
from app.main import create_app

FIXTURE = Path(__file__).parent / "fixtures" / "public_profile.html"
PROFILE_URL = "https://www.linkedin.com/in/williamhgates"


def _html() -> str:
    return FIXTURE.read_text()


def test_parses_the_real_captured_page():
    response = parse_public_profile(_html())
    profile = response.profile

    assert profile.full_name == "Bill Gates"
    assert profile.public_identifier == "williamhgates"
    assert profile.urn == "urn:li:member:251749025"
    assert profile.location.full == "Seattle, Washington, United States"
    assert profile.location.country == "US"
    assert profile.follower_count and profile.follower_count > 1_000_000
    assert profile.images.profile
    # The page has no about section at all; disambiguatingDescription is a badge.
    assert profile.about is None


def test_only_the_disclosed_employer_survives():
    response = parse_public_profile(_html())
    # Three worksFor entries in the payload, two of them masked.
    assert [position.company.name for position in response.experience] == ["Gates Foundation"]
    assert response.experience[0].title is None
    assert any(w.reason == "titles_masked" for w in response.meta.warnings)
    masked = [w for w in response.meta.warnings if w.reason == "entries_masked"]
    assert masked and "2" in (masked[0].detail or "")


def test_education_dates_survive_the_round_trip():
    education = parse_public_profile(_html()).education
    assert [e.school.name for e in education] == ["Harvard University"]
    assert education[0].start_date is not None
    assert education[0].start_date.year == 1973
    assert education[0].end_date is not None
    assert education[0].end_date.year == 1975


def test_completeness_reports_the_real_gaps():
    completeness = parse_public_profile(_html()).meta.completeness
    assert completeness["education"] == "full"
    assert completeness["experience"] == "partial"
    for section in ("skills", "certifications", "languages"):
        assert completeness[section] == "unavailable"


@respx.mock
def test_the_api_serves_the_real_page_end_to_end():
    respx.get(PROFILE_URL).mock(return_value=httpx.Response(200, text=_html()))
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None, api_keys=[])

    body = TestClient(app).get("/api/v1/profile", params={"url": PROFILE_URL}).json()

    assert body["profile"]["full_name"] == "Bill Gates"
    assert body["meta"]["data_source"] == "public_jsonld"
    assert body["meta"]["public_identifier"] == "williamhgates"
    assert body["education"][0]["start_date"]["year"] == 1973
    # Sections LinkedIn withholds serialise as empty lists, never as nulls or errors.
    assert body["skills"] == []
    assert body["certifications"] == []


def test_no_signed_image_urls_were_committed():
    """Signed media URLs expire and identify a real fetch, so they must not be in git."""
    assert "media.licdn.com/dms/image/v2" not in _html()
