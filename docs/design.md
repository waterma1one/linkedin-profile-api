# LinkedIn Profile API — Design Specification

**Date:** 2026-08-28
**Status:** Approved
**Deadline:** 2026-08-31

## 1. Problem

Build a publicly hosted HTTPS API that accepts a LinkedIn profile URL and returns the
profile's information as structured JSON. The data must be obtained by making HTTP requests
directly to LinkedIn's own endpoints. Browser automation is prohibited.

### Required output fields

Name, headline, location, about, experience, education, skills, certifications, languages,
and profile images, where available.

### Deliverables

- API deployed publicly over HTTPS
- Public GitHub repository with complete source
- README covering setup, API documentation, approach, and known limitations
- No credentials or secrets committed to the repository

## 2. Approach

LinkedIn's web client is powered by an undocumented internal API served under
`/voyager/api/`. It authenticates with cookies rather than OAuth, which makes it reachable
from a plain HTTP client.

Alternatives were evaluated and rejected:

| Approach | Why rejected |
| --- | --- |
| Official LinkedIn API | Returns only the authenticated member's own profile. Third-party profile access is partner-gated. Not reverse engineering. |
| Public logged-out HTML | Authwalled from datacenter IPs; missing skills, certifications, endorsements. Retained as a degraded fallback only. |
| Guest endpoints | LinkedIn exposes guest APIs for jobs, but none for profiles. |
| Voyager GraphQL | Requires a `queryId` hash mined from LinkedIn's minified JS bundles, which rotates on their deploys. Same cookie requirements, strictly more fragile. |
| Third-party scraping APIs | Prohibited by the brief. |

Voyager REST is therefore the primary data source, with the public HTML path retained as a
last-resort fallback.

### Authentication model

Voyager requires two cookies:

- `li_at` — the session credential. This is the only real secret.
- `JSESSIONID` — a double-submit CSRF token. LinkedIn verifies that the `csrf-token`
  request header equals the `JSESSIONID` cookie value with surrounding quotes stripped.

## 3. Architecture

```
app/
  main.py                 FastAPI app, dependency wiring
  config.py               pydantic-settings; all configuration via env
  api/
    routes.py             GET /api/v1/profile, GET /health
    deps.py               API-key guard, rate-limit dependency
  linkedin/
    session.py            SessionProvider: resolve / validate / invalidate
    login.py              programmatic login via /uas/authenticate
    client.py             VoyagerClient: headers, retries, backoff, error mapping
    endpoints.py          URL and decorationId builders
    normalizer.py         included[] URN graph -> nested tree
    parsers/
      profile.py          identity, location, images
      experience.py
      education.py
      skills.py
      certifications.py
      languages.py
    public_profile.py     logged-out HTML + JSON-LD parsing (primary source)
  models.py               Pydantic response schema
  cache.py                TTL cache keyed by public_identifier
  ratelimit.py            outbound token bucket
tests/
  fixtures/               captured Voyager payloads, PII-scrubbed
  ...
```

Design constraint: parsers perform no I/O. Each takes a resolved dictionary and returns
typed models, making every parser testable offline against a fixture.

## 4. Session layer

`SessionProvider` resolves a session in priority order, first success wins:

1. Disk cache at `/data/session.json`, file mode `0600`
2. Environment cookies: `LI_AT` and `LI_JSESSIONID`
3. Programmatic login using `LI_USERNAME` and `LI_PASSWORD`

Validation probe is `GET /voyager/api/me`; HTTP 200 means the session is live.

On any 401/403 from a downstream call, the session is invalidated, re-resolved once, and
the call retried once. A second failure is surfaced to the caller.

### Login flow

```
1. GET  https://www.linkedin.com/uas/login
        Collect JSESSIONID, bcookie, bscookie from Set-Cookie.

2. POST https://www.linkedin.com/uas/authenticate
        form: session_key, session_password, JSESSIONID
        headers: pinned desktop UA, Origin and Referer of linkedin.com

3. Response JSON carries login_result:
        PASS         -> Set-Cookie contains li_at; persist session
        CHALLENGE    -> raise CheckpointRequired; surface on /health
        BAD_PASSWORD -> fail fast, no retry
```

CAPTCHA and checkpoint challenges cannot be solved without a browser. This is an accepted
limitation, documented in the README. The env-cookie path exists so that a checkpoint never
takes the deployed demo offline.

## 5. Voyager client

Required headers on every request:

