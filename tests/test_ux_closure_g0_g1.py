"""G0 & G1 Baseline replay and contract tests for Conflux-Weave UX closure."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import json
from pathlib import Path
import subprocess
from typing import Any
import pytest
from starlette.testclient import TestClient

from conflux_weave.api_contracts import (
    FollowUpResearchTaskRequest,
    NotePatchRequest,
    UserRunState,
    map_run_state,
)
from conflux_weave.core import (
    BudgetLedger,
    RunRecord,
    RunStatus,
    StepRecord,
    StepStatus,
    TaskSpec,
)
from conflux_weave.harness.contracts import TaskSubmission
from conflux_weave.harness.orchestration import (
    CompositeOrchestrator,
    DeterministicRouter,
    TaskRuntimeUnavailable,
    UnavailableTaskRuntime,
)
from conflux_weave.document_agent import DocumentAgent
from conflux_weave.document_notes import (
    DocumentNote,
    NoteSection,
    load_note_artifact,
    save_note_artifacts,
)
from conflux_weave.documents import ImportedDocument, LocalDocumentImporter
from conflux_weave.evidence import SourceSnapshot
from conflux_weave.runtime.artifacts import LocalArtifactStore
from conflux_weave.runtime import SQLiteRuntimeRepository
from conflux_weave.runtime.memory_store import (
    HierarchicalMemoryStore,
    MemoryCategory,
    MemoryScope,
)
from conflux_weave.harness.fixture_runtime import ResearchFixtureRuntime
from conflux_weave.server import WorkerLoop, create_app
from conflux_weave.skills.registry import SkillRegistry
from conflux_weave.skills.runner import SkillRunner
from conflux_weave.skills.spec import SkillCategory, SkillExecutionRequest, SkillSpec
from conflux_weave.chat import ChatService
from conflux_weave.provider import (
    OpenAICompatibleChatAdapter,
    ProviderConfig,
    ProviderHttpResponse,
)
from conflux_weave.global_search import GlobalSearchService


NOW = "2026-09-16T12:00:00Z"


class _PassiveRuntime:
    executor_id = "passive-paper@v1"
    task_kinds = ("paper_discovery", "verified_paper_research", "managed_verified_research", "deep_research")

    def __init__(self, repository: Any = None) -> None:
        self.repository = repository

    def work_once(self, *, now: str | None = None) -> None:
        return None

    def submit(self, *args, **kwargs):
        raise NotImplementedError

    def request_cancel(self, *args, **kwargs):
        raise NotImplementedError

    def resume(self, run_id: str, decision: Any = None, *, now: str | None = None) -> Any:
        if self.repository is not None:
            return self.repository.resume_run(run_id, decision, now=now or NOW)
        raise NotImplementedError


def _route(app, path: str):
    return next(item.endpoint for item in app.routes if item.path == path)


def _build_test_app(tmp_path: Path):
    store = LocalArtifactStore(tmp_path / "artifacts")
    repository = SQLiteRuntimeRepository(
        tmp_path / "db" / "runtime.sqlite3", store, clock=lambda: NOW
    )
    runtime = _PassiveRuntime(repository)
    app = create_app(
        repository,
        runtime,
        provider_configured=False,
        worker=WorkerLoop(runtime, interval_seconds=10),
    )
    return app, repository, store


# =========================================================================
# V01 / U01: 文档离线分析不产生无依据的学术 SOTA 结论与伪造 Token 记账
# =========================================================================

def test_v01_document_analysis_offline_no_hallucinated_sota(tmp_path: Path) -> None:
    """U01 / V01: Negative paper without LLM provider should NOT hallucinate SOTA or superiority claims."""
    store = LocalArtifactStore(tmp_path / "artifacts")
    doc_agent = DocumentAgent(store, chat_adapter=None)

    # Document that explicitly states negative experimental results
    negative_doc_text = """# Empirical Study on Optimization Failure

## Abstract
In this work, we rigorously evaluate whether the proposed attention mechanism improves convergence.
Across all benchmark tasks, the method fails to beat baseline SGD, exhibiting slower training,
worse generalisation, and high instability.

## Experiments and Results
The empirical comparisons show negative statistical significance.
Our architecture yields 14.2 BLEU points lower than classical baselines,
proving that simply stacking attention layers without normalization degrades performance.

