# Operations

Running Cat-egory on one host with Docker Compose. Shorthand:

```bash
DC="docker compose -f deployment/docker-compose.yml --env-file .env"   # prefix with sudo if needed
```

## 1. First start

```bash
./deployment/scripts/generate-env.sh          # random secrets, mode 600
$DC up -d --build                              # 12 services; migrations + seed run in `migrate`
$DC ps -a                                      # migrate: Exited (0); others running/healthy
$DC exec api python -m backend.cli create-admin --email you@example.org --name "You"
$DC exec crawler python -m crawler.selftest    # sandbox, isolation, proxy (install the AppArmor profile first, see below)
```

**Admin UI sign-in.** Give an admin or moderator a password once, then sign in at the admin UI with email and
password (`POST /api/v1/admin/session`): it returns a short-lived key (12 h, or 7 days with "keep me signed in"),
revoked on sign-out. At most 5 wrong passwords per email per 15 minutes. Long-lived `cat_` keys still work for
scripts ("Use an API key").

```bash
$DC exec api python -m backend.cli set-password --email you@example.org    # prompts twice, min. 12 characters
```

Addresses (set `CATEGORY_PUBLIC_BIND` / `CATEGORY_ADMIN_BIND` in `.env`):

| Service | Default | Notes |
|---|---|---|
| Web UI (public) | `:3010` on `CATEGORY_PUBLIC_BIND` | no admin link; `/admin` answers 404. `CATEGORY_PUBLIC_API_URL` must be the API address browsers can reach; `CATEGORY_CORS_ORIGINS` must list the UI origin |
| API + Swagger (public) | `:8110` on `CATEGORY_PUBLIC_BIND` (`/api/docs`) | lookups, categories, feeds, community reports only: no `/api/v1/admin/*`, no `/metrics` |
| **Admin UI** | `:3012` on `CATEGORY_ADMIN_BIND` | admin console, review and moderation; talks to the admin API (`CATEGORY_ADMIN_API_URL`, `CATEGORY_ADMIN_CORS_ORIGINS`) |
| **Admin API** | `:8111` on `CATEGORY_ADMIN_BIND` | everything incl. `/api/v1/admin/*` and `/metrics` (Prometheus scrapes this one) |
| Grafana | `:3011` on `CATEGORY_ADMIN_BIND` | dashboard "Cat-egory overview" is provisioned |
| Prometheus | `:9091` | alert rules in `deployment/prometheus/alerts.yml`; `severity: critical` ones go to Telegram through Alertmanager |

**Public and admin are separate.** The admin pages and endpoints are not hidden behind a link: they do not
exist on the public containers (`CATEGORY_ADMIN_ROUTES=false` on `api`, no `UI_ADMIN` on `frontend`), so even a
leaked admin key cannot be used through the public address. The admin UI is the panel only: public pages redirect to `/admin`
(`frontend/proxy.ts`), except `/domain/*`, which the admin console links to. Keep `CATEGORY_ADMIN_BIND` private (`127.0.0.1` with an
SSH tunnel, a LAN address, or the VPN address); when the site goes public only `CATEGORY_PUBLIC_BIND` (behind the
TLS proxy) faces the internet. Admin CLI commands are unaffected (`$DC exec api python -m backend.cli ...`).

Optional: a service key for the UI so server-side renders are not limited like one anonymous visitor:

```bash
$DC exec -T api python -m backend.cli create-user --email ui@localhost.invalid --name "web ui" --role contributor
$DC exec -T api python -m backend.cli create-key --email ui@localhost.invalid --name web-ui --scopes community:write --rate-limit 6000
# put the printed key into .env as CATEGORY_FRONTEND_API_KEY=..., then: $DC up -d frontend
```

### Chromium sandbox (crawler) — required before the first start

The crawler runs Chromium **with its own sandbox** inside a container without capabilities. Two profiles make
that possible: `deployment/seccomp/crawler.json` (Docker's default seccomp profile + `clone`/`unshare`/`setns`/
`chroot`) and `deployment/apparmor/cat-egory-crawler` (`docker-default` + `userns`). The seccomp profile is applied
by Compose; the AppArmor profile must be installed on the host once:

