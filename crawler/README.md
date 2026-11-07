# crawler

Visits websites in Chromium (Playwright) and returns a `CrawlResult` (see `result.py`). Runs in its own
container on an internal network: it can reach Redis, the object store and the egress proxy — nothing else.

| Module | Role |
|---|---|
| `ssrf.py` | address / URL policy (shared by the pre-check, the proxy and post-hoc checks) |
| `egress_proxy.py` | the only way out: connect-time DNS + IP checks, pinning, limits (`python -m crawler.egress_proxy`) |
| `robots.py` | robots.txt per RFC 9309 |
| `browser.py` | Playwright capture (https → https ignoring TLS errors → http) |
| `storage.py` | S3 artefact store (private objects) |
| `tasks.py` | Celery task `category.crawl.run` (queue `crawl`) |
| `selftest.py` | checks sandbox, isolation and proxy inside the container |

Chromium's own sandbox is enabled through `deployment/seccomp/crawler.json` and
`deployment/apparmor/cat-egory-crawler` (no capabilities granted); see documentation/OPERATIONS.md.

The DB side of the pipeline (dispatch, DNS pre-check, persist, reaper) lives in `backend/app/scan_tasks.py`.
