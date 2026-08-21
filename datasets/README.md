# Versioned datasets

This directory is the only repository location for small, reviewable datasets
used by deterministic smoke, regression, or benchmark checks. It must not hold
private research corpora, provider responses, indexes, databases, or generated
evaluation output.

Create a dataset directory only after its cases and consumer are defined. Each
dataset version must contain:

- `manifest.json`: dataset ID, version, purpose, schema version, source lineage,
  case count, file hashes, and creation/review metadata.
- `cases.jsonl`: immutable case records with stable `case_id` values.
- `README.md`: annotation semantics, inclusion/exclusion rules, known limits,
  and the product decision the dataset supports.

Dataset layers follow the design contract:

- `smoke`: small deterministic cases suitable for every commit.
- `regression`: frozen replays and representative cases used before merge.
- `benchmark`: larger fixed comparisons run explicitly before release.
- `live`: manifests only. Raw live inputs and outputs belong under `var/` or an
  external authorized store and require explicit run authorization.

Historical data from the legacy Conflux repository enters only through a
validated export manifest. Historical scores are not Conflux-Weave results.

## Current representative cases

- Frozen: [`regression/personal-research-v1.0.0/`](regression/personal-research-v1.0.0/)
- Preserved pre-freeze draft: [`regression/personal-research-v1/`](regression/personal-research-v1/)

The frozen version is immutable. Any semantic change requires a new version.
