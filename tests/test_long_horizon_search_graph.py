"""Unit tests for Gate 1.2: Multi-source asynchronous recursive search graph."""

from __future__ import annotations

import asyncio
import pytest

from conflux_weave.long_horizon_research import (
    DiscoveredSource,
    ResearchProbe,
    SearchGraphCrawler,
    calculate_authority_score,
    extract_potential_outbound_links,
)


def test_authority_score_calculation():
    score_arxiv = calculate_authority_score(
        "https://arxiv.org/abs/2401.12345",
        "Deep Research on Transformer Benchmarks",
        "Abstract: This paper presents methodology, evaluation, benchmarks, and ablation studies with theorems.",
    )
    assert score_arxiv >= 0.90

    score_random = calculate_authority_score(
        "https://random-blog.xyz/article",
        "A quick overview",
        "Short text with no technical terms.",
    )
    assert score_random <= 0.60


def test_outbound_links_extraction():
    sample_text = """
    Here are the primary references:
    - https://arxiv.org/abs/2305.18290 (Direct paper)
    - https://github.com/vllm-project/vllm (Official implementation)
    - https://x.com/someone/status/12345 (Social media - should be filtered)
    - https://zhihu.com/question/123 (Community - should be filtered)
    - https://openreview.net/forum?id=abc123xyz (Peer review link)
    """
    links = extract_potential_outbound_links(sample_text)
    assert len(links) >= 3
    assert "https://arxiv.org/abs/2305.18290" in links
    assert "https://github.com/vllm-project/vllm" in links
    assert "https://openreview.net/forum?id=abc123xyz" in links
    assert not any("x.com" in l or "zhihu.com" in l for l in links)


def test_search_graph_crawler_direct_and_recursive_hops():
    # Mock search engine returning papers with outbound links
    async def mock_search(query: str, limit: int = 4):
        return [
            {
                "url": f"https://arxiv.org/abs/2401.{abs(hash(query)) % 1000}",
                "title": f"Paper for {query}",
                "snippet": "High authority paper mentioning https://github.com/deep-models/repo and https://openreview.net/paper-x",
            }
        ]

    # Mock fetcher returning text containing links for hop 1
    async def mock_fetcher(url: str):
        if "arxiv.org" in url:
            return (
                "ArXiv Paper Title",
                "Full text mentioning benchmarks, ablation, and citations to https://doi.org/10.1000/182 and https://huggingface.co/models.",
            )
        return (f"Title of {url}", f"Standard content for {url} with evaluation and benchmarks.")

    async def run_test():
        crawler = SearchGraphCrawler(
            max_sources=10,
            max_depth=2,
            concurrency=3,
            fetcher=mock_fetcher,
            search_engine=mock_search,
        )

        probes = (
            ResearchProbe("p-1", "硬件优化", "LLM hardware optimization", "quantitative_metric"),
            ResearchProbe("p-2", "长上下文架构", "Long context architectures", "architectural_tradeoff"),
        )

        collected = await crawler.explore_probes(probes)

        assert len(collected) > 0
        assert len(collected) <= 10
        # Must have explored hop 0
        hop0_count = sum(1 for s in collected if s.hop_depth == 0)
        assert hop0_count > 0

        # Must have explored recursive hops (hop 1)
        hop1_count = sum(1 for s in collected if s.hop_depth > 0)
        assert hop1_count > 0

        # Ensure no duplicate URLs were collected
        urls = [s.url for s in collected]
        assert len(urls) == len(set(urls))

    asyncio.run(run_test())


def test_discovered_source_serialization_roundtrip():
    src = DiscoveredSource(
        source_id="src-test1",
        url="https://arxiv.org/abs/2401.0001",
        title="Sample Title",
        snippet="Sample Snippet",
        full_text="Long content " * 100,
        authority_score=0.95,
        hop_depth=1,
        parent_url="https://arxiv.org/abs/2401.0000",
        extracted_outbound_links=("https://doi.org/123",),
        source_channel="arxiv",
    )

    data = src.to_dict()
    restored = DiscoveredSource.from_dict(data)

    assert restored.source_id == src.source_id
    assert restored.url == src.url
    assert restored.authority_score == src.authority_score
    assert restored.hop_depth == src.hop_depth
    assert restored.parent_url == src.parent_url
    assert restored.extracted_outbound_links == src.extracted_outbound_links


def test_search_graph_crawler_max_sources_strict_bound() -> None:
    async def mock_search(query: str, limit: int = 4):
        return [
            {
                "url": f"https://arxiv.org/abs/2401.{i}_{abs(hash(query)) % 1000}",
                "title": f"Paper {i} for {query}",
                "snippet": "High authority paper",
            }
            for i in range(limit)
        ]

    async def mock_fetcher(url: str):
        return (f"Title {url}", f"Content for {url}")

    async def run_test():
        crawler = SearchGraphCrawler(
            max_sources=5,
            max_depth=2,
            concurrency=8,
            fetcher=mock_fetcher,
            search_engine=mock_search,
        )
        probes = tuple(ResearchProbe(f"p-{i}", f"Aspect {i}", f"Intent {i}", "metric") for i in range(25))
        collected = await crawler.explore_probes(probes)
        assert len(collected) <= 5
        assert len(collected) == 5

    asyncio.run(run_test())


def test_search_graph_crawler_allow_network_false() -> None:
    async def run_test():
        crawler = SearchGraphCrawler(
            max_sources=3,
            allow_network=False,
        )
        probes = (ResearchProbe("p-1", "Offline probe", "Local Intent", "metric"),)
        collected = await crawler.explore_probes(probes)
        assert len(collected) > 0
        for s in collected:
            assert s.source_channel == "offline_local"
            assert s.url.startswith("offline://")

    asyncio.run(run_test())