```bash
sudo cp deployment/apparmor/cat-egory-crawler /etc/apparmor.d/              # loaded automatically at boot
sudo apparmor_parser -r -W /etc/apparmor.d/cat-egory-crawler                # load now
$DC exec crawler python -m crawler.selftest                                 # expect: chromium sandbox : OK
```

Verified on Ubuntu (kernel 7.0, `apparmor_restrict_unprivileged_userns=1`, Docker 29) on 2026-11-07. Without
the AppArmor profile the crawler container does not start (safe failure). Hosts that cannot load it can opt
into the weaker mode with `-f deployment/docker-compose.nosandbox.yml`; do not crawl untrusted input then.

**After a reboot or host maintenance.** Scans that fail on our side (`sandbox_unavailable`, `internal_error`,
`storage_error`, `proxy_unavailable`) never mark a site unreachable and are retried after
`scan_retry_infra_hours` (6). Seen on 2026-11-09: ~180 failures until the stack was restarted, after the crawler hit
its `pids` limit (`fork rejected by pids controller` in the kernel log), most likely from unreaped Chromium
processes; the crawler now runs with `init: true`. Check `sudo aa-status | grep cat-egory` and `$DC exec crawler python -m crawler.selftest`, then
re-queue what failed instead of waiting:

```bash
$DC exec api python -m backend.cli retry-infra-failures          # dry run (last 48 h)
$DC exec api python -m backend.cli retry-infra-failures --yes
```

## 2. Scheduled work (Celery beat)

| Task | Every | What |
|---|---|---|
| `category.scan.dispatch` | 10 s | moves queued scans to the crawler (max `CATEGORY_MAX_IN_FLIGHT_SCANS`) |
| `category.scan.reap` | 5 min | re-queues or fails scans stuck in `running` |
| `category.scan.schedule` | 5 min | queues re-scans for domains whose `next_scan` has passed (≤ 500 per run) |
| `category.feeds.build` | 1 h | rebuilds feeds; only changed files get a new version |
| `category.ingest.sync_due` | 1 h | syncs approved third-party sources whose interval elapsed |
| `category.maintenance.purge_artifacts` | 1 h | deletes screenshots and page content past `artifact_retention_days` (180), as `/privacy` promises |
| `category.discovery.queue` | 15 min | first scans of domains from discovery sources, under a daily budget (failed, blocked and cancelled scans do not use it up; pauses after 20 failures on our side in an hour) |
| `category.threats.sgb_check` | 1 h | up to 30 threat domains checked against the national SGB/USOM list (internal; `THREATS.md`) |
| `category.threats.virustotal_check` | 15 min | up to 4 threat domains looked up on VirusTotal; off without `CATEGORY_VIRUSTOTAL_API_KEY` (internal; `THREATS.md`) |
| `ct-watch` (service, not Celery) | continuous | certificate names → brand look-alikes → priority-3 scans |
| `category.maintenance.provider_health` | 5 min | configuration-level AI provider check (no paid call) |

Re-scan interval, chosen after every scan (settings `CATEGORY_SCAN_INTERVAL_*`):

| Situation | Next scan |
|---|---|
| active threat record | 1 day |
| low-confidence / invalid classification | 1 day (`CATEGORY_REANALYZE_DAYS`) |
| page title or final host changed since the previous scan | 7 days |
| unreachable (DNS / connection / robots unavailable) | 7 days |
| classified, unchanged | 30 days |
| blocked (robots.txt or SSRF policy) | 30 days |
| human-verified, unchanged | 60 days |

**Unchanged pages keep their answer.** A scheduled re-scan does not ask the model again when the page is the same
as the one behind the domain's current AI answer: same text fingerprint (visible text with numbers, case and spacing
ignored; `crawler.result.content_fingerprint`), same title and same final host. The answer must also be valid,
confident (≥ the auto-publish level), free of risk claims, injection signs and review requests, agree with the
domain's main category, and be younger than `CATEGORY_RECLASSIFY_UNCHANGED_DAYS` (default 180; 0 always asks), so
prompt and model improvements still reach old answers. Manual, community, import and admin re-scans, domains with
an active threat, look-alike candidates and domains waiting for re-analysis always reach the model
(`classification/unchanged.py`). The scan records the kept answer (`scan_results.reused_prediction_id`); the
number shows as `category_ai_answers_kept_24h` and in the daily summary ("Sayfası değişmediği için sorulmayan").
The setting is a code default and is not passed through compose; add it to the `worker` environment to change it.
Scans made by a crawler older than this feature carry no fingerprint and always reach the model.

