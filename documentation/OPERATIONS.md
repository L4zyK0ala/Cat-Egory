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
| Web UI (public) | `:3010` on `CATEGORY_PUBLIC_BIND` | no admin link; `/admin` answers 404. `CATEGORY_PUBLIC_API_URL` must be the API address browsers can reach; `CATEGORY_CORS_ORIGINS` must list the UI origin |
| API + Swagger (public) | `:8110` on `CATEGORY_PUBLIC_BIND` (`/api/docs`) | lookups, categories, feeds, community reports only: no `/api/v1/admin/*`, no `/metrics` |
| **Admin UI** | `:3012` on `CATEGORY_ADMIN_BIND` | admin console, review and moderation; talks to the admin API (`CATEGORY_ADMIN_API_URL`, `CATEGORY_ADMIN_CORS_ORIGINS`) |
| **Admin API** | `:8111` on `CATEGORY_ADMIN_BIND` | everything incl. `/api/v1/admin/*` and `/metrics` (Prometheus scrapes this one) |
| Grafana | `:3011` on `CATEGORY_ADMIN_BIND` | dashboard "Cat-egory overview" is provisioned |
| Prometheus | `:9091` | alert rules in `deployment/prometheus/alerts.yml` (no Alertmanager yet) |

**Public and admin are separate.** The admin pages and endpoints are not hidden behind a link: they do not
exist on the public containers (`CATEGORY_ADMIN_ROUTES=false` on `api`, no `UI_ADMIN` on `frontend`), so even a
leaked admin key cannot be used through the public address. Keep `CATEGORY_ADMIN_BIND` private (`127.0.0.1` with an
SSH tunnel, a LAN address, or the VPN address); when the site goes public only `CATEGORY_PUBLIC_BIND` (behind the
TLS proxy) faces the internet. Admin CLI commands are unaffected (`$DC exec api python -m backend.cli ...`).

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
| `category.discovery.queue` | 15 min | first scans of domains from discovery sources, under a daily budget |
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

### Discovery (popular-site lists)

Entries with `discover: true` in `integrations/sources.yaml` only bring domains in; syncing never scans. The
`category.discovery.queue` task then queues their first scans slowly, most popular first, at priority 8 (after
manual and scheduled work): at most `CATEGORY_DISCOVERY_BATCH_SIZE` (10) per run, `CATEGORY_DISCOVERY_DAILY_BUDGET`
(200) per UTC day, and nothing while `CATEGORY_DISCOVERY_MAX_QUEUED` (20) scans are waiting. One classification
uses ~7k EVREN tokens, so 200/day is ~1.4M tokens (~10 % of the account's daily quota). Large CSV feeds are
streamed (a 1M-row list needs ~15 MB), so run syncs in the `worker` container.

The first discovery sources are two views of the Majestic Million (CC BY 3.0, licence read on its download page
on 2026-11-08): `majestic-million-top` (first 20,000) and `majestic-million-tr` (all `.tr` names). After reading
the licence yourself:

```bash
for s in majestic-million-top majestic-million-tr; do
  $DC exec -T worker python -m integrations.cli register $s
  $DC exec -T worker python -m integrations.cli approve $s --license-slug CC-BY-3.0 \
      --license-name "Creative Commons Attribution 3.0 Unported" \
      --license-url https://creativecommons.org/licenses/by/3.0/ --redistribution yes --commercial yes \
      --attribution-required yes --reviewer you@example.org --notes "read majestic.com/reports/majestic-million"
  $DC exec -T worker python -m integrations.cli sync $s     # or wait for the hourly sync
done
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
`category_feed_age_seconds`, `category_db_metrics_up`, and from Redis
`category_task_last_success_timestamp_seconds{task}` (written by the worker after every successful Celery task)
with `category_heartbeats_up`. Alerts: API down, DB metrics down, scan backlog, stale feeds, > 20 % invalid AI
answers, API 5xx, **scheduler stopped** (no scan dispatch for 5 minutes: beat or the worker is down), scheduler
never ran, discovery stopped for an hour, heartbeats unreadable.

Celery beat stores its last-run times in the `beatdata` volume, so a restart or rebuild does not delay hourly
tasks (feeds, source syncs) by up to an hour.

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
