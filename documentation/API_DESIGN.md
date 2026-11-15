# Public REST API — Design (v1)

Status legend: ✅ implemented · 🗓 planned (phase)

The OpenAPI document is generated from code at `/api/v1/openapi.json`; Swagger UI at `/api/docs`.

## Conventions

| Topic | Rule |
|---|---|
| Versioning | URL prefix `/api/v1`. Breaking changes → `/api/v2`; v1 kept for ≥ 12 months with a `Sunset` header. Taxonomy changes are versioned separately (`taxonomy_version` in every response). |
| Domain input | Normalised server-side (`common/domains.py`): `Bücher.DE.` → `xn--bcher-kva.de`. Invalid input → `422` with a machine-readable `code`. |
| Pagination | Keyset: `?limit=100&cursor=<opaque>`; response has `next_cursor` (null at end). Max `limit` 1000. No `OFFSET`. |
| Caching | `ETag` + `Cache-Control: public, max-age=…` on reads; `If-None-Match` → `304`. Redis read-through cache for hot domains. |
| Rate limits | Fixed 1-minute windows in Redis. Anonymous reads 60/min per client (keyed IP hash), anonymous writes 10/min, API keys 600/min (or the key's own limit). Headers: `RateLimit-Limit`, `RateLimit-Remaining`, `RateLimit-Reset`; `429` with `Retry-After`. If Redis is down requests are allowed and logged (`CATEGORY_RATE_LIMIT_FAIL_OPEN`). |
| Auth | Public reads need nothing. Admin endpoints: `Authorization: Bearer cat_<prefix>_<secret>` (header only, never query string). Effective scopes = key scopes ∩ the user's current role. Keys are created with `python -m backend.cli` (see ADMIN_GUIDE.md); password login arrives with the web UI (Phase 5). |
| Errors | `{"error": {"code": "invalid_domain", "message": "…", "request_id": "…"}}` |
| Licensing | Every data response includes `license` and `attribution`; third-party-derived items carry their own. |
| Security headers | `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, strict CSP; `X-Request-ID` echoed. |

## Endpoints

| Method & path | Status | Purpose |
|---|---|---|
| `GET /healthz`, `GET /readyz`, `GET /metrics` | ✅ | ops (not in OpenAPI) |
| `GET /api/v1/categories?kind=content\|risk` | ✅ | taxonomy (current version) |
| `GET /api/v1/categories/{id}` | ✅ | one category |
| `GET /api/v1/domain/{domain}` | ✅ P2 | current classification + risk (ETag / 304) |
| `GET /api/v1/url?url=…` | ✅ P5 | URL-level classification, falls back to domain; `url_risk` = threats confirmed for this exact link |
| `GET /api/v1/categories/{id}/domains?cursor=…` | ✅ P5 | domains in a category (paginated) |
| `GET /api/v1/history/{domain}` | ✅ P5 | category timeline |
| `GET /api/v1/stats` | ✅ P5 | counts for dashboard (`threats`: flagged domains, `threat_urls`: single reported pages) |
| Community reputation | ✅ | an account whose moderated submissions were accepted (≥ 10, ≥ 90 %, never spam) is trusted: its category corrections apply at once (`community.auto_accept`, no moderator). Never for threats, new domains, popular or official sites, or domains a reviewer decided; automatic acceptances do not count towards trust. Moderators see each submitter's record |
| `POST /api/v1/lookup` | ✅ | many names or URLs at once (100 without a key, 1000 with; each counts against a developer key's daily quota); same visibility as a single lookup, private imports only for staff; the `/lookup` page |
| `POST /api/v1/developer/keys`, `GET /api/v1/developer/key`, `POST /api/v1/developer/key/revoke` | ✅ | self-service read-only keys: no account or e-mail, shown once, 120/min and 20 000/day (`X-Quota-*` headers, 429 `daily_quota_exceeded`), 3 a day per client, 200 a day overall; the `/developers` page |
| `GET /api/v1/accuracy` | ✅ | accuracy measured against people: published AI answers (random verification sample, 95% Wilson interval, per category from 5 checks), hard cases apart, threat flags taken back and how fast, published domains by who decided (`classification/accuracy.py`) |
| `GET /api/v1/feeds` (index), `GET /api/v1/feeds/{category}.txt` | ✅ P5 | domain list |
| `GET /api/v1/feeds/{category}.hosts`, `{category}.rpz` | ✅ P5 | hosts format (`0.0.0.0 domain`) |
| `GET /api/v1/feeds/all.json`, `all.csv` (aliases `all-domains.*`) | ✅ P5 | full dump |
| `GET /api/v1/feeds/rpz` (all security threats) | ✅ P5 | DNS RPZ zone |
| `GET /api/v1/feeds/{category}.adblock` | ✅ | AdGuard Home / uBlock syntax (`||domain^`, covers subdomains) |
| `GET /api/v1/feeds/profile-security.{txt,hosts,rpz,adblock}`, `profile-family.*` | ✅ | ready-made router profiles: all threats; threats plus adult, gambling (licensed and illegal), drugs, violence, weapons, hate, proxies (`feeds/builder.PROFILES`); setup steps on `/integrations` |
| `GET /api/v1/feeds/{name}/diff?from=<version>` | ✅ | list feeds only (txt, hosts, rpz, adblock): `+domain` added and `-domain` removed since that version; `X-Feed-Version` = the latest |
| `GET /api/v1/changes?since=<time>&limit=` | ✅ | pull feed of changes, oldest first, at most 30 days back: `category` (published domain's main category; `old` null = newly categorised), `threat_added`, `threat_removed` (`reason` withdrawn/expired; same records as the threat feeds). Pass `until` as the next `since` and repeat while `more`; a page never ends inside one instant. Webhooks are not offered yet (outbound calls need the egress proxy and signing). The `/changes` page shows the last 7 days |
| `POST /api/v1/community/report` | ✅ P6 | false positive / negative, correction |
| `POST /api/v1/community/submit`, `GET /api/v1/community/submissions`, `POST …/{id}/vote` | ✅ P6 | propose a new domain |
| `POST /api/v1/admin/domains` | ✅ P2 | add domains (single or ≤ 10 000), queue scans |
| `POST /api/v1/admin/domains/import` | ✅ P2 | upload CSV / TSV / TXT / hosts / JSON / XLSX (≤ 5 MB, ≤ 10 000 rows) |
| `GET /api/v1/admin/me`, `GET /api/v1/admin/scopes` | ✅ P2 | key check, scope list |
| `POST /api/v1/admin/scans`, `GET /api/v1/admin/scans`, `GET /api/v1/admin/scans/stats` | ✅ P2 | queue a scan, list (keyset), counts |
| `POST /api/v1/admin/scans/{id}/cancel` | ✅ P2 | cancel a queued scan |
| retry a failed scan = `POST /api/v1/admin/scans` again | ✅ P3 | |
| `GET /api/v1/admin/providers`, `GET /api/v1/admin/calibration` | ✅ P4 | provider status, accuracy per band |
| `GET /api/v1/admin/reviews`, `POST /api/v1/admin/reviews/{id}/decision`, `GET /api/v1/admin/domains/{d}/screenshot` | ✅ P4 | human review |
| `GET /api/v1/admin/submissions`, `POST /api/v1/admin/submissions/{id}/moderate` | ✅ P6 | moderation |
| `GET /api/v1/admin/threats`, `POST /api/v1/admin/threats`, `POST /api/v1/admin/threats/withdraw` | ✅ | threat records by hand (domain or one URL, evidence required); see `THREATS.md` |

## Response shapes

### `GET /api/v1/domain/{domain}`

```json
{
  "domain": "example.com",
  "display_domain": "example.com",
  "categories": [
    {"id": "information-technology", "code": 105, "name": "Information Technology", "primary": true,
     "confidence": 0.96, "origin": "ai"},
    {"id": "artificial-intelligence", "code": 106, "name": "Artificial Intelligence", "primary": false,
     "confidence": 0.91, "origin": "ai"}
  ],
  "risk": {
    "malware": false, "phishing": false, "scam": false, "fraud": false,
    "command_and_control": false, "malicious_downloads": false, "suspicious": false, "lookalike_domain": false,
    "sources": [],
    "reported_urls": 0
  },
  "classification_status": "verified",
  "last_updated": "2026-11-07T10:00:00Z",
  "last_scanned": "2026-11-06T22:14:03Z",
  "data_source": "cat-egory-ai",
  "taxonomy_version": "1.0.0",
  "license": "CC-BY-4.0",
  "attribution": "Cat-egory contributors"
}
```

Notes

- `categories[].id` are taxonomy slugs, never free text. The prompt's example `"Technology"` maps to the
  slug `information-technology`.
- `risk` flags come **only** from `threat_intelligence` (verified sources) or from human-reviewed
  classifications; an unreviewed AI suspicion is not exposed as `true`. `risk.sources[]` lists
  `{type, source, first_seen}` for each true flag. Only domain-scoped records set flags; single reported pages
  on the host (e.g. a hacked site) only raise `reported_urls`, and their URLs are not published
  (`documentation/THREATS.md`).
- Unknown domain → `404` with `{"error": {"code": "not_found"}}` and a hint to submit it.
- Confidence is shown as given, with the documented caveat that it is not a calibrated accuracy figure.

### `GET /api/v1/history/{domain}`

```json
{"domain": "example.com",
 "history": [
   {"category": "blogs", "primary": true, "origin": "ai", "valid_from": "2026-01-01T00:00:00Z",
    "valid_to": "2026-01-31T00:00:00Z"},
   {"category": "news", "primary": true, "origin": "human", "valid_from": "2026-01-31T00:00:00Z",
    "valid_to": null}
 ],
 "next_cursor": null}
```

### `POST /api/v1/community/report`

```json
{"domain": "example.com", "type": "false_positive", "category": "gambling",
 "reason": "This is a sports news site, it does not offer betting.",
 "evidence_urls": ["https://example.com/about"]}
```

→ `202 Accepted` `{"submission_id": 123, "status": "pending"}`. Anonymous reports are allowed with
the strict anonymous write rate limit; the reporter's IP is stored only as a keyed hash. (A proof-of-work
or CAPTCHA step is not implemented.) Every moderation action is audit-logged.

## Feed format headers

Every feed starts with a comment header:

```
# Title: Cat-egory — gambling
# Version: 2026.11.07.1
# Last-Modified: 2026-11-07T10:00:00Z
# Taxonomy: 1.0.0
# License: CC-BY-4.0 (https://creativecommons.org/licenses/by/4.0/)
# Attribution: Cat-egory contributors
# Entries: 12345
```

RPZ uses `$TTL`/`SOA` with the version as serial and the same metadata as comments; CSV/JSON include
the metadata in a separate `meta` object / sidecar `.meta.json`. Feeds include only rows whose provenance
licence allows redistribution; third-party-derived feeds carry the source's licence instead.
