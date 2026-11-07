# Security Policy

## Reporting a vulnerability

Please **do not open a public issue**. Use GitHub's private vulnerability reporting ("Report a
vulnerability" on the Security tab) for this repository. We aim to acknowledge reports within 3 working days
and to agree on a disclosure date with you.

Especially in scope:

- SSRF or any way to make the crawler reach internal, private, link-local or metadata addresses
- escaping the browser sandbox or crawler container
- prompt injection that changes a published classification
- authentication/authorisation bypass, API key leakage, rate-limit bypass
- injection (SQL, XSS, CSV/formula, RPZ zone) via domain names, page content or feeds
- licence/provenance bypass that would publish non-redistributable data

## Supported versions

Only the `main` branch receives security fixes until the first stable release.

## Handling secrets

All credentials come from environment variables or Docker secrets. `.env` is git-ignored; `.env.example`
contains no real values. If you find a committed secret, report it privately so it can be rotated.
