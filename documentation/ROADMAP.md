# Development Roadmap

Each phase ends with working, tested and documented output. A phase is "done" only when its exit
criteria pass in CI.

## Phase 1 — Architecture ✅ (2026-11-07)

| Deliverable | Where |
|---|---|
| System architecture, security model, licensing model | `documentation/ARCHITECTURE.md` |
| ER diagram + DB integrity rules | `documentation/ER_DIAGRAM.md` |
| SQLAlchemy models (23 tables) + Alembic migration `0001` + idempotent seed | `database/` |
| API design (v1) + first endpoints (`/categories`, health, metrics) | `documentation/API_DESIGN.md`, `backend/` |
| Docker Compose: Postgres, Redis, S3 store, API, worker, beat, migrate, Prometheus, Grafana | `deployment/` |
| AI provider interface, EVREN adapter, mock provider, hardened prompt, output schema | `ai_providers/` |
| Versioned taxonomy (1.0.0), confidence policy, answer validation | `classification/` |
| Domain/URL normalisation | `common/domains.py` |
| Tests: 101 unit + 14 integration (real Postgres) | `tests/` |

Exit criteria met: migrations upgrade/downgrade cleanly and match models; all tests green; compose
file validates. First `docker compose up` on 2026-11-07: all 9 services started, migration + seed
succeeded, `/readyz` (DB + Redis) ok, `/api/v1/categories` served 73 categories, Prometheus scraping the
API, Grafana healthy.

## Phase 2 — Core platform ✅ (2026-11-07)

| Deliverable | Where |
|---|---|
| Users + roles (`admin`, `moderator`, `contributor`), Argon2id passwords, CLI | `backend/cli.py`, `backend/app/security.py` |
| API keys: `cat_<prefix>_<secret>`, SHA-256 at rest, scopes ∩ role, expiry, revocation, last-used | `backend/app/deps.py` |
| Redis rate limiting (per key / per hashed IP), uniform error envelope, request IDs | `backend/app/deps.py`, `backend/app/errors.py` |
| Domain add (JSON) and file import (CSV/TSV/TXT/hosts/JSON/XLSX), per-row errors, dedupe, provenance | `backend/app/services/` |
| Scan jobs in the DB (one open job per domain, enforced by a unique partial index), list/cancel/stats | migration `0002`, `backend/app/api/admin.py` |
| Audit log for every write (one summary row per bulk import, with file SHA-256) | `backend/app/audit.py` |
| `GET /api/v1/domain/{domain}` with ETag/304 | `backend/app/api/v1.py` |
| Tests: 165 total (123 unit, 42 integration) | `tests/` |

Exit criteria met: 10 000 domains imported from one file in ≈ 2.5 s (target < 1 min); duplicates merged;
every write audit-logged; auth, scope, revocation, expiry and rate-limit tests green.

Design note: scans are only *rows* in `scan_jobs` for now. Phase 3 adds a dispatcher that moves queued jobs
to the crawler queue (DB = source of truth, no lost or phantom messages). Password login is deferred to the
web UI (Phase 5), where it comes with sessions and CSRF protection.

## Phase 3 — Crawler ✅ (2026-11-07, sandbox verified)

| Deliverable | Where |
|---|---|
| SSRF guard: all IPv4/IPv6 special ranges, embedded IPv4 (mapped, NAT64, 6to4, Teredo), URL checks | `crawler/ssrf.py` |
| Egress proxy container: connect-time resolution + checks, IP pinning, port allow-list, byte/time caps, one request per plain-HTTP connection, signed refusals | `crawler/egress_proxy.py` |
| Crawler container on an internal network (no internet, no Postgres, no DB credentials) | `deployment/` |
| Playwright capture: redirect chain, status, headers, TLS details, title/meta/OG/lang, visible text, HTML, screenshot; downloads refused, QUIC/WebRTC-UDP off, loopback forced through proxy | `crawler/browser.py` |
| robots.txt per RFC 9309 (honest `CategoryBot` token) | `crawler/robots.py` |
| Pipeline: dispatch (DB → queue, capacity-limited) → DNS + SSRF pre-check → crawl → persist; retries; stale-job reaper | `backend/app/scan_tasks.py` |
| Artefacts in the S3 store (private, retention date) | `crawler/storage.py` |
| Tests: 262 total, incl. 6 real-Chromium end-to-end tests through the proxy | `tests/` |

Verified in tests: internal hosts, metadata IP, IP literals, `localhost` and DNS-rebinding answers are refused
for page loads, redirects and sub-resources; a site cannot fake a proxy refusal; robots.txt is obeyed.
Live check on the Docker stack (2026-11-07): `example.com`, `bücher.de` (→ `www.buecher.de`) and
`wikipedia.org` (→ `www.wikipedia.org`) crawled through the egress proxy; redirect chains, TLS details,
titles, text and screenshots stored. `crawler.selftest`: no direct internet from the crawler, proxy refuses
the metadata IP, allows public sites.

