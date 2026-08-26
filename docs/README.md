# Documentation governance

Project documents are organized by authority and lifecycle:

| Location | Purpose |
|---|---|
| `design/current/` | current approved or proposed design basis |
| `design/deprecated/` | superseded design generations, preserved without rewriting history |
| `plans/current/` | active implementation plans and their acceptance gates |
| `plans/deprecated/` | plans and acceptance evidence retired with a superseded design |
| `decisions/` | accepted architecture decision records (ADRs) |
| `status/` | concise implementation and validation state |
| `status/deprecated/` | historical status snapshots tied to retired designs |
| `deprecated/` | other retired project documents, including root README snapshots |

Rules:

1. A design, plan, implementation, offline validation, live validation, and
   production claim are distinct evidence states.
2. Superseded documents move to a versioned `deprecated/<design-version>/`
   directory; they are not overwritten to make history look current.
3. A new core concept or dependency requires an ADR only when the decision is
   accepted. Draft discussion stays in the active plan.
4. Generated traces, provider responses, databases, reports, and evaluation
   outputs are runtime artifacts under `var/`, not project documentation.
5. Historical paths recorded inside frozen JSON and evidence documents may refer
   to their original location. Do not rewrite those bytes merely to repair links.
6. Each active plan names scope, non-goals, data inputs, acceptance evidence,
   rollback/recovery, stop conditions, and the next acceptance point.

The current design basis is
[`design/current/Conflux-Weave设计文档v0.3.md`](design/current/Conflux-Weave设计文档v0.3.md).

The v0.2 design generation, W0-W5 plans, acceptance records, and status snapshot
are indexed from [`deprecated/v0.2/README.md`](deprecated/v0.2/README.md).
