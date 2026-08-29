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
