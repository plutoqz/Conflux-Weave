# Project status

Updated: 2026-08-21

| Area | Status | Evidence boundary |
|---|---|---|
| Design v0.2 | `proposed` | Current design basis; not implementation proof |
| Repository framework | `validated_offline` | Python 3.12.9; 96 tests passed; compileall, lock check, console entry point, and sdist/wheel built |
| W0 | `validated_offline` | W0.1-W0.5 passed; 12 frozen cases, runtime contracts, and read-only legacy inventory are consistent |
| W1 | `in_progress` | W1.0-W1.2 and W1.4 validated offline; W1.3 SearchPort live; W1.5 LIVE-02 delivered partial cited output with qwen3.7-flash; LIVE-01 and user use decision remain |
| W2-W6 | `pending` | No later product slice has been implemented |
| Live capability | `partial_slice` | One real GitHub + Provider repository-identity run produced 4 claims and 5 closed citations; independent official status and the review-document task remain unverified |

Current single acceptance point: finish W1.5 with an authorized real review
document for W1-LIVE-01, then record the user's actual use decision for at least
one result. Provider configuration is retained in ignored local `.env`; the
review-document branch remains `blocked_external` until a real input is present.

Latest framework verification:

```text
uv sync --frozen --python 3.12       passed
uv run --frozen pytest               96 passed
uv run --frozen python -m compileall passed
uv build                             sdist and wheel built
```
