import asyncio
import io
import json
from types import SimpleNamespace

from pypdf import PdfWriter

from conflux_weave.documents import DocumentSegment, LocalDocumentImporter
from conflux_weave.library_papers import (
    HttpResponse,
    OpenAccessPdfFetcher,
    OpenAlexSearchAdapter,
    PaperRecord,
    PdfCandidate,
    PdfFetchResult,
    SourceSearchResult,
    UnpaywallResolver,
    arxiv_record,
    merge_and_rank,
)
from conflux_weave.paper_discovery import ArxivPaper
from conflux_weave.runtime import LocalArtifactStore, SQLiteRuntimeRepository
from conflux_weave.server import LibraryPaperRequest, WorkerLoop, create_app


class StaticTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.urls = []

    def get(self, url, *, headers, timeout_seconds):
        self.urls.append(url)
        return self.responses.pop(0)


class PassiveRuntime:
    executor_id = "passive@v1"
    task_kinds = ("paper_discovery",)

    def work_once(self, **kwargs):
        return None


def route(app, path):
    return next(item.endpoint for item in app.routes if item.path == path)


def paper(**overrides):
    values = {
        "paper_id": "paper-1",
        "title": "Geospatial Agents for Disaster Response",
        "summary": "Agents use geospatial tools for disaster response.",
        "authors": ("A. Researcher",),
        "year": 2025,
        "published": "2025-01-01",
        "updated": None,
        "venue": "GIScience",
        "doi": "10.1000/example",
        "arxiv_id": None,
        "openalex_id": "W1",
        "sources": ("openalex",),
        "landing_urls": ("https://openalex.org/W1",),
        "pdf_candidates": (PdfCandidate("https://example.org/paper.pdf", "openalex_best_oa"),),
        "topics": ("Geospatial Artificial Intelligence",),
        "cited_by_count": 8,
        "is_oa": True,
    }
    values.update(overrides)
    return PaperRecord(**values)


def test_openalex_maps_metadata_oa_and_abstract(tmp_path):
    payload = {
        "results": [{
            "id": "https://openalex.org/W1",
            "doi": "https://doi.org/10.1000/EXAMPLE",
            "title": "Geospatial Agents",
            "publication_year": 2025,
            "publication_date": "2025-02-03",
            "authorships": [{"author": {"display_name": "A. Researcher"}}],
            "abstract_inverted_index": {"Agent": [0], "mapping": [1]},
            "primary_location": {"landing_page_url": "https://example.org/article", "source": {"display_name": "GIScience"}},
            "best_oa_location": {"pdf_url": "https://example.org/paper.pdf", "license": "cc-by"},
            "open_access": {"is_oa": True},
            "primary_topic": {"display_name": "Geospatial AI"},
            "topics": [],
            "cited_by_count": 12,
            "ids": {"arxiv": "https://arxiv.org/abs/2501.00001"},
        }]
    }
    transport = StaticTransport([HttpResponse(200, json.dumps(payload).encode(), {"Content-Type": "application/json"}, "https://api.openalex.org/works")])
    result = OpenAlexSearchAdapter(LocalArtifactStore(tmp_path / "artifacts"), transport=transport).search("geospatial agents")

    assert len(result.papers) == 1
    item = result.papers[0]
    assert item.doi == "10.1000/example"
    assert item.arxiv_id == "2501.00001"
    assert item.summary == "Agent mapping"
    assert item.pdf_candidates[0].license == "cc-by"


def test_openalex_retries_only_retryable_http_status(tmp_path):
    payload = {"results": []}
    transport = StaticTransport([
        HttpResponse(503, b"busy", {"Content-Type": "text/plain", "Retry-After": "0"}, "https://api.openalex.org/works"),
        HttpResponse(200, json.dumps(payload).encode(), {"Content-Type": "application/json"}, "https://api.openalex.org/works"),
    ])
    delays = []

    result = OpenAlexSearchAdapter(LocalArtifactStore(tmp_path / "artifacts"), transport=transport, sleep=delays.append).search("geospatial agents")

    assert result.papers == ()
    assert len(transport.urls) == 2
    assert delays == [0.0]


