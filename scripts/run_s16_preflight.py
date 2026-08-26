from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
from pathlib import Path
import subprocess
from time import perf_counter

from conflux_weave.paper_discovery import (
    ArxivResponseCache,
    ArxivSearchAdapter,
    PaperSearchError,
    SourceAccessPolicy,
    SourceRequestGovernor,
)
from conflux_weave.provider import (
    OpenAICompatibleChatAdapter,
    OpenAICompatibleEmbeddingAdapter,
    OpenAICompatibleRerankerAdapter,
    ProviderConfig,
    ProviderPortError,
)
from conflux_weave.runtime import LocalArtifactStore


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-root", type=Path, default=Path("var/artifacts/sha256"))
    parser.add_argument("--dotenv", type=Path, default=Path(".env"))
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("var/acceptance/v0.3-s1/s16c-preflight/preflight-summary.json"),
    )
    parser.add_argument("--execute-live", action="store_true")
    args = parser.parse_args()
    if not args.execute_live:
        parser.error("--execute-live is required because preflight calls live services")

    store = LocalArtifactStore(args.artifact_root)
    config = ProviderConfig.from_environment(args.dotenv)
    started_at = _now()
    source_revision = _revision()
    source_dirty_at_start = _dirty()
    results = {}
    checks = (
        ("chat", lambda: _chat(store, config)),
        ("embedding", lambda: _embedding(store, config)),
        ("reranker", lambda: _reranker(store, config)),
        ("arxiv", lambda: _arxiv(store, args.output.parent / "source-cache")),
    )
    for name, check in checks:
        started = perf_counter()
        try:
            result = check()
            result["status"] = "validated_live"
        except (ProviderPortError, PaperSearchError) as exc:
            result = _port_error(exc)
        except Exception as exc:
            result = {
                "status": "failed_unclassified",
                "error_type": type(exc).__name__,
                "message": str(exc),
            }
        result["elapsed_ms"] = round((perf_counter() - started) * 1000, 1)
        results[name] = result

    completed = all(item["status"] == "validated_live" for item in results.values())
    payload = {
        "schema_version": "conflux-weave.s16c-live-preflight.v1",
        "started_at": started_at,
        "completed_at": _now(),
        "source_revision": source_revision,
        "source_dirty_at_start": source_dirty_at_start,
        "provider_automatic_retry": False,
        "results": results,
        "status": "validated_live" if completed else "failed_preflight",
        "evidence_boundary": (
            "This preflight validates one live request per Provider port and one fresh "
            "arXiv GET/Atom parse. It does not validate the eight-case research matrix."
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(args.output.read_text(encoding="utf-8"))
    if not completed:
        raise SystemExit(1)


def _chat(store, config):
    result = OpenAICompatibleChatAdapter(store, config).complete(
        system_prompt="Return exactly the requested token.",
        user_prompt="Return exactly: PREFLIGHT_OK",
        max_output_tokens=32,
        temperature=0.0,
        enable_thinking=False,
        producer_step_id="s16c-preflight-chat",
    )
    return {
        "model": result.model,
        "request_artifact": result.request_artifact.artifact_id,
        "response_artifact": result.response_artifact.artifact_id,
        "response_id": result.response_id,
        "finish_reason": result.finish_reason,
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
        "total_tokens": result.total_tokens,
    }


def _embedding(store, config):
    result = OpenAICompatibleEmbeddingAdapter(store, config).embed(
        ["S1.6-C live preflight"], producer_step_id="s16c-preflight-embedding"
    )
    return {
        "model": result.model,
        "request_artifact": result.request_artifact.artifact_id,
        "response_artifact": result.response_artifact.artifact_id,
        "dimensions": len(result.vectors[0]),
        "batch_size": len(result.vectors),
        "input_tokens": result.input_tokens,
    }


def _reranker(store, config):
    result = OpenAICompatibleRerankerAdapter(store, config).rerank(
        "context management for long-horizon agents",
        [
            "Context pruning reduces retained interaction history.",
            "A recipe describes bread fermentation.",
        ],
        producer_step_id="s16c-preflight-reranker",
    )
    return {
        "model": result.model,
        "request_artifact": result.request_artifact.artifact_id,
        "response_artifact": result.response_artifact.artifact_id,
        "ranked_indices": list(result.ranked_indices),
        "scores": list(result.scores),
    }


def _arxiv(store, cache_root):
    policy = SourceAccessPolicy(
        min_interval_seconds=0,
        max_attempts=1,
        retry_base_seconds=0,
        max_retry_wait_seconds=0,
        cache_ttl_seconds=0,
    )
    result = ArxivSearchAdapter(
        store,
        policy=policy,
        governor=SourceRequestGovernor(0),
        cache=ArxivResponseCache(cache_root, ttl_seconds=0),
    ).search(
        'all:"context management" AND all:agent',
        max_results=2,
        producer_step_id="s16c-preflight-arxiv",
    )
    manifest_digest = result.manifest_artifact.artifact_id.removeprefix(
        "artifact-sha256-"
    )
    manifest = json.loads(
        store.path_for_digest(manifest_digest).read_text(encoding="utf-8")
    )
    return {
        "search_query": result.search_query,
        "paper_count": len(result.papers),
        "selected_ids": [item.arxiv_id for item in result.papers],
        "response_artifact": result.response_artifact.artifact_id,
        "snapshot_artifact": result.snapshot_artifact.artifact_id,
        "manifest_artifact": result.manifest_artifact.artifact_id,
        "attempt_artifacts": manifest["attempt_artifact_refs"],
        "cache_hit": result.cache_hit,
        "attempt_count": result.attempt_count,
        "retry_delays": list(result.retry_delays),
        "automatic_retry": bool(result.retry_delays),
    }


def _port_error(exc):
    return {
        "status": "failed_live",
        "code": exc.code,
        "message": str(exc),
        "retryable": exc.retryable,
        "status_code": exc.status_code,
        "request_artifact": getattr(exc, "request_artifact_ref", None),
        "response_artifact": getattr(exc, "response_artifact_ref", None),
        "failure_artifact": getattr(exc, "artifact_ref", None),
        "attempt_artifacts": list(getattr(exc, "attempt_artifact_refs", ())),
        "retry_delays": list(getattr(exc, "retry_delays", ())),
        "recovery_action": exc.recovery_action,
    }


def _revision():
    return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()


def _dirty():
    return bool(subprocess.check_output(["git", "status", "--porcelain"], text=True).strip())


def _now():
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


if __name__ == "__main__":
    main()
