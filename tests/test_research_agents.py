import json
import pytest

from conflux_weave.hybrid_retrieval import HybridRetrievalPipeline
from conflux_weave.indexing import LanceDBDenseIndex
from conflux_weave.managed_research import ManagedVerifiedResearchWorkflow
from conflux_weave.provider import OpenAICompatibleChatAdapter, OpenAICompatibleEmbeddingAdapter, OpenAICompatibleRerankerAdapter, ProviderConfig, ProviderHttpResponse
from conflux_weave.research_agents import VerifiedResearchWorkflow
from conflux_weave.retrieval import RetrievalDocument
from conflux_weave.runtime import LocalArtifactStore


class SequenceTransport:
    def __init__(self, payloads): self.payloads = iter(payloads)
    def post(self, *args, **kwargs):
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
    ]))
    result = VerifiedResearchWorkflow(store, retrieval, chat).execute("How is context reduced?")
    assert result.coverage.accepted_claim_count == 1
    assert result.coverage.repair_rounds == 0
    assert len(result.claims) == len(result.citations) == 1
    report = store.path_for_digest(result.report_artifact_id.removeprefix("artifact-sha256-")).read_text(encoding="utf-8")
    assert "## Evidence" in report and "paper-a" in report
    manifest = json.loads(store.path_for_digest(result.manifest_artifact_id.removeprefix("artifact-sha256-")).read_text(encoding="utf-8"))
    assert len(manifest["harness_artifacts"]) == 10


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
    ]))
    result = VerifiedResearchWorkflow(store, retrieval, chat).execute("What is the result?")
    assert result.coverage.repair_rounds == 1
    assert result.claims[0].text == "The measured result is 20 percent."


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
        chat_response({"subquestions": ["What method reduces context?", "How is success evaluated?"], "stop_condition": "Both dimensions verified."}, "manager")
    ]))
    result = ManagedVerifiedResearchWorkflow(store, VerifiedResearchWorkflow(store, retrieval, worker_chat), manager_chat).execute("Compare methods and evaluation", max_subquestions=2)
    assert len(result.subruns) == 2
    assert result.claim_count == 2
    report = store.path_for_digest(result.report_artifact_id.removeprefix("artifact-sha256-")).read_text(encoding="utf-8")
    assert "Subquestion 1" in report and "Subquestion 2" in report


def test_manager_rejects_unauthorized_time_scope():
    with pytest.raises(ValueError, match="unauthorized time constraint"):
        ManagedVerifiedResearchWorkflow._require_scope_preserved(
            "Compare recent methods.",
            ("What methods were published in 2024?", "How were they evaluated?"),
        )
