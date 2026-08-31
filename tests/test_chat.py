import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from conflux_weave.api_contracts import ChatMessageRequest
from conflux_weave.runtime import LocalArtifactStore as _Store
from conflux_weave.chat import ChatService, _check_rag_answer
from conflux_weave.provider import (
    OpenAICompatibleChatAdapter,
    ProviderConfig,
    ProviderHttpResponse,
)
from conflux_weave.runtime import LocalArtifactStore
from conflux_weave.server import create_app


class SequenceTransport:
    def __init__(self, payloads):
        self.payloads = iter(payloads)
        self.requests = []

    def post(self, *args, **kwargs):
        self.requests.append(json.loads(kwargs["body"]))
        payload = next(self.payloads)
        return ProviderHttpResponse(200, json.dumps(payload).encode(), {"Content-Type": "application/json"})


def chat_response(content, response_id):
    return {
        "id": response_id,
        "model": "fixture-chat",
        "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
    }


def build_service(tmp_path, payloads):
    transport = SequenceTransport(payloads)
    config = ProviderConfig("https://provider.example/v1", "secret", "chat")
    adapter = OpenAICompatibleChatAdapter(
        LocalArtifactStore(tmp_path / "artifacts"), config, transport=transport
    )
    service = ChatService(adapter, tmp_path / "chat.sqlite3")
    return service, transport


def test_direct_answer_persists_conversation_and_includes_history(tmp_path):
    service, transport = build_service(
        tmp_path,
        [chat_response("你好！有什么可以帮你？", "r1"), chat_response("Conflux-Weave 是本地研究工作台。", "r2")],
    )

    first = service.direct_answer("你好", None)
    assert first["role"] == "assistant"
    assert first["conversation_id"].startswith("conv-")
    second = service.direct_answer("介绍一下它", first["conversation_id"])

    assert "本地研究工作台" in second["content"]
    history = service.history(limit=10)
    assert [message.role for message in history] == ["user", "assistant", "user", "assistant"]
    # 第二次调用必须携带同会话历史作为上下文
    user_content = transport.requests[1]["messages"][1]["content"]
    assert "user: 你好" in user_content and "assistant: 你好！有什么可以帮你？" in user_content
    assert "介绍一下它" in user_content


def test_chat_messages_schema_version_is_recorded(tmp_path):
    service, _ = build_service(tmp_path, [chat_response("ok", "r1")])
    message = service.history(limit=1)
    assert message == []
    service.direct_answer("问题", None)
    stored = service.history(limit=5)
    assert stored[0].mode == "direct"
    assert stored[0].message_id.startswith("msg-")


def test_chat_endpoints_use_injected_service(tmp_path):
    service, _ = build_service(tmp_path, [chat_response("工作台直接回答。", "r1")])
    repository = SimpleNamespace()
    app = create_app(repository, SimpleNamespace(), chat_service=service)

    submit = next(item.endpoint for item in app.routes if item.path == "/api/v1/chat")
    list_messages = next(item.endpoint for item in app.routes if item.path == "/api/v1/chat/messages")

    response = submit(ChatMessageRequest(question="这个问题怎么看？", conversation_id=None))
    assert response.content == "工作台直接回答。"
    assert response.provider_response_id == "r1"
    # W3.3：响应元数据携带证据基准标记（持久化语义，前端角标之外仍可读）
    assert response.verification == "model-knowledge"
    assert response.checks is None

    history = asyncio.run(list_messages(limit=20))
    assert [item.role for item in history.items] == ["user", "assistant"]
    assert history.items[0].content == "这个问题怎么看？"


def test_chat_endpoint_without_service_returns_503(tmp_path):
    app = create_app(SimpleNamespace(), SimpleNamespace(), chat_service=None)
    submit = next(item.endpoint for item in app.routes if item.path == "/api/v1/chat")

    response = submit(ChatMessageRequest(question="你好", conversation_id=None))

    assert response.status_code == 503
    body = json.loads(response.body)
    assert body["code"] == "provider_not_configured"


class FakeHit:
    def __init__(self, document_id, score, snapshot):
        self.document_id = document_id
        self.score = score
        self.rank = 1
        self.source_snapshot_id = snapshot
        self.locator = {"page": 3}


