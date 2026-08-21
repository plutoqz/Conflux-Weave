# Project status

Updated: 2026-08-21

| Area | Status | Evidence boundary |
|---|---|---|
| Design v0.2 | `proposed` | Current design basis; not implementation proof |
| Repository framework | `validated_offline` | Python 3.12.9; 33 tests passed; sdist/wheel built |
| W0 | `in_progress` | W0.1-W0.3 passed; read-only legacy inventory and final offline acceptance remain |
| W1-W6 | `pending` | No product vertical slice has been implemented |
| Live capability | `not_validated` | No Provider, network, or real research run executed |

Current single acceptance point: perform a read-only inventory of legacy Conflux
evaluation datasets, test corpora, and historical Runs; classify export/archive/
reject actions without copying or importing data.

Latest framework verification:

```text
uv sync --frozen --python 3.12       passed
uv run --frozen pytest               33 passed
uv run --frozen python -m compileall passed
uv build                             sdist and wheel built
```
