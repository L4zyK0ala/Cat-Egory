# crawler

Phase 3. Playwright + Chromium worker that consumes the `crawl` queue. Design (see
`documentation/ARCHITECTURE.md` §5.2–5.3):

- runs in its own image and network; **no database credentials**
- SSRF guard: URL validation → DNS answer validation (IPv4/IPv6, all special ranges) → egress proxy that
  re-checks and pins the IP at connect time → network-level egress rules
- robots.txt, per-host politeness, timeouts, retries, response-size caps, downloads disabled
- produces a `CrawlResult` (DNS, status, redirect chain, headers, title/meta/OG, visible text, HTML,
  screenshot object keys) for the core worker to persist
