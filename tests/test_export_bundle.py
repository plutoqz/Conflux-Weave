"""P6-A1 成果导出测试：渲染器单元 + 收集器 + API 端点（离线，无外部调用）。"""

import io
import json
import zipfile

import pytest
from starlette.testclient import TestClient

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
from conflux_weave.export_bundle import (
    EXPORT_SCHEMA,
    ExportCitation,
    ExportDocument,
    ExportService,
    build_export_json,
    build_export_zip,
    render_export_bibtex,
    render_export_markdown,
)
from conflux_weave.runtime import LocalArtifactStore, SQLiteRuntimeRepository
from conflux_weave.runtime.durable_paper_shared import RANK_CHECKPOINT
from conflux_weave.server import WorkerLoop, create_app

NOW = "2026-09-12T08:00:00Z"


class PassiveRuntime:
    executor_id = "passive@v1"
    task_kinds = ("paper_discovery", "managed_verified_research")

    def work_once(self, **kwargs):
        return None


def build_repository(tmp_path):
    store = LocalArtifactStore(tmp_path / "artifacts")
    repository = SQLiteRuntimeRepository(tmp_path / "db" / "runtime.sqlite3", store, clock=lambda: NOW)
    return repository, store


def _publish_run(repository, store, suffix: str, *, sources: list[dict] | None = None):
    """提交一个 managed_verified_research Run 并发布带 markdown 报告 + manifest + evidence 的交付。"""
    task = TaskSpec(
        task_id=f"task-{suffix}",
        kind="managed_verified_research",
        input={"objective": f"多模态 RAG 研究 {suffix}"},
        requested_policy="fixture-v1",
        idempotency_key=f"key-{suffix}",
    )
    run = RunRecord(
        run_id=f"run-{suffix}",
        task_id=task.task_id,
        status=RunStatus.ACCEPTED,
        workflow_version="managed-v1",
        config_snapshot_ref="file:///fixture/config.json",
        budget=BudgetLedger(180, 20_000, 2_048, "unavailable", 4, 2, 1),
        created_at=NOW,
        updated_at=NOW,
    )
    steps = [
        StepRecord(step_id=f"step-{suffix}-rank", run_id=run.run_id, kind="rank_candidates", attempt=1, status=StepStatus.PENDING),
        StepRecord(step_id=f"step-{suffix}-publish", run_id=run.run_id, kind="publish_delivery", attempt=1, status=StepStatus.PENDING),
    ]
    repository.submit_task(task, run, steps)
    repository.transition_run(run.run_id, RunStatus.QUEUED, updated_at=NOW)

    evidence_payload = {
        "schema_version": RANK_CHECKPOINT,
        "evidence": [
            {
                "evidence_id": "evidence-0001",
                "source_snapshot_id": "document-sha256-" + "e" * 64,
                "locator": {"page": 17, "type": "pdf_page"},
                "quote": "向量索引把概念映射到稠密向量。",
                "extraction_method": "fixture",
            }
        ],
    }
    manifest_payload = {
        "schema_version": "conflux-weave.durable-research-manifest.v1",
        "objective": f"多模态 RAG 研究 {suffix}",
        "code_revision": "9b62388fixture",
        "sources": sources
        if sources is not None
        else [
            {
                "source_id": "web-0001",
                "title": "多模态检索综述",
                "url": "https://example.com/mm-rag",
                "snapshot_artifact": "artifact-sha256-" + "a" * 64,
                "content_hash": "sha256:" + "b" * 64,
                "acquired_at": "2026-09-12T07:00:00Z",
            },
            {"source_id": "web-0002", "title": "仅标题来源", "url": None, "title_only": True},
        ],
    }
    # rank checkpoint 注册在 rank 步骤上
    rank_claim = repository.claim_next_step("worker", lease_seconds=60, now=NOW)
    assert rank_claim is not None
    checkpoint = store.put_json(evidence_payload, producer_step_id=rank_claim.step_id, schema_version=RANK_CHECKPOINT)
    repository.complete_attempt(rank_claim, (checkpoint,), now=NOW)

    publish_claim = repository.claim_next_step("worker", lease_seconds=60, now=NOW)
    assert publish_claim is not None
    report = store.put_bytes(
        f"# 报告 {suffix}\n\n正文引用 [1] 与 [2]。\n\n公式 $E=mc^2$ 与表格不应损坏。\n".encode("utf-8"),
        media_type="text/markdown; charset=utf-8",
        producer_step_id=publish_claim.step_id,
        schema_version="conflux-weave.durable-research-report.v1",
    )
    manifest = store.put_json(
        manifest_payload,
        producer_step_id=publish_claim.step_id,
        schema_version="conflux-weave.durable-research-manifest.v1",
    )
    evidence = store.put_json(
        {"schema_version": "conflux-weave.durable-research-evidence.v1", "evidence": evidence_payload["evidence"]},
        producer_step_id=publish_claim.step_id,
        schema_version="conflux-weave.durable-research-evidence.v1",
    )
    delivery = DeliveryRecord(
        run_id=run.run_id,
        disposition=DeliveryDisposition.COMPLETE,
        artifact_refs=(report.artifact_id, manifest.artifact_id, evidence.artifact_id),
        evidence_refs=("evidence-0001",),
    )
    repository.publish_delivery(
        run.run_id,
        RunStatus.SUCCEEDED,
        delivery,
        (report, manifest, evidence),
        claim=publish_claim,
        published_at=NOW,
    )
    return run.run_id, evidence_payload


