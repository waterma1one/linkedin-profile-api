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
