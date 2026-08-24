"""One-shot child Worker used by W3.5 real-process fault injection tests."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import time

from conflux_weave.runtime import (
    DurablePaperDiscoveryRuntime,
    LocalArtifactStore,
    SQLiteRuntimeRepository,
)
from conflux_weave.paper_discovery import ArxivHttpResponse, ArxivSearchAdapter
from conflux_weave.provider import (
    OpenAICompatibleChatAdapter,
    ProviderConfig,
    ProviderHttpResponse,
)


ATOM = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"><entry>
<id>http://arxiv.org/abs/2608.00002v1</id>
<updated>2026-08-02T00:00:00Z</updated><published>2026-08-02T00:00:00Z</published>
<title>Context Management for Language Model Agents</title>
<summary>Methods for managing context in long-running LLM agents.</summary>
<author><name>B. Author</name></author>
<link href="http://arxiv.org/abs/2608.00002v1" rel="alternate" />
<category term="cs.AI" /></entry></feed>"""


def append_call(path: Path, name: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="ascii") as handle:
        handle.write(name + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def signal_and_wait(path: Path, point: str) -> None:
    path.write_text(point, encoding="ascii")
    time.sleep(300)


class ArxivTransport:
    def __init__(self, call_log: Path):
        self.call_log = call_log

    def get(self, url, *, headers, timeout_seconds):
        append_call(self.call_log, "arxiv")
        return ArxivHttpResponse(200, ATOM, {"Content-Type": "application/atom+xml"})


class ProviderTransport:
    def __init__(self, call_log: Path, fault: str, signal: Path):
        self.call_log = call_log
        self.fault = fault
        self.signal = signal

    def post(self, url, *, headers, body, timeout_seconds):
        append_call(self.call_log, "provider")
        if self.fault == "provider_outcome_unknown":
            signal_and_wait(self.signal, self.fault)
        response = {
            "id": "chatcmpl-subprocess",
            "model": "fixture-model",
            "choices": [{
                "message": {"role": "assistant", "content": json.dumps({
                    "claims": [{
                        "text": "Context Management（2026）研究长时 Agent 的上下文管理。",
                        "evidence_ids": ["arxiv-paper-01"],
                    }]
                }, ensure_ascii=False)},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 120, "completion_tokens": 40, "total_tokens": 160},
        }
        return ProviderHttpResponse(
            200,
            json.dumps(response, ensure_ascii=False).encode(),
            {"Content-Type": "application/json"},
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--call-log", type=Path, required=True)
    parser.add_argument("--signal", type=Path, required=True)
    parser.add_argument("--fault", required=True)
    parser.add_argument("--now", required=True)
    args = parser.parse_args()

    store = LocalArtifactStore(args.artifacts)
    repository = SQLiteRuntimeRepository(
        args.database, store, clock=lambda: args.now
    )
    search = ArxivSearchAdapter(
        store,
        transport=ArxivTransport(args.call_log),
        acquired_at=args.now,
    )
    provider = OpenAICompatibleChatAdapter(
        store,
        ProviderConfig(
            "https://provider.example/v1", "fixture-secret", "fixture-model"
        ),
        transport=ProviderTransport(args.call_log, args.fault, args.signal),
    )

    def fault_hook(point: str) -> None:
        if point == args.fault:
            signal_and_wait(args.signal, point)

    runtime = DurablePaperDiscoveryRuntime(
        repository,
        store,
        search,
        provider,
        worker_id="subprocess-worker",
        lease_seconds=10,
        clock=lambda: args.now,
        fault_hook=fault_hook,
    )
    runtime.work_once(now=args.now)


if __name__ == "__main__":
    main()