Classification runs right after every successful crawl (`category.classify.scan`). Domains crawled before the
classifier existed are classified on their next scan (Admin → queue → *Rescan*). Feeds can be rebuilt at once
from Admin → *Rebuild feeds now* (`POST /api/v1/admin/feeds/build`).

To hide domains that came only from one source (e.g. a test import), with an audit-log entry:

```bash
$DC exec api python -m backend.cli deactivate-source-domains --source file-import         # dry run
$DC exec api python -m backend.cli deactivate-source-domains --source file-import --yes   # do it
```

After switching from the offline mock to a real model (`CATEGORY_AI_PROVIDER=evren`), re-classify what the mock
answered. Its open review tasks are closed (so nobody approves a keyword guess) and the domains are re-scanned at
priority 3; human decisions are kept:

```bash
$DC exec api python -m backend.cli reclassify-provider --provider mock          # dry run
$DC exec api python -m backend.cli reclassify-provider --provider mock --yes    # do it
```

After a taxonomy change that redefines a category (2.0.0 split `gambling` into licensed `gambling` and
`illegal-gambling`), re-classify the domains filed under it. They are re-scanned at priority 3 and the model answers
again under the new definitions. Domains a person verified go back to a person if the new answer differs:

```bash
$DC exec api python -m backend.cli reclassify-category --category gambling          # dry run: lists them
$DC exec api python -m backend.cli reclassify-category --category gambling --yes    # do it
```

**Sites closed to bots.** Some sites' robots.txt shuts out every bot: facebook, instagram, x, reddit and others.
They are never fetched. After such a blocked scan the model is asked once, from the domain name only
(`category.classify.name_only`). If it recognises the site (confidence ≥ 0.5), a `robots_blocked` review opens with
its suggestion. Nothing is published until a person approves it; threat claims are dropped. Unknown names create
no task. For sites blocked before this existed, run once:

```bash
$DC exec api python -m backend.cli suggest-robots-blocked          # dry run: lists them
$DC exec api python -m backend.cli suggest-robots-blocked --yes    # ask the model (answers land in the review queue)
```

When a later classification is applied automatically (≥ 0.90, or two models agree), open review tasks that only
said "the model was unsure" (or came from the mock) are closed as `skipped` with a note. Risk claims,
prompt-injection findings and model-requested reviews stay open for a person.

### Second opinion (0.70–0.89 band)

With `CATEGORY_EVREN_SECOND_MODEL` set (a different model than `CATEGORY_EVREN_MODEL`, e.g. `deepseek-v4.1-flash`),
an answer in the 0.70–0.89 band is shown to the second model; if both pick the same primary category it is
published (confidence = the lower of the two), otherwise it waits for a person as `model_disagreement`. Reviews
opened while no second model was set get one with (runs in the worker, which holds the key):

```bash
$DC exec worker python -m backend.cli second-opinion          # dry run
$DC exec worker python -m backend.cli second-opinion --yes    # queue them (category.classify.second_opinion)
```

### Comparing models on the evaluation sets

To see how other EVREN models would classify the evaluation sites (`classification/evaluation_set.yaml`,
`evaluation_holdout.yaml`), ask them about the stored scans. It runs in the worker (the key lives there), needs
migration `0009` (applied by `$DC up -d`), and changes nothing public: answers go to `model_comparisons` only.

```bash
$DC exec -T worker python -m classification.evaluation compare --models qwen3.8-flash-next,glm-5.3   # dry run
$DC exec -T worker python -m classification.evaluation compare --models qwen3.8-flash-next,glm-5.3 --yes \
    > model-comparison-$(date +%F).md
```

The dry run prints how many EVREN calls a real run makes; pairs with a valid answer are skipped on re-runs (so
an interrupted run resumes). Calls are sequential and count towards `CATEGORY_AI_DAILY_TOKEN_CAP`. Details and
the report's contents: [EVREN_INTEGRATION.md](EVREN_INTEGRATION.md#comparing-models-on-the-same-sites).

### Two review queues

