# Local runtime data

`var/` is the development-time local data root and is ignored by Git except for
this contract. Production or user installations may point the same logical
layout at an external directory through configuration introduced with its
runtime consumer.

Planned layout:

| Path | Contents | Authority |
|---|---|---|
| `var/db/weave.sqlite3` | Task, Run, Step, Evidence, and metadata | authoritative |
| `var/artifacts/sha256/` | immutable source snapshots and outputs | content authority |
| `var/indexes/` | FTS/vector indexes | derived, rebuildable |
| `var/imports/` | staged legacy exports and import manifests | staging only |
| `var/exports/` | explicit user exports | generated delivery |
| `var/logs/` | local diagnostic logs | non-authoritative |
| `var/evaluations/` | raw evaluation runs and reports | internal quality artifacts |

Do not place secrets in `var/`. Do not commit any file from this tree by force.
Artifact paths must become content-addressed and immutable when the W1/W3
storage consumer is implemented; this README does not claim that behavior is
already available.
