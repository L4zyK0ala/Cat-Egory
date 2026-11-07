import io
import json
import zipfile

import pytest
from openpyxl import Workbook

from backend.app.services.imports import ImportFileError, parse_upload


def values(rows):
    return [v for _, v in rows]


def test_csv_with_header_column():
    data = "id;Domain;note\n1;example.com;a\n2;Bücher.de;b\n\n3;#comment;c\n".encode()
    rows = parse_upload("list.csv", data, 100)
    assert rows == [(2, "example.com"), (3, "Bücher.de")]


def test_csv_without_header_uses_first_column():
    assert values(parse_upload("x.csv", b"example.com,1\nfoo.org,2\n", 100)) == ["example.com", "foo.org"]


def test_csv_windows_1254_and_bom():
    assert values(parse_upload("x.csv", "alan adı\nşirket.com.tr\n".encode("cp1254"), 10)) == ["şirket.com.tr"]
    assert values(parse_upload("x.csv", "﻿domain\nexample.com\n".encode(), 10)) == ["example.com"]


def test_hosts_and_plain_text():
    data = b"# StevenBlack style\n127.0.0.1 localhost\n0.0.0.0 ads.example.com tracker.example.net # x\n" \
           b"plain.example.org\n\n::1 ip6-localhost\n"
    assert values(parse_upload("hosts.txt", data, 100)) == ["ads.example.com", "tracker.example.net",
                                                             "plain.example.org"]


@pytest.mark.parametrize("payload", [
    ["a.com", "b.com"], [{"domain": "a.com"}, {"url": "https://b.com/x"}], {"domains": ["a.com", "b.com"]},
])
def test_json_shapes(payload):
    assert len(parse_upload("x.json", json.dumps(payload).encode(), 10)) == 2


def test_json_errors():
    with pytest.raises(ImportFileError) as exc:
        parse_upload("x.json", b"{not json", 10)
    assert exc.value.code == "invalid_json"
    with pytest.raises(ImportFileError):
        parse_upload("x.json", b'{"other": 1}', 10)


def _xlsx(rows):
    wb = Workbook()
    for r in rows:
        wb.active.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_xlsx():
    data = _xlsx([["No", "Website"], [1, "example.com"], [2, None], [3, "foo.org"]])
    assert values(parse_upload("list.xlsx", data, 10)) == ["example.com", "foo.org"]


def test_xlsx_rejects_garbage_and_bombs():
    with pytest.raises(ImportFileError) as exc:
        parse_upload("x.xlsx", b"PK not really", 10)
    assert exc.value.code == "invalid_xlsx"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("xl/huge.xml", b"0" * (60 * 1024 * 1024))
    with pytest.raises(ImportFileError) as exc:
        parse_upload("bomb.xlsx", buf.getvalue(), 10)
    assert exc.value.code == "too_large"


def test_row_limit_and_format():
    with pytest.raises(ImportFileError) as exc:
        parse_upload("x.txt", b"\n".join(f"d{i}.com".encode() for i in range(11)), 10)
    assert exc.value.code == "too_many_rows"
    with pytest.raises(ImportFileError) as exc:
        parse_upload("x.exe", b"MZ", 10)
    assert exc.value.code == "unsupported_format"
