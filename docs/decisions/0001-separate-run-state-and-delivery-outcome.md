# ADR 0001: Separate Run State and Delivery Outcome

Status: Accepted

Date: 2026-08-21

## User problem and context

The frozen representative cases require four observably different results:
complete delivery, honest no-answer, partial delivery, and a pause for required
user input. A single status string cannot say both whether execution has ended
and what useful result the user received.

Cases `CW-PR-010`, `CW-PR-011`, and `CW-PR-012` expose the ambiguity directly.
The same distinction also applies to source loss, incomplete attachments, and
evidence limitations across the ordinary cases.

## Decision

- `RunStatus` owns operational lifecycle and recovery state.
- `DeliveryDisposition` owns `complete`, `partial`, and `no_answer` semantics.
- A constrained search that correctly finds no qualifying answer is a
  `succeeded` Run with a `no_answer` delivery, not a system failure.
- A `partial` Run must publish a committed Artifact and identify unmet criteria.
- Missing required input creates a `UserInputRequest` and transitions the Run to
  `waiting_for_user`; it must not create a guessed answer.
- `ClaimAssessment` uses typed evidence relations and verdicts so contradiction
  or insufficiency cannot be silently treated as support.

## Alternatives considered

1. Add `no_answer` to `RunStatus`. Rejected because it mixes business outcome
   with lifecycle and complicates retry/recovery semantics.
2. Store limitations only in report prose. Rejected because Runtime and UI could
   not deterministically distinguish complete and degraded delivery.
3. Model each research scenario as a Core subtype. Rejected because project
   identity resolution, ontology structure, and RAG diagnostics do not yet have
   multiple runtime consumers and belong in later workflow artifacts.

## Golden-path impact and rollback

Without this decision, W1 can report false failures for valid no-answer results
or false success for incomplete work. Rollback is limited to the W0 contracts;
no database, API, or persisted Run exists yet. A later change requires a new ADR
and migration analysis once persistence becomes a consumer.

## Current consumers and evidence

- `no_answer`: `CW-PR-010`.
- `waiting_for_user`: `CW-PR-006`, `CW-PR-008`, `CW-PR-010`, `CW-PR-011` and
  other cases with conditional clarification.
- `partial`: `CW-PR-012` and general source/evidence degradation behavior.
- typed Evidence assessment: all cited-answer cases, especially conflicting or
  insufficient evidence paths.

Evidence is limited to frozen case-contract coverage and deterministic offline
tests. No W1 workflow, persistence, API, Provider, or live capability is proven.
