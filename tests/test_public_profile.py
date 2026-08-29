import json

from app.linkedin.public_profile import parse_public_profile

_PERSON = {
    "@type": "Person",
    "name": "Ada Lovelace",
    # LinkedIn masks titles on the logged-out page.
    "jobTitle": ["********", "*******"],
    "address": {"addressLocality": "London, England, United Kingdom", "addressCountry": "GB"},
    "description": "Mathematician. Writer of the first algorithm…",
    "disambiguatingDescription": "Creator, Top Voice",
    "image": {"contentUrl": "https://media.licdn.com/dms/image/x.jpg"},
    "interactionStatistic": {
        "interactionType": "https://schema.org/FollowAction",
        "userInteractionCount": 4210,
    },
    # member is present but carries no dates, exactly as observed.
    "worksFor": [
        {
            "name": "Analytical Engines",
            "url": "https://www.linkedin.com/company/analytical-engines",
            "member": {"@type": "OrganizationRole"},
        },
        # Past employers come back with the company name masked too, so this entry
        # carries no name, no title and no dates. Observed on a real profile.
        {"name": "************ ******", "member": {"@type": "OrganizationRole"}},
    ],
    "alumniOf": [
        {
            "name": "Cambridge",
            "url": "https://www.linkedin.com/school/cambridge/",
            "member": {"@type": "OrganizationRole", "startDate": 1833, "endDate": 1837},
        }
    ],
    "knowsLanguage": [{"name": "English"}],
    "awards": ["Order of the Analytical Engine"],
    "url": "https://www.linkedin.com/in/ada-lovelace",
}


def _html(person: dict, extra: str = "") -> str:
    payload = {"@graph": [{"@type": "WebPage"}, person]}
    return (
        f'<html>{extra}<script type="application/ld+json">'
        f"{json.dumps(payload)}</script></html>"
    )


def test_parses_identity_fields():
    profile = parse_public_profile(_html(_PERSON)).profile
    assert profile.full_name == "Ada Lovelace"
    assert profile.headline.startswith("Mathematician.")
    assert profile.location.full == "London, England, United Kingdom"
    assert profile.location.country == "GB"
    assert profile.images.profile[0].url.endswith("x.jpg")
    assert profile.follower_count == 4210


def test_public_identifier_comes_from_the_profile_url():
    assert parse_public_profile(_html(_PERSON)).profile.public_identifier == "ada-lovelace"


def test_member_urn_is_scraped_from_raw_html():
    html = _html(_PERSON, extra="<span>urn:li:member:251749025</span>")
    assert parse_public_profile(html).profile.urn == "urn:li:member:251749025"


def test_about_is_never_taken_from_the_badge_field():
    # disambiguatingDescription holds "Creator, Top Voice", which is chrome, not an about.
    assert parse_public_profile(_html(_PERSON)).profile.about is None


def test_masked_job_titles_are_dropped_not_stored():
    response = parse_public_profile(_html(_PERSON))
    assert response.experience[0].title is None
    assert response.experience[0].company.name == "Analytical Engines"
    assert any(w.reason == "titles_masked" for w in response.meta.warnings)


def test_entries_with_masked_company_names_are_dropped_not_stored():
    response = parse_public_profile(_html(_PERSON))
    # Only the disclosed employer survives; the masked one is not a row of asterisks.
    assert [e.company.name for e in response.experience] == ["Analytical Engines"]
    assert any(w.reason == "entries_masked" for w in response.meta.warnings)


def test_education_keeps_the_years_linkedin_supplies():
    education = parse_public_profile(_html(_PERSON)).education[0]
    assert education.school.name == "Cambridge"
    assert education.start_date.year == 1833
    assert education.end_date.year == 1837


def test_languages_and_honors_are_populated_when_present():
    response = parse_public_profile(_html(_PERSON))
    assert response.languages[0].name == "English"
    assert response.honors


def test_completeness_is_honest_about_each_section():
    completeness = parse_public_profile(_html(_PERSON)).meta.completeness
    assert completeness["education"] == "full"
    # No titles and no dates, so experience is partial rather than full.
    assert completeness["experience"] == "partial"
    assert completeness["skills"] == "unavailable"
    assert completeness["certifications"] == "unavailable"


def test_marks_the_data_source_as_public():
    assert parse_public_profile(_html(_PERSON)).meta.data_source == "public_jsonld"


def test_html_without_json_ld_yields_an_empty_profile():
    response = parse_public_profile("<html><body>authwall</body></html>")
    assert response.profile.full_name is None
    assert response.meta.completeness["experience"] == "unavailable"
