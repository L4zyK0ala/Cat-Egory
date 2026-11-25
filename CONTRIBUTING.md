# Contributing

Thanks for helping! Two kinds of contributions are welcome: **code** and **data** (category corrections,
false-positive reports, new domains).

## Code

1. Open an issue first for anything larger than a bug fix.
2. `python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'`
3. Keep `ruff check .` and `pytest` green; add tests for new behaviour (unit tests must not need network
   or services; integration tests use `CATEGORY_TEST_DATABASE_URL`).
4. Schema changes: edit `database/models.py`, then `alembic revision --autogenerate -m "..."`, review the
   generated file, and include it in the PR.
5. Taxonomy changes: never rename or reuse an `id`/`code`; deprecate with `replaced_by`; bump `version`.
6. New AI provider: subclass `ai_providers.base.AIProvider`, register it in `ai_providers/registry.py`,
   document what is verified about its API. Do not ship code that pretends an unverified API works.
7. New data source: add it to `integrations/sources.yaml` with `enabled: false` and a link to its licence.
8. Sign off your commits (`git commit -s`) to certify the [DCO](https://developercertificate.org/).

## Versions and releases

Every change gets a line under `## [Unreleased]` in `CHANGELOG.md`, in the same commit. A release raises the
version by what that section contains ([Semantic Versioning](https://semver.org/)):

| Raise | When | Example |
|---|---|---|
| patch `0.3.0 → 0.3.1` | fixes, security and image updates, docs: nothing new for users and nothing breaks | a CVE closed, a wrong guide step |
| minor `0.3.0 → 0.4.0` | something new: a list or profile, a page, an API field or endpoint, a DNS server guide. Before 1.0 also a breaking change, written under **Breaking** | `profile-business`, the Company page |
| major `→ 1.0.0` | the API and the list formats are declared stable; after that a breaking change raises the major | |

`python deployment/scripts/release.py X.Y.Z` writes the version into `pyproject.toml` and the frontend's
`package.json` and lock file, and turns the Unreleased section into `## [X.Y.Z] – date`. Commit that as
`Release X.Y.Z`, tag it `vX.Y.Z` and push both; the Release workflow then publishes the GitHub release with that
changelog section as its text. Other version numbers move on their own: the taxonomy (`taxonomy.yaml`,
semantic), the lists (`YYYY.MM.DD.N`, every build) and the DNS updater (`VERSION`, its User-Agent).

## Data

Use the community endpoints / UI (Phase 6) to report misclassifications. Provide evidence (what on the
site shows the correct category). Contributions are released under CC BY 4.0.

## Never commit

Secrets (`.env`, keys, tokens), screenshots or HTML of third-party sites, or data from sources whose
licence does not permit it.
