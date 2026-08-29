# LinkedIn Profile API

An HTTP API that takes a LinkedIn profile URL and returns the profile as structured JSON.

Everything here is reverse engineered. There is no browser anywhere in the stack, no
Playwright, no Selenium, no headless Chrome. It is plain HTTP requests and parsing.

```
GET /api/v1/profile?url=https://www.linkedin.com/in/some-person
```

## Approach

LinkedIn's own web client is backed by an undocumented internal API under `/voyager/api/`.
It authenticates with cookies rather than OAuth, which makes it reachable from any HTTP
client. That was the intended data source and most of the work went into it.

Two cookies matter. `li_at` is the session credential. `JSESSIONID` is a double submit CSRF
token, and LinkedIn only checks that the `csrf-token` header equals the cookie value with
the surrounding quotes stripped. Asking for
`application/vnd.linkedin.normalized+json+2.1` makes LinkedIn return a flat deduplicated
object graph instead of deeply nested JSON, where objects live in `included[]` keyed by
`entityUrn` and reference each other through `*field` and `**field` pointers. Resolving
that graph back into a tree is what `app/linkedin/normalizer.py` does, cycles and all.

All of that works. A cookie captured in a browser returns HTTP 200 and valid JSON from a
plain scripted client.

It still cannot back a deployed service, for two reasons found by probing rather than by
assumption:

1. Sessions die fast. In testing, a fresh session served one request, then began answering
   every subsequent request with a 302 redirect to the identical URL and an empty body.
   That happened at one request per thirty seconds, which is slower than a human browsing.
2. Recovering from that needs a new cookie, and getting a new cookie needs a human in a
   browser. Programmatic login through `/uas/authenticate` returns `CHALLENGE`, and
   solving a CAPTCHA without a browser is not possible.

So the service runs on LinkedIn's logged out public profile page instead. That path needs
no session, so nothing can rate limit it out of existence mid demo. LinkedIn embeds a
schema.org `Person` object in a JSON-LD script tag on those pages, and
`app/linkedin/public_profile.py` parses it.

The Voyager client is still in the tree, still under test, and can be switched on with
`VOYAGER_ENABLED=true` when a fresh cookie is available. It is off by default.

The full probing record, including the requests and what each one returned, is in
`docs/design.md` sections 8a through 8d.

## What you actually get

The public page is thinner than Voyager and it withholds things deliberately.

| Field | Available | Notes |
| --- | --- | --- |
| name | yes | |
| headline | partial | LinkedIn truncates it with a trailing ellipsis |
| location | yes | city and country |
| profile image | yes | signed URL, expires |
| follower count | yes | |
| education | yes | school, LinkedIn URL, start and end year |
| experience | partial | current employer only, and no job title |
| about | no | not present on the page |
| skills | no | not present on the page |
| certifications | no | not present on the page |
| languages | no | key exists but is empty |

Two things are worth calling out because they surprised me.

Job titles are masked. LinkedIn returns them as asterisks of the matching length, so
`jobTitle` comes back looking like `["********","*******"]`. Company names are masked the
same way for every employer except the current one. A masked entry has no name, no title
and no dates, so the parser drops it and records the count in `meta.warnings` rather than
storing a row of asterisks.

Nothing is ever guessed. Every field is nullable, and `meta.completeness` reports each
section as `full`, `partial` or `unavailable` so a caller can tell the difference between
"this person has no certifications" and "we could not see their certifications".

## Running it

Needs Python 3.12.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env      # then set API_KEYS
uvicorn app.main:app --reload
```

Or with Docker:

```bash
docker build -t linkedin-profile-api .
docker run --rm -p 8000:8000 -e API_KEYS=devkey linkedin-profile-api
```

Then:

```bash
curl -H "X-API-Key: devkey" \
  "http://localhost:8000/api/v1/profile?url=https://www.linkedin.com/in/some-person"
```

## Configuration

Everything is read from the environment through `app/config.py`. No other module touches
`os.environ`. See `.env.example`.

| Variable | Default | Purpose |
| --- | --- | --- |
| `API_KEYS` | empty | Comma separated keys accepted on `X-API-Key`. When empty the API is open, which is fine locally and wrong in production. |
| `CACHE_TTL_SECONDS` | 21600 | How long a fetched profile is reused |
| `OUTBOUND_RATE_SECONDS` | 30 | Sustained delay between outbound calls |
| `INBOUND_RATE_PER_MINUTE` | 20 | Per caller request cap |
| `VOYAGER_ENABLED` | false | Opt in to the authenticated path |
| `LI_AT`, `LI_JSESSIONID` | empty | LinkedIn cookies, only used when Voyager is enabled |
| `LI_USERNAME`, `LI_PASSWORD` | empty | Programmatic login, which currently hits a CAPTCHA |

No credentials are committed. `.env` is git ignored and the Docker image excludes it.

## API

### `GET /health`

No API key needed. Reports liveness, which auth path is configured, and whether Voyager is
on. Never returns a credential value.

### `GET /api/v1/profile?url=<profile url>`

Needs `X-API-Key` when `API_KEYS` is set.

Accepts the URL forms people actually paste: `/in/<slug>`, regional subdomains like
`in.linkedin.com`, `mwlite` mobile links, legacy `/pub/` URLs, trailing slashes, extra
query strings, and percent encoded Unicode slugs. Non LinkedIn hosts are rejected.

Response shape:

```jsonc
{
  "meta": {
    "requested_url": "...",
    "public_identifier": "some-person",
    "fetched_at": "2026-08-29T13:57:00Z",
    "data_source": "public_jsonld",
    "cache_hit": false,
    "duration_ms": 1101,
    "completeness": {"experience": "partial", "education": "full", "skills": "unavailable"},
    "warnings": [{"section": "experience", "reason": "titles_masked", "detail": "..."}]
  },
  "profile": {"full_name": "...", "headline": "...", "location": {}, "images": {}},
  "experience": [], "education": [], "skills": [],
  "certifications": [], "languages": [],
  "honors": [], "publications": [], "projects": [], "volunteer": []
}
```

Errors all come back the same shape:

```json
{"error": {"code": "invalid_url", "message": "...", "hint": "..."}}
```

| Code | Meaning |
| --- | --- |
| 400 | Not a parseable LinkedIn profile URL, or a missing parameter |
| 401 | Missing or invalid `X-API-Key` |
| 404 | No such profile, or it is fully private |
| 429 | Inbound rate limit tripped, see `Retry-After` |
| 502 | LinkedIn unreachable, or it served an authwall |
| 503 | No usable session when Voyager is enabled |

A partial result is a 200 with entries in `meta.warnings`. One missing section never
discards the sections that did parse.

## Known limitations

- Voyager is undocumented and changes without notice. `identity/dash/profiles`, the
  endpoint most public write ups still reference, is retired and now self redirects.
- Authenticated sessions are short lived, and refreshing one needs a human with a browser.
- The public page masks job titles and all but the current employer, and omits skills,
  certifications, languages and the about section entirely.
- Profile image URLs are signed and expire.
- Out of network members can resolve to "LinkedIn Member" with most fields withheld.
- The public path has been verified from a residential IP. Datacenter IPs are treated more
  suspiciously by LinkedIn and may see an authwall.
- Non English locale profiles may parse incompletely.
- Using the internal API is contrary to LinkedIn's Terms of Service. A dedicated throwaway
  account was used throughout, never a primary profile.

## Development

```bash
pytest          # no test touches the network, respx blocks outbound HTTP
ruff check .
mypy app
```

`docs/design.md` is the design spec and carries the live findings from probing.
`docs/plan.md` is the task by task implementation plan.
