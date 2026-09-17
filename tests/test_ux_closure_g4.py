"""G4 Automated Test Suite: Journey B Project Copilot Context & Q&A Persistence,
Coding Patch Safety & Decoupled Verification/Revert, and Real Skill Traces/Artifacts Execution.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
import pytest
from starlette.testclient import TestClient

from conflux_weave.api_contracts import (
    ProjectAskRequest,
    CodingProposalRequest,
    CodingApplyRequest,
    CodingVerifyRequest,
    CodingRevertRequest,
)
from conflux_weave.server import WorkerLoop, create_app
from conflux_weave.runtime.artifacts import LocalArtifactStore
from conflux_weave.runtime import SQLiteRuntimeRepository
from conflux_weave.chat import ChatService
from conflux_weave.provider import (
    OpenAICompatibleChatAdapter,
    ProviderConfig,
    ProviderHttpResponse,
)
from conflux_weave.harness.orchestration import CompositeOrchestrator
from conflux_weave.harness.fixture_runtime import ResearchFixtureRuntime
from conflux_weave.skills.runner import SkillRunner


NOW = "2026-09-17T12:00:00Z"


class _SequenceChatTransport:
    def __init__(self, payloads):
        self.payloads = iter(payloads)
        self.requests = []

    def post(self, *args, **kwargs):
        self.requests.append(json.loads(kwargs.get("body", "{}")))
        payload = next(self.payloads)
        return ProviderHttpResponse(200, json.dumps(payload).encode(), {"Content-Type": "application/json"})


class _SkillRetrieval:
    def __init__(self):
        self.document_by_id = {
            "chunk-quant-1": SimpleNamespace(
                document_id="chunk-quant-1",
                source_snapshot_id="doc-quant-2024",
                locator={"document_id": "doc-quant-2024", "page": 4},
                text="Quantum error correction threshold is reported under a defined noise model.",
            ),
            "chunk-topo-1": SimpleNamespace(
                document_id="chunk-topo-1",
                source_snapshot_id="doc-topo-2025",
                locator={"document_id": "doc-topo-2025", "page": 7},
                text="Topological decoding results include accuracy and computational cost measurements.",
            ),
            "chunk-excluded-1": SimpleNamespace(
                document_id="chunk-excluded-1",
                source_snapshot_id="doc-excluded-2026",
                locator={"document_id": "doc-excluded-2026", "page": 2},
                text="This excluded paper must never enter the selected comparison scope.",
            ),
        }

    def search(self, query: str, *, document_ids=None):
        allowed = set(document_ids or ())
        hits = []
        for rank, document in enumerate(self.document_by_id.values(), 1):
            if allowed and document.source_snapshot_id not in allowed:
                continue
            hits.append(
                SimpleNamespace(
                    document_id=document.document_id,
                    hit_id=document.document_id,
                    source_snapshot_id=document.source_snapshot_id,
                    locator=document.locator,
                    score=1.0 / rank,
                )
            )
        return SimpleNamespace(final=SimpleNamespace(hits=tuple(hits)))


def _mock_chat_payload(content: str, response_id: str = "resp-001"):
    return {
        "id": response_id,
        "model": "fixture-chat",
        "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
    }


def _build_g4_test_environment(tmp_path: Path):
    store = LocalArtifactStore(tmp_path / "artifacts")
    repository = SQLiteRuntimeRepository(
        tmp_path / "db" / "runtime.sqlite3", store, clock=lambda: NOW
    )
    fixture_runtime = ResearchFixtureRuntime(repository, store, tmp_path / "workspace")
    orchestrator = CompositeOrchestrator(repository, (fixture_runtime,))
    payload = _mock_chat_payload("代码分析完成，建议添加异常校验。")
    transport = _SequenceChatTransport([payload] * 10)
    config = ProviderConfig("https://provider.example/v1", "secret", "chat")
    chat_adapter = OpenAICompatibleChatAdapter(store, config, transport=transport)
    chat_db_path = tmp_path / "db" / "chat.sqlite3"
    chat_db_path.parent.mkdir(parents=True, exist_ok=True)
    chat_service = ChatService(chat_adapter, chat_db_path, artifact_store=store)

    app = create_app(
        repository,
        orchestrator,
        provider_configured=True,
        chat_service=chat_service,
        worker=WorkerLoop(orchestrator, interval_seconds=10),
        retrieval_pipeline=_SkillRetrieval(),
    )
    return app, repository, store, chat_adapter


def _create_sample_project(tmp_path: Path) -> Path:
    proj_dir = tmp_path / "sample_project"
    src_dir = proj_dir / "src"
    src_dir.mkdir(parents=True, exist_ok=True)
    
    (src_dir / "main.py").write_text(
        "def compute_summary(data):\n"
        "    # Compute summary values\n"
        "    return sum(data) / len(data)\n\n"
        "def main():\n"
        "    print(compute_summary([1, 2, 3]))\n",
        encoding="utf-8",
    )
    (src_dir / "utils.py").write_text(
        "def format_metric(val):\n"
        "    return f'{val:.2f}'\n",
        encoding="utf-8",
    )
    (src_dir / "test_main.py").write_text(
        "def test_compute():\n"
        "    from src.main import compute_summary\n"
        "    assert compute_summary([10, 20]) == 15.0\n",
        encoding="utf-8",
    )
    # Binary file for rejection testing
    (src_dir / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR")
    return proj_dir


# =========================================================================
# 1. Project Q&A Context Injection & Durable Messages Persistence
# =========================================================================

def test_project_ask_with_snippet_context_and_durable_messages(tmp_path: Path):
    app, repo, store, _ = _build_g4_test_environment(tmp_path)
    client = TestClient(app)

    proj_dir = _create_sample_project(tmp_path)
    create_res = client.post(
        "/api/v1/projects",
        json={"name": "Sample Test Project", "root_path": str(proj_dir), "description": "Test Repo"},
    )
    assert create_res.status_code == 200, create_res.text
    project_id = create_res.json()["project_id"]

    # Ask with file and snippet context
    ask_payload = {
        "question": "分析 compute_summary 的除零风险",
        "current_file": "src/main.py",
        "selected_snippet": "return sum(data) / len(data)",
    }
    ask_res = client.post(f"/api/v1/projects/{project_id}/ask", json=ask_payload)
    assert ask_res.status_code == 200, ask_res.text
    ask_data = ask_res.json()
    assert "src/main.py" in ask_data["cited_files"]
    assert len(ask_data["answer_markdown"]) > 0

    # Verify durable persistence in messages endpoint
    msg_res = client.get(f"/api/v1/projects/{project_id}/messages")
    assert msg_res.status_code == 200, msg_res.text
    msg_data = msg_res.json()
    assert msg_data["project_id"] == project_id
    items = msg_data["items"]
    assert len(items) >= 2

    user_msg = next((m for m in items if m["role"] == "user"), None)
    assert user_msg is not None
    assert user_msg["content"] == "分析 compute_summary 的除零风险"
    assert user_msg["selected_snippet"] == "return sum(data) / len(data)"
    assert "src/main.py" in user_msg["cited_files"]

    assistant_msg = next((m for m in items if m["role"] == "assistant"), None)
    assert assistant_msg is not None
    assert len(assistant_msg["content"]) > 0

    # Add a custom message via POST /messages
    post_msg_res = client.post(
        f"/api/v1/projects/{project_id}/messages",
        json={"role": "user", "content": "手动记录的一条开发备忘", "cited_files": ["src/utils.py"]},
    )
    assert post_msg_res.status_code == 200
    saved_msg = post_msg_res.json()
    assert saved_msg["content"] == "手动记录的一条开发备忘"

    msg_res_2 = client.get(f"/api/v1/projects/{project_id}/messages")
    assert len(msg_res_2.json()["items"]) == len(items) + 1


# =========================================================================
# 2. Coding Patch Safety & Validation (Missing Target File & Binary Rejection)
# =========================================================================

def test_coding_patch_rejects_missing_target_file_and_binary_file(tmp_path: Path):
    app, repo, store, _ = _build_g4_test_environment(tmp_path)
    client = TestClient(app)

    proj_dir = _create_sample_project(tmp_path)
    create_res = client.post(
        "/api/v1/projects",
        json={"name": "Safety Project", "root_path": str(proj_dir)},
    )
    project_id = create_res.json()["project_id"]

    # 1. Reject proposal with NO target file (Gate 4 strict requirement: no silent fallback to random file)
    bad_prop = client.post(
        f"/api/v1/projects/{project_id}/coding/propose",
        json={"prompt": "重构项目中的某个类"},
    )
    assert bad_prop.status_code == 400, bad_prop.text
    err_body = bad_prop.json()
    assert err_body.get("code") == "target_file_required"

    # 2. Reject proposal targeting a binary file (.png)
    binary_prop = client.post(
        f"/api/v1/projects/{project_id}/coding/propose",
        json={"prompt": "修改图标", "target_file": "src/logo.png"},
    )
    assert binary_prop.status_code == 400, binary_prop.text
    assert binary_prop.json().get("code") == "invalid_file_type"


# =========================================================================
# 3. Coding Patch Apply, Decoupled Verification, and Conflict-Protected Revert
# =========================================================================

def test_coding_patch_apply_verify_and_revert_lifecycle(tmp_path: Path):
    app, repo, store, _ = _build_g4_test_environment(tmp_path)
    client = TestClient(app)

    proj_dir = _create_sample_project(tmp_path)
    create_res = client.post(
        "/api/v1/projects",
        json={"name": "Lifecycle Project", "root_path": str(proj_dir)},
    )
    project_id = create_res.json()["project_id"]

    main_py = proj_dir / "src" / "main.py"
    orig_content = main_py.read_text(encoding="utf-8")

    # Propose patch on src/main.py
    prop_res = client.post(
        f"/api/v1/projects/{project_id}/coding/propose",
        json={
            "instruction": "添加空列表防御校验",
            "target_file": "src/main.py",
            "custom_replacement": (
                "def compute_summary(data):\n"
                "    if not data:\n"
                "        return 0.0\n"
                "    return sum(data) / len(data)\n\n"
                "def main():\n"
                "    print(compute_summary([1, 2, 3]))\n"
            ),
        },
    )
    assert prop_res.status_code == 200, prop_res.text
    prop_data = prop_res.json()
    proposal_id = prop_data["proposal_id"]
    assert prop_data["target_file"] == "src/main.py"
    assert len(prop_data["diff"]) > 0
    assert len(prop_data["original_hash"]) > 0
    assert len(prop_data["verification_commands"]) > 0

    # Apply patch
    apply_res = client.post(
        f"/api/v1/projects/{project_id}/coding/apply",
        json={"proposal_id": proposal_id},
    )
    assert apply_res.status_code == 200, apply_res.text
    assert apply_res.json()["success"] is True

    # Check file was modified on disk
    new_content = main_py.read_text(encoding="utf-8")
    assert "if not data:" in new_content

    # Verification: Disallowed command should be rejected
    bad_verify = client.post(
        f"/api/v1/projects/{project_id}/coding/verify",
        json={"proposal_id": proposal_id, "command": "curl https://evil.example.com"},
    )
    assert bad_verify.status_code == 400
    assert bad_verify.json().get("code") == "disallowed_command"

    # Verification: Allowed command (python -m unittest) executes cleanly
    good_verify = client.post(
        f"/api/v1/projects/{project_id}/coding/verify",
        json={"proposal_id": proposal_id, "command": "python -m unittest --help"},
    )
    assert good_verify.status_code == 200, good_verify.text
    v_data = good_verify.json()
    assert v_data["success"] is True
    assert v_data["exit_code"] == 0
    assert v_data["duration_seconds"] >= 0

    # Revert: Simulate concurrent file modification leading to revision conflict
    main_py.write_text(new_content + "\n# Concurrent edit by another agent\n", encoding="utf-8")

    conflict_revert = client.post(
        f"/api/v1/projects/{project_id}/coding/revert",
        json={"proposal_id": proposal_id},
    )
    assert conflict_revert.status_code == 409, conflict_revert.text
    assert conflict_revert.json().get("code") == "version_conflict"

    # Restore content to expected post-patch state and revert successfully
    main_py.write_text(new_content, encoding="utf-8", newline="\n")

    clean_revert = client.post(
        f"/api/v1/projects/{project_id}/coding/revert",
        json={"proposal_id": proposal_id},
    )
    assert clean_revert.status_code == 200, clean_revert.text
    assert clean_revert.json()["success"] is True

    # Verify original content restored
    restored_content = main_py.read_text(encoding="utf-8").replace("\r\n", "\n")
    assert restored_content == orig_content.replace("\r\n", "\n")


# =========================================================================
# 4. Skill Anti-Hallucination & Honest Failure on Nonexistent Project (U02/V02)
# =========================================================================

def test_skill_code_audit_fails_honestly_for_nonexistent_project(tmp_path: Path):
    app, repo, store, _ = _build_g4_test_environment(tmp_path)
    client = TestClient(app)

    # Calling code_architecture_audit on non-existent project MUST fail (no fake 92/100 score)
    res = client.post(
        "/api/v1/skills/code_architecture_audit/execute",
        json={"inputs": {"project_id": "non_existent_project_999"}},
    )
    # The server returns 400 skill_execution_failed when a skill fails
    assert res.status_code in (400, 200)
    data = res.json()
    assert data.get("code") == "skill_execution_failed" or data.get("status") == "failed"
    err_text = data.get("message") or data.get("error") or ""
    assert "不存在" in err_text or "not found" in err_text.lower() or "non_existent" in err_text


# =========================================================================
# 5. Real Skill Execution with Tool Traces and Artifacts Persistence
# =========================================================================

def test_skill_code_audit_real_tool_traces_and_artifacts(tmp_path: Path):
    app, repo, store, _ = _build_g4_test_environment(tmp_path)
    client = TestClient(app)

    proj_dir = _create_sample_project(tmp_path)
    create_res = client.post(
        "/api/v1/projects",
        json={"name": "Audit Skill Project", "root_path": str(proj_dir)},
    )
    project_id = create_res.json()["project_id"]

    exec_res = client.post(
        "/api/v1/skills/code_architecture_audit/execute",
        json={"inputs": {"project_id": project_id}},
    )
    assert exec_res.status_code == 200, exec_res.text
    result = exec_res.json()

    assert result["status"] == "completed"
    assert result["skill_id"] == "code_architecture_audit"

    # Check real tool traces
    traces = result.get("tool_traces", [])
    assert len(traces) >= 3, f"Expected traces for walkthrough, audit, diff, got: {traces}"
    tool_names = [t.get("tool_name") or t.get("tool") for t in traces]
    assert "project_walkthrough" in tool_names
    assert "project_audit" in tool_names
    assert "git_semantic_diff" in tool_names
    assert all(t["status"] == "success" for t in traces)

    # Check artifacts generated and stored in LocalArtifactStore
    artifacts = result.get("artifacts", [])
    assert len(artifacts) >= 2, f"Expected generated artifacts, got: {artifacts}"
    art_names = [a["name"] for a in artifacts]
    assert "project_audit_report.md" in art_names
    assert "project_walkthrough.md" in art_names

    # Verify actual persistence in artifact store
    for art in artifacts:
        art_id = art["artifact_id"]
        content = store.read_bytes_by_id(art_id)
        assert len(content) > 0


# =========================================================================
# 6. Literature Survey Skill Real Tool Traces & Artifacts Persistence
# =========================================================================

def test_skill_literature_comparative_survey_execution(tmp_path: Path):
    app, repo, store, _ = _build_g4_test_environment(tmp_path)
    client = TestClient(app)

    # 1. Missing paper_ids should fail
    fail_res = client.post(
        "/api/v1/skills/literature_comparative_survey/execute",
        json={"inputs": {"paper_ids": []}},
    )
    assert fail_res.status_code in (400, 200)
    fail_data = fail_res.json()
    assert fail_data.get("code") == "skill_execution_failed" or fail_data.get("status") == "failed"

    # 2. Nonexistent paper should fail honestly
    nonexist_res = client.post(
        "/api/v1/skills/literature_comparative_survey/execute",
        json={"inputs": {"paper_ids": ["paper-missing-without-magic-name"]}},
    )
    assert nonexist_res.status_code in (400, 200)
    nonexist_data = nonexist_res.json()
    assert nonexist_data.get("code") == "skill_execution_failed" or nonexist_data.get("status") == "failed"

    # 3. Valid paper_ids produce tool traces and survey artifact
    valid_res = client.post(
        "/api/v1/skills/literature_comparative_survey/execute",
        json={"inputs": {"paper_ids": ["doc-quant-2024", "doc-topo-2025"], "focus_dimensions": ["理论假设", "关键指标"]}},
    )
    assert valid_res.status_code == 200, valid_res.text
    result = valid_res.json()
    assert result["status"] == "completed"

    traces = result.get("tool_traces", [])
    assert len(traces) >= 3  # 2 get_paper_evidence + 1 rag_hybrid_search
    tool_names = [t.get("tool_name") or t.get("tool") for t in traces]
    assert "get_paper_evidence" in tool_names
    assert "rag_hybrid_search" in tool_names
    combined = next(t for t in traces if t.get("tool_name") == "rag_hybrid_search")
    assert set(combined["document_ids"]) == {"doc-quant-2024", "doc-topo-2025"}
    assert "chunk-excluded-1" not in combined["chunk_ids"]

    artifacts = result.get("artifacts", [])
    assert len(artifacts) >= 1
    survey_art = artifacts[0]
    content = store.read_bytes_by_id(survey_art["artifact_id"])
    assert len(content) > 0
