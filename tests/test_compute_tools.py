"""P6-B2/B3 受限计算工具与权限分级测试（离线，全部通过 subprocess/fixture 验证）。"""

import pytest

from conflux_weave.compute_tools import (
    ComputeToolRequest,
    RestrictedComputeSandbox,
    ToolClass,
    ToolDecision,
    ToolPolicy,
)
from conflux_weave.runtime import LocalArtifactStore


@pytest.fixture()
def sandbox(tmp_path):
    store = LocalArtifactStore(tmp_path / "artifacts")
    return RestrictedComputeSandbox(store, workspace_root=tmp_path / "compute")


def test_normal_compute_produces_stdout_and_output_artifacts(sandbox) -> None:
    """验收：正常计算可以生成 Artifact。"""
    request = ComputeToolRequest.from_payload({
        "script": "print('hello'); open('result.json','w').write('{\"total\": 42}')",
        "output_paths": ["result.json"],
        "timeout_seconds": 10,
    })
    result = sandbox.execute(request)
    assert result.status == "succeeded", result.error
    assert result.stdout_artifact_id is not None
    assert len(result.output_artifact_ids) == 1
    assert result.duration_ms >= 0
    content = sandbox.store.read_bytes_by_id(result.output_artifact_ids[0]).decode()
    assert "42" in content


def test_timeout_terminates_script(sandbox) -> None:
    """验收：超时会终止。"""
    request = ComputeToolRequest.from_payload({
        "script": "import time; time.sleep(30)",
        "timeout_seconds": 1,
    })
    result = sandbox.execute(request)
    assert result.status == "timeout"
    assert "terminated" in result.error


def test_illegal_output_path_rejected_at_contract(sandbox) -> None:
    """验收：非法路径被拒绝（绝对路径 / 越级路径在合同层拒绝）。"""
    with pytest.raises(ValueError):
        ComputeToolRequest.from_payload({"script": "print(1)", "output_paths": ["C:/Windows/evil"]})
    with pytest.raises(ValueError):
        ComputeToolRequest.from_payload({"script": "print(1)", "output_paths": ["../escape"]})
    with pytest.raises(ValueError):
        ComputeToolRequest.from_payload({"script": "   "})
    with pytest.raises(ValueError):
        ComputeToolRequest.from_payload({"script": "print(1)", "timeout_seconds": 9999})


def test_network_and_system_commands_are_blocked(sandbox) -> None:
    """验收：网络访问被拒绝；任意系统命令被拒绝。"""
    request = ComputeToolRequest.from_payload({
        "script": "import socket\nprint('should not reach')",
        "timeout_seconds": 10,
    })
    result = sandbox.execute(request)
    assert result.status == "failed"
    assert "blocked" in result.error

    request2 = ComputeToolRequest.from_payload({
        "script": "import os\nos.system('echo hacked')",
        "timeout_seconds": 10,
    })
    result2 = sandbox.execute(request2)
    assert result2.status == "failed"
    assert "blocked" in result2.error


def test_file_access_confined_to_sandbox(sandbox) -> None:
    """验收：读越权被拒绝，output 白名单外写入被拒绝。"""
    request = ComputeToolRequest.from_payload({
        "script": "open('../user_script.py').read()",
        "timeout_seconds": 10,
    })
    result = sandbox.execute(request)
    assert result.status == "failed"
    assert "blocked" in result.error

    request2 = ComputeToolRequest.from_payload({
        "script": "open('../escape.txt','w').write('x')",
        "timeout_seconds": 10,
    })
    result2 = sandbox.execute(request2)
    assert result2.status == "failed"
    assert "blocked" in result2.error


def test_large_output_capped(sandbox) -> None:
    """验收：大输出被限制（声明产物超过上限 → 失败；stdout 截断标记）。"""
    request = ComputeToolRequest.from_payload({
        "script": "print('x' * 300000)",
        "timeout_seconds": 10,
    })
    result = sandbox.execute(request)
    assert result.status == "succeeded"
    assert result.resource_usage["stdout_truncated"] is True

    request2 = ComputeToolRequest.from_payload({
        "script": "open('big.bin','wb').write(b'0' * 3000000)",
        "output_paths": ["big.bin"],
        "timeout_seconds": 10,
    })
    result2 = sandbox.execute(request2)
    assert result2.status == "failed"
    assert "exceeds" in result2.error


def test_script_error_does_not_crash_and_is_recorded(sandbox) -> None:
    """验收：工具错误不会导致调用方进程退出，错误折叠进结果。"""
    request = ComputeToolRequest.from_payload({
        "script": "raise ValueError('boom')",
        "timeout_seconds": 10,
    })
    result = sandbox.execute(request)
    assert result.status == "failed"
    assert "boom" in result.error
    # 声明了产物但脚本崩溃 → failed 且不产出 artifact
    assert result.output_artifact_ids == ()


def test_declared_output_missing_marks_failed(sandbox) -> None:
    request = ComputeToolRequest.from_payload({
        "script": "print('no files written')",
        "output_paths": ["missing.json"],
        "timeout_seconds": 10,
    })
    result = sandbox.execute(request)
    assert result.status == "failed"
    assert "missing.json" in result.error


def test_temp_workspace_cleaned_after_run(sandbox, tmp_path) -> None:
    from pathlib import Path

    request = ComputeToolRequest.from_payload({"script": "open('out.txt','w').write('x')", "timeout_seconds": 10})
    before = set((tmp_path / "compute").iterdir()) if (tmp_path / "compute").exists() else set()
    sandbox.execute(request)
    after = list((tmp_path / "compute").iterdir())
    # 每次调用的一次性工作区已删除（不残留 sandbox 目录）
    assert not after or all(item.is_dir() and item.name in {p.name for p in before} for item in after)


