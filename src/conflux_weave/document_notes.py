"""Document note data structures, HTML view renderer, and patch revision engine for P2."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
import html
import json
import re
from typing import Any

from conflux_weave.evidence import ArtifactRef
from conflux_weave.runtime.artifacts import LocalArtifactStore

NOTE_SCHEMA_VERSION = "conflux-weave.document-note.v1"
PATCH_SCHEMA_VERSION = "conflux-weave.document-note-patch.v1"


class NoteVersionConflictError(ValueError):
    """Raised when a patch target_version does not match current note version."""


class PatchOpType(StrEnum):
    REPLACE_SECTION = "replace_section"
    INSERT_SECTION = "insert_section"
    DELETE_SECTION = "delete_section"
    APPEND_SECTION = "append_section"
    UPDATE_SUMMARY = "update_summary"
    UPDATE_CONCEPTS = "update_concepts"
    UPDATE_METADATA = "update_metadata"


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class NoteSection:
    section_id: str
    title: str
    level: int
    content: str
    source_segments: tuple[str, ...] = ()
    citations: tuple[str, ...] = ()
    asset_refs: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "section_id": self.section_id,
            "title": self.title,
            "level": self.level,
            "content": self.content,
            "source_segments": list(self.source_segments),
            "citations": list(self.citations),
            "asset_refs": list(self.asset_refs),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> NoteSection:
        return cls(
            section_id=str(data.get("section_id", "")),
            title=str(data.get("title", "")),
            level=int(data.get("level", 2)),
            content=str(data.get("content", "")),
            source_segments=tuple(str(s) for s in data.get("source_segments", ())),
            citations=tuple(str(c) for c in data.get("citations", ())),
            asset_refs=tuple(str(a) for a in data.get("asset_refs", ())),
        )


@dataclass(frozen=True, slots=True)
class PatchOperation:
    op: PatchOpType
    target_section_id: str | None = None
    target_title: str | None = None
    section_index: int | None = None
    title: str | None = None
    level: int = 2
    content: str | None = None
    source_segments: tuple[str, ...] = ()
    citations: tuple[str, ...] = ()
    asset_refs: tuple[str, ...] = ()
    key_concepts: tuple[dict[str, str], ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"op": str(self.op)}
        for name in (
            "target_section_id",
            "target_title",
            "section_index",
            "title",
            "level",
            "content",
        ):
            val = getattr(self, name)
            if val is not None:
                result[name] = val
        if self.source_segments:
            result["source_segments"] = list(self.source_segments)
        if self.citations:
            result["citations"] = list(self.citations)
        if self.asset_refs:
            result["asset_refs"] = list(self.asset_refs)
        if self.key_concepts:
            result["key_concepts"] = [dict(c) for c in self.key_concepts]
        if self.metadata:
            result["metadata"] = dict(self.metadata)
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PatchOperation:
        raw_op = str(data.get("op", "append_section"))
        op_enum = PatchOpType(raw_op) if raw_op in PatchOpType._value2member_map_ else PatchOpType.APPEND_SECTION
        return cls(
            op=op_enum,
            target_section_id=data.get("target_section_id"),
            target_title=data.get("target_title"),
            section_index=data.get("section_index"),
            title=data.get("title"),
            level=int(data.get("level", 2)),
            content=data.get("content"),
            source_segments=tuple(str(s) for s in data.get("source_segments", ())),
            citations=tuple(str(c) for c in data.get("citations", ())),
            asset_refs=tuple(str(a) for a in data.get("asset_refs", ())),
            key_concepts=tuple(dict(c) for c in data.get("key_concepts", ())),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass(frozen=True, slots=True)
class NotePatch:
    patch_id: str
    target_note_id: str
    target_version: int
    instruction: str
    operations: tuple[PatchOperation, ...]
    applied_at: str
    producer_id: str = "DocumentAgent"
    schema_version: str = PATCH_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "patch_id": self.patch_id,
            "target_note_id": self.target_note_id,
            "target_version": self.target_version,
            "instruction": self.instruction,
            "producer_id": self.producer_id,
            "applied_at": self.applied_at,
            "operations": [op.to_dict() for op in self.operations],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> NotePatch:
        return cls(
            patch_id=str(data.get("patch_id", "")),
            target_note_id=str(data.get("target_note_id", "")),
            target_version=int(data.get("target_version", 1)),
            instruction=str(data.get("instruction", "")),
            applied_at=str(data.get("applied_at", _utc_now())),
            producer_id=str(data.get("producer_id", "DocumentAgent")),
            schema_version=str(data.get("schema_version", PATCH_SCHEMA_VERSION)),
            operations=tuple(PatchOperation.from_dict(op) for op in data.get("operations", ())),
        )


@dataclass(frozen=True, slots=True)
class DocumentNote:
    note_id: str
    document_id: str
    title: str
    version: int
    executive_summary: str
    sections: tuple[NoteSection, ...]
    parent_note_id: str | None = None
    applied_patch_id: str | None = None
    key_concepts: tuple[dict[str, str], ...] = ()
    visual_assets: tuple[dict[str, Any], ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
    markdown_content: str = ""
    html_content: str = ""
    created_at: str = ""
    schema_version: str = NOTE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        created = self.created_at or _utc_now()
        object.__setattr__(self, "created_at", created)
        if not self.markdown_content:
            object.__setattr__(self, "markdown_content", self.render_markdown())
        if not self.html_content:
            object.__setattr__(self, "html_content", self.render_html())

    def render_markdown(self) -> str:
        lines: list[str] = [
            f"# {self.title}",
            "",
            f"> 权威文献研读笔记 · 版本 `v{self.version}` · 来源 `{self.document_id}`",
            f"> 生成时间: {self.created_at}",
        ]
        if self.parent_note_id:
            lines.append(f"> 基于版本: `{self.parent_note_id}` (Patch: `{self.applied_patch_id or 'none'}`)")
        lines.append("")

        if self.executive_summary:
            lines.extend(["## 核心结论与摘要", "", self.executive_summary, ""])

        if self.key_concepts:
            lines.extend(["## 核心术语与概念", ""])
            for concept in self.key_concepts:
                term = concept.get("term") or concept.get("name") or "概念"
                definition = concept.get("definition") or concept.get("desc") or ""
                lines.append(f"- **{term}**: {definition}")
            lines.append("")

        if self.sections:
            lines.extend(["## 研读分析", ""])
            for sec in self.sections:
                hashes = "#" * max(2, min(6, sec.level + 1))
                lines.extend([f"{hashes} {sec.title}", "", sec.content, ""])
                if sec.citations:
                    c_list = ", ".join(f"`{c}`" for c in sec.citations)
                    lines.extend([f"*引用来源: {c_list}*", ""])
                if sec.asset_refs:
                    a_list = ", ".join(f"`{a}`" for a in sec.asset_refs)
                    lines.extend([f"*关联视觉资产: {a_list}*", ""])

        return "\n".join(lines).strip() + "\n"

    def render_html(self) -> str:
        return NoteHtmlRenderer.render(self)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "note_id": self.note_id,
            "document_id": self.document_id,
            "title": self.title,
            "version": self.version,
            "parent_note_id": self.parent_note_id,
            "applied_patch_id": self.applied_patch_id,
            "executive_summary": self.executive_summary,
            "created_at": self.created_at,
            "metadata": dict(self.metadata),
            "key_concepts": [dict(c) for c in self.key_concepts],
            "visual_assets": [dict(a) for a in self.visual_assets],
            "sections": [s.to_dict() for s in self.sections],
            "markdown_content": self.markdown_content,
            "html_content": self.html_content,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DocumentNote:
        note = cls(
            note_id=str(data.get("note_id", "")),
            document_id=str(data.get("document_id", "")),
            title=str(data.get("title", "")),
            version=int(data.get("version", 1)),
            parent_note_id=data.get("parent_note_id"),
            applied_patch_id=data.get("applied_patch_id"),
            executive_summary=str(data.get("executive_summary", "")),
            created_at=str(data.get("created_at", "")),
            metadata=dict(data.get("metadata", {})),
            key_concepts=tuple(dict(c) for c in data.get("key_concepts", ())),
            visual_assets=tuple(dict(a) for a in data.get("visual_assets", ())),
            sections=tuple(NoteSection.from_dict(s) for s in data.get("sections", ())),
            markdown_content=str(data.get("markdown_content", "")),
            html_content=str(data.get("html_content", "")),
            schema_version=str(data.get("schema_version", NOTE_SCHEMA_VERSION)),
        )
        has_table_in_sections = any(
            bool(re.search(r"\|[^\n]+\|[^\n]+\|\s*\n\s*\|[\s:\-]+\|", s.content))
            for s in note.sections
        )
        if (has_table_in_sections and "<table" not in note.html_content) or not note.html_content:
            object.__setattr__(note, "html_content", note.render_html())
        return note


def _pure_python_gfm_to_html(md_text: str) -> str:
    """Zero-dependency pure-Python fallback for GFM tables, code blocks, lists, and typography."""
    lines = md_text.split("\n")
    out: list[str] = []
    i = 0
    in_code_block = False
    code_block_lines: list[str] = []
    code_lang = ""

    while i < len(lines):
        line = lines[i]

        # Fenced code block
        if line.strip().startswith("```"):
            if not in_code_block:
                in_code_block = True
                code_lang = line.strip()[3:].strip()
                code_block_lines = []
                i += 1
                continue
            else:
                in_code_block = False
                code_content = html.escape("\n".join(code_block_lines))
                cls_attr = f' class="language-{html.escape(code_lang)}"' if code_lang else ""
                out.append(f'<pre><code{cls_attr}>{code_content}</code></pre>')
                i += 1
                continue

        if in_code_block:
            code_block_lines.append(line)
            i += 1
            continue

        # GFM Table detection: current line has '|' and next line is separator row
        if "|" in line and i + 1 < len(lines) and re.match(r"^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)+\|?\s*$", lines[i + 1]):
            header_line = line.strip().strip("|")
            headers = [c.strip() for c in header_line.split("|")]
            sep_line = lines[i + 1].strip().strip("|")
            aligns: list[str] = []
            for col in sep_line.split("|"):
                col = col.strip()
                if col.startswith(":") and col.endswith(":"):
                    aligns.append("center")
                elif col.endswith(":"):
                    aligns.append("right")
                else:
                    aligns.append("left")
            rows: list[list[str]] = []
            j = i + 2
            while j < len(lines) and "|" in lines[j] and lines[j].strip():
                row_line = lines[j].strip().strip("|")
                cells = [c.strip() for c in row_line.split("|")]
                rows.append(cells)
                j += 1
            i = j
            th_cells = "".join(
                f'<th style="text-align: {aligns[k] if k < len(aligns) else "left"};">{html.escape(h)}</th>'
                for k, h in enumerate(headers)
            )
            tr_rows: list[str] = []
            for r in rows:
                tds = "".join(
                    f'<td style="text-align: {aligns[k] if k < len(aligns) else "left"};">{html.escape(r[k]) if k < len(r) else ""}</td>'
                    for k in range(len(headers))
                )
                tr_rows.append(f"<tr>{tds}</tr>")
            table_html = (
                f'<div class="note-table-wrapper">'
                f'<table class="note-table">'
                f"<thead><tr>{th_cells}</tr></thead>"
                f"<tbody>{''.join(tr_rows)}</tbody>"
                f"</table></div>"
            )
            out.append(table_html)
            continue

        # Blockquote
        if line.strip().startswith(">"):
            quote_lines = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                quote_lines.append(re.sub(r"^\s*>\s?", "", lines[i]))
                i += 1
            q_text = html.escape("\n".join(quote_lines))
            out.append(f"<blockquote><p>{q_text.replace(chr(10), '<br>')}</p></blockquote>")
            continue

        # Lists (unordered)
        if re.match(r"^\s*[\-\*]\s+", line):
            list_items = []
            while i < len(lines) and re.match(r"^\s*[\-\*]\s+", lines[i]):
                item_text = re.sub(r"^\s*[\-\*]\s+", "", lines[i])
                item_text = html.escape(item_text)
                item_text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", item_text)
                item_text = re.sub(r"`(.+?)`", r"<code>\1</code>", item_text)
                list_items.append(f"<li>{item_text}</li>")
                i += 1
            out.append(f"<ul>{''.join(list_items)}</ul>")
            continue

        # Headings (h3, h4, h5, h6)
        h_match = re.match(r"^(#{1,6})\s+(.*)$", line.strip())
        if h_match:
            level = len(h_match.group(1))
            h_tag = f"h{min(6, level + 1)}"
            h_text = html.escape(h_match.group(2))
            out.append(f"<{h_tag}>{h_text}</{h_tag}>")
            i += 1
            continue

        out.append(line)
        i += 1

    # Paragraph grouping for remaining text lines
    rendered_blocks: list[str] = []
    cur_p: list[str] = []

    def flush_p():
        if cur_p:
            p_text = " ".join(cur_p).strip()
            if p_text:
                p_text = html.escape(p_text)
                p_text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", p_text)
                p_text = re.sub(r"`(.+?)`", r"<code>\1</code>", p_text)
                rendered_blocks.append(f"<p>{p_text}</p>")
            cur_p.clear()

    for item in out:
        if (
            item.startswith('<div class="note-table-wrapper">')
            or item.startswith("<pre>")
            or item.startswith("<blockquote>")
            or item.startswith("<ul>")
            or re.match(r"^<h\d>", item)
        ):
            flush_p()
            rendered_blocks.append(item)
        elif not item.strip():
            flush_p()
        else:
            cur_p.append(item)
    flush_p()

    return "\n".join(rendered_blocks)


def markdown_to_html(md_text: str) -> str:
    """Robust Markdown-to-HTML converter supporting GFM tables, code blocks, lists, and typography."""
    if not md_text or not md_text.strip():
        return ""

    try:
        import markdown

        rendered = markdown.markdown(
            md_text,
            extensions=["tables", "fenced_code", "sane_lists"],
            output_format="html5",
        )
        # Ensure tables have .note-table and are wrapped in .note-table-wrapper
        rendered = re.sub(r"<table(?![^>]*class=)", '<table class="note-table"', rendered)

        def _wrap_table(match: re.Match[str]) -> str:
            table_html = match.group(0)
            return f'<div class="note-table-wrapper">\n{table_html}\n</div>'

        rendered = re.sub(r"(?<!<div class=\"note-table-wrapper\">\n)<table[\s\S]*?</table>", _wrap_table, rendered)
        return rendered
    except Exception:
        return _pure_python_gfm_to_html(md_text)


class NoteHtmlRenderer:
    """Zero-external-dependency, self-contained HTML renderer for DocumentNote."""

    @staticmethod
    def render(note: DocumentNote) -> str:
        esc_title = html.escape(note.title)
        esc_doc_id = html.escape(note.document_id)
        rendered_summary = markdown_to_html(note.executive_summary)

        toc_items = []
        section_blocks = []
        for idx, sec in enumerate(note.sections, start=1):
            sec_anchor = f"sec-{idx}"
            esc_sec_title = html.escape(sec.title)
            rendered_sec_content = markdown_to_html(sec.content)
            toc_items.append(f'<li><a href="#{sec_anchor}">{esc_sec_title}</a></li>')

            badges = []
            if sec.citations:
                for c in sec.citations:
                    badges.append(f'<span class="badge cite">引文: {html.escape(c)}</span>')
            if sec.asset_refs:
                for a in sec.asset_refs:
                    badges.append(f'<span class="badge asset">图表: {html.escape(a)}</span>')
            badge_html = f'<div class="badge-row">{" ".join(badges)}</div>' if badges else ""

            section_blocks.append(f"""
            <article class="section-card" id="{sec_anchor}">
              <div class="section-header">
                <span class="section-num">{idx:02d}</span>
                <h3 class="section-title">{esc_sec_title}</h3>
              </div>
              <div class="section-body">
                {rendered_sec_content}
              </div>
              {badge_html}
            </article>
            """)

        concept_cards = []
        for c in note.key_concepts:
            term = html.escape(c.get("term") or c.get("name") or "")
            desc = html.escape(c.get("definition") or c.get("desc") or "")
            if term:
                concept_cards.append(f"""
                <div class="concept-card">
                  <strong class="concept-term">{term}</strong>
                  <span class="concept-desc">{desc}</span>
                </div>
                """)

        toc_nav = f'<nav class="note-toc"><div class="toc-title">章节导轨</div><ul>{"".join(toc_items)}</ul></nav>' if toc_items else ""
        concepts_block = f'<section class="concepts-grid">{"".join(concept_cards)}</section>' if concept_cards else ""

        parent_info = ""
        if note.parent_note_id:
            parent_info = f'<span class="meta-item">父版本: <code>{html.escape(note.parent_note_id)}</code></span>'

        return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{esc_title} - 研读笔记 v{note.version}</title>
  <style>
    :root {{
      --ink: #0f172a;
      --paper: #ffffff;
      --chalk: #f8fafc;
      --rule: #e2e8f0;
      --muted: #64748b;
      --accent: #0284c7;
      --moss: #0d9488;
      --amber: #d97706;
      --radius: 8px;
    }}
    @media (prefers-color-scheme: dark) {{
      :root {{
        --ink: #f8fafc;
        --paper: #090d16;
        --chalk: #131b2e;
        --rule: #1e293b;
        --muted: #94a3b8;
        --accent: #38bdf8;
        --moss: #14b8a6;
      }}
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Inter", sans-serif;
      color: var(--ink);
      background: var(--paper);
      line-height: 1.65;
      padding: 32px 20px;
    }}
    .note-container {{
      max-width: 920px;
      margin: 0 auto;
      display: flex;
      flex-direction: column;
      gap: 28px;
    }}
    .note-header {{
      border-bottom: 1px solid var(--rule);
      padding-bottom: 20px;
    }}
    .note-tag {{
      display: inline-block;
      font-size: 11px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      color: var(--moss);
      background: color-mix(in srgb, var(--moss) 12%, transparent);
      padding: 3px 8px;
      border-radius: 4px;
      margin-bottom: 8px;
    }}
    .note-title {{
      font-size: 26px;
      font-weight: 700;
      letter-spacing: -0.02em;
      margin-bottom: 12px;
      line-height: 1.3;
    }}
    .note-meta {{
      display: flex;
      flex-wrap: wrap;
      gap: 12px 18px;
      font-size: 13px;
      color: var(--muted);
    }}
    .meta-item code {{
      font-family: "JetBrains Mono", Consolas, monospace;
      font-size: 12px;
      background: var(--chalk);
      padding: 2px 5px;
      border-radius: 4px;
    }}
    .summary-card {{
      background: var(--chalk);
      border: 1px solid var(--rule);
      border-left: 4px solid var(--moss);
      border-radius: var(--radius);
      padding: 18px 22px;
    }}
    .summary-title {{
      font-size: 14px;
      font-weight: 700;
      color: var(--moss);
      margin-bottom: 8px;
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }}
    .summary-card p {{
      font-size: 15px;
      line-height: 1.7;
    }}
    .concepts-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
      gap: 12px;
    }}
    .concept-card {{
      background: var(--chalk);
      border: 1px solid var(--rule);
      border-radius: 6px;
      padding: 12px 14px;
      display: flex;
      flex-direction: column;
      gap: 4px;
    }}
    .concept-term {{
      font-size: 13px;
      font-weight: 600;
      color: var(--accent);
    }}
    .concept-desc {{
      font-size: 12px;
      color: var(--muted);
      line-height: 1.5;
    }}
    .note-layout {{
      display: grid;
      grid-template-columns: 1fr;
      gap: 24px;
    }}
    .section-card {{
      background: var(--paper);
      border: 1px solid var(--rule);
      border-radius: var(--radius);
      padding: 20px 22px;
      margin-bottom: 16px;
    }}
    .section-header {{
      display: flex;
      align-items: baseline;
      gap: 10px;
      border-bottom: 1px solid var(--rule);
      padding-bottom: 10px;
      margin-bottom: 14px;
    }}
    .section-num {{
      font-family: monospace;
      font-size: 13px;
      color: var(--muted);
    }}
    .section-title {{
      font-size: 17px;
      font-weight: 600;
      letter-spacing: -0.01em;
    }}
    .section-body {{
      font-size: 14px;
      line-height: 1.75;
      color: var(--ink);
    }}
    .section-body p {{
      font-size: 14px;
      line-height: 1.75;
      margin-bottom: 12px;
    }}
    .section-body p:last-child {{
      margin-bottom: 0;
    }}
    .section-body strong {{
      font-weight: 600;
      color: var(--ink);
    }}
    .section-body em {{
      font-style: italic;
    }}
    .section-body h4 {{
      font-size: 15px;
      font-weight: 600;
      margin: 18px 0 8px;
      color: var(--ink);
    }}
    .section-body h5 {{
      font-size: 14px;
      font-weight: 600;
      margin: 14px 0 6px;
      color: var(--ink);
    }}
    .section-body ul, .section-body ol {{
      margin: 10px 0 14px 22px;
      padding: 0;
    }}
    .section-body li {{
      margin-bottom: 5px;
      line-height: 1.65;
    }}
    .section-body blockquote {{
      margin: 14px 0;
      padding: 10px 16px;
      background: var(--chalk);
      border-left: 3.5px solid var(--moss);
      border-radius: 0 6px 6px 0;
      color: var(--muted);
      font-size: 13.5px;
      line-height: 1.65;
    }}
    .section-body blockquote p {{
      margin-bottom: 6px;
    }}
    .section-body blockquote p:last-child {{
      margin-bottom: 0;
    }}
    .section-body pre {{
      background: var(--chalk);
      border: 1px solid var(--rule);
      border-radius: 6px;
      padding: 12px 14px;
      overflow-x: auto;
      margin: 14px 0;
      font-family: "JetBrains Mono", Consolas, monospace;
      font-size: 12.5px;
      line-height: 1.5;
    }}
    .section-body code {{
      font-family: "JetBrains Mono", Consolas, monospace;
      font-size: 12.5px;
      background: var(--chalk);
      border: 1px solid var(--rule);
      padding: 1px 5px;
      border-radius: 4px;
    }}
    .note-table-wrapper {{
      margin: 16px 0;
      overflow-x: auto;
      border: 1px solid var(--rule);
      border-radius: var(--radius);
      background: var(--paper);
    }}
    .note-table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 13px;
      line-height: 1.55;
      text-align: left;
    }}
    .note-table th, .note-table td {{
      padding: 10px 14px;
      border-bottom: 1px solid var(--rule);
      border-right: 1px solid var(--rule);
      vertical-align: top;
    }}
    .note-table th:last-child, .note-table td:last-child {{
      border-right: none;
    }}
    .note-table tr:last-child td {{
      border-bottom: none;
    }}
    .note-table th {{
      background: var(--chalk);
      font-weight: 600;
      color: var(--ink);
      white-space: nowrap;
    }}
    .note-table tbody tr:nth-child(even) {{
      background: color-mix(in srgb, var(--chalk) 65%, transparent);
    }}
    .note-table tbody tr:hover {{
      background: color-mix(in srgb, var(--accent) 7%, transparent);
    }}
    .badge-row {{
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      margin-top: 12px;
      padding-top: 10px;
      border-top: 1px dashed var(--rule);
    }}
    .badge {{
      font-size: 11px;
      padding: 2px 7px;
      border-radius: 4px;
      font-family: monospace;
    }}
    .badge.cite {{
      background: color-mix(in srgb, var(--accent) 12%, transparent);
      color: var(--accent);
    }}
    .badge.asset {{
      background: color-mix(in srgb, var(--amber) 12%, transparent);
      color: var(--amber);
    }}
  </style>
</head>
<body>
  <div class="note-container">
    <header class="note-header">
      <span class="note-tag">权威研读笔记 · v{note.version}</span>
      <h1 class="note-title">{esc_title}</h1>
      <div class="note-meta">
        <span class="meta-item">来源: <code>{esc_doc_id}</code></span>
        <span class="meta-item">时间: {html.escape(note.created_at)}</span>
        {parent_info}
      </div>
    </header>

    <section class="summary-card">
      <h2 class="summary-title">核心结论与摘要</h2>
      <div class="summary-body">{rendered_summary}</div>
    </section>

    {concepts_block}

    <main class="note-layout">
      {toc_nav}
      <section class="sections-list">
        {"".join(section_blocks)}
      </section>
    </main>
  </div>
</body>
</html>
"""


