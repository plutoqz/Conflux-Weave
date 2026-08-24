"""Shared contracts and helpers for durable paper discovery."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from uuid import uuid4

from conflux_weave.paper_discovery import ArxivPaper


DURABLE_WORKFLOW_VERSION = "fixed-arxiv-paper-discovery-durable-v1"
STEP_KINDS = (
    "search_arxiv",
    "rank_candidates",
    "synthesize_claims",
    "validate_delivery",
    "publish_delivery",
)
SEARCH_CHECKPOINT = "conflux-weave.w3.search-checkpoint.v1"
RANK_CHECKPOINT = "conflux-weave.w3.rank-checkpoint.v1"
SYNTHESIS_CHECKPOINT = "conflux-weave.w3.synthesis-checkpoint.v1"
VALIDATION_CHECKPOINT = "conflux-weave.w3.validation-checkpoint.v1"
FINAL_LIMITATION = (
    "仅检索 arXiv 元数据和摘要；未核验正式发表版本、全文实验或跨数据库召回。"
)
UNMET_CRITERION = "尚未覆盖出版社、Crossref、会议页面和全文实验核验。"


@dataclass(frozen=True, slots=True)
class DurableWorkResult:
    run_id: str
    step_kind: str | None
    status: str


def _paper_from_json(value: dict) -> ArxivPaper:
    return ArxivPaper(
        arxiv_id=str(value["arxiv_id"]),
        title=str(value["title"]),
        summary=str(value["summary"]),
        authors=tuple(value["authors"]),
        published=str(value["published"]),
        updated=str(value["updated"]),
        entry_url=str(value["entry_url"]),
        pdf_url=value["pdf_url"],
        categories=tuple(value["categories"]),
    )


def _idempotency_key(frozen_input: dict) -> str:
    payload = json.dumps(
        frozen_input,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "paper-discovery-durable:" + hashlib.sha256(payload).hexdigest()


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid4()}"
