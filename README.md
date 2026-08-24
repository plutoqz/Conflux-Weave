# Conflux-Weave

Conflux-Weave is an evidence-native, local-first Research Agent Workbench under
bounded development. W0 is validated offline; W1 and W2 are validated live at
their frozen source, task, and budget boundaries. The accepted live slices cover
local review-note generation, GitHub project evidence Q&A, and arXiv
metadata/abstract paper discovery. They do not establish full-text verification,
cross-database review, general multi-source RAG, or production capability.

## Project entry points

- [Current design](docs/design/current/Conflux-Weave设计文档v0.2.md)
- [Completed W2 implementation plan](docs/plans/archive/W2-实施方案.md)
- [Completed W1 implementation plan](docs/plans/archive/W1-实施方案.md)
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

Validate W1.4 missing-input, no-answer, partial-source, source-failure, and
budget-failure semantics without external calls:

```powershell
uv run --frozen conflux-weave validate-outcome --query "缺少综述" --scenario missing_input
uv run --frozen conflux-weave validate-outcome --query "严格检索" --scenario no_answer
uv run --frozen conflux-weave validate-outcome --query "来源不完整" --scenario source_partial
uv run --frozen conflux-weave validate-outcome --query "来源失败" --scenario source_failure
uv run --frozen conflux-weave validate-outcome --query "预算耗尽" --scenario budget_failure
```

These commands validate deterministic contracts only. In particular, the
budget scenario does not implement or prove live token or cost metering.

Discover public GitHub repository candidates without treating rank as official
identity proof:

```powershell
uv run --frozen conflux-weave search-github --query "pi coding agent" --limit 10
uv run --frozen conflux-weave search-github --query "pi coding agent" --limit 10 --select "owner/repo"
```

`GITHUB_TOKEN` is optional and read only from the local environment. It is never
written to an Artifact or repository file.

Run the W1.5 evidence-bound live repository identity workflow after creating an
ignored local `.env` from `.env.example`:

```powershell
uv run --frozen conflux-weave research-repository --query "定位 pi coding agent 的规范名称、维护者、官方仓库 URL 和公开实现入口"
```

The live command performs one GitHub search, one README fetch, and one model
call without automatic retry or fallback. Provider requests and raw responses
are stored as ignored content-addressed Artifacts without the API key. A
`partial` result means that useful cited claims were produced while an explicit
acceptance criterion remains unmet.

Create a cited Chinese reading note from an authorized local review PDF:

```powershell
uv run --frozen conflux-weave review-document "path/to/review.pdf" --query "总结综述，解释术语并指出展开不足的点"
```

Run the W2 bounded arXiv metadata/abstract paper-discovery workflow:

```powershell
uv run --frozen conflux-weave discover-papers --query "研究问题" --search-query "arXiv search terms"
```

The command preserves source and Provider responses as ignored Artifacts,
compiles Claim/Evidence/Citation mappings, and reports explicit source and
full-text limitations. It performs no automatic retry or fallback.

The W1 and W2 implementation milestones are validated live at their frozen
scopes. See the project status and archived acceptance records for accepted,
rejected, and failed runs; deterministic tests or HTTP success are not treated
as substitutes for those bounded live results.

Local databases, artifacts, indexes, evaluation outputs, secrets, and private
research data must not be committed. See `var/README.md` for the planned runtime
layout.
