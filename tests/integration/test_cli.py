import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend import cli


def test_cli_lifecycle(engine, clean, monkeypatch, capsys):
    monkeypatch.setattr(cli, "session_factory", lambda: __import__("sqlalchemy.orm", fromlist=["sessionmaker"])
                        .sessionmaker(bind=engine))
    assert cli.main(["create-admin", "--email", "Admin@Example.org", "--name", "Admin"]) == 0
    out = capsys.readouterr().out
    assert "cat_" in out and "admin@example.org" in out
    prefix = out.split("prefix=")[1].split()[0]

    assert cli.main(["create-admin", "--email", "admin@example.org", "--name", "Again"]) == 1  # duplicate
    assert cli.main(["create-user", "--email", "c@example.org", "--name", "C", "--role", "contributor"]) == 0
    assert cli.main(["create-key", "--email", "c@example.org", "--name", "x", "--scopes", "domains:write"]) == 1
    assert cli.main(["create-key", "--email", "c@example.org", "--name", "x"]) == 0
    capsys.readouterr()
    assert cli.main(["list-keys"]) == 0
    listing = capsys.readouterr().out
    assert prefix in listing and "cat_" not in listing  # secrets are never listed
    assert cli.main(["revoke-key", "--prefix", prefix]) == 0
    assert cli.main(["revoke-key", "--prefix", "nonexistent"]) == 1


@pytest.mark.parametrize("email", ["no-at-sign", "x" * 400 + "@a.org"])
def test_cli_rejects_bad_email(engine, clean, monkeypatch, email):
    from sqlalchemy.orm import sessionmaker

    monkeypatch.setattr(cli, "session_factory", lambda: sessionmaker(bind=engine))
    assert cli.main(["create-user", "--email", email, "--name", "x"]) == 1


def test_deactivate_source_domains(engine, clean, client, admin):
    from sqlalchemy import select
    from sqlalchemy.orm import Session, sessionmaker

    from backend.cli import deactivate_source_domains
    from database.models import Domain, ScanJob

    client.post("/api/v1/admin/domains/import", headers=admin,
                files={"file": ("t.txt", b"t1.example.org\nt2.example.org\nshared.example.org\n", "text/plain")})
    client.post("/api/v1/admin/domains", headers=admin, json={"domains": ["shared.example.org"], "scan": "none"})
    with sessionmaker(bind=engine)() as s, s.begin():
        assert deactivate_source_domains(s, "file-import", dry_run=True) == (2, 0)
    with sessionmaker(bind=engine)() as s, s.begin():
        assert deactivate_source_domains(s, "file-import") == (2, 2)
    with Session(engine) as s:
        active = set(s.scalars(select(Domain.normalized_domain).where(Domain.is_active)))
        assert active == {"shared.example.org"}  # also reported by a public source: kept
        assert {str(j.status) for j in s.scalars(select(ScanJob))} == {"cancelled", "queued"}
    assert client.get("/api/v1/domain/t1.example.org", headers=admin).status_code == 404


def test_reclassify_provider(engine, clean):
    from sqlalchemy import select
    from sqlalchemy.orm import Session, sessionmaker

    from ai_providers.mock import MockProvider
    from backend.cli import reclassify_provider
    from database.models import ClassificationStatus, Domain, ReviewTask, ScanJob
    from tests.integration.test_classification import Scripted, make_scan, run

    run(engine, make_scan(engine, "mocked.example.com"), MockProvider())
    run(engine, make_scan(engine, "real.example.com"), Scripted())
    run(engine, make_scan(engine, "later.example.com"), MockProvider())
    run(engine, make_scan(engine, "later.example.com"), Scripted(confidence=0.6))  # latest answer is not mock

    with sessionmaker(bind=engine)() as s, s.begin():
        assert reclassify_provider(s, "mock", dry_run=True) == (1, 1)
    with sessionmaker(bind=engine)() as s, s.begin():
        assert reclassify_provider(s, "mock") == (1, 1)
    with Session(engine) as s:
        d = s.scalar(select(Domain).where(Domain.normalized_domain == "mocked.example.com"))
        assert d.classification_status == ClassificationStatus.REANALYZE
        task = s.scalar(select(ReviewTask).where(ReviewTask.domain_id == d.id))
        assert str(task.status) == "skipped" and "mock" in task.decision_note
        jobs = list(s.scalars(select(ScanJob).where(ScanJob.domain_id == d.id, ScanJob.status == "queued")))
        assert [(str(j.reason), j.priority) for j in jobs] == [("rescan", 3)]
        later = s.scalar(select(Domain).where(Domain.normalized_domain == "later.example.com"))
        assert s.scalar(select(ReviewTask.status).where(ReviewTask.domain_id == later.id)) == "open"
    with sessionmaker(bind=engine)() as s, s.begin():
        assert reclassify_provider(s, "mock") == (1, 0)  # idempotent: no second job (open-job index)


