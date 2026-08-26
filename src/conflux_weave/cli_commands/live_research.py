"""CLI handlers for evidence-bound live research workflows."""

from __future__ import annotations

from conflux_weave.cli_commands.shared import git_revision
from conflux_weave.live_research import (
    FixedRepositoryIdentityWorkflow,
    LiveResearchValidationError,
)
from conflux_weave.paper_discovery import (
    ArxivSearchAdapter,
    FixedPaperDiscoveryWorkflow,
    PaperSearchError,
)
from conflux_weave.provider import (
    OpenAICompatibleChatAdapter,
    ProviderConfig,
    ProviderConfigurationError,
    ProviderPortError,
)
from conflux_weave.review_live import FixedReviewReadingNoteWorkflow
from conflux_weave.runtime import LocalArtifactStore
from conflux_weave.search import GitHubRepositorySearchAdapter, SearchPortError


def run(args, print_json) -> int:
    handlers = {
        "discover-papers": _discover_papers,
        "research-repository": _research_repository,
        "review-document": _review_document,
    }
    return handlers[args.command](args, print_json)


def _discover_papers(args, print_json) -> int:
    try:
        store = LocalArtifactStore(args.artifact_root)
        config = ProviderConfig.from_environment(args.dotenv)
        execution = FixedPaperDiscoveryWorkflow(
            store,
            ArxivSearchAdapter(store),
            OpenAICompatibleChatAdapter(store, config),
            code_revision=git_revision(),
        ).execute(
            args.query,
            search_query=args.search_query,
            max_results=args.max_results,
        )
    except ProviderConfigurationError as exc:
        print_json(
            {
                "status": "failed",
                "error_code": "provider_configuration_invalid",
                "message": str(exc),
                "network_called": False,
                "provider_called": False,
            }
        )
        return 2
    except (
        PaperSearchError,
        ProviderPortError,
        LiveResearchValidationError,
        ValueError,
    ) as exc:
        provider_called = bool(
            getattr(exc, "request_artifact_ref", None)
            or getattr(exc, "response_artifact_ref", None)
        )
        print_json(
            {
                "status": "failed",
                "error_code": getattr(exc, "code", "paper_discovery_invalid"),
                "message": str(exc),
                "retryable": getattr(exc, "retryable", False),
                "status_code": getattr(exc, "status_code", None),
                "request_artifact_ref": getattr(exc, "request_artifact_ref", None),
                "response_artifact_ref": getattr(exc, "response_artifact_ref", None),
                "artifact_ref": getattr(exc, "artifact_ref", None),
                "recovery_action": getattr(
                    exc,
                    "recovery_action",
                    "检查 arXiv、Provider 和原始 Artifact 后显式创建新 Run。",
                ),
                "network_called": True,
                "provider_called": provider_called,
                "automatic_retry": bool(getattr(exc, "retry_delays", ())),
                "automatic_retry_scope": "read_only_source_get_only",
                "provider_automatic_retry": False,
                "fallback": False,
            }
        )
        return 1
    print_json(
        {
            "status": execution.final_run.status.value,
            "run_id": execution.final_run.run_id,
            "delivery_disposition": execution.delivery.disposition.value,
            "selected_arxiv_ids": [
                paper.arxiv_id for paper in execution.selected_papers
            ],
            "report_artifact_ref": execution.report_artifact.artifact_id,
            "report_uri": execution.report_artifact.storage_uri,
            "manifest_artifact_ref": execution.manifest_artifact.artifact_id,
            "claim_count": len(execution.claims),
            "evidence_count": len(execution.evidence),
            "citation_count": len(execution.citations),
            "provider_model": execution.provider_model,
            "provider_response_id": execution.provider_response_id,
            "usage": {
                "input_tokens": execution.input_tokens,
                "output_tokens": execution.output_tokens,
            },
            "limitations": list(execution.delivery.limitations),
            "unmet_criteria": list(execution.delivery.unmet_criteria),
            "network_called": True,
            "provider_called": True,
            "automatic_retry": bool(execution.source_retry_delays),
            "automatic_retry_scope": "read_only_source_get_only",
            "source_cache_hit": execution.source_cache_hit,
            "source_http_attempt_count": execution.source_attempt_count,
            "source_retry_delays": list(execution.source_retry_delays),
            "provider_automatic_retry": False,
            "fallback": False,
        }
    )
    return 0


