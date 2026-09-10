"""Runner for Projects Workbench visual & layout audit."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.request

from conflux_weave.harness.fixture_runtime import ResearchFixtureRuntime
from conflux_weave.harness.orchestration import CompositeOrchestrator
from conflux_weave.harness.workspace import LocalWorkspaceAdapter
from conflux_weave.runtime import LocalArtifactStore, SQLiteRuntimeRepository
from conflux_weave.server import create_app
import uvicorn
import threading

PORT = 8799
ROOT = Path("tmp/projects-audit")
ROOT.mkdir(parents=True, exist_ok=True)


def start_server():
    store = LocalArtifactStore(ROOT / "artifacts")
    repository = SQLiteRuntimeRepository(ROOT / "db" / "fixture.sqlite3", store)
    workspace = LocalWorkspaceAdapter(
        ROOT / "workspace",
        Path("src/conflux_weave/system"),
        store,
    )
    fixture = ResearchFixtureRuntime(repository, store, workspace)
    app = create_app(
        repository,
        CompositeOrchestrator(repository, (fixture,)),
        provider_configured=False,
        config_paths={
            "database": str(ROOT / "db" / "fixture.sqlite3"),
            "artifact_root": str(ROOT / "artifacts"),
            "workspace_root": str(Path.cwd()),
        },
    )
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")


def wait_for_server():
    for _ in range(30):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/v1/projects", timeout=2) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            time.sleep(0.5)
    return False


def main():
    print(f"启动审计服务器 on http://127.0.0.1:{PORT} ...")
    t = threading.Thread(target=start_server, daemon=True)
    t.start()

    if not wait_for_server():
        print("服务器启动超时！")
        sys.exit(1)
    print("服务器就绪，启动 Playwright 布局审计脚本...")

    env = os.environ.copy()
    env["CONFLUX_WEAVE_WORKBENCH_URL"] = f"http://127.0.0.1:{PORT}"

    res = subprocess.run(["node", "scripts/audit_projects_workbench.mjs"], env=env)
    print(f"审计完成，退出码: {res.returncode}")
    sys.exit(res.returncode)


if __name__ == "__main__":
    main()
