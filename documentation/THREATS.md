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
| Threat lists as **candidates** (`candidates_only: true`: Phishing.Database, OpenPhish, URLhaus; `threats/feeds.py`) | domain | hosts kept in `threat_feed_entries`, never published; `CATEGORY_THREAT_FEED_CANDIDATES_PER_HOUR` (6) scanned an hour, names resembling a watched Turkish brand first, then .tr names, never popular / official / restricted hosts; the page goes to the threat-candidate queue (`feed_listed:<slug>`, parked pages only watched). The listed link is shown to the reviewer only |
| Our page-proof detector (`threats/detector.py`) | domain | shadow mode by default; flags by itself only when switched on **and** measured (below) |
| Licensed threat feed published as is (`kind: threat_feed` without `candidates_only`) | domain | none approved |

## From lists to our own detector (phases)

1. **Candidates (semi-dependent).** Outside threat lists only nominate hosts; we scan them and a person decides, so
   every record is ours (CC BY) and a list's mistake is never republished. Approve each list with
   `--redistribution no` (OPERATIONS.md). The review card shows which lists name the host and the listed link.
2. **Shadow detector (hybrid).** After every classification — and after every scan of a threat candidate that
   needs no AI answer (below) — `threats/detector.py` judges the page with deterministic rules, `page_proven_v3`:
   * **phishing:** the page asks for a secret (password, card number / CVV, SMS or one-time code, Turkish ID
     number);
   * **investment scam:** it asks for a phone number and talks investment (at least two of "yatırım", "alım
     satım", "yapay zeka", "resmi web sitesi", trading, profit, ...): the fake "AI trading platform of <bank>";

   **and** in both cases it claims a watched brand, Turkish (`integrations/ct_watchlist.yaml`) or worldwide
   (`integrations/global_brands.yaml`: PayPal, Microsoft, Apple, Meta, WhatsApp, DHL, Binance, ...) — in its title or
   description, else by a distinctive brand string in its address unless the page goes by that address word as its
   own, different name, else as the only watched brand its first lines name (several brands named only in the text
   are a list, e.g. payment methods, not a claim; "Sign in with Google" / "Facebook ile giriş" buttons are not a
   claim either) **and** is not that brand's site nor a popular / official /
   restricted host, nor under a registrable domain the Majestic top list or the Chrome UX Report Türkiye ranks
   **and** the host is suspect by itself (its name imitates a brand or a top site, a threat list names it, or its
   certificate is under 30 days old). The page's language is recorded, not required: phishing of a Turkish brand
   abroad, or of a worldwide brand, is phishing too.

   History: `page_proven_v1` (2026-11-17) measured 40 % precision (Vodafone UK's own sign-in pages, a gambling
   site's list of banks); v2 added popularity, the brand in the address and listed brands (no wrong flag left when
   replayed on v1's pages); v3 (2026-11-18) added the investment-scam rule and the worldwide brands. A new rule name
   starts its measurement afresh. Before trusting a rule change, `python -m threats.detector replay` (worker,
   read-only) judges the earlier rule's pages again and compares both with the same decisions of people. Verdicts on
   threat candidates and all positive ones go to `detector_verdicts`; `measure` compares each domain's newest verdict
   with people: a reviewer flag = right, a closed threat review without one = wrong, a detector flag a person
   withdrew = wrong. The admin tab **Dedektör** shows precision, recall, the confusion table and the lists.
3. **On its own (independent).** With `CATEGORY_THREAT_DETECTOR_AUTO=true`, a positive verdict flags the domain
   (phishing, or scam for an investment scam; plus look-alike when the name imitates a brand) from the source
   `cat-egory-detector` — only the page's URL when the scan was of one listed page — but only while
   at least `CATEGORY_THREAT_DETECTOR_MIN_DECISIONS` (200) people's decisions on its positives exist and at least
   `..._MIN_PRECISION` (0.99) of them were right, at most `..._MAX_PER_DAY` (20) a day. Its open threat review is
   closed, except every `..._SAMPLE_EVERY`-th (10) flag, which stays with a person so the measurement goes on.
   Detector records are our own: they expire with a dead site and can be withdrawn like a reviewer's.

### Worldwide candidates and one-page scans

Cat-egory lists threats worldwide, not only those aimed at Türkiye (2026-11-18). Phishing pages die fast (hosting
companies take them down within hours or days), so worldwide candidates are picked **newest first**, and judged
without the AI model, so they cost no tokens (`threats/pages.py`):

* `CATEGORY_THREAT_FEED_CANDIDATES_PER_HOUR` (6): Türkiye-related hosts (a Turkish brand's look-alike, then .tr),
  as before: scanned, classified, and always shown to a person.
* `CATEGORY_THREAT_FEED_GLOBAL_PER_HOUR` (12): other hosts from phishing lists (never malware lists: a malware link
  is a download) that resemble a watched worldwide brand or come with the page's link. Their pages are judged by the
  detector alone; a person sees one only when the page asks for a secret or a phone number.
* **One-page scans** (`CATEGORY_THREAT_PAGE_SCANS`, on): where a phishing list gives the page's link (OpenPhish,
  `phishing-database-links`), that page is scanned — query string and fragment dropped, as they often carry a
  victim's address or token — not the host's home page; robots.txt is asked about that path. Such a scan
  (`scan_results.url_id`) never stands for the domain: no category, no re-scan schedule, no threat lifecycle. A
  detector flag from it is URL-scoped (one page of a possibly hacked, honest site); a reviewer still decides for the
  whole domain from the card, which shows the scanned page.

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
Scans that say nothing about the site (timeouts, robots.txt, our own failures, 403 and 5xx) change nothing.

**Cleaned pages.** A takedown notice counts as gone, the same as a dead site:
- a host's "account suspended" page
- an expired or for-sale domain
- a parking page or a redirect to a parking service
- a bare nginx/Apache/IIS default page
- Cloudflare's phishing warning

These records expire with `raw.expired_reason = taken_down`; dead sites get `unreachable` instead. Notices are
matched on the providers' own wording, and only on pages of at most 400 words. A lure such as "your account has
been suspended" never counts.

A flagged site that stays up but now shows a clearly different page opens a `threat_page_changed` review. "Clearly
different" means a word overlap with the previous scan below 0.4, or a landing on another host. The model looks at
the new page once, and the reviewer then either withdraws the flag (action `withdraw`, key W) or keeps it. This
happens at most once per `threat_change_cooldown_days` (7), because kits show harmless pages to some visitors.

Audit actions: `threat.expire` (with its reason), `threat.return`, `threat.changed`. These daily scans only ask whether the site is still there, so a domain with
an active confirmed threat and a valid AI answer newer than `threat_reclassify_days` (30) is not sent to the model.

## Abuse reports

**Paused (2026-11-19):** `CATEGORY_ABUSE_DRAFTS_ENABLED` now defaults to `false` and the admin menu no longer
links `/admin/abuse` (the page and API still exist). Set the variable to `true` to prepare drafts again.

`threats/abuse.py` prepares abuse-report drafts every hour (`category.threats.abuse_drafts`). Nothing is ever
sent by Cat-Egory: a person reads the draft on `/admin/abuse`, opens it in their own mail program (a `mailto:`
link), sends it, and marks it **I Sent It**. Each change is audited (`abuse.sent`, `abuse.dismissed`).

- **Which domains:** an active domain-scoped threat of our own, either a reviewer's or one Octryx proved on the
  page. The type must be phishing, scam, fraud, malware, malicious downloads or command-and-control. A
  look-alike name alone is not enough, and third-party list hits are never the basis of a report. Each domain
  gets one round of drafts per `abuse_report_repeat_days` (30).
- **Recipients:** abuse addresses come from public RDAP records. The servers are taken from IANA's bootstrap files
  and queried over HTTPS only.
  - The **registrar** contact is the registrar entity's `abuse` contact in the domain's record.
  - The **hosting network** contact comes from the record of the IP the site was last served from.
  - Some TLDs, `.tr` among them, publish no RDAP. Private IPs are skipped. When no address is found, a
    `no_contact` row reminds the reviewer to use the provider's web form.
  - Cloudflare, Google and AWS prefer their own forms; the panel links the form next to the draft.
- **Content:** only what we saw ourselves: the domain and address (defanged), IP, threat types, when it was
  confirmed, how (a person, or Octryx's page evidence), and the public domain page. USOM/SGB and VirusTotal
  results never go into a report.
- **Follow-up:** each row shows whether the site is still flagged. When a reported site is taken down, the
  lifecycle above expires the flag after 14 days.

Settings: `CATEGORY_ABUSE_DRAFTS_ENABLED`, `CATEGORY_ABUSE_DRAFTS_BATCH` (10 domains per run),
`CATEGORY_PUBLIC_SITE_URL`.

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
- Done: threat lists and the `threats.csv`/`threats.json` dataset under CC0 (see DATA_LICENSE.md). Still open: URL feed for reviewers/partners,
  MISP feed, API.

## Isolation and safety

- Pages are opened only by the sandboxed crawler (seccomp + AppArmor, egress proxy).
- Before malware-distribution URLs are visited at scale: separate egress (VPS/VPN), so the home IP and the
  database host are not exposed. Downloads are hashed, never executed outside a sandbox, never republished.
- Review capacity is the real limit (one reviewer today): keep hourly caps (`ct_watch_max_per_hour`) and raise
  them only with more reviewers.