def test_input_artifacts_mounted_readonly(sandbox, tmp_path) -> None:
    store = sandbox.store
    artifact = store.put_bytes(b"a,b,c\n1,2,3\n", media_type="text/csv", producer_step_id="fixture", schema_version="fixture.v1")
    mounted_name = artifact.artifact_id
    request2 = ComputeToolRequest.from_payload({
        "script": f"data = open('../input/{mounted_name}', 'r').read(); print(len(data))",
        "input_artifacts": [artifact.artifact_id],
        "timeout_seconds": 10,
    })
    result2 = sandbox.execute(request2)
    assert result2.status == "succeeded", result2.error
    stdout = sandbox.store.read_bytes_by_id(result2.stdout_artifact_id).decode()
    assert stdout.strip() == "12"


# ------------------------------------------------------------------ P6-B3 权限分级


def test_tool_policy_default_decisions() -> None:
    policy = ToolPolicy()
    assert policy.decide("library.search", ToolClass.READ_ONLY) is ToolDecision.AUTO
    assert policy.decide("python.compute", ToolClass.COMPUTE) is ToolDecision.AUTO
    assert policy.decide("http.write", ToolClass.EXTERNAL_WRITE) is ToolDecision.HITL_REQUIRED


def test_tool_policy_allow_deny_and_approval() -> None:
    policy = ToolPolicy(
        allowed_tools=("python.compute", "repo.push", "mail.send"),
        denied_tools=("web.fetch",),
        approvals=({"tool": "repo.push", "approved_by": "user_default", "at": "2026-09-12T00:00:00Z"},),
    )
    assert policy.decide("python.compute", ToolClass.COMPUTE) is ToolDecision.AUTO
    assert policy.decide("web.fetch", ToolClass.READ_ONLY) is ToolDecision.DENIED  # 显式禁止优先
    assert policy.decide("repo.push", ToolClass.EXTERNAL_WRITE) is ToolDecision.AUTO  # 有审批记录
    assert policy.decide("mail.send", ToolClass.EXTERNAL_WRITE) is ToolDecision.HITL_REQUIRED  # 无审批 → HITL
    no_allow = ToolPolicy(approvals=policy.approvals)
    assert no_allow.decide("mcp.other", ToolClass.READ_ONLY) is ToolDecision.AUTO
    restricted = ToolPolicy(allowed_tools=("python.compute",), approvals=policy.approvals)
    assert restricted.decide("repo.push", ToolClass.EXTERNAL_WRITE) is ToolDecision.DENIED  # 允许列表之外一律拒绝


def test_policy_from_payload_parses_contract() -> None:
    policy = ToolPolicy.from_payload({
        "allowed_tools": ["python.compute"],
        "denied_tools": [],
        "resource_limits": {"max_timeout_seconds": 30, "max_output_bytes": 1000, "max_memory_bytes": None},
        "approvals": [],
    })
    assert policy.max_timeout_seconds == 30
    assert policy.max_output_bytes == 1000
    assert policy.max_memory_bytes is None
    assert policy.decide("python.compute", ToolClass.COMPUTE) is ToolDecision.AUTO


def test_sandbox_records_agent_event_for_run(sandbox) -> None:
    events: list[dict] = []
    sandbox.agent_event_recorder = lambda **kwargs: events.append(kwargs)
    request = ComputeToolRequest.from_payload({"script": "print('tracked')", "timeout_seconds": 10})
    result = sandbox.execute(request, run_id="run-tool-1")
    assert result.status == "succeeded"
    assert len(events) == 1
    assert events[0]["run_id"] == "run-tool-1"
    assert events[0]["event_type"] == "tool_call"
    assert events[0]["payload"]["result"]["status"] == "succeeded"

# ------------------------------------------------------------------ API 层


class _PassiveRuntime:
    executor_id = "passive@v1"
    task_kinds = ("paper_discovery",)

    def work_once(self, **kwargs):
        return None


def test_api_compute_tool_endpoint(tmp_path) -> None:
    from starlette.testclient import TestClient
    from conflux_weave.runtime import SQLiteRuntimeRepository
    from conflux_weave.server import WorkerLoop, create_app

    store = LocalArtifactStore(tmp_path / "artifacts")
    repository = SQLiteRuntimeRepository(tmp_path / "db" / "runtime.sqlite3", store)
    app = create_app(repository, _PassiveRuntime(), worker=WorkerLoop(_PassiveRuntime(), interval_seconds=10))
    client = TestClient(app)

    ok = client.post("/api/v1/tools/compute", json={
        "script": "print('api smoke'); open('answer.json','w').write('{\"ok\": true}')",
        "output_paths": ["answer.json"],
        "timeout_seconds": 10,
    })
    assert ok.status_code == 200
    payload = ok.json()
    assert payload["status"] == "succeeded"
    assert payload["stdout_artifact_id"]
    assert len(payload["output_artifact_ids"]) == 1

    timeout_case = client.post("/api/v1/tools/compute", json={
        "script": "import time; time.sleep(30)",
        "timeout_seconds": 1,
    })
    assert timeout_case.status_code == 200
    assert timeout_case.json()["status"] == "timeout"

    invalid = client.post("/api/v1/tools/compute", json={"script": "print(1)", "output_paths": ["C:/evil"]})
    assert invalid.status_code == 422

    bad_json = client.post("/api/v1/tools/compute", content=b"not-json", headers={"Content-Type": "application/json"})
    assert bad_json.status_code == 422
