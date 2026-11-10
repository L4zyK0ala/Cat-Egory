# Data Licence

| Data | Licence |
|---|---|
| Categorisations created by Cat-egory (AI classification validated by our pipeline, human review, accepted community contributions) | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) — attribution: "Cat-egory contributors" |
| The taxonomy (`classification/taxonomy.yaml`) | CC BY 4.0 |
| Our own security-threat confirmations (a Cat-egory reviewer's, or a finding Octryx proved on the page): the threat lists, `security.rpz`, `profile-security.*` and the threat dataset `threats.csv` / `threats.json` | [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/) — public domain, no attribution required. Block-list tools adopt CC0 more easily. Withdrawn false positives are not in the dataset. `profile-family.*` mixes in content categories and stays CC BY 4.0 |
| Data imported from third-party lists and threat feeds | The source's own licence. Stored with provenance; redistributed only where that licence allows, with its attribution. Never relicensed under CC BY. |
| Screenshots and HTML of third-party websites | Not published. Kept private for review and deleted according to the retention policy. |

> The CC BY 4.0 choice for our own data is a **proposal pending maintainer/legal review**, including whether
> the terms of the AI provider allow publishing model outputs. Until confirmed, published data is marked as
> preview.

Every feed and API response states the licence and attribution that apply to it.

Domain names that reached Cat-egory through a third-party list keep that list's provenance (shown per domain).
Approved lists: none yet. Candidates are in `integrations/sources.yaml`; the first planned discovery source is
the Majestic Million (© Majestic, CC BY 3.0).
