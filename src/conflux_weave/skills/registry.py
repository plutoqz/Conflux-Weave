"""Skill registry and persistence management for Conflux-Weave (P5.1)."""

from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
import sqlite3
from typing import Any

from conflux_weave.skills.builtin import BUILTIN_SKILLS
from conflux_weave.skills.spec import SkillBudget, SkillCategory, SkillSpec, SkillStatus


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class SkillRegistry:
    """Manages skill discovery, validation, and lifecycle persistence."""

    def __init__(self, db_path: Path | str | None = None) -> None:
        self._db_path = Path(db_path) if db_path is not None else None
        self._memory_cache: dict[str, SkillSpec] = {}
        self._init_builtins()

    def _ensure_tables(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS skills (
                skill_id TEXT PRIMARY KEY,
                version TEXT NOT NULL,
                name TEXT NOT NULL,
                description TEXT NOT NULL,
                category TEXT NOT NULL,
                author TEXT NOT NULL,
                input_schema TEXT NOT NULL,
                required_tools TEXT NOT NULL,
                prompt_template TEXT NOT NULL,
                rules TEXT NOT NULL,
                default_budget TEXT NOT NULL,
                is_builtin INTEGER NOT NULL DEFAULT 1,
                status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'disabled')),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_skills_lookup
            ON skills(category, status)
            """
        )

    def _connect(self) -> sqlite3.Connection | None:
        if self._db_path is None:
            return None
        connection = sqlite3.connect(self._db_path)
        connection.row_factory = sqlite3.Row
        self._ensure_tables(connection)
        return connection

    def _init_builtins(self) -> None:
        """Register builtin authoritative skills in memory and SQLite."""
        now = _utc_now()
        for skill in BUILTIN_SKILLS:
            skill_with_time = SkillSpec(
                skill_id=skill.skill_id,
                version=skill.version,
                name=skill.name,
                description=skill.description,
                category=skill.category,
                author=skill.author,
                input_schema=skill.input_schema,
                required_tools=skill.required_tools,
                prompt_template=skill.prompt_template,
                rules=skill.rules,
                default_budget=skill.default_budget,
                is_builtin=skill.is_builtin,
                status=skill.status,
                created_at=now,
                updated_at=now,
            )
            self._memory_cache[skill.skill_id] = skill_with_time

        conn = self._connect()
        if conn is not None:
            with conn:
                for s in self._memory_cache.values():
                    conn.execute(
                        """
                        INSERT INTO skills (
                            skill_id, version, name, description, category, author,
                            input_schema, required_tools, prompt_template, rules,
                            default_budget, is_builtin, status, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(skill_id) DO UPDATE SET
                            version = excluded.version,
                            name = excluded.name,
                            description = excluded.description,
                            category = excluded.category,
                            input_schema = excluded.input_schema,
                            required_tools = excluded.required_tools,
                            prompt_template = excluded.prompt_template,
                            rules = excluded.rules,
                            default_budget = excluded.default_budget,
                            updated_at = excluded.updated_at
                        """,
                        (
                            s.skill_id,
                            s.version,
                            s.name,
                            s.description,
                            s.category.value,
                            s.author,
                            json.dumps(s.input_schema, ensure_ascii=False),
                            json.dumps(list(s.required_tools), ensure_ascii=False),
                            s.prompt_template,
                            json.dumps(list(s.rules), ensure_ascii=False),
                            json.dumps(s.default_budget.to_dict(), ensure_ascii=False),
                            1 if s.is_builtin else 0,
                            s.status.value,
                            s.created_at,
                            s.updated_at,
                        ),
                    )

    def list_skills(
        self,
        category: SkillCategory | str | None = None,
        status: SkillStatus | str = "active",
    ) -> list[SkillSpec]:
        """Query registered skills by optional category and status filter."""
        target_status = status.value if isinstance(status, SkillStatus) else status
        target_cat = category.value if isinstance(category, SkillCategory) else category

        conn = self._connect()
        if conn is not None:
            query = "SELECT * FROM skills WHERE status = ?"
            params: list[Any] = [target_status]
            if target_cat:
                query += " AND category = ?"
                params.append(target_cat)
            query += " ORDER BY is_builtin DESC, skill_id ASC"

            rows = conn.execute(query, params).fetchall()
            return [self._row_to_spec(r) for r in rows]

        # Memory fallback
        results = [
            s for s in self._memory_cache.values()
            if s.status.value == target_status
        ]
        if target_cat:
            results = [s for s in results if s.category.value == target_cat]
        return sorted(results, key=lambda s: (not s.is_builtin, s.skill_id))

    def get_skill(self, skill_id: str) -> SkillSpec | None:
        """Fetch a specific skill by its unique identifier."""
        conn = self._connect()
        if conn is not None:
            row = conn.execute(
                "SELECT * FROM skills WHERE skill_id = ?", (skill_id,)
            ).fetchone()
            if row is not None:
                return self._row_to_spec(row)

        return self._memory_cache.get(skill_id)

    def register_skill(self, spec: SkillSpec) -> SkillSpec:
        """Register a new custom skill or update an existing one."""
        now = _utc_now()
        updated_spec = SkillSpec(
            skill_id=spec.skill_id,
            version=spec.version,
            name=spec.name,
            description=spec.description,
            category=spec.category,
            author=spec.author,
            input_schema=spec.input_schema,
            required_tools=spec.required_tools,
            prompt_template=spec.prompt_template,
            rules=spec.rules,
            default_budget=spec.default_budget,
            is_builtin=spec.is_builtin,
            status=spec.status,
            created_at=spec.created_at or now,
            updated_at=now,
        )
        self._memory_cache[updated_spec.skill_id] = updated_spec

        conn = self._connect()
        if conn is not None:
            with conn:
                conn.execute(
                    """
                    INSERT INTO skills (
                        skill_id, version, name, description, category, author,
                        input_schema, required_tools, prompt_template, rules,
                        default_budget, is_builtin, status, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(skill_id) DO UPDATE SET
                        version = excluded.version,
                        name = excluded.name,
                        description = excluded.description,
                        category = excluded.category,
                        author = excluded.author,
                        input_schema = excluded.input_schema,
                        required_tools = excluded.required_tools,
                        prompt_template = excluded.prompt_template,
                        rules = excluded.rules,
                        default_budget = excluded.default_budget,
                        is_builtin = excluded.is_builtin,
                        status = excluded.status,
                        updated_at = excluded.updated_at
                    """,
                    (
                        updated_spec.skill_id,
                        updated_spec.version,
                        updated_spec.name,
                        updated_spec.description,
                        updated_spec.category.value,
                        updated_spec.author,
                        json.dumps(updated_spec.input_schema, ensure_ascii=False),
                        json.dumps(list(updated_spec.required_tools), ensure_ascii=False),
                        updated_spec.prompt_template,
                        json.dumps(list(updated_spec.rules), ensure_ascii=False),
                        json.dumps(updated_spec.default_budget.to_dict(), ensure_ascii=False),
                        1 if updated_spec.is_builtin else 0,
                        updated_spec.status.value,
                        updated_spec.created_at,
                        updated_spec.updated_at,
                    ),
                )
        return updated_spec

    def disable_skill(self, skill_id: str) -> bool:
        """Deactivate a skill without physical deletion."""
        skill = self.get_skill(skill_id)
        if skill is None:
            return False

        now = _utc_now()
        disabled = SkillSpec(
            skill_id=skill.skill_id,
            version=skill.version,
            name=skill.name,
            description=skill.description,
            category=skill.category,
            author=skill.author,
            input_schema=skill.input_schema,
            required_tools=skill.required_tools,
            prompt_template=skill.prompt_template,
            rules=skill.rules,
            default_budget=skill.default_budget,
            is_builtin=skill.is_builtin,
            status=SkillStatus.DISABLED,
            created_at=skill.created_at,
            updated_at=now,
        )
        self._memory_cache[skill_id] = disabled

        conn = self._connect()
        if conn is not None:
            with conn:
                conn.execute(
                    "UPDATE skills SET status = 'disabled', updated_at = ? WHERE skill_id = ?",
                    (now, skill_id),
                )
        return True

    def validate_inputs(self, skill_id: str, inputs: dict[str, Any]) -> tuple[bool, str | None]:
        """Validate input parameters against the skill's declared input schema."""
        skill = self.get_skill(skill_id)
        if skill is None:
            return False, f"未找到指定 Skill: {skill_id}"

        required_props = skill.input_schema.get("required", [])
        for req in required_props:
            if req not in inputs:
                return False, f"缺少必要参数: {req}"
            val = inputs[req]
            if val is None or (isinstance(val, (str, list, dict)) and len(val) == 0):
                return False, f"参数 {req} 不能为空"

        properties = skill.input_schema.get("properties", {})
        for key, val in inputs.items():
            if key in properties:
                prop_type = properties[key].get("type")
                if prop_type == "array" and not isinstance(val, (list, tuple)):
                    return False, f"参数 {key} 必须为数组格式"
                elif prop_type == "string" and not isinstance(val, str):
                    return False, f"参数 {key} 必须为字符串"
                elif prop_type == "object" and not isinstance(val, dict):
                    return False, f"参数 {key} 必须为对象"

        return True, None

    @staticmethod
    def _row_to_spec(row: sqlite3.Row) -> SkillSpec:
        budget_data = json.loads(row["default_budget"]) if row["default_budget"] else {}
        return SkillSpec(
            skill_id=row["skill_id"],
            version=row["version"],
            name=row["name"],
            description=row["description"],
            category=SkillCategory(row["category"]),
            author=row["author"],
            input_schema=json.loads(row["input_schema"]),
            required_tools=tuple(json.loads(row["required_tools"])),
            prompt_template=row["prompt_template"],
            rules=tuple(json.loads(row["rules"])),
            default_budget=SkillBudget.from_dict(budget_data),
            is_builtin=bool(row["is_builtin"]),
            status=SkillStatus(row["status"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
