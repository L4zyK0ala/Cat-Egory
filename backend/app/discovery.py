"""Discovery: first scans of domains that discovery sources (e.g. a top-sites list) brought in.

A synced source only adds domains; nothing scans them until this task queues them. It works in small batches
under a daily budget so that crawling and the AI provider's quota grow slowly and predictably:

* at most ``discovery_batch_size`` per run (beat: every 15 minutes),
* at most ``discovery_daily_budget`` per day, Türkiye time. Only scans that can
  reach the AI use it up: failed, blocked and cancelled ones give their place back,
* nothing while the last hour has ``discovery_pause_after_infra_failures`` failures on our side, so an outage does
  not burn through the list,
* nothing while ``discovery_max_queued`` scans are already waiting, so manual and scheduled work goes first,
* nothing once today's EVREN tokens (all work, Türkiye time) reach ``ai_daily_token_cap``, so discovery never eats
  into the share of the account's quota that other projects need,
* first the names several visitors looked up while we did not know them (``demand`` source, added nightly by
  ``backend/app/demand.py``),
* each domain once: by the source's ``discover_priority`` in sources.yaml (CrUX Türkiye, then .tr, then the world),
  then most popular first (``domain_source_records.source_rank``: the row in the source file or its own rank); later
  scans are scheduled by ``category.scan.schedule`` like any other domain,
* part of each batch (``discovery_thin_share``) goes to thin categories: names that hint at a category with fewer
  than ``discovery_thin_target`` domains (``THIN_HINTS``: vpn, proxy, anydesk, muzayede, radyo, emlak, ...), the
  thinnest category first and one name each in turn, most popular first. The lists people block with and Yaş Mama
  (``classification/yasmama.py``), which cannot learn a category from a handful of pages, get examples without
  waiting for the whole popularity order. A category drops out once it has enough domains; a wrong hint costs one
  scan, because the scan decides.

A source takes part when its catalogue entry says ``discover: true`` and a maintainer approved its licence.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import BigInteger, Select, case, cast, exists, func, literal, select, true
from sqlalchemy.orm import Session

from common.config import Settings, get_settings
from common.timeutil import local_midnight
from crawler.result import INFRA_ERRORS
from database.models import (
    AIPrediction,
    DataSource,
    Domain,
    DomainSourceRecord,
    JobStatus,
    ModelComparison,
    ScanJob,
    ScanReason,
)
from database.session import session_factory

from .services.domains import queue_scans
from .worker import celery_app

DEMAND_SOURCE = "demand"  # backend/app/demand.py (kept apart: that module is imported by the API)
PRIORITY = 8  # after manual (5), evaluation (4) and scheduled re-scans (7)
# Thin categories and a name hint for each (PostgreSQL regex, case-insensitive, on the ASCII name). Kept narrow:
# "gun" would catch every "gunes" (güneş), "estate" every "state", "bira" every "birazoku", "ammo" "backgammon".
# Hate and violence are left out on purpose: discovery does not go looking for them.
THIN_HINTS: dict[str, str] = {
    "alcohol": r"wine|sarap|winery|(^|[.-])beer|(^|[.-])bira[.-]|biracilik|vodka|whisk|liquor|brewery|distill|raki[.-]",
    "illegal-drugs": r"(^|[.-])weed|kratom|cannabis|marijuana|psychedelic|shroom|(^|[.-])cbd",
    "tobacco": r"vape|vaping|tobacco|sigara|cigar|nargile|hookah|shisha|snus|tutun",
    "weapons": r"silah|firearm|(^|[.-])ammo|ammunition|airsoft|tabanca|tufek|gunshop|(^|[.-])guns?[.-]",
    # licensed anywhere (illegal-gambling has plenty); the scan tells them apart. Not "bet" alone: alphabet, better
    "gambling": r"casino|poker|roulette|sportsbook|jackpot|bet[0-9]|(^|[.-])bet(way|fair|ting|[.-])",
    "vpn-services": r"vpn",
    "proxy-services": r"proxy|unblock|hidemy|anonymouse",
    "remote-access": r"anydesk|teamviewer|splashtop|rustdesk|parsec|remotedesktop|(^|[.-])rdp|vnc",
    "content-delivery-networks": r"(^|[.-])cdn|akamai|edgekey|cloudfront|fastly|jsdelivr",
    "email": r"(^|[.-])mail|webmail|posta[.-]",
    "auctions": r"auction|muzayede|mezat|acikartirma",
    "online-radio": r"radyo|radio|(^|[.-])fm[.-]",
    "libraries": r"kutuphane|librar|kitaplik",
    "cryptocurrency": r"crypto|kripto|(^|[.-])coin|bitcoin|blockchain|(^|[.-])btc",
    "real-estate": r"realestate|real-estate|emlak|realty|konut|gayrimenkul",
    "science": r"science|bilim|physics|chemistry|biology",
    "video": r"video|vimeo",
    "streaming-media": r"stream|(^|[.-])tv[.-]",
    "legal": r"hukuk|avukat|(^|[.-])law[.-]|lawyer|attorney|legal",
    "forums": r"forum|sozluk",
    "blogs": r"blog",
    "cybersecurity": r"cyber|siber|antivirus|malware|security|guvenlik",
    "artificial-intelligence": r"\.ai$|(^|[.-])ai[.-]|gpt|llm|openai|neural",
    "web-hosting": r"hosting|webhost|sunucu|(^|[.-])vps",
    "information-technology": r"bilisim|teknoloji",
}
DEFAULT_PRIORITY = 100  # a discovery source without discover_priority in sources.yaml
RANK_SPAN = 10_000_000  # larger than any rank (lists are cut at 3M rows)
NOT_SPENT = (JobStatus.FAILED, JobStatus.BLOCKED, JobStatus.CANCELLED)  # never reached the AI


def infra_failures_last_hour(session: Session, now: datetime | None = None) -> int:
    """Scans that failed on our side (sandbox, storage, proxy ...), not because of the site."""
    since = (now or datetime.now(UTC)) - timedelta(hours=1)
    return session.scalar(select(func.count()).select_from(ScanJob).where(
        ScanJob.status == JobStatus.FAILED, ScanJob.error_code.in_(INFRA_ERRORS), ScanJob.finished_at >= since)) or 0


def tokens_since(session: Session, since: datetime) -> int:
    """AI tokens (in + out) used since ``since`` by real providers (the offline mock costs nothing), including
    model comparisons (``classification/model_compare.py``): they spend the same account's quota."""
    total = 0
    for table in (AIPrediction, ModelComparison):
        total += int(session.scalar(select(func.coalesce(func.sum(
            func.coalesce(table.input_tokens, 0) + func.coalesce(table.output_tokens, 0)), 0))
            .where(table.created_at >= since, table.provider != "mock")) or 0)
    return total


