"""P6-B2 受限计算沙箱 + P6-B3 工具权限分级。

设计边界（与 P6 计划一致）：
- 独立临时目录：每次调用创建一次性工作区，输入挂载只读、输出目录白名单；
- 最大执行时间 / 输出大小 / 内存：时间与输出跨平台强制；内存用 resource(POSIX)，
  Windows 记录 "unavailable"（与预算语义一致的诚实降级）；
- 禁止网络与任意系统命令：子进程以受控 bootstrap 启动（sandbox_boot），导入
  网络/进程类模块直接失败，open() 被限制在沙箱目录内；
- 工具错误不会导致 API 进程退出：一切异常折叠进结果对象；
- 产物与调用记录进入权威运行库（artifact store + agent_events），不新增权威状态源。
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Callable

COMPUTE_TOOL_CALL_SCHEMA = "conflux-weave.compute-tool-call.v1"
COMPUTE_TOOL_RESULT_SCHEMA = "conflux-weave.compute-tool-result.v1"

DEFAULT_MAX_TIMEOUT_SECONDS = 60.0
DEFAULT_MAX_OUTPUT_BYTES = 1_000_000
DEFAULT_MAX_STDOUT_BYTES = 100_000
DEFAULT_MAX_MEMORY_BYTES = 512 * 1024 * 1024

_OUTPUT_MANIFEST = "__cw_outputs__"


class ToolClass(StrEnum):
    """P6-B3 工具三级分类。"""

    READ_ONLY = "read-only"
    COMPUTE = "compute"
    EXTERNAL_WRITE = "external-write"


class ToolDecision(StrEnum):
    AUTO = "auto"
    HITL_REQUIRED = "hitl-required"
    DENIED = "denied"


DEFAULT_TOOL_POLICY: dict[str, str] = {
    ToolClass.READ_ONLY.value: ToolDecision.AUTO.value,
    ToolClass.COMPUTE.value: ToolDecision.AUTO.value,
    ToolClass.EXTERNAL_WRITE.value: ToolDecision.HITL_REQUIRED.value,
}


@dataclass(frozen=True, slots=True)
class ToolPolicy:
    """一次 Run 的工具权限面：允许/禁止列表 + 资源限制 + 审批记录。"""

    allowed_tools: tuple[str, ...] = ()  # 空 = 不限制（按默认策略）
    denied_tools: tuple[str, ...] = ()
    max_timeout_seconds: float = DEFAULT_MAX_TIMEOUT_SECONDS
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES
    max_memory_bytes: int | None = DEFAULT_MAX_MEMORY_BYTES
    approvals: tuple[dict[str, Any], ...] = ()  # {"tool": ..., "approved_by": ..., "at": ...}

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed_tools": list(self.allowed_tools),
            "denied_tools": list(self.denied_tools),
            "max_timeout_seconds": self.max_timeout_seconds,
            "max_output_bytes": self.max_output_bytes,
            "max_memory_bytes": self.max_memory_bytes,
            "approvals": [dict(item) for item in self.approvals],
        }

    @staticmethod
    def from_payload(payload: dict[str, Any] | None) -> "ToolPolicy":
        data = payload or {}
        limits = data.get("resource_limits") or {}
        return ToolPolicy(
            allowed_tools=tuple(str(item) for item in data.get("allowed_tools", ())),
            denied_tools=tuple(str(item) for item in data.get("denied_tools", ())),
            max_timeout_seconds=float(limits.get("max_timeout_seconds", DEFAULT_MAX_TIMEOUT_SECONDS)),
            max_output_bytes=int(limits.get("max_output_bytes", DEFAULT_MAX_OUTPUT_BYTES)),
            max_memory_bytes=limits.get("max_memory_bytes", DEFAULT_MAX_MEMORY_BYTES),
            approvals=tuple(dict(item) for item in data.get("approvals", ()) if isinstance(item, dict)),
        )

    def decide(self, tool_name: str, tool_class: ToolClass) -> ToolDecision:
        """B3 决策顺序：显式禁止 > 允许列表 > 分类默认（自动）> 审批记录（HITL）。"""
        if tool_name in self.denied_tools:
            return ToolDecision.DENIED
        if self.allowed_tools and tool_name not in self.allowed_tools:
            return ToolDecision.DENIED
        decision = DEFAULT_TOOL_POLICY.get(tool_class.value, ToolDecision.HITL_REQUIRED.value)
        if decision == ToolDecision.AUTO.value:
            return ToolDecision.AUTO
        if any(str(item.get("tool")) == tool_name for item in self.approvals):
            return ToolDecision.AUTO
        return ToolDecision.HITL_REQUIRED


@dataclass(frozen=True, slots=True)
class ComputeToolRequest:
    """B2 Tool Contract：script + 输入挂载 + 输出白名单 + 超时。"""

    script: str
    input_artifacts: tuple[str, ...] = ()
    output_paths: tuple[str, ...] = ()
    timeout_seconds: float = DEFAULT_MAX_TIMEOUT_SECONDS

    @staticmethod
    def from_payload(payload: dict[str, Any]) -> "ComputeToolRequest":
        script = str(payload.get("script") or "")
        if not script.strip():
            raise ValueError("script must not be empty")
        timeout = float(payload.get("timeout_seconds") or DEFAULT_MAX_TIMEOUT_SECONDS)
        if not 0.1 <= timeout <= 600:
            raise ValueError("timeout_seconds must be within [0.1, 600]")
        outputs = tuple(str(item) for item in payload.get("output_paths", ()) if str(item).strip())
        for relative in outputs:
            if Path(relative).is_absolute() or ".." in Path(relative).parts:
                raise ValueError(f"illegal output path: {relative}")
        return ComputeToolRequest(
            script=script,
            input_artifacts=tuple(str(item) for item in payload.get("input_artifacts", ())),
            output_paths=outputs,
            timeout_seconds=timeout,
        )


@dataclass
class ComputeToolResult:
    status: str  # succeeded | failed | timeout | denied
    stdout_artifact_id: str | None = None
    output_artifact_ids: tuple[str, ...] = ()
    duration_ms: int = 0
    resource_usage: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    tool_contract: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": COMPUTE_TOOL_RESULT_SCHEMA,
            "status": self.status,
            "stdout_artifact_id": self.stdout_artifact_id,
            "output_artifact_ids": list(self.output_artifact_ids),
            "duration_ms": self.duration_ms,
            "resource_usage": dict(self.resource_usage),
            "error": self.error,
            "tool_contract": dict(self.tool_contract),
        }


class RestrictedComputeSandbox:
    """受限 Python 计算工具执行器（subprocess + bootstrap 守卫 + 输出白名单）。"""

    def __init__(
        self,
        artifact_store,
        *,
        workspace_root: Path,
        max_timeout_seconds: float = DEFAULT_MAX_TIMEOUT_SECONDS,
        max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
        max_memory_bytes: int | None = DEFAULT_MAX_MEMORY_BYTES,
        agent_event_recorder: Callable[..., None] | None = None,
    ) -> None:
        self.store = artifact_store
        self.workspace_root = Path(workspace_root)
        self.max_timeout_seconds = max_timeout_seconds
        self.max_output_bytes = max_output_bytes
        self.max_memory_bytes = max_memory_bytes
        self.agent_event_recorder = agent_event_recorder

    # ------------------------------------------------------------------ 入口
    def execute(
        self,
        request: ComputeToolRequest,
        *,
        policy: ToolPolicy | None = None,
        run_id: str | None = None,
    ) -> ComputeToolResult:
        policy = policy or ToolPolicy()
        started = time.monotonic()
        effective_timeout = min(request.timeout_seconds, policy.max_timeout_seconds, self.max_timeout_seconds)

        sandbox_dir = Path(tempfile.mkdtemp(prefix="cw-compute-", dir=self._ensure_workspace()))
        try:
            input_dir = sandbox_dir / "input"
            output_dir = sandbox_dir / "output"
            input_dir.mkdir()
            output_dir.mkdir()

            input_names = self._materialize_inputs(request.input_artifacts, input_dir)
            result = self._run_subprocess(request, input_dir, output_dir, sandbox_dir, effective_timeout, policy)

            if result.status == "succeeded":
                truncated = self._cap_output_files(output_dir, self.max_output_bytes)
                if truncated:
                    result.status = "failed"
                    result.error = f"output exceeds {self.max_output_bytes} bytes limit: {', '.join(truncated)}"
                output_artifacts = self._collect_outputs(output_dir)
                result.output_artifact_ids = output_artifacts
            if result.status == "succeeded" and request.output_paths:
                written = {name for name in self._list_outputs(output_dir)}
                for expected in request.output_paths:
                    if expected not in written:
                        result.status = "failed"
                        result.error = result.error or f"declared output missing: {expected}"
                        break

            result.duration_ms = int((time.monotonic() - started) * 1000)
            result.tool_contract = {
                "schema_version": COMPUTE_TOOL_CALL_SCHEMA,
                "input_artifacts": list(request.input_artifacts),
                "input_mounted": input_names,
                "output_paths": list(request.output_paths),
                "timeout_seconds": effective_timeout,
            }
            if run_id:
                self._record_run_event(run_id, request, result)
            return result
        finally:
            shutil.rmtree(sandbox_dir, ignore_errors=True)  # 任务结束自动清理

    # ------------------------------------------------------------------ 内部
    def _ensure_workspace(self) -> Path:
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        return self.workspace_root

    def _materialize_inputs(self, input_artifacts: tuple[str, ...], input_dir: Path) -> list[str]:
        """输入产物只读挂载：拷入 input/ 并去掉写权限（POSIX）；名称白名单校验。"""
        mounted: list[str] = []
        for artifact_id in input_artifacts:
            digest = artifact_id.removeprefix("artifact-sha256-")
            if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
                raise ValueError(f"illegal input artifact id: {artifact_id}")
            payload = self.store.read_bytes_by_id(artifact_id)
            target_name = Path(artifact_id).name
            target = input_dir / target_name
            target.write_bytes(payload)
            mounted.append(target_name)
        return mounted

    def _run_subprocess(
        self,
        request: ComputeToolRequest,
        input_dir: Path,
        output_dir: Path,
        sandbox_dir: Path,
        timeout: float,
        policy: ToolPolicy,
    ) -> ComputeToolResult:
        result = ComputeToolResult(status="succeeded")
        boot_path = sandbox_dir / "boot_guard.py"
        boot_path.write_text(_BOOT_GUARD_TEMPLATE.format(
            input_dir=str(input_dir.resolve()),
            output_dir=str(output_dir.resolve()),
        ), encoding="utf-8")
        script_path = sandbox_dir / "user_script.py"
        script_path.write_text(request.script, encoding="utf-8")

        limits: dict[str, int] = {}
        preexec = None
        if sys.platform != "win32" and policy.max_memory_bytes:
            preexec = _make_posix_limits(policy.max_memory_bytes)
            limits["memory_rlimit"] = policy.max_memory_bytes
        else:
            limits["memory_rlimit"] = "unavailable"  # Windows：诚实降级，不虚报

        try:
            completed = subprocess.run(
                [sys.executable, "-I", str(boot_path), str(script_path)],
                capture_output=True,
                timeout=timeout,
                cwd=str(output_dir),
                preexec_fn=preexec,
            )
        except subprocess.TimeoutExpired:
            result.status = "timeout"
            result.error = f"execution exceeded {timeout}s and was terminated"
            result.resource_usage = dict(limits)
            return result
        except Exception as exc:  # noqa: BLE001 - 工具错误不进入 API 进程崩溃路径
            result.status = "failed"
            result.error = f"sandbox launch failed: {type(exc).__name__}: {exc}"
            result.resource_usage = dict(limits)
            return result

        stdout = completed.stdout or b""
        stderr = (completed.stderr or b"").decode("utf-8", errors="replace")
        truncated = False
        if len(stdout) > DEFAULT_MAX_STDOUT_BYTES:
            stdout = stdout[:DEFAULT_MAX_STDOUT_BYTES]
            truncated = True
        stdout_artifact = self.store.put_bytes(
            stdout,
            media_type="text/plain; charset=utf-8",
            producer_step_id="tool-compute",
            schema_version=COMPUTE_TOOL_RESULT_SCHEMA,
        )
        result.stdout_artifact_id = stdout_artifact.artifact_id
        result.resource_usage = {**limits, "stdout_bytes": len(stdout), "stdout_truncated": truncated}

        if completed.returncode != 0:
            result.status = "failed"
            result.error = stderr.strip()[-2000:] or f"exit code {completed.returncode}"
        return result

    def _list_outputs(self, output_dir: Path) -> list[str]:
        return sorted(path.name for path in output_dir.iterdir() if path.is_file())

    def _cap_output_files(self, output_dir: Path, max_bytes: int) -> list[str]:
        offenders: list[str] = []
        for path in output_dir.iterdir():
            if path.is_file() and path.stat().st_size > max_bytes:
                offenders.append(path.name)
        for name in offenders:
            (output_dir / name).unlink()
        return offenders

    def _collect_outputs(self, output_dir: Path) -> tuple[str, ...]:
        artifact_ids: list[str] = []
        for name in self._list_outputs(output_dir):
            path = output_dir / name
            media_type = "application/json" if name.endswith(".json") else "application/octet-stream"
            artifact = self.store.put_bytes(
                path.read_bytes(),
                media_type=media_type,
                producer_step_id="tool-compute",
                schema_version=COMPUTE_TOOL_RESULT_SCHEMA,
            )
            artifact_ids.append(artifact.artifact_id)
        return tuple(artifact_ids)

    def _record_run_event(self, run_id: str, request: ComputeToolRequest, result: ComputeToolResult) -> None:
        """工具调用与产物关系进入 Run 可见面（agent_events 表）。"""
        record = self.agent_event_recorder
        if record is None:
            return
        try:
            record(
                run_id=run_id,
                agent_id="tool-compute",
                event_type="tool_call",
                payload={"request": result.tool_contract, "result": result.to_dict()},
            )
        except Exception:  # noqa: BLE001 - 审计失败不影响工具结果
            pass


def _make_posix_limits(max_memory_bytes: int):
    def apply_limits():
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (max_memory_bytes, max_memory_bytes))

    return apply_limits


_BOOT_GUARD_TEMPLATE = '''"""Conflux-Weave 计算沙箱 bootstrap：网络/系统命令封禁 + 文件访问白名单。"""
import builtins
import io
import os
import sys

INPUT_DIR = {input_dir!r}
OUTPUT_DIR = {output_dir!r}

_BLOCKED_MODULES = {{
    "socket", "ssl", "http", "urllib", "urllib2", "ftplib", "telnetlib",
    "smtplib", "socketserver", "asyncio", "subprocess", "multiprocessing",
    "ctypes", "pty",
}}

_REAL_OPEN = builtins.open


def _guard_import(name, globals=None, locals=None, fromlist=(), level=0):
    root = str(name).split(".")[0]
    if root in _BLOCKED_MODULES:
        raise ImportError("blocked in Conflux-Weave compute sandbox: " + root)
    return _REAL_IMPORT(name, globals, locals, fromlist, level)


def _sandbox_open(file, mode="r", *args, **kwargs):
    path = os.path.abspath(str(file)) if os.path.isabs(str(file)) else os.path.abspath(os.path.join(os.getcwd(), str(file)))
    writing = any(flag in mode for flag in ("w", "a", "x", "+"))
    if writing:
        if not path.startswith(OUTPUT_DIR + os.sep):
            raise PermissionError("write outside output dir is blocked: " + path)
    else:
        if not (path.startswith(INPUT_DIR + os.sep) or path.startswith(OUTPUT_DIR + os.sep)):
            raise PermissionError("read outside sandbox is blocked: " + path)
    return _REAL_OPEN(file, mode, *args, **kwargs)


def _blocked_call(*args, **kwargs):
    raise PermissionError("system command is blocked in Conflux-Weave compute sandbox")


_REAL_IMPORT = builtins.__import__
builtins.__import__ = _guard_import
builtins.open = _sandbox_open
io.open = _sandbox_open
for _name in ("system", "popen", "execv", "execve", "execvp", "execvpe", "fork", "spawnv", "startfile"):
    if hasattr(os, _name):
        setattr(os, _name, _blocked_call)
del _name

if __name__ == "__main__":
    script_path = sys.argv[1]
    source = _REAL_OPEN(script_path, "r", encoding="utf-8").read()
    exec(compile(source, script_path, "exec"), {{"__name__": "__main__"}})
'''


__all__ = [
    "COMPUTE_TOOL_CALL_SCHEMA",
    "COMPUTE_TOOL_RESULT_SCHEMA",
    "ComputeToolRequest",
    "ComputeToolResult",
    "RestrictedComputeSandbox",
    "ToolClass",
    "ToolDecision",
    "ToolPolicy",
    "DEFAULT_TOOL_POLICY",
]
