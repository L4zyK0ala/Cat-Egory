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
        default=None, description="Second EVREN model asked for a second opinion in the 0.70-0.89 band")
    classify_use_screenshot: bool = True
    confidence_auto_approve: float = 0.90
    confidence_extra_validation: float = 0.70
    confidence_human_review: float = 0.50
    reanalyze_days: int = 1
    dispute_threshold: int = Field(default=3, description="distinct reporters needed before a domain shows as disputed")
    max_text_chars: int = 12_000
    max_html_chars: int = 6_000

    # public API & rate limits (requests per minute)
    public_rate_limit_per_minute: int = 60
    anonymous_write_limit_per_minute: int = 10
    api_key_rate_limit_per_minute: int = 600
    rate_limit_fail_open: bool = Field(
        default=True, description="If Redis is unreachable, allow requests (logged) instead of rejecting them."
    )
    cors_origins: list[str] = []
    trusted_proxies: list[IPvAnyNetwork] = Field(
        default=[], description="connections from these addresses may state the visitor's IP (CF-Connecting-IP)")
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
    virustotal_api_key: SecretStr | None = Field(default=None, description="VirusTotal API key; empty = off")
    virustotal_check_batch: int = Field(default=4, ge=1, le=40, description="domains asked per run (every 15 minutes)")
    virustotal_check_max_age_hours: int = Field(default=72, ge=1)
    virustotal_check_pause_seconds: float = Field(default=16.0, ge=0, description="the public API allows 4 a minute")
    # discovery (first scans of domains from discovery sources; backend/app/discovery.py)
    discovery_daily_budget: int = Field(default=200, ge=0, description="new domains per day that can reach the "
                                                                       "AI (failed scans do not count); 0 = off")
    discovery_batch_size: int = Field(default=10, ge=1, description="new domains queued per 15-minute run")
    discovery_max_queued: int = Field(default=20, ge=1, description="pause while this many scans are waiting")
    discovery_pause_after_infra_failures: int = Field(default=20, ge=1,
                                                      description="pause while the last hour has this many failures "
                                                                  "on our side (sandbox, storage, ...)")
    schedule_batch_size: int = 500          # scans queued per scheduler run
    artifact_retention_days: int = 180
    stale_scan_minutes: int = 15

    # keyed hash for client fingerprints (IP addresses are never stored in clear). Unset = no fingerprints.
    fingerprint_secret: SecretStr | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()
