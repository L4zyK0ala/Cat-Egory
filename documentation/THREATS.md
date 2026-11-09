# Threats Module

Cat-Egory's security side: finding, confirming and publishing **malicious** sites (phishing, malware, scams,
look-alike domains) as an early warning, first for brands and services used in Türkiye. It lives inside
Cat-Egory but has its own code (`threats/`, `backend/app/api/threats.py`), its own rules and, later, its own
queue and worker, so it can be split out if it outgrows the project.

Why our own: third-party threat feeds rarely allow republishing (URLhaus: abuse.ch terms, no derivative works
without consent; see `integrations/sources.yaml`). What we observe and confirm ourselves we may license freely.

## Rules

1. **Nothing is published on a model's word.** A threat record comes from a reviewer (with an evidence note) or
   from a source whose licence we have checked and approved. AI suspicions only open review tasks.
2. **Scope.** A record is *domain-scoped* (`url_id` NULL: the host itself is malicious, e.g. a look-alike
   domain) or *URL-scoped* (one page on an otherwise legitimate, usually hacked, site). Only domain-scoped
   records flag a domain, appear in category listings and DNS blocklists (RPZ / hosts) and count as threat
   domains in `/api/v1/stats`. URL-scoped records are counted apart (`threat_urls`, `risk.reported_urls`).
3. **Visibility.** Public output (domain page, URL lookup, stats, risk listing) shows only active records from
   sources whose licence allows redistribution (`threats.records.public`). Feeds are stricter: only our own
   reviewer records (`feeds.builder.THREAT_SOURCES`).
4. **Privacy.** URL texts are never published: phishing links often carry the victim's e-mail address or a
   token. The public API answers "is this exact link reported?" (`GET /api/v1/url` → `url_risk`) and "how many
   pages on this host are reported?" (`risk.reported_urls`). Evidence notes stay internal (admin API only).
   Screenshots and page HTML of third-party sites are never published.
5. **Mistakes are cheap to fix.** Anyone can dispute a flag (`POST /api/v1/community/report`, `false_positive`
   with the threat category). Accepting it withdraws our record (kept as history, `is_active` false). A
   third-party feed's record is not touched; the moderation result says how many remain, and the moderator
   reports the mistake to that feed. Threat disputes never mark the content classification `disputed`.

## How a threat gets in (today)

| Path | Scope | Notes |
|---|---|---|
| CT brand watch (`integrations/ct_watch.py`) | domain | new certificates resembling watched brands or top sites → scan → forced review (`brand_lookalike`, `lookalike_of:<site>`) |
| Review decision (`POST /admin/reviews/{id}/decision`, `risk`) | domain | reviewer ticks threat categories |
| Admin threats API (`POST /api/v1/admin/threats`) | domain or URL | evidence note required (10–2000 chars) |
| Community report (`false_negative` + threat category) | domain | becomes a record only when a moderator accepts it |
| Licensed threat feed (`integrations/sources.yaml`, `kind: threat_feed`) | domain | none approved yet; URLhaus internal-only at most |

Withdraw: `POST /api/v1/admin/threats/withdraw` (reason required) or an accepted community dispute.
List (all sources, incl. internal, with URLs and evidence): `GET /api/v1/admin/threats?scope=&active=`.
All writes are audit-logged (`threat.record`, `threat.withdraw`, `community.moderate`).

## Roadmap

**Step 1 (done, 2026-11-09):** URL-scoped records; public visibility rule in one place; admin API; disputes
and reports through the community form; exact-link lookup in the UI.

**Step 2: fast lane.** Phishing pages live hours to days, categorisation is deliberately slow.
- A separate Celery queue/worker (`threats`) so threat scans neither wait for nor block categorisation.
- Rule-based pre-checks before any AI call: login/card form, form target on another host, brand logo/favicon
  hash vs the official site, HTML similarity, known kit paths.
- Re-check active records on a schedule (DNS, HTTP status, page still a phishing page) and deactivate dead
  ones; keep `last_seen` honest.

**Step 3: more of our own observations.**
- Typo/homograph generation for watched brands (dnstwist-style) + DNS resolution.
- Newly registered domains (ICANN CZDS zone files for gTLDs; read the terms first: zone data itself must not
  be redistributed).
- Spam-trap mailboxes collecting phishing links.

**Step 4: acting on findings.**
- Abuse reports to registrar and hosting provider, USOM, Google Safe Browsing, Netcraft (each needs the
  user's approval of the wording and channel first).
- Published false-positive rate and time-to-withdraw.

**Step 5: wider distribution.**
- Separate threat dataset under CC0 (blocklist tools adopt CC0 more easily), URL feed for reviewers/partners,
  MISP feed, API.

## Isolation and safety

- Pages are opened only by the sandboxed crawler (seccomp + AppArmor, egress proxy).
- Before malware-distribution URLs are visited at scale: separate egress (VPS/VPN), so the home IP and the
  database host are not exposed. Downloads are hashed, never executed outside a sandbox, never republished.
- Review capacity is the real limit (one reviewer today): keep hourly caps (`ct_watch_max_per_hour`) and raise
  them only with more reviewers.
