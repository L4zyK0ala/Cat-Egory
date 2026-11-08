# Cat-egory

[![CI](https://github.com/L4zyK0ala/Cat-Egory/actions/workflows/ci.yml/badge.svg)](https://github.com/L4zyK0ala/Cat-Egory/actions/workflows/ci.yml)
[![Code: Apache-2.0](https://img.shields.io/badge/code-Apache--2.0-blue.svg)](LICENSE)
[![Data: CC BY 4.0](https://img.shields.io/badge/data-CC%20BY%204.0-lightgrey.svg)](DATA_LICENSE.md)

**An open, AI-assisted categorisation of the world's websites.** Cat-egory visits websites in a sandboxed
real browser, classifies them against an open, versioned taxonomy with a language model, checks every answer
(confidence policy, second model, human review) and publishes the result as an open database, a REST API and
downloadable feeds (TXT, hosts, CSV, JSON, DNS RPZ).

Commercial web-categorisation databases are closed and paid. Cat-egory's aim is an open alternative whose
data anyone can use, check and correct, with a clear record of where every label came from.

> **Status: alpha (0.1).** Everything below runs on the Docker stack on one machine, with real crawling and
> real AI classification. It has not yet been run publicly or at scale; expect breaking changes.

## How it works

```
domains ──► scan queue ──► sandboxed Chromium ──► page text, HTML, screenshot
 (admin, import,            (behind an SSRF-                 │
  community, sources)        enforcing proxy)                ▼
                                              AI provider (EVREN) + validation
                                                             │
                     confidence ≥ 0.90 ─────────► published │ 0.70–0.89 ──► second model agrees?
                     injection / risk claim / < 0.70 ──► human review queue
                                                             ▼
                      open database ──► REST API, web UI, feeds (TXT / hosts / CSV / JSON / RPZ)
```

- **Taxonomy:** 58 content categories and 7 security-risk categories, versioned and never renumbered
  ([TAXONOMY.md](documentation/TAXONOMY.md)).
- **Trust rules:** the AI never overrides a human decision. Security-risk flags come only from people or from
  licensed threat feeds, never from a model's claim. Pages that try to instruct the model ("ignore previous
  instructions…") always go to a person.
- **Provenance:** every label records whether it came from the AI, a reviewer, the community or a third-party
  source. Third-party data keeps its own licence and is never relicensed.
- **Safety of the crawler:** Chromium runs with its own sandbox inside a container without capabilities, on a
  network with no route to the internet except an egress proxy that refuses private, loopback and metadata
  addresses at connect time.

## What works

| Area | State |
|---|---|
| Domain add / bulk import (CSV, TXT/hosts, JSON, XLSX), API keys, roles, rate limits, audit log | ✅ |
| Crawler: sandboxed Chromium, SSRF-enforcing egress proxy, robots.txt, screenshots / HTML / text | ✅ |
| AI classification with [EVREN](https://evren.ssyz.org.tr/), confidence policy, second opinion, human review, calibration | ✅ live since 2026-11-08 |
| Automatic re-scans (daily for threats, weekly for changed or unreachable sites, monthly otherwise) | ✅ |
| Public API: domain, URL, history, categories, stats; feeds TXT / hosts / RPZ / CSV / JSON | ✅ |
| Community: misclassification reports, category suggestions, votes, moderation | ✅ |
| Web UI (Next.js): search, category explorer, feeds, admin console, review and moderation queues | ✅ |
| Third-party sources with licence approval and provenance | ✅ engine; no source approved yet |
| Monitoring (Prometheus, Grafana, alert rules), backup / restore scripts | ✅ |
| Quality measurement on known sites (`python -m classification.evaluation`) | ✅ |
| Public instance, TLS deployment, alert delivery | not yet — see the [roadmap](documentation/ROADMAP.md) |

## Quick start (Docker)

Needs Docker with Compose on Linux. The crawler's AppArmor profile is required on hosts that restrict
unprivileged user namespaces (Ubuntu 23.10+).

```bash
./deployment/scripts/generate-env.sh      # creates .env with random secrets (mode 600)
sudo cp deployment/apparmor/cat-egory-crawler /etc/apparmor.d/
sudo apparmor_parser -r -W /etc/apparmor.d/cat-egory-crawler
docker compose -f deployment/docker-compose.yml --env-file .env up -d --build
docker compose -f deployment/docker-compose.yml --env-file .env exec api \
    python -m backend.cli create-admin --email you@example.org --name "You"   # prints an admin API key
```

Web UI `http://127.0.0.1:3010`, API docs `http://127.0.0.1:8110/api/docs`. Admin UI `http://127.0.0.1:3012` and
admin API `:8111` run as separate containers on the private admin address only; Grafana `:3011`.
Without an AI key the stack uses an offline keyword matcher (see below). Full guide:
[OPERATIONS.md](documentation/OPERATIONS.md).

```bash
curl http://127.0.0.1:8110/api/v1/domain/wikipedia.org
```

## AI providers

- `CATEGORY_AI_PROVIDER=evren` — the [EVREN](https://evren.ssyz.org.tr/) LLM gateway (OpenAI-compatible), with
  your own key. Measured behaviour, timeouts and open terms-of-use questions:
  [EVREN_INTEGRATION.md](documentation/EVREN_INTEGRATION.md).
- `CATEGORY_AI_PROVIDER=mock` (default) — an offline keyword matcher for development. It is **not** an AI; its
  confidence is capped at 0.60, so everything it answers goes to a person.

Another model can be added by subclassing `ai_providers.base.AIProvider` and registering it in
`ai_providers/registry.py`.

## Development

```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/ruff check .
.venv/bin/python -m pytest tests/unit                          # no services needed
CATEGORY_TEST_DATABASE_URL=postgresql+psycopg://user:pass@host:5432/empty_test_db \
  .venv/bin/python -m pytest                                   # + integration tests (WIPES that database)
cd frontend && npm ci && npm run typecheck
```

See [CONTRIBUTING.md](CONTRIBUTING.md). Security issues: [SECURITY.md](SECURITY.md) (please do not open a
public issue).

## Documentation

- [Architecture and security model](documentation/ARCHITECTURE.md)
- [ER diagram](documentation/ER_DIAGRAM.md) · [API design](documentation/API_DESIGN.md) ·
  [Taxonomy](documentation/TAXONOMY.md)
- [Admin guide](documentation/ADMIN_GUIDE.md) · [Operations](documentation/OPERATIONS.md) ·
  [Roadmap](documentation/ROADMAP.md)

## Repository layout

```
backend/         FastAPI app, Celery tasks, admin CLI
common/          settings, JSON logging, domain/URL normalisation
database/        SQLAlchemy models, Alembic migrations, seed
ai_providers/    provider interface, EVREN + mock adapters, prompt, output schema
classification/  taxonomy (YAML), validation, confidence policy, evaluation set
crawler/         Playwright crawler, SSRF guard, egress proxy
integrations/    third-party source engine + licence catalogue
feeds/           feed builders
frontend/        Next.js web UI
website/         project website (cat-egory.org, static, Cloudflare Pages)
deployment/      Dockerfiles, compose, seccomp/AppArmor, Prometheus, Grafana, scripts
documentation/   design documents
tests/           unit, integration and browser tests
```

## Licences

- Code: [Apache-2.0](LICENSE).
- Categorisations produced by Cat-egory and the taxonomy: CC BY 4.0 — proposed, pending legal review
  ([DATA_LICENSE.md](DATA_LICENSE.md)).
- Third-party data keeps its own licence and attribution and is never relicensed. Screenshots and HTML of
  third-party sites are never published.

## Türkçe özet

Cat-egory, dünyadaki web sitelerini açık ve herkesin kullanıp düzeltebileceği biçimde kategorize etmeyi
amaçlayan açık kaynak bir platformdur. Siteleri yalıtılmış gerçek bir tarayıcıyla ziyaret eder, yerli yapay zekâ
platformu EVREN'in modelleriyle sürümlü bir kategori ağacına göre sınıflandırır, her cevabı güven eşikleri,
ikinci model ve insan incelemesiyle denetler; sonuçları açık veritabanı, REST API ve indirilebilir listeler
(TXT, hosts, CSV, JSON, DNS RPZ) olarak yayımlar. Proje alfa aşamasındadır; tüm bileşenler tek makinede
Docker üzerinde, gerçek tarama ve gerçek yapay zekâ sınıflandırmasıyla çalışmaktadır.