def apply_patch(base_note: DocumentNote, patch: NotePatch) -> DocumentNote:
    """Deterministically apply a NotePatch to a base DocumentNote and yield the next version."""
    if patch.target_version != base_note.version:
        raise NoteVersionConflictError(
            f"Patch target_version {patch.target_version} does not match current note version {base_note.version}"
        )

    sections = list(base_note.sections)
    summary = base_note.executive_summary
    concepts = list(base_note.key_concepts)
    metadata = dict(base_note.metadata)

    for op in patch.operations:
        if op.op == PatchOpType.UPDATE_SUMMARY:
            if op.content is not None:
                summary = op.content

        elif op.op == PatchOpType.UPDATE_CONCEPTS:
            if op.key_concepts:
                concepts = list(op.key_concepts)

        elif op.op == PatchOpType.UPDATE_METADATA:
            metadata.update(op.metadata)

        elif op.op == PatchOpType.APPEND_SECTION:
            sec_id = f"{base_note.document_id}:note-sec-{len(sections) + 1:03d}"
            sections.append(
                NoteSection(
                    section_id=sec_id,
                    title=op.title or f"补充章节 {len(sections) + 1}",
                    level=op.level,
                    content=op.content or "",
                    source_segments=op.source_segments,
                    citations=op.citations,
                    asset_refs=op.asset_refs,
                )
            )

        elif op.op == PatchOpType.INSERT_SECTION:
            idx = op.section_index if op.section_index is not None else len(sections)
            idx = max(0, min(len(sections), idx))
            sec_id = f"{base_note.document_id}:note-sec-{idx + 1:03d}"
            sections.insert(
                idx,
                NoteSection(
                    section_id=sec_id,
                    title=op.title or f"插入章节 {idx + 1}",
                    level=op.level,
                    content=op.content or "",
                    source_segments=op.source_segments,
                    citations=op.citations,
                    asset_refs=op.asset_refs,
                ),
            )

        elif op.op == PatchOpType.REPLACE_SECTION:
            target_idx = -1
            if op.section_index is not None and 0 <= op.section_index < len(sections):
                target_idx = op.section_index
            elif op.target_section_id:
                for i, s in enumerate(sections):
                    if s.section_id == op.target_section_id:
                        target_idx = i
                        break
            elif op.target_title:
                for i, s in enumerate(sections):
                    if s.title.strip().lower() == op.target_title.strip().lower():
                        target_idx = i
                        break

            if target_idx >= 0:
                old = sections[target_idx]
                sections[target_idx] = NoteSection(
                    section_id=old.section_id,
                    title=op.title or old.title,
                    level=op.level if op.level else old.level,
                    content=op.content if op.content is not None else old.content,
                    source_segments=op.source_segments or old.source_segments,
                    citations=op.citations or old.citations,
                    asset_refs=op.asset_refs or old.asset_refs,
                )

        elif op.op == PatchOpType.DELETE_SECTION:
            target_idx = -1
            if op.section_index is not None and 0 <= op.section_index < len(sections):
                target_idx = op.section_index
            elif op.target_section_id:
                for i, s in enumerate(sections):
                    if s.section_id == op.target_section_id:
                        target_idx = i
                        break
            elif op.target_title:
                for i, s in enumerate(sections):
                    if s.title.strip().lower() == op.target_title.strip().lower():
                        target_idx = i
                        break

            if target_idx >= 0:
                sections.pop(target_idx)

    new_version = base_note.version + 1
    new_note_id = f"note-{base_note.document_id}-v{new_version}"
    metadata["revision_instruction"] = patch.instruction
    metadata["revision_applied_at"] = patch.applied_at

    return DocumentNote(
        note_id=new_note_id,
        document_id=base_note.document_id,
        title=base_note.title,
        version=new_version,
        parent_note_id=base_note.note_id,
        applied_patch_id=patch.patch_id,
        executive_summary=summary,
        sections=tuple(sections),
        key_concepts=tuple(concepts),
        visual_assets=base_note.visual_assets,
        metadata=metadata,
        created_at=patch.applied_at or _utc_now(),
    )


