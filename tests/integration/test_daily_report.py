"""The daily Telegram summary: yesterday's numbers in Türkiye time, and what waits for a person now."""
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from backend.app import report_tasks
from common.config import get_settings
from database.models import (
    AbuseReport,
    AIPrediction,
    AssignmentOrigin,
    ClassificationStatus,
    DataSource,
    Domain,
    DomainCategory,
    FeedRelease,
    JobStatus,
    ReviewStatus,
    ReviewTask,
    ScanJob,
    ScanReason,
    ThreatIntelligence,
    ThreatType,
    ValidationStatus,
)

NOW = datetime(2026, 11, 15, 6, 0, tzinfo=UTC)  # 09:00 in Türkiye: the report covers Saturday 14 November
YESTERDAY = NOW - timedelta(hours=12)           # 21:00 on the 14th, Türkiye time
EARLIER = NOW - timedelta(days=3)


def domain(s, name, status, primary=None, assigned=None):
    d = Domain(domain=name, normalized_domain=name, registrable_domain=name, public_suffix="example",
               classification_status=status, primary_category_id=primary)
    s.add(d)
    s.flush()
    if primary:
        s.add(DomainCategory(domain_id=d.id, category_id=primary, is_primary=True, origin=AssignmentOrigin.AI,
                             taxonomy_version="2.0.0", valid_from=assigned))
    return d


def prediction(s, d, status, tokens_in, tokens_out, at, provider="evren"):
    s.add(AIPrediction(domain_id=d.id, provider=provider, model="m", prompt_version="p", taxonomy_version="2.0.0",
                       validation_status=status, input_tokens=tokens_in, output_tokens=tokens_out, created_at=at))


def job(s, d, status, at, reason=ScanReason.SCHEDULED):
    s.add(ScanJob(domain_id=d.id, reason=reason, status=status, queued_at=at,
                  finished_at=at if status not in (JobStatus.QUEUED, JobStatus.RUNNING) else None))


def test_the_summary_counts_yesterday_in_turkiye_time(clean, session, monkeypatch):
    s = session
    monkeypatch.setattr(report_tasks, "get_settings", lambda: get_settings().model_copy(update={"ai_daily_token_cap": 10_000}))
    news = domain(s, "news.example", ClassificationStatus.CLASSIFIED, "news", YESTERDAY)
    casino = domain(s, "casino.example", ClassificationStatus.VERIFIED, "gambling", EARLIER)
    gone = domain(s, "gone.example", ClassificationStatus.UNREACHABLE)
    domain(s, "new.example", ClassificationStatus.UNCLASSIFIED)

    for status in (JobStatus.SUCCEEDED, JobStatus.SUCCEEDED, JobStatus.BLOCKED, JobStatus.FAILED):
        job(s, news, status, YESTERDAY)
    job(s, news, JobStatus.SUCCEEDED, EARLIER)  # another day
    job(s, gone, JobStatus.QUEUED, YESTERDAY, ScanReason.DISCOVERY)

    for _ in range(3):
        prediction(s, news, ValidationStatus.VALID, 1000, 200, YESTERDAY)
    prediction(s, news, ValidationStatus.INVALID_JSON, 500, 0, YESTERDAY)
    prediction(s, news, ValidationStatus.VALID, 9000, 999, NOW - timedelta(hours=1))  # today: tomorrow's report
    prediction(s, news, ValidationStatus.VALID, 0, 0, YESTERDAY, provider="mock")    # the offline model: not counted

    s.add_all([ReviewTask(domain_id=news.id, reason="low_confidence", created_at=YESTERDAY),
               ReviewTask(domain_id=casino.id, reason="low_confidence", created_at=EARLIER),
               ReviewTask(domain_id=gone.id, reason="lookalike_of:garanti.com.tr", created_at=EARLIER),
               ReviewTask(domain_id=casino.id, reason="verification_sample", created_at=EARLIER,
                          status=ReviewStatus.DONE, decided_at=YESTERDAY)])

    source = s.scalar(select(DataSource.id).order_by(DataSource.id).limit(1))
    s.add_all([ThreatIntelligence(domain_id=gone.id, threat_type=ThreatType.PHISHING, source_id=source, first_seen=YESTERDAY),
               ThreatIntelligence(domain_id=casino.id, threat_type=ThreatType.SCAM, source_id=source, first_seen=EARLIER,
                                  is_active=False, deactivated_at=YESTERDAY)])
    s.add(AbuseReport(domain_id=gone.id, kind="registrar", subject="s", body="b"))
    s.add(FeedRelease(feed="all.csv", version="2026.11.15.8", license="CC-BY-4.0", taxonomy_version="2.0.0",
                      record_count=2, sha256="0" * 64, object_key="k", generated_at=NOW - timedelta(hours=1)))
    s.flush()

    text = report_tasks.build(s, NOW, {"backup.nightly": NOW.timestamp() - 6 * 3600})
    lines = text.splitlines()
    assert lines[0] == "📊 Cat-egory · 14 Kasım 2026 Cumartesi özeti"
    for expected in ("• Kategorili: 2 / 4 (dün +1)",
                     "• İnsan onaylı: 1 · ulaşılamayan: 1 · taranmayı bekleyen: 1",
                     "• Biten: 4 · başarılı 2, engelli 1, hatalı 1",
                     "• Keşiften gelen yeni site: 1 · şu an sırada: 1",
                     "• Yanıt: 4 · geçersiz ya da hatalı: 1 (%25)",
                     "• Token: 4 bin / 10 bin sınır (%41)",
                     "• Bekleyen: 3 (tehdit sorusu 1)",
                     "• Dün açılan 1, karara bağlanan 1",
                     "• En çok: model emin değil 2, marka taklidi 1",
                     "• Etkin: 1 (dün +1 yeni, −1 kaldırıldı)",
                     "• Gönderilmeyi bekleyen kötüye kullanım taslağı: 1",
                     "• Son liste derlemesi: 08:00 (sürüm 2026.11.15.8)",
                     "• Yedek: 6 saat önce ✓",
                     "• Sunucu dışı yedek: kurulmadı"):
        assert expected in lines, (expected, text)


def test_an_empty_system_still_reads_well(clean, session):
    text = report_tasks.build(session, NOW, {"backup.nightly": NOW.timestamp() - 31 * 3600})
    assert "• Kategorili: 0 / 0 (dün +0)" in text
    assert "• Henüz liste derlenmedi" in text
    assert "• Yedek: 31 saat önce ⚠️" in text
    assert "En çok" not in text and "None" not in text
