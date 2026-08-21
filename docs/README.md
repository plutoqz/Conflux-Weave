# Documentation governance

Project documents are organized by authority and lifecycle:

| Location | Purpose |
|---|---|
| `design/current/` | current approved or proposed design basis |
| `design/archive/` | superseded designs, preserved without rewriting history |
| `plans/current/` | active implementation plans and their acceptance gates |
| `plans/archive/` | completed or superseded plans with final status recorded |
| `decisions/` | accepted architecture decision records (ADRs) |
| `status/` | concise implementation and validation state |

Rules:

1. A design, plan, implementation, offline validation, live validation, and
   production claim are distinct evidence states.
2. Superseded documents move to `archive/`; they are not overwritten to make
   history look current.
3. A new core concept or dependency requires an ADR only when the decision is
   accepted. Draft discussion stays in the active plan.
4. Generated traces, provider responses, databases, reports, and evaluation
   outputs are runtime artifacts under `var/`, not project documentation.
5. Each active plan names scope, non-goals, data inputs, acceptance evidence,
   rollback/recovery, stop conditions, and the next single acceptance point.

The current design basis is
[`design/current/Conflux-Weave设计文档v0.2.md`](design/current/Conflux-Weave设计文档v0.2.md).
