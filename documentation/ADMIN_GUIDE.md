# Admin Guide

All commands assume the Docker stack (`deployment/docker-compose.yml`). Shorthand used below:

```bash
DC="docker compose -f deployment/docker-compose.yml --env-file .env"   # prefix with sudo if needed
API=http://127.0.0.1:8110                                              # or your CATEGORY_PUBLIC_BIND address
```

## Users and API keys

```bash
$DC exec api python -m backend.cli create-admin --email you@example.org --name "Your Name"
$DC exec api python -m backend.cli create-user --email mod@example.org --name Mod --role moderator
$DC exec api python -m backend.cli create-key --email mod@example.org --name import-script --scopes domains:write --expires-days 90
$DC exec api python -m backend.cli list-keys
$DC exec api python -m backend.cli revoke-key --prefix abcdefghij
```

The key (`cat_<prefix>_<secret>`) is printed **once**; only its hash is stored. Keep it in a password
manager or an environment variable, never in the repository. A key's effective permissions are its scopes
intersected with the user's current role, so demoting or deactivating a user takes effect immediately.

| Scope | Allows | Roles |
|---|---|---|
| `domains:write` | add / import domains | admin, moderator |
| `scans:read` | view the scan queue | admin |
| `scans:write` | queue / cancel scans | admin, moderator |
| `community:write` | community submissions (Phase 6) | all |
| `reviews:write` | human review decisions (Phase 4) | admin, moderator |

## Adding domains

> **Pasting keys:** some terminals wrap pasted text in invisible control sequences (bracketed paste) or add
> a `\r`. A header containing them is rejected by the HTTP server with `Invalid HTTP request received.`
> before the API sees it. Keep only the key itself:
> `CATEGORY_KEY=$(printf '%s' "$CATEGORY_KEY" | grep -oE 'cat_[a-z0-9]{10}_[A-Za-z0-9_-]{43}')` — a valid key
> is 58 characters long.

```bash
export CATEGORY_KEY='cat_...'      # your key
curl -s -X POST $API/api/v1/admin/domains -H "Authorization: Bearer $CATEGORY_KEY" \
     -H 'Content-Type: application/json' -d '{"domains": ["example.com", "bücher.de"], "scan": "new"}'
curl -s -X POST $API/api/v1/admin/domains/import -H "Authorization: Bearer $CATEGORY_KEY" \
     -F file=@domains.csv -F scan=new
```

- Formats: `.csv` `.tsv` (column named domain/url/host/site/website/"alan adı", else first column), `.txt`
  `.hosts` `.list` (one per line or hosts format), `.json` (list, `[{"domain":…}]` or `{"domains":[…]}`),
  `.xlsx` (first sheet). Limits: 5 MB, 10 000 entries per request.
- `scan`: `new` (default) queues scans for newly created domains, `all` for every valid entry, `none` for no scans.
- `source`: slug of an enabled data source (`manual`, `file-import`, ...). Private file imports are recorded
  with the `internal` licence and are not published as CC BY.
- The response lists invalid rows with their row number and a reason code.

## Scan queue

```bash
curl -s $API/api/v1/admin/scans/stats -H "Authorization: Bearer $CATEGORY_KEY"
curl -s "$API/api/v1/admin/scans?status=queued&limit=20" -H "Authorization: Bearer $CATEGORY_KEY"
curl -s -X POST $API/api/v1/admin/scans -H "Authorization: Bearer $CATEGORY_KEY" \
     -H 'Content-Type: application/json' -d '{"domain": "example.com", "priority": 1}'
curl -s -X POST $API/api/v1/admin/scans/<id>/cancel -H "Authorization: Bearer $CATEGORY_KEY"
```

The dispatcher (every 10 s) moves queued jobs to the crawler, at most `CATEGORY_MAX_IN_FLIGHT_SCANS` (default 4)
at a time. Job states: `queued` → `running` → `succeeded` / `blocked` (SSRF policy or robots.txt) / `failed`
(after 3 attempts for temporary errors). The result is visible in the database:

```bash
$DC exec postgres psql -U category -c "SELECT d.normalized_domain, j.status, j.error_code, r.http_status, r.page_title
  FROM scan_jobs j JOIN domains d ON d.id = j.domain_id LEFT JOIN scan_results r ON r.scan_job_id = j.id
  ORDER BY j.queued_at DESC LIMIT 10"
```

## Crawler checks

```bash
$DC exec crawler python -m crawler.selftest      # sandbox, isolation, proxy
$DC logs -f crawler egress-proxy                  # crawl results and proxy refusals
```

## Audit log

Every write (user/key creation, domain add/import, scan create/cancel) is stored in `audit_logs` with actor,
request ID and before/after data:

```bash
$DC exec postgres psql -U category -c "SELECT occurred_at, action, target_id, after FROM audit_logs ORDER BY id DESC LIMIT 10"
```

## Review queue (AI classifications)

Web UI: `/admin/reviews` (enter an admin or moderator key). API:

```bash
curl -s "$API/api/v1/admin/reviews?limit=10" -H "Authorization: Bearer $CATEGORY_KEY"
curl -s -X POST $API/api/v1/admin/reviews/<id>/decision -H "Authorization: Bearer $CATEGORY_KEY" \
     -H 'Content-Type: application/json' -d '{"action": "approve"}'          # accept the model's categories
#    {"action": "set", "primary": "news", "secondary": ["sports"], "risk": ["phishing"], "note": "..."}
#    {"action": "reject"}
curl -s $API/api/v1/admin/providers -H "Authorization: Bearer $CATEGORY_KEY"       # add ?probe=true for a live call
curl -s $API/api/v1/admin/calibration -H "Authorization: Bearer $CATEGORY_KEY"     # accuracy per confidence band
```

A decision makes the domain `verified`; confirmed threats become public risk flags and enter the threat feeds.

## Moderation (community)

Web UI: `/admin/moderation`. API: `GET /api/v1/admin/submissions`, `POST /api/v1/admin/submissions/<id>/moderate`
with `{"action": "accept" | "reject" | "spam", "primary": "...", "secondary": [...], "note": "..."}`. Accepting a
report of the *primary* category requires the new primary category.

## Feeds

Built hourly by `category.feeds.build`; build now with
`$DC exec worker celery -A backend.app.worker:celery_app call category.feeds.build`. Index: `GET /api/v1/feeds`.