def test_openalex_uses_deterministic_doi_filter(tmp_path):
    transport = StaticTransport([HttpResponse(200, b'{"results":[]}', {"Content-Type": "application/json"}, "https://api.openalex.org/works")])

    OpenAlexSearchAdapter(LocalArtifactStore(tmp_path / "artifacts"), transport=transport).search("10.1000/example")

    assert "filter=doi%3A10.1000%2Fexample" in transport.urls[0]
    assert "search=" not in transport.urls[0]


def test_unpaywall_returns_only_pdf_locations(tmp_path):
    payload = {"best_oa_location": {"url_for_pdf": "https://repo.example/paper.pdf", "license": "cc-by"}, "oa_locations": [{"url": "https://example.org/landing"}]}
    transport = StaticTransport([HttpResponse(200, json.dumps(payload).encode(), {"Content-Type": "application/json"}, "https://api.unpaywall.org/v2/10.1000/example")])

    candidates = UnpaywallResolver(LocalArtifactStore(tmp_path / "artifacts"), email="researcher@example.org", transport=transport).resolve("10.1000/example")

    assert candidates == (PdfCandidate("https://repo.example/paper.pdf", "unpaywall", "cc-by"),)


def test_merge_uses_doi_and_keeps_all_sources():
    openalex = paper()
    arxiv = paper(paper_id="paper-2", doi="10.1000/example", openalex_id=None, arxiv_id="2501.00001v2", sources=("arxiv",), venue="arXiv", cited_by_count=0)

    result = merge_and_rank([openalex, arxiv], query_terms=("geospatial", "agents"), max_results=10)

    assert len(result) == 1
    assert result[0].sources == ("openalex", "arxiv")
    assert result[0].arxiv_id == "2501.00001v2"
    assert result[0].openalex_id == "W1"


def test_search_returns_partial_when_one_source_fails(tmp_path, monkeypatch):
    store = LocalArtifactStore(tmp_path / "artifacts")
    repository = SQLiteRuntimeRepository(tmp_path / "db" / "runtime.sqlite3", store)

    class GoodOpenAlex:
        def __init__(self, *args, **kwargs):
            pass

        def search(self, query, **kwargs):
            return SourceSearchResult("openalex", query, (paper(),), False, "artifact-1")

    class BadArxiv:
        def __init__(self, *args, **kwargs):
            pass

        def search(self, query, **kwargs):
            raise RuntimeError("arXiv unavailable")

    monkeypatch.setattr("conflux_weave.server.OpenAlexSearchAdapter", GoodOpenAlex)
    monkeypatch.setattr("conflux_weave.server.ArxivSearchAdapter", BadArxiv)
    runtime = PassiveRuntime()
    app = create_app(repository, runtime, worker=WorkerLoop(runtime, interval_seconds=10))
    response = asyncio.run(route(app, "/api/v1/library/papers/search")("geospatial agents", 10, "openalex,arxiv", None, None, False, "relevance"))

    assert response["status"] == "partial"
    assert len(response["items"]) == 1
    assert {item["source"]: item["status"] for item in response["sources"]} == {"openalex": "success", "arxiv": "failed"}


def test_blank_pdf_is_retained_as_parse_failed(tmp_path, monkeypatch):
    buffer = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.write(buffer)
    content = buffer.getvalue()
    store = LocalArtifactStore(tmp_path / "artifacts")
    repository = SQLiteRuntimeRepository(tmp_path / "db" / "runtime.sqlite3", store)

    class BlankFetcher:
        def __init__(self, *args, **kwargs):
            pass

        def fetch(self, candidates):
            return PdfFetchResult(content, "https://example.org/paper.pdf", "fixture", "artifact-fixture", 1)

    monkeypatch.setattr("conflux_weave.server.OpenAccessPdfFetcher", BlankFetcher)
    runtime = PassiveRuntime()
    app = create_app(repository, runtime, worker=WorkerLoop(runtime, interval_seconds=10))
    response = asyncio.run(route(app, "/api/v1/library/papers")(LibraryPaperRequest(action="fulltext", paper=paper().as_dict())))

    assert response.status_code == 422
    registry = json.loads((tmp_path / "db" / "library-registry.json").read_text(encoding="utf-8"))
    assert registry[0]["status"] == "parse_failed"
    assert registry[0]["document_id"].startswith("document-sha256-")


