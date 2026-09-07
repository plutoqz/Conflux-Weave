"""Multi-source paper discovery and lawful open-access full-text retrieval."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import math
import re
import socket
import tempfile
import time
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen

from pypdf import PdfReader

from conflux_weave.paper_discovery import ArxivPaper
from conflux_weave.runtime.artifacts import LocalArtifactStore


OPENALEX_API = "https://api.openalex.org/works"
UNPAYWALL_API = "https://api.unpaywall.org/v2"
MAX_PDF_BYTES = 80 * 1024 * 1024


class PaperSourceError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = False, artifact_id: str | None = None, details: tuple[dict[str, str], ...] = ()) -> None:
        self.code = code
        self.retryable = retryable
        self.artifact_id = artifact_id
        self.details = details
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class PdfCandidate:
    url: str
    source: str
    license: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"url": self.url, "source": self.source, "license": self.license}


@dataclass(frozen=True, slots=True)
class PaperRecord:
    paper_id: str
    title: str
    summary: str
    authors: tuple[str, ...]
    year: int | None
    published: str | None
    updated: str | None
    venue: str | None
    doi: str | None
    arxiv_id: str | None
    openalex_id: str | None
    sources: tuple[str, ...]
    landing_urls: tuple[str, ...]
    pdf_candidates: tuple[PdfCandidate, ...]
    topics: tuple[str, ...]
    cited_by_count: int
    is_oa: bool
    source_rank: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "paper_id": self.paper_id,
            "title": self.title,
            "summary": self.summary,
            "authors": list(self.authors),
            "year": self.year,
            "published": self.published,
            "updated": self.updated,
            "venue": self.venue,
            "doi": self.doi,
            "arxiv_id": self.arxiv_id,
            "openalex_id": self.openalex_id,
            "sources": list(self.sources),
            "landing_urls": list(self.landing_urls),
            "pdf_candidates": [item.as_dict() for item in self.pdf_candidates],
            "topics": list(self.topics),
            "cited_by_count": self.cited_by_count,
            "is_oa": self.is_oa,
        }


@dataclass(frozen=True, slots=True)
class SourceSearchResult:
    source: str
    query: str
    papers: tuple[PaperRecord, ...]
    cache_hit: bool
    artifact_id: str


@dataclass(frozen=True, slots=True)
class HttpResponse:
    status_code: int
    body: bytes
    headers: Mapping[str, str]
    final_url: str


class HttpTransport(Protocol):
    def get(self, url: str, *, headers: Mapping[str, str], timeout_seconds: float) -> HttpResponse: ...


class UrllibHttpTransport:
    def __init__(self, *, max_bytes: int | None = None, follow_redirects: bool = True) -> None:
        self.max_bytes = max_bytes
        self.follow_redirects = follow_redirects

    def get(self, url: str, *, headers: Mapping[str, str], timeout_seconds: float) -> HttpResponse:
        request = Request(url, headers=dict(headers))
        try:
            opener = build_opener() if self.follow_redirects else build_opener(_NoRedirect())
            with opener.open(request, timeout=timeout_seconds) as response:
                body = response.read(self.max_bytes + 1) if self.max_bytes else response.read()
                return HttpResponse(response.status, body, dict(response.headers.items()), response.geturl())
        except HTTPError as exc:
            body = exc.read(self.max_bytes + 1) if self.max_bytes else exc.read()
            return HttpResponse(exc.code, body, dict(exc.headers.items()) if exc.headers else {}, exc.geturl())
        except (URLError, TimeoutError, OSError) as exc:
            raise PaperSourceError("source_network_failed", f"来源请求失败：{exc}", retryable=True) from exc


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class OpenAlexSearchAdapter:
    def __init__(
        self,
        artifact_store: LocalArtifactStore,
        *,
        transport: HttpTransport | None = None,
        contact_email: str | None = None,
        timeout_seconds: float = 30.0,
        sleep=time.sleep,
    ) -> None:
        self.store = artifact_store
        self.transport = transport or UrllibHttpTransport()
        self.contact_email = (contact_email or "").strip()
        self.timeout_seconds = timeout_seconds
        self.sleep = sleep
        self.cache_root = artifact_store.root.parent / "source-cache" / "openalex"

    def search(
        self,
        query: str,
        *,
        max_results: int = 10,
        year_from: int | None = None,
        year_to: int | None = None,
        oa_only: bool = False,
        producer_step_id: str = "step-openalex-search",
    ) -> SourceSearchResult:
        normalized = query.strip()
        if not normalized:
            raise ValueError("query must not be empty")
        filters = []
        if year_from:
            filters.append(f"from_publication_date:{year_from}-01-01")
        if year_to:
            filters.append(f"to_publication_date:{year_to}-12-31")
        if oa_only:
            filters.append("is_oa:true")
        doi_match = re.fullmatch(r"(?:https?://(?:dx\.)?doi\.org/|doi:\s*)?(10\.\d{4,9}/\S+)", normalized, re.IGNORECASE)
        params: dict[str, Any] = {
            "per-page": max(1, min(max_results, 25)),
            "select": "id,doi,title,display_name,publication_year,publication_date,updated_date,authorships,abstract_inverted_index,primary_location,best_oa_location,open_access,primary_topic,topics,cited_by_count,ids",
        }
        if doi_match:
            filters.insert(0, "doi:" + normalize_doi(doi_match.group(1)))
        else:
            params["search"] = normalized
        if filters:
            params["filter"] = ",".join(filters)
        if self.contact_email:
            params["mailto"] = self.contact_email
        url = OPENALEX_API + "?" + urlencode(params)
        cache_key = hashlib.sha256(url.encode("utf-8")).hexdigest()
        cached = self._cache_get(cache_key)
        if cached is None:
            response, attempt_artifact_ids = self._request(url, producer_step_id)
            if response.status_code != 200:
                raise PaperSourceError(
                    "openalex_http_failed",
                    f"OpenAlex 返回 HTTP {response.status_code}",
                    retryable=response.status_code == 429 or response.status_code >= 500,
                )
            body = response.body
            cache_hit = False
        else:
            body = cached
            cache_hit = True
            attempt_artifact_ids = []
        artifact = self.store.put_bytes(
            body,
            media_type="application/json",
            producer_step_id=producer_step_id,
            schema_version="openalex.works-response.v1",
        )
        try:
            payload = json.loads(body)
            rows = payload.get("results", [])
            if not isinstance(rows, list):
                raise ValueError("results is not an array")
            papers = tuple(_openalex_record(row, index) for index, row in enumerate(rows) if isinstance(row, dict))
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError) as exc:
            raise PaperSourceError("openalex_response_invalid", f"OpenAlex 响应无法解析：{exc}") from exc
        if not cache_hit:
            self._cache_put(cache_key, body)
        self.store.put_json(
            {
                "schema_version": "conflux-weave.openalex-search.v1",
                "query": normalized,
                "request_url": url,
                "paper_count": len(papers),
                "cache_hit": cache_hit,
                "attempt_artifact_refs": attempt_artifact_ids,
                "response_artifact_ref": artifact.artifact_id,
                "acquired_at": _utc_now(),
            },
            producer_step_id=producer_step_id,
            schema_version="conflux-weave.openalex-search.v1",
        )
        return SourceSearchResult("openalex", normalized, papers, cache_hit, artifact.artifact_id)

    def _request(self, url: str, producer_step_id: str) -> tuple[HttpResponse, list[str]]:
        attempts = []
        last_error = None
        for attempt in range(1, 4):
            try:
                response = self.transport.get(url, headers={"Accept": "application/json", "User-Agent": self._user_agent()}, timeout_seconds=self.timeout_seconds)
            except PaperSourceError as exc:
                last_error = exc
                if not exc.retryable or attempt == 3:
                    raise
                self.sleep(float(attempt))
                continue
            artifact = self.store.put_bytes(response.body, media_type=_header(response.headers, "Content-Type") or "application/octet-stream", producer_step_id=producer_step_id, schema_version="openalex.http-attempt.v1")
            attempts.append(artifact.artifact_id)
            if response.status_code not in {429, 500, 502, 503, 504} or attempt == 3:
                return response, attempts
            retry_after = _header(response.headers, "Retry-After")
            try:
                delay = max(0.0, min(float(retry_after), 30.0)) if retry_after else float(attempt)
            except ValueError:
                delay = float(attempt)
            self.sleep(delay)
        raise last_error or AssertionError("OpenAlex retry loop did not return")

    def _user_agent(self) -> str:
        suffix = f"; mailto:{self.contact_email}" if self.contact_email else ""
        return f"Conflux-Weave/0.3 (research-workbench{suffix})"

    def _cache_get(self, key: str) -> bytes | None:
        path = self.cache_root / f"{key}.json"
        try:
            if time.time() - path.stat().st_mtime > 86400:
                return None
            return path.read_bytes()
        except OSError:
            return None

    def _cache_put(self, key: str, body: bytes) -> None:
        self.cache_root.mkdir(parents=True, exist_ok=True)
        target = self.cache_root / f"{key}.json"
        descriptor, name = tempfile.mkstemp(dir=self.cache_root, prefix=f".{key}.", suffix=".tmp")
        temporary = Path(name)
        try:
            with open(descriptor, "wb", closefd=True) as handle:
                handle.write(body)
                handle.flush()
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)


class UnpaywallResolver:
    def __init__(self, store: LocalArtifactStore, *, email: str, transport: HttpTransport | None = None) -> None:
        self.store = store
        self.email = email.strip()
        self.transport = transport or UrllibHttpTransport()

    def resolve(self, doi: str, *, producer_step_id: str = "step-unpaywall-resolve") -> tuple[PdfCandidate, ...]:
        if not self.email:
            return ()
        normalized = normalize_doi(doi)
        url = f"{UNPAYWALL_API}/{quote(normalized, safe='')}?" + urlencode({"email": self.email})
        response = self.transport.get(url, headers={"Accept": "application/json", "User-Agent": f"Conflux-Weave/0.3; mailto:{self.email}"}, timeout_seconds=30)
        artifact = self.store.put_bytes(response.body, media_type="application/json", producer_step_id=producer_step_id, schema_version="unpaywall.response.v1")
        if response.status_code == 404:
            return ()
        if response.status_code != 200:
            raise PaperSourceError("unpaywall_http_failed", f"Unpaywall 返回 HTTP {response.status_code}", retryable=response.status_code == 429 or response.status_code >= 500)
        try:
            payload = json.loads(response.body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PaperSourceError("unpaywall_response_invalid", f"Unpaywall 响应无法解析：{exc}") from exc
        locations = [payload.get("best_oa_location"), *(payload.get("oa_locations") or [])]
        candidates = []
        for location in locations:
            if not isinstance(location, dict):
                continue
            url_value = location.get("url_for_pdf")
            if isinstance(url_value, str) and url_value:
                candidates.append(PdfCandidate(url_value, "unpaywall", location.get("license")))
        self.store.put_json({"schema_version": "conflux-weave.unpaywall-resolution.v1", "doi": normalized, "candidate_count": len(candidates), "response_artifact_ref": artifact.artifact_id, "acquired_at": _utc_now()}, producer_step_id=producer_step_id, schema_version="conflux-weave.unpaywall-resolution.v1")
        return _dedupe_pdf_candidates(candidates)


@dataclass(frozen=True, slots=True)
class PdfFetchResult:
    content: bytes
    final_url: str
    source: str
    attempt_artifact_id: str
    page_count: int


class OpenAccessPdfFetcher:
    def __init__(self, store: LocalArtifactStore, *, transport: HttpTransport | None = None) -> None:
        self.store = store
        self.transport = transport or UrllibHttpTransport(max_bytes=MAX_PDF_BYTES, follow_redirects=False)

    def fetch(self, candidates: tuple[PdfCandidate, ...], *, producer_step_id: str = "step-library-paper-fetch") -> PdfFetchResult:
        failures = []
        for candidate in _dedupe_pdf_candidates(candidates):
            try:
                validate_public_url(candidate.url)
                response = self._get_with_redirects(candidate.url)
                validate_public_url(response.final_url)
                artifact = self.store.put_bytes(response.body, media_type=_header(response.headers, "Content-Type") or "application/octet-stream", producer_step_id=producer_step_id, schema_version="conflux-weave.paper-fetch-attempt.v1")
                if response.status_code != 200:
                    raise ValueError(f"HTTP {response.status_code}")
                if len(response.body) > MAX_PDF_BYTES:
                    raise ValueError("PDF 超过 80 MB 上限")
                if not response.body.startswith(b"%PDF"):
                    raise ValueError("响应不是 PDF")
                page_count = len(PdfReader(_bytes_io(response.body)).pages)
                if page_count < 1:
                    raise ValueError("PDF 没有可读取页面")
                return PdfFetchResult(response.body, response.final_url, candidate.source, artifact.artifact_id, page_count)
            except Exception as exc:
                failures.append({"url": candidate.url, "source": candidate.source, "error": str(exc)[:500]})
        failure = self.store.put_json({"schema_version": "conflux-weave.paper-fetch-failure.v1", "attempts": failures, "acquired_at": _utc_now()}, producer_step_id=producer_step_id, schema_version="conflux-weave.paper-fetch-failure.v1")
        raise PaperSourceError("paper_fetch_failed", "所有合法开放全文地址均获取失败。", artifact_id=failure.artifact_id, details=tuple(failures))

    def _get_with_redirects(self, url: str) -> HttpResponse:
        current = url
        for _ in range(6):
            validate_public_url(current)
            response = self.transport.get(current, headers={"Accept": "application/pdf", "User-Agent": "Conflux-Weave/0.3 (research-workbench)"}, timeout_seconds=45)
            if response.status_code not in {301, 302, 303, 307, 308}:
                return response
            location = _header(response.headers, "Location")
            if not location:
                return response
            from urllib.parse import urljoin

            current = urljoin(current, location)
        raise ValueError("PDF 下载重定向次数过多")


def arxiv_record(paper: ArxivPaper, rank: int = 0) -> PaperRecord:
    arxiv_id = paper.arxiv_id
    base_id = re.sub(r"v\d+$", "", arxiv_id, flags=re.IGNORECASE)
    candidates = (PdfCandidate(paper.pdf_url, "arxiv"),) if paper.pdf_url else ()
    year = _year(paper.published)
    return PaperRecord(
        paper_id=f"paper-arxiv-{base_id.replace('/', '-')}",
        title=paper.title,
        summary=paper.summary,
        authors=paper.authors,
        year=year,
        published=paper.published,
        updated=paper.updated,
        venue="arXiv",
        doi=None,
        arxiv_id=arxiv_id,
        openalex_id=None,
        sources=("arxiv",),
        landing_urls=(paper.entry_url,),
        pdf_candidates=candidates,
        topics=paper.categories,
        cited_by_count=0,
        is_oa=bool(candidates),
        source_rank=1.0 / (rank + 1),
    )


def merge_and_rank(records: list[PaperRecord], *, query_terms: tuple[str, ...], max_results: int, sort: str = "relevance", required_terms: tuple[str, ...] = (), required_concepts: tuple[tuple[str, ...], ...] = ()) -> tuple[PaperRecord, ...]:
    merged: list[PaperRecord] = []
    indexes: dict[str, int] = {}
    for record in records:
        keys = _identity_keys(record)
        match = next((indexes[key] for key in keys if key in indexes), None)
        if match is None:
            match = len(merged)
            merged.append(record)
        else:
            merged[match] = _merge_records(merged[match], record)
        for key in _identity_keys(merged[match]):
            indexes[key] = match
    clean_terms = tuple(dict.fromkeys(_normalize_title(term) for term in query_terms if len(_normalize_title(term)) > 1))
    if clean_terms:
        minimum_hits = 1 if len(clean_terms) == 1 else 2
        merged = [paper for paper in merged if _term_hits(paper, clean_terms) >= minimum_hits]
    required = tuple(dict.fromkeys(_normalize_title(term) for term in required_terms if len(_normalize_title(term)) > 1))
    if required:
        merged = [paper for paper in merged if _term_hits(paper, required) == len(required)]
    concepts = tuple(tuple(dict.fromkeys(_normalize_title(term) for term in group if len(_normalize_title(term)) > 1)) for group in required_concepts)
    concepts = tuple(group for group in concepts if group)
    if concepts:
        merged = [paper for paper in merged if all(_concept_hit(paper, group) for group in concepts)]
    if sort == "newest":
        key = lambda paper: (paper.year or 0, _relevance(paper, query_terms))
    elif sort == "impact":
        key = lambda paper: (paper.cited_by_count, _relevance(paper, query_terms))
    else:
        key = lambda paper: (_relevance(paper, query_terms), paper.year or 0)
    return tuple(sorted(merged, key=key, reverse=True)[:max_results])


def paper_from_dict(value: Mapping[str, Any]) -> PaperRecord:
    return PaperRecord(
        paper_id=str(value.get("paper_id") or ""),
        title=str(value.get("title") or ""),
        summary=str(value.get("summary") or ""),
        authors=tuple(str(item) for item in value.get("authors", []) if item),
        year=int(value["year"]) if value.get("year") else None,
        published=str(value["published"]) if value.get("published") else None,
        updated=str(value["updated"]) if value.get("updated") else None,
        venue=str(value["venue"]) if value.get("venue") else None,
        doi=normalize_doi(str(value["doi"])) if value.get("doi") else None,
        arxiv_id=str(value["arxiv_id"]) if value.get("arxiv_id") else None,
        openalex_id=str(value["openalex_id"]) if value.get("openalex_id") else None,
        sources=tuple(str(item) for item in value.get("sources", []) if item),
        landing_urls=tuple(str(item) for item in value.get("landing_urls", []) if item),
        pdf_candidates=tuple(PdfCandidate(str(item["url"]), str(item.get("source") or "unknown"), item.get("license")) for item in value.get("pdf_candidates", []) if isinstance(item, Mapping) and item.get("url")),
        topics=tuple(str(item) for item in value.get("topics", []) if item),
        cited_by_count=int(value.get("cited_by_count") or 0),
        is_oa=bool(value.get("is_oa")),
    )


def normalize_doi(value: str) -> str:
    normalized = value.strip().lower()
    normalized = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", normalized)
    normalized = re.sub(r"^doi:\s*", "", normalized)
    return normalized.rstrip(" .")


def validate_public_url(value: str) -> None:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("全文地址必须是公开 HTTP(S) URL")
    hostname = parsed.hostname.lower()
    if hostname in {"localhost", "localhost.localdomain"} or hostname.endswith(".local"):
        raise ValueError("全文地址不能指向本机或私有网络")
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(hostname, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)}
    except OSError as exc:
        raise ValueError(f"全文地址无法解析：{exc}") from exc
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if not ip.is_global:
            raise ValueError("全文地址不能指向本机或私有网络")


def _openalex_record(row: Mapping[str, Any], rank: int) -> PaperRecord:
    ids = row.get("ids") if isinstance(row.get("ids"), Mapping) else {}
    openalex_id = _tail_id(row.get("id") or ids.get("openalex"))
    doi = normalize_doi(str(row.get("doi") or ids.get("doi") or "")) or None
    arxiv_id = _tail_id(ids.get("arxiv"))
    authors = tuple(
        str(item.get("author", {}).get("display_name"))
        for item in row.get("authorships", [])
        if isinstance(item, Mapping) and isinstance(item.get("author"), Mapping) and item["author"].get("display_name")
    )
    primary = row.get("primary_location") if isinstance(row.get("primary_location"), Mapping) else {}
    best_oa = row.get("best_oa_location") if isinstance(row.get("best_oa_location"), Mapping) else {}
    source = primary.get("source") if isinstance(primary.get("source"), Mapping) else {}
    open_access = row.get("open_access") if isinstance(row.get("open_access"), Mapping) else {}
    pdfs = []
    for location, label in ((best_oa, "openalex_best_oa"), (primary, "openalex_primary")):
        pdf_url = location.get("pdf_url") if isinstance(location, Mapping) else None
        if isinstance(pdf_url, str) and pdf_url:
            pdfs.append(PdfCandidate(pdf_url, label, location.get("license")))
    landing = [url for url in (row.get("id"), primary.get("landing_page_url"), best_oa.get("landing_page_url")) if isinstance(url, str) and url]
    topic_rows = row.get("topics") if isinstance(row.get("topics"), list) else []
    primary_topic = row.get("primary_topic") if isinstance(row.get("primary_topic"), Mapping) else {}
    topics = [primary_topic.get("display_name"), *(item.get("display_name") for item in topic_rows if isinstance(item, Mapping))]
    title = str(row.get("title") or row.get("display_name") or "未命名论文")
    return PaperRecord(
        paper_id=_paper_id(doi, arxiv_id, openalex_id, title),
        title=title,
        summary=_abstract(row.get("abstract_inverted_index")),
        authors=authors,
        year=int(row["publication_year"]) if row.get("publication_year") else None,
        published=str(row["publication_date"]) if row.get("publication_date") else None,
        updated=str(row["updated_date"]) if row.get("updated_date") else None,
        venue=str(source.get("display_name")) if source.get("display_name") else None,
        doi=doi,
        arxiv_id=arxiv_id,
        openalex_id=openalex_id,
        sources=("openalex",),
        landing_urls=tuple(dict.fromkeys(landing)),
        pdf_candidates=_dedupe_pdf_candidates(pdfs),
        topics=tuple(dict.fromkeys(str(item) for item in topics if item)),
        cited_by_count=int(row.get("cited_by_count") or 0),
        is_oa=bool(open_access.get("is_oa") or pdfs),
        source_rank=1.2 / (rank + 1),
    )


def _identity_keys(record: PaperRecord) -> tuple[str, ...]:
    keys = []
    if record.doi:
        keys.append("doi:" + normalize_doi(record.doi))
    if record.arxiv_id:
        keys.append("arxiv:" + re.sub(r"v\d+$", "", record.arxiv_id.lower()))
    if record.openalex_id:
        keys.append("openalex:" + record.openalex_id.lower())
    title = _normalize_title(record.title)
    first_author = _normalize_title(record.authors[0]) if record.authors else ""
    if title and record.year and first_author:
        keys.append(f"title:{title}|{record.year}|{first_author}")
    return tuple(keys or ("paper:" + record.paper_id,))


def _merge_records(left: PaperRecord, right: PaperRecord) -> PaperRecord:
    prefer = right if right.doi and not left.doi else left
    other = left if prefer is right else right
    return replace(
        prefer,
        paper_id=_paper_id(prefer.doi or other.doi, prefer.arxiv_id or other.arxiv_id, prefer.openalex_id or other.openalex_id, prefer.title),
        summary=prefer.summary or other.summary,
        authors=prefer.authors or other.authors,
        year=prefer.year or other.year,
        published=prefer.published or other.published,
        updated=max(filter(None, (prefer.updated, other.updated)), default=None),
        venue=prefer.venue or other.venue,
        doi=prefer.doi or other.doi,
        arxiv_id=prefer.arxiv_id or other.arxiv_id,
        openalex_id=prefer.openalex_id or other.openalex_id,
        sources=tuple(dict.fromkeys((*left.sources, *right.sources))),
        landing_urls=tuple(dict.fromkeys((*left.landing_urls, *right.landing_urls))),
        pdf_candidates=_dedupe_pdf_candidates((*left.pdf_candidates, *right.pdf_candidates)),
        topics=tuple(dict.fromkeys((*left.topics, *right.topics))),
        cited_by_count=max(left.cited_by_count, right.cited_by_count),
        is_oa=left.is_oa or right.is_oa,
        source_rank=max(left.source_rank, right.source_rank),
    )


def _relevance(paper: PaperRecord, terms: tuple[str, ...]) -> float:
    title = _normalize_title(paper.title)
    body = _normalize_title(" ".join((paper.title, paper.summary, *paper.topics)))
    clean_terms = tuple(dict.fromkeys(_normalize_title(term) for term in terms if len(_normalize_title(term)) > 1))
    title_hits = sum(term in title for term in clean_terms)
    body_hits = sum(term in body for term in clean_terms)
    coverage = body_hits / max(1, len(clean_terms))
    return title_hits * 3.0 + body_hits + coverage * 4.0 + paper.source_rank + (0.4 if paper.is_oa else 0.0) + min(0.6, math.log1p(paper.cited_by_count) / 20)


def _term_hits(paper: PaperRecord, terms: tuple[str, ...]) -> int:
    body = _normalize_title(" ".join((paper.title, paper.summary, *paper.topics)))
    return sum(term in body for term in terms)


def _concept_hit(paper: PaperRecord, alternatives: tuple[str, ...]) -> bool:
    body_terms = {_stem(term) for term in re.findall(r"[a-zA-Z][a-zA-Z0-9-]*", " ".join((paper.title, paper.summary, *paper.topics)).lower())}
    return any({_stem(term) for term in re.findall(r"[a-zA-Z][a-zA-Z0-9-]*", alternative)} <= body_terms for alternative in alternatives)


def _stem(value: str) -> str:
    word = value.lower().strip("-")
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def _paper_id(doi: str | None, arxiv_id: str | None, openalex_id: str | None, title: str) -> str:
    identity = f"doi:{normalize_doi(doi)}" if doi else f"arxiv:{re.sub(r'v\d+$', '', arxiv_id or '', flags=re.IGNORECASE)}" if arxiv_id else f"openalex:{openalex_id}" if openalex_id else f"title:{_normalize_title(title)}"
    return "paper-sha256-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _abstract(index: Any) -> str:
    if not isinstance(index, Mapping):
        return ""
    positions = []
    for word, indexes in index.items():
        if not isinstance(indexes, list):
            continue
        positions.extend((int(position), str(word)) for position in indexes if isinstance(position, int))
    return " ".join(word for _, word in sorted(positions))


def _normalize_title(value: str) -> str:
    return " ".join(re.findall(r"[\w]+", value.lower(), flags=re.UNICODE))


def _tail_id(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    return value.rstrip("/").rsplit("/", 1)[-1]


def _year(value: str | None) -> int | None:
    match = re.match(r"(\d{4})", value or "")
    return int(match.group(1)) if match else None


def _dedupe_pdf_candidates(values) -> tuple[PdfCandidate, ...]:
    seen = set()
    result = []
    for item in values:
        if item.url in seen:
            continue
        seen.add(item.url)
        result.append(item)
    return tuple(result)


def _header(headers: Mapping[str, str], name: str) -> str:
    return next((str(value) for key, value in headers.items() if key.lower() == name.lower()), "")


def _bytes_io(content: bytes):
    import io

    return io.BytesIO(content)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
