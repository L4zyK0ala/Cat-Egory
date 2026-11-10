# Threats Module

Cat-Egory's security side: finding, confirming and publishing **malicious** sites (phishing, malware, scams,
look-alike domains) as an early warning, first for brands and services used in Türkiye. It lives inside
Cat-Egory but has its own code (`threats/`, `backend/app/api/threats.py`), its own rules and, later, its own
queue and worker, so it can be split out if it outgrows the project.

Why our own: third-party threat feeds rarely allow republishing (URLhaus: abuse.ch terms, no derivative works
without consent; see `integrations/sources.yaml`). What we observe and confirm ourselves we may license freely.

## Rules

1. **Nothing is published on a model's word.** A threat record comes from a reviewer (with an evidence note), from
   a source whose licence we have checked and approved, or from Octryx when it proved the imitation on the page (the
   operator's decision; `threats/octryx.py`). AI suspicions only open review tasks.
2. **Scope.** A record is *domain-scoped* (`url_id` NULL: the host itself is malicious, e.g. a look-alike
   domain) or *URL-scoped* (one page on an otherwise legitimate, usually hacked, site). Only domain-scoped
   records flag a domain, appear in category listings and DNS blocklists (RPZ / hosts) and count as threat
   domains in `/api/v1/stats`. URL-scoped records are counted apart (`threat_urls`, `risk.reported_urls`).
3. **A threat outranks its content category.** A domain with a public domain-scoped record leaves every
   content-category list (feeds, `all.csv`/`all.json`, category pages, the distribution chart) and appears only
   under Security Threats: those lists are also used as allow lists. Its own domain page still shows both.
4. **Visibility.** Public output (domain page, URL lookup, stats, risk listing) shows only active records from
   sources whose licence allows redistribution (`threats.records.public`). Feeds are stricter: only our own
   reviewer records (`feeds.builder.THREAT_SOURCES`).
5. **Privacy.** URL texts are never published: phishing links often carry the victim's e-mail address or a
   token. The public API answers "is this exact link reported?" (`GET /api/v1/url` → `url_risk`) and "how many
   pages on this host are reported?" (`risk.reported_urls`). Evidence notes stay internal (admin API only).
   Screenshots and page HTML of third-party sites are never published.
6. **Mistakes are cheap to fix.** Anyone can dispute a flag (`POST /api/v1/community/report`, `false_positive`
   with the threat category). Accepting it withdraws our record (kept as history, `is_active` false). A
   third-party feed's record is not touched; the moderation result says how many remain, and the moderator
   reports the mistake to that feed. Threat disputes never mark the content classification `disputed`.

## How a threat gets in (today)

| Path | Scope | Notes |
|---|---|---|
| CT brand watch (`integrations/ct_watch.py`) | domain | new certificates resembling watched brands or top sites → scan → threat-candidate queue (`brand_lookalike`, `lookalike_of:<site>`); parked pages (model ≥ 0.90, no risk claim beyond the name: only `lookalike-domain` or `suspicious`) are only watched, re-checked weekly |
| Review decision (`POST /admin/reviews/{id}/decision`, `risk`) | domain | reviewer ticks threat categories |
| Admin threats API (`POST /api/v1/admin/threats`) | domain or URL | evidence note required (10–2000 chars) |
| Community report (`false_negative` + threat category) | domain | becomes a record only when a moderator accepts it |
| Octryx sync (`threats/octryx.py`, hourly) | domain | proven on the page: listed at once as phishing + look-alike, without review. Public only through a third-party list, protected names, or over the per-run cap: threat-candidate queue |
| Licensed threat feed (`integrations/sources.yaml`, `kind: threat_feed`) | domain | none approved yet; URLhaus internal-only at most |

A threat flag on a popular site (Majestic top list), a watched brand's official domain or a restricted suffix
(.gov.tr, .edu.tr, ...) — or any subdomain of these — is refused with 409 `protected_domain` until the reviewer
confirms (`confirm_protected`; the UI asks). Added after `*.smoot.apple.com` was once flagged as scam by mistake.
Look-alikes are other registrable domains and are not affected (`threats/guard.py`).

Withdraw: `POST /api/v1/admin/threats/withdraw` (reason required) or an accepted community dispute.
List (all sources, incl. internal, with URLs and evidence): `GET /api/v1/admin/threats?scope=&active=`.
All writes are audit-logged (`threat.record`, `threat.withdraw`, `community.moderate`).

## National list cross-check (SGB / USOM)

T.C. Siber Güvenlik Başkanlığı (formerly USOM) publishes its malicious-address list through a public API
(`https://siberguvenlik.gov.tr/api/openapi.yaml`, no key; the old `url-list.txt` ended on 2026-06-01). Every hour
`category.threats.sgb_check` asks it about up to 30 domains that have an active domain-scoped threat record or
an open threat-candidate review, each at most once a day, one second apart (`threats/sgb.py`). The answer — on
the list (exact domain, a subdomain, or a URL on it; with the list's category, criticality and date) or not —
is stored in `external_listings`.

**Internal only.** The site's terms (`/yasal-uyarilar`) forbid copying, republishing or distributing its content
without prior written permission and attribution. So nothing from the list appears on the public site, in the
public API, stats or feeds. Reviewers see it in the admin threat list and on `/admin/threats`
(`GET /api/v1/admin/threats/sgb-report`): our confirmed threats **not on the national list** are candidates to
report through the İhbar Formu on siberguvenlik.gov.tr. Showing "also on the national list" publicly would need
the Başkanlık's written permission (contact: iletisim@siberguvenlik.gov.tr).

Run now (in the worker, which has internet access): `$DC exec worker python -m backend.cli sgb-check --limit 50`.
Switch off: `CATEGORY_SGB_CHECK_ENABLED=false`.

## VirusTotal lookups (reviewers only)

With `CATEGORY_VIRUSTOTAL_API_KEY` set (worker only; empty = off), `category.threats.virustotal_check` looks up
4 domains every 15 minutes, 16 seconds apart: the same domains as the SGB check, each at most every 72 hours
(`threats/virustotal.py`). Reviewers see "VT: flagging vendors / total" on review cards and on `/admin/threats`.

- **Lookups only** (`GET /api/v3/domains/{domain}`). Nothing is submitted for scanning and no URL is sent:
  submissions are shared with VirusTotal's community, and phishing URLs can carry a victim's e-mail address.
- **Internal only.** Nothing appears on the public site, in the public API, stats or feeds, and an answer never
  creates a threat flag. Whether results may be republished was not established; the API overview also rules out
  integration in projects that may harm the antivirus industry, so do not feed results into published block lists.
- **Terms are the operator's call.** The public API allows 500 requests a day and 4 a minute and must not be used
  in commercial products or services. If Cat-egory becomes commercial, this needs a premium licence.
- **Reading it.** 0 detections does not mean safe: most look-alikes are new and vendors have often not seen them.
  One vendor alone can be a false alarm.

Run now (in the worker): `$DC exec worker python -m backend.cli virustotal-check --limit 20`.

## Keeping records honest

Flagged domains are re-scanned daily, and `threats/lifecycle.py` reads those scans for our own domain-scoped records:
a live site moves `last_seen` on; a site gone (no DNS, no connection, 404/410) and unseen for `threat_expire_days` (14)
is deactivated and leaves the public pages and feeds, kept with `raw.expired_at`. If it comes back, a `threat_returned`
review opens in the threat-candidate queue: nothing is flagged again automatically, the name may have a new owner.
Scans that say nothing about the site (timeouts, robots.txt, our own failures, 403 and 5xx) change nothing. Audit
actions: `threat.expire`, `threat.return`. These daily scans only ask whether the site is still there, so a domain with
an active confirmed threat and a valid AI answer newer than `threat_reclassify_days` (30) is not sent to the model.

## Roadmap

**Step 1 (done, 2026-11-09):** URL-scoped records; public visibility rule in one place; admin API; disputes
and reports through the community form; exact-link lookup in the UI.

**Step 2: fast lane.** Phishing pages live hours to days, categorisation is deliberately slow.
- A separate Celery queue/worker (`threats`) so threat scans neither wait for nor block categorisation.
- Rule-based pre-checks before any AI call: login/card form, form target on another host, brand logo/favicon
  hash vs the official site, HTML similarity, known kit paths.
- Detect that a flagged page was cleaned while the site stays online (today a reviewer withdraws it).

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
