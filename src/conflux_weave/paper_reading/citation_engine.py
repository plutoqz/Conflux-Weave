"""Zero-Key Dual Citation Graph Engine with OpenAlex, arXiv, and Local PDF Fallback."""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from conflux_weave.paper_reading.spec import (
    CitationItem,
    CitationType,
    DualCitationGraph,
)


OPENALEX_WORKS_API = "https://api.openalex.org/works"
DEFAULT_TIMEOUT_SEC = 4.0
USER_AGENT = "ConfluxWeave/0.3 (mailto:research@conflux-weave.local)"


def _safe_fetch_json(url: str, timeout: float = DEFAULT_TIMEOUT_SEC) -> dict[str, Any] | None:
    """Safe HTTP GET with strict timeout and clean error swallowing."""
    req = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urlopen(req, timeout=timeout) as resp:
            if resp.status == 200:
                return json.loads(resp.read().decode("utf-8"))
    except Exception:
        pass
    return None


def extract_references_via_regex(raw_text: str, max_items: int = 15) -> list[CitationItem]:
    """Parse bibliography entries from local text using robust academic regex patterns."""
    if not raw_text:
        return []

    # Find the References / Bibliography section or use full text
    ref_heading_match = re.search(
        r"(?:^|\n)\s*(?:references|bibliography|参考文献)[\s:：]*\n(.*)",
        raw_text,
        re.IGNORECASE | re.DOTALL,
    )
    body = ref_heading_match.group(1) if ref_heading_match else raw_text

    items: list[CitationItem] = []

    # Pattern A: Numbered citations e.g. [1] or 1.
    numbered_blocks = re.findall(
        r"(?:^|\n)\s*\[?(\d+)\]?[\.\s]+([^\n]+(?:\n(?!\s*\[?\d+\]?)[^\n]+)*)",
        body,
    )

    for num_str, block in numbered_blocks:
        if len(items) >= max_items:
            break
        cleaned_block = " ".join(block.split()).strip()
        if len(cleaned_block) < 8:
            continue

        # Extract year
        year_match = re.search(r"\b(19\d{2}|20\d{2})\b", cleaned_block)
        year = int(year_match.group(1)) if year_match else None

        # Extract title: check quotation marks first
        quote_match = re.search(r"[\"“]([^\n\"”]+)[\"”]", cleaned_block)
        if quote_match:
            clean_title = quote_match.group(1).strip()
            authors_part = cleaned_block[:quote_match.start()].strip().rstrip(",.").strip()
        else:
            # Split by period or commas
            parts = [p.strip() for p in re.split(r"[\.\?]", cleaned_block) if p.strip()]
            if len(parts) >= 2:
                authors_part = parts[0]
                clean_title = parts[1]
            else:
                authors_part = cleaned_block[:30]
                clean_title = cleaned_block[30:] or cleaned_block

        authors = tuple(a.strip() for a in re.split(r"[,&;]|and", authors_part) if a.strip())[:3]
        if not authors:
            authors = ("Academic Authors",)

        if len(clean_title) >= 4:
            items.append(
                CitationItem(
                    paper_id=f"local_ref_{num_str}",
                    title=clean_title,
                    authors=authors,
                    year=year,
                    citation_type=CitationType.LOCAL_REFERENCE,
                    summary_or_context=f"本地参考文献 [{num_str}]: {authors_part} ({year or '未知年份'})",
                )
            )

    # Pattern B: Unnumbered entries fallback
    if len(items) < 2:
        line_pattern = re.compile(
            r"([A-Z][a-zA-Z\s,\.]+?(?:et\s+al\.?|[A-Z]\.))\s+[\"“]?([A-Z][^\n\.\?]{8,120})[\"”]?\.?\s+.*?(\b(?:19|20)\d{2}\b)",
            re.MULTILINE,
        )
        for idx, m in enumerate(line_pattern.finditer(body)):
            if len(items) >= max_items:
                break
            authors_str, title_str, year_str = m.groups()
            clean_title = title_str.strip().strip('"').strip("'")
            if not any(it.title.lower() == clean_title.lower() for it in items):
                items.append(
                    CitationItem(
                        paper_id=f"local_ref_line_{idx+1}",
                        title=clean_title,
                        authors=(authors_str.strip(),),
                        year=int(year_str) if year_str else None,
                        citation_type=CitationType.LOCAL_REFERENCE,
                        summary_or_context=f"本地引用: {authors_str} ({year_str})",
                    )
                )

    return items



