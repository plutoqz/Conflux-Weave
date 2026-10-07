"""Skill registry and persistence management for Conflux-Weave (P5.1 + Gate D1)."""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
import json
from pathlib import Path
import re
import sqlite3
from typing import Any
import yaml

from conflux_weave.skills.builtin import BUILTIN_SKILLS
from conflux_weave.skills.spec import (
    SchemaCompatibilityReport,
    SkillBudget,
    SkillCategory,
    SkillSpec,
    SkillStatus,
    SkillVersionRecord,
)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def check_schema_compatibility(
    old_schema: dict[str, Any] | None,
    new_schema: dict[str, Any],
) -> SchemaCompatibilityReport:
    """Analyze backward compatibility and schema drift between skill versions (Gate D1)."""
    if not old_schema:
        return SchemaCompatibilityReport(compatible=True, breaking_changes=(), warnings=())

    breaking: list[str] = []
    warnings: list[str] = []

    old_props = old_schema.get("properties", {})
    new_props = new_schema.get("properties", {})
    old_required = set(old_schema.get("required", []))
    new_required = set(new_schema.get("required", []))

    # 1. Newly introduced required properties break callers not supplying them
    added_required = new_required - old_required
    for prop in added_required:
        breaking.append(f"新增必填参数 '{prop}'，可能破坏现有调用方契约")

    # 2. Existing properties removed
    removed_props = set(old_props.keys()) - set(new_props.keys())
    for prop in removed_props:
        warnings.append(f"已废弃/移除参数 '{prop}'")

    # 3. Property type changes
    for prop, old_def in old_props.items():
        if prop in new_props:
            old_type = old_def.get("type")
            new_type = new_props[prop].get("type")
            if old_type and new_type and old_type != new_type:
                breaking.append(
                    f"参数 '{prop}' 类型变更: 由 '{old_type}' 变更为 '{new_type}'"
                )

    is_compatible = len(breaking) == 0
    return SchemaCompatibilityReport(
        compatible=is_compatible,
        breaking_changes=tuple(breaking),
        warnings=tuple(warnings),
    )


def parse_skill_markdown(content: str) -> SkillSpec:
    """Parse local Markdown/YAML skill definition (Gate D1).

    Format:
    ---
    skill_id: custom_audit
    version: 1.0.0
    name: Custom Audit
    description: Code auditor
    category: governance
    author: Alice
    input_schema:
      type: object
      required: [code]
      properties:
        code: {type: string}
    default_budget:
      max_tokens: 4000
      max_steps: 10
    required_tools: [git_diff]
    rules: [no_secrets]
    ---
    # Prompt Template
    Audit the code below:
    {{code}}
    """
    pattern = r"^---\s*\n(.*?)\n---\s*\n(.*)$"
    match = re.match(pattern, content.strip(), re.DOTALL)
    if not match:
        raise ValueError("Invalid skill markdown format: missing frontmatter delimiter '---'")

    frontmatter_raw, body_raw = match.group(1), match.group(2).strip()
    data = yaml.safe_load(frontmatter_raw) or {}

    skill_id = str(data.get("skill_id") or data.get("id") or "").strip()
    if not skill_id:
        raise ValueError("Skill markdown frontmatter missing 'skill_id'")

    category_str = str(data.get("category", "utility")).lower()
    try:
        category = SkillCategory(category_str)
    except ValueError:
        category = SkillCategory.UTILITY

    budget_raw = data.get("default_budget") or data.get("budget") or {}
    budget = SkillBudget.from_dict(budget_raw)

    return SkillSpec(
        skill_id=skill_id,
        version=str(data.get("version", "1.0.0")),
        name=str(data.get("name", skill_id)),
        description=str(data.get("description", "")),
        category=category,
        author=str(data.get("author", "local_user")),
        input_schema=dict(data.get("input_schema") or data.get("inputs") or {"type": "object"}),
        required_tools=tuple(str(t) for t in (data.get("required_tools") or data.get("tools") or ())),
        prompt_template=body_raw or str(data.get("prompt_template", "")),
        rules=tuple(str(r) for r in data.get("rules", ())),
        default_budget=budget,
        is_builtin=bool(data.get("is_builtin", False)),
        status=SkillStatus(str(data.get("status", "active"))),
    )


