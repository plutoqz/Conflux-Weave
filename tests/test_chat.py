import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from conflux_weave.api_contracts import ChatMessageRequest
from conflux_weave.chat import ChatService
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
