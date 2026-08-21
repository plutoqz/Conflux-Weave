# Project status

Updated: 2026-08-21

| Area | Status | Evidence boundary |
|---|---|---|
| Design v0.2 | `proposed` | Current design basis; not implementation proof |
| Repository framework | `validated_offline` | Python 3.12.9; 39 tests passed; compileall and sdist/wheel built |
| W0 | `validated_offline` | W0.1-W0.5 passed; 12 frozen cases, runtime contracts, and read-only legacy inventory are consistent |
| W1-W6 | `pending` | No product vertical slice has been implemented |
| Live capability | `not_validated` | No Provider, network, or real research run executed |

Current single acceptance point: freeze W1's minimal Research Query vertical
slice, real inputs, completion criteria, Provider/network authorization boundary,
and validation set before implementation.

Latest framework verification:

```text
uv sync --frozen --python 3.12       passed
uv run --frozen pytest               39 passed
uv run --frozen python -m compileall passed
uv build                             sdist and wheel built
```
