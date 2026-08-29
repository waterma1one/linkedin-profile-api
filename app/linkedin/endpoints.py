"""Voyager endpoint paths and decoration identifiers."""

VOYAGER_BASE = "https://www.linkedin.com/voyager/api"

FULL_PROFILE_DECORATION = (
    "com.linkedin.voyager.dash.deco.identity.profile.FullProfileWithEntities-93"
)


# Observed on a live web session. LinkedIn rotates these on their deploys, so a 400 or an
# empty response here is the first thing to re-check. See docs/design.md section 8b.
PROFILE_QUERY_ID = "voyagerIdentityDashProfiles.b5c27c04968c409fc0ed3546575b9b7a"


def dash_profile(slug: str) -> tuple[str, dict[str, str]]:
    """Retired. Kept only so the retirement stays documented and testable.

    This self-redirects on a healthy session and is the prime suspect for poisoning a
    session outright, since no real browser issues it. Never call it from the service.
    See docs/design.md section 8d.
    """
    return "/identity/dash/profiles", {
        "q": "memberIdentity",
        "memberIdentity": slug,
        "decorationId": FULL_PROFILE_DECORATION,
    }


def graphql_profile(member_identity: str) -> str:
    """Return the full GraphQL profile URL for a member identity.

    Built as a literal string rather than through a params dict on purpose. LinkedIn's
    GraphQL layer expects the Rest.li argument syntax `(memberIdentity:xyz)` verbatim, and
    percent-encoding the parentheses or the colon makes it reject the request.

    ``member_identity`` may be a vanity slug or a member URN id; which forms are accepted
    is still being verified against a live session.
    """
    return (
        f"{VOYAGER_BASE}/graphql"
        f"?includeWebMetadata=true"
        f"&variables=(memberIdentity:{member_identity})"
        f"&queryId={PROFILE_QUERY_ID}"
    )


def legacy_profile_view(slug: str) -> tuple[str, dict[str, str]]:
    """Tier 3: the older endpoint, a differently shaped secondary source."""
    return f"/identity/profileView/{slug}", {}
