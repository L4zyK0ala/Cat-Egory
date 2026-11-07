# Database ER Diagram

Source of truth: [`database/models.py`](../database/models.py) and the Alembic migration
[`0001`](../database/migrations/versions/20261107_0001_initial_schema.py). A test
(`tests/integration/test_schema.py::test_migrations_match_models`) fails if they drift apart.

## Core: domains, classification, history

```mermaid
erDiagram
    domains ||--o{ urls : has
    domains ||--o{ domain_categories : "assigned (temporal)"
    domains ||--o{ domain_source_records : "reported by"
    domains ||--o{ scan_jobs : scanned
    domains ||--o{ ai_predictions : "predicted for"
    domains ||--o{ threat_intelligence : "reported threats"
    domains ||--o{ review_tasks : "needs review"
    domains }o--|| categories : "primary (cache)"
    urls ||--o{ url_categories : "assigned (temporal)"
    categories ||--o{ categories : "parent"
    categories ||--o{ domain_categories : ""
    categories ||--o{ url_categories : ""
    ai_predictions ||--o{ domain_categories : "justifies"
    ai_predictions ||--o{ evidence : "claims"
    data_sources ||--o{ domain_categories : "provenance"
    data_sources ||--o{ domain_source_records : ""
    data_sources ||--o{ threat_intelligence : ""
    data_sources }o--|| source_licenses : "licensed under"

    domains {
        bigint id PK
        varchar domain "display (Unicode)"
        varchar normalized_domain UK "ASCII / punycode"
        varchar registrable_domain "eTLD+1"
        varchar public_suffix
        varchar primary_category_id FK "cache of current state"
        varchar_arr secondary_categories "cache"
        numeric confidence
        varchar classification_status
        timestamptz first_seen
        timestamptz last_seen
        timestamptz last_scanned
        timestamptz next_scan
        int scan_count
        bigint source_id FK "first source"
        varchar source_license
        bool is_active
        timestamptz created_at
        timestamptz updated_at
    }
    categories {
        varchar id PK "stable slug"
        smallint code UK "stable number"
        varchar parent_id FK
        varchar kind "content | risk"
        varchar name
        text description
        bool is_group
        varchar introduced_in
        varchar deprecated_in
        varchar replaced_by_id FK
    }
    domain_categories {
        bigint id PK
        bigint domain_id FK
        varchar category_id FK
        bool is_primary
        numeric confidence
        varchar origin "ai | human | community | source"
        bigint source_id FK
        bigint ai_prediction_id FK
        bigint assigned_by_id FK
        varchar taxonomy_version
        timestamptz valid_from
        timestamptz valid_to "NULL = current"
    }
    urls {
        bigint id PK
        bigint domain_id FK
        text url
        text normalized_url
        char64 url_hash UK
    }
    url_categories {
        bigint id PK
        bigint url_id FK
        varchar category_id FK
        timestamptz valid_from
        timestamptz valid_to
    }
    ai_predictions {
        bigint id PK
        bigint domain_id FK
        bigint scan_result_id FK
        varchar provider
        varchar model
        varchar prompt_version
        varchar taxonomy_version
        varchar validation_status
        jsonb validation_errors
        varchar primary_category_id FK
        varchar_arr secondary_category_ids
        varchar_arr risk_flags
        numeric confidence
        text description "model claim"
        bool requires_human_review
        jsonb raw_response
        int input_tokens
        int output_tokens
        int latency_ms
    }
    evidence {
        bigint id PK
        bigint ai_prediction_id FK "exactly one owner"
        bigint community_submission_id FK
        varchar type
        text observation
        text reference
        bool verified "humans only"
    }
    threat_intelligence {
        bigint id PK
        bigint domain_id FK
        bigint url_id FK
        varchar threat_type
        bigint source_id FK
        text source_reference
        numeric confidence
        bool is_active
    }
    review_tasks {
        bigint id PK
        bigint domain_id FK
        bigint ai_prediction_id FK
        varchar reason
        varchar status
        varchar decision_primary_id FK
    }
    data_sources {
        bigint id PK
        varchar slug UK
        varchar kind
        text feed_url
        varchar data_format
        bigint license_id FK
        int update_interval_seconds
        bool enabled
        timestamptz last_success_at
        numeric quality_score
    }
    source_licenses {
        bigint id PK
        varchar slug UK "SPDX id"
        bool redistribution_allowed
        bool commercial_use_allowed
        bool attribution_required
        bool share_alike
    }
    domain_source_records {
        bigint id PK
        bigint domain_id FK
        bigint source_id FK
        varchar source_category
        timestamptz first_seen
        timestamptz last_seen
    }
```

## Scanning and artefacts

