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

## Phase 3 — Crawler

- Separate crawler image (Playwright + Chromium, non-root, sandbox on, read-only FS, seccomp).
- SSRF guard library (IPv4/IPv6 ranges, encoded/embedded IPv4, redirect re-validation) + egress proxy
  doing connect-time IP checks, crawler network without access to the core network.
- robots.txt (cached per host), per-host politeness (Redis), timeouts, retries, size caps,
  downloads blocked.
- Output: DNS, HTTP status, redirect chain, final URL, headers, title/meta/OG, visible text, HTML,
  full-page screenshot → object store (private) + `CrawlResult`.
- Exit: SSRF test suite (≥ 40 cases incl. DNS-rebinding fixture) passes; crawls JS-rendered test site;
  crawler container cannot reach Postgres/Redis-admin/metadata IP.

## Phase 4 — AI classification

- `classify` task: CrawlResult → provider → validation → `ai_predictions` + `evidence`.
- Decision engine: confidence policy, conflict detection (vs. previous scans, other models, source
  lists), review task creation, temporal `domain_categories` update.
- Multi-model agreement for the 0.70–0.89 band (second EVREN model).
- Calibration tooling: gold set (human-labelled), reliability report per model.
- Live EVREN verification checklist (see `EVREN_INTEGRATION.md`) run and recorded.
- Exit: end-to-end mock pipeline green in CI; EVREN live smoke test documented; injection test corpus
  never changes the category.

## Phase 5 — Public database

- Public endpoints: domain, url, history, category listing, stats (cache + ETag + keyset pagination).
- Feed builder: TXT, hosts, CSV, JSON, RPZ, per category, with licence headers; `feed_releases`.
- Source integration engine: connectors for licence-verified sources (`integrations/sources.yaml`).
- Frontend (Next.js): dashboard, domain explorer, history view.
- Exit: acceptance criteria 8–10.

## Phase 6 — Community

- Submissions, reports, votes, moderation queue, reputation, anti-abuse (rate limits, PoW, spam status).
- Exit: acceptance criterion 11; moderation fully audit-logged.

## Phase 7 — Production

- Security testing (ZAP baseline, dependency audit, container scan, SSRF/injection regression suites).
- Load tests (k6/Locust) for public API and feeds; DB partitioning plan activated if needed.
- Backups (pg_dump/WAL archiving + object-store lifecycle), restore drill.
- Grafana dashboards + alerts; runbooks; deployment guide (reverse proxy, TLS, secrets).

## Acceptance criteria mapping

| # | Criterion | Phase |
|---|---|---|
| 1 | Compose brings all services up | 1 ✅ (2026-11-07) |
| 2 | Add a domain | 2 ✅ |
| 3–5 | Real browser visit, screenshot, HTML/text | 3 |
| 6 | Classification via EVREN or mock | 1 (adapter + tests) → 4 (pipeline) |
| 7 | Results in Postgres | 1 (schema) → 4 |
| 8 | Search in web UI | 5 |
| 9 | Category via public API | 5 (categories ✅ in 1) |
| 10 | JSON/CSV/TXT/RPZ | 5 |
| 11 | Community report | 6 |
| 12 | Security + automated tests | every phase, 7 |
| 13 | Pluggable providers & sources | 1 (provider registry) → 5 (sources) |