def discovery_sources(session: Session, entries: dict[str, dict[str, Any]] | None = None) -> dict[int, int]:
    """{source id: discover_priority} of the approved discovery sources."""
    if entries is None:
        from integrations.engine import catalogue

        entries = catalogue()
    slugs = {slug: int(entry.get("discover_priority", DEFAULT_PRIORITY)) for slug, entry in entries.items()
             if entry.get("discover")}
    if not slugs:
        return {}
    return {sid: slugs[slug] for sid, slug in session.execute(
        select(DataSource.id, DataSource.slug).where(DataSource.slug.in_(slugs), DataSource.enabled,
                                                     DataSource.license_id.is_not(None)))}


def thin_categories(session: Session, settings: Settings) -> list[str]:
    """``THIN_HINTS``' categories with fewer than ``discovery_thin_target`` domains, the thinnest first."""
    counts = dict(session.execute(
        select(Domain.primary_category_id, func.count()).where(Domain.primary_category_id.in_(list(THIN_HINTS)),
                                                               Domain.is_active)
        .group_by(Domain.primary_category_id)).all())
    return sorted((c for c in THIN_HINTS if counts.get(c, 0) < settings.discovery_thin_target),
                  key=lambda c: (counts.get(c, 0), c))


def thin_picks(session: Session, settings: Settings, listed: Select, room: int) -> list[int]:
    """Up to ``discovery_thin_share`` of ``room`` candidates from ``listed`` that hint at a thin category: one per
    category in turn, the thinnest category first and its most popular name first."""
    share = max(1, round(room * settings.discovery_thin_share))
    per_category = [list(session.scalars(listed.where(Domain.normalized_domain.op("~*")(THIN_HINTS[c])).limit(share)))
                    for c in thin_categories(session, settings)]
    picks: list[int] = []
    for turn in range(share):
        for names in per_category:
            if turn < len(names) and names[turn] not in picks:
                picks.append(names[turn])
                if len(picks) == share:
                    return picks
    return picks


