"""Tests for P2 DocumentAgent, DocumentNote, HTML rendering, and Patch revision."""

from __future__ import annotations

import io
import json
from pathlib import Path
import xml.etree.ElementTree as ET
import zipfile
import pytest

from conflux_weave.document_agent import DocumentAgent
from conflux_weave.document_notes import (
    DocumentNote,
    NotePatch,
    NoteSection,
    NoteVersionConflictError,
    PatchOpType,
    PatchOperation,
    apply_patch,
    load_note_artifact,
)
from conflux_weave.documents import LocalDocumentImporter
from conflux_weave.runtime import SQLiteRuntimeRepository
from conflux_weave.runtime.artifacts import LocalArtifactStore
from conflux_weave.server import WorkerLoop, create_app


class _PassiveRuntime:
    executor_id = "passive-paper@v1"
    task_kinds = ("paper_discovery",)

    def work_once(self, *, now: str | None = None) -> None:
        return None


def build_test_app(tmp_path: Path):
    store = LocalArtifactStore(tmp_path / "artifacts")
    repository = SQLiteRuntimeRepository(
        tmp_path / "db" / "runtime.sqlite3", store, clock=lambda: "2026-09-08T12:00:00Z"
    )
    runtime = _PassiveRuntime()
    return create_app(
        repository,
        runtime,
        provider_configured=False,
        worker=WorkerLoop(runtime, interval_seconds=10),
    )


