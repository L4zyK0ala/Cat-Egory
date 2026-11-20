<p align="center">
  <img src="frontend/public/logo.svg" width="104" alt="Cat-Egory logo: a grey cat">
</p>

<h1 align="center">Cat-Egory</h1>

<p align="center">
  <b>Every website, curiously categorized.</b><br>
  An open, AI-assisted database of website categories and security threats,<br>
  checked by people and published as an API and free DNS block lists.
</p>

<p align="center">
  <a href="https://github.com/L4zyK0ala/Cat-Egory/actions/workflows/ci.yml"><img src="https://github.com/L4zyK0ala/Cat-Egory/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/code-Apache--2.0-C8352B.svg" alt="Code: Apache-2.0"></a>
  <a href="DATA_LICENSE.md"><img src="https://img.shields.io/badge/data-CC%20BY%204.0-2A1E1A.svg" alt="Data: CC BY 4.0"></a>
  <a href="documentation/TAXONOMY.md"><img src="https://img.shields.io/badge/taxonomy-3.0.0-5C4A42.svg" alt="Taxonomy 3.0.0"></a>
  <img src="https://img.shields.io/badge/status-alpha-9A5B00.svg" alt="Status: alpha">
</p>

<p align="center">
  <a href="https://cat-egory.org"><b>Website</b></a> ·
  <a href="https://api.cat-egory.org/api/docs"><b>API docs</b></a> ·
  <a href="https://cat-egory.org/feeds"><b>Block lists</b></a> ·
  <a href="https://cat-egory.org/accuracy"><b>Accuracy</b></a> ·
  <a href="#türkçe-özet"><b>Türkçe</b></a>
</p>

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="documentation/images/screenshot-home-dark.png">
    <img src="documentation/images/screenshot-home-light.png" width="860" alt="The Cat-Egory home page: a search box, a cat with a magnifying glass, and live totals">
  </picture>
</p>

---

Commercial web-categorisation databases are closed and paid. Cat-Egory is an open alternative: anyone can look up,
download, check and correct its data, and every label says where it came from.

|  |  |  |
|---|---|---|
| 📂 **Open data**<br>Categories you can download and build on, under CC BY 4.0. | 🧑‍⚖️ **Checked by people**<br>The AI reads first; unsure answers, look-alikes and threats go to a person. | 🏷️ **Every label has a source**<br>AI, reviewer, community, rule or a licensed source: always recorded. |