```http
csrf-token: <JSESSIONID value, quotes stripped>
x-restli-protocol-version: 2.0.0
accept: application/vnd.linkedin.normalized+json+2.1
x-li-lang: en_US
x-li-track: {"clientVersion":"<observed>","osName":"web","osVersion":"unknown",
             "clientPlatform":"desktop_web","deviceFormFactor":"DESKTOP",
             "mpName":"voyager-web","mpVersion":"<observed>"}
user-agent: <pinned desktop Chrome UA>
referer: https://www.linkedin.com/in/<slug>/
host: www.linkedin.com
```

The `host` header is mandatory. Requests made outside a browser without it return HTTP 400.

The `accept` header requesting `normalized+json+2.1` is significant: it makes LinkedIn
return a flat, deduplicated object graph rather than deeply nested duplicated JSON.

The `clientVersion` and `mpVersion` values in `x-li-track` are marked `<observed>` because
they must be read from a live LinkedIn web session during fixture capture on day one, then
pinned as configuration. A stale or invented `clientVersion` is a known bot signal.

### Fetch tiers

| Tier | Source | Purpose |
| --- | --- | --- |
| 1 | `identity/dash/profiles?q=memberIdentity&memberIdentity={slug}&decorationId=com.linkedin.voyager.dash.deco.identity.profile.FullProfileWithEntities-93` | Single call returning most of the payload |
| 2 | Targeted dash calls keyed on the resolved `profileUrn` | Fill in sections returned truncated with paging stubs. **Stretch goal.** The supplementary endpoint paths must be observed from a live session during fixture capture, so this tier is not in the initial build. Truncation is still detected and reported through `meta.completeness` and `meta.warnings`, which keeps the response honest even when the remainder is not backfilled. |
| 3 | `identity/profileView/{slug}` | Legacy endpoint. Its top-level shape differs — identity fields sit under `profile` and each section under its own `*View.elements` list — so a small adapter remaps it onto the dash key names before the shared parsers run. |
| 4 | Logged-out public HTML with embedded JSON-LD | Session dead or checkpointed; degraded but still answering |

The tier that served the request is reported in `meta.data_source`.

## 6. Normalizer

The `normalized+json+2.1` response has the shape `{data, included[]}`. Entries in
`included[]` are identified by `entityUrn`, and references between them appear as
star-prefixed keys: `*fieldName` for a single reference, `**fieldName` for a collection.

The normalizer:

1. Builds an index of `{entityUrn: object}` from `included[]`
2. Recursively substitutes each `*` and `**` pointer with its target object
3. Guards against cycles — the graph is genuinely cyclic, since positions reference
   companies which reference positions
4. Caps recursion depth

The result is a nested tree, allowing all six parsers to use plain dictionary access.

LinkedIn truncates long collections and returns a paging stub. The normalizer detects these
and records them so Tier 2 can fetch the remainder and `meta.completeness` can report the
section as `partial`.

## 7. API contract

### `GET /api/v1/profile?url=<linkedin profile url>`

Requires header `X-API-Key`.

Accepted URL forms: standard `/in/<slug>`, locale-prefixed variants, `mwlite` mobile URLs,
legacy `/pub/` URLs, trailing slashes, arbitrary query strings, and percent-encoded Unicode
slugs. Non-LinkedIn hosts are rejected.

### Response schema

```jsonc
{
  "meta": {
    "requested_url": "...",
    "public_identifier": "...",
    "fetched_at": "2026-08-28T18:22:11Z",
    "data_source": "voyager_dash",
    "cache_hit": false,
    "duration_ms": 1840,
    "completeness": {
      "experience": "full",
      "education": "full",
      "skills": "partial",
      "certifications": "unavailable"
    },
    "warnings": [
      {"section": "skills", "reason": "paged_truncated", "detail": "20 of 47 returned"}
    ]
  },
  "profile": {
    "urn": "urn:li:fsd_profile:...",
    "public_identifier": "...",
    "first_name": "...", "last_name": "...", "full_name": "...",
    "headline": "...",
    "about": "...",
    "location": {"full": "...", "country": "...", "city": "..."},
    "industry": "...",
    "pronouns": null,
    "follower_count": 1240,
    "connection_count": 500,
    "connection_count_capped": true,
    "is_premium": false,
    "is_influencer": false,
    "is_open_to_work": false,
    "images": {
      "profile":    [{"url": "...", "width": 400,  "height": 400, "expires_at": "..."}],
      "background": [{"url": "...", "width": 1584, "height": 396, "expires_at": "..."}]
    }
  },
  "experience": [{
    "title": "...",
    "employment_type": "...",
    "company": {"name": "...", "urn": "...", "linkedin_url": "...", "logo": "..."},
    "location": "...",
    "description": "...",
    "start_date": {"year": 2024, "month": 3},
    "end_date": null,
    "is_current": true,
    "duration_months": 29,
    "group_id": "grp_1"
  }],
  "education":      [{"school": {}, "degree": "...", "field_of_study": "...",
                      "grade": "...", "activities": "...",
                      "start_date": {}, "end_date": {}}],
  "skills":         [{"name": "...", "endorsement_count": 32}],
  "certifications": [{"name": "...", "issuer": "...", "issue_date": {},
                      "expiration_date": null, "credential_id": "...",
                      "credential_url": "..."}],
  "languages":      [{"name": "...", "proficiency": "..."}],
  "honors": [], "publications": [], "projects": [], "volunteer": []
}
```

