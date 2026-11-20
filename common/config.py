"""Runtime settings, read from environment variables (prefix ``CATEGORY_``) or an optional ``.env`` file.

Secrets are never given defaults. Anything that looks like a credential must come from the environment
or a Docker secret file.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, IPvAnyNetwork, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CATEGORY_", env_file=".env", env_file_encoding="utf-8", extra="ignore", secrets_dir=None
    )

    environment: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"

    # storage
    database_url: str = "postgresql+psycopg://category:category@localhost:5432/category"
    redis_url: str = "redis://localhost:6379/0"
    s3_endpoint_url: str | None = None
    s3_bucket: str = "category-artifacts"
    s3_assume_bucket: bool = Field(default=False, description="skip HeadBucket/CreateBucket (the crawler's key may "
                                                               "only write objects; the core creates the bucket)")
    s3_access_key: SecretStr | None = None
    s3_secret_key: SecretStr | None = None

    # AI provider: "mock" until a real provider is configured and verified
    ai_provider: Literal["mock", "evren"] = "mock"
    evren_base_url: str = "https://evren-llmapi.ssyz.org.tr"
    evren_api_key: SecretStr | None = Field(default=None, description="Bearer token for the EVREN LLM gateway")
    evren_model: str = "qwen3.8-flash-next"
    evren_timeout_seconds: float = Field(default=180.0, description="read timeout of one HTTP request to EVREN")
    evren_max_output_tokens: int = Field(
        default=3000, description="answer token limit; 1500 cut two of 35 real answers short (2026-11-08)")
    evren_deadline_seconds: float = Field(
        default=270.0, description="total time for one EVREN call incl. retries and waiting for an in-flight request; "
                                   "keep 2x below the classify task's 600 s soft limit (second opinion)")
    evren_json_mode: bool = Field(
        default=False,
        description="Send response_format={'type':'json_object'}. Off until the gateway is verified to support it.",
    )

    # classification
    taxonomy_path: str | None = None  # defaults to the bundled classification/taxonomy.yaml
    evren_second_model: str | None = Field(
        default=None, description="Second EVREN model asked for a second opinion between 0.70 and the publish threshold")
    classify_use_screenshot: bool = True
    confidence_auto_approve: float = 0.90  # sensitive categories (classification/confidence.py SENSITIVE)
    confidence_auto_approve_ordinary: float | None = Field(
        default=0.80, description="publish from this confidence outside sensitive categories (calibration of "
                                  "2026-11-20); empty = confidence_auto_approve everywhere")
    confidence_extra_validation: float = 0.70
    confidence_human_review: float = 0.50
    reanalyze_days: int = 1
    # Demand (backend/app/demand.py): looked-up domains and list downloads as daily totals, never who asked.
    demand_counting: bool = Field(default=True, description="count looked-up domains and feed downloads")
    demand_min_callers: int = Field(default=3, ge=1, description="distinct visitors a day before an unknown domain "
                                    "joins discovery")
    demand_daily_cap: int = Field(default=100, ge=0, description="unknown domains added from demand per day (0 = none)")
    demand_max_names_per_day: int = Field(default=50_000, ge=100, description="distinct names counted a day (Redis "
                                          "memory bound; later names that day are not counted)")
    demand_keep_days: int = Field(default=90, ge=7, description="daily domain totals kept this long")
    # Yaş Mama, the local classifier (classification/yasmama.py): learns from published labels. Shadow mode only: it
    # never publishes. Off until switched on; the weekly training runs only while it is on.
    local_shadow: bool = Field(default=False, description="record the local classifier's guess next to every AI answer")
    local_train_on_ai_labels: bool = Field(default=True, description="learn from confident AI labels too, not only "
                                           "from people's (turn off if the AI provider's terms forbid it)")
    local_train_hours: int = Field(default=168, ge=1, description="retrain this often while shadow mode is on")
    local_min_examples: int = Field(default=5, ge=2, description="categories with fewer training pages are left out")
    local_min_train: int = Field(default=200, ge=10, description="do not train on fewer pages than this")
    local_max_train: int = Field(default=20_000, ge=100,
                                 description="newest pages used for training (worker memory: ~450 MB at 20,000)")
    local_text_chars: int = Field(default=2000, ge=200, description="characters of visible text kept per page")
    local_class_balance: float = Field(
        default=0.5, ge=0, le=1, description="weigh categories against their size when training: 0 = as they come "
        "(the big ones win: news, government, business), 1 = every category counts the same, 0.5 = in between")
    local_keep_models: int = Field(default=4, ge=1, description="trained models whose weights are kept")
    reclassify_unchanged_days: int = Field(
        default=180, ge=0, description="a scheduled re-scan whose page has not changed keeps the AI answer instead of "
        "asking the model again, until the answer is this many days old (0 = always ask)")
    # Community reputation (backend/app/api/community.py): an account's corrections apply at once after this record.
    community_trust_min_accepted: int = Field(default=10, ge=1, description="moderator-accepted submissions needed")
    community_trust_min_share: float = Field(default=0.9, ge=0, le=1, description="accepted / all moderated")
    dispute_threshold: int = Field(default=3, description="distinct reporters needed before a domain shows as disputed")
    max_text_chars: int = 12_000
    max_html_chars: int = 6_000

    # public API & rate limits (requests per minute)
    public_rate_limit_per_minute: int = 60
    anonymous_write_limit_per_minute: int = 10
    api_key_rate_limit_per_minute: int = 600
    # Self-service developer keys (backend/app/api/developer.py): read-only, no personal data asked.
    developer_key_per_minute: int = Field(default=120, ge=1)
    bulk_lookup_max_anonymous: int = Field(default=100, ge=1, description="names per POST /lookup without a key")
    bulk_lookup_max_with_key: int = Field(default=1000, ge=1, description="names per POST /lookup with a key")
    developer_key_daily_quota: int = Field(default=20_000, ge=1)
    developer_key_days: int = Field(default=365, ge=1, description="a key expires after this many days")
    developer_keys_per_client_per_day: int = Field(default=3, ge=1)
    developer_keys_per_day: int = Field(default=200, ge=1, description="all clients together")
    auth_failure_limit_per_minute: int = Field(
        default=20, ge=1, description="wrong API keys from one client per minute before it gets 429 instead of 401")
    max_request_bytes: int = Field(
        default=1024 * 1024, ge=1024, description="largest request body the API reads (the admin instance also "
                                                  "allows max_upload_bytes for file uploads)")
    rate_limit_fail_open: bool = Field(
        default=True, description="If Redis is unreachable, allow requests (logged) instead of rejecting them."
    )
    cors_origins: list[str] = []
    trusted_proxies: list[IPvAnyNetwork] = Field(
        default=[], description="connections from these addresses may state the visitor's IP (CF-Connecting-IP)")
    metrics_port: int | None = Field(
        default=None, ge=1024, le=65535,
        description="public API only: serve the HTTP request counters for Prometheus on this extra port (not "
                    "published, not behind the tunnel). The admin instance has /metrics instead")
    admin_routes: bool = Field(
        default=False, description="serve /api/v1/admin/* and /metrics. Off on the public API; on only for the "
                                   "admin instance, which is bound to a private address (CATEGORY_ADMIN_BIND)")

    # imports
    max_import_rows: int = 10_000
    max_upload_bytes: int = 5 * 1024 * 1024

    # crawler (Phase 3)
    crawler_proxy_url: str | None = Field(default=None, description="egress proxy, e.g. http://egress-proxy:3128")
    egress_proxy_token: SecretStr | None = None
    crawler_chromium_sandbox: bool = True
    crawler_allow_no_sandbox: bool = Field(
        default=False, description="If the Chromium sandbox cannot start, run without it (container isolation only)."
    )
    crawler_navigation_timeout_ms: int = 20_000
    crawler_max_requests_per_page: int = 300
    crawler_max_html_bytes: int = 2 * 1024 * 1024
    crawler_max_text_chars: int = 100_000
    crawler_user_agent_token: str = "CategoryBot/0.1"  # noqa: S105 - product token, not a secret
    crawler_respect_robots: bool = True
    crawler_viewport_width: int = 1280
    crawler_viewport_height: int = 800
    max_in_flight_scans: int = 4
    # threat fast lane: jobs at this priority or more urgent (lower number) get their own queues and worker,
    # and may use extra crawl slots, so a suspected phishing site is not stuck behind routine scans
    fast_lane_max_priority: int = 2
    fast_lane_extra_slots: int = 1
    # re-scan scheduling (days until the next scan, chosen after every scan)
    scan_interval_days: int = 30            # classified, nothing notable
    scan_interval_verified_days: int = 60   # human-verified and unchanged
    scan_interval_changed_days: int = 7     # title or final host changed since the previous scan
    app_utc_offset_hours: int = Field(default=3, ge=-12, le=14, description="\"today\" and daily totals; Türkiye is GMT+3")
    scan_interval_risk_days: int = 1        # active threat record: re-check daily
    threat_reclassify_days: int = Field(default=30, ge=1, description="domains with a confirmed threat ask the AI "
                                                                      "again only after this many days")
    threat_expire_days: int = Field(default=14, ge=1, description="a confirmed threat whose site has been gone this "
                                                                  "long is deactivated (threats/lifecycle.py)")
    # abuse-report drafts (threats/abuse.py): prepared hourly, sent only by a person
    abuse_drafts_enabled: bool = False  # off for now (no admin tab); the drafts code stays
    abuse_drafts_batch: int = Field(default=10, ge=1, le=100)
    abuse_report_repeat_days: int = Field(default=30, ge=1, description="draft again for a domain after this long")
    public_site_url: str = "https://cat-egory.org"
    # Daily summary in Turkish on Telegram (backend/app/report_tasks.py), one message a day, from the worker. Same bot
    # and chat as the alerts (CATEGORY_ALERT_TELEGRAM_*); both empty = nothing is sent.
    alert_telegram_token: SecretStr | None = None
    alert_telegram_chat_id: str | None = None
    daily_report_enabled: bool = True
    daily_report_hour: int = Field(default=9, ge=0, le=23, description="hour of the day (Türkiye time) it is sent")
    threat_change_cooldown_days: int = Field(default=7, ge=1, description="a flagged page that changed goes to a "
                                                                          "person at most this often (cloaking flips)")
    scan_interval_unreachable_days: int = 7 # broken domains are re-checked weekly
    scan_interval_blocked_days: int = 30    # robots.txt / SSRF policy refused
    scan_retry_infra_hours: int = 6         # our sandbox / crawler / storage failed: try again soon
    # certificate-transparency brand watch (integrations/ct_watch.py)
    certstream_url: str = "ws://certstream:8080/domains-only"
    lookalike_top_n: int = Field(default=1000, ge=0, description="protect the top N domains of majestic-million-top "
                                                              "against homographs and typos; 0 = off")
    # One reviewer can look at a handful of threat candidates an hour; more only grows the queue.
    ct_watch_max_per_hour: int = Field(default=5, ge=1, description="look-alike domains queued per hour at most")
    lookalike_recheck_days: int = Field(default=7, ge=1, description="re-check interval for parked look-alikes")
    # cross-check with the national malicious-address list (threats/sgb.py); internal use only
    sgb_check_enabled: bool = True
    sgb_check_batch: int = Field(default=30, ge=1, le=200, description="domains asked per hourly run")
    sgb_check_max_age_hours: int = Field(default=24, ge=1)
    sgb_check_pause_seconds: float = Field(default=1.0, ge=0)
    # VirusTotal lookups for reviewers (threats/virustotal.py); off without a key. Worker only.
    # Findings from the sister project Octryx (threats/octryx.py); off without both. Token: worker only.
    octryx_url: str | None = None
    octryx_token: SecretStr | None = None
    octryx_auto_flag_max_per_run: int = Field(default=20, ge=0, description="the rest of a run goes to a person")
    # Third-party threat lists as candidates only (threats/feeds.py): hosts are scanned and a person decides.
    abusech_auth_key: SecretStr | None = Field(default=None, description="abuse.ch Auth-Key (URLhaus export); "
                                               "goes into the download path, never into the database. Worker only")
    threat_feed_candidates_per_hour: int = Field(default=6, ge=0, description="hosts from threat lists queued for "
                                                 "a scan per hour, Türkiye-related first; 0 = off")
    threat_feed_min_relevance: int = Field(default=1, ge=0, le=2, description="pick only hosts this related to "
                                           "Türkiye: 2 = resembles a watched brand, 1 = also .tr names, 0 = any")
    threat_feed_global_per_hour: int = Field(default=12, ge=0, description="worldwide phishing candidates (a watched "
                                             "global brand in the name, or a link a list gives) queued per hour, "
                                             "newest first, judged without the AI; a person sees one only when its "
                                             "page asks for a secret or a phone number; 0 = off")
    threat_page_scans: bool = Field(default=True, description="scan the exact page a phishing list names (its link, "
                                    "query string dropped) instead of the host's home page")
    # Our own page-proof detector (threats/detector.py): measured in shadow mode, flags only when allowed AND proven.
    threat_detector_auto: bool = Field(default=False, description="let the detector flag by itself once its measured "
                                       "precision qualifies (threat_detector_min_*)")
    threat_detector_min_decisions: int = Field(default=200, ge=20, description="people's decisions on its positive "
                                               "verdicts needed before it may flag")
    threat_detector_min_precision: float = Field(default=0.99, ge=0.9, le=1.0)
    threat_detector_max_per_day: int = Field(default=20, ge=0, description="flags it may add a day, Türkiye time")
    threat_detector_sample_every: int = Field(default=10, ge=2, description="every Nth flag it adds still goes to a "
                                              "person, so its precision keeps being measured")
    virustotal_api_key: SecretStr | None = Field(default=None, description="VirusTotal API key; empty = off")
    # USOM report e-mails (threats/usom.py): sent by the worker through Resend when a person presses send
    usom_mail_from: str = Field(default="", description='sender, e.g. "Cat-Egory <ihbar@cat-egory.org>"; empty = off')
    usom_mail_to: str = "ihbar@siberguvenlik.gov.tr"
    usom_mail_reply_to: str = ""
    resend_api_key: SecretStr | None = Field(default=None, description="Resend API key for the USOM e-mails")
    virustotal_check_batch: int = Field(default=4, ge=1, le=40, description="domains asked per run (every 15 minutes)")
    virustotal_check_max_age_hours: int = Field(default=72, ge=1)
    virustotal_check_pause_seconds: float = Field(default=16.0, ge=0, description="the public API allows 4 a minute")
    # discovery (first scans of domains from discovery sources; backend/app/discovery.py)
    discovery_daily_budget: int = Field(default=1000, ge=0, description="new domains per day that can reach the "
                                                                        "AI (failed scans do not count); 0 = off")
    discovery_batch_size: int = Field(default=15, ge=1, description="new domains queued per 15-minute run")
    discovery_max_queued: int = Field(default=30, ge=1, description="pause while this many scans are waiting")
    ai_daily_token_cap: int = Field(default=0, ge=0, description="EVREN tokens (in + out) per day, Türkiye time, after "
                                                                 "which discovery pauses; other work goes on. "
                                                                 "0 = no cap. Set it to the share of the account's "
                                                                 "daily quota Cat-Egory may use")
    discovery_pause_after_infra_failures: int = Field(default=20, ge=1,
                                                      description="pause while the last hour has this many failures "
                                                                  "on our side (sandbox, storage, ...)")
    schedule_batch_size: int = 500          # scans queued per scheduler run
    artifact_retention_days: int = 180
    stale_scan_minutes: int = 15

    # keyed hash for client fingerprints (IP addresses are never stored in clear). Unset = no fingerprints.
    fingerprint_secret: SecretStr | None = None
    admin_mfa_required: bool = Field(default=False, description="refuse password sign-in to staff accounts without "
                                                                 "a second factor (python -m backend.cli mfa-enroll)")


@lru_cache
def get_settings() -> Settings:
    return Settings()
