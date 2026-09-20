# Multimodal Retrieval & Grounding Benchmark v2 (p2-multimodal-benchmark-v2)

Comprehensive, multi-tier benchmark evaluating multimodal RAG retrieval, visual grounding, and factual fidelity.

## Corpus Distribution (150 Cases)
- **architecture** (30 cases): System design, workflow diagrams, pipeline blueprints.
- **benchmark_plot** (35 cases): Performance plots, ablation curves, Pareto frontiers.
- **taxonomy** (20 cases): Hierarchical classifications, decision trees, risk structures.
- **heatmap** (15 cases): Attack success matrices, attention weights, spatial distributions.
- **complex_table** (20 cases): Cross-page quantitative tables, ablation matrices.
- **unanswerable_negative** (30 cases): Out-of-domain topics lacking figures, enforcing <=5% FPR.

## Target Metrics
- **Image Recall@5**: >= 0.85
- **MRR**: >= 0.65
- **BBox IoU Localization**: 1.00
- **Evidence Closure**: 1.00
- **Unanswerable FPR**: <= 0.05
- **Chart Factuality Score**: >= 0.75
- **Citation Precision**: >= 0.85
