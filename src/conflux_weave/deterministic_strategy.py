"""Deterministic non-Agent arXiv query derivation for the W4 comparison."""

from __future__ import annotations

import re
from typing import Any, Mapping


DETERMINISTIC_STRATEGY_ID = "deterministic-arxiv-query-v1"
_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9-]*")
_YEAR_CONSTRAINT_RE = re.compile(r"^published_in_(20\d{2})$")


def derive_arxiv_query(case_input: Mapping[str, Any]) -> str:
    """Derive one bounded query from explicit input fields without case identity."""
    topics = case_input.get("topics")
    if isinstance(topics, list) and topics:
        terms = tuple(_normalize_topic(item) for item in topics)
        terms = tuple(item for item in terms if item and item != "agent")
        if not terms:
            raise ValueError("topics do not contain a usable arXiv term")
        return "all:agent AND (" + " OR ".join(_all_term(item) for item in terms) + ")"

    constraints = case_input.get("hard_constraints")
    if isinstance(constraints, list) and constraints:
        terms: list[str] = []
        date_filter = None
        for raw in constraints:
            if not isinstance(raw, str) or not raw.strip():
                raise ValueError("hard_constraints must contain non-empty strings")
            normalized = raw.strip().lower()
            year = _YEAR_CONSTRAINT_RE.fullmatch(normalized)
            if year:
                date_filter = (
                    f"submittedDate:[{year.group(1)}01010000 TO "
                    f"{year.group(1)}12312359]"
                )
                continue
            terms.append(" ".join(normalized.replace("-", "_").split("_")))
        clauses = [_all_term(item) for item in terms]
        if date_filter is not None:
            clauses.append(date_filter)
        if not clauses:
            raise ValueError("hard_constraints do not contain a usable arXiv term")
        return " AND ".join(clauses)

    query = case_input.get("query_zh")
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query_zh must be a non-empty string")
    tokens = []
    for match in _TOKEN_RE.findall(query):
        token = match.lower().replace("-", " ")
        if token not in tokens:
            tokens.append(token)
    if not tokens:
        raise ValueError("query_zh contains no deterministic arXiv token")
    return " AND ".join(_all_term(item) for item in tokens)


def _normalize_topic(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("topics must contain non-empty strings")
    normalized = " ".join(value.strip().lower().replace("-", " ").split())
    if normalized.startswith("agent "):
        normalized = normalized.removeprefix("agent ")
    if normalized.endswith(" llm"):
        normalized = normalized.removesuffix(" llm")
    return normalized


def _all_term(term: str) -> str:
    return f'all:"{term}"' if " " in term else f"all:{term}"