def _research_repository(args, print_json) -> int:
    try:
        store = LocalArtifactStore(args.artifact_root)
        config = ProviderConfig.from_environment(args.dotenv)
        execution = FixedRepositoryIdentityWorkflow(
            store,
            GitHubRepositorySearchAdapter.from_environment(store),
            OpenAICompatibleChatAdapter(store, config),
            code_revision=git_revision(),
        ).execute(args.query)
    except ProviderConfigurationError as exc:
        print_json(
            {
                "status": "failed",
                "error_code": "provider_configuration_invalid",
                "message": str(exc),
                "recovery_action": "检查被 Git 忽略的本地 .env 配置。",
                "network_called": False,
                "provider_called": False,
            }
        )
        return 2
    except (
        SearchPortError,
        ProviderPortError,
        LiveResearchValidationError,
        ValueError,
    ) as exc:
        print_json(
            {
                "status": "failed",
                "error_code": getattr(exc, "code", "live_output_invalid"),
                "message": str(exc),
                "retryable": getattr(exc, "retryable", False),
                "status_code": getattr(exc, "status_code", None),
                "request_artifact_ref": getattr(exc, "request_artifact_ref", None),
                "response_artifact_ref": getattr(exc, "response_artifact_ref", None),
                "artifact_ref": getattr(exc, "artifact_ref", None),
                "recovery_action": getattr(
                    exc,
                    "recovery_action",
                    "检查原始 Artifact 和证据映射后显式创建新 Run。",
                ),
                "automatic_retry": False,
                "fallback": False,
            }
        )
        return 1
    print_json(
        {
            "status": execution.final_run.status.value,
            "run_id": execution.final_run.run_id,
            "delivery_disposition": execution.delivery.disposition.value,
            "selected_repository": execution.selected_repository.full_name,
            "report_artifact_ref": execution.report_artifact.artifact_id,
            "report_uri": execution.report_artifact.storage_uri,
            "manifest_artifact_ref": execution.manifest_artifact.artifact_id,
            "claim_count": len(execution.claims),
            "evidence_count": len(execution.evidence),
            "citation_count": len(execution.citations),
            "provider_model": execution.provider_model,
            "provider_response_id": execution.provider_response_id,
            "usage": {
                "input_tokens": execution.input_tokens,
                "output_tokens": execution.output_tokens,
            },
            "limitations": list(execution.delivery.limitations),
            "unmet_criteria": list(execution.delivery.unmet_criteria),
            "network_called": True,
            "provider_called": True,
            "automatic_retry": False,
            "fallback": False,
        }
    )
    return 0


def _review_document(args, print_json) -> int:
    try:
        store = LocalArtifactStore(args.artifact_root)
        config = ProviderConfig.from_environment(args.dotenv)
        execution = FixedReviewReadingNoteWorkflow(
            store,
            OpenAICompatibleChatAdapter(store, config),
            code_revision=git_revision(),
        ).execute(args.path, args.query)
    except ProviderConfigurationError as exc:
        print_json(
            {
                "status": "failed",
                "error_code": "provider_configuration_invalid",
                "message": str(exc),
                "network_called": False,
                "provider_called": False,
            }
        )
        return 2
    except (
        LiveResearchValidationError,
        ProviderPortError,
        ValueError,
        FileNotFoundError,
    ) as exc:
        print_json(
            {
                "status": "failed",
                "error_code": getattr(exc, "code", "review_input_invalid"),
                "message": str(exc),
                "request_artifact_ref": getattr(exc, "request_artifact_ref", None),
                "response_artifact_ref": getattr(exc, "response_artifact_ref", None),
                "recovery_action": getattr(
                    exc,
                    "recovery_action",
                    "检查输入和原始 Artifact 后显式创建新 Run。",
                ),
                "network_called": isinstance(exc, ProviderPortError),
                "provider_called": isinstance(exc, ProviderPortError),
                "automatic_retry": False,
                "fallback": False,
            }
        )
        return 1
    print_json(
        {
            "status": execution.final_run.status.value,
            "run_id": execution.final_run.run_id,
            "delivery_disposition": execution.delivery.disposition.value,
            "source_artifact_ref": execution.source_artifact.artifact_id,
            "report_artifact_ref": execution.report_artifact.artifact_id,
            "report_uri": execution.report_artifact.storage_uri,
            "manifest_artifact_ref": execution.manifest_artifact.artifact_id,
            "claim_count": len(execution.claims),
            "evidence_count": len(execution.evidence),
            "citation_count": len(execution.citations),
            "provider_model": execution.provider_model,
            "provider_response_id": execution.provider_response_id,
            "usage": {
                "input_tokens": execution.input_tokens,
                "output_tokens": execution.output_tokens,
            },
            "limitations": list(execution.delivery.limitations),
            "unmet_criteria": list(execution.delivery.unmet_criteria),
            "network_called": True,
            "provider_called": True,
            "automatic_retry": False,
            "fallback": False,
        }
    )
    return 0