class FakeRetrieval:
    def __init__(self, texts):
        self.document_by_id = {
            f"chunk-{index}": SimpleNamespace(text=text)
            for index, text in enumerate(texts, 1)
        }
        self.searched = None

    def search(self, query):
        self.searched = query
        hits = [
            FakeHit(f"chunk-{index}", 0.9 - index * 0.1, f"snap-{index}")
            for index in range(1, len(self.document_by_id) + 1)
        ]
        run = SimpleNamespace(final=SimpleNamespace(hits=hits))
        return run


def build_rag_service(tmp_path, payloads, texts=("Agents use skills to act.", "Memory dedup by hash.")):
    service, transport = build_service(tmp_path, payloads)
    retrieval = FakeRetrieval(texts)
    service._retrieval = retrieval
    service._store = LocalArtifactStore(tmp_path / "ctx")
    return service, transport, retrieval


GOOD_RAG_ANSWER = (
    "智能体的能力执行依赖技能机制：技能是可复用的行为单元，智能体在完成规划后"
    "调用对应技能来执行具体动作，从而把意图落到操作上 [1]。记忆侧则按内容哈希"
    "做去重，重复条目不会继续膨胀存储空间 [2]。综合来看，技能决定智能体能做"
    "什么，记忆决定它能记住什么，两者共同支撑智能体在长任务中的稳定运行。"
)


def test_rag_answer_cites_snippets_and_stores_context(tmp_path):
    service, transport, retrieval = build_rag_service(
        tmp_path, [chat_response(GOOD_RAG_ANSWER, "r9")]
    )

    result = service.rag_answer("智能体怎么用技能？", None)

    assert retrieval.searched == "智能体怎么用技能？"
    assert result["mode"] == "rag"
    assert result["verification"] == "unverified-aggregation"
    assert result["checks"] == {"status": "passed", "violations": []}
    assert "[1]" in result["content"] and "未核验聚合" in result["content"]
    assert "⚠" not in result["content"]
    assert len(result["citations"]) == 2
    assert result["citations"][0]["chunk_id"] == "chunk-1"
    history = service.history(limit=5)
    assert [m.role for m in history] == ["user", "assistant"]
    assert history[1].mode == "rag" and history[1].context_artifact_id
    prompt = transport.requests[0]["messages"][1]["content"]
    assert "[1] chunk `chunk-1`" in prompt and "Agents use skills to act." in prompt
    # 上下文工件记录后检结果（新增字段，schema v1 兼容扩展）
    artifact_file = next(p for p in (tmp_path / "ctx").rglob("*") if p.is_file())
    payload = json.loads(artifact_file.read_text(encoding="utf-8"))
    assert payload["answer_checks"] == {
        "status": "passed",
        "violations": [],
        "attempts": 1,
    }


def test_rag_answer_retries_once_then_passes_check(tmp_path):
    weak = "这个问题涉及多个方面，简单来说智能体可以使用技能完成任务。"
    service, transport, _ = build_rag_service(
        tmp_path, [chat_response(weak, "r1"), chat_response(GOOD_RAG_ANSWER, "r2")]
    )

    result = service.rag_answer("智能体怎么用技能？", None)

    assert len(transport.requests) == 2
    feedback = transport.requests[1]["messages"][1]["content"]
    assert "【重试】" in feedback and "至少需要 1 个" in feedback
    assert result["checks"] == {"status": "passed", "violations": []}
    assert "⚠" not in result["content"]


def test_rag_answer_degrades_after_failed_retry(tmp_path):
    weak = "这个问题涉及多个方面，简单来说智能体可以使用技能完成任务。"
    service, transport, _ = build_rag_service(
        tmp_path, [chat_response(weak, "r1"), chat_response(weak, "r2")]
    )

    result = service.rag_answer("智能体怎么用技能？", None)

    assert len(transport.requests) == 2  # 首答 + 一次重试，绝不更多
    assert result["checks"]["status"] == "degraded"
    assert result["checks"]["violations"]
    assert "⚠" in result["content"] and "已按模型原文交付" in result["content"]


def test_rag_check_rejects_out_of_range_citation_and_low_coverage():
    # 引用编号超出片段范围
    assert any("超出片段范围" in v for v in _check_rag_answer("回答内容包含 [5]。", 2))
    # 长度达标但 4 个片段只引用 1 个：素材回避
    padded = "长" * 300 + " [1]"
    assert any("仅实际引用" in v for v in _check_rag_answer(padded, 4))


def test_rag_unavailable_without_retrieval(tmp_path):
    service, _ = build_service(tmp_path, [])
    try:
        service.rag_answer("问题", None)
        raise AssertionError("expected RuntimeError")
    except RuntimeError as exc:
        assert "corpus" in str(exc)
