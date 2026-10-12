"""REST API endpoint and end-to-end workflow tests for Paper Reading V2."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from starlette.testclient import TestClient

from conflux_weave.paper_reading.spec import PaperParadigm
from conflux_weave.paper_reading.workflow import run_paper_reading_v2
from conflux_weave.runtime import LocalArtifactStore
from conflux_weave.runtime.sqlite import SQLiteRuntimeRepository
from conflux_weave.server import create_app


def test_paper_reading_workflow_end_to_end(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path / "artifacts")
    raw_sample = """
    Abstract: We propose the Transformer, a novel neural network architecture based solely on attention mechanisms.
    
    Model Architecture: The Transformer follows an encoder-decoder structure.
    Figure 1: The Transformer model architecture.
    
    Experiments: We evaluate our model on WMT 2014 English-to-German and English-to-French translation tasks.
    Table 1: The Transformer achieves 28.4 BLEU on English-to-German.
    Figure 2: Ablation study showing the effect of varying number of attention heads.
    
    References
    [1] Bahdanau, D., Cho, K., Bengio, Y., "Neural machine translation by jointly learning to align and translate", ICLR, 2015.
    [2] Vaswani, A., et al., "Attention is all you need", NeurIPS, 2017.
    """

    # 1. Without PDF: zero fake synthetic visual slices (evidence fidelity)
    report = run_paper_reading_v2(
        paper_id="test_transformer_p2",
        title="Attention Is All You Need",
        raw_text=raw_sample,
        artifact_store=store,
        allow_network=False,
    )

    assert report.paper_id == "test_transformer_p2"
    assert report.title == "Attention Is All You Need"
    assert report.paradigm == PaperParadigm.ALGORITHM_ARCHITECTURE
    assert len(report.outline.chapters) == 5
    assert len(report.visual_slices) == 0
    assert report.citation_graph is not None
    assert len(report.citation_graph.backward_foundations) >= 1
    assert "```mermaid" in report.markdown_content

    # 2. With real PDF: verifies physical extraction, real bbox, and disk storage
    import fitz
    pdf_path = tmp_path / "sample_paper.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 50), "Figure 1: The Transformer model architecture.")
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 200, 200), 0)
    pix.set_rect(fitz.IRect(0, 0, 200, 200), (100, 150, 200))
    page.insert_image(fitz.Rect(50, 100, 250, 300), pixmap=pix)
    doc.save(str(pdf_path))
    doc.close()

    report_pdf = run_paper_reading_v2(
        paper_id="test_transformer_with_pdf",
        title="Attention Is All You Need",
        raw_text=raw_sample,
        pdf_path=pdf_path,
        artifact_store=store,
        allow_network=False,
    )
    assert len(report_pdf.visual_slices) >= 1
    assert report_pdf.visual_slices[0].bbox is not None
    assert (store.root / "visual_slices" / Path(report_pdf.visual_slices[0].image_rel_path).name).is_file()
    assert "> [!NOTE]" in report_pdf.markdown_content
    assert "图表物理切片深度解析" in report_pdf.markdown_content


def test_paper_reading_api_endpoints(tmp_path: Path) -> None:
    db_file = tmp_path / "paper_reading_api.sqlite3"
    store = LocalArtifactStore(tmp_path / "artifacts")
    repository = SQLiteRuntimeRepository(db_file, store)
    orchestrator = SimpleNamespace()
    app = create_app(repository, orchestrator)
    client = TestClient(app)

    # 1. Test classify endpoint
    resp_classify = client.post(
        "/api/v1/paper-reading/classify",
        json={
            "title": "A Systematic Survey on Diffusion Models: Taxonomy and Frontiers",
            "abstract": "We categorize diffusion models into a multi-tiered taxonomy and discuss future directions.",
            "section_headings": ["Overview", "Taxonomy", "Applications", "Challenges"],
        },
    )
    assert resp_classify.status_code == 200, resp_classify.text
    data_classify = resp_classify.json()
    assert data_classify["paradigm"] == "survey_taxonomy"
    assert data_classify["confidence"] >= 0.7
    assert len(data_classify["chapters"]) == 5

    # 2. Test citation-graph endpoint (offline fallback)
    resp_graph = client.post(
        "/api/v1/paper-reading/citation-graph",
        json={
            "title": "Attention Is All You Need",
            "raw_text": (
                "References\n"
                "[1] Bahdanau et al., \"Neural machine translation\", 2015.\n"
                "[2] Ba et al., \"Layer normalization\", 2016."
            ),
            "allow_network": False,
        },
    )
    assert resp_graph.status_code == 200, resp_graph.text
    data_graph = resp_graph.json()
    assert data_graph["seed_paper_title"] == "Attention Is All You Need"
    assert len(data_graph["backward_foundations"]) >= 1
    assert "```mermaid" in data_graph["mermaid_graph"]

    # 3. Test cross-compare endpoint
    resp_compare = client.post(
        "/api/v1/paper-reading/cross-compare",
        json={
            "papers": [
                {"paper_id": "p1", "title": "Attention Is All You Need", "year": 2017},
                {"paper_id": "p2", "title": "FlashAttention", "year": 2022},
                {"paper_id": "p3", "title": "Mamba", "year": 2023},
            ]
        },
    )
    assert resp_compare.status_code == 200, resp_compare.text
    data_compare = resp_compare.json()
    assert len(data_compare["entities"]) == 3
    assert "| 对比维度 |" in data_compare["dimension_matrix_markdown"]
    assert "```mermaid" in data_compare["lineage_mermaid_timeline"]
    assert "跨文献技术演进" in data_compare["synthesis_narrative"]

    # 4. Test read endpoint
    resp_read = client.post(
        "/api/v1/paper-reading/read",
        json={
            "paper_id": "api_test_read",
            "title": "Attention Is All You Need",
            "raw_text": "We introduce the Transformer, replacing RNNs with self-attention.",
            "allow_network": False,
        },
    )
    assert resp_read.status_code == 200, resp_read.text
    data_read = resp_read.json()
    assert data_read["paper_id"] == "api_test_read"
    assert data_read["paradigm"] == "algorithm_architecture"
    assert len(data_read["outline"]["chapters"]) == 5
    assert len(data_read["markdown_content"]) > 1000
    assert len(data_read["visual_slices"]) == 0