The final four sections — honors, publications, projects, volunteer — are best-effort. They
are populated when the payload contains them and returned as empty arrays otherwise. They
are not part of the required field set and do not affect `meta.completeness`.

### Schema rules

- Every field is nullable. LinkedIn omits, gates, or truncates arbitrary sections. An
  absent field is not an error.
- Completeness is reported per section rather than as a single score.
- Dates are `{year, month, day}` with all components optional. LinkedIn frequently supplies
  year only; synthesising a full date would invent data.
- Partial success returns HTTP 200 with entries in `meta.warnings`. A failure in one
  section must not discard successfully parsed sections.
- Image URLs are assembled from a `vectorImage` `rootUrl` plus per-resolution artifact path
  segments. These URLs are signed and expire, so `expires_at` is included.
- `connection_count_capped` is set because LinkedIn reports `500` for any member with 500 or
  more connections.

## 8. Error handling

### LinkedIn-side signals

| Signal | Meaning | Action |
| --- | --- | --- |
| `999` | Bot detection | Back off, mark session suspect, drop to next tier |
| `403` with CSRF message | `csrf-token` / `JSESSIONID` mismatch | Re-resolve session, retry once |
| Redirect to `/authwall` | Session dead | Invalidate, re-resolve, retry once |
| Redirect to `/checkpoint/challenge` | CAPTCHA gate | Cannot be solved headlessly; surface on `/health`, drop to next tier |
| `429` | Rate limited | Exponential backoff with jitter, then drop to next tier |
| `302` to the identical URL | Soft block. Observed on 2026-08-29 after roughly 15 Voyager
requests inside three minutes from a new account: every endpoint, including `/voyager/api/me`,
began self-redirecting with an empty body. Distinct from an authwall redirect, and not fixed by
any header or cookie change. | Treat as throttling, not a dead session. Do not re-resolve the
session and do not retry quickly; back off and drop to the next tier. |
| `404` | No such profile | Fail fast, no retry |

Retries are capped at 3 attempts with exponential backoff and jitter, and are attempted
only for 429, 5xx, and network errors. Deterministic failures (400, 404) are never retried,
as retrying them only consumes account reputation.

### Our HTTP status contract

| Code | Condition |
| --- | --- |
| 200 | Success, possibly partial — see `meta.warnings` |
| 400 | URL is not a parseable LinkedIn profile URL |
| 401 | Missing or invalid `X-API-Key` |
| 404 | Profile does not exist or is fully private |
| 429 | Local rate limiter tripped; `Retry-After` is set |
| 502 | LinkedIn unreachable after retries and all tiers exhausted |
| 503 | No usable session, e.g. checkpoint required; `/health` explains |

Error bodies are uniform:

```json
{"error": {"code": "...", "message": "...", "hint": "..."}}
```

### `GET /health`

Reports process liveness, which session resolution path is active, session validity, and
whether a checkpoint is currently blocking login. Does not require an API key. Never
returns secret values.

## 8b. Session binding — the central risk (2026-08-29)

The REST endpoint `identity/dash/profiles` is **retired**. Profile data now comes from
GraphQL:

```
GET /voyager/api/graphql
    ?includeWebMetadata=true
    &variables=(memberIdentity:<member URN id>)
    &queryId=voyagerIdentityDashProfiles.b5c27c04968c409fc0ed3546575b9b7a
```

Note `memberIdentity` is a member URN id (`ACoAA...`), not the vanity slug the REST
endpoint accepted. A slug-to-URN resolution step is therefore required.