def query_openalex_citations(
    title: str,
    doi: str | None = None,
    max_foundations: int = 3,
    max_derivatives: int = 3,
) -> tuple[tuple[CitationItem, ...], tuple[CitationItem, ...], str | None]:
    """Query OpenAlex for backward foundations and forward derivative citations without API keys."""
    seed_work: dict[str, Any] | None = None
    openalex_id: str | None = None

    # Step 1: Find seed work by DOI or title
    if doi:
        clean_doi = doi.strip().removeprefix("https://doi.org/").removeprefix("http://doi.org/")
        seed_work = _safe_fetch_json(f"{OPENALEX_WORKS_API}/https://doi.org/{clean_doi}")

    if not seed_work and title:
        params = {"search": title.strip()[:100], "per-page": 1}
        resp = _safe_fetch_json(f"{OPENALEX_WORKS_API}?{urlencode(params)}")
        if resp and resp.get("results"):
            seed_work = resp["results"][0]

    if not seed_work:
        return (), (), None

    openalex_id = seed_work.get("id")

    foundations: list[CitationItem] = []
    derivatives: list[CitationItem] = []

    # Step 2: Backward foundations (referenced_works)
    ref_work_urls = seed_work.get("referenced_works", [])[:10]
    if ref_work_urls:
        # Fetch first few referenced works IDs
        ref_ids = "|".join(u.split("/")[-1] for u in ref_work_urls[:max_foundations])
        ref_resp = _safe_fetch_json(f"{OPENALEX_WORKS_API}?filter=openalex_id:{ref_ids}&sort=cited_by_count:desc")
        if ref_resp and ref_resp.get("results"):
            for w in ref_resp["results"][:max_foundations]:
                authors = tuple(a.get("author", {}).get("display_name", "") for a in w.get("authorships", []))[:3]
                foundations.append(
                    CitationItem(
                        paper_id=w.get("id", "").split("/")[-1],
                        title=w.get("title") or "奠基性文献",
                        authors=authors,
                        year=w.get("publication_year"),
                        venue=w.get("primary_location", {}).get("source", {}).get("display_name") if w.get("primary_location") and w.get("primary_location", {}).get("source") else None,
                        doi=w.get("doi"),
                        citation_type=CitationType.BACKWARD_FOUNDATION,
                        summary_or_context=f"本文核心理论/算法奠基工作之一（被引频次: {w.get('cited_by_count', 0)} 次）",
                        relevance_score=0.95,
                    )
                )

    # Step 3: Forward derivatives (works citing this work)
    if openalex_id:
        clean_id = openalex_id.split("/")[-1]
        cites_resp = _safe_fetch_json(
            f"{OPENALEX_WORKS_API}?filter=cites:{clean_id}&sort=cited_by_count:desc&per-page={max_derivatives}"
        )
        if cites_resp and cites_resp.get("results"):
            for w in cites_resp["results"]:
                authors = tuple(a.get("author", {}).get("display_name", "") for a in w.get("authorships", []))[:3]
                derivatives.append(
                    CitationItem(
                        paper_id=w.get("id", "").split("/")[-1],
                        title=w.get("title") or "后续衍生研究",
                        authors=authors,
                        year=w.get("publication_year"),
                        venue=w.get("primary_location", {}).get("source", {}).get("display_name") if w.get("primary_location") and w.get("primary_location", {}).get("source") else None,
                        doi=w.get("doi"),
                        citation_type=CitationType.FORWARD_DERIVATIVE,
                        summary_or_context=f"引用本文的最新/高引衍生工作（被引频次: {w.get('cited_by_count', 0)} 次）",
                        relevance_score=0.90,
                    )
                )

    return tuple(foundations), tuple(derivatives), openalex_id


