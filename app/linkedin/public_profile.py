"""Parse the logged-out public profile page.

Still browserless: a plain HTTP GET plus HTML parsing. LinkedIn embeds a schema.org
Person object in a JSON-LD script tag on public profile pages. This is the primary source
for the deployed service, because it needs no session and so cannot be rate limited out of
existence the way an authenticated Voyager session is. See docs/design.md sections 8c/8d.

The page withholds three of the required sections outright and masks position titles, so
this module's job is to take everything that is genuinely there and to be explicit in
meta.completeness about everything that is not.
"""

import json
import re
from typing import Any

from selectolax.parser import HTMLParser

from app.errors import InvalidProfileURL
from app.linkedin.urls import parse_profile_url
from app.models import (
    Company,
    Education,
    Image,
    Language,
    LinkedInDate,
    Meta,
    Position,
    Profile,
    ProfileResponse,
    School,
    SectionWarning,
)

SECTIONS = ("experience", "education", "skills", "certifications", "languages")

# The numeric member URN appears in the page markup, outside the JSON-LD block.
_MEMBER_URN = re.compile(r"urn:li:member:\d+")

_PUBLIC_NOTE = (
    "Served from the logged-out public page; "
    "skills and certifications are not exposed there"
)


def _person(html: str) -> dict[str, Any] | None:
    tree = HTMLParser(html)
    for node in tree.css('script[type="application/ld+json"]'):
        try:
            payload = json.loads(node.text())
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        graph = payload.get("@graph")
        for candidate in graph if isinstance(graph, list) else [payload]:
            if isinstance(candidate, dict) and candidate.get("@type") == "Person":
                return candidate
    return None


def _text(value: Any) -> str | None:
    if isinstance(value, list):
        value = next((item for item in value if isinstance(item, str)), None)
    return (value.strip() or None) if isinstance(value, str) else None


def _is_masked(value: Any) -> bool:
    """LinkedIn returns withheld strings as asterisks of the right length on this page.

    It applies to job titles and, past the current employer, to company names too.
    """
    return isinstance(value, str) and bool(value) and set(value.replace(" ", "")) == {"*"}


def _visible(value: Any) -> str | None:
    """Text that LinkedIn actually disclosed, or None when it returned a mask."""
    text = _text(value)
    return None if _is_masked(text) else text


def _year(value: Any) -> LinkedInDate | None:
    """LinkedIn supplies bare integer years here, never a full date."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return LinkedInDate(year=value)
    if isinstance(value, str) and value.isdigit():
        return LinkedInDate(year=int(value))
    return None


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _followers(person: dict[str, Any]) -> int | None:
    stat = person.get("interactionStatistic")
    for raw in stat if isinstance(stat, list) else [stat]:
        entry = _dict(raw)
        if str(entry.get("interactionType", "")).endswith("FollowAction"):
            count = entry.get("userInteractionCount")
            if isinstance(count, int) and not isinstance(count, bool):
                return count
    return None


def _empty(reason: str) -> ProfileResponse:
    return ProfileResponse(
        meta=Meta(
            data_source="public_jsonld",
            completeness=dict.fromkeys(SECTIONS, "unavailable"),
            warnings=[SectionWarning(section="all", reason=reason)],
        )
    )


def _identity(person: dict[str, Any], html: str) -> Profile:
    address = _dict(person.get("address"))
    image = _dict(person.get("image"))

    profile = Profile(
        full_name=_text(person.get("name")),
        # description holds the headline. It arrives truncated with a trailing ellipsis.
        headline=_text(person.get("description")),
        # There is no about text on this page. disambiguatingDescription is a badge
        # such as "Creator, Top Voice", so mapping it here would invent data.
        about=None,
        follower_count=_followers(person),
    )
    profile.location.full = _text(address.get("addressLocality"))
    profile.location.country = _text(address.get("addressCountry"))
    if isinstance(image.get("contentUrl"), str):
        profile.images.profile.append(Image(url=image["contentUrl"]))

    for key in ("url", "sameAs"):
        try:
            profile.public_identifier = parse_profile_url(str(person.get(key)))
            break
        except InvalidProfileURL:
            continue

    found_urn = _MEMBER_URN.search(html)
    if found_urn:
        profile.urn = found_urn.group(0)
    return profile


def _experience(person: dict[str, Any]) -> tuple[list[Position], int]:
    """Return the disclosed positions and how many were withheld behind a mask.

    LinkedIn discloses only the current employer to a logged-out viewer. Past entries come
    back with the company name masked, and titles are masked throughout, so a masked entry
    carries no name, no title and no dates. There is nothing to keep, so it is dropped and
    counted instead of being stored as a row of asterisks.
    """
    positions: list[Position] = []
    withheld = 0
    for raw in person.get("worksFor") or []:
        item = _dict(raw)
        name = _visible(item.get("name"))
        if not name:
            withheld += 1
            continue
        positions.append(
            Position(
                # Titles are masked here, so leave it null rather than store "****".
                title=None,
                company=Company(name=name, linkedin_url=_text(item.get("url"))),
            )
        )
    return positions, withheld


def _education(person: dict[str, Any]) -> tuple[list[Education], int]:
    schools: list[Education] = []
    withheld = 0
    for raw in person.get("alumniOf") or []:
        item = _dict(raw)
        name = _visible(item.get("name"))
        if not name:
            withheld += 1
            continue
        member = _dict(item.get("member"))
        schools.append(
            Education(
                school=School(name=name, linkedin_url=_text(item.get("url"))),
                start_date=_year(member.get("startDate")),
                end_date=_year(member.get("endDate")),
            )
        )
    return schools, withheld


def parse_public_profile(html: str) -> ProfileResponse:
    person = _person(html)
    if person is None:
        return _empty("no_json_ld_found")

    warnings: list[SectionWarning] = []
    profile = _identity(person, html)
    experience, hidden_jobs = _experience(person)
    education, hidden_schools = _education(person)

    if any(_is_masked(title) for title in person.get("jobTitle") or []):
        warnings.append(
            SectionWarning(
                section="experience",
                reason="titles_masked",
                detail="LinkedIn masks position titles on the logged-out page",
            )
        )
    for section, hidden in (("experience", hidden_jobs), ("education", hidden_schools)):
        if hidden:
            warnings.append(
                SectionWarning(
                    section=section,
                    reason="entries_masked",
                    detail=f"{hidden} entries withheld by LinkedIn and omitted",
                )
            )

    languages = [
        Language(name=name)
        for name in (
            _text(entry) or _text(_dict(entry).get("name"))
            for entry in person.get("knowsLanguage") or []
        )
        if name
    ]
    honors = [{"name": _text(a)} for a in person.get("awards") or [] if _text(a)]

    completeness: dict[str, str] = dict.fromkeys(SECTIONS, "unavailable")
    if experience:
        # Company names only. No titles and no dates, so never "full".
        completeness["experience"] = "partial"
    if education:
        completeness["education"] = "full" if any(e.start_date for e in education) else "partial"
    if languages:
        completeness["languages"] = "partial"

    warnings.append(
        SectionWarning(section="all", reason="public_source", detail=_PUBLIC_NOTE)
    )

    return ProfileResponse(
        meta=Meta(
            data_source="public_jsonld",
            completeness=completeness,  # type: ignore[arg-type]
            warnings=warnings,
        ),
        profile=profile,
        experience=experience,
        education=education,
        languages=languages,
        honors=honors,
    )
