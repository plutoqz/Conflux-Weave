# Project status

Updated: 2026-08-26

| Area | Status | Evidence boundary |
|---|---|---|
| Design v0.3 | `proposed` | Current product and architecture direction; not implementation proof |
| v0.2 design generation | `deprecated` | Preserved under versioned deprecated directories |
| Existing runtime | `legacy_v0.2_implemented` | SQLite, Artifact, Evidence, recovery, FastAPI and Workbench assets remain in source |
| Existing live capability | `bounded_retrieval_evidence` | Historical qwen3.7flash arXiv/GitHub runs; not full RAG or multi-Agent proof |
| v0.3 implementation plan | `not_frozen` | No P0 contract or implementation plan has been accepted yet |

The v0.2 W0-W5 plans are no longer active gates. Their implementation and
validation evidence remains available at `docs/plans/deprecated/v0.2/`; the
unmodified detailed status snapshot is at
`docs/status/deprecated/v0.2/project-status-v0.2.md`.

The current working tree contains ongoing v0.2-era source and W5 evidence changes.
Archiving the documents does not revert those changes or invalidate their bounded
historical evidence. Reuse in v0.3 requires checking the current source revision
against the new Harness, virtual filesystem, communication, context and RAG
contracts.

The next design action is to refine and freeze v0.3 P0. No v0.3 implementation or
new capability validation is claimed by this documentation reorganization.
