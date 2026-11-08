import pytest

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
