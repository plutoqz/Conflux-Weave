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
| `var/traces/` | optional exported spans | non-authoritative; never owns Run state |
| `var/evaluations/` | raw evaluation runs and reports | internal quality artifacts |

Do not place secrets in `var/`. Do not commit any file from this tree by force.
W3.1 implements a checksum-protected SQLite migration plus SHA-256 content
addressing, idempotent writes, integrity checks, and atomic Artifact publication.
The current database schema covers Task, Run/Step, Delivery, Artifact metadata,
W3.2 Attempt/Lease/Event records, W3.3 Step policies and external-effect state,
W3.4 Budget limit/reservation/actual/release plus structured Error lineage, and
W3.5 sanitized telemetry-drop diagnostics. Optional Trace export remains outside
the authoritative state and Artifact paths.
The durable paper-discovery path exchanges five JSON checkpoints through the
Artifact store. User-facing CLI status views, a deployed Trace backend, indexes,
imports, exports, logs, and evaluation consumers remain planned for their named
acceptance points.

## Offline installation smoke

Run the deterministic package/API/Run/Delivery/Citation/Workbench check without
Provider configuration or network access:

```text
conflux-weave offline-smoke
```

The default uses a temporary data root. To retain the SQLite database and Artifacts for
inspection, pass `--data-root <local-directory>`. A successful JSON result must keep
`label=offline_smoke`, all external-call counters at zero, Citation and Evidence counts
equal, and Workbench assets equal to `app.js`, `index.html`, and `styles.css`.

Troubleshooting boundaries:

- `fixture schema or label is invalid`: reinstall from an intact wheel or sdist; do not
  edit packaged fixture bytes in place.
- Artifact integrity or SQLite errors: use a new writable `--data-root`; preserve the
  failed root for inspection rather than deleting evidence.
- Missing Workbench assets: rebuild/reinstall the distribution and inspect package
  contents. Do not fetch replacement JavaScript from a CDN.
- Provider configuration is not required. If the command attempts a network or Provider
  call, stop and report it as a contract violation.
