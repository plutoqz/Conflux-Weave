import asyncio
import json

from conflux_weave.core import (
    BudgetLedger,
    DeliveryDisposition,
    DeliveryRecord,
    RunRecord,
    RunStatus,
    StepRecord,
    StepStatus,
    TaskSpec,
)
from conflux_weave.runtime import LocalArtifactStore, SQLiteRuntimeRepository
from conflux_weave.runtime.durable_paper_shared import RANK_CHECKPOINT
from conflux_weave.server import WorkerLoop, create_app


NOW = "2026-08-25T12:00:00Z"


class PassiveRuntime:
    executor_id = "passive-paper@v1"
    task_kinds = ("paper_discovery",)

    def work_once(self, *, now: str | None = None) -> None:
        return None

    def submit(self, *args, **kwargs):
        raise AssertionError("submission is not used by this fixture")

    def request_cancel(self, *args, **kwargs):
        raise AssertionError("cancellation is not used by this fixture")

    def resume(self, *args, **kwargs):
        raise AssertionError("resume is not used by this fixture")


def route(app, path: str):
    return next(item.endpoint for item in app.routes if item.path == path)


def build_completed_app(tmp_path):
    store = LocalArtifactStore(tmp_path / "artifacts")
    repository = SQLiteRuntimeRepository(
        tmp_path / "db" / "runtime.sqlite3", store, clock=lambda: NOW
    )
    task = TaskSpec(
        task_id="task-workbench",
        kind="paper_discovery",
        input={"query": "How does durable recovery preserve evidence?"},
        requested_policy="fixed-arxiv-v1",
        idempotency_key="workbench-fixture",
    )
    run = RunRecord(
        run_id="run-workbench",
        task_id=task.task_id,
        status=RunStatus.ACCEPTED,
        workflow_version="fixed-arxiv-v1",
        config_snapshot_ref="fixture-config",
        budget=BudgetLedger(60, 100, 100, "unavailable", 1, 1, 1),
        created_at=NOW,
        updated_at=NOW,
    )
    steps = (
        StepRecord(
            step_id="step-workbench-rank",
            run_id=run.run_id,
            kind="rank_candidates",
            attempt=1,
            status=StepStatus.PENDING,
        ),
        StepRecord(
            step_id="step-workbench-publish",
            run_id=run.run_id,
            kind="publish_delivery",
            attempt=1,
            status=StepStatus.PENDING,
        ),
    )
    repository.submit_task(task, run, steps)
    repository.transition_run(run.run_id, RunStatus.QUEUED, updated_at=NOW)
    rank_claim = repository.claim_next_step("fixture-worker", lease_seconds=60, now=NOW)
    assert rank_claim is not None
    checkpoint = store.put_json(
        {
            "schema_version": RANK_CHECKPOINT,
            "evidence": [
                {
                    "evidence_id": "evidence-workbench-1",
                    "source_snapshot_id": "source-snapshot-1",
                    "locator": {"section": "abstract", "paragraph": 2},
                    "quote": "Durable state keeps delivery lineage available after restart.",
                    "extraction_method": "structured-fixture",
                }
            ],
        },
        producer_step_id=rank_claim.step_id,
        schema_version=RANK_CHECKPOINT,
    )
    repository.complete_attempt(rank_claim, (checkpoint,), now=NOW)
    publish_claim = repository.claim_next_step("fixture-worker", lease_seconds=60, now=NOW)
    assert publish_claim is not None
    report = store.put_bytes(
        b"# Durable recovery\n\nPersisted evidence remains inspectable [1].\n",
        media_type="text/markdown; charset=utf-8",
        producer_step_id=publish_claim.step_id,
        schema_version="conflux-weave.paper-discovery-report.v1",
    )
    unregistered = store.put_bytes(
        b"not published",
        media_type="text/plain",
        producer_step_id=publish_claim.step_id,
        schema_version="fixture.private.v1",
    )
    delivery = DeliveryRecord(
        run_id=run.run_id,
        disposition=DeliveryDisposition.PARTIAL,
        artifact_refs=(report.artifact_id,),
        evidence_refs=("evidence-workbench-1",),
        limitations=("Fixture evidence only.",),
        unmet_criteria=("No live Provider validation.",),
        recovery_actions=("Create a separately authorized live Run.",),
    )
    repository.publish_delivery(
        run.run_id,
        RunStatus.PARTIAL,
        delivery,
        (report,),
        claim=publish_claim,
        published_at=NOW,
    )
    runtime = PassiveRuntime()
    app = create_app(
        repository,
        runtime,
        provider_configured=False,
        worker=WorkerLoop(runtime, interval_seconds=10),
    )
    return app, report, unregistered


