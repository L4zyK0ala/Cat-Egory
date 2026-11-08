# Operations

Running Cat-egory on one host with Docker Compose. Shorthand:

```bash
DC="docker compose -f deployment/docker-compose.yml --env-file .env"   # prefix with sudo if needed
```

## 1. First start

```bash
./deployment/scripts/generate-env.sh          # random secrets, mode 600
$DC up -d --build                              # 12 services; migrations + seed run in `migrate`
$DC ps -a                                      # migrate: Exited (0); others running/healthy
$DC exec api python -m backend.cli create-admin --email you@example.org --name "You"
$DC exec crawler python -m crawler.selftest    # sandbox, isolation, proxy (install the AppArmor profile first, see below)
```

Addresses (set `CATEGORY_PUBLIC_BIND` / `CATEGORY_ADMIN_BIND` in `.env`):

| Service | Default | Notes |
|---|---|---|
| Web UI | `:3010` | `CATEGORY_PUBLIC_API_URL` must be the API address browsers can reach; `CATEGORY_CORS_ORIGINS` must list the UI origin |
| API + Swagger | `:8110` (`/api/docs`) | |
| Grafana | `:3011` | dashboard "Cat-egory overview" is provisioned |
| Prometheus | `:9091` | alert rules in `deployment/prometheus/alerts.yml` (no Alertmanager yet) |

Optional: a service key for the UI so server-side renders are not limited like one anonymous visitor:

```bash
$DC exec -T api python -m backend.cli create-user --email ui@localhost.invalid --name "web ui" --role contributor
$DC exec -T api python -m backend.cli create-key --email ui@localhost.invalid --name web-ui --scopes community:write --rate-limit 6000
# put the printed key into .env as CATEGORY_FRONTEND_API_KEY=..., then: $DC up -d frontend
```

### Chromium sandbox (crawler) — required before the first start

The crawler runs Chromium **with its own sandbox** inside a container without capabilities. Two profiles make
that possible: `deployment/seccomp/crawler.json` (Docker's default seccomp profile + `clone`/`unshare`/`setns`/
`chroot`) and `deployment/apparmor/cat-egory-crawler` (`docker-default` + `userns`). The seccomp profile is applied
by Compose; the AppArmor profile must be installed on the host once:

```bash
sudo cp deployment/apparmor/cat-egory-crawler /etc/apparmor.d/              # loaded automatically at boot
sudo apparmor_parser -r -W /etc/apparmor.d/cat-egory-crawler                # load now
$DC exec crawler python -m crawler.selftest                                 # expect: chromium sandbox : OK
```

Verified on Ubuntu (kernel 7.0, `apparmor_restrict_unprivileged_userns=1`, Docker 29) on 2026-11-07. Without
the AppArmor profile the crawler container does not start (safe failure). Hosts that cannot load it can opt
into the weaker mode with `-f deployment/docker-compose.nosandbox.yml`; do not crawl untrusted input then.

## 2. Scheduled work (Celery beat)

| Task | Every | What |
|---|---|---|
| `category.scan.dispatch` | 10 s | moves queued scans to the crawler (max `CATEGORY_MAX_IN_FLIGHT_SCANS`) |
| `category.scan.reap` | 5 min | re-queues or fails scans stuck in `running` |
| `category.scan.schedule` | 5 min | queues re-scans for domains whose `next_scan` has passed (≤ 500 per run) |
| `category.feeds.build` | 1 h | rebuilds feeds; only changed files get a new version |
| `category.ingest.sync_due` | 1 h | syncs approved third-party sources whose interval elapsed |
| `category.maintenance.provider_health` | 5 min | configuration-level AI provider check (no paid call) |

Re-scan interval, chosen after every scan (settings `CATEGORY_SCAN_INTERVAL_*`):

| Situation | Next scan |
|---|---|
| active threat record | 1 day |
| low-confidence / invalid classification | 1 day (`CATEGORY_REANALYZE_DAYS`) |
| page title or final host changed since the previous scan | 7 days |
| unreachable (DNS / connection / robots unavailable) | 7 days |
| classified, unchanged | 30 days |
| blocked (robots.txt or SSRF policy) | 30 days |
| human-verified, unchanged | 60 days |

Classification runs right after every successful crawl (`category.classify.scan`). Domains crawled before the
classifier existed are classified on their next scan (Admin → queue → *Rescan*). Feeds can be rebuilt at once
from Admin → *Rebuild feeds now* (`POST /api/v1/admin/feeds/build`).

To hide domains that came only from one source (e.g. a test import), with an audit-log entry:

```bash
$DC exec api python -m backend.cli deactivate-source-domains --source file-import         # dry run
$DC exec api python -m backend.cli deactivate-source-domains --source file-import --yes   # do it
```

After switching from the offline mock to a real model (`CATEGORY_AI_PROVIDER=evren`), re-classify what the mock
answered. Its open review tasks are closed (so nobody approves a keyword guess) and the domains are re-scanned at
priority 3; human decisions are kept:

```bash
$DC exec api python -m backend.cli reclassify-provider --provider mock          # dry run
$DC exec api python -m backend.cli reclassify-provider --provider mock --yes    # do it
```

