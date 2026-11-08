"""Runtime data files must ship with the package: the Docker image installs the project (pip install .), so a
file missing from [tool.setuptools.package-data] exists in the repository but not in the container."""
import fnmatch
import tomllib
from pathlib import Path

ROOT = Path(__file__).parents[2]
DATA_SUFFIXES = {".yaml", ".yml", ".json", ".csv", ".txt", ".mako"}
NOT_RUNTIME = {"README.md"}


def test_every_data_file_is_packaged():
    config = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["setuptools"]
    packages = {p.rstrip("*") for p in config["packages"]["find"]["include"]}
    patterns = config["package-data"]
    missing = []
    for package in packages:
        base = ROOT / package
        for f in base.rglob("*"):
            if not f.is_file() or f.suffix not in DATA_SUFFIXES or f.name in NOT_RUNTIME or "__pycache__" in f.parts:
                continue
            rel = f.relative_to(base).as_posix()
            if not any(fnmatch.fnmatch(rel, pat) for pat in patterns.get(package, [])):
                missing.append(f"{package}/{rel}")
    assert not missing, f"add to [tool.setuptools.package-data] in pyproject.toml: {missing}"