`GET /api/v1/admin/reviews?queue=categories|threats|all`. *Threat candidates* are look-alikes (`brand_lookalike`,
`lookalike_of:<site>`) and model threat claims (`risk_claim`); everything else is a category review. Quick Review
has a selector, the dashboard counts them apart (`pending_reviews`, `pending_threat_candidates`).

### Registry allocation rules (.gov.tr, .edu.tr, …)

Some Turkish suffixes are only allocated to one kind of holder after a document check (TRABİS, "belgeli alan
adları"): `gov.tr`, `bel.tr`, `pol.tr`, `tsk.tr` → government; `edu.tr`, `k12.tr` → education; `av.tr` → legal;
`dr.tr` → health-information; `kep.tr` → email. When a scan of such a domain fails or is refused although the name
resolves (several public sites drop requests from any user agent containing "bot"), and nothing else has
classified it, the category is taken from this rule (origin `rule`, source `cat-egory-registry-rules`, no
confidence). A later AI, human or community classification replaces it. Rules: `classification/registry_rules.py`.
For domains scanned before the rules existed:

```bash
$DC exec api python -m backend.cli apply-registry-rules          # dry run
$DC exec api python -m backend.cli apply-registry-rules --yes
```

### Certificate-transparency brand watch (phishing early warning)

`certstream` (certstream-server-go v1.10.1, MIT, pinned by digest) reads the public CT logs Chrome trusts, classic
and static-ct/tiled, and relays new certificate names over the internal `ct` network. **Bandwidth ~15 Mbit/s
continuously (~150 GB/day); nothing is stored** — read-only file system, no volume, recovery/catch-up and Kafka
forwarding off (`deployment/certstream/config.yaml`). `ct-watch` (our image, no internet access) matches every name
against `integrations/ct_watchlist.yaml` (look-alike folding: Turkish letters, Cyrillic/Greek look-alikes, 0/1/l,
rn→m; official domains and .gov.tr/.edu.tr/… never match) and adds matches from the source `ct-brand-watch` with a
priority-3 scan. Each name at most once per 30 days, at most `CATEGORY_CT_WATCH_MAX_PER_HOUR` (5; one reviewer's
pace) per hour. **These domains go to the threat-candidate queue (reason `brand_lookalike`)**, however confident
the model is: a phishing copy of a bank would otherwise be published as "banking". The one exception: the model is
≥ 0.90 sure the page is parked / for sale / a placeholder and claims no risk beyond the name itself (only
`lookalike-domain` or the vague `suspicious`, which add nothing to what our own detector already established; the model's own `requires_human_review` is not
used here, only our injection detector) — then nothing is published, no review
is opened, and the domain is re-checked every 7 days (`lookalike_recheck_days`); once the page changes it comes
back to a person. (About 80 % of look-alike names are parked.) When the model is only 0.70–0.89 sure the page is
parked, the second model (`CATEGORY_EVREN_SECOND_MODEL`) is asked; if it agrees, the same happens. Older open
reviews: `$DC exec api python -m backend.cli triage-lookalikes [--yes]` (the dry run says why the rest stay open),
then `$DC exec worker python -m backend.cli second-opinion --yes` for the 0.70–0.89 ones. Liveness: heartbeat `ct-watch.stream`, alert
`CtWatchStopped`. To pause it: `$DC stop certstream ct-watch`.

ct-watch reads into a bounded queue (50k messages), matches in a separate task and writes hits in a third, so a
burst never makes certstream skip certificates ("client's buffer is full" in certstream's log means ct-watch fell
behind). Every minute it logs `stream alive` with `messages_last_minute`, `names_last_minute`, `hits_last_minute`,
`queued_last_minute`, `dropped_last_minute` (a warning if > 0) and `backlog`. Load-tested at ~5000 messages/s on
one core with zero drops (the live stream is ~1000/s).

### Look-alike domains (homographs and typos of popular sites)

`integrations/lookalike.py` protects the top `CATEGORY_LOOKALIKE_TOP_N` (1000) domains of `majestic-million-top`
without a hand-written list: a name whose label reads the same once look-alike characters are folded
(`аmazon.com` with a Cyrillic а, `amaz0n.net`, `googIe.com`) or differs by one letter with the same suffix
(`amazom.com`, `gooogle.com`, labels ≥ 6 characters) is a look-alike. TLD swaps (`amazon.de`) are not flagged:
they are usually the owner's own sites. Checked on certificate names (ct-watch) and whenever any domain is
classified; a hit always goes to the review queue (reason `lookalike_of:<target>`), where the reviewer can confirm
the risk category **`lookalike-domain`** (taxonomy 1.1.0, code 908; feed `lookalike-domain` and the RPZ). Owners
often register their own look-alikes defensively, so nothing is flagged without a person.

## 3. Third-party sources

Nothing is synced until a maintainer has read the licence and recorded it:

```bash
$DC exec api python -m integrations.cli list
$DC exec api python -m integrations.cli register urlhaus
$DC exec api python -m integrations.cli approve urlhaus --license-slug <SPDX> --license-name "<name>" \
    --license-url <url> --redistribution yes|no --commercial yes|no --attribution-required yes|no \
    --reviewer you@example.org --notes "read terms on YYYY-MM-DD"
$DC exec api python -m integrations.cli sync urlhaus
```

### Discovery (popular-site lists)

Entries with `discover: true` in `integrations/sources.yaml` only bring domains in; syncing never scans. The
`category.discovery.queue` task then queues their first scans slowly, most popular first, at priority 8 (after
manual and scheduled work). Popularity is the row in the source file, so both Majestic cuts agree. One in four of
each batch goes to names that hint at restricted content (casino, bahis, porn, vape ...; `RESTRICTED_HINT` in
`backend/app/discovery.py`), so the lists people block most fill up early. Limits:
- at most `CATEGORY_DISCOVERY_BATCH_SIZE` (15) per run;
- at most `CATEGORY_DISCOVERY_DAILY_BUDGET` (1,000) per day, Türkiye time;
- nothing while `CATEGORY_DISCOVERY_MAX_QUEUED` (30) scans are waiting;
- nothing once today's EVREN tokens reach `CATEGORY_AI_DAILY_TOKEN_CAP`. That count covers every task, not just
  discovery; 0 means no cap.

One classification uses ~7k EVREN tokens on average, so 1,000/day is ~7M tokens. The account's daily quota is not
documented here. Agreed on 2026-11-15: Cat-Egory may use 60 % of it. Read the quota on the EVREN panel and set the
cap in `.env` to 60 % of it, e.g. `CATEGORY_AI_DAILY_TOKEN_CAP=8400000` for a 14M quota. The cap is the real limit;
the domain budget only spreads the work over the day.

To watch usage, see `category_ai_tokens_today` and `category_ai_daily_token_cap` on `/metrics`. EVREN's free
period ends on 2026-12-01; check the price before keeping this pace. Large CSV feeds are streamed (a 1M-row list
needs ~15 MB), so run syncs in the `worker` container.

The first discovery sources are two views of the Majestic Million (CC BY 3.0, licence read on its download page
on 2026-11-08): `majestic-million-top` (first 20,000) and `majestic-million-tr` (all `.tr` names). After reading
the licence yourself:

```bash
for s in majestic-million-top majestic-million-tr; do
  $DC exec -T worker python -m integrations.cli register $s
  $DC exec -T worker python -m integrations.cli approve $s --license-slug CC-BY-3.0 \
      --license-name "Creative Commons Attribution 3.0 Unported" \
      --license-url https://creativecommons.org/licenses/by/3.0/ --redistribution yes --commercial yes \
      --attribution-required yes --reviewer you@example.org --notes "read majestic.com/reports/majestic-million"
  $DC exec -T worker python -m integrations.cli sync $s     # or wait for the hourly sync
done
```

Sources with `--redistribution no` are used internally only: their threat flags are not shown by the API and
never enter feeds.

## 4. Backups

```bash
sudo ./deployment/scripts/backup.sh            # ./backups: postgres-*.dump, objects-*.tar.gz, SHA256SUMS-*; keeps 14
sudo ./deployment/scripts/restore.sh backups/postgres-<stamp>.dump   # destructive, asks for confirmation
```

Nightly at 02:30 with a systemd timer (`make backup-timer`; check with `systemctl list-timers cat-egory-backup.timer`
and `journalctl -u cat-egory-backup.service`). A run that was missed while the host was off starts at the next boot.
`make restore-test` verifies the checksums and restores the newest dump into a throwaway container with no network;
the live stack is not touched; pass a directory to test a copy (`sudo ./deployment/scripts/restore-test.sh /mnt/yedek/cat-egory`).
Each run is also copied and checksummed to the second disk (`BACKUP_COPY_TO`, required) and a USB stick
(`BACKUP_COPY_OPTIONAL`, newest 3). Both are on this machine, so a fire or theft takes them all: set up the off-site
copy below.
The dump/restore round trip was tested against PostgreSQL 18; the object-store archive is a plain tarball of the SeaweedFS
volume (restore by extracting into an empty `cat-egory_objectdata` volume while `storage` is stopped).

### Off-site backup (encrypted)

After each nightly backup, `deployment/scripts/offsite-backup.sh` copies the newest dump and the object store to
a storage bucket with restic (pinned image, run through Docker).
- **Encryption:** everything is encrypted on this machine first, so the provider only ever holds ciphertext. The
  dumps contain e-mail addresses and password hashes.
- **Uploads:** the object store is backed up file by file, so each night uploads only what changed.
- **Retention:** 14 daily, 8 weekly and 12 monthly snapshots.
- **Checks:** every run verifies the repository structure.
- **Failure:** a failed upload is only a warning in the nightly run. It has its own heartbeat, and the
  **OffsiteBackupStale** alert fires after 36 hours without a successful upload.

Setup (once; Cloudflare R2 shown, B2 works too, see `deployment/offsite.env.example`):

1. In Cloudflare, go to R2 and create the bucket `cat-egory-backup`. Then under Manage API tokens, create a token
   with **Object Read & Write** on that bucket only.
2. Put the settings in a root-only file outside the repository:
   ```bash
   sudo install -d -m 700 /etc/cat-egory
   sudo install -m 600 deployment/offsite.env.example /etc/cat-egory/offsite.env
   sudo nano /etc/cat-egory/offsite.env      # account ID, access key ID, secret access key
   ```
3. Run `make offsite-init`. It creates the encryption password (`/etc/cat-egory/offsite.password`) and the
   repository. **Copy the password into your password manager**: without it the off-site copy cannot be read, and the
   password itself is not in the backup. To show it, run `sudo cat /etc/cat-egory/offsite.password`.
4. Run the first upload by hand (it can take a while): `sudo ./deployment/scripts/offsite-backup.sh backup`. Then run
   `make offsite-check`, which lists the snapshots and reads back 5 % of the data.

Restore drill, the "restore drill once" item in the checklist below. It does not touch the live stack:

```bash
sudo ./deployment/scripts/offsite-backup.sh restore latest /tmp/offsite-restore
sudo ./deployment/scripts/restore-test.sh /tmp/offsite-restore     # restores the dump into a throwaway database
sudo rm -rf /tmp/offsite-restore
```

The bucket token sits on this server, so someone who takes over the server could also delete the off-site copies.
For that case, copy a backup to a disk kept elsewhere every few months.

## 5. Monitoring

**Telegram alerts.** Set `CATEGORY_ALERT_TELEGRAM_TOKEN` (from @BotFather) and `CATEGORY_ALERT_TELEGRAM_CHAT_ID` in
`.env`, then `$DC up -d alertmanager prometheus`. Only critical alerts are sent, grouped, at most once a day per alert
while it lasts, plus a message when it ends. Messages are in Turkish: each rule's `telegram` annotation
(`deployment/prometheus/alerts.yml`), falling back to its English `summary`. Send a test:
`$DC exec alertmanager amtool alert add alertname=Test severity=critical --annotation=telegram="deneme" --alertmanager.url=http://127.0.0.1:9093`.

**Daily summary.** With the same two settings, the main worker sends one Turkish message a day at 09:00 Türkiye time:
yesterday's newly categorised sites, scans, AI answers and tokens against the cap, the
review queue, threats added and removed, abuse drafts and community reports waiting, the newest feed build and the
age of the backups. It only reads the database. Preview it, or send it now:
`$DC exec worker python -m backend.cli daily-report [--send]`. Turn it off with `CATEGORY_DAILY_REPORT_ENABLED=false`.
The token reaches the alertmanager and the main worker only.

**What visitors use.** Prometheus scrapes three targets: the admin API (`/metrics`, everything below), the public
API (`api:9100`, request counters and process memory only; the port is not published and the tunnel does not forward
it) and the public site (`frontend:3000/healthz`). **PublicApiDown** and **SiteDown** fire after 5 minutes without
an answer; the 5xx alert counts both API instances, including crashes (an unhandled error is answered with the
normal error envelope and counted). Prometheus runs on the same machine, so it cannot report the machine or the
home connection being down: for that, turn on Cloudflare's *Tunnel health* notification (Notifications → Add) or any
external uptime check of the public address.

`/metrics` on the admin API exports HTTP metrics and database-derived gauges (cached 30 s):
`category_domains{status}`, `category_scan_jobs{status}`, `category_open_reviews`,
`category_pending_submissions`, `category_active_threats`, `category_ai_predictions_24h{status}`,
`category_feed_age_seconds`, `category_fast_lane_oldest_seconds`, `category_db_metrics_up`, and from Redis
`category_task_last_success_timestamp_seconds{task}` (written by the worker after every successful Celery task)
with `category_heartbeats_up`. Alerts: API down, public API down, site down, DB metrics down, scan backlog, stale feeds, > 20 % invalid AI
answers, API 5xx, **scheduler stopped** (no scan dispatch for 5 minutes: beat or the worker is down), scheduler
never ran, discovery stopped for an hour, heartbeats unreadable, **fast lane stuck** (a suspected threat waited
over 20 minutes).

**Threat fast lane.** Scan jobs at priority 0–2 (`CATEGORY_FAST_LANE_MAX_PRIORITY`): CT look-alikes, Octryx
findings, and scans a person queues at that priority. They may use one crawl slot above
`CATEGORY_MAX_IN_FLIGHT_SCANS` (`CATEGORY_FAST_LANE_EXTRA_SLOTS`). Their scan and classification steps run on
the `fast` queue, which the `worker-fast` container works through on its own. Routine scanning also has its own
`worker-scan` container (queue `scan`: dispatch, DNS pre-check, storing results). Long AI calls on the main
`worker` cannot starve it; on 2026-11-15 they did, and scanning stopped for an hour (**scheduler stopped** alert). The crawler reads `crawl-fast`
before `crawl`.

Celery beat stores its last-run times in the `beatdata` volume, so a restart or rebuild does not delay hourly
tasks (feeds, source syncs) by up to an hour.

## 6. Upgrades

```bash
git pull
$DC up -d --build        # `migrate` applies new Alembic migrations before api/worker start
```

Roll back a migration with `$DC run --rm migrate alembic downgrade -1` (take a backup first).

## 7. Production checklist

- [ ] Publish through Cloudflare Tunnel; the admin side stays on a private address (see "Going public" below).
- [x] UI CSP `connect-src` allows only this container's API origin (`frontend/proxy.ts`).
- [x] **Chromium sandbox**: crawler AppArmor profile installed in `/etc/apparmor.d`, `crawler.selftest` reports OK.
- [x] Request bodies are capped (1 MB on the public API), wrong API keys are throttled, the site's fetch cache
      cannot grow with visitor input (pre-launch review 2026-11-15, `tests/integration/test_hardening.py`).
- [ ] Public containers off the `edge` network (`docker-compose.public.yml`, step 5 below).
- [ ] Host firewall: drop egress to private ranges from the Docker bridge networks (defence in depth).
- [ ] Off-host backups (`offsite-backup.sh`, set up per section 4); restore drill once.
- [x] Alertmanager wired to Telegram (`CATEGORY_ALERT_TELEGRAM_CHAT_ID`; test message received 2026-11-09).
- [ ] EVREN: terms questions answered (`EVREN_INTEGRATION.md`), live checklist done. (Project-specific key: done 2026-11-08.)
- [ ] Legal review of the data licence (CC BY 4.0 for own data) and of each enabled source.

### Going public

The Cloudflare Tunnel dials out, so no port is opened at home. `cloudflared` sits on the `tunnel` network with only
`frontend` and `api`, and has a fixed address, the only one whose `CF-Connecting-IP` header the public API believes.
Its way out to Cloudflare is a network of its own (`tunnelout`), shared with nothing. Admin containers never join
either network.

1. In Cloudflare Zero Trust create a tunnel and copy its token to `CATEGORY_TUNNEL_TOKEN` in `.env`. Public hostnames:
   the site to `http://frontend:3000`, the API to `http://api:8000`. Nothing else: no hostname for `admin-ui`,
   `admin-api`, Grafana, Prometheus or `api:9100`.
2. In `.env`: `CATEGORY_PUBLIC_API_URL` and `CATEGORY_CORS_ORIGINS` to the HTTPS origins (no trailing slash),
   `CATEGORY_CONTACT_EMAIL` and `CATEGORY_OPERATOR_NAME` set, and a `CATEGORY_FRONTEND_API_KEY` (section 1).
3. The admin side stays on a private address and is never given a tunnel hostname. Either the home network
   (`CATEGORY_ADMIN_BIND=<LAN address>`, reachable from home only) or the VPN: `make docker-after-vpn`, then
   `CATEGORY_ADMIN_BIND=<VPN address>`, `CATEGORY_ADMIN_API_URL=http://<VPN address>:8111`,
   `CATEGORY_ADMIN_CORS_ORIGINS=["http://<VPN address>:3012"]`.
4. `$DC --profile public up -d --build`, then `$DC restart prometheus` (new scrape jobs and alerts).
5. Once the public address works, take the public containers off the `edge` network:
   `$DC -f deployment/docker-compose.public.yml --profile public up -d`. From then on `:3010` and `:8110` no longer
   answer on the server's own address (use the public one), and a compromised public container has no route to the
   home network or the internet. Keep the extra `-f` in every later `up`; without it the ports come back.

Check from outside: `/admin` on the site is a 404; `/metrics` and `/api/v1/admin/me` on the API are 404; `:3012`,
`:8111`, `:3011` and `:9091` do not answer from the internet; `https://<api>/readyz` says `ok`. In Prometheus
(Status → Targets) all three jobs are up.

**Cloudflare settings** (dashboard, not in this repository):

- SSL/TLS: *Always Use HTTPS* on. The site and the API already send `Strict-Transport-Security`.
- A rate-limiting rule on the site's hostname (Security → WAF → Rate limiting rules), for example 120 requests per
  10 seconds per IP → block. Page renders on the server use one service key for all visitors, so the API's
  per-visitor limit does not apply to them; this rule is the per-visitor limit for pages. The API hostname has its
  own limits (60 reads and 10 writes a minute per visitor, 20 wrong keys a minute).
- Notifications: *Tunnel health* to your e-mail. It is the only alert that still works when the server is off.
- Two-factor authentication on the Cloudflare account: whoever controls it controls where the tunnel points.
- Indexing is a choice, not a default here: there is no `robots.txt`, so search engines may index every
  `/domain/<name>` page they find a link to.

**Limits that protect the public API** (defaults; `common/config.py`): request bodies over 1 MB are refused with 413
before they are read (`CATEGORY_MAX_REQUEST_BYTES`; the admin instance allows uploads up to
`CATEGORY_MAX_UPLOAD_BYTES`); after 20 wrong API keys in a minute a client gets 429 instead of 401
(`CATEGORY_AUTH_FAILURE_LIMIT_PER_MINUTE`). These are not passed through `docker-compose.yml`; add them to the `api`
service's environment if you ever need other values.

## 8. Performance (measured 2026-11-07)

Local, single uvicorn worker, PostgreSQL 18, load generator on the same 4-core host, 20 concurrent clients:

| Endpoint | req/s | p50 | p95 |
|---|---|---|---|
| `/healthz` (no DB, framework baseline) | 339 | 46 ms | 130 ms |
| `/api/v1/domain/{domain}` | 142 | 137 ms | 196 ms |
| `/api/v1/url?url=…` | 130 | 149 ms | 211 ms |
| `/api/v1/categories/{id}/domains` | 172 | 113 ms | 161 ms |
| `/api/v1/stats` | 102 | 190 ms | 267 ms |

These are modest single-process numbers. Scale-out path: several API replicas behind the proxy, HTTP caching
(responses carry `ETag` / `Cache-Control`; a CDN can serve lookups and feeds), Redis read-through cache for hot
domains, and `/stats` precomputed by a beat task. Bulk import measured at ≈ 2.5 s per 10 000 domains.
