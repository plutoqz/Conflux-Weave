"""P6-A2 数据生命周期：Run 与 Document 的归档/软删除/恢复持久化。

状态推导：deleted_at 非空 = deleted；否则 archived_at 非空 = archived；否则 active。
软删除不触碰 Evidence、Artifact、交付关系，恢复后原 ID 与引用关系不变。
"""

from __future__ import annotations

import sqlite3
from typing import Literal

from conflux_weave.runtime.sqlite_contracts import PersistenceInvariantError, RecordNotFound

LifecycleAction = Literal["archive", "delete", "restore"]
LifecycleState = Literal["active", "archived", "deleted"]

_DOCUMENT_LIFECYCLE_TABLE = "document_lifecycle"


def lifecycle_state_from_row(archived_at: str | None, deleted_at: str | None) -> LifecycleState:
    if deleted_at:
        return "deleted"
    if archived_at:
        return "archived"
    return "active"


class LifecycleRepositoryMixin:
    """统一生命周期字段读写（runs 表列 + document_lifecycle 表）。"""

    def _set_lifecycle(
        self,
        connection: sqlite3.Connection,
        *,
        action: LifecycleAction,
        now: str,
        update_run: bool,
        update_document: bool,
        run_id: str = "",
        document_id: str = "",
    ) -> LifecycleState:
        archived_at: str | None
        deleted_at: str | None
        if action == "archive":
            archived_at, deleted_at = now, None
        elif action == "delete":
            archived_at, deleted_at = None, now
        else:  # restore
            archived_at, deleted_at = None, None
        if update_run:
            row = connection.execute(
                "SELECT run_id FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if row is None:
                raise RecordNotFound("Run not found")
            connection.execute(
                "UPDATE runs SET archived_at = ?, deleted_at = ?, updated_at = ? WHERE run_id = ?",
                (archived_at, deleted_at, now, run_id),
            )
        if update_document:
            row = connection.execute(
                f"SELECT document_id FROM {_DOCUMENT_LIFECYCLE_TABLE} WHERE document_id = ?",
                (document_id,),
            ).fetchone()
            if row is None:
                connection.execute(
                    f"INSERT INTO {_DOCUMENT_LIFECYCLE_TABLE} (document_id, archived_at, deleted_at) VALUES (?, ?, ?)",
                    (document_id, archived_at, deleted_at),
                )
            else:
                connection.execute(
                    f"UPDATE {_DOCUMENT_LIFECYCLE_TABLE} SET archived_at = ?, deleted_at = ? WHERE document_id = ?",
                    (archived_at, deleted_at, document_id),
                )
        return lifecycle_state_from_row(archived_at, deleted_at)

    # ------------------------------------------------------------------ Run
    def set_run_lifecycle(self, run_id: str, action: LifecycleAction, *, now: str | None = None) -> LifecycleState:
        applied_at = now or self.clock()
        with self._connect() as connection:
            return self._set_lifecycle(
                connection,
                action=action,
                now=applied_at,
                update_run=True,
                update_document=False,
                run_id=run_id,
            )

    def get_run_lifecycle(self, run_id: str) -> LifecycleState:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT archived_at, deleted_at FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        if row is None:
            raise RecordNotFound("Run not found")
        return lifecycle_state_from_row(row["archived_at"], row["deleted_at"])

    def get_run_lifecycle_map(self, run_ids) -> dict[str, tuple[str, str | None, str | None]]:
        """批量查询 Run 生命周期：(state, archived_at, deleted_at) 映射。"""
        ids = list(run_ids)
        if not ids:
            return {}
        placeholders = ",".join("?" for _ in ids)
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT run_id, archived_at, deleted_at FROM runs WHERE run_id IN ({placeholders})",
                ids,
            ).fetchall()
        return {
            str(row["run_id"]): (
                lifecycle_state_from_row(row["archived_at"], row["deleted_at"]),
                row["archived_at"],
                row["deleted_at"],
            )
            for row in rows
        }

    def list_run_ids_by_lifecycle(self, state: LifecycleState) -> tuple[str, ...]:
        clause = {
            "active": "WHERE deleted_at IS NULL AND archived_at IS NULL",
            "archived": "WHERE deleted_at IS NULL AND archived_at IS NOT NULL",
            "deleted": "WHERE deleted_at IS NOT NULL",
        }[state]
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT run_id FROM runs {clause} ORDER BY created_at DESC"
            ).fetchall()
        return tuple(str(row["run_id"]) for row in rows)

    # -------------------------------------------------------------- Document
    def set_document_lifecycle(self, document_id: str, action: LifecycleAction, *, now: str | None = None) -> LifecycleState:
        applied_at = now or self.clock()
        with self._connect() as connection:
            return self._set_lifecycle(
                connection,
                action=action,
                now=applied_at,
                update_run=False,
                update_document=True,
                document_id=document_id,
            )

    def get_document_lifecycle(self, document_id: str) -> LifecycleState:
        with self._connect() as connection:
            row = connection.execute(
                f"SELECT archived_at, deleted_at FROM {_DOCUMENT_LIFECYCLE_TABLE} WHERE document_id = ?",
                (document_id,),
            ).fetchone()
        if row is None:
            return "active"
        return lifecycle_state_from_row(row["archived_at"], row["deleted_at"])

    def get_document_lifecycle_map(self) -> dict[str, LifecycleState]:
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT document_id, archived_at, deleted_at FROM {_DOCUMENT_LIFECYCLE_TABLE}"
            ).fetchall()
        return {
            str(row["document_id"]): lifecycle_state_from_row(row["archived_at"], row["deleted_at"])
            for row in rows
        }


__all__ = [
    "LifecycleAction",
    "LifecycleRepositoryMixin",
    "LifecycleState",
    "PersistenceInvariantError",
    "lifecycle_state_from_row",
]