def test_arxiv_record_preserves_version_but_identity_uses_base_id():
    item = arxiv_record(ArxivPaper("2501.00001v3", "Title", "Summary", ("A",), "2025-01-01", "2025-02-01", "https://arxiv.org/abs/2501.00001v3", "https://arxiv.org/pdf/2501.00001v3", ("cs.AI",)))

    assert item.arxiv_id == "2501.00001v3"
    assert item.paper_id.startswith("paper-arxiv-2501.00001")


def test_pdf_redirect_to_private_network_is_rejected_before_following(tmp_path):
    transport = StaticTransport([HttpResponse(302, b"", {"Location": "http://127.0.0.1/private.pdf"}, "https://1.1.1.1/paper.pdf")])
    fetcher = OpenAccessPdfFetcher(LocalArtifactStore(tmp_path / "artifacts"), transport=transport)

    try:
        fetcher.fetch((PdfCandidate("https://1.1.1.1/paper.pdf", "fixture"),))
    except Exception as exc:
        assert "所有合法开放全文地址均获取失败" in str(exc)
        assert exc.artifact_id.startswith("artifact-sha256-")
        assert exc.details[0]["source"] == "fixture"
    else:
        raise AssertionError("private redirect was followed")
    assert transport.urls == ["https://1.1.1.1/paper.pdf"]


def test_ranker_rejects_weak_matches_for_multi_concept_query():
    unrelated = paper(title="Navigating the Future", summary="A general editorial.", topics=("Editorial",))

    result = merge_and_rank([unrelated], query_terms=("quantum", "banana", "cadastral", "agents"), max_results=10)

    assert result == ()


def test_required_concept_anchors_reject_partial_topic_match():
    partial = paper(title="Geospatial Semantics", summary="Spatial information systems.", topics=("Geospatial AI",))
    relevant = paper(paper_id="paper-2", doi="10.1000/relevant", openalex_id="W2", title="An Autonomous Geospatial Agent", summary="An agent for GIS tasks.")

    result = merge_and_rank([partial, relevant], query_terms=("geospatial", "agent", "spatial"), required_terms=("geospatial", "agent"), max_results=10)

    assert [item.paper_id for item in result] == ["paper-2"]


def test_concept_groups_accept_synonyms_but_require_every_concept():
    partial = paper(title="Geospatial Semantics", summary="Spatial information systems.", topics=("Geospatial AI",))
    relevant = paper(paper_id="paper-2", doi="10.1000/relevant", openalex_id="W2", title="An Autonomous GIS Agent", summary="An agent for spatial analysis.")

    result = merge_and_rank([partial, relevant], query_terms=("geospatial", "agent"), required_concepts=(("geographic", "geospatial", "gis"), ("agent", "agentic", "autonomous")), max_results=10)

    assert [item.paper_id for item in result] == ["paper-2"]


def test_concept_groups_normalize_singular_and_plural_terms():
    relevant = paper(title="An Autonomous GIS Agent", summary="A framework for mapping tasks.")

    result = merge_and_rank([relevant], query_terms=("gis", "agent"), required_concepts=(("geospatial ai", "gis"), ("autonomous agents",)), max_results=10)

    assert result == (relevant,)


