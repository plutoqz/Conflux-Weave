# Project status

Updated: 2026-08-21

| Area | Status | Evidence boundary |
|---|---|---|
| Design v0.2 | `proposed` | Current design basis; not implementation proof |
| Repository framework | `validated_offline` | Python 3.12.9; 58 tests passed; compileall, lock check, console entry point, and sdist/wheel built |
| W0 | `validated_offline` | W0.1-W0.5 passed; 12 frozen cases, runtime contracts, and read-only legacy inventory are consistent |
| W1 | `in_progress` | W1.0-W1.2 validated offline; CLI/runtime shell and local document Evidence/Citation loop exist; no Provider, network, or research answer yet |
| W2-W6 | `pending` | No later product slice has been implemented |
| Live capability | `not_validated` | No Provider, network, or real research run executed |

Current single acceptance point: implement W1.3's official GitHub repository
SearchPort Adapter, candidate discovery Artifact, identity normalization, and
single registered-source boundary; validate recorded responses, disambiguation,
and network failure offline.

Latest framework verification:

```text
uv sync --frozen --python 3.12       passed
uv run --frozen pytest               58 passed
uv run --frozen python -m compileall passed
uv build                             sdist and wheel built
```
