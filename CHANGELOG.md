# Changelog

All notable changes to Cat-Egory. Versions follow [Semantic Versioning](https://semver.org/); until 1.0 any
release may contain breaking changes.

## [0.2.0] – 2026-11-20

### Classification
- Taxonomy 3.0.0: *piracy* is kept apart from licensed *streaming media* (60 content and 8 security-risk
  categories).
- Answers publish from 0.80 confidence outside sensitive categories (0.90 inside them), after a calibration
  against people's decisions (`backend.cli calibration`: ECE, Brier score, confidence bands).
- EVREN's guard model (`qwen3-guard-4b`) measured on known pages (`python -m threats.guard_eval`); it is used
  only to find adult content the AI did not name, and every hit opens a review.
- Discovery takes Türkiye's most visited sites first (CrUX); Yaş Mama weighs small categories up.

### Threats
- Outside phishing lists become candidates; only what the reported page itself proves is flagged. Page-proof
  detector v3 and one-page scans; candidates from Turkish and worldwide brands.
- Confirmed threat pages warn the visitor first.
- Confirmed threats can be reported to USOM in batches of at most 100 domains per mail.
- "Reject" never records a threat (API and review panel).

### Site and admin console
- Monthly infographic for social media: seven cards (cover, from discovery to verification, .tr categories,
  threats, AI quality, crawling capacity, block lists and API), each with a footnote on its source.
- Search engines: a title and description per domain and category, `robots.txt` and `sitemap.xml`.
- Admin sign-in can require a second factor (TOTP, with backup codes): `CATEGORY_ADMIN_MFA_REQUIRED=true`,
  `backend.cli mfa-enroll`.
- `backend.cli list-keys` shows expired keys and `--active`; `revoke-sessions` signs every browser out.

### Security and operations
- Content-Security-Policy with a per-request nonce; no `'unsafe-inline'` scripts.
- The crawler has its own Redis user and object-store key with only the rights it needs.
- Python packages are pinned (`requirements/constraints.txt`); CI builds the three images and scans them with
  Trivy (pinned by commit). The frontend image runs on Node 22 / Alpine 3.24 without package managers.
- Encrypted off-site backups with restic (`deployment/scripts/offsite-backup.sh`), with their own alert.
- Host monitoring (Grafana "Server" dashboard) and Docker housekeeping when the system disk passes 80 %.

## [0.1.0] – 2026-11-16

First public release: sandboxed crawler behind an egress proxy, AI classification through EVREN with a
confidence policy, second opinion and human review, REST API, web UI and block lists (TXT, hosts, CSV, JSON,
DNS RPZ), community reports and moderation, Docker Compose deployment with Prometheus, Grafana and backups, and
the shadow-mode local classifier Yaş Mama.

[0.2.0]: https://github.com/L4zyK0ala/Cat-Egory/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/L4zyK0ala/Cat-Egory/releases/tag/v0.1.0
