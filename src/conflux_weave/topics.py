"""Research Topic container, multi-object relationship manager, and workspace location tracker for Gate 5 (U21)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import re
from typing import Any
from uuid import uuid4


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


@dataclass
class Topic:
    topic_id: str
    name: str
    objective: str = ""
    description: str = ""
    tags: list[str] = field(default_factory=list)
    document_ids: list[str] = field(default_factory=list)
    note_ids: list[str] = field(default_factory=list)
    run_ids: list[str] = field(default_factory=list)
    project_ids: list[str] = field(default_factory=list)
    conversation_ids: list[str] = field(default_factory=list)
    recent_location: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_utc_now)
    updated_at: str = field(default_factory=_utc_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Topic:
        return cls(
            topic_id=str(data["topic_id"]),
            name=str(data["name"]),
            objective=str(data.get("objective", "")),
            description=str(data.get("description", "")),
            tags=list(data.get("tags") or []),
            document_ids=list(data.get("document_ids") or []),
            note_ids=list(data.get("note_ids") or []),
            run_ids=list(data.get("run_ids") or []),
            project_ids=list(data.get("project_ids") or []),
            conversation_ids=list(data.get("conversation_ids") or []),
            recent_location=dict(data.get("recent_location") or {}),
            created_at=str(data.get("created_at", _utc_now())),
            updated_at=str(data.get("updated_at", _utc_now())),
        )


class TopicStore:
    """Registry and storage for Conflux-Weave research topics."""

    def __init__(self, registry_file: Path) -> None:
        self.registry_file = registry_file
        self._ensure_initialized()

    def _ensure_initialized(self) -> None:
        if not self.registry_file.exists():
            self.registry_file.parent.mkdir(parents=True, exist_ok=True)
            self._save([])

    def _load(self) -> list[Topic]:
        if not self.registry_file.exists():
            return []
        try:
            data = json.loads(self.registry_file.read_text(encoding="utf-8"))
            return [Topic.from_dict(item) for item in data]
        except Exception:
            return []

    def _save(self, topics: list[Topic]) -> None:
        data = [t.to_dict() for t in topics]
        self.registry_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def list_topics(self) -> list[Topic]:
        return self._load()

    def get_topic(self, topic_id: str) -> Topic | None:
        for t in self.list_topics():
            if t.topic_id == topic_id:
                return t
        return None

    def create_topic(
        self,
        name: str,
        objective: str = "",
        description: str = "",
        tags: list[str] | None = None,
        document_ids: list[str] | None = None,
        note_ids: list[str] | None = None,
        run_ids: list[str] | None = None,
        project_ids: list[str] | None = None,
        conversation_ids: list[str] | None = None,
    ) -> Topic:
        if not name.strip():
            raise ValueError("专题名称不能为空。")

        slug = re.sub(r"[^a-zA-Z0-9_-]", "-", name.strip().lower()).strip("-") or "topic"
        h = hashlib.sha256(f"{name}:{uuid4().hex}".encode("utf-8")).hexdigest()[:8]
        topic_id = f"topic-{slug[:24]}-{h}"

        topic = Topic(
            topic_id=topic_id,
            name=name.strip(),
            objective=objective.strip(),
            description=description.strip(),
            tags=list(tags or []),
            document_ids=list(dict.fromkeys(document_ids or [])),
            note_ids=list(dict.fromkeys(note_ids or [])),
            run_ids=list(dict.fromkeys(run_ids or [])),
            project_ids=list(dict.fromkeys(project_ids or [])),
            conversation_ids=list(dict.fromkeys(conversation_ids or [])),
        )
        topics = self._load()
        topics.insert(0, topic)
        self._save(topics)
        return topic

    def update_topic(
        self,
        topic_id: str,
        name: str | None = None,
        objective: str | None = None,
        description: str | None = None,
        tags: list[str] | None = None,
    ) -> Topic | None:
        topics = self._load()
        target: Topic | None = None
        for t in topics:
            if t.topic_id == topic_id:
                target = t
                break
        if not target:
            return None

        if name is not None and name.strip():
            target.name = name.strip()
        if objective is not None:
            target.objective = objective.strip()
        if description is not None:
            target.description = description.strip()
        if tags is not None:
            target.tags = list(tags)
        target.updated_at = _utc_now()
        self._save(topics)
        return target

    def delete_topic(self, topic_id: str) -> bool:
        topics = self._load()
        initial_len = len(topics)
        topics = [t for t in topics if t.topic_id != topic_id]
        if len(topics) < initial_len:
            self._save(topics)
            return True
        return False

    def link_object(self, topic_id: str, object_type: str, object_id: str) -> Topic | None:
        target = self.get_topic(topic_id)
        if not target:
            return None

        attr_map = {
            "document": "document_ids",
            "note": "note_ids",
            "run": "run_ids",
            "project": "project_ids",
            "conversation": "conversation_ids",
        }
        attr = attr_map.get(object_type)
        if not attr:
            raise ValueError(f"不支持关联对象种类: {object_type}")

        current_list: list[str] = getattr(target, attr)
        if object_id not in current_list:
            current_list.append(object_id)
            target.updated_at = _utc_now()
            topics = self._load()
            for idx, t in enumerate(topics):
                if t.topic_id == topic_id:
                    topics[idx] = target
                    break
            self._save(topics)
        return target

    def unlink_object(self, topic_id: str, object_type: str, object_id: str) -> Topic | None:
        target = self.get_topic(topic_id)
        if not target:
            return None

        attr_map = {
            "document": "document_ids",
            "note": "note_ids",
            "run": "run_ids",
            "project": "project_ids",
            "conversation": "conversation_ids",
        }
        attr = attr_map.get(object_type)
        if not attr:
            raise ValueError(f"不支持关联对象种类: {object_type}")

        current_list: list[str] = getattr(target, attr)
        if object_id in current_list:
            current_list.remove(object_id)
            target.updated_at = _utc_now()
            topics = self._load()
            for idx, t in enumerate(topics):
                if t.topic_id == topic_id:
                    topics[idx] = target
                    break
            self._save(topics)
        return target

    def update_recent_location(self, topic_id: str, location: dict[str, Any]) -> Topic | None:
        target = self.get_topic(topic_id)
        if not target:
            return None

        target.recent_location = {
            "section": str(location.get("section", "overview")),
            "object_id": str(location.get("object_id", "")) if location.get("object_id") else None,
            "label": str(location.get("label", "")) if location.get("label") else None,
            "timestamp": _utc_now(),
        }
        target.updated_at = _utc_now()
        topics = self._load()
        for idx, t in enumerate(topics):
            if t.topic_id == topic_id:
                topics[idx] = target
                break
        self._save(topics)
        return target
