# Taxonomy

The category tree lives in [`classification/taxonomy.yaml`](../classification/taxonomy.yaml) (CC BY 4.0) and
is loaded into the `categories` table by `python -m database.seed`. Current version: **3.0.0** (2026-11-18: new
content category `piracy`, code 610, for films, series, music, sports streams, software, games or books offered
without the rights holder's licence, and their block-evasion mirrors; `streaming-media`, `file-sharing` and
`software-downloads` narrowed to licensed or free content. Major, because those three changed meaning: a filter
that allows streaming but not pirate sites allows `streaming-media` and blocks `piracy`). 2.0.0 (2026-11-15:
`gambling` narrowed to operators licensed in at least one country; new content category `illegal-gambling`, code
609, for unlicensed sites and block-evasion mirrors). 1.1.0 (2026-11-08) added the risk category
`lookalike-domain`, code 908.

## Structure

- **Groups** (`group: true`, codes x00) organise the tree and are never assigned.
- **Content categories** (`kind: content`) describe what a site is about. A domain has exactly one primary
  and up to five secondary content categories.
- **Security-threat categories** (`kind: risk`, codes 9xx) are a separate risk classification. A site *about*
  security is `cybersecurity` (content), not a threat.

| Code range | Group |
|---|---|
| 100 | Business & Technology |
| 200 | Communication & Social |
| 300 | Entertainment |
| 400 | Education & Information |
| 500 | Shopping & Lifestyle |
| 600 | Restricted Content |
| 700 | Web Infrastructure (incl. `parked-domains`, 709) |
| 900 | Security Threats (risk) |

## Stability rules

- `id` (slug) and `code` are permanent: never renamed, never reused.
- Removing a category = set `deprecated_in` and `replaced_by`; old assignments stay in history.
- Semantic versioning: new category → minor; deprecation or changed meaning → major; wording → patch.
- Each assignment records the `taxonomy_version` it was made under.

## Relation to commercial taxonomies

Commercial web-filter taxonomies (e.g. FortiGuard) were consulted only for coverage ideas (which kinds of
sites filters typically need to distinguish). Names, definitions, IDs and all data here are our own; no
proprietary category database or descriptions were copied.
