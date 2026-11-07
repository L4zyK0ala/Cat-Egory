"""Parse uploaded domain lists (CSV/TSV, TXT/hosts, JSON, XLSX) into ``(row_number, value)`` pairs.

Uploads are untrusted: size is capped before parsing, XLSX archives are checked for decompression bombs,
XML is parsed through defusedxml (openpyxl uses it automatically when installed), and nothing is evaluated.
"""
from __future__ import annotations

import csv
import io
import ipaddress
import json
import zipfile
from pathlib import PurePath

HEADER_NAMES = {"domain", "domains", "url", "urls", "host", "hostname", "site", "website", "alan adı", "alan adi"}
# Entries that appear in every hosts file and are not domains; skipped silently.
HOSTS_NOISE = {"localhost", "localhost.localdomain", "local", "broadcasthost", "ip6-localhost", "ip6-loopback",
               "ip6-localnet", "ip6-mcastprefix", "ip6-allnodes", "ip6-allrouters", "ip6-allhosts"}
MAX_XLSX_UNCOMPRESSED = 50 * 1024 * 1024
SUPPORTED = (".csv", ".tsv", ".txt", ".hosts", ".list", ".json", ".xlsx")

Rows = list[tuple[int, str]]


class ImportFileError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _decode(content: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1254"):  # UTF-8 first, then Windows Turkish (common for Excel CSV exports)
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ImportFileError("encoding", "file is neither UTF-8 nor Windows-1254 text")


def _limit(rows: Rows, max_rows: int) -> Rows:
    if len(rows) > max_rows:
        raise ImportFileError("too_many_rows", f"file has more than {max_rows} entries")
    return rows


def _table(records: list[tuple[int, list[str]]], max_rows: int) -> Rows:
    """First column, or the column named like 'domain'/'url' if the first row is a header."""
    records = [(n, cells) for n, cells in records if any(c.strip() for c in cells)]
    if not records:
        return []
    column, start = 0, 0
    header = [c.strip().lower() for c in records[0][1]]
    for i, name in enumerate(header):
        if name in HEADER_NAMES:
            column, start = i, 1
            break
    rows: Rows = []
    for n, cells in records[start:]:
        value = cells[column].strip() if column < len(cells) else ""
        if value and not value.startswith("#"):
            rows.append((n, value))
            if len(rows) > max_rows:
                break
    return _limit(rows, max_rows)


def parse_csv(content: bytes, max_rows: int, delimiter: str | None = None) -> Rows:
    text = _decode(content)
    if delimiter is None:
        try:
            delimiter = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|").delimiter
        except csv.Error:
            delimiter = ","
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    return _table([(n, row) for n, row in enumerate(reader, start=1)], max_rows)


def _is_ip(token: str) -> bool:
    try:
        ipaddress.ip_address(token)
    except ValueError:
        return False
    return True


def parse_text(content: bytes, max_rows: int) -> Rows:
    """One domain per line, ``#`` comments, or hosts format (``0.0.0.0 a.com b.com``)."""
    rows: Rows = []
    for n, line in enumerate(_decode(content).splitlines(), start=1):
        tokens = line.split("#", 1)[0].split()
        if not tokens:
            continue
        names = tokens[1:] if len(tokens) > 1 and _is_ip(tokens[0]) else tokens[:1]
        for name in names:
            if name.lower() not in HOSTS_NOISE:
                rows.append((n, name))
        if len(rows) > max_rows:
            break
    return _limit(rows, max_rows)


def parse_json(content: bytes, max_rows: int) -> Rows:
    try:
        data = json.loads(_decode(content))
    except json.JSONDecodeError as exc:
        raise ImportFileError("invalid_json", f"invalid JSON: {exc.msg} (line {exc.lineno})") from exc
    if isinstance(data, dict):
        data = data.get("domains")
    if not isinstance(data, list):
        raise ImportFileError("invalid_json", 'expected a list, or an object with a "domains" list')
    rows: Rows = []
    for n, item in enumerate(data, start=1):
        if isinstance(item, dict):
            item = item.get("domain") or item.get("url")
        if isinstance(item, str) and item.strip():
            rows.append((n, item.strip()))
        elif item is not None:
            rows.append((n, str(item)[:100]))  # reported as invalid later
        if len(rows) > max_rows:
            break
    return _limit(rows, max_rows)


def parse_xlsx(content: bytes, max_rows: int) -> Rows:
    from openpyxl import load_workbook

    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            if sum(info.file_size for info in archive.infolist()) > MAX_XLSX_UNCOMPRESSED:
                raise ImportFileError("too_large", "workbook expands beyond the allowed size")
        workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    except ImportFileError:
        raise
    except Exception as exc:  # noqa: BLE001 - any parser failure means "not a valid workbook"
        raise ImportFileError("invalid_xlsx", "file is not a valid .xlsx workbook") from exc
    try:
        sheet = workbook.worksheets[0]
        records: list[tuple[int, list[str]]] = []
        for n, values in enumerate(sheet.iter_rows(values_only=True), start=1):
            records.append((n, ["" if v is None else str(v) for v in values]))
            if len(records) > max_rows + 1:
                break
    finally:
        workbook.close()
    return _table(records, max_rows)


def parse_upload(filename: str, content: bytes, max_rows: int) -> Rows:
    suffix = PurePath(filename or "").suffix.lower()
    if suffix == ".csv":
        return parse_csv(content, max_rows)
    if suffix == ".tsv":
        return parse_csv(content, max_rows, delimiter="\t")
    if suffix in (".txt", ".hosts", ".list"):
        return parse_text(content, max_rows)
    if suffix == ".json":
        return parse_json(content, max_rows)
    if suffix == ".xlsx":
        return parse_xlsx(content, max_rows)
    raise ImportFileError("unsupported_format", f"unsupported file type {suffix or '(none)'}; use {', '.join(SUPPORTED)}")
