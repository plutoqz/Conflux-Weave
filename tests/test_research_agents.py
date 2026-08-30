import json
from pathlib import Path
import pytest

from conflux_weave.core import DeliveryDisposition
from conflux_weave.evidence import Citation, Claim, EvidenceRef
from conflux_weave.hybrid_retrieval import HybridRetrievalPipeline
from conflux_weave.indexing import LanceDBDenseIndex
from conflux_weave.managed_research import ManagedVerifiedResearchWorkflow
from conflux_weave.provider import OpenAICompatibleChatAdapter, OpenAICompatibleEmbeddingAdapter, OpenAICompatibleRerankerAdapter, ProviderConfig, ProviderHttpResponse
from conflux_weave.research_agents import (
    CoverageReport,
    ResearchExecution,
    VerifiedResearchWorkflow,
    _parse_verifier_assessments,
)
from conflux_weave.retrieval import RetrievalDocument
from conflux_weave.runtime import LocalArtifactStore


FAILURE_FIXTURE = Path("tests/fixtures/s16c_contract_failures.json")
REFERENTIAL_FAILURE_FIXTURE = Path("tests/fixtures/s16e_referential_failure.json")
WRITER_FIXTURE = json.loads(Path("tests/fixtures/writer_stage_fixtures.json").read_text(encoding="utf-8"))

TWO_CLAIM_DRAFT = {"claims": [
    {"text": "The framework selects evidence before tool actions.", "evidence_ids": ["evidence-0001"]},
    {"text": "The evaluation measures tool success.", "evidence_ids": ["evidence-0001"]},
]}
TWO_CLAIM_VERIFY = {"assessments": [
    {"claim_id": "claim-0001", "evidence_ids": ["evidence-0001"], "relation": "supports", "verdict": "accepted", "rationale": "Direct."},
    {"claim_id": "claim-0002", "evidence_ids": ["evidence-0001"], "relation": "supports", "verdict": "accepted", "rationale": "Direct."},
]}


def verified_workflow(tmp_path, chat):
    store = LocalArtifactStore(tmp_path / "artifacts")
    config = ProviderConfig("https://provider.example/v1", "secret", "chat")
    documents = (
        RetrievalDocument(
            "chunk-a",
            "The framework selects evidence before tool actions and the evaluation measures tool success.",
            "paper-a",
            {"page": 1},
        ),
    )
    index = LanceDBDenseIndex(tmp_path / "db")
    index.publish(documents, ((1.0, 0.0),))
    retrieval = HybridRetrievalPipeline(
        documents,
        index,
        OpenAICompatibleEmbeddingAdapter(store, config, transport=SequenceTransport([{"data": [{"index": 0, "embedding": [1.0, 0.0]}]}])),
        OpenAICompatibleRerankerAdapter(store, config, transport=SequenceTransport([{"results": [{"index": 0, "relevance_score": 0.9}]}])),
    )
    return VerifiedResearchWorkflow(store, retrieval, chat, corpus_scope="fixture new-paper corpus"), store


class SequenceTransport:
    def __init__(self, payloads): self.payloads = iter(payloads); self.requests = []
    def post(self, *args, **kwargs):
        self.requests.append(json.loads(kwargs["body"]))
        payload = next(self.payloads); return ProviderHttpResponse(200, json.dumps(payload).encode(), {"Content-Type": "application/json"})


