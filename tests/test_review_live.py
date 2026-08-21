import json

from conflux_weave.core import DeliveryDisposition, RunStatus
from conflux_weave.provider import OpenAICompatibleChatAdapter, ProviderConfig, ProviderHttpResponse
from conflux_weave.review_live import FixedReviewReadingNoteWorkflow, SELECTED_PAGES
from conflux_weave.runtime import LocalArtifactStore


class FakeProviderTransport:
    def post(self, url, *, headers, body, timeout_seconds):
        note = {
            "title": "Agent Harness 工程综述阅读笔记",
            "executive_summary": {
                "text": "综述将 agent harness 作为围绕模型的系统工程对象，并用 ETCLOVG 组织设计空间。",
                "evidence_ids": ["pdf-page-01", "pdf-page-07"],
            },
            "key_points": [
                {"text": "ETCLOVG 将 Harness 分为 Execution、Tooling、Context、Lifecycle、Observability、Verification、Governance 七层。", "evidence_ids": ["pdf-page-07"]},
                {"text": "评测应连接任务、执行前检查、受控执行和反馈闭环。", "evidence_ids": ["pdf-page-33"]},
            ],
            "terms": [
                {"term": "Context Engineering", "explanation": "围绕模型当前任务选择、压缩和组织上下文的工程实践。", "evidence_ids": ["pdf-page-19"]}
            ],
            "omitted_or_underdeveloped": [
                {"text": "选定页没有提供全文逐条实验复核，因此不能替代完整文献审计。", "evidence_ids": ["pdf-page-67"]}
            ],
            "implications": [
                {"text": "研究系统应把上下文、生命周期、可观测性和治理作为可验证边界，而不只调 Prompt。", "evidence_ids": ["pdf-page-48"]}
            ],
            "limitations": ["本 fixture 只验证结构化输出合同。"],
        }
        response = {
            "id": "chatcmpl-review-fixture",
            "model": "fixture-model",
            "choices": [{"message": {"content": json.dumps(note, ensure_ascii=False)}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 120, "completion_tokens": 80, "total_tokens": 200},
        }
        return ProviderHttpResponse(200, json.dumps(response, ensure_ascii=False).encode(), {"Content-Type": "application/json"})


def test_review_workflow_imports_pdf_and_compiles_page_citations(tmp_path, monkeypatch):
    class FakePage:
        def __init__(self, page_number):
            self.page_number = page_number

        def extract_text(self):
            return f"Review evidence on PDF page {self.page_number}."

    class FakePdfReader:
        def __init__(self, source):
            self.pages = [FakePage(page) for page in range(1, 72)]

    monkeypatch.setattr("conflux_weave.documents.PdfReader", FakePdfReader)
    pdf_path = tmp_path / "review.pdf"
    pdf_path.write_bytes(b"%PDF-local-fixture")
    store = LocalArtifactStore(tmp_path / "artifacts")
    provider = OpenAICompatibleChatAdapter(store, ProviderConfig("https://provider.example/v1", "fixture-secret", "fixture-model"), transport=FakeProviderTransport())
    result = FixedReviewReadingNoteWorkflow(store, provider, clock=lambda: "2026-08-21T00:00:00Z", id_factory=lambda p: p + "-fixed").execute(pdf_path, "总结综述并解释术语")

    assert result.final_run.status is RunStatus.PARTIAL
    assert result.delivery.disposition is DeliveryDisposition.PARTIAL
    assert len(result.evidence) == len(SELECTED_PAGES)
    assert result.evidence[0].locator["page"] == 1
    assert len(result.citations) == 7
    report = store.read_bytes(result.report_artifact).decode()
    assert "ETCLOVG" in report
    assert "执行摘要" in report and "[1] [2]" in report
    assert "PDF 第 7 页" in report
