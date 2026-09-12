"""P6-A1 成果导出：统一 ExportDocument 与 Markdown / BibTeX / JSON / ZIP 渲染。

权威状态仍只读自 SQLite 运行库与内容寻址产物库；导出文件本身不作运行时状态源。
导出文档显式携带 Run ID、code_revision、生成时间与失败/部分成功/未核验状态，
不包含 Provider key、环境配置或无关用户数据。
"""

from __future__ import annotations

import io
import json
import re
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from conflux_weave.core.delivery import DeliveryRecord
from conflux_weave.evidence.contracts import ArtifactRef
from conflux_weave.runtime.durable_paper_shared import RANK_CHECKPOINT
from conflux_weave.runtime.durable_research import DURABLE_RESEARCH_EVIDENCE_SCHEMA
from conflux_weave.runtime.sqlite_contracts import RecordNotFound

EXPORT_SCHEMA = "conflux-weave.export-document.v1"

# 报告正文已内嵌引用清单（融合/未核验渲染器均按此标题生成）时不再追加导出版清单。
_EMBEDDED_REFERENCE_HEADING = re.compile(
    r"^#{1,3}\s*(来源清单|参考文献|参考资料|参考来源|references|bibliography)\s*$",
    re.IGNORECASE | re.MULTILINE,
)

_ZIP_UNSAFE = re.compile(r"[^\w\u4e00-\u9fff.-]+")


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class ExportCitation:
    """一条可复核的引用：编号 + 来源标题/URL + 定位信息。"""

    display_index: int
    title: str
    url: str | None = None
    locator: dict[str, Any] | None = None
    evidence_id: str | None = None
    snapshot_artifact: str | None = None
    content_hash: str | None = None
    acquired_at: str | None = None
    source_type: str = "web"
    authors: tuple[str, ...] = ()
    year: str | None = None
    doi: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "display_index": self.display_index,
            "title": self.title,
            "source_type": self.source_type,
        }
        for key in ("url", "locator", "evidence_id", "snapshot_artifact", "content_hash", "acquired_at", "year", "doi"):
            value = getattr(self, key)
            if value:
                payload[key] = value
        if self.authors:
            payload["authors"] = list(self.authors)
        return payload


