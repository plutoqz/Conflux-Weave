# Personal research representative cases v1

Status: `awaiting_user_review`

This dataset translates the user's recurring research questions into W0 product
acceptance semantics. It is not an answer key, benchmark score, prompt suite, or
evidence that the Research Query workflow works.

## Lineage and coverage

Cases `CW-PR-001` through `CW-PR-008` correspond directly to the eight user
examples provided on 2026-08-21. Cases `CW-PR-009` through `CW-PR-012` preserve
failure and boundary behavior needed by the current design.

| Cases | Coverage |
|---|---|
| 001-004 | current-state, implementation, ontology, and architecture synthesis |
| 005 | literature discovery and deduplication |
| 006 | grounded reading notes from a supplied review |
| 007 | evidence-led Agent evaluation design |
| 008 | artifact-grounded RAG diagnosis |
| 009-010 | constrained discovery and honest no-answer behavior |
| 011 | missing required attachment and resumable confirmation |
| 012 | primary-source failure and partial delivery |

## Annotation semantics

- `origin=user_seeded` preserves a direct user need. `derived_boundary` is a
  testable variant introduced to cover failure semantics, not a new product.
- `expected_outcome` is the correct delivery class under the frozen input, not
  a precomputed runtime result.
- `required_evidence` defines the minimum source support needed for the
  deliverable. It does not authorize network or Provider calls.
- `allowed_confirmation` lists questions that may materially change correctness.
  The runtime should not ask them when existing input already resolves the issue.
- `acceptable_degradation` defines how to remain useful without fabricating
  evidence or silently relaxing constraints.

## Review required before freeze

The user must confirm or amend:

1. Whether the 12 cases represent normal weekly use with no missing major task.
2. The exact project identities intended by `pi agent` and `DeepSeek harness`.
3. Whether GIS includes remote sensing and whether preprints are acceptable by
   default for literature discovery.
4. Whether case 006 should default to a bilingual or Chinese-only reading note.
5. Whether RAG diagnosis normally receives only metric summaries or also
   case-level results, configuration, traces, and pipeline descriptions.

After review, create a new immutable dataset version if case semantics change.
Only a review that accepts the current bytes may change this version's Manifest
status and every case `annotation_status` to `frozen`.
