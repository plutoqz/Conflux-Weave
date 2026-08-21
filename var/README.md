# Local runtime data

`var/` is the development-time local data root and is ignored by Git except for
this contract. Production or user installations may point the same logical
layout at an external directory through configuration introduced with its
runtime consumer.

Planned layout:

| Path | Contents | Authority |
|---|---|---|
| `var/db/weave.sqlite3` | Task, Run, Step, Evidence, and metadata | authoritative |
| `var/artifacts/sha256/` | immutable source snapshots and outputs | content authority; W1.1 store implemented |
| `var/indexes/` | FTS/vector indexes | derived, rebuildable |
| `var/imports/` | staged legacy exports and import manifests | staging only |
| `var/exports/` | explicit user exports | generated delivery |
| `var/logs/` | local diagnostic logs | non-authoritative |
| `var/evaluations/` | raw evaluation runs and reports | internal quality artifacts |

Do not place secrets in `var/`. Do not commit any file from this tree by force.
W1.1 implements SHA-256 content addressing, idempotent writes, and integrity
checks for `var/artifacts/sha256/`. The database, indexes, imports, exports,
logs, and evaluation paths remain planned until their named consumers exist.