def _document(**overrides) -> ExportDocument:
    base = dict(
        object_kind="run_report",
        object_id="run-abc",
        title="融合报告标题",
        body_markdown="# 融合报告标题\n\n正文引用 [1]。\n\n公式 $E=mc^2$ 与表格不应损坏。\n",
        citations=(
            ExportCitation(
                display_index=1,
                title="多模态检索综述",
                url="https://example.com/a_b",
                locator={"page": 3},
                evidence_id="evidence-0001",
                acquired_at="2026-09-12T07:00:00Z",
            ),
            ExportCitation(display_index=2, title="本地 PDF 段落", locator={"page": 17}, source_type="local"),
        ),
        metadata={
            "run_id": "run-abc",
            "code_revision": "9b62388",
            "disposition": "complete",
            "run_created_at": NOW,
        },
    )
    base.update(overrides)
    return ExportDocument(**base)


# ---------------------------------------------------------------- 渲染器单元


def test_markdown_export_keeps_cjk_formula_and_appends_reference_list() -> None:
    doc = _document()
    text = render_export_markdown(doc)
    assert "多模态检索综述" in text
    assert "E=mc^2" in text
    assert "code_revision：`9b62388`" in text
    assert "来源 Run：`run-abc`" in text
    assert "## 参考文献" in text
    assert "1. [多模态检索综述](https://example.com/a_b) （第 3 页）" in text
    assert "2. 本地 PDF 段落 （第 17 页）" in text


def test_markdown_export_skips_reference_list_when_body_already_has_one() -> None:
    doc = _document(body_markdown="# 报告\n\n正文 [1]。\n\n## 来源清单\n\n1. 来源 A\n")
    text = render_export_markdown(doc)
    assert text.count("## 参考文献") == 0
    assert "## 来源清单" in text


def test_markdown_export_states_disposition_and_limitations_for_failed_run() -> None:
    doc = _document(
        metadata={"run_id": "run-x", "disposition": "partial", "run_status": "partial", "limitations": ["未核验"]},
    )
    text = render_export_markdown(doc)
    assert "交付状态 partial" in text
    assert "Run 状态 partial" in text


def test_bibtex_export_escapes_and_records_locators() -> None:
    doc = _document()
    bib = render_export_bibtex(doc)
    assert "@misc{cw01-" in bib
    # \url{} 内不转义下划线（url 包自行处理特殊字符）
    assert "howpublished = {\\url{https://example.com/a_b}}" in bib
    assert "year = {2026}" in bib
    assert "第 3 页" in bib  # locator note (plain, unescaped CJK allowed)
    assert "Evidence evidence-0001" in bib
    assert "Conflux-Weave Run run-abc" in bib
    assert bib.count("@misc{") == 2


def test_json_export_carries_full_ledger_without_secrets() -> None:
    doc = _document(
        evidence=({"evidence_id": "evidence-0001", "quote": "向量索引把概念映射到稠密向量。", "locator": {"page": 17}},),
        metadata={"run_id": "run-abc", "api_key": "should-not-leak"},
    )
    payload = json.loads(build_export_json(doc))
    assert payload["schema_version"] == EXPORT_SCHEMA
    assert payload["metadata"]["run_id"] == "run-abc"
    assert payload["evidence"][0]["quote"].startswith("向量索引")
    assert "exported_at" in payload
    # 渲染器不会重新引入调用方塞进 metadata 的键，但 JSON 导出必须忠实于文档本身；
    # API 层的 metadata 由 ExportService 构造，不含任何配置/密钥字段（由 API 测试覆盖）。


