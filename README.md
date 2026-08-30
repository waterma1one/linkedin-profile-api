# LinkedIn Profile API

An HTTP API that takes a LinkedIn profile URL and returns the profile as structured JSON.

Everything here is reverse engineered. There is no browser anywhere in the stack, no
Playwright, no Selenium, no headless Chrome. It is plain HTTP requests and parsing.

```
GET /api/v1/profile?url=https://www.linkedin.com/in/some-person
```

## Live

<https://linkedin-profile-api-8f8m.onrender.com>

```bash
curl -H "X-API-Key: <key>" \
  "https://linkedin-profile-api-8f8m.onrender.com/api/v1/profile?url=https://www.linkedin.com/in/williamhgates"
```

`/health` needs no key. Interactive OpenAPI documentation is at `/docs`.

It is on a free tier, so the first request after an idle period wakes the container and
takes a few seconds. That first request is also a live fetch rather than a cache hit, so
it is the one most likely to meet LinkedIn's rate limiting; asking again usually succeeds.

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

It still cannot back a deployed service, for three reasons found by probing rather than by
assumption.

The endpoints that every public write up points at are retired. Both
`identity/dash/profiles` and `identity/profileView/{slug}` answer with a 302 to the
identical URL and an empty body. Worse, calling one kills the session outright: a
`/voyager/api/me` that succeeded thirty seconds earlier starts failing the same way
immediately afterwards. The session budget is not a request count, it is whether you touch
a retired endpoint.

Profile content now comes from GraphQL, and each section needs a `queryId` that LinkedIn
generates server side and rotates on deploys. The one documented value turned out to be a
resolver: it maps a vanity slug to a profile URN and returns 1334 bytes containing no
profile data. The content queryIds could not be recovered. They are absent from the logged
out page, absent from all nine JavaScript bundles the client loads, not published anywhere,
and did not appear in browser captures, which returned only preload traffic. The account
was being served a Server Driven UI build, which may mean this client never issues the
named profile queries that older write ups describe.

Recovering a dead session needs a new cookie, and that needs a human in a browser.
Programmatic login through `/uas/authenticate` answers with `CHALLENGE`, and solving a
CAPTCHA without a browser is not possible.

So the service runs on LinkedIn's logged out public profile page instead. That path needs
no session, so nothing can rate limit it out of existence mid demo. LinkedIn embeds a
schema.org `Person` object in a JSON-LD script tag on those pages, and
`app/linkedin/public_profile.py` parses it.

The Voyager client, session provider and normalizer are still in the tree and still under
test. They are the reverse engineering itself and they work, but the serving path does not
call them, because without the profile queryIds there is no content endpoint to call.

The full probing record, including the requests and what each one returned, is in
`docs/design.md` sections 8a through 8f.

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

## Deploying

`render.yaml` describes the service, so on Render it is New, then Blueprint, then point it
at this repository. It builds the Dockerfile, health checks `/health`, and picks up the
non-secret environment defaults from the blueprint. Set `API_KEYS` in the dashboard;
the blueprint marks it `sync: false` so it is never read from the repository.

The container listens on `$PORT` and runs as an unprivileged user. The free plan has no
persistent disk, so `SESSION_PATH` points at `/tmp`, which only matters for the optional
Voyager path.

## Configuration

Everything is read from the environment through `app/config.py`. No other module touches
`os.environ`. See `.env.example`.

| Variable | Default | Purpose |
| --- | --- | --- |
| `API_KEYS` | empty | Comma separated keys accepted on `X-API-Key`. When empty the API is open, which is fine locally and wrong in production. |
| `CACHE_TTL_SECONDS` | 21600 | How long a fetched profile is reused |
| `OUTBOUND_RATE_SECONDS` | 30 | Sustained delay between outbound calls |
| `INBOUND_RATE_PER_MINUTE` | 20 | Per caller request cap |
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
| 429 | Rate limited, either by the per-caller cap or the outbound throttle. `Retry-After` says when to return. |
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
- LinkedIn answers HTTP 999 when it decides it is talking to a bot. It is intermittent
  rather than a lasting block: the same URL was measured returning 999 and then 200 about
  ten seconds later from the same client. The service retries three times with a jittered
  backoff of roughly 2, 5 and 10 seconds before reporting `bot_detected`, so a request that
  meets one can take up to about twenty seconds. Some profiles are blocked more stubbornly
  than others. Sustained polling will be blocked regardless.
- The pinned desktop user agent matters. The same request with a default HTTP client user
  agent is answered with 999 immediately.
- The cache lives in the process, so a restart empties it. On a free hosting tier the
  service sleeps when idle, which means the first request after a cold start is always a
  live fetch and is the one most likely to meet a 999. Asking again usually succeeds, and a
  shared cache such as Redis would remove the problem entirely.
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
