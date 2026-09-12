"""P6-C2 Step 级 checkpoint 台账：深度研究主链路的每阶段恢复点。

覆盖链路阶段：plan / retrieve / claim / verify / synthesize / write / deliver。
每条 checkpoint 保存输入摘要、输出产物、状态、attempt、错误引用、起止时间与
可恢复条件。恢复规则：
- 已有输出 Artifact 的步骤优先复用（resumable=1 且输入摘要一致）；
- 不确定结果标记 unknown_outcome（resumable=0，不自动重试）；
- 原始失败记录保留（errors 表不被清除）。
"""

from __future__ import annotations

import sqlite3
import uuid

from conflux_weave.runtime.sqlite_contracts import RecordNotFound

CHAIN_PHASES = ("plan", "retrieve", "claim", "verify", "synthesize", "write", "deliver")


class StepCheckpointMixin:
    def record_step_checkpoint(
        self,
        run_id: str,
        step_id: str,
        *,
        chain_phase: str,
        input_digest: str,
        output_artifact_id: str,
        attempt: int,
        status: str = "succeeded",
        error_ref: str | None = None,
        started_at: str,
        finished_at: str | None = None,
        resumable: bool = True,
    ) -> str:
        if chain_phase not in CHAIN_PHASES:
            raise ValueError(f"unsupported chain phase: {chain_phase}")
        if status not in {"succeeded", "failed", "unknown_outcome"}:
            raise ValueError(f"unsupported checkpoint status: {status}")
        checkpoint_id = f"chk-{uuid.uuid4().hex}"
        with self._connect() as connection:
            exists = connection.execute(
                "SELECT 1 FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if exists is None:
                raise RecordNotFound("Run not found")
            connection.execute(
                "INSERT INTO step_checkpoints "
                "(checkpoint_id, run_id, step_id, chain_phase, input_digest, output_artifact_id, "
                " attempt, status, error_ref, started_at, finished_at, resumable, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    checkpoint_id,
                    run_id,
                    step_id,
                    chain_phase,
                    input_digest,
                    output_artifact_id,
                    attempt,
                    status,
                    error_ref,
                    started_at,
                    finished_at or started_at,
                    1 if resumable else 0,
                    started_at,
                ),
            )
        return checkpoint_id

    def list_step_checkpoints(self, run_id: str) -> tuple[dict, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM step_checkpoints WHERE run_id = ? ORDER BY created_at, checkpoint_id",
                (run_id,),
            ).fetchall()
        return tuple(dict(row) for row in rows)

    def reuse_step_checkpoint(
        self,
        run_id: str,
        chain_phase: str,
        input_digest: str,
    ) -> dict | None:
        """取最新的可复用 checkpoint：同 Run、同阶段、同输入摘要、succeeded。"""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM step_checkpoints WHERE run_id = ? AND chain_phase = ? "
                "AND input_digest = ? AND status = 'succeeded' AND resumable = 1 "
                "ORDER BY created_at DESC, checkpoint_id DESC LIMIT 1",
                (run_id, chain_phase, input_digest),
            ).fetchone()
        return dict(row) if row is not None else None

    def mark_step_checkpoints_unknown_outcome(self, run_id: str, step_id: str) -> int:
        """不确定结果：该步骤未完成 checkpoint 置为 unknown_outcome（不可自动复用）。"""
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE step_checkpoints SET status = 'unknown_outcome', resumable = 0 "
                "WHERE run_id = ? AND step_id = ? AND status = 'succeeded' "
                "AND finished_at >= (SELECT COALESCE(MAX(started_at), '') FROM step_checkpoints WHERE run_id = ?)",
                (run_id, step_id, run_id),
            )
            return cursor.rowcount
