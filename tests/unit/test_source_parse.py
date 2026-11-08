from integrations.engine import MAX_ENTRIES, parse, select_rows


def test_csv_header_column_bom_and_order():
    data = "﻿GlobalRank;TldRank;Domain;TLD\n1;1;google.com;com\n\n2;1;örnek.com.tr;tr\n9;9;# note;x\n".encode()
    assert parse(data, "csv") == [(2, "google.com"), (4, "örnek.com.tr")]


def test_csv_without_header_uses_first_column():
    assert parse(b"a.com,1\nb.com,2\n", "csv") == [(1, "a.com"), (2, "b.com")]


def test_filters_while_reading():
    data = b"Domain\n" + b"".join(f"s{i}.{'com.tr' if i % 3 == 0 else 'com'}\n".encode() for i in range(1, 31))
    assert [v for _, v in parse(data, "csv", {"max_entries": 2})] == ["s1.com", "s2.com"]
    assert [v for _, v in parse(data, "csv", {"include_suffixes": ["tr"], "max_entries": 2})] == ["s3.com.tr", "s6.com.tr"]
    assert len(parse(data, "csv", {"include_suffixes": ["tr"]})) == 10


def test_hosts_feed_still_parses():
    assert parse(b"0.0.0.0 a.example.com b.example.com\n# c\n", "hosts") == [(1, "a.example.com"), (1, "b.example.com")]


def test_select_rows_caps_at_max_entries():
    rows = ((i, f"d{i}.com") for i in range(MAX_ENTRIES + 5))
    assert len(select_rows(rows, {})) == MAX_ENTRIES
