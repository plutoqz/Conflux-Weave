"""Unit tests for Parent-Child Hierarchical Chunking (Phase 3 Topic 2).

Verifies:
1. Hierarchy construction with child chunks and parent pointers.
2. Word windowing and overlap behavior.
3. Fallback for single-sentence short segments.
4. Passage resolution with parent deduplication and ranking preservation.
"""

from __future__ import annotations

from conflux_weave.hierarchical_chunking import (
    ChildChunk,
    ParentChunk,
    build_parent_child_hierarchy,
    resolve_parent_passages,
)
from conflux_weave.retrieval import RetrievalDocument


def test_build_parent_child_hierarchy():
    # Long academic passage (120 words)
    words = [f"word{i}" for i in range(120)]
    long_text = " ".join(words)

    doc1 = RetrievalDocument(
        document_id="doc1:seg0",
        text=long_text,
        source_snapshot_id="snap1",
        locator={"page": 3},
    )
    # Short paragraph (20 words)
    doc2 = RetrievalDocument(
        document_id="doc2:seg0",
        text="Short paragraph explaining the attention mechanism and softmax formulation.",
        source_snapshot_id="snap2",
        locator={"page": 5},
    )

    parents, children, lookup = build_parent_child_hierarchy(
        [doc1, doc2],
        child_chunk_words=50,
        child_overlap_words=10,
    )

    assert len(parents) == 2
    assert parents[0].document_id == "doc1:seg0"
    assert parents[0].page == 3
    assert len(parents[0].child_ids) >= 2

    # Short doc should have exactly 1 child
    assert parents[1].document_id == "doc2:seg0"
    assert len(parents[1].child_ids) == 1

    # Verify children properties
    assert len(children) >= 3
    assert all(isinstance(c, ChildChunk) for c in children)
    assert all(c.parent_id.startswith("parent::") for c in children)

    # Verify lookup mapping
    assert "child::doc1:seg0::0" in lookup
    assert lookup["child::doc1:seg0::0"] == long_text


def test_resolve_parent_passages_deduplication():
    child_map = {
        "c1_1": "Parent Context A: Detailed mathematical formulation of policy gradient theorem.",
        "c1_2": "Parent Context A: Detailed mathematical formulation of policy gradient theorem.",
        "c2_1": "Parent Context B: Hyperparameter schedule and learning rate warmup.",
        "c3_1": "Parent Context C: Evaluation on Atari benchmarks.",
    }

    # Hits containing two children from the same parent
    hits = ["c1_1", "c1_2", "c2_1", "c3_1"]
    resolved = resolve_parent_passages(hits, child_map, max_parents=2)

    # Deduplicated to 2 distinct parent contexts in order
    assert len(resolved) == 2
    assert "Parent Context A" in resolved[0]
    assert "Parent Context B" in resolved[1]