class SkillRegistry:
    """Manages skill discovery, validation, versioning, and lifecycle persistence (P5.1 + Gate D1)."""

    def __init__(self, db_path: Path | str | None = None) -> None:
        self._db_path = Path(db_path) if db_path is not None else None
        self._memory_cache: dict[str, SkillSpec] = {}
        self._version_cache: dict[str, list[SkillVersionRecord]] = defaultdict(list)
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
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS skill_versions (
                skill_id TEXT NOT NULL,
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
                change_summary TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                PRIMARY KEY (skill_id, version)
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_skill_versions_lookup
            ON skill_versions(skill_id, created_at DESC)
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
            self._record_version_memory(skill_with_time, change_summary="Builtin initial version")

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
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO skill_versions (
                            skill_id, version, name, description, category, author,
                            input_schema, required_tools, prompt_template, rules,
                            default_budget, change_summary, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                            "Builtin initial version",
                            s.created_at,
                        ),
                    )

    def _record_version_memory(self, spec: SkillSpec, change_summary: str = "") -> None:
        rec = SkillVersionRecord(
            skill_id=spec.skill_id,
            version=spec.version,
            name=spec.name,
            description=spec.description,
            category=spec.category.value,
            author=spec.author,
            input_schema=spec.input_schema,
            required_tools=spec.required_tools,
            prompt_template=spec.prompt_template,
            rules=spec.rules,
            default_budget=spec.default_budget,
            change_summary=change_summary,
            created_at=spec.updated_at or spec.created_at or _utc_now(),
        )
        existing = self._version_cache[spec.skill_id]
        if not any(v.version == spec.version for v in existing):
            existing.insert(0, rec)

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

    def register_skill(self, spec: SkillSpec, change_summary: str = "") -> SkillSpec:
        """Register a new custom skill or update an existing one with version snapshot (Gate D1)."""
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
        self._record_version_memory(updated_spec, change_summary=change_summary or "Register or update")

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
                conn.execute(
                    """
                    INSERT INTO skill_versions (
                        skill_id, version, name, description, category, author,
                        input_schema, required_tools, prompt_template, rules,
                        default_budget, change_summary, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(skill_id, version) DO UPDATE SET
                        name = excluded.name,
                        description = excluded.description,
                        input_schema = excluded.input_schema,
                        required_tools = excluded.required_tools,
                        prompt_template = excluded.prompt_template,
                        rules = excluded.rules,
                        default_budget = excluded.default_budget,
                        change_summary = excluded.change_summary
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
                        change_summary or "Version update",
                        updated_spec.updated_at,
                    ),
                )
        return updated_spec

    def list_versions(self, skill_id: str) -> list[SkillVersionRecord]:
        """Fetch all historical version records for a skill (Gate D1)."""
        conn = self._connect()
        if conn is not None:
            rows = conn.execute(
                """
                SELECT * FROM skill_versions
                WHERE skill_id = ?
                ORDER BY created_at DESC
                """,
                (skill_id,),
            ).fetchall()
            return [self._row_to_version_record(r) for r in rows]

        return list(self._version_cache.get(skill_id, []))

    def rollback_skill(self, skill_id: str, target_version: str) -> SkillSpec:
        """Rollback a skill to a specific historical version snapshot (Gate D1)."""
        versions = self.list_versions(skill_id)
        target = next((v for v in versions if v.version == target_version), None)
        if target is None:
            raise ValueError(f"Version '{target_version}' not found for skill '{skill_id}'")

        now = _utc_now()
        restored = SkillSpec(
            skill_id=target.skill_id,
            version=target.version,
            name=target.name,
            description=target.description,
            category=SkillCategory(target.category),
            author=target.author,
            input_schema=target.input_schema,
            required_tools=target.required_tools,
            prompt_template=target.prompt_template,
            rules=target.rules,
            default_budget=target.default_budget,
            is_builtin=False,
            status=SkillStatus.ACTIVE,
            created_at=target.created_at,
            updated_at=now,
        )
        return self.register_skill(restored, change_summary=f"Rolled back to version {target_version}")

    def update_skill_schema(
        self,
        skill_id: str,
        new_schema: dict[str, Any],
        new_version: str | None = None,
        change_summary: str = "",
    ) -> tuple[SkillSpec, SchemaCompatibilityReport]:
        """Edit and update a skill's input schema with drift and compatibility auditing (Gate D1)."""
        current = self.get_skill(skill_id)
        if current is None:
            raise ValueError(f"Skill '{skill_id}' not found")

        report = check_schema_compatibility(current.input_schema, new_schema)

        if new_version is None:
            # Auto bump patch if compatible, minor if breaking
            parts = current.version.split(".")
            if len(parts) == 3 and all(p.isdigit() for p in parts):
                major, minor, patch = int(parts[0]), int(parts[1]), int(parts[2])
                if report.compatible:
                    new_version = f"{major}.{minor}.{patch + 1}"
                else:
                    new_version = f"{major}.{minor + 1}.0"
            else:
                new_version = f"{current.version}.1"

        updated = SkillSpec(
            skill_id=current.skill_id,
            version=new_version,
            name=current.name,
            description=current.description,
            category=current.category,
            author=current.author,
            input_schema=new_schema,
            required_tools=current.required_tools,
            prompt_template=current.prompt_template,
            rules=current.rules,
            default_budget=current.default_budget,
            is_builtin=current.is_builtin,
            status=current.status,
            created_at=current.created_at,
        )
        summary = change_summary or ("Schema update (compatible)" if report.compatible else "Schema update (breaking changes)")
        saved = self.register_skill(updated, change_summary=summary)
        return saved, report

    def update_skill_budget(
        self,
        skill_id: str,
        budget: SkillBudget,
        change_summary: str = "",
    ) -> SkillSpec:
        """Update default compute and execution budget configuration for a skill (Gate D1)."""
        current = self.get_skill(skill_id)
        if current is None:
            raise ValueError(f"Skill '{skill_id}' not found")

        updated = SkillSpec(
            skill_id=current.skill_id,
            version=current.version,
            name=current.name,
            description=current.description,
            category=current.category,
            author=current.author,
            input_schema=current.input_schema,
            required_tools=current.required_tools,
            prompt_template=current.prompt_template,
            rules=current.rules,
            default_budget=budget,
            is_builtin=current.is_builtin,
            status=current.status,
            created_at=current.created_at,
        )
        return self.register_skill(updated, change_summary=change_summary or "Budget updated")

    def import_skill_from_markdown(self, content: str) -> SkillSpec:
        """Parse and register a skill from local Markdown file content (Gate D1)."""
        spec = parse_skill_markdown(content)
        return self.register_skill(spec, change_summary="Imported from Markdown")

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

    @staticmethod
    def _row_to_version_record(row: sqlite3.Row) -> SkillVersionRecord:
        budget_data = json.loads(row["default_budget"]) if row["default_budget"] else {}
        return SkillVersionRecord(
            skill_id=row["skill_id"],
            version=row["version"],
            name=row["name"],
            description=row["description"],
            category=row["category"],
            author=row["author"],
            input_schema=json.loads(row["input_schema"]),
            required_tools=tuple(json.loads(row["required_tools"])),
            prompt_template=row["prompt_template"],
            rules=tuple(json.loads(row["rules"])),
            default_budget=SkillBudget.from_dict(budget_data),
            change_summary=row["change_summary"],
            created_at=row["created_at"],
        )