def queue_discovery(session: Session, settings: Settings, *, entries: dict[str, dict[str, Any]] | None = None,
                    now: datetime | None = None) -> int:
    """Queue the next batch of never-scanned discovery domains. Returns how many were queued."""
    if settings.discovery_daily_budget <= 0:
        return 0
    priorities = discovery_sources(session, entries)
    source_ids = list(priorities)
    # names several visitors looked up while we did not know them (backend/app/demand.py): before the lists
    demand_id = session.scalar(select(DataSource.id).where(DataSource.slug == DEMAND_SOURCE, DataSource.enabled))
    if not source_ids and demand_id is None:
        return 0
    now = now or datetime.now(UTC)
    day_start = local_midnight(now, settings.app_utc_offset_hours)
    if infra_failures_last_hour(session, now) >= settings.discovery_pause_after_infra_failures:
        return 0
    if settings.ai_daily_token_cap and tokens_since(session, day_start) >= settings.ai_daily_token_cap:
        return 0  # today's share of the AI quota is used up; tomorrow it starts again
    used = session.scalar(select(func.count()).select_from(ScanJob).where(
        ScanJob.reason == ScanReason.DISCOVERY, ScanJob.queued_at >= day_start, ScanJob.status.not_in(NOT_SPENT))) or 0
    waiting = session.scalar(select(func.count()).select_from(ScanJob).where(ScanJob.status == JobStatus.QUEUED)) or 0
    n = min(settings.discovery_daily_budget - used, settings.discovery_max_queued - waiting,
            settings.discovery_batch_size)
    if n <= 0:
        return 0
    # A cancelled job never ran (895 domains were cancelled when the offline demo was cleaned up and then never
    # came back): only a job that was not cancelled counts as "already queued once".
    ever_queued = exists(select(ScanJob.id).where(ScanJob.domain_id == Domain.id,
                                                  ScanJob.status != JobStatus.CANCELLED))
    # The source's priority first (a CrUX bucket of 1000 and Majestic's row 1000 are not the same thing), then its
    # rank; a domain in several lists takes its best place. Unranked rows go last within their source.
    priority = case(priorities, value=DomainSourceRecord.source_id, else_=DEFAULT_PRIORITY) if priorities \
        else literal(DEFAULT_PRIORITY)
    place = func.min(cast(priority, BigInteger) * RANK_SPAN
                     + func.coalesce(DomainSourceRecord.source_rank, RANK_SPAN - 1))

    def candidates(ids: list[int]):
        return (select(Domain.id).join(DomainSourceRecord, DomainSourceRecord.domain_id == Domain.id)
                .where(DomainSourceRecord.source_id.in_(ids), DomainSourceRecord.is_listed, Domain.is_active,
                       Domain.last_scanned.is_(None), ~ever_queued)
                .group_by(Domain.id).order_by(place, Domain.id))

    ids = list(session.scalars(candidates([demand_id]).limit(n))) if demand_id is not None else []
    if source_ids and len(ids) < n:
        listed = candidates(source_ids).where(Domain.id.not_in(ids) if ids else true())
        room = n - len(ids)
        hinted = thin_picks(session, settings, listed, room) if settings.discovery_thin_share > 0 else []
        ids += hinted + list(session.scalars(listed.where(Domain.id.not_in(hinted) if hinted else true())
                                             .limit(room - len(hinted))))
    return queue_scans(session, ids, reason=ScanReason.DISCOVERY, priority=PRIORITY, requested_by_id=None) if ids else 0


@celery_app.task(name="category.discovery.queue", ignore_result=True)
def queue() -> int:
    settings = get_settings()
    with session_factory()() as session, session.begin():
        return queue_discovery(session, settings)