def chat_response(content, response_id):
    return {"id": response_id, "model": "fixture-chat", "choices": [{"message": {"content": json.dumps(content)}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20}}


def test_verified_research_produces_closed_delivery(tmp_path):
    store = LocalArtifactStore(tmp_path / "artifacts"); config = ProviderConfig("https://provider.example/v1", "secret", "chat")
    documents = (RetrievalDocument("chunk-a", "The framework reduces context by selecting evidence before tool actions.", "paper-a", {"type": "pdf_page", "page": 3}),)
    index = LanceDBDenseIndex(tmp_path / "db"); index.publish(documents, ((1.0, 0.0),))
    retrieval = HybridRetrievalPipeline(documents, index, OpenAICompatibleEmbeddingAdapter(store, config, transport=SequenceTransport([{"data": [{"index": 0, "embedding": [1.0, 0.0]}]}])), OpenAICompatibleRerankerAdapter(store, config, transport=SequenceTransport([{"results": [{"index": 0, "relevance_score": 0.9}]}])))
    chat = OpenAICompatibleChatAdapter(store, config, transport=SequenceTransport([
        chat_response({"claims": [{"text": "The framework selects evidence before tool actions.", "evidence_ids": ["evidence-0001"]}]}, "draft"),
        chat_response({"assessments": [{"claim_id": "claim-0001", "evidence_ids": ["evidence-0001"], "relation": "supports", "verdict": "accepted", "rationale": "Directly stated."}]}, "verify"),
        chat_response(WRITER_FIXTURE["distill_payloads"]["canonical"], "distill"),
        chat_response({"summary": {"text": "该框架在工具动作之前先选择证据。", "claim_ids": ["claim-0001"]}, "sections": [{"heading": "机制", "paragraphs": [{"text": "证据选择发生在工具动作之前。", "claim_ids": ["claim-0001"]}]}], "background": [], "open_questions": ["更大规模语料上的收益仍待验证。"]}, "writer"),
        chat_response({"audits": [
            {"section_index": 0, "paragraph_index": 0, "verdict": "supported", "rationale": "Summary restates the cited claim."},
            {"section_index": 1, "paragraph_index": 0, "verdict": "supported", "rationale": "Paragraph cites the only accepted claim."},
        ]}, "audit"),
    ]))
    result = VerifiedResearchWorkflow(
        store, retrieval, chat, corpus_scope="fixture new-paper corpus"
    ).execute("How is context reduced?")
    assert result.coverage.accepted_claim_count == 1
    assert result.coverage.repair_rounds == 0
    assert len(result.claims) == len(result.citations) == 1
    report = store.path_for_digest(result.report_artifact_id.removeprefix("artifact-sha256-")).read_text(encoding="utf-8")
    assert "## 回答摘要" in report and "### 审计附录（Evidence 汇总）" in report
    assert "[1]" in report and "paper-a" in report
    manifest = json.loads(store.path_for_digest(result.manifest_artifact_id.removeprefix("artifact-sha256-")).read_text(encoding="utf-8"))
    assert len(manifest["harness_artifacts"]) == 10
    assert manifest["corpus_scope"] == "fixture new-paper corpus"
    assert manifest["report_contract"] == "v2"
    assert manifest["writer_status"] == "ok"
    assert manifest["writer_document_artifact"]
    assert manifest["distill_status"] == "ok"
    assert manifest["distill_artifact"]
    assert len(chat.transport.requests) == 5
    writer_request = json.loads(chat.transport.requests[3]["messages"][1]["content"])
    assert writer_request["cards"][0]["zh_summary"].startswith("该框架在工具动作执行之前")
    assert "evidence" not in writer_request
    assert "fixture new-paper corpus" in report


def test_verifier_ignores_only_extraneous_unknown_evidence_ids(tmp_path):
    fixture = json.loads(REFERENTIAL_FAILURE_FIXTURE.read_text(encoding="utf-8"))
    claim = Claim("claim-0003", "Reported result.", "finding", "primary", "fixture")
    evidence = tuple(
        EvidenceRef(evidence_id, "source", {"page": 1}, "Reported result.", "fixture")
        for evidence_id in fixture["known_evidence_ids"]
    )
    store = LocalArtifactStore(tmp_path / "artifacts")

    assessments, artifact = _parse_verifier_assessments(
        json.dumps(fixture["observed"]),
        (claim,),
        evidence,
        "artifact-sha256-" + "0" * 64,
        store,
        "fixture-verifier",
    )

    assert assessments[0].evidence_ids == ("evidence-0002", "evidence-0007")
    payload = json.loads(
        store.path_for_digest(
            artifact.artifact_id.removeprefix("artifact-sha256-")
        ).read_text(encoding="utf-8")
    )
    assert payload["normalization_warnings"] == [
        {
            "claim_id": "claim-0003",
            "ignored_unknown_evidence_ids": ["evidence-0014"],
        }
    ]


def test_verifier_fails_closed_when_no_known_evidence_remains(tmp_path):
    claim = Claim("claim-0001", "Reported result.", "finding", "primary", "fixture")
    evidence = EvidenceRef(
        "evidence-0001", "source", {"page": 1}, "Reported result.", "fixture"
    )
    content = json.dumps(
        {
            "assessments": [
                {
                    "claim_id": "claim-0001",
                    "evidence_ids": ["evidence-9999"],
                    "relation": "supports",
                    "verdict": "accepted",
                    "rationale": "Unsupported reference.",
                }
            ]
        }
    )

    with pytest.raises(ValueError, match="no known Evidence ID"):
        _parse_verifier_assessments(
            content,
            (claim,),
            (evidence,),
            "artifact-sha256-" + "0" * 64,
            LocalArtifactStore(tmp_path / "artifacts"),
            "fixture-verifier",
        )


def test_verified_research_returns_explicit_no_answer_without_verifier_call(tmp_path):
    store = LocalArtifactStore(tmp_path / "artifacts")
    config = ProviderConfig("https://provider.example/v1", "secret", "chat")
    documents = (
        RetrievalDocument(
            "chunk-a",
            "This paper discusses context management for coding agents.",
            "paper-a",
            {"page": 1},
        ),
    )
    index = LanceDBDenseIndex(tmp_path / "db")
    index.publish(documents, ((1.0, 0.0),))
    retrieval = HybridRetrievalPipeline(
        documents,
        index,
        OpenAICompatibleEmbeddingAdapter(
            store,
            config,
            transport=SequenceTransport(
                [{"data": [{"index": 0, "embedding": [1.0, 0.0]}]}]
            ),
        ),
        OpenAICompatibleRerankerAdapter(
            store,
            config,
            transport=SequenceTransport(
                [{"results": [{"index": 0, "relevance_score": 0.1}]}]
            ),
        ),
    )
    chat = OpenAICompatibleChatAdapter(
        store,
        config,
        transport=SequenceTransport([chat_response({"claims": []}, "draft-empty")]),
    )

    result = VerifiedResearchWorkflow(
        store, retrieval, chat, corpus_scope="fixture mixed corpus"
    ).execute("What do Venetian guild tax records report?")

    assert result.disposition is DeliveryDisposition.NO_ANSWER
    assert result.claims == result.evidence == result.citations == ()
    assert result.coverage.stop_reason == "no_supported_claim"
    assert result.limitations
    assert len(chat.transport.requests) == 1
    report = store.path_for_digest(
        result.report_artifact_id.removeprefix("artifact-sha256-")
    ).read_text(encoding="utf-8")
    assert "No evidence-supported answer" in report
    assert "fixture mixed corpus" in report
    manifest = json.loads(
        store.path_for_digest(
            result.manifest_artifact_id.removeprefix("artifact-sha256-")
        ).read_text(encoding="utf-8")
    )
    assert manifest["disposition"] == "no_answer"
    assert manifest["coverage"]["accepted_claim_count"] == 0


def test_verifier_can_trigger_exactly_one_repair(tmp_path):
    store = LocalArtifactStore(tmp_path / "artifacts"); config = ProviderConfig("https://provider.example/v1", "secret", "chat")
    documents = (RetrievalDocument("chunk-a", "The measured result is 20 percent.", "paper-a", {"page": 1}),)
    index = LanceDBDenseIndex(tmp_path / "db"); index.publish(documents, ((1.0, 0.0),))
    retrieval = HybridRetrievalPipeline(documents, index, OpenAICompatibleEmbeddingAdapter(store, config, transport=SequenceTransport([{"data": [{"index": 0, "embedding": [1.0, 0.0]}]}])), OpenAICompatibleRerankerAdapter(store, config, transport=SequenceTransport([{"results": [{"index": 0, "relevance_score": 0.9}]}])))
    chat = OpenAICompatibleChatAdapter(store, config, transport=SequenceTransport([
        chat_response({"claims": [{"text": "The result is 30 percent.", "evidence_ids": ["evidence-0001"]}]}, "draft"),
        chat_response({"assessments": [{"claim_id": "claim-0001", "evidence_ids": ["evidence-0001"], "relation": "contradicts", "verdict": "rejected", "rationale": "Wrong value."}]}, "verify-1"),
        chat_response({"claims": [{"text": "The measured result is 20 percent.", "evidence_ids": ["evidence-0001"]}]}, "repair"),
        chat_response({"assessments": [{"claim_id": "claim-0001", "evidence_ids": ["evidence-0001"], "relation": "supports", "verdict": "accepted", "rationale": "Exact match."}]}, "verify-2"),
        chat_response(WRITER_FIXTURE["distill_payloads"]["canonical"], "distill"),
        chat_response({"summary": {"text": "测得结果是 20%。", "claim_ids": ["claim-0001"]}, "sections": [{"heading": "结果", "paragraphs": [{"text": "测量结果为 20%。", "claim_ids": ["claim-0001"]}]}], "background": [], "open_questions": []}, "writer"),
        chat_response({"audits": [
            {"section_index": 0, "paragraph_index": 0, "verdict": "supported", "rationale": "Restates the claim."},
            {"section_index": 1, "paragraph_index": 0, "verdict": "supported", "rationale": "Restates the claim."},
        ]}, "audit"),
    ]))
    result = VerifiedResearchWorkflow(store, retrieval, chat).execute("What is the result?")
    assert result.coverage.repair_rounds == 1
    assert result.claims[0].text == "The measured result is 20 percent."
    assert len(chat.transport.requests) == 7


def test_all_claims_rejected_after_repair_returns_no_answer(tmp_path):
    store = LocalArtifactStore(tmp_path / "artifacts")
    config = ProviderConfig("https://provider.example/v1", "secret", "chat")
    documents = (
        RetrievalDocument("chunk-a", "The measured result is 20 percent.", "paper-a", {"page": 1}),
    )
    index = LanceDBDenseIndex(tmp_path / "db")
    index.publish(documents, ((1.0, 0.0),))
    retrieval = HybridRetrievalPipeline(
        documents,
        index,
        OpenAICompatibleEmbeddingAdapter(store, config, transport=SequenceTransport([{"data": [{"index": 0, "embedding": [1.0, 0.0]}]}])),
        OpenAICompatibleRerankerAdapter(store, config, transport=SequenceTransport([{"results": [{"index": 0, "relevance_score": 0.9}]}])),
    )
    rejected = {"assessments": [{"claim_id": "claim-0001", "evidence_ids": ["evidence-0001"], "relation": "insufficient", "verdict": "rejected", "rationale": "The claimed comparison is absent."}]}
    chat = OpenAICompatibleChatAdapter(store, config, transport=SequenceTransport([
        chat_response({"claims": [{"text": "Method A beats method B.", "evidence_ids": ["evidence-0001"]}]}, "draft"),
        chat_response(rejected, "verify-1"),
        chat_response({"claims": [{"text": "Method A is best.", "evidence_ids": ["evidence-0001"]}]}, "repair"),
        chat_response(rejected, "verify-2"),
    ]))

    result = VerifiedResearchWorkflow(store, retrieval, chat).execute("Which method is best?")

    assert result.disposition is DeliveryDisposition.NO_ANSWER
    assert result.coverage.candidate_claim_count == 1
    assert result.coverage.rejected_claim_count == 1
    assert result.coverage.repair_rounds == 1


def test_manager_plans_and_aggregates_verified_subruns(tmp_path):
    store = LocalArtifactStore(tmp_path / "artifacts")
    config = ProviderConfig("https://provider.example/v1", "secret", "chat")
    documents = (
        RetrievalDocument("chunk-a", "Method A reduces context.", "paper-a", {"page": 1}),
        RetrievalDocument("chunk-b", "Evaluation B measures tool success.", "paper-b", {"page": 2}),
    )
    index = LanceDBDenseIndex(tmp_path / "db")
    index.publish(documents, ((1.0, 0.0), (0.0, 1.0)))
    retrieval = HybridRetrievalPipeline(
        documents,
        index,
        OpenAICompatibleEmbeddingAdapter(store, config, transport=SequenceTransport([
            {"data": [{"index": 0, "embedding": [1.0, 0.0]}]},
            {"data": [{"index": 0, "embedding": [0.0, 1.0]}]},
        ])),
        OpenAICompatibleRerankerAdapter(store, config, transport=SequenceTransport([
            {"results": [{"index": 0, "relevance_score": 0.9}, {"index": 1, "relevance_score": 0.1}]},
            {"results": [{"index": 1, "relevance_score": 0.9}, {"index": 0, "relevance_score": 0.1}]},
        ])),
    )
    worker_chat = OpenAICompatibleChatAdapter(store, config, transport=SequenceTransport([
        chat_response({"claims": [{"text": "Method A reduces context.", "evidence_ids": ["evidence-0001"]}]}, "draft-a"),
        chat_response({"assessments": [{"claim_id": "claim-0001", "evidence_ids": ["evidence-0001"], "relation": "supports", "verdict": "accepted", "rationale": "Direct."}]}, "verify-a"),
        chat_response({"claims": [{"text": "Evaluation B measures tool success.", "evidence_ids": ["evidence-0001"]}]}, "draft-b"),
        chat_response({"assessments": [{"claim_id": "claim-0001", "evidence_ids": ["evidence-0001"], "relation": "supports", "verdict": "accepted", "rationale": "Direct."}]}, "verify-b"),
    ]))
    manager_chat = OpenAICompatibleChatAdapter(store, config, transport=SequenceTransport([
        chat_response({
            "coverage_requirements": [
                {"coverage_id": "coverage-methods", "objective_quote": "methods"},
                {"coverage_id": "coverage-evaluation", "objective_quote": "evaluation"},
            ],
            "subquestions": [
                {"question": "What method reduces context?", "coverage_ids": ["coverage-methods"]},
                {"question": "How is success evaluated?", "coverage_ids": ["coverage-evaluation"]},
            ],
        }, "manager"),
        chat_response({"assessments": [
            {"coverage_id": "coverage-methods", "status": "covered", "claim_ids": ["sq1-claim-0001"], "rationale": "The method Claim addresses this requirement."},
            {"coverage_id": "coverage-evaluation", "status": "covered", "claim_ids": ["sq2-claim-0001"], "rationale": "The evaluation Claim addresses this requirement."},
        ]}, "coverage"),
    ]))
    result = ManagedVerifiedResearchWorkflow(store, VerifiedResearchWorkflow(store, retrieval, worker_chat), manager_chat).execute("Compare methods and evaluation", max_subquestions=2)
    assert len(result.subruns) == 2
    assert result.claim_count == 2
    assert result.disposition is DeliveryDisposition.COMPLETE
    assert all(item.status == "covered" for item in result.coverage_assessments)
    assert len(worker_chat.transport.requests) == 4
    report = store.path_for_digest(result.report_artifact_id.removeprefix("artifact-sha256-")).read_text(encoding="utf-8")
    assert "Subquestion 1" in report and "Subquestion 2" in report
    manifest = json.loads(
        store.path_for_digest(
            result.manifest_artifact_id.removeprefix("artifact-sha256-")
        ).read_text(encoding="utf-8")
    )
    assert manifest["coverage_assessment_artifact"]
    assert manifest["coverage_request_artifact"]
    assert manifest["stop_reason"] == "all_subquestions_verified"


def test_manager_rejects_unauthorized_time_scope():
    with pytest.raises(ValueError, match="unauthorized time constraint"):
        ManagedVerifiedResearchWorkflow._require_scope_preserved(
            "Compare recent methods.",
            ("What methods were published in 2024?", "How were they evaluated?"),
        )


def test_manager_plan_requires_grounded_and_fully_assigned_coverage():
    content = json.dumps({
        "coverage_requirements": [
            {"coverage_id": "coverage-1", "objective_quote": "invented constraint"},
        ],
        "subquestions": [
            {"question": "Check A", "coverage_ids": ["coverage-1"]},
            {"question": "Check B", "coverage_ids": ["coverage-1"]},
        ],
    })

    with pytest.raises(ValueError, match="quote the original objective"):
        ManagedVerifiedResearchWorkflow._parse_plan(
            "Compare A and B", content, max_subquestions=2
        )


@pytest.mark.parametrize("fixture_index", [0, 1])
def test_manager_plan_replays_observed_s16c_schema_failure(fixture_index):
    fixture = json.loads(FAILURE_FIXTURE.read_text(encoding="utf-8"))[
        "manager_plan_responses"
    ][fixture_index]

    with pytest.raises(ValueError, match="coverage requirement has an invalid schema"):
        ManagedVerifiedResearchWorkflow._parse_plan(
            fixture["objective"], json.dumps(fixture["observed"]), max_subquestions=2
        )

    requirements, subquestions = ManagedVerifiedResearchWorkflow._parse_plan(
        fixture["objective"], json.dumps(fixture["canonical"]), max_subquestions=2
    )
    assert requirements
    assert len(subquestions) == 2


def test_manager_plan_prompt_enumerates_exact_parser_schema(tmp_path):
    fixture = json.loads(FAILURE_FIXTURE.read_text(encoding="utf-8"))[
        "manager_plan_responses"
    ][0]
    store = LocalArtifactStore(tmp_path / "artifacts")
    config = ProviderConfig("https://provider.example/v1", "secret", "chat")
    transport = SequenceTransport(
        [chat_response(fixture["observed"], "observed-invalid-plan")]
    )
    chat = OpenAICompatibleChatAdapter(store, config, transport=transport)
    worker = type("UnusedWorker", (), {"execute": lambda self, question: None})()

    with pytest.raises(ValueError, match="coverage requirement has an invalid schema"):
        ManagedVerifiedResearchWorkflow(store, worker, chat).execute(
            fixture["objective"], max_subquestions=2
        )

    prompt = transport.requests[0]["messages"][0]["content"]
    assert "objective_quote" in prompt
    assert '"question"' in prompt
    assert '"coverage_ids"' in prompt
    assert "Do not use text, subquestion_id, or mapped_coverage_ids" in prompt


def test_manager_marks_missing_objective_coverage_partial(tmp_path):
    store = LocalArtifactStore(tmp_path / "artifacts")
    config = ProviderConfig("https://provider.example/v1", "secret", "chat")
    worker_report = store.put_bytes(
        b"# Supported result\n",
        media_type="text/markdown",
        producer_step_id="fixture-worker",
        schema_version="fixture-report.v1",
    )
    worker_manifest = store.put_json(
        {"disposition": "complete"},
        producer_step_id="fixture-worker",
        schema_version="fixture-manifest.v1",
    )
    supported = ResearchExecution(
        worker_report.artifact_id,
        worker_manifest.artifact_id,
        (Claim("claim-0001", "Method A reduces context.", "finding", "primary", "fixture"),),
        (EvidenceRef("evidence-0001", "source-1", {"page": 1}, "Method A reduces context.", "fixture"),),
        (Citation("citation-0001", "claim-0001", "evidence-0001", 1),),
        (),
        CoverageReport(1, 1, 1, 0, 0, "verified_delivery"),
    )
    worker = type("SupportedWorker", (), {"execute": lambda self, question, **kwargs: supported})()
    manager_chat = OpenAICompatibleChatAdapter(
        store,
        config,
        transport=SequenceTransport([
            chat_response({
                "coverage_requirements": [
                    {"coverage_id": "coverage-methods", "objective_quote": "methods"},
                    {"coverage_id": "coverage-evaluation", "objective_quote": "evaluation"},
                ],
                "subquestions": [
                    {"question": "Compare the methods.", "coverage_ids": ["coverage-methods"]},
                    {"question": "Compare their evaluation.", "coverage_ids": ["coverage-evaluation"]},
                ],
            }, "manager-partial"),
            chat_response({"assessments": [
                {"coverage_id": "coverage-methods", "status": "covered", "claim_ids": ["sq1-claim-0001"], "rationale": "Methods are addressed."},
                {"coverage_id": "coverage-evaluation", "status": "missing", "claim_ids": [], "rationale": "No evaluation outcome is reported."},
            ]}, "coverage-partial"),
        ]),
    )

    result = ManagedVerifiedResearchWorkflow(store, worker, manager_chat).execute(
        "Compare methods and evaluation", max_subquestions=2
    )

    assert result.disposition is DeliveryDisposition.PARTIAL
    assert result.unmet_criteria == (
        "Objective coverage was not demonstrated for: evaluation",
    )
    assert result.coverage_assessments[1].status == "missing"


def test_manager_aggregates_empty_subruns_as_no_answer(tmp_path):
    store = LocalArtifactStore(tmp_path / "artifacts")
    config = ProviderConfig("https://provider.example/v1", "secret", "chat")
    worker_report = store.put_bytes(
        b"# No answer\n",
        media_type="text/markdown",
        producer_step_id="fixture-worker",
        schema_version="fixture-report.v1",
    )
    worker_manifest = store.put_json(
        {"disposition": "no_answer"},
        producer_step_id="fixture-worker",
        schema_version="fixture-manifest.v1",
    )
    no_answer = ResearchExecution(
        worker_report.artifact_id,
        worker_manifest.artifact_id,
        (),
        (),
        (),
        (),
        CoverageReport(2, 0, 0, 0, 0, "no_supported_claim"),
        DeliveryDisposition.NO_ANSWER,
        ("No supported Claim.",),
    )
    worker = type("NoAnswerWorker", (), {"execute": lambda self, question, **kwargs: no_answer})()
    manager_chat = OpenAICompatibleChatAdapter(
        store,
        config,
        transport=SequenceTransport(
            [
                chat_response(
                    {
                        "coverage_requirements": [
                            {"coverage_id": "coverage-compare", "objective_quote": "Compare method A and method B"},
                        ],
                        "subquestions": [
                            {"question": "Check method A.", "coverage_ids": ["coverage-compare"]},
                            {"question": "Check method B.", "coverage_ids": ["coverage-compare"]},
                        ],
                    },
                    "manager-empty",
                )
            ]
        ),
    )

    result = ManagedVerifiedResearchWorkflow(store, worker, manager_chat).execute(
        "Compare method A and method B", max_subquestions=2
    )

    assert result.disposition is DeliveryDisposition.NO_ANSWER
    assert result.claim_count == result.evidence_count == result.citation_count == 0
    assert result.limitations
    report = store.path_for_digest(
        result.report_artifact_id.removeprefix("artifact-sha256-")
    ).read_text(encoding="utf-8")
    assert report.count("No evidence-supported answer") == 2


def _verified_chat(tmp_path, payloads):
    store = LocalArtifactStore(tmp_path / "artifacts")
    config = ProviderConfig("https://provider.example/v1", "secret", "chat")
    return OpenAICompatibleChatAdapter(store, config, transport=SequenceTransport(payloads))


def _run_with_writer(tmp_path, payloads):
    chat = _verified_chat(tmp_path, payloads)
    workflow, store = verified_workflow(tmp_path, chat)
    result = workflow.execute("How is context reduced?")
    manifest = json.loads(
        store.path_for_digest(
            result.manifest_artifact_id.removeprefix("artifact-sha256-")
        ).read_text(encoding="utf-8")
    )
    report = store.path_for_digest(
        result.report_artifact_id.removeprefix("artifact-sha256-")
    ).read_text(encoding="utf-8")
    return result, manifest, report


SINGLE_CLAIM_DRAFT = {"claims": [
    {"text": "The framework selects evidence before tool actions.", "evidence_ids": ["evidence-0001"]},
]}
SINGLE_CLAIM_VERIFY = {"assessments": [
    {"claim_id": "claim-0001", "evidence_ids": ["evidence-0001"], "relation": "supports", "verdict": "accepted", "rationale": "Direct."},
]}
THREE_CLAIM_DRAFT = {"claims": [
    {"text": "The framework selects evidence before tool actions.", "evidence_ids": ["evidence-0001"]},
    {"text": "The evaluation measures tool success.", "evidence_ids": ["evidence-0001"]},
    {"text": "The corpus is small.", "evidence_ids": ["evidence-0001"]},
]}
THREE_CLAIM_VERIFY = {"assessments": [
    {"claim_id": f"claim-{index:04d}", "evidence_ids": ["evidence-0001"], "relation": "supports", "verdict": "accepted", "rationale": "Direct."}
    for index in (1, 2, 3)
]}


def test_writer_invalid_json_degrades_to_v1_claim_list(tmp_path):
    result, manifest, report = _run_with_writer(tmp_path, [
        chat_response(SINGLE_CLAIM_DRAFT, "draft"),
        chat_response(SINGLE_CLAIM_VERIFY, "verify"),
        chat_response(WRITER_FIXTURE["distill_payloads"]["canonical"], "distill"),
        chat_response(WRITER_FIXTURE["writer_invalid_content"], "writer"),
    ])

    assert result.disposition is DeliveryDisposition.COMPLETE
    assert manifest["report_contract"] == "v1"
    assert manifest["writer_status"] == "degraded"
    assert manifest["writer_degrade_reason"].startswith("report writer failed")
    assert manifest["writer_document_artifact"] is None
    assert manifest["writer_response_artifact"]
    assert "Claim 1" in report
    assert "## 回答摘要" not in report
    assert "报告写作未通过校验，已降级为证据清单视图。" in report
    assert "报告写作未通过校验" in " ".join(manifest["limitations"])


def test_writer_unknown_claim_reference_degrades(tmp_path):
    result, manifest, report = _run_with_writer(tmp_path, [
        chat_response(SINGLE_CLAIM_DRAFT, "draft"),
        chat_response(SINGLE_CLAIM_VERIFY, "verify"),
        chat_response(WRITER_FIXTURE["distill_payloads"]["canonical"], "distill"),
        chat_response(WRITER_FIXTURE["writer_payloads"]["unknown_claim"], "writer"),
        chat_response(WRITER_FIXTURE["writer_payloads"]["unknown_claim"], "writer-retry"),
    ])

    assert manifest["report_contract"] == "v1"
    assert manifest["writer_status"] == "degraded"
    assert "references unknown Claims" in manifest["writer_degrade_reason"]
    assert "Claim 1" in report


def test_writer_audit_unsupported_verdict_degrades(tmp_path):
    result, manifest, report = _run_with_writer(tmp_path, [
        chat_response(TWO_CLAIM_DRAFT, "draft"),
        chat_response(TWO_CLAIM_VERIFY, "verify"),
        chat_response(WRITER_FIXTURE["distill_payloads"]["canonical"], "distill"),
        chat_response(WRITER_FIXTURE["writer_payloads"]["canonical"], "writer"),
        chat_response(WRITER_FIXTURE["audit_payloads"]["unsupported"], "audit"),
    ])

    assert manifest["report_contract"] == "v1"
    assert manifest["writer_status"] == "degraded"
    assert "audit rejected paragraph" in manifest["writer_degrade_reason"]
    assert "Claim 1" in report
    assert "## 回答摘要" not in report


def test_writer_omitting_majority_of_claims_degrades_before_audit(tmp_path):
    result, manifest, report = _run_with_writer(tmp_path, [
        chat_response(THREE_CLAIM_DRAFT, "draft"),
        chat_response(THREE_CLAIM_VERIFY, "verify"),
        chat_response(WRITER_FIXTURE["distill_payloads"]["canonical"], "distill"),
        chat_response(WRITER_FIXTURE["writer_payloads"]["over_omission"], "writer"),
    ])

    assert manifest["writer_status"] == "degraded"
    assert "omitted 2 of 3" in manifest["writer_degrade_reason"]
    assert manifest["writer_audit_request_artifact"] is None
    assert "Claim 1" in report


def test_writer_partial_coverage_appends_supplementary_section(tmp_path):
    result, manifest, report = _run_with_writer(tmp_path, [
        chat_response(TWO_CLAIM_DRAFT, "draft"),
        chat_response(TWO_CLAIM_VERIFY, "verify"),
        chat_response(WRITER_FIXTURE["distill_payloads"]["canonical"], "distill"),
        chat_response(WRITER_FIXTURE["writer_payloads"]["partial_coverage"], "writer"),
        chat_response(WRITER_FIXTURE["audit_payloads"]["canonical"], "audit"),
    ])

    assert manifest["report_contract"] == "v2"
    assert manifest["writer_status"] == "ok"
    assert manifest["unreferenced_claim_ids"] == ["claim-0002"]
    assert manifest["writer_warnings"]
    assert "## 补充发现" in report
    assert "The evaluation measures tool success." in report
    assert "## 回答摘要" in report


def test_multi_query_retrieval_merges_evidence_and_records_queries(tmp_path):
    store = LocalArtifactStore(tmp_path / "artifacts")
    config = ProviderConfig("https://provider.example/v1", "secret", "chat")
    documents = (
        RetrievalDocument("chunk-a", "Agents deduplicate memory writes by content hash before index refresh.", "paper-a", {"page": 1}),
        RetrievalDocument("chunk-b", "The retrieval index is refreshed after memory consolidation.", "paper-b", {"page": 2}),
    )
    index = LanceDBDenseIndex(tmp_path / "db")
    index.publish(documents, ((1.0, 0.0), (0.0, 1.0)))
    retrieval = HybridRetrievalPipeline(
        documents,
        index,
        OpenAICompatibleEmbeddingAdapter(store, config, transport=SequenceTransport([
            {"data": [{"index": 0, "embedding": [1.0, 0.0]}]},
            {"data": [{"index": 0, "embedding": [0.9, 0.1]}]},
            {"data": [{"index": 0, "embedding": [0.0, 1.0]}]},
        ])),
        OpenAICompatibleRerankerAdapter(store, config, transport=SequenceTransport([
            {"results": [{"index": 0, "relevance_score": 0.9}, {"index": 1, "relevance_score": 0.2}]},
            {"results": [{"index": 1, "relevance_score": 0.9}, {"index": 0, "relevance_score": 0.2}]},
            {"results": [{"index": 0, "relevance_score": 0.8}, {"index": 1, "relevance_score": 0.7}]},
        ])),
    )
    chat = OpenAICompatibleChatAdapter(store, config, transport=SequenceTransport([
        chat_response({"queries": ["memory write deduplication", "retrieval index refresh"]}, "plan"),
        chat_response({"claims": [
            {"text": "Memory writes are deduplicated by content hash.", "evidence_ids": ["evidence-0001"]},
            {"text": "The retrieval index is refreshed after consolidation.", "evidence_ids": ["evidence-0002"]},
        ]}, "draft"),
        chat_response({"assessments": [
            {"claim_id": "claim-0001", "evidence_ids": ["evidence-0001"], "relation": "supports", "verdict": "accepted", "rationale": "Direct."},
            {"claim_id": "claim-0002", "evidence_ids": ["evidence-0002"], "relation": "supports", "verdict": "accepted", "rationale": "Direct."},
        ]}, "verify"),
        chat_response(WRITER_FIXTURE["distill_payloads"]["two_cards"], "distill"),
        chat_response(WRITER_FIXTURE["writer_payloads"]["canonical"], "writer"),
        chat_response(WRITER_FIXTURE["audit_payloads"]["canonical"], "audit"),
    ]))
    result = VerifiedResearchWorkflow(store, retrieval, chat).execute(
        "How is agent memory maintained?", max_queries=4
    )
    manifest = json.loads(
        store.path_for_digest(result.manifest_artifact_id.removeprefix("artifact-sha256-")).read_text(encoding="utf-8")
    )
    retrieval_payload = json.loads(
        store.path_for_digest(manifest["retrieval_artifact"].removeprefix("artifact-sha256-")).read_text(encoding="utf-8")
    )

    assert manifest["search_queries"] == [
        "How is agent memory maintained?",
        "memory write deduplication",
        "retrieval index refresh",
    ]
    assert manifest["query_planning_warning"] is None
    assert len(retrieval_payload["queries"]) == 3
    assert len(retrieval_payload["runs"]) == 3
    assert result.coverage.evidence_count == 2
    assert result.coverage.accepted_claim_count == 2
    assert manifest["report_contract"] == "v2"
    draft_request = json.loads(chat.transport.requests[1]["messages"][1]["content"])
    assert {item["evidence_id"] for item in draft_request["evidence"]} == {"evidence-0001", "evidence-0002"}


def test_query_planning_failure_falls_back_to_single_query(tmp_path):
    store = LocalArtifactStore(tmp_path / "artifacts")
    config = ProviderConfig("https://provider.example/v1", "secret", "chat")
    documents = (
        RetrievalDocument("chunk-a", "Agents deduplicate memory writes by content hash.", "paper-a", {"page": 1}),
    )
    index = LanceDBDenseIndex(tmp_path / "db")
    index.publish(documents, ((1.0, 0.0),))
    retrieval = HybridRetrievalPipeline(
        documents,
        index,
        OpenAICompatibleEmbeddingAdapter(store, config, transport=SequenceTransport([{"data": [{"index": 0, "embedding": [1.0, 0.0]}]}])),
        OpenAICompatibleRerankerAdapter(store, config, transport=SequenceTransport([{"results": [{"index": 0, "relevance_score": 0.9}]}])),
    )
    chat = OpenAICompatibleChatAdapter(store, config, transport=SequenceTransport([
        chat_response("not-json", "plan-broken"),
        chat_response(SINGLE_CLAIM_DRAFT, "draft"),
        chat_response(SINGLE_CLAIM_VERIFY, "verify"),
        chat_response(WRITER_FIXTURE["distill_payloads"]["canonical"], "distill"),
        chat_response({"summary": {"text": "写入按内容哈希去重。", "claim_ids": ["claim-0001"]}, "sections": [{"heading": "机制", "paragraphs": [{"text": "写入按内容哈希去重。", "claim_ids": ["claim-0001"]}]}], "background": [], "open_questions": []}, "writer"),
        chat_response({"audits": [
            {"section_index": 0, "paragraph_index": 0, "verdict": "supported", "rationale": "Restates the claim."},
            {"section_index": 1, "paragraph_index": 0, "verdict": "supported", "rationale": "Restates the claim."},
        ]}, "audit"),
    ]))
    result = VerifiedResearchWorkflow(store, retrieval, chat).execute(
        "How is agent memory maintained?", max_queries=4
    )
    manifest = json.loads(
        store.path_for_digest(result.manifest_artifact_id.removeprefix("artifact-sha256-")).read_text(encoding="utf-8")
    )

    assert manifest["search_queries"] == ["How is agent memory maintained?"]
    assert manifest["query_planning_warning"].startswith("query planning failed")
    assert manifest["report_contract"] == "v2"
    assert result.disposition is DeliveryDisposition.COMPLETE
    assert len(chat.transport.requests) == 6


def test_writer_schema_violation_recovers_with_one_repair(tmp_path):
    broken = {"summary": {"text": "摘要。", "claim_ids": ["claim-0001"]}, "sections": [{"heading": "机制"}], "background": [], "open_questions": []}
    fixed = {"summary": {"text": "该框架先选择证据。", "claim_ids": ["claim-0001"]}, "sections": [{"heading": "机制", "paragraphs": [{"text": "证据选择发生在工具动作之前。", "claim_ids": ["claim-0001"]}]}], "background": [], "open_questions": []}
    result, manifest, report = _run_with_writer(tmp_path, [
        chat_response(SINGLE_CLAIM_DRAFT, "draft"),
        chat_response(SINGLE_CLAIM_VERIFY, "verify"),
        chat_response(WRITER_FIXTURE["distill_payloads"]["canonical"], "distill"),
        chat_response(broken, "writer"),
        chat_response(fixed, "writer-repair"),
        chat_response({"audits": [
            {"section_index": 0, "paragraph_index": 0, "verdict": "supported", "rationale": "Restates the claim."},
            {"section_index": 1, "paragraph_index": 0, "verdict": "supported", "rationale": "Restates the claim."},
        ]}, "audit"),
    ])

    assert manifest["report_contract"] == "v2"
    assert manifest["writer_status"] == "ok"
    assert "## 回答摘要" in report