def test_library_search_uses_latin_word_boundaries_and_returns_snippet(tmp_path):
    store = LocalArtifactStore(tmp_path / "artifacts")
    repository = SQLiteRuntimeRepository(tmp_path / "db" / "runtime.sqlite3", store)
    importer = LocalDocumentImporter(store)
    rows = []
    for name, text in (
        ("actual.md", "UReCoM is a user-relayed context manipulation attack."),
        ("nature.md", "Published in Nature Communications."),
        ("compute.md", "We ensure computational efficiency."),
    ):
        path = tmp_path / name
        path.write_text(text, encoding="utf-8")
        document = importer.import_path(path)
        rows.append({"relative_path": name, "document_id": document.document_id, "source_snapshot_id": document.source_snapshot.source_id, "segments_artifact_id": document.segments_artifact.artifact_id, "status": "knowledge_ready", "segment_count": len(document.segments)})
    rows.extend((
        {"relative_path": "indexing.pdf", "document_id": "document-indexing", "status": "indexing"},
        {"relative_path": "index-failed.pdf", "document_id": "document-index-failed", "status": "index_failed"},
    ))
    registry = tmp_path / "db" / "library-registry.json"
    registry.parent.mkdir(parents=True, exist_ok=True)
    registry.write_text(json.dumps(rows), encoding="utf-8")
    runtime = PassiveRuntime()
    app = create_app(repository, runtime, worker=WorkerLoop(runtime, interval_seconds=10))

    response = asyncio.run(route(app, "/api/v1/library/search")("UReCoM", "", 0, 30))

    assert response["total"] == 1
    assert response["items"][0]["title"] == "actual"
    assert "UReCoM" in response["items"][0]["match_snippet"]
    overview = asyncio.run(route(app, "/api/v1/library")())
    assert all("searchable_text" not in item for item in overview["items"])
    assert overview["processing"] == 1
    assert overview["failed"] == 1


def test_fulltext_import_becomes_knowledge_ready_only_after_incremental_index(tmp_path, monkeypatch):
    store = LocalArtifactStore(tmp_path / "artifacts")
    repository = SQLiteRuntimeRepository(tmp_path / "db" / "runtime.sqlite3", store)
    artifact = SimpleNamespace(artifact_id="artifact-fixture")
    segment = DocumentSegment("document-sha256-fixture:segment-0001", "document-sha256-fixture", 1, "UReCoM agent safety content. " * 20, {"page": 1})
    imported = SimpleNamespace(document_id="document-sha256-fixture", source_snapshot=SimpleNamespace(source_id="document-sha256-fixture"), source_artifact=artifact, segments_artifact=artifact, segments=(segment,))

    class FixtureFetcher:
        def __init__(self, *args, **kwargs):
            pass

        def fetch(self, candidates):
            return PdfFetchResult(b"%PDF fixture", "https://example.org/paper.pdf", "fixture", "artifact-fetch", 1)

    class FixtureImporter:
        def __init__(self, *args, **kwargs):
            pass

        def import_path(self, path, **kwargs):
            return imported

    class FixturePipeline:
        def __init__(self):
            self.documents = ()

        def add_documents(self, documents, **kwargs):
            self.documents = documents
            return {"status": "published", "added_count": len(documents), "embedding_artifacts": [{"request": "a", "response": "b"}]}

    monkeypatch.setattr("conflux_weave.server.OpenAccessPdfFetcher", FixtureFetcher)
    monkeypatch.setattr("conflux_weave.server.LocalDocumentImporter", FixtureImporter)
    pipeline = FixturePipeline()
    runtime = PassiveRuntime()
    app = create_app(repository, runtime, worker=WorkerLoop(runtime, interval_seconds=10), retrieval_pipeline=pipeline)

    response = asyncio.run(route(app, "/api/v1/library/papers")(LibraryPaperRequest(action="fulltext", paper=paper().as_dict())))

    assert response["status"] == "knowledge_ready"
    assert pipeline.documents[0].document_id.endswith("segment-0001")
    registry = json.loads((tmp_path / "db" / "library-registry.json").read_text(encoding="utf-8"))
    assert registry[0]["status"] == "knowledge_ready"
