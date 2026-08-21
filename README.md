# Conflux-Weave

Conflux-Weave is being initialized as an evidence-native, local-first Research
Agent Workbench. W0 is validated offline and the W1 scope is frozen, but no
Research Query product path or live capability has been delivered. W1.1 exposes
only a deterministic runtime validation command.

## Project entry points

- [Current design](docs/design/current/Conflux-Weave设计文档v0.2.md)
- [Current W1 implementation plan](docs/plans/current/W1-实施方案.md)
- [W0 implementation plan](docs/plans/archive/W0-实施方案.md)
- [Project status](docs/status/project-status.md)
- [Documentation governance](docs/README.md)
- [Versioned dataset policy](datasets/README.md)
- [Local runtime data policy](var/README.md)

## Framework verification

```powershell
uv sync --frozen --python 3.12
uv run --frozen pytest
uv build
```

Validate the W1.1 CLI and content-addressed Artifact path without source,
network, or model calls:

```powershell
uv run --frozen conflux-weave validate-workflow --query "validate fixed workflow"
```

The command reports `validation_only: true`; it does not produce a research
answer or a user Delivery.

Local databases, artifacts, indexes, evaluation outputs, secrets, and private
research data must not be committed. See `var/README.md` for the planned runtime
layout.
