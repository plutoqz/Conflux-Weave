from __future__ import annotations

from pathlib import Path
import sys

from run_s15_live_matrix import main


if __name__ == "__main__":
    defaults = [
        "--dataset",
        str(Path("datasets/regression/s16-worker-verifier-closeout-live-v1")),
        "--database",
        str(Path("var/acceptance/v0.3-s1/s16f-closeout.sqlite3")),
        "--output",
        str(Path("var/acceptance/v0.3-s1/s16f-closeout-summary.json")),
        "--idempotency-namespace",
        "s16f",
        "--summary-schema-version",
        "conflux-weave.s16f-closeout-summary.v1",
    ]
    sys.argv[1:1] = defaults
    main()