When a later classification is applied automatically (≥ 0.90, or two models agree), open review tasks that only
said "the model was unsure" (or came from the mock) are closed as `skipped` with a note. Risk claims,
prompt-injection findings and model-requested reviews stay open for a person.

### Registry allocation rules (.gov.tr, .edu.tr, …)

Some Turkish suffixes are only allocated to one kind of holder after a document check (TRABİS, "belgeli alan
adları"): `gov.tr`, `bel.tr`, `pol.tr`, `tsk.tr` → government; `edu.tr`, `k12.tr` → education; `av.tr` → legal;
`dr.tr` → health-information; `kep.tr` → email. When a scan of such a domain fails or is refused although the name
resolves (several public sites drop requests from any user agent containing "bot"), and nothing else has
classified it, the category is taken from this rule (origin `rule`, source `cat-egory-registry-rules`, no
confidence). A later AI, human or community classification replaces it. Rules: `classification/registry_rules.py`.
For domains scanned before the rules existed:

```bash
$DC exec api python -m backend.cli apply-registry-rules          # dry run
$DC exec api python -m backend.cli apply-registry-rules --yes
```

## 3. Third-party sources

Nothing is synced until a maintainer has read the licence and recorded it:

```bash
$DC exec api python -m integrations.cli list
$DC exec api python -m integrations.cli register urlhaus
$DC exec api python -m integrations.cli approve urlhaus --license-slug <SPDX> --license-name "<name>" \
    --license-url <url> --redistribution yes|no --commercial yes|no --attribution-required yes|no \
    --reviewer you@example.org --notes "read terms on YYYY-MM-DD"
$DC exec api python -m integrations.cli sync urlhaus
```

Sources with `--redistribution no` are used internally only: their threat flags are not shown by the API and
never enter feeds.

## 4. Backups

```bash
sudo ./deployment/scripts/backup.sh            # ./backups: postgres-*.dump, objects-*.tar.gz, SHA256SUMS-*; keeps 14
sudo ./deployment/scripts/restore.sh backups/postgres-<stamp>.dump   # destructive, asks for confirmation
```

Schedule `backup.sh` with a systemd timer or cron and copy `./backups` off the host. The dump/restore
round trip was tested against PostgreSQL 18; the object-store archive is a plain tarball of the SeaweedFS
volume (restore by extracting into an empty `cat-egory_objectdata` volume while `storage` is stopped).

## 5. Monitoring

`/metrics` on the API exports HTTP metrics and database-derived gauges (cached 30 s):
`category_domains{status}`, `category_scan_jobs{status}`, `category_open_reviews`,
`category_pending_submissions`, `category_active_threats`, `category_ai_predictions_24h{status}`,
`category_feed_age_seconds`, `category_db_metrics_up`. Alerts: API down, DB metrics down, scan backlog,
stale feeds, > 20 % invalid AI answers, API 5xx.

## 6. Upgrades

```bash
git pull
$DC up -d --build        # `migrate` applies new Alembic migrations before api/worker start
```

Roll back a migration with `$DC run --rm migrate alembic downgrade -1` (take a backup first).

## 7. Production checklist

- [ ] Put a TLS reverse proxy (Caddy, nginx, Traefik) in front of `:3010` and `:8110`; bind the containers to
      `127.0.0.1`; set `CATEGORY_PUBLIC_API_URL` and `CATEGORY_CORS_ORIGINS` to the HTTPS origins.
- [ ] Tighten the UI CSP `connect-src` (`frontend/next.config.mjs`) to the API origin.
- [x] **Chromium sandbox**: crawler AppArmor profile installed in `/etc/apparmor.d`, `crawler.selftest` reports OK.
- [ ] Host firewall: drop egress to private ranges from the Docker bridge networks (defence in depth).
- [ ] Off-host backups; restore drill once.
- [ ] Alertmanager (or Grafana alerting) wired to a channel you read.
- [ ] EVREN: terms questions answered (`EVREN_INTEGRATION.md`), live checklist done. (Project-specific key: done 2026-11-08.)
- [ ] Legal review of the data licence (CC BY 4.0 for own data) and of each enabled source.

## 8. Performance (measured 2026-11-07)

Local, single uvicorn worker, PostgreSQL 18, load generator on the same 4-core host, 20 concurrent clients:

| Endpoint | req/s | p50 | p95 |
|---|---|---|---|
| `/healthz` (no DB, framework baseline) | 339 | 46 ms | 130 ms |
| `/api/v1/domain/{domain}` | 142 | 137 ms | 196 ms |
| `/api/v1/url?url=…` | 130 | 149 ms | 211 ms |
| `/api/v1/categories/{id}/domains` | 172 | 113 ms | 161 ms |
| `/api/v1/stats` | 102 | 190 ms | 267 ms |

These are modest single-process numbers. Scale-out path: several API replicas behind the proxy, HTTP caching
(responses carry `ETag` / `Cache-Control`; a CDN can serve lookups and feeds), Redis read-through cache for hot
domains, and `/stats` precomputed by a beat task. Bulk import measured at ≈ 2.5 s per 10 000 domains.