More seriously, the captured session cannot be used from a scripted client:

| Observation | Result |
| --- | --- |
| `GET /voyager/api/me` from curl, first attempts | HTTP 200, valid JSON |
| Same request ~15 requests later | HTTP 302 redirecting to the identical URL, empty body |
| Same cookies in the browser, concurrently | HTTP 200 throughout |
| Full browser header set (sec-ch-ua, sec-fetch, x-li-track, x-li-page-instance) | HTTP 302 |
| Full non-HttpOnly cookie set (lidc, bcookie, liap, timezone, sdui_ver) | HTTP 302 |
| TLS impersonation: chrome, chrome120/131/136, safari18, edge101 | HTTP 302 |
| Public homepage from the same client, **no cookies** | HTTP 200, 141 KB |
| Public homepage from the same client, **with cookies** | Infinite redirect loop |

The last two rows are decisive. The client itself is not blocked — an unauthenticated
request succeeds from the same process, IP, and TLS fingerprint. Attaching this session's
cookies is what triggers the loop. LinkedIn has bound the session to the browser that
created it and rejects it elsewhere.

Consequences for the design:

- The four-tier fallback is no longer defensive polish; it is the feature that keeps the
  deployed service answering. Tier 4 (unauthenticated public HTML) is the only path
  currently proven to work from a scripted client.
- Deployment to a datacenter IP is strictly more suspicious than a residential one, so
  live Voyager access from Railway should be treated as best-effort, not assumed.
- Probing must obey the same throttle as production. The soft block was self-inflicted by
  roughly 15 requests in three minutes.

Two untested avenues remain, in priority order:

1. A session minted by our own client through `POST /uas/authenticate`. A session that was
   never issued to a browser may not carry the binding. This is already in the design and
   requires `LI_USERNAME` and `LI_PASSWORD`.
2. A freshly captured browser cookie used from the very first request under the
   30-second throttle, to test whether disciplined pacing avoids the classifier.

## 8a. Verified against a live session (2026-08-29)

Confirmed by direct probing before implementation began:

- Cookie auth works as designed. `GET /voyager/api/me` returned HTTP 200 with `li_at` plus
  `JSESSIONID`, and a `csrf-token` header set to the `JSESSIONID` value with quotes stripped.
- The `accept: application/vnd.linkedin.normalized+json+2.1` header does return the
  `{data, included}` shape the normalizer is built around.
- `x-li-track` and its `clientVersion` are **not required**. Requests succeed without them.
  The header is retained as optional configuration rather than a hard dependency.
- Adding the `lidc` routing cookie **broke** otherwise-working requests. Send only `li_at`
  and `JSESSIONID`; do not seed a full browser cookie jar.
- Rate limits are tighter than assumed. Roughly 15 requests in three minutes from a new
  account triggered the self-redirect soft block described above. This validates the
  1-request-per-30-seconds outbound default, which must apply to development and fixture
  capture too, not only to production traffic.

The profile endpoints themselves could not be confirmed before the soft block took effect.
Verifying `identity/dash/profiles` against a cooled-down session is a prerequisite for
Task 7 and may change the endpoint choice.

## 8c. Avenue 1 closed, tier 4 measured (2026-08-29)

Both open questions from section 8b were probed after the client layer was built.

**Avenue 1, a session minted by our own client, is closed.** `POST /uas/authenticate`
with the throwaway account's credentials returned `login_result: CHALLENGE` with a
challenge URL. A session that was never issued to a browser does not escape the
verification gate, it simply meets it earlier. Section 4 already records that challenges
cannot be solved without a browser, and browser automation is prohibited, so this path
cannot be reopened for this account. The login code itself behaved correctly and raised
`CheckpointRequired`, so it stays in the codebase for the case where a future account
logs in cleanly.

**Tier 4 works from a residential IP and is richer than assumed.** A logged-out request
for a public profile with no cookies at all returned HTTP 200 and 661 KB of HTML, with no
authwall and no redirect. The page carries one `application/ld+json` block whose `@graph`
includes a `Person` node.

Mapping that node onto the required output fields:

| Required field | Source in JSON-LD | Available |
| --- | --- | --- |
| name | `name` | yes |
| headline | `description`, truncated by LinkedIn with a trailing ellipsis | partial |
| location | `address.addressLocality`, `address.addressCountry` | yes |
| images | `image.contentUrl` | yes |
| follower count | `interactionStatistic.userInteractionCount` | yes |
| education | `alumniOf[]` name and url, plus `member.startDate` and `member.endDate` | yes |
| experience | `worksFor[]` name and url only | partial |
| about | not present | no |
| skills | absent | no |
| certifications | absent | no |
| languages | `knowsLanguage` key present but empty | no |

