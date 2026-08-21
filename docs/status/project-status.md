# Project status

Updated: 2026-08-21

| Area | Status | Evidence boundary |
|---|---|---|
| Design v0.2 | `proposed` | Current design basis; not implementation proof |
| Repository framework | `validated_offline` | Python 3.12.9; 16 tests passed; sdist/wheel built |
| W0 | `in_progress` | W0.1-W0.2 passed; contract mapping and legacy inventory remain |
| W1-W6 | `pending` | No product vertical slice has been implemented |
| Live capability | `not_validated` | No Provider, network, or real research run executed |

Current single acceptance point: map the frozen 12 representative cases in
`datasets/regression/personal-research-v1.0.0/` to the minimum Core, Evidence,
confirmation, and degradation contracts before implementing a W1 workflow.

Latest framework verification:

```text
uv sync --frozen --python 3.12       passed
uv run --frozen pytest               16 passed
uv run --frozen python -m compileall passed
uv build                             sdist and wheel built
```