@dataclass(frozen=True, slots=True)
class ExportDocument:
    """统一导出结构：正文 Markdown + 引用 + Evidence 台账 + 导出元数据。"""

    object_kind: str  # run_report | chat_answer | note | evidence
    object_id: str
    title: str
    body_markdown: str
    citations: tuple[ExportCitation, ...] = ()
    evidence: tuple[dict[str, Any], ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def references_embedded(self) -> bool:
        return bool(_EMBEDDED_REFERENCE_HEADING.search(self.body_markdown))

    def export_payload(self) -> dict[str, Any]:
        return {
            "schema_version": EXPORT_SCHEMA,
            "object_kind": self.object_kind,
            "object_id": self.object_id,
            "title": self.title,
            "body_markdown": self.body_markdown,
            "citations": [item.to_dict() for item in self.citations],
            "evidence": [dict(item) for item in self.evidence],
            "metadata": dict(self.metadata),
            "exported_at": _utc_now(),
        }


class ExportService:
    """从权威运行库组装导出文档（只读，不写任何运行时状态）。"""

    def __init__(self, repository, chat_service=None, *, title_resolver=None, corpus_manifest_path=None) -> None:
        self.repository = repository
        self.chat_service = chat_service
        self._title_resolver = title_resolver
        self._corpus_manifest_path = corpus_manifest_path
        self._local_documents: dict[str, dict] | None = None
        self._corpus_documents: dict[str, dict] | None = None

    def _resolve_local_title(self, document_id: str) -> dict | None:
        """本地来源标题解析：检索分段表首页标题 → 文库注册表 → corpus 清单文件名。"""
        resolver = self._title_resolver
        if resolver is not None:
            try:
                resolved = resolver(document_id)
            except Exception:
                resolved = None
            if resolved:
                return {"title": str(resolved)}
        record = self._local_document_index().get(document_id)
        if record is not None and record.get("title"):
            return record
        corpus = self._corpus_document_index().get(document_id)
        return corpus

    def _corpus_document_index(self) -> dict[str, dict]:
        if self._corpus_documents is not None:
            return self._corpus_documents
        index: dict[str, dict] = {}
        if self._corpus_manifest_path is not None:
            try:
                payload = json.loads(Path(self._corpus_manifest_path).read_text(encoding="utf-8"))
                for row in payload.get("files", []):
                    document_id = str(row.get("document_id") or "")
                    relative = str(row.get("relative_path") or "")
                    if document_id and relative:
                        stem = Path(relative).stem
                        index[document_id] = {"title": stem} if stem else {}
            except (OSError, ValueError):
                pass
        self._corpus_documents = index
        return index

    def _local_document_index(self) -> dict[str, dict]:
        """懒加载文库注册表：document_id → 标题/作者/年份/DOI（读不到返回空表）。"""
        if self._local_documents is not None:
            return self._local_documents
        index: dict[str, dict] = {}
        try:
            registry_path = self.repository.database_path.with_name("library-registry.json")
            rows = json.loads(registry_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            rows = []
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            for key in ("document_id", "paper_id"):
                document_id = row.get(key)
                if document_id:
                    index[str(document_id)] = row
        self._local_documents = index
        return index

    # ------------------------------------------------------------------ Run
    def collect_run_export(self, run_id: str) -> ExportDocument:
        repository = self.repository
        run = repository.get_run(run_id)
        task = repository.get_task_for_run(run_id)
        try:
            delivery = repository.get_delivery(run_id)
        except RecordNotFound as exc:
            raise RecordNotFound("Run has no delivery to export") from exc

        artifacts = repository.get_delivery_artifacts(run_id)
        report_ref = _first_markdown(artifacts)
        if report_ref is None:
            raise RecordNotFound("Run delivery has no markdown report artifact")
        body = repository.artifact_store.read_bytes(report_ref).decode("utf-8")

        manifest_payload = _read_json_artifact(repository, _first_schema(artifacts, "manifest"))
        evidence_records = self._collect_evidence(run_id, delivery)

        citations = _citations_from_manifest(manifest_payload)
        if not citations and evidence_records:
            citations = self._citations_from_evidence(evidence_records)

        objective = ""
        if isinstance(manifest_payload, dict):
            objective = str(manifest_payload.get("objective") or "")
        if not objective:
            objective = str(task.input.get("objective") or task.input.get("query") or run_id)
        code_revision = ""
        if isinstance(manifest_payload, dict):
            code_revision = str(manifest_payload.get("code_revision") or "")

        metadata = {
            "run_id": run_id,
            "task_kind": task.kind,
            "task_id": task.task_id,
            "objective": objective,
            "run_status": run.status.value,
            "disposition": delivery.disposition.value,
            "limitations": list(delivery.limitations),
            "unmet_criteria": list(delivery.unmet_criteria),
            "recovery_actions": list(delivery.recovery_actions),
            "evidence_count": len(evidence_records),
            "workflow_version": run.workflow_version,
            "code_revision": code_revision,
            "run_created_at": run.created_at,
            "report_artifact_id": report_ref.artifact_id,
            "report_schema_version": report_ref.schema_version,
        }
        return ExportDocument(
            object_kind="run_report",
            object_id=run_id,
            title=objective,
            body_markdown=body,
            citations=tuple(citations),
            evidence=evidence_records,
            metadata=metadata,
        )

    def _collect_evidence(self, run_id: str, delivery: DeliveryRecord) -> tuple[dict, ...]:
        """按 Evidence ID 从注册检查点回收 Evidence 台账（保持交付引用顺序）。"""
        wanted = list(delivery.evidence_refs)
        if not wanted:
            return ()
        repository = self.repository
        found: dict[str, dict] = {}
        for step in repository.get_steps(run_id):
            for artifact in repository.get_step_artifacts(step.step_id):
                if artifact.schema_version not in {
                    RANK_CHECKPOINT,
                    DURABLE_RESEARCH_EVIDENCE_SCHEMA,
                }:
                    continue
                payload = _safe_json(repository.artifact_store.read_bytes(artifact))
                items = payload.get("evidence") if isinstance(payload, dict) else None
                if not isinstance(items, list):
                    continue
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    item_id = str(item.get("evidence_id") or "")
                    if item_id in wanted and item_id not in found:
                        found[item_id] = item
        return tuple(found[evidence_id] for evidence_id in wanted if evidence_id in found)

    def _citations_from_evidence(self, evidence: tuple[dict, ...]) -> list[ExportCitation]:
        """Evidence 台账 → 引用列表；本地来源回填真实标题/作者/年份/DOI。"""
        local_index = self._local_document_index()
        citations = []
        for index, item in enumerate(evidence, 1):
            snapshot_id = str(item.get("source_snapshot_id") or "")
            record = local_index.get(snapshot_id)
            resolved = self._resolve_local_title(snapshot_id) if snapshot_id else None
            title = str(
                (record or {}).get("title")
                or (resolved or {}).get("title")
                or snapshot_id
                or item.get("evidence_id")
                or f"Evidence {index}"
            )
            year_value = (record or {}).get("year")
            citations.append(
                ExportCitation(
                    display_index=index,
                    title=title,
                    locator=item.get("locator") if isinstance(item.get("locator"), dict) else None,
                    evidence_id=item.get("evidence_id"),
                    source_type="local" if record is not None or snapshot_id.startswith("document-") else "web",
                    authors=tuple(str(author) for author in (record or {}).get("authors", ()) if author),
                    year=str(year_value) if year_value else None,
                    doi=(record or {}).get("doi") or None,
                )
            )
        return citations

    # --------------------------------------------------------------- Evidence
    def collect_evidence_export(self, run_id: str, evidence_id: str) -> ExportDocument:
        run_doc = self.collect_run_export(run_id)
        match = next(
            (item for item in run_doc.evidence if item.get("evidence_id") == evidence_id),
            None,
        )
        if match is None:
            raise RecordNotFound("Evidence not found")
        locator = match.get("locator") or {}
        quote = str(match.get("quote") or "")
        lines = [
            f"# Evidence {evidence_id}",
            "",
            f"> 来源 Run：`{run_id}` · 证据快照 `{match.get('source_snapshot_id', '')}`",
            "",
            "## 原文引文",
            "",
            quote or "_（无引文内容）_",
            "",
            "## 定位信息",
            "",
        ]
        if locator:
            for key in sorted(locator):
                lines.append(f"- **{key}**: {locator[key]}")
        else:
            lines.append("_（无定位信息）_")
        citation = next(
            (item for item in run_doc.citations if item.evidence_id == evidence_id),
            None,
        )
        if citation is not None:
            lines.extend(
                [
                    "",
                    "## 来源",
                    "",
                    f"- [{citation.title}]({citation.url})" if citation.url else f"- {citation.title}",
                ]
            )
        metadata = dict(run_doc.metadata)
        metadata["evidence_id"] = evidence_id
        metadata["parent_run_export"] = run_doc.object_id
        return ExportDocument(
            object_kind="evidence",
            object_id=evidence_id,
            title=f"Evidence {evidence_id}",
            body_markdown="\n".join(lines) + "\n",
            citations=(citation,) if citation else (),
            evidence=(match,),
            metadata=metadata,
        )

    # ------------------------------------------------------------ Chat answer
    def collect_chat_answer_export(self, message_id: str) -> ExportDocument:
        if self.chat_service is None:
            raise RecordNotFound("Chat service is not available")
        message = self.chat_service.load_message(message_id)
        if message is None or message.role != "assistant":
            raise RecordNotFound("Chat answer not found")
        context = self.chat_service.read_message_context(message)
        citations = _citations_from_chat_context(context) if isinstance(context, dict) else []
        checks = context.get("answer_checks") if isinstance(context, dict) else None
        metadata = {
            "message_id": message.message_id,
            "conversation_id": message.conversation_id,
            "mode": message.mode,
            "created_at": message.created_at,
            "run_id": message.run_id or "",
            "verification": str((checks or {}).get("status") or ""),
        }
        return ExportDocument(
            object_kind="chat_answer",
            object_id=message.message_id,
            title=_title_from_body(message.content),
            body_markdown=message.content,
            citations=tuple(citations),
            metadata=metadata,
        )

    # ----------------------------------------------------------------- Note
    def collect_note_export(self, note_id: str) -> ExportDocument:
        from conflux_weave.document_notes import load_note_artifact

        note = load_note_artifact(note_id, self.repository.artifact_store)
        return ExportDocument(
            object_kind="note",
            object_id=note.note_id,
            title=note.title,
            body_markdown=note.render_markdown(),
            metadata={
                "note_id": note.note_id,
                "document_id": note.document_id,
                "note_version": note.version,
                "created_at": note.created_at,
                "verification": "human_curated_note",
            },
        )


# --------------------------------------------------------------------- 渲染


def render_export_markdown(doc: ExportDocument) -> str:
    """导出 Markdown：元数据头 + 正文 +（正文无清单时的）编号引用列表。"""
    metadata = doc.metadata
    generated_at = metadata.get("exported_at") or doc.export_payload()["exported_at"]
    lines = [
        "<!-- conflux-weave export -->",
        f"# {doc.title}",
        "",
        f"> 导出来源：`{doc.object_kind}` `{doc.object_id}` · 导出时间 {generated_at}",
    ]
    run_id = metadata.get("run_id") or metadata.get("parent_run_export")
    if run_id:
        lines.append(f"> 来源 Run：`{run_id}`")
    code_revision = metadata.get("code_revision")
    if code_revision:
        lines.append(f"> code_revision：`{code_revision}`")
    status_bits = []
    if metadata.get("disposition"):
        status_bits.append(f"交付状态 {metadata['disposition']}")
    if metadata.get("run_status"):
        status_bits.append(f"Run 状态 {metadata['run_status']}")
    if metadata.get("verification"):
        status_bits.append(f"核验标记 {metadata['verification']}")
    if status_bits:
        lines.append(f"> 状态：{' · '.join(str(bit) for bit in status_bits)}")
    lines.append("")
    lines.append(doc.body_markdown.rstrip())
    if doc.citations and not doc.references_embedded:
        lines.extend(["", "## 参考文献", ""])
        for citation in doc.citations:
            title_part = (
                f"[{citation.title}]({citation.url})" if citation.url else citation.title
            )
            lines.append(f"{citation.display_index}. {title_part}{_locator_note(citation)}")
    return "\n".join(lines) + "\n"


def render_export_bibtex(doc: ExportDocument) -> str:
    """从引用列表生成 BibTeX；无 URL 的本地来源用 @misc + note 说明。"""
    entries: list[str] = []
    seen_keys: set[str] = set()
    for citation in doc.citations:
        if not citation.title and not citation.url:
            continue
        key = _bibtex_key(citation, seen_keys)
        run_label = str(doc.metadata.get("run_id") or doc.object_id)
        year = citation.year or _year_month(citation.acquired_at or doc.metadata.get("run_created_at"))[0]
        notes = []
        locator_note = _locator_note(citation, plain=True).strip("（）：() ")
        if locator_note:
            notes.append(locator_note)
        if citation.evidence_id:
            notes.append(f"Evidence {citation.evidence_id}")
        if citation.content_hash:
            notes.append(f"内容哈希 {citation.content_hash}")
        notes.append(f"Conflux-Weave Run {run_label}")
        field_lines = [
            f"  @misc{{{key},",
            f"    title = {{{_tex_escape(citation.title or citation.url or 'untitled')}}},",
        ]
        if citation.authors:
            joined = " and ".join(_tex_escape(author) for author in citation.authors)
            field_lines.append(f"    author = {{{joined}}},")
        if citation.url:
            field_lines.append("    howpublished = {\\url{%s}}," % citation.url)
            field_lines.append("    url = {%s}," % citation.url)
        if citation.doi:
            field_lines.append(f"    doi = {{{citation.doi}}},")
        if year:
            field_lines.append(f"    year = {{{year}}},")
        field_lines.append(f"    note = {{{_tex_escape('；'.join(notes))}}},")
        field_lines.append("  }")
        entries.append("\n".join(field_lines))
    header = (
        "%% BibTeX export from Conflux-Weave\n"
        f"%% object: {doc.object_kind} {doc.object_id}\n"
        f"%% generated_at: {doc.export_payload()['exported_at']}\n"
    )
    return header + ("\n\n".join(entries) + "\n" if entries else "")


def build_export_json(doc: ExportDocument) -> bytes:
    return json.dumps(doc.export_payload(), ensure_ascii=False, indent=2).encode("utf-8")


def build_export_zip(doc: ExportDocument) -> bytes:
    """完整证据包：report.md + references.bib + evidence.json（含 Evidence 台账）。"""
    payload = doc.export_payload()
    run_label = str(payload["metadata"].get("run_id") or payload["object_id"])
    stem = _zip_safe(run_label) or "conflux-weave-export"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f"{stem}/report.md", render_export_markdown(doc).encode("utf-8"))
        archive.writestr(f"{stem}/references.bib", render_export_bibtex(doc).encode("utf-8"))
        archive.writestr(f"{stem}/evidence.json", build_export_json(doc))
    return buffer.getvalue()


# ------------------------------------------------------------------- 内部工具


def _first_markdown(artifacts: tuple[ArtifactRef, ...]) -> ArtifactRef | None:
    for artifact in artifacts:
        if artifact.media_type.startswith("text/markdown"):
            return artifact
    return None


def _first_schema(artifacts: tuple[ArtifactRef, ...], needle: str) -> ArtifactRef | None:
    for artifact in artifacts:
        if needle in artifact.schema_version:
            return artifact
    return None


def _read_json_artifact(repository, artifact: ArtifactRef | None) -> dict | None:
    if artifact is None:
        return None
    return _safe_json(repository.artifact_store.read_bytes(artifact))


def _safe_json(raw: bytes) -> Any:
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


def _citations_from_manifest(manifest: dict | None) -> list[ExportCitation]:
    if not isinstance(manifest, dict):
        return []
    sources = manifest.get("sources")
    if not isinstance(sources, list):
        return []
    citations: list[ExportCitation] = []
    for index, item in enumerate(sources, 1):
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or item.get("source_id") or f"来源 {index}")
        url = item.get("url") or item.get("canonical_uri")
        citations.append(
            ExportCitation(
                display_index=index,
                title=title,
                url=str(url) if url else None,
                snapshot_artifact=item.get("snapshot_artifact"),
                content_hash=item.get("content_hash"),
                acquired_at=item.get("acquired_at"),
                source_type="title_only" if item.get("title_only") else "web",
            )
        )
    return citations


