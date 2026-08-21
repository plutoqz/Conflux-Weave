# Conflux-Weave

Conflux-Weave is being initialized as an evidence-native, local-first Research
Agent Workbench. The repository is currently in W0: the validation framework
exists, but no Research Query product path or live capability has been delivered.

## Project entry points

- [Current design](docs/design/current/Conflux-Weave设计文档v0.2.md)
- [W0 implementation plan](docs/plans/current/W0-实施方案.md)
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

Local databases, artifacts, indexes, evaluation outputs, secrets, and private
research data must not be committed. See `var/README.md` for the planned runtime
layout.
