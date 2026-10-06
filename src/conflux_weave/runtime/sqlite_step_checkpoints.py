"""P6-C2 Step 级 checkpoint 台账：深度研究主链路的每阶段恢复点。

覆盖链路阶段：plan / retrieve / claim / verify / synthesize / write / deliver。
每条 checkpoint 保存输入摘要、输出产物、状态、attempt、错误引用、起止时间与
可恢复条件。恢复规则：
- 已有输出 Artifact 的步骤优先复用（resumable=1 且输入摘要一致）；
- 不确定结果标记 unknown_outcome（resumable=0，不自动重试）；
- 原始失败记录保留（errors 表不被清除）。
"""

from enum import StrEnum
import sqlite3
import uuid

from conflux_weave.runtime.sqlite_contracts import RecordNotFound

CHAIN_PHASES = ("plan", "retrieve", "claim", "verify", "synthesize", "write", "deliver")


class StepRecoveryOutcome(StrEnum):
    REQUEST_NOT_SENT = "request_not_sent"
    REQUEST_SENT_NO_RESPONSE = "request_sent_no_response"
    RESPONSE_RECEIVED_NOT_PERSISTED = "response_received_not_persisted"
    RESPONSE_PERSISTED_DELIVERY_FAILED = "response_persisted_delivery_failed"
    DELIVERY_PUBLISHED = "delivery_published"


class RecoveryAction(StrEnum):
    AUTOMATIC_RETRY = "automatic_retry"
    REUSE_ARTIFACT = "reuse_artifact"
    AWAIT_USER_DECISION = "await_user_decision"
    TERMINATE_RUN = "terminate_run"


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

    def evaluate_recovery_action(
        self,
        run_id: str,
        step_id: str,
    ) -> tuple[StepRecoveryOutcome, RecoveryAction, str]:
        """评估步骤恢复决策 (P7-C2)：
        将当前执行/中断状态划分为 5 种结果，并依据副作用类型推荐恢复动作。

        返回: (outcome, action, rationale)
        """
        with self._connect() as connection:
            # 0. 检查 Run 或 Step 是否已处于终态
            run = connection.execute(
                "SELECT status FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if run is not None and run["status"] in {"failed", "cancelled"}:
                return (
                    StepRecoveryOutcome.REQUEST_NOT_SENT,
                    RecoveryAction.TERMINATE_RUN,
                    f"Run 已处于终态 ({run['status']})，终止重试与恢复执行。",
                )

            step = connection.execute(
                "SELECT status, attempt FROM steps WHERE step_id = ?",
                (step_id,),
            ).fetchone()
            if step is not None and step["status"] == "failed":
                return (
                    StepRecoveryOutcome.REQUEST_NOT_SENT,
                    RecoveryAction.TERMINATE_RUN,
                    "步骤已处于失败终态，终止恢复流程。",
                )

            # 1. 检查是否已完成交付发布
            delivery = connection.execute(
                "SELECT 1 FROM deliveries WHERE run_id = ?", (run_id,)
            ).fetchone()
            if delivery is not None:
                return (
                    StepRecoveryOutcome.DELIVERY_PUBLISHED,
                    RecoveryAction.REUSE_ARTIFACT,
                    "交付已发布，无需重试，直接复用已发布成果。",
                )

            # 2. 检查 step_checkpoints 是否存在已完成阶段产物
            checkpoints = connection.execute(
                "SELECT * FROM step_checkpoints WHERE run_id = ? AND step_id = ? "
                "ORDER BY created_at DESC",
                (run_id, step_id),
            ).fetchall()

            has_succeeded_chk = any(
                row["status"] == "succeeded" and row["resumable"] == 1 for row in checkpoints
            )
            has_terminal_chk = any(
                row["chain_phase"] in {"write", "deliver"} and row["status"] == "succeeded"
                for row in checkpoints
            )
            if has_terminal_chk or (has_succeeded_chk and any(row["chain_phase"] == "deliver" for row in checkpoints)):
                return (
                    StepRecoveryOutcome.RESPONSE_PERSISTED_DELIVERY_FAILED,
                    RecoveryAction.REUSE_ARTIFACT,
                    "计算/写作响应已持久化为 Checkpoint，优先复用产物进行交付重放，避免重复计费。",
                )

            # 3. 检查 attempts 与 attempt_effects
            effect = connection.execute(
                """
                SELECT COALESCE(sp.side_effect, 'none') AS side_effect,
                       COALESCE(ae.effect_state, 'not_started') AS effect_state,
                       ae.response_artifact_ref
                FROM attempts a
                LEFT JOIN step_policies sp ON sp.step_id = a.step_id
                LEFT JOIN attempt_effects ae ON ae.attempt_id = a.attempt_id
                WHERE a.step_id = ? ORDER BY a.attempt_number DESC LIMIT 1
                """,
                (step_id,),
            ).fetchone()

            if effect is None or effect["effect_state"] == "not_started":
                return (
                    StepRecoveryOutcome.REQUEST_NOT_SENT,
                    RecoveryAction.AUTOMATIC_RETRY,
                    "请求尚未向外部 Provider 发出，无外部副作用或额外计费，可安全自动重试。",
                )

            if effect["effect_state"] == "request_started":
                if effect["side_effect"] == "paid_external_unknown":
                    return (
                        StepRecoveryOutcome.REQUEST_SENT_NO_RESPONSE,
                        RecoveryAction.AWAIT_USER_DECISION,
                        "外部付费请求已发出但未收到确认响应，存在未知外部副作用，需等待人工决策以防止超额计费。",
                    )
                return (
                    StepRecoveryOutcome.REQUEST_NOT_SENT,
                    RecoveryAction.AUTOMATIC_RETRY,
                    "请求为只读或可重放，可自动重试。",
                )

            if effect["effect_state"] == "response_committed":
                return (
                    StepRecoveryOutcome.RESPONSE_RECEIVED_NOT_PERSISTED,
                    RecoveryAction.REUSE_ARTIFACT,
                    "响应已接收，可复用已接收响应产物继续推进。",
                )

            return (
                StepRecoveryOutcome.REQUEST_NOT_SENT,
                RecoveryAction.AUTOMATIC_RETRY,
                "默认回退：安全自动重试。",
            )
