"""The daily summary on Telegram, in Turkish: yesterday in numbers (Türkiye day) and what waits for a person.

One message a day at ``daily_report_hour`` (09:00, Türkiye time), to the same bot and chat as the alerts. The token
reaches only the main worker. Read-only: it counts and changes nothing. Preview it without sending:
``python -m backend.cli daily-report``.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta, timezone

import httpx
import redis
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from classification.engine import threat_candidate
from common.config import get_settings
from common.timeutil import local_midnight
from database.models import (
    AbuseReport,
    AIPrediction,
    ClassificationStatus,
    CommunitySubmission,
    Domain,
    DomainCategory,
    FalsePositiveReport,
    FeedRelease,
    JobStatus,
    ModerationStatus,
    ReviewStatus,
    ReviewTask,
    ScanJob,
    ScanReason,
    ScanResult,
    ThreatIntelligence,
    ValidationStatus,
)
from database.session import session_factory

from .discovery import tokens_since
from .worker import HEARTBEAT_KEY, celery_app

log = logging.getLogger("category.report")
# The bot token is part of Telegram's URL, and httpx logs every request URL at INFO: never let that reach the logs.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

SENT_KEY = "category:daily-report:{day}"  # one message per day, even if beat fires twice
MONTHS = ("Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık")
DAYS = ("Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar")
# The review queue's own words (frontend/components/QuickReview.tsx), shortened.
REASONS = {
    "verification_sample": "doğrulama örneği", "brand_lookalike": "marka taklidi", "low_confidence": "model emin değil",
    "model_requested_review": "model sordu", "needs_extra_validation": "ikinci görüş", "model_disagreement": "modeller ayrıştı",
    "prompt_injection": "sayfa modeli yönlendiriyor", "risk_claim": "tehdit iddiası",
    "conflicts_with_human_decision": "insan kararıyla çelişki", "threat_returned": "geri dönen tehdit",
    "threat_page_changed": "tehdit sayfası değişti", "robots_blocked": "botlara kapalı (ad önerisi)",
}


class TelegramError(Exception):
    """Never carries the request URL: it contains the bot token."""


def num(n: int | float) -> str:
    return f"{int(n):,}".replace(",", ".")


def tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}".replace(".", ",") + " milyon"
    if n >= 1_000:
        return f"{round(n / 1_000)} bin"
    return str(n)


def share(part: int, whole: int) -> str:
    return f"%{round(100 * part / whole)}" if whole else "—"


def reason_label(reason: str) -> str:
    return "marka taklidi" if reason.startswith("lookalike_of:") else REASONS.get(reason, reason)


def ago(seconds: float) -> str:
    hours = int(seconds // 3600)
    return f"{hours} saat önce" if hours else f"{int(seconds // 60)} dakika önce"


def build(session: Session, now: datetime, heartbeats: dict[str, float] | None = None) -> str:
    """Yesterday (00:00-24:00 Türkiye time) in numbers, plus the state right now."""
    settings = get_settings()
    zone = timezone(timedelta(hours=settings.app_utc_offset_hours))
    end = local_midnight(now, settings.app_utc_offset_hours)
    start = end - timedelta(days=1)
    in_day = lambda column: and_(column >= start, column < end)  # noqa: E731

    def count(stmt) -> int:
        return int(session.scalar(stmt) or 0)

    def grouped(column, *where) -> dict:
        return {str(k): int(n) for k, n in session.execute(select(column, func.count()).where(*where).group_by(column))}

    # ---- sites
    active = Domain.is_active.is_(True)
    total = count(select(func.count()).select_from(Domain).where(active))
    categorised = count(select(func.count()).select_from(Domain).where(active, Domain.primary_category_id.is_not(None)))
    first = (select(DomainCategory.domain_id).where(DomainCategory.is_primary.is_(True))
             .group_by(DomainCategory.domain_id).having(func.min(DomainCategory.valid_from) >= start)
             .having(func.min(DomainCategory.valid_from) < end).subquery())
    new_categorised = count(select(func.count()).select_from(first))
    status = grouped(Domain.classification_status, active)
    verified = status.get(ClassificationStatus.VERIFIED.value, 0)
    unreachable = status.get(ClassificationStatus.UNREACHABLE.value, 0)
    waiting = status.get(ClassificationStatus.UNCLASSIFIED.value, 0) + status.get(ClassificationStatus.REANALYZE.value, 0)

    # ---- scans
    finished = grouped(ScanJob.status, in_day(ScanJob.finished_at))
    ok, blocked = finished.get(JobStatus.SUCCEEDED.value, 0), finished.get(JobStatus.BLOCKED.value, 0)
    failed = finished.get(JobStatus.FAILED.value, 0)
    discovered = count(select(func.count()).select_from(ScanJob).where(ScanJob.reason == ScanReason.DISCOVERY,
                                                                       in_day(ScanJob.queued_at)))
    queued = count(select(func.count()).select_from(ScanJob).where(ScanJob.status == JobStatus.QUEUED))

    # ---- AI
    answers = grouped(AIPrediction.validation_status, in_day(AIPrediction.created_at), AIPrediction.provider != "mock")
    asked = sum(answers.values())
    invalid = asked - answers.get(ValidationStatus.VALID.value, 0)
    used = tokens_since(session, start) - tokens_since(session, end)
    kept = count(select(func.count()).select_from(ScanResult).where(ScanResult.reused_prediction_id.is_not(None),
                                                                    in_day(ScanResult.created_at)))

    # ---- reviews
    open_ = ReviewTask.status == ReviewStatus.OPEN
    open_total = count(select(func.count()).select_from(ReviewTask).where(open_))
    open_threats = count(select(func.count()).select_from(ReviewTask).where(open_, threat_candidate()))
    opened = count(select(func.count()).select_from(ReviewTask).where(in_day(ReviewTask.created_at)))
    decided = count(select(func.count()).select_from(ReviewTask).where(ReviewTask.status == ReviewStatus.DONE,
                                                                       in_day(ReviewTask.decided_at)))
    top = session.execute(select(ReviewTask.reason, func.count().label("n")).where(open_).group_by(ReviewTask.reason)
                          .order_by(func.count().desc(), ReviewTask.reason).limit(3)).all()

    # ---- threats, community
    threats_active = count(select(func.count()).select_from(ThreatIntelligence).where(ThreatIntelligence.is_active))
    threats_new = count(select(func.count()).select_from(ThreatIntelligence).where(in_day(ThreatIntelligence.first_seen)))
    threats_gone = count(select(func.count()).select_from(ThreatIntelligence)
                         .where(in_day(ThreatIntelligence.deactivated_at)))
    drafts = count(select(func.count()).select_from(AbuseReport).where(AbuseReport.status == "draft"))
    community = (count(select(func.count()).select_from(CommunitySubmission)
                       .where(CommunitySubmission.status == ModerationStatus.PENDING))
                 + count(select(func.count()).select_from(FalsePositiveReport)
                         .where(FalsePositiveReport.status == ModerationStatus.PENDING)))

    # ---- feeds
    feed = session.execute(select(FeedRelease.generated_at, FeedRelease.version)
                           .order_by(FeedRelease.generated_at.desc()).limit(1)).first()

    day = start.astimezone(zone)
    lines = [f"📊 Cat-egory · {day.day} {MONTHS[day.month - 1]} {day.year} {DAYS[day.weekday()]} özeti", ""]
    lines += ["🗂 Siteler",
              f"• Kategorili: {num(categorised)} / {num(total)} (dün +{num(new_categorised)})",
              f"• İnsan onaylı: {num(verified)} · ulaşılamayan: {num(unreachable)} · taranmayı bekleyen: {num(waiting)}",
              ""]
    lines += ["🔎 Tarama (dün)",
              f"• Biten: {num(ok + blocked + failed)} · başarılı {num(ok)}, engelli {num(blocked)}, hatalı {num(failed)}",
              f"• Keşiften gelen yeni site: {num(discovered)} · şu an sırada: {num(queued)}",
              ""]
    cap = settings.ai_daily_token_cap
    token_line = f"• Token: {tokens(used)}" + (f" / {tokens(cap)} sınır ({share(used, cap)})" if cap else "")
    lines += ["🤖 Yapay zekâ (dün)",
              f"• Yanıt: {num(asked)} · geçersiz ya da hatalı: {num(invalid)} ({share(invalid, asked)})",
              f"• Sayfası değişmediği için sorulmayan: {num(kept)}",
              token_line,
              ""]
    lines += ["🧑‍⚖️ İnceleme",
              f"• Bekleyen: {num(open_total)} (tehdit sorusu {num(open_threats)})",
              f"• Dün açılan {num(opened)}, karara bağlanan {num(decided)}"]
    if top:
        lines.append("• En çok: " + ", ".join(f"{reason_label(r)} {num(n)}" for r, n in top))
    lines.append("")
    lines += ["🛡 Tehditler",
              f"• Etkin: {num(threats_active)} (dün +{num(threats_new)} yeni, −{num(threats_gone)} kaldırıldı)",
              f"• Gönderilmeyi bekleyen kötüye kullanım taslağı: {num(drafts)}",
              f"• Topluluktan bekleyen bildirim: {num(community)}",
              ""]
    lines.append("📦 Listeler ve yedek")
    if feed:
        lines.append(f"• Son liste derlemesi: {feed.generated_at.astimezone(zone):%H:%M} (sürüm {feed.version})")
    else:
        lines.append("• Henüz liste derlenmedi")
    beats = heartbeats or {}
    for key, label, stale_hours in (("backup.nightly", "Yedek", 30), ("backup.offsite", "Sunucu dışı yedek", 36)):
        if key not in beats:
            lines.append(f"• {label}: kayıt yok" if key == "backup.nightly" else f"• {label}: kurulmadı")
            continue
        age = now.timestamp() - beats[key]
        lines.append(f"• {label}: {ago(age)}" + (" ⚠️" if age > stale_hours * 3600 else " ✓"))
    return "\n".join(lines)


def read_heartbeats() -> dict[str, float]:
    try:
        raw = redis.Redis.from_url(get_settings().redis_url, socket_timeout=2).hgetall(HEARTBEAT_KEY)
    except redis.RedisError:
        return {}
    return {(k.decode() if isinstance(k, bytes) else k): float(v) for k, v in raw.items()}


def send_telegram(text: str, token: str, chat_id: str, client: httpx.Client | None = None) -> None:
    own = client is None
    client = client or httpx.Client(timeout=20)
    try:
        r = client.post(f"https://api.telegram.org/bot{token}/sendMessage",
                        json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True})
    except httpx.HTTPError as exc:
        raise TelegramError(f"Telegram unreachable: {type(exc).__name__}") from None  # no chained URL
    finally:
        if own:
            client.close()
    if r.status_code != 200:
        raise TelegramError(f"Telegram answered {r.status_code}")


def run_daily_report(now: datetime | None = None, *, client: httpx.Client | None = None,
                     store: redis.Redis | None = None) -> str:
    settings = get_settings()
    token, chat = settings.alert_telegram_token, settings.alert_telegram_chat_id
    if not (settings.daily_report_enabled and token and chat):
        return "off"
    now = now or datetime.now(UTC)
    store = store or redis.Redis.from_url(settings.redis_url)
    key = SENT_KEY.format(day=local_midnight(now, settings.app_utc_offset_hours).date().isoformat())
    if not store.set(key, "1", nx=True, ex=2 * 86_400):
        return "already sent"
    try:
        with session_factory()() as session:
            text = build(session, now, read_heartbeats())
        send_telegram(text, token.get_secret_value(), chat, client)
    except Exception:
        store.delete(key)  # a retry may send it
        raise
    log.info("daily report sent")
    return "sent"


@celery_app.task(name="category.maintenance.daily_report", bind=True, ignore_result=True, max_retries=3,
                 soft_time_limit=120, time_limit=150)
def daily_report(self) -> str:
    try:
        return run_daily_report()
    except TelegramError as exc:
        log.warning("daily report not sent", extra={"error": str(exc)})
        raise self.retry(countdown=600) from None
