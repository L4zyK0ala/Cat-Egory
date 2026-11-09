import httpx

from threats import virustotal


def payload(**stats):
    return {"data": {"attributes": {
        "last_analysis_date": 1762646400,
        "last_analysis_stats": {"malicious": 0, "suspicious": 0, "harmless": 60, "undetected": 30, "timeout": 5, **stats},
        "last_analysis_results": {
            "B": {"engine_name": "Bvendor", "category": "suspicious", "result": "suspicious"},
            "A": {"engine_name": "Avendor", "category": "harmless", "result": "clean"},
            "C": {"engine_name": "Cvendor", "category": "malicious", "result": "phishing"},
        }}}}


def test_summary_counts_exclude_timeouts_and_lists_only_flagging_vendors():
    s = virustotal.summary(payload(malicious=1, suspicious=1))
    assert (s["malicious"], s["suspicious"], s["total"]) == (1, 1, 92)
    assert [e["name"] for e in s["engines"]] == ["Cvendor", "Bvendor"]  # malicious first
    assert s["last_analysis"].startswith("2025-11-09")


def test_never_analysed_is_none_and_listed_needs_a_vendor():
    assert virustotal.summary({"data": {"attributes": {}}}) is None
    assert virustotal.is_listed([]) is False
    assert virustotal.is_listed([virustotal.summary(payload())]) is False
    assert virustotal.is_listed([virustotal.summary(payload(suspicious=1))]) is True


def test_http_fetch_is_a_get_of_the_bare_domain_with_the_key_in_a_header():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=payload(malicious=2)) if "evil" in request.url.path else httpx.Response(404)

    http = httpx.Client(transport=httpx.MockTransport(handler), headers={"x-apikey": "k"})
    fetch = virustotal.http_fetch(http)
    assert fetch("evil.example.net")[0]["malicious"] == 2
    assert fetch("new.example.net") == []  # 404: VirusTotal does not know it
    assert all(r.method == "GET" and not r.url.query and "k" == r.headers["x-apikey"] for r in seen)
    assert str(seen[0].url) == "https://www.virustotal.com/api/v3/domains/evil.example.net"
