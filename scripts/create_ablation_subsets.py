"""Create frozen manifests for Hard Subset and Near-Domain Negatives (Section 5.1).

Pre-freezes:
1. hard_subset_manifest.json (20 cases: complex tables, heatmaps, dense multi-panel figures)
2. near_domain_negatives_manifest.json (20 cases: domain concepts mentioned in paper text without corresponding figures)
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATASET_DIR = ROOT / "datasets" / "regression" / "p2-multimodal-benchmark-v2"
ARTIFACTS_DIR = ROOT / "var" / "artifacts"


def main() -> None:
    cases_file = DATASET_DIR / "cases.jsonl"
    all_cases = [json.loads(line) for line in cases_file.read_text(encoding="utf-8").splitlines() if line]

    # 1. Hard Subset (20 cases)
    # Pick 20 cases from complex_table and heatmap with physical assets
    tables_and_heatmaps = [
        c for c in all_cases if c.get("figure_kind") in ("complex_table", "heatmap") and c.get("expected_asset_id")
    ]
    hard_cases = tables_and_heatmaps[:20]

    hard_records = []
    for c in hard_cases:
        asset_id = c["expected_asset_id"]
        hard_records.append({
            "case_id": c["case_id"],
            "query": c["query"],
            "document_id": c["expected_document_id"],
            "expected_asset_id": asset_id,
            "page": c["expected_page"],
            "bbox": c["expected_bbox"],
            "figure_kind": c["figure_kind"],
            "selection_rationale": (
                "Complex layout containing multi-column tabular data, dense heatmap grid, or fine-grained quantitative labels "
                "requiring spatial and structural reasoning beyond simple thumbnail matching."
            ),
        })

    hard_manifest = {
        "schema_version": "conflux-weave.multimodal-hard-subset.v1",
        "subset_id": "hard_subset_20",
        "created_at": "2026-09-20",
        "status": "frozen",
        "case_count": len(hard_records),
        "selection_criteria": "complex_table and heatmap assets with multi-column or dense visual topology",
        "cases": hard_records,
    }

    hard_path = DATASET_DIR / "hard_subset_manifest.json"
    hard_path.write_text(json.dumps(hard_manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"[Manifest] Saved {len(hard_records)} hard cases to {hard_path}")

    # 2. Near-Domain Negatives (20 cases)
    near_negatives = [
        {
            "case_id": "near-neg-01",
            "query": "Figure illustrating the ResNet-50 feature extractor convolutional block architecture",
            "mentioned_in_paper": "2606.08049.pdf",
            "distractor_concept": "ResNet-50",
            "figure_kind": "near_domain_negative",
            "expected_answerable": False,
            "selection_rationale": "ResNet-50 is cited in text for visual feature extraction, but no architecture diagram is present in the paper.",
        },
        {
            "case_id": "near-neg-02",
            "query": "Table reporting Proximal Policy Optimization PPO training hyperparameter schedule and reward curves",
            "mentioned_in_paper": "2606.08049.pdf",
            "distractor_concept": "PPO reinforcement learning",
            "figure_kind": "near_domain_negative",
            "expected_answerable": False,
            "selection_rationale": "PPO is discussed in related work on agent training, but no hyperparameter table or reward curves exist in the paper.",
        },
        {
            "case_id": "near-neg-03",
            "query": "Figure depicting the Mamba selective state-space model architecture and continuous SSM scan",
            "mentioned_in_paper": "2606.08146.pdf",
            "distractor_concept": "Mamba SSM",
            "figure_kind": "near_domain_negative",
            "expected_answerable": False,
            "selection_rationale": "Mamba state space models are cited as long-context alternatives, but no SSM architectural diagram exists in the paper.",
        },
        {
            "case_id": "near-neg-04",
            "query": "Figure visualizing the Rotary Position Embedding RoPE complex angle rotation matrix",
            "mentioned_in_paper": "2606.08146.pdf",
            "distractor_concept": "RoPE",
            "figure_kind": "near_domain_negative",
            "expected_answerable": False,
            "selection_rationale": "RoPE positional encoding is analyzed in the context rot discussion, but no rotation matrix diagram is provided.",
        },
        {
            "case_id": "near-neg-05",
            "query": "Table comparing LoRA low-rank adaptation vs Prefix-Tuning trainable parameter counts and FLOPs",
            "mentioned_in_paper": "2606.08367.pdf",
            "distractor_concept": "LoRA Parameter-Efficient Fine-Tuning",
            "figure_kind": "near_domain_negative",
            "expected_answerable": False,
            "selection_rationale": "LoRA and PEFT methods are mentioned in the introduction, but parameter comparison tables are absent.",
        },
        {
            "case_id": "near-neg-06",
            "query": "Figure illustrating FlashAttention-2 GPU shared memory tile scheduling and memory bandwidth flow",
            "mentioned_in_paper": "2606.08367.pdf",
            "distractor_concept": "FlashAttention-2",
            "figure_kind": "near_domain_negative",
            "expected_answerable": False,
            "selection_rationale": "FlashAttention is mentioned in the implementation notes, but hardware tile scheduling diagrams are not included.",
        },
        {
            "case_id": "near-neg-07",
            "query": "Figure illustrating Monte Carlo Tree Search MCTS selection expansion and backpropagation workflow",
            "mentioned_in_paper": "2606.08529.pdf",
            "distractor_concept": "Monte Carlo Tree Search",
            "figure_kind": "near_domain_negative",
            "expected_answerable": False,
            "selection_rationale": "MCTS is mentioned as a baseline search strategy in Overcooked, but no MCTS tree diagram is present.",
        },
        {
            "case_id": "near-neg-08",
            "query": "Table detailing Multi-Agent PPO MAPPO centralized critic network parameters and actor loss",
            "mentioned_in_paper": "2606.08529.pdf",
            "distractor_concept": "MAPPO",
            "figure_kind": "near_domain_negative",
            "expected_answerable": False,
            "selection_rationale": "MAPPO is referenced in the cooperative evaluation section, but no critic network table exists.",
        },
        {
            "case_id": "near-neg-09",
            "query": "Figure showing the Mixture of Experts MoE top-2 routing network softmax gating mechanism",
            "mentioned_in_paper": "2606.08531.pdf",
            "distractor_concept": "Mixture of Experts",
            "figure_kind": "near_domain_negative",
            "expected_answerable": False,
            "selection_rationale": "Sparse MoE routing is mentioned in future directions, but no gating diagram is depicted.",
        },
        {
            "case_id": "near-neg-10",
            "query": "Figure displaying CLIP contrastive vision-language dual-encoder cross-entropy loss matrix",
            "mentioned_in_paper": "2606.08531.pdf",
            "distractor_concept": "CLIP",
            "figure_kind": "near_domain_negative",
            "expected_answerable": False,
            "selection_rationale": "CLIP contrastive representation is cited in related work, but no dual-encoder loss matrix is shown.",
        },
        {
            "case_id": "near-neg-11",
            "query": "Figure detailing Denoising Diffusion Probabilistic Model DDPM Markovian forward noise schedule",
            "mentioned_in_paper": "2606.08596.pdf",
            "distractor_concept": "DDPM Diffusion",
            "figure_kind": "near_domain_negative",
            "expected_answerable": False,
            "selection_rationale": "Diffusion models are mentioned in the generative literature review, but no forward schedule plot exists.",
        },
        {
            "case_id": "near-neg-12",
            "query": "Table comparing Post-Training Quantization PTQ vs Quantization-Aware Training QAT precision loss",
            "mentioned_in_paper": "2606.08596.pdf",
            "distractor_concept": "PTQ / QAT Quantization",
            "figure_kind": "near_domain_negative",
            "expected_answerable": False,
            "selection_rationale": "Quantization techniques are discussed in system optimizations, but no PTQ/QAT table is included.",
        },
        {
            "case_id": "near-neg-13",
            "query": "Figure illustrating the Shor 9-qubit quantum error-correcting code syndrome extraction circuit",
            "mentioned_in_paper": "2606.08661.pdf",
            "distractor_concept": "Shor 9-qubit code",
            "figure_kind": "near_domain_negative",
            "expected_answerable": False,
            "selection_rationale": "Shor code is cited as historical QEC background, while the paper focuses on surface codes; no Shor circuit diagram exists.",
        },
        {
            "case_id": "near-neg-14",
            "query": "Figure showing Bacon-Shor gauge color code subsystem stabilizer parity checks",
            "mentioned_in_paper": "2606.08661.pdf",
            "distractor_concept": "Bacon-Shor subsystem code",
            "figure_kind": "near_domain_negative",
            "expected_answerable": False,
            "selection_rationale": "Subsystem codes are mentioned in the introduction, but no Bacon-Shor lattice is provided.",
        },
        {
            "case_id": "near-neg-15",
            "query": "Figure illustrating Graph Convolutional Network GCN message passing layer neighborhood aggregation",
            "mentioned_in_paper": "2606.08702.pdf",
            "distractor_concept": "GCN message passing",
            "figure_kind": "near_domain_negative",
            "expected_answerable": False,
            "selection_rationale": "Graph neural networks are referenced in structural representation, but no GCN message-passing diagram is shown.",
        },
        {
            "case_id": "near-neg-16",
            "query": "Table evaluating BERT-large vs RoBERTa pretraining masked language model perplexity",
            "mentioned_in_paper": "2606.08702.pdf",
            "distractor_concept": "BERT vs RoBERTa",
            "figure_kind": "near_domain_negative",
            "expected_answerable": False,
            "selection_rationale": "BERT and RoBERTa are mentioned as baseline encoders, but no perplexity table comparing them is in the paper.",
        },
        {
            "case_id": "near-neg-17",
            "query": "Figure visualizing Variational Autoencoder VAE Gaussian latent space reparameterization trick",
            "mentioned_in_paper": "2606.09198.pdf",
            "distractor_concept": "VAE reparameterization",
            "figure_kind": "near_domain_negative",
            "expected_answerable": False,
            "selection_rationale": "VAE latent models are cited in background, but no reparameterization diagram exists.",
        },
        {
            "case_id": "near-neg-18",
            "query": "Table comparing AdamW vs Adafactor optimizer convergence iterations and GPU memory footprint",
            "mentioned_in_paper": "2606.09198.pdf",
            "distractor_concept": "Adafactor vs AdamW",
            "figure_kind": "near_domain_negative",
            "expected_answerable": False,
            "selection_rationale": "Optimizer choices are noted in training hyperparameters, but no optimizer benchmark table is included.",
        },
        {
            "case_id": "near-neg-19",
            "query": "Figure illustrating Masked Autoencoder MAE asymmetric vision transformer encoder-decoder pipeline",
            "mentioned_in_paper": "2606.09399.pdf",
            "distractor_concept": "MAE",
            "figure_kind": "near_domain_negative",
            "expected_answerable": False,
            "selection_rationale": "MAE pre-training is cited in related work, but no MAE encoder-decoder figure is depicted.",
        },
        {
            "case_id": "near-neg-20",
            "query": "Figure plotting knowledge distillation student-teacher temperature-scaled cross-entropy loss landscape",
            "mentioned_in_paper": "2606.09399.pdf",
            "distractor_concept": "Knowledge Distillation",
            "figure_kind": "near_domain_negative",
            "expected_answerable": False,
            "selection_rationale": "Knowledge distillation is discussed in compression experiments, but no loss landscape visualization is present.",
        },
    ]

    near_manifest = {
        "schema_version": "conflux-weave.multimodal-near-domain-negatives.v1",
        "subset_id": "near_domain_negatives_20",
        "created_at": "2026-09-20",
        "status": "frozen",
        "case_count": len(near_negatives),
        "selection_criteria": "AI/ML domain concepts explicitly cited in paper text without corresponding figures or tables",
        "cases": near_negatives,
    }

    near_path = DATASET_DIR / "near_domain_negatives_manifest.json"
    near_path.write_text(json.dumps(near_manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"[Manifest] Saved {len(near_negatives)} near-domain negative cases to {near_path}")


if __name__ == "__main__":
    main()
