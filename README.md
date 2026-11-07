# Cat-egory

**Open Web Intelligence & Categorization Platform** — discovers websites, visits them in a sandboxed real
browser, classifies them with an AI provider against an open, versioned taxonomy, validates the result and
publishes it as an open database, REST API and downloadable feeds (TXT, hosts, CSV, JSON, DNS RPZ).

> **Status: Phase 2 (core platform) complete.** API keys and roles, rate limiting, domain add/import, the scan
> queue, audit log and domain lookup work and are tested. Crawling, AI classification in the pipeline, feeds,
> frontend and community features are next — see the [roadmap](documentation/ROADMAP.md).

## What works today

| Area | State |
|---|---|
| PostgreSQL schema (23 tables, temporal category history, provenance, licences) + Alembic migration | ✅ |
| Taxonomy 1.0.0: 58 content + 7 security-threat categories, stable IDs and codes | ✅ |
| Domain/URL normalisation (IDNA, Public Suffix List, special-use names rejected) | ✅ |
| AI provider interface; EVREN adapter (OpenAI-compatible gateway); offline mock provider | ✅ (EVREN not live-tested by this project) |
| Prompt-injection-hardened prompt, strict JSON + taxonomy validation, confidence policy | ✅ |
| `GET /api/v1/categories`, `GET /api/v1/domain/{domain}`, health, Prometheus metrics, OpenAPI | ✅ |
| Admin API: API keys + roles + scopes, rate limits, domain add / file import (≤ 10k rows), scan queue, audit log | ✅ |
| Docker Compose: Postgres, Redis, S3 store, API, Celery worker + beat, Prometheus, Grafana | ✅ (first `up` verified 2026-11-07) |
| Crawler, classification pipeline, feeds, UI, community | 🗓 Phases 3–6 |

## Quick start (Docker)

```bash
./deployment/scripts/generate-env.sh     # creates .env with random secrets (mode 600)
make up                                  # docker compose build + up
curl http://127.0.0.1:8110/api/v1/categories
```

API docs: `http://127.0.0.1:8110/api/docs`. Grafana: `127.0.0.1:3011`. Set `CATEGORY_PUBLIC_BIND` /
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
- [Roadmap](documentation/ROADMAP.md)

## Repository layout

```
backend/         FastAPI app, Celery app
common/          settings, JSON logging, domain/URL normalisation
database/        SQLAlchemy models, Alembic migrations, seed
ai_providers/    provider interface, EVREN + mock adapters, prompt, output schema
classification/  taxonomy (YAML), validation, confidence policy
crawler/         Playwright crawler (Phase 3)
integrations/    third-party source connectors + licence catalogue (Phase 5)
feeds/           feed builders (Phase 5)
frontend/        Next.js UI (Phase 5)
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
yayımlayan açık kaynak bir platformdur. Şu an Faz 1 (mimari) tamamlandı; ayrıntılar yukarıdaki belgelerde.
