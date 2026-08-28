"""Serve the UX-0.2 state-matrix fixture with a fully passive orchestrator.

All four task kinds route to UnavailableTaskRuntime: the worker never claims
fixture Runs, while cancel/resume still exercise the real repository paths.

Usage:
    uv run --frozen python scripts/serve_ux0_fixture.py
"""

from __future__ import annotations

import os
from pathlib import Path

import uvicorn

from conflux_weave.harness.orchestration import CompositeOrchestrator, UnavailableTaskRuntime
from conflux_weave.runtime import LocalArtifactStore, SQLiteRuntimeRepository
from conflux_weave.server import create_app

ROOT = Path("tmp/ux0-states")

repository = SQLiteRuntimeRepository(
    Path(os.environ.get("UX0_DB", str(ROOT / "states.sqlite3"))),
    LocalArtifactStore(ROOT / "artifacts"),
)
passive = UnavailableTaskRuntime(
    repository,
    executor_id="ux0-state-fixture@v1",
    task_kinds=(
        "paper_discovery",
        "verified_paper_research",
        "managed_verified_research",
        "research_fixture",
    ),
    message="UX-0 状态矩阵环境不提供新任务提交。",
)
app = create_app(
    repository,
    CompositeOrchestrator(repository, (passive,)),
    provider_configured=True,
)

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8766, workers=1)
