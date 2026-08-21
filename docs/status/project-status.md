# Project status

Updated: 2026-08-21

| Area | Status | Evidence boundary |
|---|---|---|
| Design v0.2 | `proposed` | Current design basis; not implementation proof |
| Repository framework | `validated_offline` | Python 3.12.9; 82 tests passed; compileall, lock check, console entry point, and sdist/wheel built |
| W0 | `validated_offline` | W0.1-W0.5 passed; 12 frozen cases, runtime contracts, and read-only legacy inventory are consistent |
| W1 | `in_progress` | W1.0-W1.2 and W1.4 validated offline; W1.3 SearchPort validated on live GitHub; no Provider, generated research answer, or end-to-end user task yet |
| W2-W6 | `pending` | No later product slice has been implemented |
| Live capability | `component_only` | Public GitHub repository search and explicit source registration validated; no Provider or end-to-end research run |

Current single acceptance point: W1.5 real-use acceptance. Freeze Provider,
model, Prompt, budget, and authorization for a real review document, then run
the two authorized end-to-end tasks and retain raw evidence and the user's use
decision. Until those inputs exist, live acceptance remains `blocked_external`.

Latest framework verification:

```text
uv sync --frozen --python 3.12       passed
uv run --frozen pytest               82 passed
uv run --frozen python -m compileall passed
uv build                             sdist and wheel built
```
