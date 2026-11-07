# integrations

Source Integration Engine (Phase 5). Each connector downloads a third-party list, normalises every entry
with `common.domains.normalize_domain`, merges duplicates through `domain_source_records`, and records
licence/attribution per source. Candidate sources and their licence status: [`sources.yaml`](sources.yaml).

A source can only be enabled when `license_status: verified`. Threat feeds write to `threat_intelligence`;
category lists write `domain_categories` rows with `origin = source` — never mixed with our own CC BY data.
