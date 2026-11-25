"""A stand-in for the Cat-egory feed API in the DNS lab: the same paths, headers and list formats, plus failures.

    python feedserver.py [port]            # serves /api/v1/feeds/profile-security.{rpz,txt,hosts,adblock}
    GET /_mode/<mode>                      # switch what downloads get:
        ok         the list (example.com, example.net and 300 filler names), ETag = SHA-256 of the body
        error      HTTP 500
        garbage    an HTML page with a matching ETag (a captive portal or an error page answering 200)
        truncated  the first 60 % of the list under the full list's ETag (a cut-off download)
        shrunk     a valid list of 10 entries (a broken build on the server side)
        plus       the list plus example.org (a new version: proves the server reloads it)

The list formats follow feeds/builder.py; the lab's live test checks the real API as well.
"""
from __future__ import annotations

import hashlib
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DOMAINS = ["example.com", "example.net"] + [f"f{i:04d}.lab.invalid" for i in range(1, 301)]
FORMATS = ("rpz", "txt", "hosts", "adblock")
MODE = {"value": "ok"}


def body(fmt: str, domains: list[str], serial: int = 2026112401) -> bytes:
    prefix = {"rpz": ";", "adblock": "!"}.get(fmt, "#")
    head = "".join(f"{prefix} {line}\n" for line in (
        "Title: Cat-egory - security threats (lab)", "Version: 2026.11.24.1", "License: CC0-1.0 (lab)",
        f"Entries: {len(domains)}"))
    if fmt == "rpz":
        head += ("$TTL 300\n$ORIGIN rpz.cat-egory.\n@ SOA localhost. hostmaster.localhost. "
                 f"({serial} 3600 600 86400 300)\n@ NS localhost.\n")
        lines = "".join(f"{d} CNAME .\n*.{d} CNAME .\n" for d in domains)
    elif fmt == "hosts":
        lines = "".join(f"0.0.0.0 {d}\n" for d in domains)
    elif fmt == "adblock":
        lines = "".join(f"||{d}^\n" for d in domains)
    else:
        lines = "".join(f"{d}\n" for d in domains)
    return (head + lines).encode()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # quiet: the lab prints its own results
        pass

    def send(self, code: int, payload: bytes, etag: str | None = None) -> None:
        self.send_response(code)
        if etag:
            self.send_header("ETag", f'"{etag}"')
            self.send_header("X-Feed-Version", "2026.11.24.1")
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(payload)

    def do_GET(self):  # noqa: N802 (http.server API)
        if self.path == "/healthz":
            return self.send(200, b"ok\n")
        if self.path.startswith("/_mode/"):
            MODE["value"] = self.path.rsplit("/", 1)[-1]
            return self.send(200, f"mode {MODE['value']}\n".encode())
        prefix = "/api/v1/feeds/profile-security."
        fmt = self.path[len(prefix):] if self.path.startswith(prefix) else ""
        if fmt not in FORMATS:
            return self.send(404, b"unknown feed\n")
        mode = MODE["value"]
        if mode == "error":
            return self.send(500, b"internal error\n")
        if mode == "garbage":
            page = b"<html><body>Please sign in to the guest network</body></html>\n"
            return self.send(200, page, hashlib.sha256(page).hexdigest())
        full = body(fmt, DOMAINS + ["example.org"], 2026112402) if mode == "plus" else body(fmt, DOMAINS)
        if mode == "shrunk":
            small = body(fmt, DOMAINS[:10])
            return self.send(200, small, hashlib.sha256(small).hexdigest())
        etag = hashlib.sha256(full).hexdigest()
        if self.headers.get("If-None-Match", "").strip('"') == etag:
            return self.send(304, b"", etag)
        if mode == "truncated":
            return self.send(200, full[: len(full) * 6 // 10], etag)
        return self.send(200, full, etag)

    do_HEAD = do_GET


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
