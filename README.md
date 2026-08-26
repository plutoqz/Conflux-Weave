# Conflux-Weave

Conflux-Weave is a proposed local-first personal research and engineering agent
workbench. The v0.3 direction combines paper discovery, document analysis, deep
research, project understanding, personal memory, Skill/MCP integration and a
unified conversation interface on a shared observable Harness.

## Project entry points

- [Current v0.3 design](docs/design/current/Conflux-Weave设计文档v0.3.md)
- [Current project status](docs/status/project-status.md)
- [Documentation governance](docs/README.md)
- [Deprecated v0.2 documentation](docs/deprecated/v0.2/README.md)
- [Architecture decisions](docs/decisions/README.md)
- [Versioned dataset policy](datasets/README.md)
- [Local runtime data policy](var/README.md)

## Current implementation boundary

The repository still contains reusable v0.2-era SQLite, Artifact, Evidence,
recovery, FastAPI and Workbench implementation. Historical validation proves only
the bounded tasks recorded in the deprecated W0-W5 evidence. It does not prove the
v0.3 multi-Agent, complete RAG, Memory, Skill/MCP or unified workbench capabilities.

No v0.3 implementation plan has been frozen yet.

## Development verification

```powershell
uv sync --frozen --python 3.12
uv run --frozen pytest
uv build
```

Local databases, artifacts, indexes, evaluation outputs, secrets and private
research data must not be committed.
