"""Serve the UX-1 workbench verification fixture on 127.0.0.1:8767.

Offline environment for the UX-1 browser verification:

- ``research_fixture`` Runs execute for real through the offline fixture
  runtime (no network, no Provider).
- Chat submissions to ``/api/v1/tasks/verified-research`` are rewritten to
  ``research_fixture`` at the orchestrator boundary so the unified-entry flow
  is exercisable without a corpus or model credentials. The stored Run stays an
  honest ``research_fixture`` Run — family labels and metadata are not faked.
- Provider is deliberately unconfigured (``provider_configured=False``) so the
  overview alert and settings guidance render their not-ready states.

Usage:
    uv run --frozen python scripts/serve_ux1_fixture.py
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import uvicorn
from dotenv import dotenv_values

from conflux_weave.harness.contracts import TaskSubmission
from conflux_weave.harness.fixture_runtime import (
    FIXTURE_TASK_KIND,
    ResearchFixtureRuntime,
)
from conflux_weave.harness.orchestration import CompositeOrchestrator
from conflux_weave.harness.workspace import LocalWorkspaceAdapter
from conflux_weave.runtime import LocalArtifactStore, SQLiteRuntimeRepository
from conflux_weave.server import create_app

ROOT = Path("tmp/ux1-fixture")
DOTENV_PATH = ROOT / "settings.env"

_VERIFIED_KINDS = {"verified_paper_research", "managed_verified_research"}


class ChatFixtureOrchestrator(CompositeOrchestrator):
    """Route chat submissions of verified kinds into the offline fixture."""

    def submit(self, submission: TaskSubmission):
        if submission.task_kind in _VERIFIED_KINDS:
            objective = str(submission.input.get("objective", "")).strip()
            submission = replace(
                submission,
                task_kind=FIXTURE_TASK_KIND,
                input={"objective": objective},
                requested_agent=None,
            )
        return super().submit(submission)


def main() -> None:
    store = LocalArtifactStore(ROOT / "artifacts")
    repository = SQLiteRuntimeRepository(ROOT / "db" / "fixture.sqlite3", store)
    workspace = LocalWorkspaceAdapter(
        ROOT / "workspace",
        Path("src/conflux_weave/system"),
        store,
    )
    fixture = ResearchFixtureRuntime(repository, store, workspace)
    if not DOTENV_PATH.exists():
        DOTENV_PATH.parent.mkdir(parents=True, exist_ok=True)
        DOTENV_PATH.write_text(
            "# UX-1 fixture provider configuration\n"
            "CONFLUX_WEAVE_PROVIDER_BASE_URL=https://127.0.0.1:1/v1\n"
            "CONFLUX_WEAVE_PROVIDER_API_KEY=sk-fixture-abcdef\n"
            "CONFLUX_WEAVE_PROVIDER_MODEL=qwen3.7-flash\n",
            encoding="utf-8",
        )
    app = create_app(
        repository,
        ChatFixtureOrchestrator(repository, (fixture,)),
        provider_configured=False,
        dotenv_path=DOTENV_PATH,
        config_paths={
            "database": str(ROOT / "db" / "fixture.sqlite3"),
            "artifact_root": str(ROOT / "artifacts"),
            "workspace_root": str(ROOT / "workspace"),
            "corpus_manifest": "未导入",
            "lancedb_root": "未导入",
            "dotenv": str(DOTENV_PATH),
        },
    )
    uvicorn.run(app, host="127.0.0.1", port=8767, workers=1)


if __name__ == "__main__":
    main()