Three details make tier 4 weaker than a first reading of the payload suggests, and all
three were verified against the raw bytes on disk rather than inferred.

Masking is broader than job titles, and it was only caught by running the parser against
the real page rather than a synthetic fixture. `jobTitle` comes back as asterisks of the
right length, literally `"jobTitle":["********","*******","**********"]`. Company names in
`worksFor` are masked the same way for every employer except the current one, so the
sampled profile disclosed `Gates Foundation` and returned `************ ******` for the
other two. An entry that is masked has no name, no title and no dates, so it carries
nothing at all and the parser drops it and counts it in a warning rather than storing a
row of asterisks. In practice the public page yields one usable position, the current one.

`worksFor[].member` carries no dates. The `OrganizationRole` object is present but empty,
so employment start and end dates are unavailable. Education is the exception and does
carry real dates, 1973 and 1975 in the sampled profile.

`disambiguatingDescription` is a badge, not an about section. It held `Creator, Top Voice`,
which is profile chrome. There is no about text on the page at all, and `description`
holds a truncated headline rather than the about section.

The missing sections are absent from the whole document, not only from the JSON-LD. A
case-insensitive search of the full 661 KB for `skills`, `certification`, `licenses`,
`languages`, `volunteer`, `honors`, and `publications` returns zero matches, which
confirms the assessment in section 2.

Tier 4 therefore answers five of the required fields cleanly, two partially, and cannot
answer the rest. That is a weaker result than the earlier reading of this payload, and it
raises the value of avenue 2 accordingly.

One caveat on sampling. This was measured against a single profile belonging to an
Influencer and Creator account, which may be more public than a typical member. Field
availability should be re-measured against an ordinary profile before the parser is
treated as finished.

This is the outcome the response schema was built for. Every field is nullable, and
`meta.completeness` reports per section rather than as one score, so a tier 4 response
marks skills and certifications `unavailable` and stays truthful instead of failing.

Two questions remain open, and both are now on the critical path:

1. Whether a freshly captured browser cookie used under the 30 second throttle from the
   very first request avoids the classifier. This is avenue 2 from section 8b and is the
   only remaining route to skills, certifications, and languages.
2. Whether the tier 4 path survives from a datacenter IP. It was measured from a
   residential connection, and section 2 records that the logged-out path is authwalled
   from datacenters. Railway is a datacenter. This makes deployment a test of the primary
   data path rather than a final packaging step, so it should be brought forward.

## 8d. Avenue 2 succeeds, and section 8b was wrong about binding (2026-08-29)

A freshly captured browser cookie was tested from the scripted client. The result
overturns the central claim of section 8b.

| # | Request, 30 seconds apart | Result |
| --- | --- | --- |
| 1 | `GET /voyager/api/me` | HTTP 200, valid JSON, `{data, included}` |
| 2 | `GET /identity/dash/profiles?q=memberIdentity&...` | HTTP 302 to the identical URL, empty body |
| 3 | `GET /voyager/api/me`, repeat of request 1 | HTTP 302 to the identical URL, empty body |

The session is not bound to the browser. Request 1 proves a cookie captured in a browser
works from a plain HTTP client, returning exactly the normalized envelope the normalizer
was built for. Section 8b concluded that LinkedIn had bound the session to its originating
browser, but that conclusion was drawn entirely from requests made while the account was
already soft blocked. The infinite redirect loop recorded there was the soft block, not a
binding check. Only `li_at` and `JSESSIONID` were sent, per section 8a.

The dash endpoint is genuinely retired. Request 2 self-redirected on a demonstrably
healthy session, thirty seconds after request 1 succeeded. Section 8b was right about this
even though its reasoning was contaminated.

The throttle is far tighter than 1 request per 30 seconds. Request 3 repeated a call that
had worked sixty seconds earlier, at the documented sustained rate, and it failed. The
session survived three requests. Section 8a's estimate of roughly fifteen requests in three
minutes was measured on an older session and does not hold here.

The most likely reading is that request 2 poisoned the session rather than the rate alone
exhausting it. A retired endpoint carrying an obsolete `decorationId` is a request no real
browser ever makes, so it is a strong automation signal. If that reading is correct, a
session that touches only endpoints the web client actually uses may survive considerably
longer, and the practical ceiling is unknown rather than three.