_NOTE_CACHE: dict[str, DocumentNote] = {}


def save_note_artifacts(
    note: DocumentNote,
    store: LocalArtifactStore,
    *,
    producer_step_id: str = "step-document-note",
) -> dict[str, ArtifactRef]:
    """Persist structured note, raw Markdown, and HTML view into LocalArtifactStore."""
    note_dict = note.to_dict()
    json_ref = store.put_json(
        note_dict,
        producer_step_id=producer_step_id,
        schema_version=NOTE_SCHEMA_VERSION,
    )
    md_ref = store.put_bytes(
        note.markdown_content.encode("utf-8"),
        media_type="text/markdown",
        producer_step_id=producer_step_id,
        schema_version="conflux-weave.note-markdown.v1",
    )
    html_ref = store.put_bytes(
        note.html_content.encode("utf-8"),
        media_type="text/html",
        producer_step_id=producer_step_id,
        schema_version="conflux-weave.note-html.v1",
    )
    _NOTE_CACHE[note.note_id] = note
    _NOTE_CACHE[json_ref.artifact_id] = note
    return {
        "note_artifact": json_ref,
        "markdown_artifact": md_ref,
        "html_artifact": html_ref,
    }


def load_note_artifact(identifier: str, store: LocalArtifactStore) -> DocumentNote:
    """Load DocumentNote from an ArtifactRef id (artifact-sha256-...) or note_id."""
    if identifier in _NOTE_CACHE:
        return _NOTE_CACHE[identifier]

    digest = identifier.removeprefix("artifact-sha256-")
    if len(digest) == 64 and all(c in "0123456789abcdef" for c in digest):
        path = store.path_for_digest(digest)
        payload = json.loads(path.read_text(encoding="utf-8"))
        note = DocumentNote.from_dict(payload)
        _NOTE_CACHE[note.note_id] = note
        _NOTE_CACHE[identifier] = note
        return note

    # Fallback search by note_id across artifact store
    for path in store.root.glob("*/*"):
        if path.is_file() and not path.name.endswith(".tmp"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                if payload.get("schema_version") == NOTE_SCHEMA_VERSION and payload.get("note_id") == identifier:
                    note = DocumentNote.from_dict(payload)
                    _NOTE_CACHE[identifier] = note
                    _NOTE_CACHE[note.note_id] = note
                    return note
            except Exception:
                continue
    raise KeyError(f"Note artifact not found for identifier: {identifier}")


def save_patch_artifact(
    patch: NotePatch,
    store: LocalArtifactStore,
    *,
    producer_step_id: str = "step-document-patch",
) -> ArtifactRef:
    """Persist NotePatch into LocalArtifactStore."""
    return store.put_json(
        patch.to_dict(),
        producer_step_id=producer_step_id,
        schema_version=PATCH_SCHEMA_VERSION,
    )


def load_patch_artifact(identifier: str, store: LocalArtifactStore) -> NotePatch:
    """Load NotePatch from an ArtifactRef id or patch_id."""
    digest = identifier.removeprefix("artifact-sha256-")
    if len(digest) == 64 and all(c in "0123456789abcdef" for c in digest):
        path = store.path_for_digest(digest)
        payload = json.loads(path.read_text(encoding="utf-8"))
        return NotePatch.from_dict(payload)

    for path in store.root.glob("*/*"):
        if path.is_file() and not path.name.endswith(".tmp"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                if payload.get("schema_version") == PATCH_SCHEMA_VERSION and payload.get("patch_id") == identifier:
                    return NotePatch.from_dict(payload)
            except Exception:
                continue
    raise KeyError(f"Patch artifact not found for identifier: {identifier}")