def test_reclassify_category(engine, clean, client, admin):
    from backend.cli import reclassify_category
    from classification.engine import apply_categories
    from classification.taxonomy import default_taxonomy
    from database.models import AssignmentOrigin, ClassificationStatus, Domain, ScanJob

    client.post("/api/v1/admin/domains", headers=admin,
                json={"domains": ["casino.example.com", "news.example.com"], "scan": "none"})
    with Session(engine) as s, s.begin():
        for name, primary in (("casino.example.com", "gambling"), ("news.example.com", "news")):
            apply_categories(s, s.scalar(select(Domain).where(Domain.normalized_domain == name)), primary=primary,
                             secondary=[], confidence=0.9, origin=AssignmentOrigin.AI, taxonomy=default_taxonomy(),
                             status=ClassificationStatus.CLASSIFIED)
    with Session(engine) as s, s.begin():
        assert reclassify_category(s, "gambling", dry_run=True) == ["casino.example.com"]
        assert s.scalar(select(func.count()).select_from(ScanJob)) == 0
        assert reclassify_category(s, "gambling") == ["casino.example.com"]
    with Session(engine) as s:
        jobs = s.scalars(select(ScanJob)).all()
        assert [(str(j.reason), j.priority) for j in jobs] == [("rescan", 3)]


def test_reclassify_only_likely_pirates(engine, clean, client, admin):
    from backend.cli import reclassify_category
    from classification.engine import apply_categories
    from classification.taxonomy import default_taxonomy
    from database.models import AssignmentOrigin, ClassificationStatus, Domain
    from tests.integration.test_classification import make_scan

    names = {"hdfilmcehennemi.land": ("streaming-media", "Film İzle - Full HD Türkçe Dublaj"),
             "netflix.com": ("streaming-media", "Netflix Türkiye - Dizi ve Film İzleyin"),
             "hbo.com": ("streaming-media", "HBO: Home"),
             "canlimacizle.net": ("sports", "Canlı Maç İzle"),
             "news.example.com": ("news", "Film izle haberleri")}
    client.post("/api/v1/admin/domains", headers=admin, json={"domains": list(names), "scan": "none"})
    with Session(engine) as s, s.begin():
        for name, (primary, _) in names.items():
            apply_categories(s, s.scalar(select(Domain).where(Domain.normalized_domain == name)), primary=primary,
                             secondary=[], confidence=0.9, origin=AssignmentOrigin.AI, taxonomy=default_taxonomy(),
                             status=ClassificationStatus.CLASSIFIED)
    for name, (_, title) in names.items():
        make_scan(engine, name, title=title)
    with Session(engine) as s, s.begin():
        found = reclassify_category(s, "streaming-media,sports", dry_run=True, matching="piracy")
        # the model decides (Netflix's "Film İzleyin" is asked again too); HBO and other categories are not
        assert sorted(found) == ["canlimacizle.net", "hdfilmcehennemi.land", "netflix.com"]
        assert len(reclassify_category(s, "streaming-media,sports", dry_run=True, matching="piracy", limit=1)) == 1
        assert reclassify_category(s, "streaming-media", dry_run=True, matching="hbo") == ["hbo.com"]
    # a category list longer than the audit column still queues (the audit row keeps it whole in "after")
    many = "streaming-media,video,music,entertainment,sports,file-sharing,software-downloads"
    with Session(engine) as s, s.begin():
        assert sorted(reclassify_category(s, many, matching="piracy")) == [
            "canlimacizle.net", "hdfilmcehennemi.land", "netflix.com"]
    with Session(engine) as s:
        from database.models import AuditLog
        row = s.scalar(select(AuditLog).where(AuditLog.action == "domains.reclassify_category"))
        assert row.target_id == many[:64] and row.after["target"] == many and row.after["queued_scans"] == 3
