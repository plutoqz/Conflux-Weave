"""Data structures and schemas for long-horizon survey research (Deep Research V2)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any
import uuid


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class ResearchProbe:
    """A granular, evidence-seeking inquiry facet belonging to a survey chapter."""

    probe_id: str
    target_aspect: str
    query_intent: str
    expected_data_type: str = "architectural_tradeoff"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ResearchProbe:
        return cls(
            probe_id=str(data.get("probe_id") or f"probe-{uuid.uuid4().hex[:8]}"),
            target_aspect=str(data.get("target_aspect", "")).strip(),
            query_intent=str(data.get("query_intent", "")).strip(),
            expected_data_type=str(data.get("expected_data_type", "architectural_tradeoff")).strip(),
        )


@dataclass(frozen=True)
class SurveyChapterPlan:
    """A high-level chapter in the comprehensive survey outline tree."""

    chapter_id: str
    chapter_index: int
    title: str
    core_thesis: str
    probes: tuple[ResearchProbe, ...] = field(default_factory=tuple)
    depends_on: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "chapter_id": self.chapter_id,
            "chapter_index": self.chapter_index,
            "title": self.title,
            "core_thesis": self.core_thesis,
            "probes": [p.to_dict() for p in self.probes],
            "depends_on": list(self.depends_on),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SurveyChapterPlan:
        probes_raw = data.get("probes") or []
        probes = tuple(ResearchProbe.from_dict(p) for p in probes_raw)
        depends_on = tuple(str(d) for d in (data.get("depends_on") or []))
        return cls(
            chapter_id=str(data.get("chapter_id") or f"ch-{data.get('chapter_index', 0)}"),
            chapter_index=int(data.get("chapter_index", 0)),
            title=str(data.get("title", "")).strip(),
            core_thesis=str(data.get("core_thesis", "")).strip(),
            probes=probes,
            depends_on=depends_on,
        )


@dataclass(frozen=True)
class ResearchOutlineTree:
    """The master outline tree for a multi-chapter exhaustive literature survey."""

    outline_id: str
    objective: str
    background_summary: str
    target_word_count_range: tuple[int, int]
    chapters: tuple[SurveyChapterPlan, ...]
    created_at: str = field(default_factory=_utc_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "outline_id": self.outline_id,
            "objective": self.objective,
            "background_summary": self.background_summary,
            "target_word_count_range": list(self.target_word_count_range),
            "chapters": [c.to_dict() for c in self.chapters],
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ResearchOutlineTree:
        chapters_raw = data.get("chapters") or []
        chapters = tuple(SurveyChapterPlan.from_dict(c) for c in chapters_raw)
        twc = data.get("target_word_count_range") or [8000, 15000]
        return cls(
            outline_id=str(data.get("outline_id") or f"outline-{uuid.uuid4().hex[:8]}"),
            objective=str(data.get("objective", "")).strip(),
            background_summary=str(data.get("background_summary", "")).strip(),
            target_word_count_range=(int(twc[0]), int(twc[1])),
            chapters=chapters,
            created_at=str(data.get("created_at") or _utc_now()),
        )

    def total_probes_count(self) -> int:
        return sum(len(c.probes) for c in self.chapters)

    def get_all_probes(self) -> list[ResearchProbe]:
        probes: list[ResearchProbe] = []
        for c in self.chapters:
            probes.extend(c.probes)
        return probes

    def validate_acyclic(self) -> bool:
        """Verify that chapter dependencies form a valid Directed Acyclic Graph (DAG)."""
        chapter_ids = {c.chapter_id for c in self.chapters}
        # Adjacency: dep -> chapter
        in_degree = {cid: 0 for cid in chapter_ids}
        adj: dict[str, list[str]] = {cid: [] for cid in chapter_ids}

        for c in self.chapters:
            for dep in c.depends_on:
                if dep in chapter_ids and dep != c.chapter_id:
                    adj[dep].append(c.chapter_id)
                    in_degree[c.chapter_id] += 1
                elif dep == c.chapter_id:
                    return False  # Self-loop

        # Kahn's algorithm
        queue = [cid for cid, deg in in_degree.items() if deg == 0]
        visited_count = 0
        while queue:
            curr = queue.pop(0)
            visited_count += 1
            for nxt in adj[curr]:
                in_degree[nxt] -= 1
                if in_degree[nxt] == 0:
                    queue.append(nxt)

        return visited_count == len(chapter_ids)

    def topological_chapters(self) -> list[SurveyChapterPlan]:
        """Return chapters sorted in topological dependency order (falling back to chapter_index)."""
        chapter_map = {c.chapter_id: c for c in self.chapters}
        if not self.validate_acyclic():
            # If circular, fallback to numerical index sorting
            return sorted(self.chapters, key=lambda c: c.chapter_index)

        in_degree = {c.chapter_id: 0 for c in self.chapters}
        adj: dict[str, list[str]] = {c.chapter_id: [] for c in self.chapters}
        for c in self.chapters:
            for dep in c.depends_on:
                if dep in in_degree:
                    adj[dep].append(c.chapter_id)
                    in_degree[c.chapter_id] += 1

        queue = sorted(
            [cid for cid, deg in in_degree.items() if deg == 0],
            key=lambda cid: chapter_map[cid].chapter_index,
        )
        ordered: list[SurveyChapterPlan] = []
        while queue:
            curr_id = queue.pop(0)
            ordered.append(chapter_map[curr_id])
            for nxt_id in adj[curr_id]:
                in_degree[nxt_id] -= 1
                if in_degree[nxt_id] == 0:
                    queue.append(nxt_id)
            queue.sort(key=lambda cid: chapter_map[cid].chapter_index)

        return ordered
