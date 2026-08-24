# Local runtime data

`var/` is the development-time local data root and is ignored by Git except for
this contract. Production or user installations may point the same logical
layout at an external directory through configuration introduced with its
runtime consumer.

Runtime layout:

| Path | Contents | Authority |
|---|---|---|
| `var/db/conflux-weave.sqlite3` | W3 runtime state and metadata | authoritative; W3.1 Repository implemented |
| `var/artifacts/sha256/` | immutable source snapshots and outputs | content authority; W3.1 atomic publication implemented |
| `var/indexes/` | FTS/vector indexes | derived, rebuildable |
| `var/imports/` | staged legacy exports and import manifests | staging only |
| `var/exports/` | explicit user exports | generated delivery |
| `var/logs/` | local diagnostic logs | non-authoritative |
| `var/evaluations/` | raw evaluation runs and reports | internal quality artifacts |

Do not place secrets in `var/`. Do not commit any file from this tree by force.
W3.1 implements a checksum-protected SQLite migration plus SHA-256 content
addressing, idempotent writes, integrity checks, and atomic Artifact publication.
The current database schema is limited to Task, initial Run/Step, Delivery, and
Artifact metadata needed by W3.1. Worker, Attempt/Lease, checkpoint recovery,
Budget ledger, structured diagnostics, Trace, indexes, imports, exports, logs,
and evaluation consumers remain planned for their named acceptance points.
