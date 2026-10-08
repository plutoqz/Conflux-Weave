"""Recursive multi-source exploration graph and deep link crawling engine."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Callable, Coroutine
from urllib.parse import urlparse
import uuid

logger = logging.getLogger(__name__)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


# High authority domains for scholarly and deep technical research
HIGH_AUTHORITY_DOMAINS = {
    "arxiv.org": 0.95,
    "openalex.org": 0.92,
    "doi.org": 0.92,
    "acm.org": 0.90,
    "ieee.org": 0.90,
    "nature.com": 0.95,
    "science.org": 0.95,
    "github.com": 0.85,
    "huggingface.co": 0.88,
    "semanticscholar.org": 0.90,
    "openreview.net": 0.90,
    "paperswithcode.com": 0.88,
    "openai.com": 0.85,
    "anthropic.com": 0.85,
    "deepmind.google": 0.88,
    "research.google": 0.88,
}

EXCLUDED_DOMAINS = {
    "twitter.com",
    "x.com",
    "facebook.com",
    "instagram.com",
    "tiktok.com",
    "weibo.com",
    "zhihu.com",
    "youtube.com",
    "bilibili.com",
    "reddit.com",
}


@dataclass(frozen=True)
class DiscoveredSource:
    """An ingested scholarly or technical document inside the exploration graph."""

    source_id: str
    url: str
    title: str
    snippet: str
    full_text: str
    authority_score: float
    hop_depth: int
    parent_url: str | None = None
    extracted_outbound_links: tuple[str, ...] = field(default_factory=tuple)
    source_channel: str = "web"  # "arxiv" | "openalex" | "web" | "local"
    created_at: str = field(default_factory=_utc_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "url": self.url,
            "title": self.title,
            "snippet": self.snippet,
            "full_text": self.full_text[:3000],  # truncated in serializing summary
            "authority_score": self.authority_score,
            "hop_depth": self.hop_depth,
            "parent_url": self.parent_url,
            "extracted_outbound_links": list(self.extracted_outbound_links),
            "source_channel": self.source_channel,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DiscoveredSource:
        return cls(
            source_id=str(data.get("source_id") or f"src-{uuid.uuid4().hex[:8]}"),
            url=str(data.get("url", "")).strip(),
            title=str(data.get("title", "")).strip(),
            snippet=str(data.get("snippet", "")).strip(),
            full_text=str(data.get("full_text", "")).strip(),
            authority_score=float(data.get("authority_score", 0.5)),
            hop_depth=int(data.get("hop_depth", 0)),
            parent_url=data.get("parent_url"),
            extracted_outbound_links=tuple(data.get("extracted_outbound_links") or ()),
            source_channel=str(data.get("source_channel", "web")),
            created_at=str(data.get("created_at") or _utc_now()),
        )


def calculate_authority_score(url: str, title: str, content: str) -> float:
    """Compute a baseline authority score based on domain credibility and technical content depth."""
    score = 0.50
    domain = ""
    try:
        parsed = urlparse(url)
        domain = parsed.netloc.lower()
        if domain.startswith("www."):
            domain = domain[4:]
    except Exception:
        pass

    # Domain bonus
    for auth_domain, weight in HIGH_AUTHORITY_DOMAINS.items():
        if domain == auth_domain or domain.endswith("." + auth_domain):
            score = max(score, weight)
            break

    if domain.endswith(".edu") or domain.endswith(".gov") or domain.endswith(".org"):
        score = max(score, 0.80)

    # Content quality indicators
    text_len = len(content)
    if text_len >= 2000:
        score += 0.05
    if text_len >= 8000:
        score += 0.05

    # Scholarly markers in text
    scholarly_terms = ["abstract", "methodology", "benchmark", "evaluation", "ablation", "theorem", "references"]
    term_matches = sum(1 for term in scholarly_terms if re.search(r"\b" + term + r"\b", content, re.IGNORECASE))
    score += min(0.10, term_matches * 0.02)

    return min(1.0, max(0.1, round(score, 3)))


def extract_potential_outbound_links(text: str) -> list[str]:
    """Extract valid scholarly and documentation links from text or markdown."""
    pattern = re.compile(r'https?://[^\s<>"\')\]}]+')
    raw_urls = pattern.findall(text)
    clean_urls: list[str] = []
    seen: set[str] = set()

    for u in raw_urls:
        u_clean = u.rstrip(".,;!?)]}")
        try:
            parsed = urlparse(u_clean)
            domain = parsed.netloc.lower()
            if domain in EXCLUDED_DOMAINS or any(domain.endswith("." + ed) for ed in EXCLUDED_DOMAINS):
                continue
            if parsed.scheme in ("http", "https") and len(parsed.path) > 1:
                # Prioritize docs/papers/github/arxivid
                if u_clean not in seen:
                    seen.add(u_clean)
                    clean_urls.append(u_clean)
        except Exception:
            continue

    return clean_urls[:10]  # bounded fan-out per page


class SearchGraphCrawler:
    """Asynchronous multi-source crawler with recursive link and citation traversal."""

    def __init__(
        self,
        *,
        max_sources: int = 80,
        max_depth: int = 2,
        concurrency: int = 5,
        fetcher: Callable[[str], Coroutine[Any, Any, tuple[str, str]]] | None = None,
        search_engine: Callable[[str, int], Coroutine[Any, Any, list[dict[str, str]]]] | None = None,
    ) -> None:
        self.max_sources = max_sources
        self.max_depth = max_depth
        self.concurrency = concurrency
        self.fetcher = fetcher or self._default_fetcher
        self.search_engine = search_engine or self._default_search_engine
        self.visited_urls: set[str] = set()
        self.collected_sources: list[DiscoveredSource] = []

    async def _default_fetcher(self, url: str) -> tuple[str, str]:
        """Default stub fetcher for offline or fallback execution."""
        title = f"Document from {urlparse(url).netloc}"
        content = f"Simulated authoritative content from {url} exploring technical foundations and benchmarks."
        return title, content

    async def _default_search_engine(self, query: str, limit: int = 5) -> list[dict[str, str]]:
        """Default stub search engine for offline or fallback execution."""
        q_hash = hashlib.md5(query.encode("utf-8")).hexdigest()[:6]
        return [
            {
                "url": f"https://arxiv.org/abs/260{i}.{q_hash}",
                "title": f"Research Paper on {query} - Variant {i}",
                "snippet": f"This study explores {query} with extensive ablation benchmarks and architectural comparisons.",
            }
            for i in range(1, min(limit + 1, 6))
        ]

    async def explore_probes(
        self,
        probes: tuple[Any, ...],
        *,
        progress_callback: Callable[[int, int, str], None] | None = None,
    ) -> list[DiscoveredSource]:
        """Execute breadth search for probes and recursively explore high-authority outbound links."""
        semaphore = asyncio.Semaphore(self.concurrency)
        total_probes = len(probes)

        # Step 1: Hop 0 - Direct probe queries
        query_tasks = []
        for idx, probe in enumerate(probes):
            q = getattr(probe, "query_intent", str(probe))
            query_tasks.append(self._search_and_queue(q, semaphore, idx, total_probes, progress_callback))

        await asyncio.gather(*query_tasks)

        # Step 2: Hop 1 & Hop 2 - Recursive outbound links traversal
        current_depth = 1
        while current_depth <= self.max_depth and len(self.collected_sources) < self.max_sources:
            # Pick collected sources from previous depth with high authority
            candidates: list[str] = []
            for src in list(self.collected_sources):
                if src.hop_depth == current_depth - 1 and src.authority_score >= 0.70:
                    for link in src.extracted_outbound_links:
                        if link not in self.visited_urls:
                            candidates.append(link)
                            if len(candidates) + len(self.collected_sources) >= self.max_sources:
                                break

            if not candidates:
                break

            # Fetch candidates in bounded parallel batches
            crawl_tasks = [
                self._fetch_and_record(
                    url=url,
                    hop_depth=current_depth,
                    parent_url=None,
                    semaphore=semaphore,
                )
                for url in candidates[: self.max_sources - len(self.collected_sources)]
            ]
            await asyncio.gather(*crawl_tasks)
            current_depth += 1

        return list(self.collected_sources)

    async def _search_and_queue(
        self,
        query: str,
        semaphore: asyncio.Semaphore,
        probe_idx: int,
        total_probes: int,
        progress_callback: Callable[[int, int, str], None] | None,
    ) -> None:
        if len(self.collected_sources) >= self.max_sources:
            return

        async with semaphore:
            try:
                results = await self.search_engine(query, limit=4)
            except Exception as exc:
                logger.warning("Search query failed for '%s': %s", query, exc)
                results = []

        if progress_callback:
            progress_callback(probe_idx + 1, total_probes, f"检索切面完成: {query[:30]}")

        fetch_tasks = []
        for r in results:
            url = r.get("url", "").strip()
            if not url or url in self.visited_urls:
                continue
            if len(self.collected_sources) >= self.max_sources:
                break
            fetch_tasks.append(
                self._fetch_and_record(
                    url=url,
                    hop_depth=0,
                    parent_url=None,
                    initial_title=r.get("title", ""),
                    initial_snippet=r.get("snippet", ""),
                    semaphore=semaphore,
                )
            )

        if fetch_tasks:
            await asyncio.gather(*fetch_tasks)

    async def _fetch_and_record(
        self,
        url: str,
        hop_depth: int,
        parent_url: str | None,
        semaphore: asyncio.Semaphore,
        initial_title: str = "",
        initial_snippet: str = "",
    ) -> None:
        if url in self.visited_urls or len(self.collected_sources) >= self.max_sources:
            return
        self.visited_urls.add(url)

        async with semaphore:
            try:
                title, content = await self.fetcher(url)
            except Exception as exc:
                logger.debug("Fetch failed for '%s': %s", url, exc)
                return

        final_title = title or initial_title or url
        snippet = initial_snippet or content[:300]
        authority = calculate_authority_score(url, final_title, content)
        outbound = extract_potential_outbound_links(content)

        source_channel = "web"
        if "arxiv.org" in url:
            source_channel = "arxiv"
        elif "openalex.org" in url or "doi.org" in url:
            source_channel = "openalex"

        discovered = DiscoveredSource(
            source_id=f"src-{uuid.uuid4().hex[:8]}",
            url=url,
            title=final_title,
            snippet=snippet,
            full_text=content,
            authority_score=authority,
            hop_depth=hop_depth,
            parent_url=parent_url,
            extracted_outbound_links=tuple(outbound),
            source_channel=source_channel,
        )

        self.collected_sources.append(discovered)