That distinction decides whether Voyager can back the deployed service at all:

- If normal endpoints survive, a low traffic demo backed by the six hour cache and the
  outbound throttle is workable.
- If any authenticated session dies after a few requests, Voyager can produce fixtures and
  prove the technique, but the deployed service has to answer from the public tier.

One useful free result. The logged-out public page contains `urn:li:member:251749025`, so a
numeric member URN is recoverable with no authenticated request. It is not the `ACoAA` form
that section 8b records `memberIdentity` as requiring, so whether it can seed the GraphQL
call is still unverified.

## 9. Throttling and caching

- Outbound token bucket: 1 request per 30 seconds sustained, burst of 3, tunable via env.
  This respects the observed practical ceiling of 1–2 Voyager requests per minute per
  account.
- TTL cache keyed on `public_identifier`, 6 hour default. Repeat requests are served without
  contacting LinkedIn.
- Inbound rate limiting per API key, returning 429 with `Retry-After`. Implemented as a
  fixed 60-second window with a default quota of 20 requests, falling back to the client
  address when no API key is present. This exists so that one caller cannot monopolise the
  account's limited outbound budget.

## 10. Testing

```
tests/
  fixtures/            captured Voyager payloads, PII-scrubbed
  test_normalizer.py   cycles, missing URNs, deep nesting, paging stubs
  test_parsers/        one module per section, fixture-driven
  test_url_parser.py   all accepted URL forms plus rejection cases
  test_api.py          FastAPI TestClient with a mocked session
```

Outbound network access is blocked in CI via `respx`. A green build therefore proves the
parsing logic rather than LinkedIn's current availability.

### Fixture capture

Fixtures are produced by `scripts/capture_fixtures.py`, which performs a live authenticated
fetch and writes the raw response to `tests/fixtures/raw/` (git-ignored), then writes a
scrubbed copy to `tests/fixtures/`. Scrubbing replaces member URNs, tracking identifiers,
signed image URLs, and personal contact details with stable synthetic values, so committed
fixtures preserve structure without publishing anyone's data.

At least three profiles are captured: a sparse profile, a dense profile that triggers
collection truncation, and a profile with non-English content.

CI runs `ruff`, `mypy`, and `pytest` on GitHub Actions.

## 11. Deployment

- Base image `python:3.12-slim`, running as a non-root user under `uvicorn`
- Railway: persistent volume mounted at `/data`, healthcheck on `/health`, environment
  variables stored as platform secrets
- `.gitignore` excludes `.env`, `/data`, the challenge PDF, and unscrubbed fixtures
- `.env.example` documents every variable name with empty values
- Pre-push check greps staged content for `li_at`, `JSESSIONID`, and `ajax:` patterns

### Environment variables

| Variable | Purpose |
| --- | --- |
| `API_KEYS` | Comma-separated keys accepted on `X-API-Key` |
| `LI_AT` | LinkedIn session cookie (primary auth path) |
| `LI_JSESSIONID` | LinkedIn CSRF cookie |
| `LI_USERNAME` | Account email for programmatic login (fallback path) |
| `LI_PASSWORD` | Account password for programmatic login |
| `SESSION_PATH` | Session cache location, default `/data/session.json` |
| `CACHE_TTL_SECONDS` | Profile cache TTL, default 21600 |
| `OUTBOUND_RATE_SECONDS` | Sustained delay between Voyager calls, default 30 |

## 12. Known limitations

To be documented in the README:

- Voyager is an undocumented internal API and may change without notice
- Rate limits are strict; the backing account can be restricted
- Profile image URLs are signed and expire within weeks
- Some fields are visibility-gated; out-of-network members may resolve to
  `"LinkedIn Member"` with most fields withheld
- Non-English locale profiles may parse incompletely
- CAPTCHA and checkpoint challenges cannot be solved without a browser
- Use of the internal API is contrary to LinkedIn's Terms of Service; a dedicated
  throwaway account is used, never a primary profile

## 13. Schedule

| Day | Work |
| --- | --- |
| Fri 28 Aug | Scaffold, session layer, login flow, Voyager client, capture fixtures, normalizer |
| Sat 29 Aug | Six parsers, Pydantic models, routes, cache, limiter, API key, tests |
| Sun 30 Aug | Public fallback tier, Railway deployment, README |
| Mon 31 Aug | Buffer and submission |

Fixture capture is scheduled on day one because every later task depends on having real
payloads, and it is the only step that cannot be performed offline.