def _citations_from_chat_context(context: dict) -> list[ExportCitation]:
    citations: list[ExportCitation] = []
    raw = context.get("citations")
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            index = int(item.get("index") or len(citations) + 1)
            locator = item.get("locator") if isinstance(item.get("locator"), dict) else None
            citations.append(
                ExportCitation(
                    display_index=index,
                    title=str(
                        item.get("chunk_id") or item.get("source_snapshot_id") or f"片段 {index}"
                    ),
                    locator=locator,
                    evidence_id=item.get("source_snapshot_id") or item.get("chunk_id"),
                    source_type="local",
                )
            )
    return citations


def _locator_note(citation: ExportCitation, *, plain: bool = False) -> str:
    locator = citation.locator or {}
    bits = []
    if locator.get("page") is not None:
        bits.append(f"第 {locator['page']} 页")
    for key in ("section", "heading", "chunk", "start_char", "end_char"):
        if locator.get(key) is not None:
            bits.append(f"{key}: {locator[key]}")
    if citation.source_type == "title_only":
        bits.append("仅标题，未获取正文")
    if not bits:
        return ""
    text = "（" + "，".join(str(bit) for bit in bits) + "）"
    return text if plain else " " + text


def _bibtex_key(citation: ExportCitation, seen: set[str]) -> str:
    base = (
        _zip_safe(f"{citation.evidence_id or citation.title or citation.display_index}")
        or f"ref{citation.display_index}"
    )
    key = f"cw{citation.display_index:02d}-{base}"[:80]
    candidate, counter = key, 2
    while candidate in seen:
        candidate = f"{key}-{counter}"
        counter += 1
    seen.add(candidate)
    return candidate


def _tex_escape(text: str) -> str:
    return (
        text.replace("\\", "\\textbackslash{}")
        .replace("&", "\\&")
        .replace("%", "\\%")
        .replace("$", "\\$")
        .replace("#", "\\#")
        .replace("_", "\\_")
        .replace("{", "\\{")
        .replace("}", "\\}")
        .replace("~", "\\textasciitilde{}")
        .replace("^", "\\textasciicircum{}")
    )


def _year_month(iso_value: object) -> tuple[str, str]:
    raw = str(iso_value or "")
    match = re.match(r"(\d{4})-(\d{2})", raw)
    if not match:
        return "", ""
    return match.group(1), match.group(2)


def _zip_safe(text: str) -> str:
    return _ZIP_UNSAFE.sub("-", text.strip())[:80] or ""


def _title_from_body(body: str, limit: int = 60) -> str:
    text = body.strip().splitlines()[0] if body.strip() else "对话回答"
    return text[:limit]
