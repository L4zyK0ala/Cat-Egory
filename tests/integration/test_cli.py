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