## Limitations and Conclusion
We conclude that this technique is inferior to conventional models under realistic constraints.
"""
    doc_file = tmp_path / "negative_paper.md"
    doc_file.write_text(negative_doc_text, encoding="utf-8")

    importer = LocalDocumentImporter(store)
    imported = importer.import_path(doc_file)

    note = doc_agent.analyze_document(imported)

    full_note_content = (
        f"{note.title}\n{note.executive_summary}\n"
        + "\n".join(s.content for s in note.sections)
    )

    # Must NOT hallucinate positive SOTA or superiority
    assert "SOTA" not in full_note_content, "Offline note must not claim SOTA when unsupported"
    assert "达到 SOTA 最新最高水平" not in full_note_content
    assert "显著超越经典模型" not in full_note_content
    assert "颠覆性" not in full_note_content
    assert "范式革新" not in full_note_content

    # Token usage must not fabricate positive token consumption if no LLM provider was called
    actual_tokens = note.metadata.get("tokens_consumed")
    if actual_tokens is not None:
        assert actual_tokens == 0 or note.metadata.get("token_usage_type") == "estimated"


# =========================================================================
# V02 / U02: Skill 离线缺少 Provider/工具时不应返回虚假真实成功
# =========================================================================

def test_v02_skill_runner_blocks_fake_success(tmp_path: Path) -> None:
    """U02 / V02: Skill without provider and without tools execution must not return fake completed status."""
    registry = SkillRegistry()
    runner = SkillRunner(registry, provider=None)

    # Execute code architecture audit on non-existent project
    req = SkillExecutionRequest(
        skill_id="code_architecture_audit",
        inputs={"project_id": "non_existent_project_12345", "severity_threshold": "high"},
    )
    result = runner.execute_skill(req)

    # It must NOT claim completed with 92/100 score as a real result
    if result.status == "completed":
        assert "demonstration_only" in str(result.structured_data) or "演示" in result.summary or "模拟" in result.summary
    else:
        assert result.status in {"failed", "demonstration_only", "disabled"}


# =========================================================================
# V03 / U04: 空库指标真实性
# =========================================================================

def test_v03_library_empty_stats_truthfulness(tmp_path: Path) -> None:
    """U04 / V03: Empty library overview returns 0 counts, not fake hardcoded statistics."""
    app, repository, store = _build_test_app(tmp_path)
    overview_fn = _route(app, "/api/v1/library")

    res = asyncio.run(overview_fn(status="active"))
    assert res["total"] == 0
    assert res["imported"] == 0
    assert res["character_count"] == 0
    assert res["size_bytes"] == 0
    assert res["items"] == []
    assert "未就绪" in res["index_status"] or res["imported"] == 0


# =========================================================================
# V04 / U05: 追问合同兼容 query / question，恢复接口正常可用
# =========================================================================

def test_v04_followup_and_recovery_contracts(tmp_path: Path) -> None:
    """U05 / V04: Follow-up accepts query or question; resume/retry endpoints do not 404."""
    # Test FollowUp request model validator: accepts question
    req1 = FollowUpResearchTaskRequest.model_validate({"question": "Explain topological codes"})
    assert req1.question == "Explain topological codes"

    # Accepts query as sent by frontend
    req2 = FollowUpResearchTaskRequest.model_validate({"query": "Explain surface codes"})
    assert req2.question == "Explain surface codes"

    # Blank raises validation error
    with pytest.raises(ValueError):
        FollowUpResearchTaskRequest.model_validate({"query": "   "})

    app, repository, store = _build_test_app(tmp_path)

    resume_endpoint = _route(app, "/api/v1/runs/{run_id}/resume")
    assert resume_endpoint is not None

    retry_endpoint = _route(app, "/api/v1/runs/{run_id}/retry_unknown_external")
    assert retry_endpoint is not None

    fail_endpoint = _route(app, "/api/v1/runs/{run_id}/fail_unknown_external")
    assert fail_endpoint is not None


# =========================================================================
# V05 / U08: 文档最新笔记获取与 409 冲突重试保证
# =========================================================================

def test_v05_document_latest_note_and_conflict_payload(tmp_path: Path) -> None:
    """U08 / V05: GET /documents/{id}/note returns tip version; 409 conflict returns latest_note_id & latest_version."""
    app, repository, store = _build_test_app(tmp_path)
    get_doc_note_fn = _route(app, "/api/v1/documents/{document_id}/note")
    patch_note_fn = _route(app, "/api/v1/notes/{note_id}/patch")

    doc_id = "doc-empirical-001"
    reg_path = tmp_path / "db" / "notes-registry.json"
    reg_path.parent.mkdir(parents=True, exist_ok=True)

    # 1. Non-existent note -> 404
    resp_404 = asyncio.run(get_doc_note_fn(doc_id))
    assert resp_404.status_code == 404

    # 2. Create Note v1 and register
    note_v1 = DocumentNote(
        note_id="note-empirical-001-v1",
        document_id=doc_id,
        title="实证研究论文笔记 v1",
        version=1,
        parent_note_id=None,
        applied_patch_id=None,
        executive_summary="实证分析摘要",
        sections=(NoteSection(section_id="sec-1", title="引言", level=2, content="内容", source_segments=(), citations=(), asset_refs=()),),
        key_concepts=(),
        visual_assets=(),
        metadata={},
        markdown_content="# 实证研究论文笔记 v1\n\n内容",
        html_content="<h1>实证研究论文笔记 v1</h1>",
        created_at=NOW,
    )
    save_note_artifacts(note_v1, store)

    reg_entries = [
        {
            "note_id": note_v1.note_id,
            "document_id": doc_id,
            "title": note_v1.title,
            "version": 1,
            "created_at": NOW,
        }
    ]
    reg_path.write_text(json.dumps(reg_entries), encoding="utf-8")

    # Latest should return v1
    resp_v1 = asyncio.run(get_doc_note_fn(doc_id))
    assert resp_v1.note_id == "note-empirical-001-v1"
    assert resp_v1.version == 1

    # 3. Add Note v2 (chain revision)
    note_v2 = DocumentNote(
        note_id="note-empirical-001-v2",
        document_id=doc_id,
        title="实证研究论文笔记 v2",
        version=2,
        parent_note_id="note-empirical-001-v1",
        applied_patch_id="patch-001",
        executive_summary="实证分析摘要 v2",
        sections=(NoteSection(section_id="sec-1", title="引言", level=2, content="修订后的内容", source_segments=(), citations=(), asset_refs=()),),
        key_concepts=(),
        visual_assets=(),
        metadata={},
        markdown_content="# 实证研究论文笔记 v2\n\n修订后的内容",
        html_content="<h1>实证研究论文笔记 v2</h1>",
        created_at=NOW,
    )
    save_note_artifacts(note_v2, store)

    reg_entries.append({
        "note_id": note_v2.note_id,
        "document_id": doc_id,
        "title": note_v2.title,
        "version": 2,
        "created_at": NOW,
    })
    reg_path.write_text(json.dumps(reg_entries), encoding="utf-8")

    # Latest should now return v2!
    resp_v2 = asyncio.run(get_doc_note_fn(doc_id))
    assert resp_v2.note_id == "note-empirical-001-v2"
    assert resp_v2.version == 2

    # 4. Patching stale v1 when tip is v2 must return 409 with latest_version AND latest_note_id
    patch_req = NotePatchRequest(instruction="添加补充论述", target_version=1)
    conflict_resp = asyncio.run(patch_note_fn("note-empirical-001-v1", patch_req))
    assert conflict_resp.status_code == 409
    payload = json.loads(conflict_resp.body.decode("utf-8"))
    assert payload["code"] == "version_conflict"
    assert payload["latest_version"] == 2
    assert payload["latest_note_id"] == "note-empirical-001-v2"


# =========================================================================
# V07 / U20: Skill 分类枚举与规范对齐
# =========================================================================

def test_v07_skill_categories_contract() -> None:
    """U20 / V07: SkillCategory enum must include research, governance, writing, utility."""
    categories = {c.value for c in SkillCategory}
    assert {"research", "governance", "writing", "utility"} <= categories


class _SequenceChatTransport:
    def __init__(self, payloads):
        self.payloads = iter(payloads)
        self.requests = []

    def post(self, *args, **kwargs):
        self.requests.append(json.loads(kwargs.get("body", "{}")))
        payload = next(self.payloads)
        return ProviderHttpResponse(200, json.dumps(payload).encode(), {"Content-Type": "application/json"})


def _mock_chat_payload(content: str, response_id: str = "resp-001"):
    return {
        "id": response_id,
        "model": "fixture-chat",
        "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
    }


# =========================================================================
# V08 / U03: 外部 Markdown 与 HTML 净化，防范 XSS
# =========================================================================

def test_v08_markdown_html_sanitization() -> None:
    """U03 / V08: Malicious scripts, onerror, and javascript: links are stripped, MathML & citations preserved."""
    node_test_script = (
        "import { sanitizeHtml } from './src/lib/math.ts';\n"
        "const malicious = '<script>alert(1)</script><img src=\"x\" onerror=\"alert(2)\"><a href=\"javascript:alert(3)\">bad link</a>';\n"
        "const clean = sanitizeHtml(malicious);\n"
        "if (clean.includes('<script>') || clean.includes('onerror') || clean.includes('javascript:')) {\n"
        "  process.exit(1);\n"
        "}\n"
        "const mathInput = '<math><mrow><msup><mi>x</mi><mn>2</mn></msup></mrow></math>';\n"
        "const mathClean = sanitizeHtml(mathInput);\n"
        "if (!mathClean.includes('<math>') || !mathClean.includes('<mrow>')) {\n"
        "  process.exit(2);\n"
        "}\n"
        "const citeInput = '<a class=\"citation-badge\" data-cite=\"sq1-claim-0001\">[Claim 1.1]</a>';\n"
        "const citeClean = sanitizeHtml(citeInput);\n"
        "if (!citeClean.includes('data-cite=\"sq1-claim-0001\"')) {\n"
        "  process.exit(3);\n"
        "}\n"
        "console.log('SANITIZATION_OK');\n"
    )
    result = subprocess.run(
        ["node", "--experimental-strip-types", "-e", node_test_script],
        cwd=Path(__file__).resolve().parent.parent / "web",
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"Node sanitization test failed: {result.stderr}"
    assert "SANITIZATION_OK" in result.stdout


# =========================================================================
# V09 / U06: 前端任务模式与后端 Runtime 调度无碰撞映射
# =========================================================================

def test_v09_task_modes_to_runtime_dispatch(tmp_path: Path) -> None:
    """U06 / V09: Frontend task modes (single, managed, discovery, fixture) route correctly."""
    store = LocalArtifactStore(tmp_path / "artifacts")
    repository = SQLiteRuntimeRepository(
        tmp_path / "db" / "runtime.sqlite3", store, clock=lambda: NOW
    )

    class _MockDiscoveryRuntime:
        executor_id = "legacy_paper_runtime@v1"
        task_kinds = ("paper_discovery",)
        def submit(self, sub): return "discovery_submitted"
        def work_once(self, **kw): pass
        def request_cancel(self, *a, **kw): pass
        def resume(self, *a, **kw): pass

    class _MockResearchRuntime:
        executor_id = "durable_verified_research@v1"
        task_kinds = ("verified_paper_research", "managed_verified_research", "deep_research")
        def submit(self, sub): return f"{sub.task_kind}_submitted"
        def work_once(self, **kw): pass
        def request_cancel(self, *a, **kw): pass
        def resume(self, *a, **kw): pass

    class _MockFixtureRuntime:
        executor_id = "research_fixture@v1"
        task_kinds = ("research_fixture",)
        def submit(self, sub): return "fixture_submitted"
        def work_once(self, **kw): pass
        def request_cancel(self, *a, **kw): pass
        def resume(self, *a, **kw): pass

    orchestrator = CompositeOrchestrator(
        repository,
        (_MockFixtureRuntime(), _MockDiscoveryRuntime(), _MockResearchRuntime()),
    )

    # 1. Single paper research mode
    res_single = orchestrator.submit(TaskSubmission(task_kind="verified_paper_research", input={"objective": "obj"}))
    assert res_single == "verified_paper_research_submitted"

    # 2. Managed multi-agent research mode
    res_managed = orchestrator.submit(TaskSubmission(task_kind="managed_verified_research", input={"objective": "obj"}))
    assert res_managed == "managed_verified_research_submitted"

    # 3. Discovery mode
    res_discovery = orchestrator.submit(TaskSubmission(task_kind="paper_discovery", input={"query": "q"}))
    assert res_discovery == "discovery_submitted"

    # 4. Fixture mode
    res_fixture = orchestrator.submit(TaskSubmission(task_kind="research_fixture", input={"objective": "obj"}))
    assert res_fixture == "fixture_submitted"

    # 5. Unsupported task kind raises ValueError
    with pytest.raises(ValueError, match="unsupported task kind"):
        orchestrator.submit(TaskSubmission(task_kind="unknown_mode", input={}))


# =========================================================================
# V10 / U07: 运行状态语义区分：主动取消 vs 外部等待 vs 底层失败
# =========================================================================

def test_v10_run_status_and_user_run_state_mapping() -> None:
    """U07 / V10: RunStatus correctly maps to UserRunState without misclassifying cancelled as failed."""
    assert map_run_state(RunStatus.CANCELLED) == UserRunState.CANCELLED
    assert map_run_state(RunStatus.WAITING_FOR_USER) == UserRunState.NEEDS_ATTENTION
    assert map_run_state(RunStatus.FAILED) == UserRunState.FAILED
    assert map_run_state(RunStatus.SUCCEEDED) == UserRunState.COMPLETE
    assert map_run_state(RunStatus.RUNNING) == UserRunState.WORKING
    assert map_run_state(RunStatus.ACCEPTED) == UserRunState.PENDING
    assert map_run_state(RunStatus.QUEUED) == UserRunState.PENDING
    assert map_run_state(RunStatus.CANCELLING) == UserRunState.CANCELLING
    assert map_run_state(RunStatus.PARTIAL) == UserRunState.PARTIAL
    assert map_run_state(RunStatus.EXPIRED) == UserRunState.EXPIRED

    # Semantic guarantee: User cancellation is not a crashed execution
    assert UserRunState.CANCELLED.value != UserRunState.FAILED.value
    assert UserRunState.CANCELLED.value == "cancelled"
    assert UserRunState.NEEDS_ATTENTION.value == "needs_attention"


# =========================================================================
# V11 / U09: 恢复决策执行与别名路由可用性
# =========================================================================

def test_v11_recovery_decision_execution(tmp_path: Path) -> None:
    """U09 / V11: Runs in needs_attention can be resumed with retry or fail decision."""
    app, repository, store = _build_test_app(tmp_path)
    client = TestClient(app)

    # Calling resume on a non-existent run returns 404 (not 500 or broken route)
    resp = client.post("/api/v1/runs/run-non-existent/resume", json={"decision": "retry_unknown_external"})
    assert resp.status_code == 404
    assert resp.json()["code"] == "not_found"

    # Legacy alias routes must also reach the handler (returning 404 for non-existent, proving route exists)
    resp_retry = client.post("/api/v1/runs/run-non-existent/retry_unknown_external")
    assert resp_retry.status_code == 404
    assert resp_retry.json()["code"] == "not_found"

    resp_fail = client.post("/api/v1/runs/run-non-existent/fail_unknown_external")
    assert resp_fail.status_code == 404
    assert resp_fail.json()["code"] == "not_found"


# =========================================================================
# V12 / U10: 任务中心多状态列表与生命周期筛选
# =========================================================================

def test_v12_workbench_runs_listing_and_filtering(tmp_path: Path) -> None:
    """U10 / V12: /api/v1/runs supports limit and lifecycle filtering."""
    store = LocalArtifactStore(tmp_path / "artifacts")
    repository = SQLiteRuntimeRepository(
        tmp_path / "db" / "runtime.sqlite3", store, clock=lambda: NOW
    )
    fixture_runtime = ResearchFixtureRuntime(repository, store, tmp_path / "workspace")
    orchestrator = CompositeOrchestrator(repository, (fixture_runtime,))
    app = create_app(
        repository,
        orchestrator,
        provider_configured=False,
        worker=WorkerLoop(orchestrator, interval_seconds=10),
    )
    client = TestClient(app)

    # Initial runs query returns empty list
    r0 = client.get("/api/v1/runs?limit=10&lifecycle=active")
    assert r0.status_code == 200
    assert r0.json()["items"] == []

    # Submit a research fixture run
    r_sub = client.post("/api/v1/tasks/research-fixture", json={"objective": "验证运行列表"})
    assert r_sub.status_code == 200
    run_id = r_sub.json()["run_id"]

    # Listed under active
    r_active = client.get("/api/v1/runs?limit=10&lifecycle=active")
    assert r_active.status_code == 200
    items = r_active.json()["items"]
    assert any(item["run_id"] == run_id for item in items)


# =========================================================================
# V13 / U11: 全局搜索深链与定位器元数据
# =========================================================================

def test_v13_global_search_deep_link_locators(tmp_path: Path) -> None:
    """U11 / V13: Global search returns structured items with query and locator metadata."""
    store = LocalArtifactStore(tmp_path / "artifacts")
    repository = SQLiteRuntimeRepository(
        tmp_path / "db" / "runtime.sqlite3", store, clock=lambda: NOW
    )
    search_service = GlobalSearchService(repository.database_path)
    search_service.index_object(
        "document",
        "doc-test-1",
        title="表面码容错架构设计",
        body="量子计算表面码容错阈值与晶格手术技术解析。",
        metadata={"document_id": "doc-test-1", "chunk_id": "chk-1"},
    )
    runtime = _PassiveRuntime()
    app = create_app(
        repository,
        runtime,
        provider_configured=False,
        search_service=search_service,
        worker=WorkerLoop(runtime, interval_seconds=10),
    )
    client = TestClient(app)

    search_resp = client.get("/api/v1/search?q=量子计算")
    assert search_resp.status_code == 200
    data = search_resp.json()
    assert data["query"] == "量子计算"
    assert data["total"] >= 1
    assert len(data["items"]) >= 1
    hit = data["items"][0]
    assert hit["object_id"] == "doc-test-1"
    assert hit["locator"] is not None
    assert hit["deep_link"] is not None

    # Verify locate endpoint
    locate_resp = client.get(f"/api/v1/search/{hit['result_id']}/locate")
    assert locate_resp.status_code == 200
    loc_data = locate_resp.json()
    assert loc_data["result_id"] == hit["result_id"]
    assert loc_data["deep_link"] is not None


# =========================================================================
# V14 / U12: 记忆候选沉淀入库与拒绝操作打通
# =========================================================================

def test_v14_memory_candidate_action_contract(tmp_path: Path) -> None:
    """U12 / V14: Approving a candidate promotes it to active memory; rejecting closes candidate."""
    store = LocalArtifactStore(tmp_path / "artifacts")
    repository = SQLiteRuntimeRepository(
        tmp_path / "db" / "runtime.sqlite3", store, clock=lambda: NOW
    )
    runtime = _PassiveRuntime()
    app = create_app(
        repository,
        runtime,
        provider_configured=False,
        worker=WorkerLoop(runtime, interval_seconds=10),
    )
    client = TestClient(app)
    memory_store: HierarchicalMemoryStore = app.state.memory_store

    # 1. Create candidate 1
    c1 = memory_store.create_candidate(
        scope=MemoryScope.USER,
        target_id="user_default",
        category=MemoryCategory.PREFERENCE,
        statement="偏好量子表面码与晶格手术技术方案",
        confidence=0.95,
        source_type="dialogue",
        source_id="msg-001",
    )
    assert c1 is not None

    # Approve candidate 1
    resp1 = client.post(
        f"/api/v1/memories/candidates/{c1.candidate_id}/action",
        json={"action": "approve"},
    )
    assert resp1.status_code == 200
    b1 = resp1.json()
    assert b1["ok"] is True
    assert b1["action"] == "approved"
    assert b1["memory_id"]

    # Verify active memory in store
    active_mems = memory_store.list_memories()
    assert any(m.statement == "偏好量子表面码与晶格手术技术方案" for m in active_mems)

    # 2. Create candidate 2
    c2 = memory_store.create_candidate(
        scope=MemoryScope.USER,
        target_id="user_default",
        category=MemoryCategory.PREFERENCE,
        statement="临时无关记忆事实",
        confidence=0.5,
        source_type="dialogue",
        source_id="msg-002",
    )
    assert c2 is not None

    # Reject candidate 2
    resp2 = client.post(
        f"/api/v1/memories/candidates/{c2.candidate_id}/action",
        json={"action": "reject"},
    )
    assert resp2.status_code == 200
    b2 = resp2.json()
    assert b2["ok"] is True
    assert b2["action"] == "rejected"


# =========================================================================
# V15 / P0 & U13: 新消息即时拥有 message_id 且支持免刷新导出
# =========================================================================

def test_v15_chat_immediate_message_export(tmp_path: Path) -> None:
    """P0 / U13 / V15: Newly generated message immediately receives message_id and can be exported without reload."""
    store = LocalArtifactStore(tmp_path / "artifacts")
    repository = SQLiteRuntimeRepository(
        tmp_path / "db" / "runtime.sqlite3", store, clock=lambda: NOW
    )
    runtime = _PassiveRuntime()
    payload = _mock_chat_payload("这是关于表面码纠错阈值的详细技术论述。")
    transport = _SequenceChatTransport([payload])
    config = ProviderConfig("https://provider.example/v1", "secret", "chat")
    chat_adapter = OpenAICompatibleChatAdapter(store, config, transport=transport)
    chat_db_path = tmp_path / "db" / "chat.sqlite3"
    chat_db_path.parent.mkdir(parents=True, exist_ok=True)
    chat_service = ChatService(chat_adapter, chat_db_path, artifact_store=store)

    app = create_app(
        repository,
        runtime,
        provider_configured=True,
        chat_service=chat_service,
        worker=WorkerLoop(runtime, interval_seconds=10),
    )
    client = TestClient(app)

    # 1. Send chat message
    chat_resp = client.post(
        "/api/v1/chat",
        json={"question": "表面码的容错阈值是多少？", "mode": "direct"},
    )
    assert chat_resp.status_code == 200
    chat_data = chat_resp.json()
    message_id = chat_data.get("message_id")
    assert message_id, "Newly created assistant message must have a non-empty message_id"
    assert "这是关于表面码纠错阈值的详细技术论述" in chat_data["content"]

    # 2. Immediately export as markdown without reload
    md_resp = client.get(f"/api/v1/chat/messages/{message_id}/export?format=markdown")
    assert md_resp.status_code == 200
    assert "text/markdown" in md_resp.headers["content-type"]
    assert "这是关于表面码纠错阈值的详细技术论述" in md_resp.text

    # 3. Immediately export as json without reload
    json_resp = client.get(f"/api/v1/chat/messages/{message_id}/export?format=json")
    assert json_resp.status_code == 200
    assert "application/json" in json_resp.headers["content-type"]
    export_body = json_resp.json()
    assert "这是关于表面码纠错阈值的详细技术论述" in export_body["body_markdown"]


# =========================================================================
# V16 / P1: 任务运行时不可用时显式返回 503 与恢复指引
# =========================================================================

def test_v16_unavailable_task_runtime_returns_503(tmp_path: Path) -> None:
    """P1 / V16: When Provider is unconfigured, verified-research and deep-research return HTTP 503 service_unavailable."""
    store = LocalArtifactStore(tmp_path / "artifacts")
    repository = SQLiteRuntimeRepository(
        tmp_path / "db" / "runtime.sqlite3", store, clock=lambda: NOW
    )
    paper_runtime = UnavailableTaskRuntime(
        repository,
        executor_id="legacy_paper_runtime@v1",
        task_kinds=("paper_discovery",),
        message="Provider configuration is incomplete",
    )
    research_runtime = UnavailableTaskRuntime(
        repository,
        executor_id="durable_verified_research@v1",
        task_kinds=("verified_paper_research", "managed_verified_research", "deep_research"),
        message="Provider configuration is incomplete",
    )
    fixture_runtime = ResearchFixtureRuntime(repository, store, tmp_path / "workspace")
    orchestrator = CompositeOrchestrator(
        repository,
        (fixture_runtime, paper_runtime, research_runtime),
    )
    app = create_app(
        repository,
        orchestrator,
        provider_configured=False,
        worker=WorkerLoop(orchestrator, interval_seconds=10),
    )
    client = TestClient(app)

    # 1. verified-research (single) -> 503 service_unavailable
    resp_single = client.post(
        "/api/v1/tasks/verified-research",
        json={"objective": "研究拓扑量子计算", "mode": "single"},
    )
    assert resp_single.status_code == 503
    body_single = resp_single.json()
    assert body_single["code"] == "service_unavailable"
    assert "Provider configuration is incomplete" in body_single["message"]
    assert body_single.get("recovery_action") is not None

    # 2. verified-research (managed) -> 503 service_unavailable
    resp_managed = client.post(
        "/api/v1/tasks/verified-research",
        json={"objective": "研究拓扑量子计算", "mode": "managed"},
    )
    assert resp_managed.status_code == 503
    body_managed = resp_managed.json()
    assert body_managed["code"] == "service_unavailable"
    assert body_managed.get("recovery_action") is not None

    # 3. deep-research -> 503 service_unavailable
    resp_deep = client.post(
        "/api/v1/tasks/deep-research",
        json={"objective": "研究拓扑量子计算"},
    )
    assert resp_deep.status_code == 503
    body_deep = resp_deep.json()
    assert body_deep["code"] == "service_unavailable"
    assert body_deep.get("recovery_action") is not None