def _build_docx_bytes(heading: str, paragraphs: list[str]) -> bytes:
    """Build a minimal valid .docx package in memory."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # [Content_Types].xml
        zf.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            "</Types>",
        )
        # word/document.xml
        p_xmls = [
            f'<w:p xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            f'<w:pPr><w:pStyle w:val="Heading1"/></w:pPr>'
            f'<w:r><w:t>{heading}</w:t></w:r></w:p>'
        ]
        for p in paragraphs:
            p_xmls.append(
                f'<w:p xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                f'<w:r><w:t>{p}</w:t></w:r></w:p>'
            )
        doc_xml = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            f'<w:body>{"".join(p_xmls)}</w:body></w:document>'
        )
        zf.writestr("word/document.xml", doc_xml)
    return buf.getvalue()


def test_html_document_parsing(tmp_path: Path) -> None:
    source = tmp_path / "paper.html"
    source.write_text(
        """<!DOCTYPE html>
        <html>
        <head><title>分布式系统概述</title></head>
        <body>
          <h1>一、系统总体架构</h1>
          <p>本文提出了自适应容错调度机制，能够有效应对网络分区与节点异常崩溃。</p>
          <h2>二、核心共识协议</h2>
          <p>共识模块基于 Raft 协议进行了确定性日志回放优化，延迟降低 45%。</p>
        </body>
        </html>
        """,
        encoding="utf-8",
    )
    store = LocalArtifactStore(tmp_path / "artifacts")
    imported = LocalDocumentImporter(store, acquired_at="2026-09-08T00:00:00Z").import_path(source)

    assert imported.media_type == "text/html"
    assert len(imported.segments) == 2
    assert imported.segments[0].locator["type"] == "html_section"
    assert imported.segments[0].locator["heading"] == "一、系统总体架构"
    assert "自适应容错调度机制" in imported.segments[0].text
    assert imported.segments[1].locator["heading"] == "二、核心共识协议"
    assert "共识模块基于 Raft 协议" in imported.segments[1].text


def test_docx_document_parsing(tmp_path: Path) -> None:
    docx_bytes = _build_docx_bytes(
        "深度学习与知识图谱融合",
        [
            "图神经网络在学术发现与证据链溯源中展现出显著优势。",
            "实验证明联合嵌入比单一文本嵌入的召回率提高 18.2%。",
        ],
    )
    source = tmp_path / "analysis.docx"
    source.write_bytes(docx_bytes)

    store = LocalArtifactStore(tmp_path / "artifacts")
    imported = LocalDocumentImporter(store, acquired_at="2026-09-08T00:00:00Z").import_path(source)

    assert "wordprocessingml" in imported.media_type
    assert len(imported.segments) >= 1
    assert imported.segments[0].locator["type"] == "docx_paragraph"
    assert imported.segments[0].locator["heading"] == "深度学习与知识图谱融合"
    assert "图神经网络" in imported.segments[0].text


def test_document_agent_analysis_and_html_generation(tmp_path: Path) -> None:
    source = tmp_path / "report.md"
    source.write_text(
        "# 大模型智能体 Harness 架构设计\n\n"
        "Harness 为智能体提供执行沙箱、预算审计与确定性重试回路。\n\n"
        "## 执行控制层\n\n"
        "控制层负责捕获每一次工具调用与副作用，确保所有操作可回放、可解释。\n\n"
        "## 证据与引用闭包\n\n"
        "所有结论必须绑定至确切的 SourceSnapshot，不存在无依据的臆断。\n",
        encoding="utf-8",
    )
    store = LocalArtifactStore(tmp_path / "artifacts")
    imported = LocalDocumentImporter(store).import_path(source)

    agent = DocumentAgent(store)
    note = agent.analyze_document(imported, focus="上下文控制与沙箱隔离", title="Agent Harness 深度研读笔记")

    assert note.version == 1
    assert note.parent_note_id is None
    assert note.applied_patch_id is None
    assert "上下文控制" in note.executive_summary
    assert len(note.sections) >= 2

    # Markdown content validation
    assert "# Agent Harness 深度研读笔记" in note.markdown_content
    assert "版本 `v1`" in note.markdown_content
    assert "## 核心结论与摘要" in note.markdown_content

    # HTML content validation
    html = note.html_content
    assert "<!DOCTYPE html>" in html
    assert "Agent Harness 深度研读笔记" in html
    assert 'class="section-card"' in html
    assert 'class="summary-card"' in html
    # Strict zero-CDN offline guarantee
    assert "http://" not in html
    assert "https://" not in html

    # Verify storage & retrieval
    loaded = load_note_artifact(note.note_id, store)
    assert loaded.note_id == note.note_id
    assert loaded.title == note.title
    assert len(loaded.sections) == len(note.sections)


def test_note_patch_and_continuous_revision(tmp_path: Path) -> None:
    source = tmp_path / "paper.md"
    source.write_text(
        "# 多模态检索评测基准\n\n"
        "本文评测跨模态检索在学术图表定位上的表现。\n\n"
        "## 实验设定\n\n"
        "使用 3 种不同 embedding 方案进行对比。\n",
        encoding="utf-8",
    )
    store = LocalArtifactStore(tmp_path / "artifacts")
    imported = LocalDocumentImporter(store).import_path(source)
    agent = DocumentAgent(store)

    note_v1 = agent.analyze_document(imported, title="多模态评测基准研读笔记")
    assert note_v1.version == 1

    # Revision 1: Append section via natural instruction
    patch1, note_v2 = agent.revise_note(note_v1, "补充基线模型对比实验与消融分析")
    assert note_v2.version == 2
    assert note_v2.parent_note_id == note_v1.note_id
    assert note_v2.applied_patch_id == patch1.patch_id
    assert len(note_v2.sections) == len(note_v1.sections) + 1
    assert "补充章节" in note_v2.sections[-1].title

    # Revision 2: Explicit patch operations (replace section & update summary)
    ops = [
        PatchOperation(
            op=PatchOpType.UPDATE_SUMMARY,
            content="经过多轮消融实验验证，图文联合嵌入在学术检索中表现最优。",
        ),
        PatchOperation(
            op=PatchOpType.REPLACE_SECTION,
            section_index=0,
            title="实验设定 (深度修正版)",
            content="采用了零样本与少样本结合的最新评估基准。",
        ),
    ]
    patch2, note_v3 = agent.revise_note(note_v2, "更新实验设定并重写摘要", patch_ops=ops)
    assert note_v3.version == 3
    assert note_v3.parent_note_id == note_v2.note_id
    assert note_v3.applied_patch_id == patch2.patch_id
    assert "经过多轮消融实验验证" in note_v3.executive_summary
    assert note_v3.sections[0].title == "实验设定 (深度修正版)"

    # Precondition check: Version mismatch conflict rejection
    stale_patch = NotePatch(
        patch_id="stale-patch-001",
        target_note_id=note_v1.note_id,
        target_version=1,  # Note is now at version 3
        instruction="基于旧版本尝试覆盖",
        operations=(PatchOperation(op=PatchOpType.UPDATE_SUMMARY, content="脏写尝试"),),
        applied_at="2026-09-08T00:00:00Z",
    )
    with pytest.raises(NoteVersionConflictError, match="does not match current note version 3"):
        apply_patch(note_v3, stale_patch)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_document_notes_api_endpoints(tmp_path: Path) -> None:
    from httpx import ASGITransport, AsyncClient

    app = build_test_app(tmp_path)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Create source document
        doc_file = tmp_path / "sample_guide.md"
        doc_file.write_text(
            "# 智能体开发规范与最佳实践\n\n"
            "智能体应当始终保持确定性优先、状态持久化与证据可追溯。\n\n"
            "## 核心红线\n\n"
            "严禁引入外部未授权依赖与不可控网络调用。\n",
            encoding="utf-8",
        )

        # 2. POST /api/v1/documents/analyze
        res = await client.post(
            "/api/v1/documents/analyze",
            json={"path": str(doc_file), "focus": "合规与架构红线"},
        )
        assert res.status_code == 200, res.text
        data = res.json()
        note_id = data["note_id"]
        assert data["version"] == 1
        assert "智能体开发规范" in data["title"]
        assert len(data["sections"]) >= 1
        assert "http://" not in data["html_content"]
        assert "https://" not in data["html_content"]

        # 3. GET /api/v1/notes/{note_id}
        res_get = await client.get(f"/api/v1/notes/{note_id}")
        assert res_get.status_code == 200
        assert res_get.json()["note_id"] == note_id

        # 4. POST /api/v1/notes/{note_id}/patch
        patch_res = await client.post(
            f"/api/v1/notes/{note_id}/patch",
            json={"instruction": "补充工程治理与回滚策略", "target_version": 1},
        )
        assert patch_res.status_code == 200
        patched_data = patch_res.json()
        new_note_id = patched_data["note_id"]
        assert patched_data["version"] == 2
        assert patched_data["parent_note_id"] == note_id
        assert len(patched_data["sections"]) == len(data["sections"]) + 1

        # 5. Version conflict rejection on API
        conflict_res = await client.post(
            f"/api/v1/notes/{note_id}/patch",
            json={"instruction": "并发覆盖冲突测试", "target_version": 99},
        )
        assert conflict_res.status_code == 409
        assert conflict_res.json()["code"] == "version_conflict"

        # 6. GET /api/v1/notes/{new_note_id}/revisions
        rev_res = await client.get(f"/api/v1/notes/{new_note_id}/revisions")
        assert rev_res.status_code == 200
        rev_data = rev_res.json()
        assert len(rev_data["revisions"]) >= 2
        assert rev_data["revisions"][0]["version"] == 1
        assert rev_data["revisions"][1]["version"] == 2

        # 7. Non-existent note returns 404
        not_found_res = await client.get("/api/v1/notes/note-non-existent")
        assert not_found_res.status_code == 404

        # 8. Non-existent document analyze returns 404
        doc_not_found = await client.post(
            "/api/v1/documents/analyze",
            json={"path": "non-existent-doc.md"},
        )
        assert doc_not_found.status_code == 404
        assert doc_not_found.json()["code"] == "document_not_found"


def test_english_document_analysis_generates_authoritative_chinese_notes(tmp_path: Path) -> None:
    source = tmp_path / "attention.md"
    source.write_text(
        "# Attention Is All You Need\n\n"
        "The dominant sequence transduction models are based on complex recurrent or convolutional neural networks.\n\n"
        "## Introduction\n\n"
        "Recurrent neural networks factor computation along the symbol positions of the input and output sequences.\n\n"
        "## Model Architecture\n\n"
        "The Transformer follows this overall architecture using stacked self-attention and point-wise, fully connected layers.\n\n"
        "## Experiments\n\n"
        "On the WMT 2014 English-to-German translation task, the big transformer model achieves state of the art results.\n",
        encoding="utf-8",
    )
    store = LocalArtifactStore(tmp_path / "artifacts")
    imported = LocalDocumentImporter(store).import_path(source)
    agent = DocumentAgent(store)

    note = agent.analyze_document(imported, focus="自注意力与全局长程依赖建模")
    assert note.version == 1
    # Verify executive summary is in Chinese
    assert "自注意力与全局长程依赖建模" in note.executive_summary
    assert "核心理论机制" in note.executive_summary

    # Verify headings are translated into authoritative academic Chinese
    titles = [s.title for s in note.sections]
    assert any("研究背景" in t for t in titles)
    assert any("模型架构" in t or "系统架构" in t for t in titles)
    assert any("实验验证" in t or "性能评测" in t for t in titles)

    # Verify section content has structured Chinese analysis
    for s in note.sections:
        assert len(s.content) > 20

    # Verify key concepts contain authoritative bilingual definitions
    concept_terms = [c["term"] for c in note.key_concepts]
    assert any("Transformer" in t for t in concept_terms)
    assert any("Self-Attention" in t or "自注意力" in t for t in concept_terms)

    # Verify technical methods, innovations, and comparison tables in v1 note
    assert any("技术方法与系统架构深度解析" in s.content for s in note.sections)
    assert any("核心技术创新点与范式突破" in s.content for s in note.sections)
    assert any("技术机制与架构对比矩阵" in s.content for s in note.sections)

    # Verify Markdown table parsed into HTML <table> in HTML view
    assert "<table" in note.html_content
    assert "<thead" in note.html_content
    assert "<th" in note.html_content
    assert "<tbody" in note.html_content
    assert "<td" in note.html_content
    assert "note-table-wrapper" in note.html_content
    assert "note-table" in note.html_content


def test_markdown_table_and_rich_html_rendering(tmp_path: Path) -> None:
    """Test that Markdown tables, lists, and code blocks in note sections are rendered to semantic HTML."""
    from conflux_weave.document_notes import NoteSection, markdown_to_html

    sample_md = (
        "**【核心机制对比】**\n\n"
        "| 对比指标 | 传统方案 | 本文方案 | 演进收益 |\n"
        "| :--- | :---: | ---: | :--- |\n"
        "| 计算复杂度 | O(n) | O(1) | 并行效率大幅提升 |\n"
        "| 内存占用 | 静态分配 | 动态分块 | 显存节约 60% |\n\n"
        "- 亮点一：完全消除时序依赖\n"
        "- 亮点二：支持超长上下文\n\n"
        "> 权威引言：摒弃递归，拥抱全局自注意力。\n"
    )

    html_out = markdown_to_html(sample_md)
    assert "<table" in html_out
    assert "<thead" in html_out
    assert "<th" in html_out
    assert "<tbody" in html_out
    assert "<td" in html_out
    assert "note-table-wrapper" in html_out
    assert "note-table" in html_out
    assert "<ul>" in html_out
    assert "<li>" in html_out
    assert "<blockquote>" in html_out

    store = LocalArtifactStore(tmp_path / "artifacts")
    note = DocumentNote(
        note_id="note-custom-table-test-v1",
        document_id="doc-custom-table-001",
        title="表格渲染专项测试",
        version=1,
        executive_summary="验证 Markdown 表格向语义化 HTML 转换。",
        sections=(
            NoteSection(
                section_id="sec-001",
                title="核心机制评测",
                level=2,
                content=sample_md,
            ),
        ),
    )
    assert "<table" in note.html_content
    assert "note-table-wrapper" in note.html_content
    assert "O(1)" in note.html_content


