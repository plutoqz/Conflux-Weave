# Personal research representative cases v1.0.0

Status: `frozen`

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

## Frozen decisions

The user reviewed and accepted the case set on 2026-08-21 with these decisions:

1. Project and repository identity resolution is a normal network-search
   responsibility. The user is not expected to provide URLs for `pi agent` or
   `DeepSeek harness`; unresolved ambiguity must remain visible in the result.
2. GIS scope excludes work that is only remote-sensing image understanding or
   classification. Literature discovery accepts preprints when their status is
   explicit.
3. Review-paper reading notes default to Chinese.
4. RAG diagnosis may receive case-level results, configuration, traces, and a
   pipeline description, but those inputs are not guaranteed. Missing optional
   evidence lowers diagnosis strength instead of blocking all advice.

This directory is immutable after freeze. Any semantic or byte-level case
change requires a new dataset version and Manifest; do not edit this version in
place after it has been committed.