```mermaid
erDiagram
    domains ||--o{ scan_jobs : ""
    urls ||--o{ scan_jobs : ""
    users ||--o{ scan_jobs : "requested by"
    scan_jobs ||--o| scan_results : produces
    scan_results ||--o{ screenshots : ""
    scan_results ||--o| extracted_content : ""
    scan_results ||--o{ ai_predictions : "input for"

    scan_jobs {
        uuid id PK
        bigint domain_id FK
        varchar reason
        smallint priority "0-9"
        varchar status "queued running succeeded failed blocked cancelled"
        smallint attempts
        varchar error_code
        timestamptz queued_at
        timestamptz finished_at
    }
    scan_results {
        bigint id PK
        uuid scan_job_id FK,UK
        text requested_url
        text final_url
        smallint http_status
        jsonb redirect_chain
        inet_arr resolved_ips
        jsonb dns
        jsonb tls
        jsonb response_headers
        text page_title
        text meta_description
        jsonb open_graph
        bool robots_allowed
        varchar blocked_reason
    }
    screenshots {
        bigint id PK
        text object_key "object store"
        char64 sha256
        varchar visibility "private | public"
        timestamptz retention_until
    }
    extracted_content {
        bigint id PK
        text html_object_key
        text text_excerpt
        varchar visibility
        timestamptz retention_until
    }
```

## Community, accounts, audit, distribution

```mermaid
erDiagram
    users ||--o{ api_keys : owns
    users ||--o{ community_submissions : submits
    users ||--o{ community_votes : casts
    community_submissions ||--o{ community_votes : ""
    community_submissions ||--o{ evidence : "supporting"
    community_submissions ||--o{ false_positive_reports : ""
    domains ||--o{ false_positive_reports : ""
    users ||--o{ audit_logs : actor
    api_keys ||--o{ audit_logs : actor

    users {
        bigint id PK
        varchar email UK
        varchar role "admin moderator contributor"
        text password_hash "Argon2id"
    }
    api_keys {
        bigint id PK
        bigint user_id FK
        varchar prefix UK
        char64 key_hash
        varchar_arr scopes
        int rate_limit_per_minute
        timestamptz revoked_at
    }
    community_submissions {
        bigint id PK
        varchar type
        bigint domain_id FK
        varchar submitted_domain
        varchar_arr proposed_category_ids
        varchar submitter_fingerprint "keyed hash, no raw IP"
        varchar status
        bigint moderator_id FK
    }
    community_votes {
        bigint id PK
        bigint submission_id FK
        bigint user_id FK
        smallint vote "+1 / -1"
    }
    false_positive_reports {
        bigint id PK
        bigint domain_id FK
        varchar category_id FK
        bool is_false_negative
        text reason
        varchar status
    }
    audit_logs {
        bigint id PK
        timestamptz occurred_at
        varchar action
        varchar target_type
        varchar target_id
        jsonb before
        jsonb after
        varchar request_id
    }
    feed_releases {
        bigint id PK
        varchar feed
        varchar version
        varchar license
        int record_count
        char64 sha256
        text object_key
    }
    taxonomy_versions {
        bigint id PK
        varchar version UK
        bool is_current "only one true"
    }
```

## Integrity rules enforced by the database

| Rule | Mechanism |
|---|---|
| One row per site | `UNIQUE (domains.normalized_domain)` + `CHECK (normalized_domain = lower(normalized_domain))` |
| One current assignment per (domain, category) | partial unique index `WHERE valid_to IS NULL` |
| Exactly one current primary category per domain | partial unique index `WHERE valid_to IS NULL AND is_primary` |
| History is consistent | `CHECK (valid_to IS NULL OR valid_to >= valid_from)` |
| Only known categories | FK to `categories(id)` (`ON DELETE RESTRICT`) |
| Confidence in [0, 1] | `CHECK` on every confidence column |
| Enum values | `VARCHAR + CHECK` constraints |
| Evidence belongs to one thing | `CHECK (num_nonnulls(ai_prediction_id, community_submission_id) = 1)` |
| One threat report per source | unique `(domain, threat_type, source, coalesce(url_id, 0))` |
| One vote per user | `UNIQUE (submission_id, user_id)` |
| One current taxonomy | partial unique index `WHERE is_current` |

## Example: category of a domain at a point in time

```sql
SELECT dc.category_id, dc.is_primary, dc.origin, dc.confidence
FROM domain_categories dc
JOIN domains d ON d.id = dc.domain_id
WHERE d.normalized_domain = 'example.com'
  AND dc.valid_from <= TIMESTAMPTZ '2026-06-01'
  AND (dc.valid_to IS NULL OR dc.valid_to > TIMESTAMPTZ '2026-06-01');
```
