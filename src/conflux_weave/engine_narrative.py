"""Engine narrative parser (W3.5 模式 C 融合交付).

把聚合引擎（GPT Researcher）的 Markdown 报告解析成段落级骨架：标题、
小节（一、二、…）、段落。骨架是融合 Writer 的正文主线——标题与段落
划分优先继承引擎结构，因为它已经完成了基本的研究叙事。解析是确定性的，
不调用模型；解析不出任何小节（仅标题/空报告）时返回 None，交付退回
claim 组装路径。

预算（冻结）：小节 ≤12、每节段落 ≤12、段落正文 >1600 字符时在句子
边界确定性切分；H3 子标题折叠为该节的引导段落（加粗独立段）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass


ENGINE_MAX_SECTIONS = 12
ENGINE_MAX_PARAGRAPHS_PER_SECTION = 12
ENGINE_MAX_PARAGRAPH_CHARS = 1600

HEADING_H2 = re.compile(r"^##\s+(?!#)\s*(.+?)\s*$")
HEADING_H3 = re.compile(r"^###\s+(?!#)\s*(.+?)\s*$")
HEADING_H1 = re.compile(r"^#\s+(?!#)\s*(.+?)\s*$")
# 引擎自带的 "一、" 序号避免重复添加
ORDINAL_PREFIX = re.compile(r"^[一二三四五六七八九十]+、")


@dataclass(frozen=True, slots=True)
class EngineSection:
    heading: str
    paragraphs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class EngineNarrative:
    title: str
    sections: tuple[EngineSection, ...]


def _ordinal(index: int) -> str:
    return "一二三四五六七八九十"[index - 1] if 1 <= index <= 10 else str(index)


def _section_heading(raw: str, index: int) -> str:
    heading = raw.strip().strip("*# ")
    if ORDINAL_PREFIX.match(heading):
        return heading
    return f"{_ordinal(index)}、{heading}"


def _split_long_paragraph(text: str) -> list[str]:
    if len(text) <= ENGINE_MAX_PARAGRAPH_CHARS:
        return [text]
    chunks = []
    current = ""
    for sentence in re.split(r"(?<=[。！？；.!?;])", text):
        if not sentence:
            continue
        if current and len(current) + len(sentence) > ENGINE_MAX_PARAGRAPH_CHARS:
            chunks.append(current)
            current = sentence
        else:
            current += sentence
    if current.strip():
        chunks.append(current)
    return chunks or [text[:ENGINE_MAX_PARAGRAPH_CHARS]]


def parse_engine_narrative(markdown: str) -> EngineNarrative | None:
    """确定性解析引擎报告；无 H2 小节时返回 None（退回 legacy 交付路径）。"""
    title = ""
    sections: list[EngineSection] = []
    current_heading: str | None = None
    current_paragraphs: list[str] = []
    pending_subheading = ""

    def flush() -> None:
        nonlocal current_paragraphs, pending_subheading, current_heading
        if current_heading is not None and len(sections) < ENGINE_MAX_SECTIONS:
            if pending_subheading:
                current_paragraphs.append(f"**{pending_subheading}**")
                pending_subheading = ""
            paragraphs: list[str] = []
            for paragraph in current_paragraphs:
                paragraphs.extend(_split_long_paragraph(paragraph))
            paragraphs = [item for item in paragraphs if item.strip()]
            if paragraphs:
                sections.append(
                    EngineSection(
                        _section_heading(current_heading, len(sections) + 1),
                        tuple(paragraphs[:ENGINE_MAX_PARAGRAPHS_PER_SECTION]),
                    )
                )
        current_paragraphs = []
        current_heading = None

    for line in markdown.splitlines():
        h2 = HEADING_H2.match(line)
        h3 = HEADING_H3.match(line)
        h1 = HEADING_H1.match(line)
        if h2:
            flush()
            current_heading = h2.group(1)
        elif h3:
            if pending_subheading:
                current_paragraphs.append(f"**{pending_subheading}**")
            pending_subheading = h3.group(1).strip().strip("*# ")
        elif h1:
            if not title:
                title = h1.group(1).strip()
        elif line.strip():
            if pending_subheading:
                current_paragraphs.append(f"**{pending_subheading}**")
                pending_subheading = ""
            current_paragraphs.append(line.strip())
        # 空行只是段落分隔；首个 H2 之前的引言段落没有归属小节，按预算丢弃
    flush()
    if not sections:
        return None
    return EngineNarrative(title=title, sections=tuple(sections))
