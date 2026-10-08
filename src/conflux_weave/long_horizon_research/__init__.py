"""Long-horizon survey research package for Conflux-Weave (Deep Research V2)."""

from __future__ import annotations

from conflux_weave.long_horizon_research.outline_planner import (
    OutlinePlanner,
    build_deterministic_outline,
)
from conflux_weave.long_horizon_research.reflection_auditor import (
    ContradictionFinding,
    EvidenceGapFinding,
    InformationGapAuditor,
    ReflectionAuditReport,
)
from conflux_weave.long_horizon_research.search_graph import (
    DiscoveredSource,
    SearchGraphCrawler,
    calculate_authority_score,
    extract_potential_outbound_links,
)
from conflux_weave.long_horizon_research.spec import (
    ResearchOutlineTree,
    ResearchProbe,
    SurveyChapterPlan,
)
from conflux_weave.long_horizon_research.survey_synthesizer import (
    ComprehensiveSurveyReport,
    SurveyReportSection,
    SurveySynthesizer,
)
from conflux_weave.long_horizon_research.workflow import LongHorizonSurveyWorkflow

__all__ = [
    "ComprehensiveSurveyReport",
    "ContradictionFinding",
    "DiscoveredSource",
    "EvidenceGapFinding",
    "InformationGapAuditor",
    "LongHorizonSurveyWorkflow",
    "OutlinePlanner",
    "ReflectionAuditReport",
    "ResearchOutlineTree",
    "ResearchProbe",
    "SearchGraphCrawler",
    "SurveyChapterPlan",
    "SurveyReportSection",
    "SurveySynthesizer",
    "build_deterministic_outline",
    "calculate_authority_score",
    "extract_potential_outbound_links",
]