> **Security condition — closed 2026-11-07:** Chromium's own sandbox did not start in the container at first
> (Ubuntu `apparmor_restrict_unprivileged_userns=1` + Docker's default seccomp). The crawler now ships with a
> seccomp profile and an AppArmor profile that allow exactly the user-namespace calls Chromium needs, without
> granting any capability; `crawler.selftest` on the stack reports "chromium sandbox : OK". The no-sandbox mode
> remains only as an explicit opt-in (`docker-compose.nosandbox.yml`).

## Phase 4 — AI classification ✅ (2026-11-07; live with EVREN on the Docker stack since 2026-11-08)

- `classify` task after every successful crawl: stored scan → provider input (text, HTML, screenshot) →
  prediction + evidence (`classification/engine.py`, `backend/app/classify_tasks.py`).
- Decision rules: ≥ 0.90 applied; 0.70–0.89 needs a second model that agrees (`CATEGORY_EVREN_SECOND_MODEL`),
  otherwise review; 0.50–0.69 review; < 0.50 re-analyse. Injection signals, risk claims and model-requested
  review always go to a person. AI never overrides a human or community decision. History is kept.
- Review API + UI: approve / set / reject, confirm threats (→ `threat_intelligence`, source `cat-egory-review`),
  screenshot viewer, provider status, calibration report (accuracy per confidence band from reviews).
- Tests: 18 integration tests with a scripted provider covering every band and rule.
- Live on 2026-11-08 with `qwen3.8-flash-next`: wikipedia.org, example.com, bücher.de classified and published;
  timeout / in-flight retry behaviour of the gateway verified. Quality is measured on 36 known sites with
  `python -m classification.evaluation` (results pending).
- **Open:** a project-specific EVREN key and answers to the terms-of-use questions (`EVREN_INTEGRATION.md`).

## Phase 5 — Public database ✅ (2026-11-07; running on the Docker stack, first real feeds 2026-11-08)

- Public API: domain, URL (falls back to registrable domain), history, category listings (keyset), stats.
- Privacy rule: domains known only from a private import are never listed, counted by name or put in feeds,
  and answer 404 to anonymous lookups (`database/publication.py`).
- Feeds: TXT, hosts, RPZ per content and threat category, `all.csv`, `all.json`, `rpz` (= all threats); licence
  headers; versioned releases only when content changes; served with ETag (`feeds/builder.py`). RPZ files are
  validated with dnspython's zone parser in tests.
- Source integration engine with an explicit licence-approval step, SSRF-safe downloads, provenance,
  delisting; threat data from non-redistributable sources is never exposed (`integrations/`).
- Next.js UI (`frontend/`): dashboard, domain explorer, categories, feeds, suggest, admin console (add/import,
  queue, rescan/cancel, provider status), review queue with screenshots, moderation. Checked end to end in
  Chromium against a local API (desktop + 390 px mobile).

## Phase 6 — Community ✅ (2026-11-07)

- Reports (false positive/negative, correction, verification) with evidence URLs, anonymous or with a key;
  new-domain suggestions; votes; moderation queue ordered by score; accept/reject/spam; audit log.
- Abuse controls: write rate limit per hashed IP, one open report per reporter and domain, `disputed` only
  after `CATEGORY_DISPUTE_THRESHOLD` (3) distinct reporters, nothing public changes before a moderator acts.

## Phase 7 — Production 🟡 partly done (2026-11-07)

Done: HTTP security test suite (headers, error envelope, hostile input, key handling, CORS); `pip-audit` and
`npm audit` clean; DB-derived Prometheus metrics, Grafana dashboard and alert rules; backup/restore scripts
(dump/restore round trip tested); load measurements; `OPERATIONS.md`.

Done later: Chromium sandbox profiles (verified on the stack); adaptive re-scan scheduling (risk, change,
verification and reachability decide the interval; `category.scan.schedule` queues due domains).

Done later (2026-11-08): Certificate-transparency brand watch (certstream + ct-watch, look-alike matching,
forced human review), public/admin split, scheduler heartbeats and alerts, discovery under a daily budget.

Open: TLS reverse proxy and production deployment,
Alertmanager, off-host backups + restore drill on the real stack, horizontal scaling, ZAP/container scans.

## Acceptance criteria mapping

| # | Criterion | Phase |
|---|---|---|
| 1 | Compose brings all services up | 1 ✅ (2026-11-07) |
| 2 | Add a domain | 2 ✅ |
| 3–5 | Real browser visit, screenshot, HTML/text | 3 ✅ |
| 6 | Classification via EVREN or mock | 4 ✅ (EVREN live 2026-11-08) |
| 7 | Results in Postgres | 3–4 ✅ |
| 8 | Search in web UI | 5 ✅ |
| 9 | Category via public API | 5 ✅ |
| 10 | JSON/CSV/TXT/RPZ | 5 ✅ |
| 11 | Community report | 6 ✅ |
| 12 | Security + automated tests | ✅ 330+ tests, CI on GitHub Actions; sandbox verified |
| 13 | Pluggable providers & sources | ✅ |
