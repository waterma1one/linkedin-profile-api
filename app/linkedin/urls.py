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