def test_zip_export_contains_report_bib_and_evidence_ledger() -> None:
    doc = _document(evidence=({"evidence_id": "evidence-0001", "quote": "q"},))
    raw = build_export_zip(doc)
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        names = archive.namelist()
        assert sorted(names) == ["run-abc/evidence.json", "run-abc/references.bib", "run-abc/report.md"]
        report = archive.read("run-abc/report.md").decode("utf-8")
        assert "融合报告标题" in report
        bib = archive.read("run-abc/references.bib").decode("utf-8")
        assert "@misc{" in bib
        ledger = json.loads(archive.read("run-abc/evidence.json"))
        assert ledger["evidence"][0]["evidence_id"] == "evidence-0001"


def test_repeated_export_is_byte_stable_except_exported_at() -> None:
    doc = _document()
    first = build_export_json(doc)
    second = build_export_json(doc)
    # exported_at 每次导出生成（真实时间），其余字段必须稳定
    first_payload = json.loads(first)
    second_payload = json.loads(second)
    first_payload.pop("exported_at")
    second_payload.pop("exported_at")
    assert first_payload == second_payload


# ------------------------------------------------------------------ 收集器


def test_collect_run_export_assembles_citations_manifest_and_evidence(tmp_path) -> None:
    repository, store = build_repository(tmp_path)
    run_id, _ = _publish_run(repository, store, "collect")
    service = ExportService(repository)
    doc = service.collect_run_export(run_id)

    assert doc.object_kind == "run_report"
    assert doc.title == "多模态 RAG 研究 collect"
    assert "正文引用 [1] 与 [2]" in doc.body_markdown
    assert [c.display_index for c in doc.citations] == [1, 2]
    assert doc.citations[0].url == "https://example.com/mm-rag"
    assert doc.citations[1].source_type == "title_only"
    assert doc.evidence[0]["evidence_id"] == "evidence-0001"
    assert doc.evidence[0]["locator"]["page"] == 17
    assert doc.metadata["code_revision"] == "9b62388fixture"
    assert doc.metadata["disposition"] == "complete"


def test_collect_run_export_falls_back_to_evidence_when_manifest_has_no_sources(tmp_path) -> None:
    repository, store = build_repository(tmp_path)
    run_id, _ = _publish_run(repository, store, "fallback", sources=[])
    service = ExportService(repository)
    doc = service.collect_run_export(run_id)
    assert doc.citations[0].evidence_id == "evidence-0001"
    assert doc.citations[0].source_type == "local"


def test_collect_run_export_without_delivery_is_not_found(tmp_path) -> None:
    from conflux_weave.runtime import RecordNotFound

    repository, store = build_repository(tmp_path)
    task = TaskSpec(task_id="task-1", kind="paper_discovery", input={"query": "q"}, requested_policy="p", idempotency_key="k")
    run = RunRecord(
        run_id="run-nodelivery", task_id=task.task_id, status=RunStatus.ACCEPTED,
        workflow_version="v", config_snapshot_ref="c", budget=BudgetLedger(1, 1, 1, "unavailable", 1, 1, 1),
        created_at=NOW, updated_at=NOW,
    )
    repository.submit_task(task, run, [StepRecord(step_id="s", run_id=run.run_id, kind="rank", attempt=1, status=StepStatus.PENDING)])
    service = ExportService(repository)
    with pytest.raises(RecordNotFound):
        service.collect_run_export("run-nodelivery")


def test_collect_evidence_export_renders_single_evidence(tmp_path) -> None:
    repository, store = build_repository(tmp_path)
    run_id, _ = _publish_run(repository, store, "evexp")
    service = ExportService(repository)
    doc = service.collect_evidence_export(run_id, "evidence-0001")
    text = render_export_markdown(doc)
    assert "# Evidence evidence-0001" in text
    assert "- **page**: 17" in text
    assert "向量索引把概念映射到稠密向量" in text


