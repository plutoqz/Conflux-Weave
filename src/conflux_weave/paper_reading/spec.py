"""Data contracts and specification models for Paper Reading V2."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class PaperParadigm(str, Enum):
    ALGORITHM_ARCHITECTURE = "algorithm_architecture"  # 算法/架构类
    SURVEY_TAXONOMY = "survey_taxonomy"              # 综述/分类学类
    BENCHMARK_DATASET = "benchmark_dataset"          # 评测/数据集类


class VisualAssetType(str, Enum):
    ARCHITECTURE_DIAGRAM = "architecture_diagram"  # 系统路线图/网络架构图
    ABLATION_CHART = "ablation_chart"              # 消融趋势图/敏感性分析图
    PERFORMANCE_TABLE = "performance_table"        # 主实验数据对比表
    GENERIC_FIGURE = "generic_figure"
    GENERIC_TABLE = "generic_table"


class CitationType(str, Enum):
    BACKWARD_FOUNDATION = "backward_foundation"    # 理论源流/奠基工作
    FORWARD_DERIVATIVE = "forward_derivative"      # 后序演进/衍生工作
    LOCAL_REFERENCE = "local_reference"            # 本地参考文献


@dataclass(frozen=True)
class ReadingChapterSpec:
    key: str
    title: str
    target_aspect: str
    expected_visual_types: tuple[VisualAssetType, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "title": self.title,
            "target_aspect": self.target_aspect,
            "expected_visual_types": [v.value for v in self.expected_visual_types],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ReadingChapterSpec:
        return cls(
            key=data["key"],
            title=data["title"],
            target_aspect=data["target_aspect"],
            expected_visual_types=tuple(
                VisualAssetType(v) for v in data.get("expected_visual_types", [])
            ),
        )


@dataclass(frozen=True)
class AdaptiveReadingOutline:
    paradigm: PaperParadigm
    confidence: float
    rationale: str
    chapters: tuple[ReadingChapterSpec, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "paradigm": self.paradigm.value,
            "confidence": round(self.confidence, 4),
            "rationale": self.rationale,
            "chapters": [c.to_dict() for c in self.chapters],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AdaptiveReadingOutline:
        return cls(
            paradigm=PaperParadigm(data["paradigm"]),
            confidence=float(data.get("confidence", 1.0)),
            rationale=data.get("rationale", ""),
            chapters=tuple(ReadingChapterSpec.from_dict(c) for c in data.get("chapters", [])),
        )


@dataclass(frozen=True)
class PhysicalVisualSlice:
    asset_id: str
    page: int
    asset_type: VisualAssetType
    caption: str
    image_rel_path: str
    bbox: tuple[float, float, float, float] | None = None
    deep_analysis: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "page": self.page,
            "asset_type": self.asset_type.value,
            "caption": self.caption,
            "image_rel_path": self.image_rel_path,
            "bbox": list(self.bbox) if self.bbox else None,
            "deep_analysis": self.deep_analysis,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PhysicalVisualSlice:
        bbox_raw = data.get("bbox")
        bbox = tuple(float(x) for x in bbox_raw) if bbox_raw else None
        return cls(
            asset_id=data["asset_id"],
            page=int(data["page"]),
            asset_type=VisualAssetType(data["asset_type"]),
            caption=data.get("caption", ""),
            image_rel_path=data.get("image_rel_path", ""),
            bbox=bbox,  # type: ignore[arg-type]
            deep_analysis=data.get("deep_analysis", ""),
        )


@dataclass(frozen=True)
class CitationItem:
    paper_id: str
    title: str
    authors: tuple[str, ...] = ()
    year: int | None = None
    venue: str | None = None
    doi: str | None = None
    arxiv_id: str | None = None
    citation_type: CitationType = CitationType.LOCAL_REFERENCE
    summary_or_context: str = ""
    relevance_score: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "paper_id": self.paper_id,
            "title": self.title,
            "authors": list(self.authors),
            "year": self.year,
            "venue": self.venue,
            "doi": self.doi,
            "arxiv_id": self.arxiv_id,
            "citation_type": self.citation_type.value,
            "summary_or_context": self.summary_or_context,
            "relevance_score": round(self.relevance_score, 4),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CitationItem:
        return cls(
            paper_id=data["paper_id"],
            title=data["title"],
            authors=tuple(data.get("authors", [])),
            year=data.get("year"),
            venue=data.get("venue"),
            doi=data.get("doi"),
            arxiv_id=data.get("arxiv_id"),
            citation_type=CitationType(data.get("citation_type", "local_reference")),
            summary_or_context=data.get("summary_or_context", ""),
            relevance_score=float(data.get("relevance_score", 1.0)),
        )


@dataclass(frozen=True)
class DualCitationGraph:
    seed_paper_title: str
    seed_paper_id: str
    backward_foundations: tuple[CitationItem, ...] = ()
    forward_derivatives: tuple[CitationItem, ...] = ()
    local_references: tuple[CitationItem, ...] = ()
    mermaid_graph: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "seed_paper_title": self.seed_paper_title,
            "seed_paper_id": self.seed_paper_id,
            "backward_foundations": [c.to_dict() for c in self.backward_foundations],
            "forward_derivatives": [c.to_dict() for c in self.forward_derivatives],
            "local_references": [c.to_dict() for c in self.local_references],
            "mermaid_graph": self.mermaid_graph,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DualCitationGraph:
        return cls(
            seed_paper_title=data["seed_paper_title"],
            seed_paper_id=data["seed_paper_id"],
            backward_foundations=tuple(CitationItem.from_dict(c) for c in data.get("backward_foundations", [])),
            forward_derivatives=tuple(CitationItem.from_dict(c) for c in data.get("forward_derivatives", [])),
            local_references=tuple(CitationItem.from_dict(c) for c in data.get("local_references", [])),
            mermaid_graph=data.get("mermaid_graph", ""),
        )


@dataclass(frozen=True)
class PaperComparisonEntity:
    paper_id: str
    title: str
    year: int | None
    paradigm: PaperParadigm
    core_problem: str
    key_architecture_mechanism: str
    compute_space_overhead: str
    main_benchmark_metrics: str
    tradeoffs_and_risks: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "paper_id": self.paper_id,
            "title": self.title,
            "year": self.year,
            "paradigm": self.paradigm.value,
            "core_problem": self.core_problem,
            "key_architecture_mechanism": self.key_architecture_mechanism,
            "compute_space_overhead": self.compute_space_overhead,
            "main_benchmark_metrics": self.main_benchmark_metrics,
            "tradeoffs_and_risks": self.tradeoffs_and_risks,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PaperComparisonEntity:
        return cls(
            paper_id=data["paper_id"],
            title=data["title"],
            year=data.get("year"),
            paradigm=PaperParadigm(data.get("paradigm", "algorithm_architecture")),
            core_problem=data.get("core_problem", ""),
            key_architecture_mechanism=data.get("key_architecture_mechanism", ""),
            compute_space_overhead=data.get("compute_space_overhead", ""),
            main_benchmark_metrics=data.get("main_benchmark_metrics", ""),
            tradeoffs_and_risks=data.get("tradeoffs_and_risks", ""),
        )


@dataclass(frozen=True)
class CrossPaperComparativeMatrix:
    entities: tuple[PaperComparisonEntity, ...]
    dimension_matrix_markdown: str
    lineage_mermaid_timeline: str
    synthesis_narrative: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "entities": [e.to_dict() for e in self.entities],
            "dimension_matrix_markdown": self.dimension_matrix_markdown,
            "lineage_mermaid_timeline": self.lineage_mermaid_timeline,
            "synthesis_narrative": self.synthesis_narrative,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CrossPaperComparativeMatrix:
        return cls(
            entities=tuple(PaperComparisonEntity.from_dict(e) for e in data.get("entities", [])),
            dimension_matrix_markdown=data.get("dimension_matrix_markdown", ""),
            lineage_mermaid_timeline=data.get("lineage_mermaid_timeline", ""),
            synthesis_narrative=data.get("synthesis_narrative", ""),
        )


@dataclass(frozen=True)
class PaperReadingReportV2:
    paper_id: str
    title: str
    paradigm: PaperParadigm
    outline: AdaptiveReadingOutline
    markdown_content: str
    visual_slices: tuple[PhysicalVisualSlice, ...]
    citation_graph: DualCitationGraph | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_utc_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "paper_id": self.paper_id,
            "title": self.title,
            "paradigm": self.paradigm.value,
            "outline": self.outline.to_dict(),
            "markdown_content": self.markdown_content,
            "visual_slices": [s.to_dict() for s in self.visual_slices],
            "citation_graph": self.citation_graph.to_dict() if self.citation_graph else None,
            "metadata": dict(self.metadata),
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PaperReadingReportV2:
        return cls(
            paper_id=data["paper_id"],
            title=data["title"],
            paradigm=PaperParadigm(data["paradigm"]),
            outline=AdaptiveReadingOutline.from_dict(data["outline"]),
            markdown_content=data.get("markdown_content", ""),
            visual_slices=tuple(PhysicalVisualSlice.from_dict(s) for s in data.get("visual_slices", [])),
            citation_graph=DualCitationGraph.from_dict(data["citation_graph"]) if data.get("citation_graph") else None,
            metadata=dict(data.get("metadata", {})),
            created_at=data.get("created_at", _utc_now()),
        )