def build_mermaid_citation_graph(
    seed_title: str,
    backward_foundations: tuple[CitationItem, ...] | list[CitationItem],
    forward_derivatives: tuple[CitationItem, ...] | list[CitationItem],
) -> str:
    """Render clean Mermaid graph representing bidirectional academic lineage."""
    lines = [
        "```mermaid",
        "graph LR",
        "    classDef seedStyle fill:#1e40af,stroke:#3b82f6,stroke-width:3px,color:#ffffff,font-weight:bold;",
        "    classDef fndStyle fill:#0f766e,stroke:#14b8a6,stroke-width:2px,color:#ffffff;",
        "    classDef drvStyle fill:#854d0e,stroke:#eab308,stroke-width:2px,color:#ffffff;",
        f'    SEED["【本文】{seed_title[:30]}"]:::seedStyle',
    ]

    if backward_foundations:
        lines.append('    subgraph FND["理论源流与奠基性工作 (Foundations)"]')
        for idx, item in enumerate(backward_foundations):
            fid = f"F_{idx+1}"
            year_str = f" ({item.year})" if item.year else ""
            author_str = item.authors[0] if item.authors else "Foundational Work"
            clean_t = item.title[:25].replace('"', "'")
            lines.append(f'        {fid}["{author_str}{year_str}<br/>{clean_t}"]:::fndStyle')
            lines.append(f"        {fid} -->|理论/架构启发| SEED")
        lines.append("    end")

    if forward_derivatives:
        lines.append('    subgraph DRV["后序衍生与突破 (Derivatives)"]')
        for idx, item in enumerate(forward_derivatives):
            did = f"D_{idx+1}"
            year_str = f" ({item.year})" if item.year else ""
            author_str = item.authors[0] if item.authors else "Derivative Work"
            clean_t = item.title[:25].replace('"', "'")
            lines.append(f'        {did}["{author_str}{year_str}<br/>{clean_t}"]:::drvStyle')
            lines.append(f"        SEED -->|技术演进/扩展| {did}")
        lines.append("    end")
    if not backward_foundations and not forward_derivatives:
        lines.append('    NOTE["[离线/免Key模式: 仅基于本地文献，未索引到公开引文流向]"]:::fndStyle')
        lines.append('    SEED -.-> NOTE')

    lines.append("```")
    return "\n".join(lines)


def build_dual_citation_graph(
    seed_paper_id: str,
    seed_paper_title: str,
    raw_text: str = "",
    doi: str | None = None,
    allow_network: bool = True,
) -> DualCitationGraph:
    """Build complete dual citation graph with online OpenAlex and offline regex fallback."""
    foundations: tuple[CitationItem, ...] = ()
    derivatives: tuple[CitationItem, ...] = ()

    if allow_network:
        foundations, derivatives, _ = query_openalex_citations(
            title=seed_paper_title,
            doi=doi,
        )

    # Local fallback
    local_refs = tuple(extract_references_via_regex(raw_text))

    # If network returned no foundations, convert top local references to foundations
    if not foundations and local_refs:
        converted: list[CitationItem] = []
        for ref in local_refs[:5]:
            converted.append(
                CitationItem(
                    paper_id=ref.paper_id,
                    title=ref.title,
                    authors=ref.authors,
                    year=ref.year,
                    citation_type=CitationType.BACKWARD_FOUNDATION,
                    summary_or_context=f"从本地参考文献识别的理论前置工作: {ref.title}",
                    relevance_score=0.85,
                )
            )
        foundations = tuple(converted)

    mermaid_graph = build_mermaid_citation_graph(
        seed_title=seed_paper_title,
        backward_foundations=foundations,
        forward_derivatives=derivatives,
    )

    return DualCitationGraph(
        seed_paper_title=seed_paper_title,
        seed_paper_id=seed_paper_id,
        backward_foundations=foundations,
        forward_derivatives=derivatives,
        local_references=local_refs,
        mermaid_graph=mermaid_graph,
    )
