# ADR 0002: Use FastAPI for the single HTTP boundary

- Status: Accepted
- Date: 2026-08-24
- Milestone: W5.1

## Context

W5 must expose the existing SQLite-authoritative Fixed Workflow through one local
service, one OpenAPI contract, cursor-based event projection, and a packaged static
Workbench. The first-success path must not require Node.js or a second frontend
service. HTTP and validation code must remain outside Core, Evidence, and Runtime
authority.

The alternatives were:

1. FastAPI with Uvicorn and packaged static HTML/CSS/ES modules.
2. FastAPI plus React/Vite, which adds a Node.js build and release chain before a
   second UI consumer exists.
3. Streamlit, Gradio, or another service, which introduces a second service/state
   boundary and does not naturally preserve the frozen SQLite/SSE recovery model.

## Decision

Use `fastapi>=0.115,<1` for the single ASGI/OpenAPI boundary and
`uvicorn>=0.34,<1` as its single-process runner. W5.1 uses Pydantic models for stable
request/response contracts but does not start an ASGI application. W5.2 may create
the one application and one lifespan-managed Worker after separate authorization.

The Workbench will be packaged static assets served by the same application and
origin. Node.js, a CDN, a second HTTP service, multiple Uvicorn processes, and an
in-memory authority are not admitted by this decision.

FastAPI and Uvicorn may depend on their normal runtime dependencies. LangGraph,
Phoenix, Ragas, DeepEval, and frontend build tooling remain excluded from the default
installation.

## Consequences

- API validation and OpenAPI schemas have one typed contract.
- SQLite remains authoritative for Run state and events; FastAPI does not own state.
- SSE will be a later projection over persisted `run_events`, not an in-memory bus.
- The initial install gains an HTTP framework and runner, but no Node.js toolchain.
- Removing FastAPI must not invalidate Runtime, Evidence, CLI, or historical Runs.
- W5.1 tests prove contracts and read queries only, not server startup, SSE transport,
  browser usability, clean-environment onboarding, or live capability.

## Rollback

Remove the HTTP adapter and its two direct dependencies. The existing CLI,
`fixed-arxiv-v1`, SQLite schema, Artifact store, and W1-W4 evidence remain valid and
readable because this decision does not change their authority or persistence format.
