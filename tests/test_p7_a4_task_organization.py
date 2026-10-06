"""Tests for P7-A4 Research Task Organization: Tags, Favorites, Recent Visits, Project Grouping, Note Linking, Templates, and Continuation."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

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
from conflux_weave.runtime import LocalArtifactStore, SQLiteRuntimeRepository
from conflux_weave.server import create_app
from conflux_weave.task_organization import (
    ResearchTaskTemplate,
    ResearchTaskTemplateStore,
    RunOrganizationRecord,
    RunOrganizationStore,
    derive_continuation_proposal,
)


NOW = "2026-10-06T12:00:00Z"


def _build_test_harness(tmp_path: Path):
    store = LocalArtifactStore(tmp_path / "artifacts")
    repo = SQLiteRuntimeRepository(tmp_path / "db" / "runtime.sqlite3", store, clock=lambda: NOW)

    class DummyOrchestrator:
        def __init__(self):
            self.submissions = []

        def submit(self, submission):
            self.submissions.append(submission)
            rid = f"run-{uuid4().hex[:8]}"
            tid = f"task-{uuid4().hex[:8]}"
            task = TaskSpec(
                task_id=tid,
                kind=submission.task_kind,
                input=dict(submission.input),
                requested_policy="auto",
                idempotency_key=submission.idempotency_key or f"idem-{uuid4().hex[:8]}",
            )
            run = RunRecord(
                run_id=rid,
                task_id=tid,
                status=RunStatus.ACCEPTED,
                workflow_version="v0.3",
                config_snapshot_ref="snapshot",
                budget=BudgetLedger(30, 0, 0, "0", 1, 0, 1),
                created_at=NOW,
                updated_at=NOW,
            )
            steps = [
                StepRecord(
                    step_id=f"step-{uuid4().hex[:6]}",
                    run_id=rid,
                    kind="plan",
                    attempt=1,
                    status=StepStatus.PENDING,
                )
            ]
            res = repo.submit_task(task, run, steps)
            return res

    orchestrator = DummyOrchestrator()
    app = create_app(
        repository=repo,
        orchestrator=orchestrator,
        enable_worker=False,
    )
    client = TestClient(app)
    return repo, store, orchestrator, app, client


# ---------------------------------------------------------------------------
# 1. RunOrganizationStore Unit Tests
# ---------------------------------------------------------------------------


def test_run_organization_store_crud_and_persistence(tmp_path: Path):
    reg_file = tmp_path / "run_orgs.json"
    store = RunOrganizationStore(reg_file)

    # 1. Default for unknown run
    rec = store.get("run-1")
    assert rec.run_id == "run-1"
    assert rec.is_favorite is False
    assert rec.tags == []
    assert rec.project_id is None
    assert rec.note_ids == []

    # 2. Favorite toggle & set
    store.set_favorite("run-1", True)
    assert store.get("run-1").is_favorite is True
    store.toggle_favorite("run-1")
    assert store.get("run-1").is_favorite is False

    # 3. Tags (add, remove, deduplicate, whitespace strip)
    store.set_tags("run-1", [" ai ", "deep-learning", "ai", ""])
    assert store.get("run-1").tags == ["ai", "deep-learning"]
    store.add_tag("run-1", "benchmark")
    assert store.get("run-1").tags == ["ai", "deep-learning", "benchmark"]
    store.remove_tag("run-1", "deep-learning")
    assert store.get("run-1").tags == ["ai", "benchmark"]

    # 4. Touch
    store.touch("run-1", timestamp="2026-10-06T15:00:00Z")
    assert store.get("run-1").last_accessed_at == "2026-10-06T15:00:00Z"

    # 5. Project & Note links
    store.set_project("run-1", "proj-alpha")
    store.link_note("run-1", "note-101")
    store.link_note("run-1", "note-102")
    assert store.get("run-1").project_id == "proj-alpha"
    assert store.get("run-1").note_ids == ["note-101", "note-102"]
    store.unlink_note("run-1", "note-101")
    assert store.get("run-1").note_ids == ["note-102"]

    # 6. Reopen from disk to verify persistence
    reopened = RunOrganizationStore(reg_file)
    rec_reopened = reopened.get("run-1")
    assert rec_reopened.project_id == "proj-alpha"
    assert rec_reopened.tags == ["ai", "benchmark"]
    assert rec_reopened.note_ids == ["note-102"]
    assert rec_reopened.last_accessed_at == "2026-10-06T15:00:00Z"

    # 7. Queries & aggregations
    store.set_project("run-2", "proj-alpha")
    store.set_favorite("run-2", True)
    store.add_tag("run-2", "ai")
    store.link_note("run-2", "note-102")
    store.touch("run-2", timestamp="2026-10-06T16:00:00Z")

    assert set(store.list_runs_for_project("proj-alpha")) == {"run-1", "run-2"}
    assert set(store.list_runs_for_note("note-102")) == {"run-1", "run-2"}
    assert store.list_favorites() == ["run-2"]
    assert set(store.list_by_tag("ai")) == {"run-1", "run-2"}

    recents = store.list_recent(limit=10)
    assert len(recents) == 2
    assert recents[0].run_id == "run-2"  # 16:00 is more recent than 15:00
    assert recents[1].run_id == "run-1"


# ---------------------------------------------------------------------------
# 2. ResearchTaskTemplateStore Unit Tests
# ---------------------------------------------------------------------------


def test_research_task_template_store(tmp_path: Path):
    reg_file = tmp_path / "templates.json"
    store = ResearchTaskTemplateStore(reg_file)

    # 1. Builtin templates
    builtins = store.list_templates()
    assert len(builtins) >= 4
    tpl_arch = store.get_template("tpl-architecture-survey")
    assert tpl_arch is not None
    assert tpl_arch.is_builtin is True
    assert tpl_arch.category == "architecture"

    # 2. Query placeholder rendering
    rendered = tpl_arch.render_query({"technology": "LanceDB Hybrid Vector Index"})
    assert "LanceDB Hybrid Vector Index" in rendered
    assert "{technology}" not in rendered

    # 3. Category filtering
    lit_templates = store.list_templates(category="literature")
    assert any(t.template_id == "tpl-literature-matrix" for t in lit_templates)
    assert all(t.category == "literature" for t in lit_templates)

    # 4. Custom template CRUD
    custom = store.create_custom_template(
        name="模型可解释性实验调研",
        description="针对注意力权重与特征归因的可解释性分析",
        task_family="verified_paper_research",
        category="interpretability",
        default_query="调研评估 {model_name} 的特征归因机制与忠实度",
        tags=["interpretability", "attribution"],
        suggested_budgets={"max_tool_calls": 15},
        variables_schema=[{"name": "model_name", "label": "模型名称", "required": True}],
    )
    assert custom.is_builtin is False
    assert custom.template_id.startswith("tpl-")
    assert store.get_template(custom.template_id) is not None

    # Verify persistence
    reopened = ResearchTaskTemplateStore(reg_file)
    assert reopened.get_template(custom.template_id) is not None

    # Deleting custom template
    assert store.delete_custom_template(custom.template_id) is True
    assert store.get_template(custom.template_id) is None

    # Cannot delete builtin template
    with pytest.raises(ValueError, match="内置模板不允许删除"):
        store.delete_custom_template("tpl-architecture-survey")


# ---------------------------------------------------------------------------
# 3. Continuation Proposal Engine Unit Tests
# ---------------------------------------------------------------------------


def test_derive_continuation_proposal(tmp_path: Path):
    store = LocalArtifactStore(tmp_path / "artifacts")
    repo = SQLiteRuntimeRepository(tmp_path / "db" / "runtime.sqlite3", store, clock=lambda: NOW)
    org_store = RunOrganizationStore(tmp_path / "org.json")

    # Seed predecessor run
    task = TaskSpec(
        task_id="task-pred",
        kind="verified_paper_research",
        input={"query": "大模型代码验证机制调研"},
        requested_policy="auto",
        idempotency_key="idem-pred",
    )
    run = RunRecord(
        run_id="run-pred",
        task_id="task-pred",
        status=RunStatus.ACCEPTED,
        workflow_version="v0.3",
        config_snapshot_ref="snap",
        budget=BudgetLedger(30, 0, 0, "0", 1, 0, 1),
        created_at=NOW,
        updated_at=NOW,
    )
    steps = [
        StepRecord(step_id="step-p1", run_id="run-pred", kind="plan", attempt=1, status=StepStatus.PENDING),
        StepRecord(step_id="step-p2", run_id="run-pred", kind="publish_delivery", attempt=1, status=StepStatus.PENDING),
    ]
    repo.submit_task(task, run, steps)
    repo.transition_run("run-pred", RunStatus.QUEUED, updated_at=NOW)
    repo.transition_run("run-pred", RunStatus.RUNNING, updated_at=NOW)

    # Seed delivery with limitations and unmet criteria
    art = store.put_bytes(b"# Research Report\n", media_type="text/markdown", producer_step_id="step-p2", schema_version="v1")
    delivery = DeliveryRecord(
        run_id="run-pred",
        disposition=DeliveryDisposition.PARTIAL,
        artifact_refs=(art.artifact_id,),
        evidence_refs=("ev-1",),
        limitations=("未覆盖多语言代码沙箱的跨平台兼容性", "缺乏生产级高并发压测数据"),
        unmet_criteria=("跨语言测试覆盖率 >= 90%",),
    )
    repo.publish_delivery("run-pred", RunStatus.PARTIAL, delivery, (art,))

    # Seed predecessor organization attributes
    org_store.set_project("run-pred", "proj-core")
    org_store.set_tags("run-pred", ["code-gen", "verification"])
    org_store.link_note("run-pred", "note-arch-1")

    # 1. Automatic derivation from top limitation
    proposal = derive_continuation_proposal("run-pred", repo, org_store)
    assert proposal.predecessor_run_id == "run-pred"
    assert proposal.parent_query == "大模型代码验证机制调研"
    assert proposal.inherited_project_id == "proj-core"
    assert "code-gen" in proposal.inherited_tags
    assert "continued" in proposal.inherited_tags
    assert proposal.inherited_note_ids == ["note-arch-1"]
    assert "未覆盖多语言代码沙箱的跨平台兼容性" in proposal.suggested_query
    assert "部分回答" in proposal.context_summary

    # 2. Custom focus specified
    custom_proposal = derive_continuation_proposal(
        "run-pred", repo, org_store, custom_focus="专项调研 Windows 环境下的沙箱回滚性能"
    )
    assert custom_proposal.suggested_query == "专项调研 Windows 环境下的沙箱回滚性能"
    assert custom_proposal.inherited_project_id == "proj-core"


# ---------------------------------------------------------------------------
# 4. End-to-End FastAPI Integration Tests
# ---------------------------------------------------------------------------


def test_research_task_organization_api_endpoints(tmp_path: Path):
    repo, store, orch, app, client = _build_test_harness(tmp_path)

    # 1. Submit a base research task
    resp = client.post(
        "/api/v1/tasks/research",
        json={"query": "多智能体架构调研", "topics": ["multi-agent"]},
    )
    assert resp.status_code == 200
    run_id = resp.json()["run_id"]

    # 2. Favorite endpoint
    fav_resp = client.post(f"/api/v1/research/runs/{run_id}/favorite", json={"is_favorite": True})
    assert fav_resp.status_code == 200
    assert fav_resp.json()["is_favorite"] is True

    # Toggle favorite
    toggle_resp = client.post(f"/api/v1/research/runs/{run_id}/favorite", json={})
    assert toggle_resp.status_code == 200
    assert toggle_resp.json()["is_favorite"] is False

    # Set favorite back to True
    client.post(f"/api/v1/research/runs/{run_id}/favorite", json={"is_favorite": True})

    # 3. Tags endpoint
    tag_resp = client.post(
        f"/api/v1/research/runs/{run_id}/tags",
        json={"action": "set", "tags": ["agent", "architecture"]},
    )
    assert tag_resp.status_code == 200
    assert tag_resp.json()["tags"] == ["agent", "architecture"]

    add_tag_resp = client.post(
        f"/api/v1/research/runs/{run_id}/tags",
        json={"action": "add", "tag": "benchmark"},
    )
    assert add_tag_resp.status_code == 200
    assert "benchmark" in add_tag_resp.json()["tags"]

    remove_tag_resp = client.post(
        f"/api/v1/research/runs/{run_id}/tags",
        json={"action": "remove", "tag": "architecture"},
    )
    assert remove_tag_resp.status_code == 200
    assert remove_tag_resp.json()["tags"] == ["agent", "benchmark"]

    # 4. Touch endpoint
    touch_resp = client.post(f"/api/v1/research/runs/{run_id}/touch")
    assert touch_resp.status_code == 200
    assert touch_resp.json()["last_accessed_at"] is not None

    # 5. Project assignment
    proj_resp = client.post(
        f"/api/v1/research/runs/{run_id}/project",
        json={"project_id": "proj-conflux-weave"},
    )
    assert proj_resp.status_code == 200
    assert proj_resp.json()["project_id"] == "proj-conflux-weave"

    # 6. Note linking
    link_resp = client.post(
        f"/api/v1/research/runs/{run_id}/link-note",
        json={"note_id": "note-agent-patterns", "action": "link"},
    )
    assert link_resp.status_code == 200
    assert "note-agent-patterns" in link_resp.json()["note_ids"]

    # 7. Organization detail endpoint
    org_detail = client.get(f"/api/v1/research/runs/{run_id}/organization")
    assert org_detail.status_code == 200
    assert org_detail.json()["is_favorite"] is True
    assert org_detail.json()["project_id"] == "proj-conflux-weave"
    assert org_detail.json()["tags"] == ["agent", "benchmark"]
    assert org_detail.json()["note_ids"] == ["note-agent-patterns"]

    # 8. Filtered Runs query via GET /api/v1/runs
    runs_all = client.get("/api/v1/runs")
    assert runs_all.status_code == 200
    run_item = next(item for item in runs_all.json()["items"] if item["run_id"] == run_id)
    assert run_item["is_favorite"] is True
    assert run_item["project_id"] == "proj-conflux-weave"
    assert run_item["tags"] == ["agent", "benchmark"]

    runs_by_proj = client.get("/api/v1/runs?project_id=proj-conflux-weave")
    assert runs_by_proj.status_code == 200
    assert any(i["run_id"] == run_id for i in runs_by_proj.json()["items"])

    runs_by_fav = client.get("/api/v1/runs?is_favorite=true")
    assert runs_by_fav.status_code == 200
    assert any(i["run_id"] == run_id for i in runs_by_fav.json()["items"])

    runs_by_tag = client.get("/api/v1/runs?tag=agent")
    assert runs_by_tag.status_code == 200
    assert any(i["run_id"] == run_id for i in runs_by_tag.json()["items"])

    # 9. Grouped runs by project
    grouped_resp = client.get("/api/v1/research/runs/by-project")
    assert grouped_resp.status_code == 200
    groups = grouped_resp.json()["groups"]
    assert any(g["project_id"] == "proj-conflux-weave" for g in groups)

    # 10. Recent & Favorite endpoints
    recents = client.get("/api/v1/research/runs/recent?limit=10")
    assert recents.status_code == 200
    assert any(i["run_id"] == run_id for i in recents.json()["items"])

    favs = client.get("/api/v1/research/runs/favorites")
    assert favs.status_code == 200
    assert any(i["run_id"] == run_id for i in favs.json()["items"])

    # 11. Templates endpoints
    tpl_list = client.get("/api/v1/research/templates")
    assert tpl_list.status_code == 200
    assert tpl_list.json()["count"] >= 4

    tpl_inst = client.post(
        "/api/v1/research/templates/tpl-architecture-survey/instantiate",
        json={
            "variables": {"technology": "LanceDB 向量索引"},
            "project_id": "proj-conflux-weave",
            "tags": ["lancedb", "survey"],
            "is_favorite": True,
        },
    )
    assert tpl_inst.status_code == 200
    inst_run_id = tpl_inst.json()["run_id"]

    inst_org = client.get(f"/api/v1/research/runs/{inst_run_id}/organization")
    assert inst_org.status_code == 200
    assert inst_org.json()["project_id"] == "proj-conflux-weave"
    assert inst_org.json()["is_favorite"] is True
    assert "lancedb" in inst_org.json()["tags"]

    # 12. Continuation Proposal & Execution
    prop_resp = client.get(f"/api/v1/research/runs/{run_id}/continuation-proposal")
    assert prop_resp.status_code == 200
    prop_data = prop_resp.json()
    assert prop_data["predecessor_run_id"] == run_id
    assert prop_data["inherited_project_id"] == "proj-conflux-weave"
    assert "continued" in prop_data["inherited_tags"]

    cont_resp = client.post(
        f"/api/v1/research/runs/{run_id}/continue",
        json={"question": "深挖多智能体状态机恢复与 lease 竞态保护", "additional_tags": ["recovery"]},
    )
    assert cont_resp.status_code == 200
    cont_data = cont_resp.json()
    assert cont_data["predecessor_run_id"] == run_id
    cont_run_id = cont_data["run_id"]

    cont_org = client.get(f"/api/v1/research/runs/{cont_run_id}/organization")
    assert cont_org.status_code == 200
    assert cont_org.json()["project_id"] == "proj-conflux-weave"
    assert "continued" in cont_org.json()["tags"]
    assert "recovery" in cont_org.json()["tags"]
    assert "note-agent-patterns" in cont_org.json()["note_ids"]

    # 13. Unlink note via API
    unlink_resp = client.post(
        f"/api/v1/research/runs/{cont_run_id}/link-note",
        json={"note_id": "note-agent-patterns", "action": "unlink"},
    )
    assert unlink_resp.status_code == 200
    assert "note-agent-patterns" not in unlink_resp.json()["note_ids"]

    # 14. Custom template CRUD via API
    custom_tpl_resp = client.post(
        "/api/v1/research/templates",
        json={
            "name": "多模态视觉评测模板",
            "description": "多模态图表解析消融实验",
            "task_family": "verified_paper_research",
            "category": "benchmark",
            "default_query": "评测 {dataset} 上的视觉定位精确率",
            "tags": ["vision", "benchmark"],
        },
    )
    assert custom_tpl_resp.status_code == 200
    custom_tpl_id = custom_tpl_resp.json()["template_id"]

    get_custom_resp = client.get(f"/api/v1/research/templates/{custom_tpl_id}")
    assert get_custom_resp.status_code == 200
    assert get_custom_resp.json()["name"] == "多模态视觉评测模板"

    del_custom_resp = client.delete(f"/api/v1/research/templates/{custom_tpl_id}")
    assert del_custom_resp.status_code == 200
    assert del_custom_resp.json()["deleted"] is True

    # 15. Continuation without explicit question (automatic fallback)
    cont_default_resp = client.post(f"/api/v1/research/runs/{run_id}/continue", json={})
    assert cont_default_resp.status_code == 200
    assert cont_default_resp.json()["predecessor_run_id"] == run_id