def test_workbench_is_packaged_same_origin_without_external_assets(tmp_path) -> None:
    app, _, _ = build_completed_app(tmp_path)
    index_response = asyncio.run(route(app, "/")())
    index = index_response.path.read_text(encoding="utf-8")
    workbench_root = index_response.path.parent
    styles = (workbench_root / "styles.css").read_text(encoding="utf-8")
    script = (workbench_root / "app.js").read_text(encoding="utf-8")
    notices = (workbench_root / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")

    assert index_response.media_type == "text/html"
    assert 'id="run-list"' in index
    assert 'id="task-dialog"' in index
    assert 'class="mode-switch"' in index
    assert 'id="retry-run"' in index
    assert 'id="fail-run"' in index
    assert 'id="refresh-run"' in index
    assert 'id="rerun-run"' in index
    assert 'id="follow-up-run"' in index
    assert 'value="single" checked' in index
    assert 'value="managed"' in index
    assert 'id="follow-up-dialog"' in index
    assert 'id="evidence-inspector"' in index
    assert 'id="hud-toggle"' in index
    assert 'id="activity-tab" role="tab" aria-selected="false" aria-controls="activity-panel" tabindex="-1"' in index
    assert "@media (max-width: 760px)" in styles
    assert "EventSource" in script
    assert "/api/v1/tasks/research" in script
    assert "/api/v1/tasks/research-fixture" in script
    assert "/api/v1/tasks/verified-research" in script
    assert "/follow-up`" in script
    assert "/rerun`" in script
    assert "updateTaskMode" in script
    assert "retry_unknown_external" in script
    assert "fail_unknown_external" in script
    assert "eventCursor" in script
    assert "events?after=${state.eventCursor}" in script
    assert "state.eventReconnectTimer" in script
    assert "state.eventRunId !== payload.run_id" in script
    assert "handleTabKeydown" in script
    assert "state.inspectorTrigger" in script
    assert '$("#hud-corpus").textContent = context.corpus_scope' in script
    assert "if (run.is_terminal) return" not in script
    assert "overflow-wrap: anywhere" in styles
    assert "grid-template-columns: 1fr" in styles
    assert ".research-context" in styles
    assert "repeat(4, minmax(0, 1fr))" in styles
    assert "http://" not in index + styles + script
    assert "https://" not in index + styles + script
    assert "Lucide Static 1.34.0" in notices
    assert "ISC License" in notices


def test_workbench_ux1_sections_are_local_and_wired(tmp_path) -> None:
    from pathlib import Path

    app, _, _ = build_completed_app(tmp_path)
    index_response = asyncio.run(route(app, "/")())
    index = index_response.path.read_text(encoding="utf-8")
    workbench_root = index_response.path.parent
    modules = (workbench_root / "modules")
    module_sources = {
        item.name: item.read_text(encoding="utf-8")
        for item in sorted(modules.glob("*.js"))
    }
    shared = module_sources.get("shared.js", "")
    router = module_sources.get("router.js", "")
    overview = module_sources.get("overview.js", "")
    chat = module_sources.get("chat.js", "")
    settings = module_sources.get("settings.js", "")

    assert {"shared.js", "router.js", "overview.js", "chat.js", "settings.js"} <= set(module_sources)
    assert 'id="overview-view"' in index
    assert 'id="chat-view"' in index
    assert 'id="settings-view"' in index
    assert 'data-section-link="overview"' in index
    assert 'data-section-link="chat"' in index
    assert 'data-section-link="research"' in index
    assert 'data-section-link="settings"' in index
    assert 'id="provider-form"' in index
    assert 'id="cfg-api-key" name="api_key" type="password"' in index
    assert 'id="chat-input"' in index
    assert 'id="overview-checks"' in index
    assert 'id="chat-empty-title"' in index
    assert 'id="chat-empty-description"' in index

    app_source = (workbench_root / "app.js").read_text(encoding="utf-8")
    assert 'from "./modules/shared.js' in app_source
    assert "initRouter" in (workbench_root / "app.js").read_text(encoding="utf-8")
    assert "registerView" in router and "hashchange" in router
    assert 'shell.dataset.toc = "closed"' in router
    assert 'shell.dataset.inspector = "closed"' in router
    assert "/api/v1/health/ready" in overview
    assert "/api/v1/config" in settings and "/api/v1/config/provider" in settings
    assert "/api/v1/tasks/deep-research" in chat
    assert "/api/v1/chat" in chat
    assert "/follow-up" in chat
    assert "EventSource" in chat
    assert "MODE_INTRO" in chat
    assert "emptyDescription.textContent" in chat
    assert 'data-mode="auto"' in index
    assert 'id="chat-autocomplete"' in index
    assert 'id="settings-memory-section"' in index
    assert "/api/v1/memories" in settings
    assert "renderMemoryStudio" in settings
    assert "chat-route-badge" in chat
    assert "memory-candidate-bubble" in chat

    combined = "".join(module_sources.values())
    assert "http://" not in combined
    assert "https://" not in combined


def test_workbench_w55_layout_and_keyboard_contracts_are_local_and_responsive() -> None:
    from pathlib import Path

    root = Path(__file__).parents[1] / "src" / "conflux_weave" / "workbench"
    index = (root / "index.html").read_text(encoding="utf-8")
    styles = (root / "styles.css").read_text(encoding="utf-8")
    script = (root / "app.js").read_text(encoding="utf-8")

    # These are the DOM/CSS contracts exercised by the fixture browser run at
    # 320px, 390px and 200% zoom; no external browser runtime is required here.
    assert 'aria-label="研究历史"' in index
    assert 'role="tablist"' in index
    assert 'aria-label="证据检查器"' in index
    assert '<dialog id="task-dialog"' in index
    assert "calc(100vw - 32px)" in styles
    assert "overflow-wrap: anywhere" in styles
    assert "showModal()" in script
    assert 'event.key === "ArrowRight"' in script
    assert "trigger?.isConnected" in script
    assert "event.preventDefault()" in script


def test_workbench_ux32_zen_focus_and_right_pane_tri_pane_contracts() -> None:
    from pathlib import Path

    root = Path(__file__).parents[1] / "src" / "conflux_weave" / "workbench"
    index = (root / "index.html").read_text(encoding="utf-8")
    styles = (root / "styles.css").read_text(encoding="utf-8")
    script = (root / "app.js").read_text(encoding="utf-8")
    shared = (root / "modules" / "shared.js").read_text(encoding="utf-8")

    # Zen Focus Mode contracts
    assert 'id="run-rail-mini"' in index
    assert ".run-rail-mini" in styles
    assert ".run-mini-dot" in styles
    assert "--sidebar-w: 48px" in styles
    assert "renderRunRailMini" in script
    assert 'event.key === "["' in script

    # Adaptive Studio Tri-Pane & Right Pane Tab Fusion contracts
    assert 'id="insp-pane-tabs"' in index
    assert 'id="tab-toc-from-insp"' in index
    assert 'id="tab-evidence-from-insp"' in index
    assert 'tab-evidence-from-toc' in shared
    assert "conflux:open-inspector" in shared
    assert "conflux:open-inspector" in script
    assert ".right-pane-tabs" in styles
    assert ".right-pane-tab" in styles
    assert ".report-toc-rail" in styles
    assert "grid-column: 3" in styles
    assert '.app-shell[data-inspector="open"] .report-toc-rail' in styles

    # Strict offline / zero-CDN guarantee
    for file_content in (index, styles, script, shared):
        assert "http://" not in file_content
        assert "https://" not in file_content


def test_library_uses_persistent_tabs_and_configurable_result_count() -> None:
    from pathlib import Path

    root = Path(__file__).parents[1] / "src" / "conflux_weave" / "workbench"
    index = (root / "index.html").read_text(encoding="utf-8")
    library = (root / "modules" / "library.js").read_text(encoding="utf-8")

    tabs_at = index.index('class="library-subnav"')
    documents_at = index.index('id="library-documents-panel"')
    papers_at = index.index('id="library-papers-panel"')
    assert tabs_at < documents_at < papers_at
    assert 'id="paper-back-documents"' not in index
    assert 'id="paper-result-limit"' in index
    assert 'id="library-load-more"' in index
    assert 'value="20" selected' in index
    assert 'document.getElementById("paper-result-limit").value' in library
    assert 'max_results: "10"' not in library
    assert 'library.js?v=v0.3-library-ux-3' in (root / "app.js").read_text(encoding="utf-8")
    assert "item.doi" in library and "item.arxiv_id" in library and "item.authors" in library
    assert 'match.textContent = "正文命中"' in library
    assert 'api(`/api/v1/library/search?${params}`)' in library
    assert 'id="library-selection-all"' in index
    assert 'document.getElementById("library-selection-all").addEventListener' in library
    assert 'start += 100' in library
    assert 'limit: "100"' in library
    assert "text-indent: 2em" in (root / "styles.css").read_text(encoding="utf-8")
    assert "item.match_snippet" in library
    assert 'retry.addEventListener("click", () => runPaperSearch(source.source))' in library
    assert 'id="paper-query-understanding"' in index
    assert 'id="paper-source-details"' in index
    assert 'button.title = "使用此意图重新检索"' in library


def test_workbench_reads_only_registered_delivery_text(tmp_path) -> None:
    app, report, unregistered = build_completed_app(tmp_path)
    read_artifact = route(
        app, "/api/v1/runs/{run_id}/artifacts/{artifact_id}/content"
    )
    response = asyncio.run(read_artifact("run-workbench", report.artifact_id))
    rejected = asyncio.run(read_artifact("run-workbench", unregistered.artifact_id))

    assert response.artifact.schema_version == (
        "conflux-weave.paper-discovery-report.v1"
    )
    assert "Persisted evidence remains inspectable" in response.content
    assert rejected.status_code == 404
    assert json.loads(rejected.body) == {
        "code": "not_found",
        "message": "请求的记录不存在。",
        "recovery_action": None,
        "retryable": False,
    }


def test_workbench_p2_document_note_studio_contracts() -> None:
    from pathlib import Path

    root = Path(__file__).parents[1] / "src" / "conflux_weave" / "workbench"
    index = (root / "index.html").read_text(encoding="utf-8")
    styles = (root / "styles.css").read_text(encoding="utf-8")
    app_js = (root / "app.js").read_text(encoding="utf-8")
    notes_js = (root / "modules" / "notes.js").read_text(encoding="utf-8")
    library_js = (root / "modules" / "library.js").read_text(encoding="utf-8")

    # Verify DOM elements in index.html
    assert 'id="document-analyze-dialog"' in index
    assert 'id="document-note-dialog"' in index
    assert 'id="note-html-frame"' in index
    assert 'id="note-md-view"' in index
    assert 'id="note-patch-input"' in index
    assert 'id="note-patch-submit"' in index
    assert 'id="note-studio-revisions"' in index
    assert 'id="note-tab-html"' in index
    assert 'id="note-tab-md"' in index

    # Verify CSS rules
    assert ".document-note-dialog" in styles
    assert ".note-studio-container" in styles
    assert ".note-patch-studio" in styles
    assert ".library-note-button" in styles

    # Verify JS modules wiring
    assert 'import "./modules/notes.js"' in app_js
    assert "openAnalyzeDialog" in library_js
    assert "library-note-button" in library_js
    assert "AI 研读 / 生成权威笔记" in library_js
    assert "/api/v1/documents/analyze" in notes_js
    assert "/api/v1/notes/" in notes_js
    assert "/patch" in notes_js
    assert "/revisions" in notes_js
    assert "status === 409" in notes_js

    # Verify layout alignment and dialog close contracts
    assert '.app-shell:not([data-section="research"]) .workspace' in styles
    assert 'grid-column: 1' in styles
    assert 'id="analyze-dialog-close"' in index
    assert 'type="button"' in index
    assert "analyze-dialog-close" in notes_js
    assert "border-bottom: 2px solid var(--moss)" in styles

    # Verify zero external CDN
    for js_src in (index, styles, app_js, notes_js):
        assert "http://" not in js_src
        assert "https://" not in js_src


def test_workbench_p3_projects_and_code_studio_contracts() -> None:
    from pathlib import Path

    root = Path(__file__).parents[1] / "src" / "conflux_weave" / "workbench"
    index = (root / "index.html").read_text(encoding="utf-8")
    styles = (root / "styles.css").read_text(encoding="utf-8")
    app_js = (root / "app.js").read_text(encoding="utf-8")
    router_js = (root / "modules" / "router.js").read_text(encoding="utf-8")
    projects_js = (root / "modules" / "projects.js").read_text(encoding="utf-8")

    # Verify DOM anchors in index.html
    assert 'data-section-link="projects"' in index
    assert 'id="projects-view"' in index
    assert 'id="project-select"' in index
    assert 'id="project-git-card"' in index
    assert 'id="proj-git-branch"' in index
    assert 'id="project-file-tree"' in index
    assert 'id="proj-code-viewer"' in index
    assert 'id="proj-diff-viewer"' in index
    assert 'id="proj-qa-input"' in index
    assert 'id="proj-qa-submit"' in index
    assert 'id="proj-coding-propose-btn"' in index
    assert 'id="proj-prop-apply-btn"' in index

    # Verify CSS rules
    assert ".projects-view" in styles
    assert ".projects-layout" in styles
    assert ".projects-sidebar" in styles
    assert ".projects-stage" in styles
    assert ".projects-agent-panel" in styles
    assert ".project-code-viewer" in styles
    assert ".project-diff-viewer" in styles

    # Verify router & module wiring
    assert 'projects: "projects-view"' in router_js
    assert 'import "./modules/projects.js"' in app_js
    assert "/api/v1/projects" in projects_js
    assert "/coding/propose" in projects_js
    assert "/coding/apply" in projects_js

    # Verify zero external CDN
    for js_src in (index, styles, app_js, router_js, projects_js):
        assert "http://" not in js_src
        assert "https://" not in js_src


def test_modern_react_shadcn_workbench_is_packaged_and_zero_cdn(tmp_path) -> None:
    from pathlib import Path
    app, _, _ = build_completed_app(tmp_path)
    modern_endpoint = route(app, "/modern")
    response = asyncio.run(modern_endpoint())
    assert response.media_type == "text/html"

    dist_index = Path(response.path)
    assert dist_index.is_file()
    html_content = dist_index.read_text(encoding="utf-8")

    assert '<div id="root"></div>' in html_content
    assert 'src="/assets/' in html_content
    assert 'href="/assets/' in html_content

    # Strict zero external CDN in modern index HTML
    assert "http://" not in html_content
    assert "https://" not in html_content


def test_workbench_p5_skills_studio_and_mcp_gateway_contracts() -> None:
    from pathlib import Path

    root = Path(__file__).parents[1] / "src" / "conflux_weave" / "workbench"
    index = (root / "index.html").read_text(encoding="utf-8")
    styles = (root / "styles.css").read_text(encoding="utf-8")
    app_js = (root / "app.js").read_text(encoding="utf-8")
    router_js = (root / "modules" / "router.js").read_text(encoding="utf-8")
    skills_js = (root / "modules" / "skills.js").read_text(encoding="utf-8")
    settings_js = (root / "modules" / "settings.js").read_text(encoding="utf-8")

    # Verify DOM elements in index.html
    assert 'data-section-link="skills"' in index
    assert 'id="skills-view"' in index
    assert 'id="skills-card-grid"' in index
    assert 'id="skill-runner-modal"' in index
    assert 'id="skill-modal-title"' in index
    assert 'id="skill-modal-form"' in index
    assert 'id="skill-modal-submit-btn"' in index
    assert 'id="settings-mcp-section"' in index
    assert 'id="mcp-servers-list"' in index
    assert 'id="mcp-register-dialog"' in index
    assert 'id="mcp-config-snippet"' in index

    # Verify CSS rules in styles.css
    assert ".skills-view" in styles
    assert ".skills-card-grid" in styles
    assert ".skill-card-item" in styles
    assert ".skill-runner-dialog" in styles
    assert ".mcp-server-card" in styles
    assert ".mcp-export-card" in styles

    # Verify router & module wiring
    assert 'skills: "skills-view"' in router_js
    assert 'import "./modules/skills.js"' in app_js
    assert "/api/v1/skills" in skills_js
    assert "/api/v1/mcp/servers" in settings_js

    # Verify zero external CDN
    for js_src in (index, styles, app_js, router_js, skills_js, settings_js):
        assert "http://" not in js_src
        assert "https://" not in js_src




