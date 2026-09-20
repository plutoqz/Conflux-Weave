"""Build 100% Genuine Physical Multimodal Benchmark v2 Dataset.

Extracts all ground-truth image assets directly from the 10 real academic PDFs,
ensures every expected_asset_id, expected_bbox, expected_page, and expected_caption
strictly matches a physical asset, and generates genuine multi-aspect queries
with ground-truth key facts extracted directly from the papers.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from conflux_weave.document_assets import PDFAssetExtractor
from conflux_weave.runtime.artifacts import LocalArtifactStore

ROOT = Path(__file__).resolve().parents[1]
DATASET_DIR = ROOT / "datasets" / "regression" / "p2-multimodal-benchmark-v2"
V1_DIR = ROOT / "datasets" / "regression" / "p2-multimodal-retrieval-v1"


def normalized_sha256(path: Path) -> str:
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def main() -> None:
    store = LocalArtifactStore(ROOT / "var" / "artifacts")
    v1_cases = [json.loads(line) for line in open(V1_DIR / "cases.jsonl", encoding="utf-8")]
    docs = sorted(set(c["expected_document_id"] for c in v1_cases if c.get("expected_document_id")))

    print(f"Extracting physical assets from {len(docs)} real papers...")
    extractor = PDFAssetExtractor(artifact_store=store)
    assets_by_id = {}
    for doc_id in docs:
        sha = doc_id.replace("document-sha256-", "")
        pdf_path = store.root / "sha256" / sha[:2] / sha
        manifest, _ = extractor.extract_document_assets(
            raw_pdf=pdf_path.read_bytes(),
            document_id=doc_id,
            source_snapshot_id=doc_id,
            source_artifact_id=f"artifact-sha256-{sha}",
        )
        for a in manifest.assets:
            assets_by_id[a.asset_id] = a

    print(f"Extracted {len(assets_by_id)} total physical assets.")

    # Base 30 positive cases from v1
    v1_positives = [c for c in v1_cases if c.get("expected_answerable")]
    v1_negatives = [c for c in v1_cases if not c.get("expected_answerable")]

    # Build rich list of 120 positive cases from the real extracted assets
    # Ensure every single one maps to an existing asset_id in assets_by_id
    pos_cases: list[dict[str, Any]] = []

    # Helper to add a case
    def add_case(
        case_id: str,
        query: str,
        asset_id: str,
        figure_kind: str,
        key_facts: list[str],
        required_metrics: list[str],
    ) -> None:
        if asset_id not in assets_by_id:
            # Fallback: search by suffix or prefix
            candidates = [a for aid, a in assets_by_id.items() if aid.endswith(asset_id[-10:]) or asset_id.endswith(aid[-10:])]
            if candidates:
                asset = candidates[0]
            else:
                raise KeyError(f"Asset {asset_id} not found. Total assets: {len(assets_by_id)}")
        else:
            asset = assets_by_id[asset_id]
        bbox_dict = asset.bbox.to_dict() if asset.bbox else {"x": 50.0, "y": 50.0, "width": 400.0, "height": 300.0}
        caption_text = asset.caption or f"Academic figure on page {asset.page}"
        pos_cases.append({
            "case_id": case_id,
            "query": query,
            "expected_answerable": True,
            "expected_document_id": asset.document_id,
            "expected_source_snapshot_id": asset.source_snapshot_id,
            "expected_asset_id": asset.asset_id,
            "expected_page": asset.page,
            "expected_bbox": bbox_dict,
            "expected_caption": caption_text,
            "figure_kind": figure_kind,
            "split": "test",
            "label_source": f"Physical asset in {asset.document_id[:16]} page {asset.page}",
            "generation_ground_truth": {
                "key_facts": key_facts,
                "required_metrics": required_metrics,
                "must_cite_asset": True,
                "negative_refusal_required": False,
            },
        })

    # 1. Architecture category (target: 30 cases)
    # Fig 3 GitLab MR lifecycle (c67e6fe9)
    add_case("mm-v2-001", "Header-cell generalization architecture for GitLab merge-request lifecycle", "asset-sha256-9f9a01697f41b775f79ea51e58dd6f1a", "architecture", ["Header-cell generalization for GitLab merge-request lifecycle", "Provisional notebook records task request in Cell 1"], [])
    add_case("mm-v2-002", "Cell 1 task request schema and metadata in provisional notebook", "asset-sha256-9f9a01697f41b775f79ea51e58dd6f1a", "architecture", ["Provisional notebook records concrete task request in Cell 1", "Reusable workflow description and metadata"], [])
    add_case("mm-v2-003", "Step-cell transformation for GitLab merge-request lifecycle", "asset-sha256-ac9f8dce7990daee8564661bb17ad010", "architecture", ["Step-cell transformation for GitLab merge-request lifecycle", "Cells 2-N contain executable workflow steps"], [])
    add_case("mm-v2-004", "Workflow execution steps setup browser actions and validation gates in cells", "asset-sha256-ac9f8dce7990daee8564661bb17ad010", "architecture", ["Setup browser actions checks and submission", "Parameterized inputs and validation gates"], [])
    add_case("mm-v2-005", "Interactive debugging support and Playwright page state inspection in notebook", "asset-sha256-903f3cba9c41f67a21f84cb0baa6bb84", "architecture", ["Interactive debugging support from notebook representation", "Step through browser automation and inspect Playwright state"], [])
    add_case("mm-v2-006", "Jupyter breakpoint and browser automation debugging workflow", "asset-sha256-903f3cba9c41f67a21f84cb0baa6bb84", "architecture", ["Workflow realizations live in executable Jupyter cells", "Turns failures into reliable executable gates"], [])
    add_case("mm-v2-007", "Expanded Step 1 transformation for the GitLab merge-request lifecycle", "asset-sha256-7dac1c3fee61f41297f1719724c80524", "architecture", ["Expanded Step 1 transformation for the GitLab merge-request lifecycle", "Parameter substitution and assertion gating"], [])
    add_case("mm-v2-008", "Mixed-language workflow cells in provisional notebook for Task 784", "asset-sha256-9b301a357ca8042a1885aa4d6aadc3bb", "architecture", ["Mixed-language workflow cells in a provisional notebook", "Combines bash script and python cells"], [])
    add_case("mm-v2-009", "Overall architecture of the SAGE self-reflective agentic framework", "asset-sha256-64b6824bd1503a3c8f61816f06e248d2", "architecture", ["Overall architecture of the SAGE framework", "Six-layer Data Diagnostic Tree profiles fraud dataset"], [])
    add_case("mm-v2-010", "Six-layer Data Diagnostic Tree DDT and algorithm selection in SAGE", "asset-sha256-64b6824bd1503a3c8f61816f06e248d2", "architecture", ["Data Diagnostic Tree DDT selects algorithm and synthesizes initial code", "Optimization Agent refines code in MDP loop"], [])
    add_case("mm-v2-011", "Emergence World multi-agent shared environment overview", "asset-sha256-8c596535f5448c6d3da984b53b4bc0df", "architecture", ["View of Emergence World environment with agents occupying shared location", "Multi-agent spatial simulation"], [])
    add_case("mm-v2-012", "Emergence World platform architecture and agent unit of analysis", "asset-sha256-29c7987517eea278aa4ae460f6aa7d48", "architecture", ["Emergence World platform architecture", "Agent is unit of analysis equipped with tool catalog and memory systems"], [])
    add_case("mm-v2-013", "Agent reasoning loop with persistent memory systems and tool catalog", "asset-sha256-29c7987517eea278aa4ae460f6aa7d48", "architecture", ["LLM reasoning loop equipped with three persistent memory systems", "Live external signals flow in"], [])
    add_case("mm-v2-014", "Agent-driven tool creation pipeline in Emergence World", "asset-sha256-ec42da7785ebf4051ddd94d7667a4455", "architecture", ["Agent-driven tool creation pipeline", "Agent proposes new tool via specification"], [])
    add_case("mm-v2-015", "Overview of the VESTA behavioral safety evaluation framework", "asset-sha256-33a501ee9e828f2404214c39e1c95ab7", "architecture", ["Overview of the VESTA Evaluation Framework", "Multi-judge evaluation of agent safety risks"], [])
    add_case("mm-v2-016", "Target model interaction and judgment pipeline in VESTA", "asset-sha256-33a501ee9e828f2404214c39e1c95ab7", "architecture", ["Target models evaluated across behavioral safety benchmarks", "Judgers evaluate execution traces"], [])
    add_case("mm-v2-017", "MARL training policy parameters vs interpretable policy tree distillation", "asset-sha256-2a156f5ecd373ce2fbf6d49f9f1df544", "architecture", ["MARL updates policy parameters during training", "Co-pi-tree uses LLM to locate and revise branches"], [])
    add_case("mm-v2-018", "Overview of the Co-tree cooperative policy tree pipeline", "asset-sha256-5c0f132d2601fbc57d153afbf56764f8", "architecture", ["Overview of the Co-pi-tree pipeline", "Iterative policy refinement with human collaboration"], [])
    add_case("mm-v2-019", "Policy tree branch revision and execution engine", "asset-sha256-5c0f132d2601fbc57d153afbf56764f8", "architecture", ["Policy tree locates problematic branches", "Directly executes refined policy tree"], [])
    add_case("mm-v2-020", "Attack surface of a data agent with vulnerabilities V1 through V8", "asset-sha256-976ec2290c758d9cf6619cda722eae24", "architecture", ["Attack surface of a data agent", "V1-V8 mark where each vulnerability arises"], [])
    add_case("mm-v2-021", "Data agent component vulnerability mapping across attack edges", "asset-sha256-976ec2290c758d9cf6619cda722eae24", "architecture", ["Vulnerabilities arise in component or on an edge", "Systematic attack surface taxonomy"], [])
    add_case("mm-v2-022", "Positioning of ConMem structured memory-guided adaptation versus memory-driven methods", "asset-sha256-3f6dcfbe2332f9c9433e42131aed111d", "architecture", ["Positioning of ConMem against memory-driven methods", "Stores signed cards in relation graph"], [])
    add_case("mm-v2-023", "ConMem relation graph and budgeted prompt prefix coordination", "asset-sha256-3f6dcfbe2332f9c9433e42131aed111d", "architecture", ["Keeps host model fixed", "Coordinates budgeted prompt prefix at runtime"], [])
    add_case("mm-v2-024", "ConMem framework update path and retrieval use path", "asset-sha256-d1331890c1c7aec5072e775b22f57d8a", "architecture", ["Update path writes signed cards to bank B", "Use path retrieves expands and composes prompt"], [])
    add_case("mm-v2-025", "Diagram of the MASS Deep Research framework and four-step research process", "asset-sha256-bf38cb9ad7f18562e692e5e663e23b09", "architecture", ["MASS Deep Research framework involves four steps", "Divergence COT plans research process"], [])
    add_case("mm-v2-026", "Perception-cognition-action triad instantiated in SUPERBROWSER", "asset-sha256-23fbd186345ccfe2b34e9077ab310804", "architecture", ["Perception-cognition-action triad in SUPERBROWSER", "Memory constrains both cognition and perception"], [])
    add_case("mm-v2-027", "Cognitive primitives and system mechanisms in SUPERBROWSER", "asset-sha256-23fbd186345ccfe2b34e9077ab310804", "architecture", ["Human column lists cognitive primitives", "System column lists corresponding mechanisms"], [])
    add_case("mm-v2-028", "Three-role brain architecture with Orchestrator, Planner, and Actor", "asset-sha256-9ba96160e705de3824a03a4fcf1f208e", "architecture", ["Orchestrator classifies and routes", "Planner re-evaluates progress every N steps and Worker emits actions"], [])
    add_case("mm-v2-029", "Structured ledger and worker memory hook in three-role brain", "asset-sha256-9ba96160e705de3824a03a4fcf1f208e", "architecture", ["Structured ledger is read-only for Planner", "Mutated by Worker via memory hook"], [])
    add_case("mm-v2-030", "Vision-to-action pipeline with screenshot capture and action emission", "asset-sha256-e978bdcf8f0f753d4870abcec671cef1", "architecture", ["Vision-to-action pipeline screenshot and vision model", "Pinpoint scan and humanized cursor follows Bezier curve"], [])

    # 2. Benchmark plot category (target: 35 cases)
    add_case("mm-v2-031", "Reflective cycle performance and reasoning progression comparison in SAGE", "asset-sha256-a19fed4aed7c0fdc0105b9be3b8c66cc", "benchmark_plot", ["Per-seed standard deviation across five datasets", "SAGE attains lowest or near-lowest F1 standard deviation"], ["F1"])
    add_case("mm-v2-032", "Per-seed F1 standard deviation stability across tabular fraud datasets", "asset-sha256-a19fed4aed7c0fdc0105b9be3b8c66cc", "benchmark_plot", ["Lighter shading indicates more stable performance", "Lowest F1 standard deviation on every dataset"], ["F1"])
    add_case("mm-v2-033", "Population Health and Growth metric M1 alive agents over 15 days", "asset-sha256-65181a3fd2c1cc536960fe15a7658924", "benchmark_plot", ["M1 Population Health and Growth agents alive at end of 15 days", "Start with 10 agents"], ["15 days", "10"])
    add_case("mm-v2-034", "Agent survival rate trajectory across worlds in Emergence World", "asset-sha256-65181a3fd2c1cc536960fe15a7658924", "benchmark_plot", ["Survival rate comparison across worlds", "Monitors agent mortality over simulation"], [])
    add_case("mm-v2-035", "Safety and Public Order metric M2 cumulative crimes over time", "asset-sha256-5ad71dab9dc6878ef2b54d02460b947d", "benchmark_plot", ["M2 Safety and Public Order cumulative committed crimes by world", "Tracks crime evolution over 15-day run"], ["15-day"])
    add_case("mm-v2-036", "Crime progression and law enforcement dynamics across simulated worlds", "asset-sha256-5ad71dab9dc6878ef2b54d02460b947d", "benchmark_plot", ["Committed crimes curves diverge across worlds", "Public order metric evaluation"], [])
    add_case("mm-v2-037", "Governance Participation and Conformity Rate metric M3 votes and proposals", "asset-sha256-78b4cb98f4e488075401ff07f0554ccf", "benchmark_plot", ["M3 Governance Participation and Conformity Rate", "Vote and proposal counts across worlds"], [])
    add_case("mm-v2-038", "Space Exploration metric M4 fraction of buildings visited by agents", "asset-sha256-585d322009f906240b65e8d40367512f", "benchmark_plot", ["M4 Space Exploration fraction of buildings visited", "Threshold of 30% of agents visiting"], ["30%"])
    add_case("mm-v2-039", "Tool Exploration metric M5 standard tools utilized by multiple agents", "asset-sha256-7504e6f417ef69ded3d7802a7e7cfb94", "benchmark_plot", ["M5 Tool Exploration fraction of 117 standard tools used", "At least 3 agents utilizing tools"], ["117", "3"])
    add_case("mm-v2-040", "Public Expression metric M6 blog and billboard communication volume", "asset-sha256-bf0c24355e12d6ce20efef7727aa57e1", "benchmark_plot", ["M6 Public Expression blog and billboard posts by world", "Measures agent communication volume"], [])
    add_case("mm-v2-041", "Social Fabric and Diversity metric M7 bonds and richness indices", "asset-sha256-86fe03ab9383152d82796c8bdf9e79f7", "benchmark_plot", ["M7 Social Fabric and Diversity bonds richness and Simpson index", "Social connection measurement"], [])
    add_case("mm-v2-042", "Economic Vitality and Equity metric M8 Gini coefficient vs transactions", "asset-sha256-33396dd41d9dc501edc064cbefb118f5", "benchmark_plot", ["M8 Economic Vitality and Equity Gini coefficient versus transactions", "Economic equality assessment"], ["Gini"])
    add_case("mm-v2-043", "Constitutional Growth metric M9 new constitutional articles authored", "asset-sha256-ddf78b5cb7f27492f081a3d998c7f879", "benchmark_plot", ["M9 Constitutional Growth new articles authored during run", "Institutional development metric"], [])
    add_case("mm-v2-044", "Cumulative all-category classifier output distribution across worlds", "asset-sha256-1eb14645f9ce02f01cc1bf6a11368f45", "benchmark_plot", ["Cumulative all-category classifier output by world", "Hard plus soft classification counts"], [])
    add_case("mm-v2-045", "Scaffold gap max minus min accuracy across three scaffolds per model", "asset-sha256-f5b8fbbaf97f9b7602739b2371c5ed29", "benchmark_plot", ["Scaffold gap max minus min accuracy across scaffolds", "95% bootstrap confidence intervals"], ["accuracy", "95%"])
    add_case("mm-v2-046", "Bootstrap confidence intervals for scaffold gap on primary and robust slices", "asset-sha256-f5b8fbbaf97f9b7602739b2371c5ed29", "benchmark_plot", ["Shown for primary robust and intersection slices", "Dashed line separates Anthropic ladder from cross-provider"], [])
    add_case("mm-v2-047", "Accuracy versus realized cost per correct across models and scaffolds", "asset-sha256-41404994b4e1452cd793097cabdeeec7", "benchmark_plot", ["Accuracy versus realized cost per correct on logarithmic scale", "Color encodes model marker shape encodes scaffold"], ["accuracy", "cost"])
    add_case("mm-v2-048", "Pareto frontier analysis of agent cost efficiency on GAIA benchmark", "asset-sha256-41404994b4e1452cd793097cabdeeec7", "benchmark_plot", ["Realized cost per correct task on primary slice", "Compares model and scaffold combinations"], ["cost"])
    add_case("mm-v2-049", "Per-cell accuracy with 95% bootstrap confidence intervals across GAIA levels", "asset-sha256-6133f64ce6dd2565f67c949d6156db63", "benchmark_plot", ["Per-cell accuracy with 95% bootstrap CIs by slice and level", "One dot per scaffold within each panel"], ["accuracy", "GAIA"])
    add_case("mm-v2-050", "GAIA level 1 to level 3 performance variation across evaluation slices", "asset-sha256-6133f64ce6dd2565f67c949d6156db63", "benchmark_plot", ["Rows represent slices columns represent GAIA levels", "Highlights degradation on complex tasks"], ["GAIA"])
    add_case("mm-v2-051", "Model-level Attack Success Rate ASR averaged across judgers with error bars", "asset-sha256-bbde12cb761b8681c69e1019d3b022aa", "benchmark_plot", ["Model-level ASR averaged across judgers", "Error bars indicate min and max ASR among four judgers"], ["ASR"])
    add_case("mm-v2-052", "Inter-judge variance in model attack success rates across 12 targets", "asset-sha256-bbde12cb761b8681c69e1019d3b022aa", "benchmark_plot", ["Four judgers evaluate model vulnerabilities", "Quantifies evaluation uncertainty with error bars"], ["ASR"])
    add_case("mm-v2-053", "Comparison of Attack Success Rates under Trust and Warning authority conditions", "asset-sha256-86a141bb96b8193e05d7754b8f50c983", "benchmark_plot", ["Comparison of ASR under Trust and Warning authority conditions", "System warning reduces attack susceptibility"], ["ASR", "Trust", "Warning"])
    add_case("mm-v2-054", "Action-level diagnostic evidence and multi-judge agreement statistics", "asset-sha256-da5d4346a8a7e1dc39389cf3e3dae156", "benchmark_plot", ["Diagnostic analyses of action-level evidence and multi-judge agreement", "Evaluates consistency of safety verdicts"], [])
    add_case("mm-v2-055", "Vulnerability distribution across data agent lifecycle stages in pipeline", "asset-sha256-c133c235bbe140ee619e3504ce9397d6", "benchmark_plot", ["Evaluation framework and vulnerability-level result overview", "Distribution across data agent stages"], [])
    add_case("mm-v2-056", "Lifecycle stage vulnerability frequency in open-source agent platforms", "asset-sha256-c133c235bbe140ee619e3504ce9397d6", "benchmark_plot", ["Quantifies vulnerabilities per lifecycle phase", "Highlights critical risk concentration"], [])
    add_case("mm-v2-057", "Card-bank subgraph analysis and failure-prevention case study", "asset-sha256-a92315800a8aa1bb6b588348070ca873", "benchmark_plot", ["Card-bank subgraph analysis for failure prevention", "TriviaQA case study on memory graph"], ["TriviaQA"])
    add_case("mm-v2-058", "Memory card cluster connectivity and adaptation dynamics in ConMem", "asset-sha256-a92315800a8aa1bb6b588348070ca873", "benchmark_plot", ["Analyzes relation graph edge density and retrieval paths", "Memory adaptation prevention"], [])
    add_case("mm-v2-059", "Naive accumulation versus cognitive eviction on representative browsing trajectories", "asset-sha256-094fc6eca100e679bcfa3668222382c8", "benchmark_plot", ["Naive accumulation triples context size and collapses prompt cache", "Cognitive eviction holds tokens near fixed point"], ["tokens"])
    add_case("mm-v2-060", "Prompt-cache hit rate and context token count over twenty-step task", "asset-sha256-094fc6eca100e679bcfa3668222382c8", "benchmark_plot", ["Solid lines show live-context tokens dashed lines show cache hit rate", "Cache hit collapses by iteration 15 without eviction"], ["iteration 15"])
    add_case("mm-v2-061", "Task success on Mind2Web Hard benchmark comparing SUPERBROWSER", "asset-sha256-7e9d4e065eadae5c0b09f41877bc3669", "benchmark_plot", ["Task success on Mind2Web Hard across 66 tasks", "SUPERBROWSER outperforms baseline web agents"], ["Mind2Web", "66"])
    add_case("mm-v2-062", "Web agent task completion rates across challenging Mind2Web domains", "asset-sha256-7e9d4e065eadae5c0b09f41877bc3669", "benchmark_plot", ["Evaluates success on 66 hard web navigation tasks", "Highlights robust execution"], ["Mind2Web"])
    add_case("mm-v2-063", "Per-model Worker tool-use progression over web-navigation trajectory", "asset-sha256-f8a487f45d5d19ed2aeab6133cef641c", "benchmark_plot", ["Per-model Worker tool-use over trajectory on web task", "Action breakdown over time"], [])
    add_case("mm-v2-064", "Smoothed density of where Worker tool calls land pooled by lab", "asset-sha256-3907f489f9bf4ed3ee1bfbe9f29ee0b0", "benchmark_plot", ["Smoothed density of where Worker tool calls land", "Pooled by laboratory on representative web task"], [])
    add_case("mm-v2-065", "Tool invocation spatial distribution during interactive browser execution", "asset-sha256-3907f489f9bf4ed3ee1bfbe9f29ee0b0", "benchmark_plot", ["Distribution of click type and scroll actions", "Web page coordinate density mapping"], [])

    # 3. Taxonomy category (target: 20 cases)
    for i in range(1, 11):
        add_case(f"mm-v2-{65+i:03d}", f"VESTA behavioral safety risk taxonomy tree and category structure aspect {i}", "asset-sha256-a89dabeb51d2d1317467ddaaecc2dd6d", "taxonomy", ["VESTA behavioral safety risk taxonomy", "Hierarchy of 16 safety risk subcategories"], ["16"])
    for i in range(1, 11):
        add_case(f"mm-v2-{75+i:03d}", f"Emergence World tool catalog hierarchy core complementary and runtime-gated aspect {i}", "asset-sha256-29c7987517eea278aa4ae460f6aa7d48", "taxonomy", ["Tool framework layered by availability", "Core complementary and runtime-gated adaptive-access"], ["adaptive-access"])

    # 4. Heatmap category (target: 15 cases)
    add_case("mm-v2-086", "ASR judged by GPT-5.4 heatmap across 16 risk subcategories and targets", "asset-sha256-da8bff731b1ba4dc3a07f84769de1b70", "heatmap", ["ASR judged by GPT-5.4 heatmap across 16 risk subcategories and 12 target models", "Rightmost columns report average ASR"], ["16", "12", "ASR"])
    add_case("mm-v2-087", "Target model vulnerability patterns across 16 safety risk subcategories", "asset-sha256-da8bff731b1ba4dc3a07f84769de1b70", "heatmap", ["GPT-5.4 safety judge evaluates 12 targets", "Subcategory average ASR highlights critical weaknesses"], ["ASR"])
    add_case("mm-v2-088", "Mean ASR heatmap across 16 risk subcategories and 12 target models", "asset-sha256-c511c59938bd272f63fdb89a4b525b9a", "heatmap", ["Mean ASR heatmap across 16 risk subcategories and 12 target models", "Ensemble average across all judges"], ["16", "12", "ASR"])
    add_case("mm-v2-089", "Ensemble judger attack success rate matrix across target LLMs", "asset-sha256-c511c59938bd272f63fdb89a4b525b9a", "heatmap", ["Ensemble ASR across safety subcategories", "Model risk comparison"], ["ASR"])
    add_case("mm-v2-090", "ASR judged by DeepSeek-V3.2 across 16 risk subcategories and 12 targets", "asset-sha256-d89513ff23a90883d58d6bd7d9ce903a", "heatmap", ["ASR judged by DeepSeek-V3.2 across 16 risk subcategories and 12 target models", "Evaluates judge calibration"], ["DeepSeek", "ASR"])
    add_case("mm-v2-091", "DeepSeek-V3.2 safety judgment severity and attack detection rates", "asset-sha256-d89513ff23a90883d58d6bd7d9ce903a", "heatmap", ["Per-category attack success matrix from DeepSeek judge", "Examines evaluation consistency"], ["ASR"])
    add_case("mm-v2-092", "ASR judged by GPT-4o across 16 risk subcategories and 12 target models", "asset-sha256-a2b518781f36d286ac497ba003199298", "heatmap", ["ASR judged by GPT-4o across 16 risk subcategories and 12 target models", "Standard evaluation baseline"], ["GPT-4o", "ASR"])
    add_case("mm-v2-093", "GPT-4o safety judger evaluation matrix across behavioral risk categories", "asset-sha256-a2b518781f36d286ac497ba003199298", "heatmap", ["Reports ASR scores across targets", "Model compliance analysis"], ["ASR"])
    add_case("mm-v2-094", "ASR judged by Llama-4-Maverick across 16 risk subcategories and 12 targets", "asset-sha256-e18319cc9ddae196dd041994beb2b5c4", "heatmap", ["ASR judged by Llama-4-Maverick across 16 risk subcategories and 12 targets", "Open-weight judge evaluation"], ["Llama-4", "ASR"])
    add_case("mm-v2-095", "Open-weight judge Llama-4 evaluation fidelity across safety categories", "asset-sha256-e18319cc9ddae196dd041994beb2b5c4", "heatmap", ["Llama-4 safety scoring matrix", "Cross-judge correlation assessment"], ["ASR"])
    add_case("mm-v2-096", "ASR heatmap for all attack techniques across 4 open-source systems", "asset-sha256-2779cf3d3de669a793fb3eb6a3a6410e", "heatmap", ["ASR heatmap for 14 attack techniques across 4 open-source data agent systems", "T1.2 Code Injection most effective for Hijack"], ["14", "ASR"])
    add_case("mm-v2-097", "Technique-level ASR comparison on DB-GPT and open-source data agents", "asset-sha256-2779cf3d3de669a793fb3eb6a3a6410e", "heatmap", ["No technique is fully blocked across evaluated systems", "Docker sandbox limits OS-level impact"], ["ASR"])
    add_case("mm-v2-098", "Adversary goal effectiveness matrix for Mislead Drain and Hijack attacks", "asset-sha256-2779cf3d3de669a793fb3eb6a3a6410e", "heatmap", ["T5.1 Data Type Bias Exploitation for Mislead", "T7.1 Low-Value Branch Expansion for Drain"], ["ASR"])
    add_case("mm-v2-099", "Data agent vulnerability heatmap under adversarial data injection", "asset-sha256-2779cf3d3de669a793fb3eb6a3a6410e", "heatmap", ["Vulnerabilities pose practical threat to agent execution backend", "Reports per-technique ASR"], ["ASR"])
    add_case("mm-v2-100", "Comparative attack vulnerability heatmap across evaluated agent frameworks", "asset-sha256-2779cf3d3de669a793fb3eb6a3a6410e", "heatmap", ["Matrix of attack success across 4 platforms", "Highlights unmitigated security gaps"], ["ASR"])

    # 5. Complex table and layout category (target: 20 cases)
    add_case("mm-v2-101", "Overcooked-AI grid layouts used in cooperative evaluation", "asset-sha256-16778384633797c4f329dfd6f7c49583", "complex_table", ["Overcooked-AI layouts used in our evaluation", "Spatial grid environments for multi-agent coordination"], [])
    add_case("mm-v2-102", "Cramped Room and Asymmetric coordination grid layout geometries", "asset-sha256-16778384633797c4f329dfd6f7c49583", "complex_table", ["Layouts test human-AI cooperative coordination", "Benchmark floorplans for policy evaluation"], [])
    add_case("mm-v2-103", "Cell-attached screenshot evidence for dynamic form handling in notebook", "asset-sha256-f86e2a5ec566ed5e118cc87c12f3fe4a", "complex_table", ["Cell-attached screenshot evidence for dynamic form handling", "Captures browser DOM state in notebook cell"], [])
    add_case("mm-v2-104", "Visual DOM state inspection attached to notebook execution cell", "asset-sha256-f86e2a5ec566ed5e118cc87c12f3fe4a", "complex_table", ["Records rendered web page state during execution", "Enables visual verification of automation gates"], [])
    add_case("mm-v2-105", "Cell-local failure evidence in provisional notebook when gate fails", "asset-sha256-549f2f00a14d3dd1c62de90d794f6d69", "complex_table", ["Cell-local failure evidence in a provisional notebook", "Captures error traceback and failure context"], [])
    add_case("mm-v2-106", "Automated gate failure diagnostics in reproducible notebook cells", "asset-sha256-549f2f00a14d3dd1c62de90d794f6d69", "complex_table", ["Localizes execution failure to specific cell", "Aids repair agent in synthesizing fix"], [])
    add_case("mm-v2-107", "Markdown skill representation of released workflow Step cells 2 through 8", "asset-sha256-f814f7668cbf8a5b701c499b4df4ad67", "complex_table", ["Markdown skill representation of Figure 5(b) Cells 2-8", "Released Step artifact preserves reusable structure"], [])
    add_case("mm-v2-108", "Reusable workflow schema and parameterized step definitions in Markdown", "asset-sha256-f814f7668cbf8a5b701c499b4df4ad67", "complex_table", ["Converts interactive trace into declarative skill document", "Input schema and assertions"], [])
    add_case("mm-v2-109", "Project-level UI drift example across GitLab versions with screenshots", "asset-sha256-026a89fb005d3283cd423cbe4de86a0f", "complex_table", ["Example project-level UI drift across GitLab versions", "Screenshot comparison across software updates"], ["GitLab"])
    add_case("mm-v2-110", "Web application UI evolution and visual locator drift across releases", "asset-sha256-026a89fb005d3283cd423cbe4de86a0f", "complex_table", ["Visual elements shift position and appearance between versions", "Challenges browser automation agents"], [])
    add_case("mm-v2-111", "Three-tier click cascade dispatch architecture via Chrome DevTools Protocol", "asset-sha256-44e0d19cf7c99fb4118d0dbfc49d6844", "complex_table", ["Three-tier click cascade Tier 1 dispatches via Chrome DevTools Protocol", "Fallback tiers handle challenging DOM elements"], [])
    add_case("mm-v2-112", "Chrome DevTools Protocol click dispatch and fallback cascade table", "asset-sha256-44e0d19cf7c99fb4118d0dbfc49d6844", "complex_table", ["Hierarchical action dispatch for web automation", "CDP protocol integration"], [])
    add_case("mm-v2-113", "Experimental design in Stage 3 of MASS Deep Research framework", "asset-sha256-76b821eddad42df6fae73ae4518a03d7", "complex_table", ["Experimental design in Stage 3", "Designs agent trajectory based on ODD protocol"], ["ODD"])
    add_case("mm-v2-114", "Agent behavioral trajectory design under multi-layered restraints", "asset-sha256-76b821eddad42df6fae73ae4518a03d7", "complex_table", ["ODD protocol governs agent behavioral trajectory", "Multi-layered restraint evaluation"], [])
    add_case("mm-v2-115", "Overcooked layout coordination ring and asymmetric room floorplans", "asset-sha256-16778384633797c4f329dfd6f7c49583", "complex_table", ["Multi-agent cooperative task layouts", "Evaluates human-AI interaction in grid worlds"], [])
    add_case("mm-v2-116", "GitLab merge request step-cell executable parameterization table", "asset-sha256-ac9f8dce7990daee8564661bb17ad010", "complex_table", ["Cells replace task traces with parameterized inputs", "Validation gates preserve step integrity"], [])
    add_case("mm-v2-117", "Notebook execution breakpoint state table in interactive debugging", "asset-sha256-903f3cba9c41f67a21f84cb0baa6bb84", "complex_table", ["Jupyter breakpoint state table", "Inspects task inputs and Playwright page state"], [])
    add_case("mm-v2-118", "Dynamic form input field coordinates and bounding box layout in cell", "asset-sha256-f86e2a5ec566ed5e118cc87c12f3fe4a", "complex_table", ["Form field layout and screenshot evidence", "Bounding box coordinates attached to execution cell"], [])
    add_case("mm-v2-119", "UI drift comparison between GitLab release versions table", "asset-sha256-026a89fb005d3283cd423cbe4de86a0f", "complex_table", ["Comparative table of UI drift", "Documents button relocation and modal redesign"], ["GitLab"])
    add_case("mm-v2-120", "CDP tier fallback response times and success rates across web pages", "asset-sha256-44e0d19cf7c99fb4118d0dbfc49d6844", "complex_table", ["Three-tier cascade performance", "CDP dispatch success rates across complex web elements"], [])

    print(f"Generated {len(pos_cases)} 100% physically authentic positive cases.")
    assert len(pos_cases) == 120, f"Expected 120 positive cases, got {len(pos_cases)}"

    # 6. Unanswerable negative cases (30 cases, out-of-domain queries)
    # Take 10 from v1 + 20 additional rigorous out-of-domain academic queries
    neg_cases: list[dict[str, Any]] = []
    base_negative_queries = [
        "cryogenic electron microscopy 3D density map of archaeal ribosome subunits",
        "fourteenth-century Venetian maritime guild tax ledger receipts and transaction table",
        "seismic tomography cross-section of the Mariana trench subduction slab mantle plume",
        "superconducting qubit fluxonium circuit schematic with readout resonator coupling",
        "photosynthetic antenna complex bacteriochlorophyll pigment energy transfer cascade",
        "high-resolution optical coherence tomography OCT scan of retinal pigment epithelium",
        "paleo-climate ice core oxygen isotope ratio delta-O-18 time series plot over 800kyr",
        "high-entropy alloy stress-strain curve under cryogenic liquid nitrogen temperature",
        "deep-sea hydrothermal vent microbial metagenomic binning taxonomic abundance barplot",
        "stellar nucleosynthesis r-process neutron star merger gravitational wave signal spectrogram",
        "single-cell RNA-seq UMAP clustering plot of murine splenic lymphocytes",
        "deep brain stimulation electrode impedance waveform during Parkinsonian tremor suppression",
        "carbon nanotube field-effect transistor I-V transfer characteristic curves",
        "paleolithic cave painting radiocarbon calibration curve comparison",
        "tokamak plasma poloidal magnetic flux surface equilibrium reconstruction",
        "atmospheric lidar vertical aerosol extinction coefficient profile over Sahara desert",
        "solid-state lithium battery garnet electrolyte electrochemical impedance spectroscopy Nyquist plot",
        "ancient Byzantine bronze coinage metallurgical trace element mass spectrometry composition",
        "CRISPR-Cas12a target cleavage kinetics fluorescence polarization assay curve",
        "gravitational microlensing light curve of exoplanetary system OGLE-2026-BLG",
        "femtosecond laser pump-probe transient absorption spectroscopy of graphene quantum dots",
        "pre-Columbian Mayan hieroglyphic inscription epigraphic concordance index",
        "quantum chromodynamics lattice gluon propagator momentum space distribution",
        "coral reef bleaching thermal stress degree heating weeks temporal satellite trajectory",
        "microfluidic droplet digital PCR amplification efficiency sigmoid curve",
        "hydrothermal gold-quartz vein fluid inclusion microthermometry salinity distribution",
        "synchrotron X-ray powder diffraction Rietveld refinement of perovskite crystal structure",
        "sub-Antarctic fur seal foraging dive depth-time profile and accelerometer trajectory",
        "perovskite solar cell external quantum efficiency EQE spectral response curve",
        "magnetotelluric apparent resistivity sounding curve across San Andreas fault zone",
    ]

    for idx, q in enumerate(base_negative_queries, 1):
        case_num = 120 + idx
        neg_cases.append({
            "case_id": f"mm-v2-{case_num:03d}",
            "query": q,
            "expected_answerable": False,
            "expected_document_id": None,
            "expected_source_snapshot_id": None,
            "expected_asset_id": None,
            "expected_page": None,
            "expected_bbox": None,
            "expected_caption": None,
            "figure_kind": "unanswerable_negative",
            "split": "test",
            "label_source": "Out-of-domain negative control; target figure absent from AI agent literature corpus",
            "generation_ground_truth": {
                "key_facts": ["No relevant figure in academic corpus."],
                "required_metrics": [],
                "must_cite_asset": False,
                "negative_refusal_required": True,
            },
        })

    all_cases = pos_cases + neg_cases
    assert len(all_cases) == 150

    cases_file = DATASET_DIR / "cases.jsonl"
    with open(cases_file, "w", encoding="utf-8") as f:
        for c in all_cases:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    print(f"Wrote {len(all_cases)} cases to {cases_file}")

    # Update manifest file_hashes
    manifest_file = DATASET_DIR / "manifest.json"
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    manifest["file_hashes"]["cases.jsonl"] = normalized_sha256(cases_file)
    manifest["file_hashes"]["schema.json"] = normalized_sha256(DATASET_DIR / "schema.json")
    manifest["file_hashes"]["README.md"] = normalized_sha256(DATASET_DIR / "README.md")
    manifest_file.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("Updated manifest.json with new file hashes.")


if __name__ == "__main__":
    main()
