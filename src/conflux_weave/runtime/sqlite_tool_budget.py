"""P6-B4 工具调用预算硬限制：预留 → 执行 → 记录实际 → 释放 → 超限停止。

与 W3 的 step/attempt 级预算（authorize_external_call）互补：本模块为不在
attempt 上下文内执行的工具调用（如受限计算工具）提供 Run 级预算门。所有判定
只读 SQLite 权威表；超限后 budget_limits.state 置为 stopped，后续调用一律拒绝，
且不存在绕过路径（确定性兜底不得执行外部工具调用）。
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timedelta

from conflux_weave.runtime.sqlite_contracts import RecordNotFound


class ToolBudgetExceeded(RuntimeError):
    """预算不足或已停止：不得发起外部工具调用。"""


class ToolBudgetMixin:
    def _tool_budget_totals(self, connection: sqlite3.Connection, run_id: str) -> tuple[int, int]:
        usage = connection.execute(
            "SELECT COALESCE(SUM(tool_calls), 0) AS tc, COALESCE(SUM(wall_clock_seconds), 0) AS wc "
            "FROM tool_budget_usage WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        reserved = connection.execute(
            "SELECT COALESCE(SUM(tool_calls), 0) AS tc, COALESCE(SUM(wall_clock_seconds), 0) AS wc "
            "FROM tool_budget_reservations WHERE run_id = ? AND status = 'active'",
            (run_id,),
        ).fetchone()
        return int(usage["tc"]) + int(reserved["tc"]), int(usage["wc"]) + int(reserved["wc"])

    def reserve_tool_budget(
        self,
        run_id: str,
        *,
        tool_calls: int = 1,
        wall_clock_seconds: int,
        now: str | None = None,
    ) -> str:
        """预留工具预算；不足或已停止时抛 ToolBudgetExceeded（调用方不得执行）。"""
        applied_at = now or self.clock()
        if tool_calls < 0 or wall_clock_seconds < 0:
            raise ValueError("tool budget reservation must be non-negative")
        with self._connect() as connection:
            limit = connection.execute(
                "SELECT * FROM budget_limits WHERE run_id = ?", (run_id,)
            ).fetchone()
            if limit is None:
                raise RecordNotFound("BudgetLimit not found")
            if limit["state"] != "active":
                raise ToolBudgetExceeded("run budget is stopped")
            created_at = str(limit["created_at"])
            deadline = datetime.fromisoformat(created_at.replace("Z", "+00:00")) + timedelta(
                seconds=int(limit["wall_clock_seconds"])
            )
            current = datetime.fromisoformat(applied_at.replace("Z", "+00:00"))
            if current > deadline:
                raise ToolBudgetExceeded("run wall-clock deadline exceeded")
            used_calls, used_seconds = self._tool_budget_totals(connection, run_id)
            if used_calls + tool_calls > int(limit["tool_calls"]):
                raise ToolBudgetExceeded("tool_calls budget exhausted")
            if used_seconds + wall_clock_seconds > int(limit["wall_clock_seconds"]):
                raise ToolBudgetExceeded("wall_clock budget exhausted")
            reservation_id = f"tool-res-{uuid.uuid4().hex}"
            connection.execute(
                "INSERT INTO tool_budget_reservations "
                "(reservation_id, run_id, tool_calls, wall_clock_seconds, status, created_at) "
                "VALUES (?, ?, ?, ?, 'active', ?)",
                (reservation_id, run_id, tool_calls, wall_clock_seconds, applied_at),
            )
        return reservation_id

    def settle_tool_budget(
        self,
        reservation_id: str,
        *,
        actual_tool_calls: int,
        actual_seconds: int,
        now: str | None = None,
    ) -> bool:
        """记录实际消耗并关闭预留；超出预留的部分仍如实入账。"""
        applied_at = now or self.clock()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT run_id, status FROM tool_budget_reservations WHERE reservation_id = ?",
                (reservation_id,),
            ).fetchone()
            if row is None:
                raise RecordNotFound("Tool budget reservation not found")
            if row["status"] != "active":
                return False
            connection.execute(
                "UPDATE tool_budget_reservations SET status = 'settled', closed_at = ? WHERE reservation_id = ?",
                (applied_at, reservation_id),
            )
            connection.execute(
                "INSERT INTO tool_budget_usage (run_id, tool_calls, wall_clock_seconds, source, created_at) "
                "VALUES (?, ?, ?, 'tool-compute', ?)",
                (row["run_id"], max(0, int(actual_tool_calls)), max(0, int(actual_seconds)), applied_at),
            )
        return True

    def release_tool_budget(self, reservation_id: str, *, now: str | None = None) -> bool:
        applied_at = now or self.clock()
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE tool_budget_reservations SET status = 'released', closed_at = ? "
                "WHERE reservation_id = ? AND status = 'active'",
                (applied_at, reservation_id),
            )
            return cursor.rowcount > 0

    def stop_tool_budget(self, run_id: str, *, now: str | None = None) -> bool:
        """预算超限后停止该 Run 的后续外部工具调用。"""
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE budget_limits SET state = 'stopped' WHERE run_id = ? AND state = 'active'",
                (run_id,),
            )
            return cursor.rowcount > 0

    def get_tool_budget_usage(self, run_id: str) -> dict[str, int]:
        with self._connect() as connection:
            usage = connection.execute(
                "SELECT COALESCE(SUM(tool_calls), 0) AS tc, COALESCE(SUM(wall_clock_seconds), 0) AS wc "
                "FROM tool_budget_usage WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            reserved = connection.execute(
                "SELECT COALESCE(SUM(tool_calls), 0) AS tc, COALESCE(SUM(wall_clock_seconds), 0) AS wc "
                "FROM tool_budget_reservations WHERE run_id = ? AND status = 'active'",
                (run_id,),
            ).fetchone()
        return {
            "tool_calls_used": int(usage["tc"]),
            "wall_clock_seconds_used": int(usage["wc"]),
            "tool_calls_reserved": int(reserved["tc"]),
            "wall_clock_seconds_reserved": int(reserved["wc"]),
        }
