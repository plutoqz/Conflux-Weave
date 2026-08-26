"""Small, deterministic retrieval baseline for W2.1.

This module deliberately owns ranking only. Source snapshots, Evidence and
answer rendering remain in their existing domains.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from enum import StrEnum
from typing import Iterable


_TOKEN_RE = re.compile(r"[a-z0-9_]+|[\u4e00-\u9fff]+", re.IGNORECASE)


class RetrievalStrategy(StrEnum):
    BM25 = "bm25"
    DENSE = "dense"
    HYBRID = "hybrid"


class TokenizationMode(StrEnum):
    LEGACY_CONTIGUOUS_CJK = "legacy_contiguous_cjk"
    CJK_UNIGRAM_BIGRAM = "cjk_unigram_bigram"


@dataclass(frozen=True, slots=True)
class RetrievalDocument:
    document_id: str
    text: str
    source_snapshot_id: str | None = None
    locator: dict[str, object] | None = None

    def __post_init__(self) -> None:
        if not self.document_id.strip():
            raise ValueError("document_id must not be empty")
        if not self.text.strip():
            raise ValueError("text must not be empty")


@dataclass(frozen=True, slots=True)
class RetrievalHit:
    document_id: str
    score: float
    rank: int
    source_snapshot_id: str | None
    locator: dict[str, object] | None


@dataclass(frozen=True, slots=True)
class RetrievalQueryResult:
    query: str
    strategy: RetrievalStrategy
    hits: tuple[RetrievalHit, ...]


def cosine_similarity(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    if len(left) != len(right) or not left:
        raise ValueError("vectors must have equal non-zero dimensions")
    denominator = math.sqrt(sum(value * value for value in left)) * math.sqrt(sum(value * value for value in right))
    return 0.0 if denominator == 0 else sum(a * b for a, b in zip(left, right)) / denominator


class DenseRetriever:
    strategy = RetrievalStrategy.DENSE

    def __init__(self, documents: Iterable[RetrievalDocument], vectors: Iterable[tuple[float, ...]]) -> None:
        self.documents = tuple(documents)
        self.vectors = tuple(tuple(vector) for vector in vectors)
        if not self.documents or len(self.documents) != len(self.vectors):
            raise ValueError("documents and vectors must be non-empty and aligned")
        if len({len(vector) for vector in self.vectors}) != 1 or not self.vectors[0]:
            raise ValueError("vectors must have consistent non-zero dimensions")

    def search_vector(self, query_vector: tuple[float, ...], *, top_k: int = 5) -> RetrievalQueryResult:
        if top_k <= 0:
            raise ValueError("top_k must be positive")
        ranked = sorted(enumerate(self.vectors), key=lambda item: (-cosine_similarity(query_vector, item[1]), self.documents[item[0]].document_id))[:top_k]
        return RetrievalQueryResult("<embedding>", self.strategy, tuple(RetrievalHit(self.documents[i].document_id, cosine_similarity(query_vector, vector), rank, self.documents[i].source_snapshot_id, self.documents[i].locator) for rank, (i, vector) in enumerate(ranked, 1)))


def reciprocal_rank_fusion(*results: RetrievalQueryResult, top_k: int = 10, k: int = 60) -> RetrievalQueryResult:
    if not results or top_k <= 0 or k <= 0:
        raise ValueError("results, top_k and k must be positive")
    scores: dict[str, float] = {}
    metadata: dict[str, RetrievalHit] = {}
    for result in results:
        for hit in result.hits:
            scores[hit.document_id] = scores.get(hit.document_id, 0.0) + 1 / (k + hit.rank)
            metadata.setdefault(hit.document_id, hit)
    ordered = sorted(scores, key=lambda doc_id: (-scores[doc_id], doc_id))[:top_k]
    return RetrievalQueryResult(results[0].query, RetrievalStrategy.HYBRID, tuple(RetrievalHit(doc_id, scores[doc_id], rank, metadata[doc_id].source_snapshot_id, metadata[doc_id].locator) for rank, doc_id in enumerate(ordered, 1)))


@dataclass(frozen=True, slots=True)
class RetrievalCase:
    case_id: str
    query: str
    relevant_document_ids: frozenset[str]

    def __post_init__(self) -> None:
        if not self.query.strip():
            raise ValueError("query must not be empty")
        if not self.relevant_document_ids:
            raise ValueError("relevant_document_ids must not be empty")


@dataclass(frozen=True, slots=True)
class RetrievalEvaluation:
    strategy: RetrievalStrategy
    case_count: int
    recall_at_k: float
    mean_reciprocal_rank: float
    hit_count: int
    k: int


class BM25Retriever:
    """A compact BM25 index with deterministic tie-breaking.

    Documents are ranked by score descending and document id ascending. The
    tokenizer keeps Latin identifiers intact and treats contiguous CJK text as
    a token, which is adequate for the frozen W2.1 lexical baseline.
    """

    strategy = RetrievalStrategy.BM25

    def __init__(
        self,
        documents: Iterable[RetrievalDocument],
        *,
        k1: float = 1.2,
        b: float = 0.75,
        tokenization_mode: TokenizationMode = TokenizationMode.CJK_UNIGRAM_BIGRAM,
    ) -> None:
        if k1 <= 0:
            raise ValueError("k1 must be positive")
        if not 0 <= b <= 1:
            raise ValueError("b must be between 0 and 1")
        self.documents = tuple(documents)
        if not self.documents:
            raise ValueError("documents must not be empty")
        ids = [doc.document_id for doc in self.documents]
        if len(ids) != len(set(ids)):
            raise ValueError("document_id values must be unique")
        self.k1 = k1
        self.b = b
        self.tokenization_mode = tokenization_mode
        self._tokens = tuple(
            _tokenize(doc.text, tokenization_mode) for doc in self.documents
        )
        self._term_frequencies = tuple(Counter(tokens) for tokens in self._tokens)
        self._document_frequency = Counter(
            token for tokens in self._tokens for token in set(tokens)
        )
        self._average_length = sum(map(len, self._tokens)) / len(self._tokens)

    def search(self, query: str, *, top_k: int = 5) -> RetrievalQueryResult:
        normalized = query.strip()
        if not normalized:
            raise ValueError("query must not be empty")
        if top_k <= 0:
            raise ValueError("top_k must be positive")
        query_tokens = _tokenize(
            normalized, self.tokenization_mode, prefer_latin_when_mixed=True
        )
        scores = [self._score(query_tokens, index) for index in range(len(self.documents))]
        ranked = sorted(
            ((index, score) for index, score in enumerate(scores) if score > 0),
            key=lambda item: (-item[1], self.documents[item[0]].document_id),
        )[:top_k]
        hits = tuple(
            RetrievalHit(
                document_id=self.documents[index].document_id,
                score=score,
                rank=rank,
                source_snapshot_id=self.documents[index].source_snapshot_id,
                locator=self.documents[index].locator,
            )
            for rank, (index, score) in enumerate(ranked, start=1)
        )
        return RetrievalQueryResult(normalized, self.strategy, hits)

    def _score(self, query_tokens: tuple[str, ...], index: int) -> float:
        frequencies = self._term_frequencies[index]
        length = len(self._tokens[index])
        score = 0.0
        for token in set(query_tokens):
            term_frequency = frequencies.get(token, 0)
            if not term_frequency:
                continue
            document_frequency = self._document_frequency[token]
            idf = math.log1p(
                (len(self.documents) - document_frequency + 0.5)
                / (document_frequency + 0.5)
            )
            denominator = term_frequency + self.k1 * (
                1 - self.b + self.b * length / self._average_length
            )
            score += idf * term_frequency * (self.k1 + 1) / denominator
        return score


def evaluate_retrieval(
    retriever: BM25Retriever,
    cases: Iterable[RetrievalCase],
    *,
    k: int = 5,
) -> RetrievalEvaluation:
    if k <= 0:
        raise ValueError("k must be positive")
    frozen_cases = tuple(cases)
    if not frozen_cases:
        raise ValueError("cases must not be empty")
    hit_count = 0
    reciprocal_ranks: list[float] = []
    for case in frozen_cases:
        result = retriever.search(case.query, top_k=k)
        ranks = [
            hit.rank
            for hit in result.hits
            if hit.document_id in case.relevant_document_ids
        ]
        if ranks:
            hit_count += 1
            reciprocal_ranks.append(1 / min(ranks))
        else:
            reciprocal_ranks.append(0.0)
    return RetrievalEvaluation(
        strategy=retriever.strategy,
        case_count=len(frozen_cases),
        recall_at_k=hit_count / len(frozen_cases),
        mean_reciprocal_rank=sum(reciprocal_ranks) / len(reciprocal_ranks),
        hit_count=hit_count,
        k=k,
    )


def _tokenize(
    text: str,
    mode: TokenizationMode = TokenizationMode.CJK_UNIGRAM_BIGRAM,
    *,
    prefer_latin_when_mixed: bool = False,
) -> tuple[str, ...]:
    matched = [match.group(0).lower() for match in _TOKEN_RE.finditer(text)]
    if prefer_latin_when_mixed and any(not _is_cjk(token) for token in matched):
        matched = [token for token in matched if not _is_cjk(token)]
    tokens: list[str] = []
    for token in matched:
        if mode is TokenizationMode.LEGACY_CONTIGUOUS_CJK or not _is_cjk(token):
            tokens.append(token)
            continue
        tokens.extend(token)
        tokens.extend(token[index : index + 2] for index in range(len(token) - 1))
    return tuple(tokens)


def _is_cjk(token: str) -> bool:
    return bool(token) and all("\u4e00" <= character <= "\u9fff" for character in token)
