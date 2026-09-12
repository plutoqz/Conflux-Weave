# Conflux-Weave

Conflux-Weave is a local-first personal research and engineering agent workbench.
The v0.3 direction combines paper discovery, document analysis, deep research,
project understanding, personal memory, Skill/MCP integration and a unified
conversation interface on a shared observable Harness.

## Project entry points

- [Current v0.3 design](docs/design/current/Conflux-Weave设计文档v0.3.md)
- [Current project status](docs/status/project-status.md)
- [Documentation governance](docs/README.md)
- [Completed implementation plans](docs/plans/completed/)
- [Deprecated v0.2 documentation](docs/deprecated/v0.2/README.md)
- [Architecture decisions](docs/decisions/README.md)
- [Versioned dataset policy](datasets/README.md)
- [Local runtime data policy](var/README.md)

## Current implementation boundary

The v0.3 delivery route is implemented phase by phase on the single FastAPI
boundary (`src/conflux_weave/server.py`, ~80 `/api/v1` endpoints) with a packaged
React workbench (`web/` built into `src/conflux_weave/workbench/dist/`, served
locally with zero external CDN):

- S0/S1: Harness contracts, durable Task/Run/Step/Delivery state, BM25 + LanceDB
  Dense hybrid retrieval with RRF and rerank, single-Agent and Manager verified
  research with citation-closed deliveries, live acceptance closed as
  `validated_live`.
- W1-W3.6: research report writing (fact cards, layered delivery, no-degrade
  deterministic fallback) and the three answer modes — direct chat (A), local
  RAG QA (B) and GPT-Researcher-backed deep research with per-paragraph local
  fusion (C).
- P2: PDF image asset extraction with lineage, image vector indexing,
  cross-modal retrieval, Evidence inspector.
- P3: project containers with architecture walkthrough, theory-to-code mapping,
  Git semantic diff, contract audit and a guarded CodingAgent patch flow.
- P4: unified conversation router (fast/slow channels), hierarchical memory
  store with HITL candidate approval, modern React workbench.
- P5: declarative Skills, dual MCP gateway (external client + local academic
  MCP server over SSE/stdio), async agent event bus and concurrent DAG
  scheduling.

Phase-level status and per-phase evidence boundaries are tracked in
[docs/status/project-status.md](docs/status/project-status.md); claims are
bounded to offline mechanism validation plus the live evidence recorded there.
v0.2-era assets remain reusable but their W0-W5 validation no longer gates
anything.

## Development verification

Backend (Python 3.12, uv-managed):

```powershell
uv sync --frozen --python 3.12
uv run --frozen pytest
uv build
```

Workbench frontend (pnpm; build output is committed under
`src/conflux_weave/workbench/dist/`):

```powershell
cd web
pnpm install
pnpm build
```

Run the local server and open the workbench:

```powershell
uv run conflux-weave serve   # http://127.0.0.1:8000 (React UI at /; /modern forces it,
                             # CONFLUX_LEGACY_UI=1 restores the legacy vanilla UI)
```

Local databases, artifacts, indexes, evaluation outputs, secrets and private
research data must not be committed.