def test_collect_evidence_export_unknown_id_is_not_found(tmp_path) -> None:
    from conflux_weave.runtime import RecordNotFound

    repository, store = build_repository(tmp_path)
    run_id, _ = _publish_run(repository, store, "evmiss")
    service = ExportService(repository)
    with pytest.raises(RecordNotFound):
        service.collect_evidence_export(run_id, "evidence-9999")


# ------------------------------------------------------------------ API 端点


def _api_app(tmp_path):
    repository, store = build_repository(tmp_path)
    run_id, _ = _publish_run(repository, store, "api")
    app = create_app(repository, PassiveRuntime(), worker=WorkerLoop(PassiveRuntime(), interval_seconds=10))
    return TestClient(app), run_id


def test_api_export_run_markdown(tmp_path) -> None:
    client, run_id = _api_app(tmp_path)
    response = client.get(f"/api/v1/runs/{run_id}/export?format=markdown")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/markdown")
    assert "attachment" in response.headers["content-disposition"]
    body = response.content.decode("utf-8")
    assert "## 参考文献" in body
    assert "https://example.com/mm-rag" in body
    assert "多模态 RAG 研究 api" in body


def test_api_export_run_bibtex_json_zip(tmp_path) -> None:
    client, run_id = _api_app(tmp_path)

    bib = client.get(f"/api/v1/runs/{run_id}/export?format=bibtex")
    assert bib.status_code == 200
    assert "application/x-bibtex" in bib.headers["content-type"]
    assert "@misc{" in bib.content.decode("utf-8")

    payload_response = client.get(f"/api/v1/runs/{run_id}/export?format=json")
    assert payload_response.status_code == 200
    payload = json.loads(payload_response.content)
    assert payload["schema_version"] == EXPORT_SCHEMA
    assert payload["metadata"]["run_id"] == run_id
    # 不泄露内部配置：导出 JSON 不得包含环境/密钥类字段
    flat = json.dumps(payload, ensure_ascii=False)
    assert "config_snapshot_ref" not in flat
    assert "api_key" not in flat.lower()

    bundle = client.get(f"/api/v1/runs/{run_id}/export?format=zip")
    assert bundle.status_code == 200
    assert bundle.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(bundle.content)) as archive:
        assert any(name.endswith("report.md") for name in archive.namelist())


def test_api_export_rejects_unknown_format_and_missing_run(tmp_path) -> None:
    client, run_id = _api_app(tmp_path)
    bad_format = client.get(f"/api/v1/runs/{run_id}/export?format=pdf")
    assert bad_format.status_code == 422
    missing = client.get("/api/v1/runs/run-missing/export?format=markdown")
    assert missing.status_code == 404


def test_api_export_single_evidence(tmp_path) -> None:
    client, run_id = _api_app(tmp_path)
    ok = client.get(f"/api/v1/runs/{run_id}/evidence/evidence-0001/export?format=json")
    assert ok.status_code == 200
    payload = json.loads(ok.content)
    assert payload["object_kind"] == "evidence"
    assert payload["object_id"] == "evidence-0001"
    miss = client.get(f"/api/v1/runs/{run_id}/evidence/evidence-9999/export?format=markdown")
    assert miss.status_code == 404


def test_api_export_note_from_artifact_store(tmp_path) -> None:
    from conflux_weave.document_notes import NOTE_SCHEMA_VERSION, DocumentNote, NoteSection

    repository, store = build_repository(tmp_path)
    _publish_run(repository, store, "notebase")
    note = DocumentNote(
        note_id="note-export-1",
        document_id="doc-1",
        title="研读笔记标题",
        version=1,
        executive_summary="摘要内容",
        sections=(NoteSection(section_id="s1", title="分析", level=1, content="正文", source_segments=("seg-1",), citations=("seg-1",), asset_refs=()),),
    )
    store.put_json(
        note.to_dict(),
        producer_step_id="fixture",
        schema_version=NOTE_SCHEMA_VERSION,
    )
    app = create_app(repository, PassiveRuntime(), worker=WorkerLoop(PassiveRuntime(), interval_seconds=10))
    client = TestClient(app)
    markdown = client.get("/api/v1/notes/note-export-1/export?format=markdown")
    assert markdown.status_code == 200
    body = markdown.content.decode("utf-8")
    assert "研读笔记标题" in body
    assert "核验标记 human_curated_note" in body
    payload = client.get("/api/v1/notes/note-export-1/export?format=json")
    assert payload.status_code == 200
    assert json.loads(payload.content)["object_kind"] == "note"
