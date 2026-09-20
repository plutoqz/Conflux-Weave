"""Parent-Child Hierarchical Chunking Architecture for Academic Literature (Phase 3 Topic 2).

Decouples fine-grained retrieval representation (Child Chunks, ~200 tokens)
from macro-reasoning generation context (Parent Chunks, ~1000 tokens).
Preserves formula derivation lineage and inter-paragraph academic context.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Sequence

from conflux_weave.retrieval import RetrievalDocument


@dataclass(frozen=True, slots=True)
class ParentChunk:
    """Coarse-grained macro chunk preserving complete sectional/derivation context."""

    parent_id: str
    document_id: str
    page: int
    text: str
    child_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ChildChunk:
    """Fine-grained atomic chunk optimized for dense/sparse lexical retrieval."""

    child_id: str
    parent_id: str
    document_id: str
    page: int
    text: str
    ordinal: int


def build_parent_child_hierarchy(
    documents: Sequence[RetrievalDocument],
    *,
    child_chunk_words: int = 60,
    child_overlap_words: int = 15,
) -> tuple[tuple[ParentChunk, ...], tuple[ChildChunk, ...], dict[str, str]]:
    """Construct parent-child hierarchy from academic text segments.

    Returns:
        (parents, children, child_to_parent_text_map)
    """
    parents: list[ParentChunk] = []
    children: list[ChildChunk] = []
    child_to_parent_text: dict[str, str] = {}

    for doc in documents:
        page = 1
        if isinstance(doc.locator, dict):
            page = int(doc.locator.get("page", 1))

        parent_id = f"parent::{doc.document_id}"
        parent_text = doc.text.strip()

        words = re.findall(r"\S+", parent_text)
        child_ids: list[str] = []

        if len(words) <= child_chunk_words:
            # Short paragraph: single child identical to parent
            child_id = f"child::{doc.document_id}::0"
            child_ids.append(child_id)
            c = ChildChunk(
                child_id=child_id,
                parent_id=parent_id,
                document_id=doc.document_id,
                page=page,
                text=parent_text,
                ordinal=0,
            )
            children.append(c)
            child_to_parent_text[child_id] = parent_text
            child_to_parent_text[doc.document_id] = parent_text
        else:
            step = max(1, child_chunk_words - child_overlap_words)
            c_idx = 0
            for start in range(0, len(words), step):
                chunk_words = words[start : start + child_chunk_words]
                if not chunk_words:
                    break
                child_id = f"child::{doc.document_id}::{c_idx}"
                child_ids.append(child_id)
                sub_text = " ".join(chunk_words)
                c = ChildChunk(
                    child_id=child_id,
                    parent_id=parent_id,
                    document_id=doc.document_id,
                    page=page,
                    text=sub_text,
                    ordinal=c_idx,
                )
                children.append(c)
                child_to_parent_text[child_id] = parent_text
                c_idx += 1
            child_to_parent_text[doc.document_id] = parent_text

        p = ParentChunk(
            parent_id=parent_id,
            document_id=doc.document_id,
            page=page,
            text=parent_text,
            child_ids=tuple(child_ids),
        )
        parents.append(p)

    return tuple(parents), tuple(children), child_to_parent_text


def resolve_parent_passages(
    hit_ids: Sequence[str],
    child_to_parent_map: dict[str, str],
    *,
    max_parents: int = 3,
) -> list[str]:
    """Given a sequence of retrieved chunk IDs, resolve deduplicated macro parent contexts."""
    seen_parents: set[str] = set()
    passages: list[str] = []

    for hid in hit_ids:
        parent_text = child_to_parent_map.get(hid)
        if not parent_text:
            continue
        # Deduplicate parent passages
        if parent_text in seen_parents:
            continue
        seen_parents.add(parent_text)
        passages.append(parent_text)
        if len(passages) >= max_parents:
            break

    return passages
