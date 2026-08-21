"""Local command-line entry point for Conflux-Weave."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Sequence

from conflux_weave.documents import LocalDocumentImporter, UnsupportedDocumentError
from conflux_weave.live_research import (
    FixedRepositoryIdentityWorkflow,
    LiveResearchValidationError,
)
from conflux_weave.provider import (
    OpenAICompatibleChatAdapter,
    ProviderConfig,
    ProviderConfigurationError,
    ProviderPortError,
)
from conflux_weave.runtime import (
    FixedOutcomeWorkflow,
    FixedValidationWorkflow,
    LocalArtifactStore,
    OutcomeScenario,
)
from conflux_weave.search import GitHubRepositorySearchAdapter, SearchPortError


def _print_json(value: object, *, indent: int | None = 2) -> None:
    text = json.dumps(value, ensure_ascii=False, indent=indent)
    encoding = sys.stdout.encoding or "utf-8"
    safe_text = text.encode(encoding, errors="backslashreplace").decode(encoding)
    print(safe_text)


def _configure_stdout() -> None:
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if callable(reconfigure):
        reconfigure(encoding="utf-8", errors="backslashreplace")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="conflux-weave")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser(
        "validate-workflow",
        help="run the W1.1 deterministic workflow shell without research calls",
    )
    validate.add_argument("--query", required=True, help="validation input text")
    validate.add_argument(
        "--artifact-root",
        type=Path,
        default=Path("var") / "artifacts" / "sha256",
        help="content-addressed artifact root",
    )
    document = subparsers.add_parser(
        "import-document",
        help="import a local PDF or Markdown document and emit a cited report",
    )
    document.add_argument("path", type=Path)
    document.add_argument(
        "--artifact-root",
        type=Path,
        default=Path("var") / "artifacts" / "sha256",
    )
    document.add_argument("--title", default=None)
    github = subparsers.add_parser(
        "search-github",
        help="discover GitHub repository candidates and optionally register one source",
    )
    github.add_argument("--query", required=True)
    github.add_argument("--limit", type=int, default=5)
    github.add_argument("--select", dest="selected_full_name")
    github.add_argument(
        "--artifact-root",
        type=Path,
        default=Path("var") / "artifacts" / "sha256",
    )
    outcome = subparsers.add_parser(
        "validate-outcome",
        help="validate W1.4 user-visible outcome semantics without external calls",
    )
    outcome.add_argument("--query", required=True)
    outcome.add_argument("--scenario", required=True, choices=[item.value for item in OutcomeScenario])
    outcome.add_argument(
        "--artifact-root",
        type=Path,
        default=Path("var") / "artifacts" / "sha256",
    )
    live_repository = subparsers.add_parser(
        "research-repository",
        help="run the W1.5 live evidence-bound repository identity workflow",
    )
    live_repository.add_argument("--query", required=True)
    live_repository.add_argument(
        "--dotenv",
        type=Path,
        default=Path(".env"),
        help="ignored local Provider configuration file",
    )
    live_repository.add_argument(
        "--artifact-root",
        type=Path,
        default=Path("var") / "artifacts" / "sha256",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    _configure_stdout()
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "research-repository":
        try:
            store = LocalArtifactStore(args.artifact_root)
            config = ProviderConfig.from_environment(args.dotenv)
            execution = FixedRepositoryIdentityWorkflow(
                store,
                GitHubRepositorySearchAdapter.from_environment(store),
                OpenAICompatibleChatAdapter(store, config),
                code_revision=_git_revision(),
            ).execute(args.query)
        except ProviderConfigurationError as exc:
            _print_json(
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
        except (SearchPortError, ProviderPortError, LiveResearchValidationError, ValueError) as exc:
            _print_json(
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
        _print_json(
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

    if args.command == "validate-outcome":
        try:
            execution = FixedOutcomeWorkflow(
                LocalArtifactStore(args.artifact_root)
            ).execute(args.query, OutcomeScenario(args.scenario))
        except ValueError as exc:
            _print_json(
                {
                    "status": "failed",
                    "error_code": "outcome_input_invalid",
                    "message": str(exc),
                }
            )
            return 2
        _print_json(
            {
                "run_id": execution.final_run.run_id,
                "run_status": execution.final_run.status.value,
                "step_status": execution.final_step.status.value,
                "scenario": args.scenario,
                "artifact_ref": execution.artifact.artifact_id,
                "delivery_disposition": (
                    execution.delivery.disposition.value
                    if execution.delivery
                    else None
                ),
                "user_input_request": (
                    {
                        "reason_code": execution.user_input_request.reason_code,
                        "prompt": execution.user_input_request.prompt,
                        "requested_inputs": list(
                            execution.user_input_request.requested_inputs
                        ),
                    }
                    if execution.user_input_request
                    else None
                ),
                "error": (
                    {
                        "code": execution.error.code,
                        "category": execution.error.category.value,
                        "retryable": execution.error.retryable,
                        "recovery_action": execution.error.recovery_action,
                    }
                    if execution.error
                    else None
                ),
                "validation_only": True,
                "network_called": False,
                "provider_called": False,
            }
        )
        return 1 if execution.error else 0

    if args.command == "search-github":
        try:
            adapter = GitHubRepositorySearchAdapter.from_environment(
                LocalArtifactStore(args.artifact_root)
            )
            result = adapter.search(args.query, limit=args.limit)
            registered = (
                adapter.register(result, full_name=args.selected_full_name)
                if args.selected_full_name
                else None
            )
        except (SearchPortError, ValueError) as exc:
            output = {
                "status": "failed",
                "error_code": getattr(exc, "code", "search_input_invalid"),
                "message": str(exc),
                "retryable": getattr(exc, "retryable", False),
                "status_code": getattr(exc, "status_code", None),
                "artifact_ref": getattr(exc, "artifact_ref", None),
                "recovery_action": getattr(exc, "recovery_action", "修正查询参数后重试。"),
                "provider_called": False,
            }
            _print_json(output)
            return 1
        output = {
            "status": "registered" if registered else "requires_selection",
            "query": result.query,
            "candidate_count": len(result.candidates),
            "candidates": [
                {
                    "full_name": candidate.full_name,
                    "html_url": candidate.html_url,
                    "description": candidate.description,
                    "stars": candidate.stars,
                    "archived": candidate.archived,
                    "fork": candidate.fork,
                }
                for candidate in result.candidates
            ],
            "rejected_items": list(result.rejected_items),
            "response_artifact_ref": result.response_artifact.artifact_id,
            "manifest_artifact_ref": result.manifest_artifact.artifact_id,
            "rate_limit_remaining": result.rate_limit_remaining,
            "selected_source": (
                {
                    "full_name": registered.candidate.full_name,
                    "source_snapshot_id": registered.source_snapshot.source_id,
                    "source_artifact_ref": registered.source_artifact.artifact_id,
                    "snapshot_artifact_ref": registered.snapshot_artifact.artifact_id,
                    "official_status": "selected_candidate_not_independently_verified",
                }
                if registered
                else None
            ),
            "identity_boundary": "GitHub search rank is not proof that a repository is official.",
            "network_called": True,
            "provider_called": False,
        }
        _print_json(output)
        return 0

    if args.command == "import-document":
        try:
            importer = LocalDocumentImporter(LocalArtifactStore(args.artifact_root))
            imported = importer.import_path(args.path)
            report = importer.build_report(imported, title=args.title)
        except FileNotFoundError as exc:
            _print_json(
                {
                    "status": "waiting_for_user",
                    "reason": "document_missing",
                    "message": str(exc),
                },
                indent=None,
            )
            return 0
        except (UnicodeDecodeError, UnsupportedDocumentError, ValueError) as exc:
            _print_json(
                {
                    "status": "failed",
                    "reason": "document_invalid",
                    "message": str(exc),
                },
                indent=None,
            )
            return 1
        _print_json(
            {
                "status": "succeeded",
                "document_id": imported.document_id,
                "source_snapshot_id": imported.source_snapshot.source_id,
                "source_artifact_id": imported.source_artifact.artifact_id,
                "snapshot_artifact_id": imported.snapshot_artifact.artifact_id,
                "segments_artifact_id": imported.segments_artifact.artifact_id,
                "report_artifact_id": report.report_artifact.artifact_id,
                "segment_count": len(imported.segments),
                "evidence_count": len(report.evidence),
                "citation_count": len(report.citations),
                "network_called": False,
                "provider_called": False,
            }
        )
        return 0

    if args.command != "validate-workflow":
        parser.error(f"unsupported command: {args.command}")

    try:
        execution = FixedValidationWorkflow(
            LocalArtifactStore(args.artifact_root)
        ).execute(args.query)
    except ValueError as exc:
        print(f"input error: {exc}", file=sys.stderr)
        return 2

    output = {
        "task_id": execution.task.task_id,
        "run_id": execution.final_run.run_id,
        "run_status": execution.final_run.status.value,
        "step_status": execution.final_step.status.value,
        "artifact_id": execution.artifact.artifact_id,
        "artifact_uri": execution.artifact.storage_uri,
        "validation_only": execution.validation_only,
        "evidence_boundary": execution.evidence_boundary,
        "error": execution.error.code if execution.error else None,
    }
    _print_json(output)
    return 0 if execution.error is None else 1


def _git_revision() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )
    revision = completed.stdout.strip()
    return revision if completed.returncode == 0 and revision else "unknown"
