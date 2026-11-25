"""deployment/scripts/release.py: one version everywhere, the changelog closed, nothing written on a mistake."""
import importlib.util
import json
import sys
from datetime import date
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "deployment" / "scripts" / "release.py"
spec = importlib.util.spec_from_file_location("release", SCRIPT)
release = importlib.util.module_from_spec(spec)
sys.modules["release"] = release
spec.loader.exec_module(release)

REPO = release.REPO
LOG = f"""# Changelog

## [Unreleased]

### Lists
- `profile-business`.

## [0.2.0] – 2026-11-20

### Classification
- Taxonomy 3.0.0.

[Unreleased]: {REPO}/compare/v0.2.0...HEAD
[0.2.0]: {REPO}/compare/v0.1.0...v0.2.0
"""


@pytest.fixture
def root(tmp_path):
    (tmp_path / "frontend").mkdir()
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "cat-egory"\nversion = "0.2.0"\n')
    (tmp_path / "frontend/package.json").write_text(json.dumps({"name": "f", "version": "0.2.0"}, indent=2) + "\n")
    lock = {"name": "f", "version": "0.2.0", "lockfileVersion": 3, "packages": {"": {"version": "0.2.0"}}}
    (tmp_path / "frontend/package-lock.json").write_text(json.dumps(lock, indent=2) + "\n")
    (tmp_path / "CHANGELOG.md").write_text(LOG)
    return tmp_path


def test_bump_writes_one_version_everywhere(root):
    assert release.bump("0.3.0", root, date(2026, 11, 26)) == "0.2.0"
    assert release.current(root) == "0.3.0"
    assert json.loads((root / "frontend/package.json").read_text())["version"] == "0.3.0"
    lock = json.loads((root / "frontend/package-lock.json").read_text())
    assert lock["version"] == "0.3.0" and lock["packages"][""]["version"] == "0.3.0"
    log = (root / "CHANGELOG.md").read_text()
    assert "## [Unreleased]\n\n## [0.3.0] – 2026-11-26\n\n### Lists\n- `profile-business`." in log
    assert f"[Unreleased]: {REPO}/compare/v0.3.0...HEAD\n[0.3.0]: {REPO}/compare/v0.2.0...v0.3.0\n" in log
    assert release.notes("0.3.0", root) == "### Lists\n- `profile-business`.\n"
    assert release.notes("0.2.0", root) == "### Classification\n- Taxonomy 3.0.0.\n"


@pytest.mark.parametrize("version", ["0.2.0", "0.1.9", "0.3", "v0.3.0"])
def test_a_version_not_above_the_current_one_is_refused(root, version):
    with pytest.raises(ValueError):
        release.bump(version, root)
    assert release.current(root) == "0.2.0" and (root / "CHANGELOG.md").read_text() == LOG


def test_nothing_to_release_writes_nothing(root):
    (root / "CHANGELOG.md").write_text(LOG.replace("### Lists\n- `profile-business`.\n\n", ""))
    with pytest.raises(ValueError, match="empty"):
        release.bump("0.3.0", root)
    assert release.current(root) == "0.2.0"


def test_notes_for_an_unknown_version(root):
    with pytest.raises(ValueError):
        release.notes("9.9.9", root)


def test_the_real_changelog_has_every_tagged_version():
    for version in ("0.1.0", "0.2.0"):
        assert release.notes(version).strip()
