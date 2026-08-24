"""One-shot child Worker for W4.4 bounded-strategy kill tests."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import time

from conflux_weave.paper_discovery import ArxivHttpResponse, ArxivSearchAdapter
from conflux_weave.planning import BOUNDED_STRATEGY_ID, PLAN_SCHEMA_VERSION
from conflux_weave.provider import (
    OpenAICompatibleChatAdapter,
    ProviderConfig,
    ProviderHttpResponse,
)
from conflux_weave.runtime import (
    BoundedPaperStrategyRuntime,
    LocalArtifactStore,
    SQLiteRuntimeRepository,
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


def planner_content(request: dict) -> str:
    prompt = request["messages"][1]["content"]
    context = json.loads(
        prompt.split("ContextBundle:\n", 1)[1].split("\nRequired root keys:", 1)[0]
    )
    refs = [item["constraint_id"] for item in context["constraints"]]
    return json.dumps(
        {
            "schema_version": PLAN_SCHEMA_VERSION,
            "strategy_id": BOUNDED_STRATEGY_ID,
            "strategy_version": "bounded-arxiv-prompt-v1",
            "context_sha256": context["content_sha256"],
            "objective": "Find relevant Agent context-management papers.",
            "actions": [
                {
                    "action_id": "search-1",
                    "action_type": "tool_call",
                    "tool_id": "search_arxiv",
                    "arguments": {
                        "query": "all:agent AND all:context",
                        "max_results": 15,
                    },
                    "expected_evidence": ["arXiv metadata and abstracts"],
                    "constraint_refs": refs,
                },
                {
                    "action_id": "finish-1",
                    "action_type": "finish",
                    "tool_id": None,
                    "arguments": {},
                    "expected_evidence": [],
                    "constraint_refs": [],
                },
            ],
            "stop_reason": "Stop after the validated bounded search.",
        }
    )


class ArxivTransport:
    def __init__(self, call_log: Path, fault: str, signal: Path):
        self.call_log = call_log
        self.fault = fault
        self.signal = signal

    def get(self, url, *, headers, timeout_seconds):
        append_call(self.call_log, "arxiv")
        if self.fault == "search_outcome_unknown":
            signal_and_wait(self.signal, self.fault)
        return ArxivHttpResponse(200, ATOM, {"Content-Type": "application/atom+xml"})


class ProviderTransport:
    def __init__(self, call_log: Path, fault: str, signal: Path):
        self.call_log = call_log
        self.fault = fault
        self.signal = signal

    def post(self, url, *, headers, body, timeout_seconds):
        request = json.loads(body)
        planner = request["max_tokens"] == 768
        name = "planner" if planner else "synthesis"
        append_call(self.call_log, name)
        if self.fault == f"{name}_outcome_unknown":
            signal_and_wait(self.signal, self.fault)
        content = (
            planner_content(request)
            if planner
            else json.dumps(
                {
                    "claims": [
                        {
                            "text": "该论文研究长时 Agent 的上下文管理。",
                            "evidence_ids": ["arxiv-paper-01"],
                        }
                    ]
                },
                ensure_ascii=False,
            )
        )
        response = {
            "id": f"chatcmpl-{name}-subprocess",
            "model": "fixture-model",
            "choices": [
                {
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 120,
                "completion_tokens": 40,
                "total_tokens": 160,
            },
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
    runtime = BoundedPaperStrategyRuntime(
        repository,
        store,
        ArxivSearchAdapter(
            store,
            transport=ArxivTransport(args.call_log, args.fault, args.signal),
            acquired_at=args.now,
        ),
        OpenAICompatibleChatAdapter(
            store,
            ProviderConfig(
                "https://provider.example/v1", "fixture-secret", "fixture-model"
            ),
            transport=ProviderTransport(args.call_log, args.fault, args.signal),
        ),
        worker_id="w4-subprocess-worker",
        lease_seconds=10,
        clock=lambda: args.now,
        fault_hook=lambda point: (
            signal_and_wait(args.signal, point) if point == args.fault else None
        ),
    )
    runtime.work_once(now=args.now)


if __name__ == "__main__":
    main()
