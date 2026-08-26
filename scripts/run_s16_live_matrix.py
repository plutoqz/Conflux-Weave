from __future__ import annotations

from pathlib import Path
import sys

from run_s15_live_matrix import main


if __name__ == "__main__":
    defaults = [
        "--dataset",
        str(Path("datasets/regression/s16-post-remediation-live-v1")),
        "--database",
        str(Path("var/acceptance/v0.3-s1/s16c-matrix.sqlite3")),
        "--output",
        str(Path("var/acceptance/v0.3-s1/s16c-matrix-summary.json")),
        "--idempotency-namespace",
        "s16c",
        "--summary-schema-version",
        "conflux-weave.s16c-live-matrix-summary.v1",
    ]
    sys.argv[1:1] = defaults
    main()