> **Status: alpha.** The public instance runs at [cat-egory.org](https://cat-egory.org) on one machine, with real
> crawling and real AI classification. On 19 November 2026 it knew about 60,500 domains, 6,000 of them categorised
> and 1,600 checked by a person. Expect breaking changes.

## Contents

[How it works](#how-it-works) · [Features](#features) · [Use the data](#use-the-data) ·
[Screenshots](#screenshots) · [Run it yourself](#run-it-yourself) · [Development](#development) ·
[Documentation](#documentation) · [Licences](#licences) · [Türkçe özet](#türkçe-özet)

## How it works

```mermaid
flowchart TD
    IN["Domains<br/>admin · import · community<br/>licensed sources · CT watch"] --> Q[Scan queue]
    Q --> BR["Sandboxed Chromium<br/>behind an SSRF-safe proxy"]
    BR --> PG["Page text · HTML<br/>screenshot"]
    PG --> AI{"AI classification<br/>EVREN + validation"}
    AI -- "confidence ≥ 0.80<br/>(≥ 0.90 sensitive)" --> PUB[("Open database")]
    AI -- "0.70 up to that" --> SM{"Second model<br/>agrees?"}
    SM -- yes --> PUB
    SM -- no --> HR["Human review"]
    AI -- "below 0.70 · injection<br/>threat claim" --> HR
    HR --> PUB
    PG --> DT{"Page-proof<br/>detector"}
    DT -- "proven" --> PUB
    PUB --> OUT["REST API · web UI<br/>block lists · change feed"]
```

- **Taxonomy 3.0.0:** 60 content categories and 8 security-risk categories, versioned and never renumbered.
  Licensed and unlicensed sites are kept apart where filters need it: *gambling* and *illegal gambling*,
  *streaming media* and *piracy* ([TAXONOMY.md](documentation/TAXONOMY.md)).
- **Trust rules:** the AI never overrides a person. A threat flag comes only from a reviewer, a licensed threat
  source, the sister project Octryx when it proved an imitation on the page, or our page-proof detector, which
  only acts once people's decisions have measured it. A model's threat claim only opens a review. Pages that try
  to instruct the model ("ignore previous instructions…") always go to a person ([THREATS.md](documentation/THREATS.md)).
- **Provenance:** every label records who or what set it. Third-party data keeps its own licence and is never
  relicensed; screenshots and HTML of other people's sites are never published.
- **A safe crawler:** Chromium runs with its own sandbox in a container without capabilities, on a network whose
  only way out is an egress proxy that refuses private, loopback and metadata addresses at connect time.

## Features

**Classification**

- AI classification with [EVREN](https://evren.ssyz.org.tr/), a confidence policy, a second opinion and human
  review; accuracy measured against people on a [public page](https://cat-egory.org/accuracy) (95 % interval).
  Answers publish from 0.80 confidence, 0.90 in sensitive ones (adult, gambling, piracy, banking, government…); the
  thresholds come from a calibration against people's decisions.
- EVREN's guard model looks for adult content the AI did not name; every hit goes to a person.
- Automatic re-scans: daily for threats, weekly for changed or unreachable sites, monthly otherwise. An unchanged
  page keeps its answer instead of asking the model again.
- Discovery: popular sites first ([Majestic Million](https://majestic.com/reports/majestic-million), CC BY 3.0),
  names visitors looked up before them, all under a daily AI-token cap.
- 🧪 **Yaş Mama**, a local classifier that learns from published labels, runs in shadow mode next to the AI and
  publishes nothing yet.

**Threats**

- Look-alike domains of Turkish brands caught early from certificate-transparency logs, always reviewed by a
  person.
- Phishing candidates from open lists, Turkish and worldwide brands alike: the exact reported page is scanned, and
  only what the page itself proves is flagged.
- A page-proof detector (rule v3): a page that asks for passwords, card details or a phone number for an
  "investment", under a brand it does not belong to, on a suspicious name.
- Confirmed threat pages open with a plain warning for the visitor: what not to enter, what to do if they already
  did, and where to report it. Listed addresses are never clickable links.
- Confirmed threats can be reported to USOM (Türkiye's national CERT) from the threats page, in batches.

**Data and integrations**

- Public REST API: domain, URL, history, categories, stats, bulk lookup and a change feed.
- Block lists in TXT, hosts, AdGuard / uBlock, DNS RPZ, CSV and JSON, with ready-made router profiles (security,
  family) and diffs between versions.
- Read-only developer API keys without an account (daily quota).
- Community reports, category suggestions, votes and moderation; corrections from trusted contributors apply at
  once.

**Running it**

- Admin console: quick review with screenshots, moderation, confirmed threats, the detector's track record,
  demand trends (daily totals, never who asked) and a monthly infographic for social media.
- Prometheus, Grafana and alert rules, Turkish alerts and a daily summary on Telegram.
- Nightly backups with a restore test, and an encrypted off-site copy (restic, any S3 bucket).
- Public instance behind a Cloudflare Tunnel with TLS; the admin console is never published and requires a second
  factor (TOTP). Strict Content-Security-Policy with per-request nonces; the crawler has its own narrow
  credentials; images are scanned for vulnerabilities in CI.

## Use the data

```bash
# how is a site categorised, and is it a confirmed threat?
curl https://api.cat-egory.org/api/v1/domain/wikipedia.org

# a block list for a firewall or Pi-hole (also .hosts, .adblock, .rpz)
curl -O https://api.cat-egory.org/api/v1/feeds/phishing.txt
curl -O https://api.cat-egory.org/api/v1/feeds/profile-family.txt
```

Every list, with its record count and checksum, is on [cat-egory.org/feeds](https://cat-egory.org/feeds) and at
`/api/v1/feeds`. The [API documentation](https://api.cat-egory.org/api/docs) describes every endpoint. Please credit
"Cat-Egory contributors" (CC BY 4.0).

## Screenshots

<table>
  <tr>
    <td width="50%">
      <picture>
        <source media="(prefers-color-scheme: dark)" srcset="documentation/images/screenshot-domain-dark.png">
        <img src="documentation/images/screenshot-domain-light.png" alt="A domain page: categories, their source, and the history of every change">
      </picture>
      <p align="center"><sub>A domain page: categories, where they came from, and their history</sub></p>
    </td>
    <td width="50%">
      <picture>
        <source media="(prefers-color-scheme: dark)" srcset="documentation/images/screenshot-threat-dark.png">
        <img src="documentation/images/screenshot-threat-light.png" alt="A confirmed phishing domain: a red warning tells the visitor not to visit it">
      </picture>
      <p align="center"><sub>A confirmed phishing site: the visitor is warned first</sub></p>
    </td>
  </tr>
</table>

## Run it yourself

Needs Docker with Compose on Linux. The crawler's AppArmor profile is required on hosts that restrict
unprivileged user namespaces (Ubuntu 23.10 and later).

```bash
./deployment/scripts/generate-env.sh      # creates .env with random secrets (mode 600)
sudo cp deployment/apparmor/cat-egory-crawler /etc/apparmor.d/
sudo apparmor_parser -r -W /etc/apparmor.d/cat-egory-crawler
docker compose -f deployment/docker-compose.yml --env-file .env up -d --build
docker compose -f deployment/docker-compose.yml --env-file .env exec api \
    python -m backend.cli create-admin --email you@example.org --name "You"   # prints an admin API key
```

| Service | Address |
|---|---|
| Web UI | `http://127.0.0.1:3010` |
| API and its docs | `http://127.0.0.1:8110/api/docs` |
| Admin UI and admin API | `:3012` and `:8111`, on the private admin address only |
| Grafana | `:3011` |

Full guide: [OPERATIONS.md](documentation/OPERATIONS.md).

**AI providers**

- `CATEGORY_AI_PROVIDER=evren`: the [EVREN](https://evren.ssyz.org.tr/) LLM gateway (OpenAI-compatible), with
  your own key. Measured behaviour, timeouts and open terms-of-use questions:
  [EVREN_INTEGRATION.md](documentation/EVREN_INTEGRATION.md).
- `CATEGORY_AI_PROVIDER=mock` (default): an offline keyword matcher for development. It is **not** an AI; its
  confidence is capped at 0.60, so everything it answers goes to a person.

Another model can be added by subclassing `ai_providers.base.AIProvider` and registering it in
`ai_providers/registry.py`.

## Development

```bash
python3 -m venv .venv && .venv/bin/pip install -c requirements/constraints.txt -e '.[dev]'
.venv/bin/ruff check .
.venv/bin/python -m pytest tests/unit                          # no services needed
CATEGORY_TEST_DATABASE_URL=postgresql+psycopg://user:pass@host:5432/empty_test_db \
  .venv/bin/python -m pytest                                   # + integration tests (WIPES that database)
cd frontend && npm ci && npm run typecheck
```

See [CONTRIBUTING.md](CONTRIBUTING.md). Security issues: [SECURITY.md](SECURITY.md); please do not open a public
issue.

<details>
<summary><b>Repository layout</b></summary>

```
backend/         FastAPI app, Celery tasks, admin CLI
common/          settings, JSON logging, domain/URL normalisation
database/        SQLAlchemy models, Alembic migrations, seed
ai_providers/    provider interface, EVREN + mock adapters, prompt, output schema
classification/  taxonomy (YAML), validation, confidence policy, evaluation set, Yaş Mama
crawler/         Playwright crawler, SSRF guard, egress proxy
threats/         detector, threat candidates and one-page scans, lifecycle
integrations/    third-party source engine, licence catalogue, brand watchlists
feeds/           block-list builders
frontend/        Next.js web UI and admin console
deployment/      Dockerfiles, compose, seccomp/AppArmor, Prometheus, Grafana, scripts
documentation/   design documents
tests/           unit, integration and browser tests
```

</details>

## Documentation

| Design | Running it |
|---|---|
| [Architecture and security model](documentation/ARCHITECTURE.md) | [Operations](documentation/OPERATIONS.md) |
| [API design](documentation/API_DESIGN.md) · [ER diagram](documentation/ER_DIAGRAM.md) | [Admin guide](documentation/ADMIN_GUIDE.md) |
| [Taxonomy](documentation/TAXONOMY.md) | [Threats and the detector](documentation/THREATS.md) |
| [EVREN integration](documentation/EVREN_INTEGRATION.md) | [Roadmap](documentation/ROADMAP.md) · [Changelog](CHANGELOG.md) |

## Licences

- **Code:** [Apache-2.0](LICENSE).
- **Categorisations and the taxonomy:** CC BY 4.0, proposed and pending legal review ([DATA_LICENSE.md](DATA_LICENSE.md)).
- **Third-party data** keeps its own licence and attribution and is never relicensed.

## Türkçe özet

**Cat-Egory**, web sitelerini herkesin kullanıp denetleyebileceği ve düzeltebileceği biçimde kategorize eden açık
kaynak bir platformdur. Siteleri yalıtılmış gerçek bir tarayıcıyla ziyaret eder, yerli yapay zekâ platformu
EVREN'in modelleriyle sürümlü bir kategori ağacına göre sınıflandırır. Her cevabı güven eşikleri, ikinci model ve
insan incelemesiyle denetler; sonuçları açık veritabanı, REST API ve indirilebilir listeler (TXT, hosts,
AdGuard / uBlock, DNS RPZ, CSV, JSON) olarak yayımlar.

- **Kategoriler:** 60 içerik ve 8 güvenlik kategorisi. Yasal ve yasa dışı olanlar filtrelerin ihtiyaç duyduğu
  yerde ayrıdır: Kumar / Yasa Dışı Kumar, Yayın Platformları / Korsan İçerik.
- **Tehditler:** Taklit alan adları sertifika şeffaflığı kayıtlarından erken yakalanır; açık listelerdeki
  oltalama adresleri tek tek taranır. Tehdit işaretini yalnızca bir insan, lisanslı bir kaynak, taklidi sayfada
  kanıtlayan kardeş proje Octryx ya da insan kararlarıyla ölçülmüş sayfa dedektörü koyar. İşaretli sitelerin
  sayfasında ziyaretçiye açık bir "Bu siteye girmeyin" uyarısı çıkar.
- **Kullanım:** Router'lar için hazır güvenlik ve aile profilleri, toplu sorgu, geliştirici anahtarları ve insan
  kontrolüyle ölçülen bir [doğruluk sayfası](https://cat-egory.org/accuracy) vardır.
- **Güvenlik:** Yönetim paneli internete açılmaz ve iki aşamalı doğrulama (TOTP) ister; yedekler şifreli olarak
  makine dışına da kopyalanır. Doğrulanmış tehditler USOM'a bildirilebilir.
- **Yaş Mama:** Yerel sınıflandırıcı şimdilik gölge modda yapay zekâyla karşılaştırılmaktadır.

Proje alfa aşamasındadır; tüm bileşenler tek makinede Docker üzerinde, gerçek tarama ve gerçek yapay zekâ
sınıflandırmasıyla çalışır: [cat-egory.org](https://cat-egory.org).
