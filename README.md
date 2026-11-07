# Cat-egory

**Open Web Intelligence & Categorization Platform** — discovers websites, visits them in a sandboxed real
browser, classifies them with an AI provider against an open, versioned taxonomy, validates the result and
publishes it as an open database, REST API and downloadable feeds (TXT, hosts, CSV, JSON, DNS RPZ).

> **Status (2026-11-07):** Phases 1–3 run on the Docker stack (crawling verified with real sites). Phases 4–6
> (AI classification pipeline, public database + feeds + web UI, community) are implemented and tested
> locally (320+ tests, UI checked in Chromium) but not yet run on the Docker stack. Phase 7 is partly done.
> Open items: live EVREN key, production deployment — see the
> [roadmap](documentation/ROADMAP.md).

## What works

| Area | State |
|---|---|
| Domain add / bulk import (CSV, TXT/hosts, JSON, XLSX), API keys, roles, rate limits, audit log | ✅ |
| Crawler: sandboxed Chromium behind an SSRF-enforcing egress proxy, robots.txt, screenshots/HTML/text | ✅ |
| AI classification: EVREN adapter + mock, confidence policy, second opinion, human review, calibration | ✅ (EVREN not live-tested) |
| Public API: domain, URL, history, categories, stats; feeds TXT/hosts/RPZ/CSV/JSON | ✅ |
| Community: reports, suggestions, votes, moderation | ✅ |
| Web UI (Next.js): dashboard, explorer, feeds, admin console, review & moderation | ✅ (local) |
| Third-party sources with licence approval and provenance | ✅ (no source approved yet) |
| Monitoring: Prometheus metrics, Grafana dashboard, alert rules; backup/restore scripts | ✅ |

## Quick start (Docker)

```bash
./deployment/scripts/generate-env.sh     # creates .env with random secrets (mode 600)
sudo cp deployment/apparmor/cat-egory-crawler /etc/apparmor.d/ && sudo apparmor_parser -r -W /etc/apparmor.d/cat-egory-crawler
docker compose -f deployment/docker-compose.yml --env-file .env up -d --build   # or: make up
curl http://127.0.0.1:8110/api/v1/categories
```

Web UI: `http://127.0.0.1:3010`. API docs: `http://127.0.0.1:8110/api/docs`. Grafana: `127.0.0.1:3011`.
Operations guide: [OPERATIONS.md](documentation/OPERATIONS.md). Set `CATEGORY_PUBLIC_BIND` /
`CATEGORY_ADMIN_BIND` in `.env` to a LAN address to reach them from another machine.

## Development

```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/python -m pytest tests/unit                       # no services needed
CATEGORY_TEST_DATABASE_URL=postgresql+psycopg://user@host:5432/empty_test_db \
  .venv/bin/python -m pytest                                # + integration tests (wipes that DB)
.venv/bin/ruff check .
```

## AI providers

`CATEGORY_AI_PROVIDER=mock` (default) uses an offline keyword matcher. It is **not** an AI; its confidence
is capped at 0.60 so it can never auto-approve anything. `CATEGORY_AI_PROVIDER=evren` uses the EVREN LLM
gateway with a key issued for this project. What is and isn't verified about EVREN, and open terms-of-use
questions: [EVREN_INTEGRATION.md](documentation/EVREN_INTEGRATION.md). New providers: subclass
`ai_providers.base.AIProvider` and register a factory in `ai_providers/registry.py`.

## Documentation

- [Architecture & security model](documentation/ARCHITECTURE.md)
- [ER diagram](documentation/ER_DIAGRAM.md)
- [API design](documentation/API_DESIGN.md)
- [Taxonomy](documentation/TAXONOMY.md)
- [Admin guide](documentation/ADMIN_GUIDE.md)
- [Operations](documentation/OPERATIONS.md)
- [Roadmap](documentation/ROADMAP.md)

## Repository layout

```
backend/         FastAPI app, Celery app
common/          settings, JSON logging, domain/URL normalisation
database/        SQLAlchemy models, Alembic migrations, seed
ai_providers/    provider interface, EVREN + mock adapters, prompt, output schema
classification/  taxonomy (YAML), validation, confidence policy
crawler/         Playwright crawler, SSRF guard, egress proxy
integrations/    third-party source engine + licence catalogue
feeds/           feed builders
frontend/        Next.js web UI
deployment/      Dockerfiles, compose, Prometheus, Grafana
documentation/   design documents
tests/           unit + integration tests
```

## Licences

- Code: [Apache-2.0](LICENSE)
- Classification data produced by Cat-egory: CC BY 4.0 (see [DATA_LICENSE.md](DATA_LICENSE.md))
- Third-party data keeps its own licence and attribution and is never relicensed.

## Türkçe özet

Cat-egory, web sitelerini gerçek bir tarayıcıyla ziyaret edip yapay zekâ (EVREN) ile açık ve versiyonlu bir
kategori ağacına göre sınıflandıran, sonuçları açık veritabanı, REST API ve indirilebilir listeler olarak
yayımlayan açık kaynak bir platformdur. Faz 1–3 Docker üzerinde çalışıyor; Faz 4–6 yerelde test edildi.
