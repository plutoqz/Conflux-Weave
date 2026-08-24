"""CLI handlers for source discovery and local document ingestion."""

from __future__ import annotations

from conflux_weave.documents import LocalDocumentImporter, UnsupportedDocumentError
from conflux_weave.runtime import LocalArtifactStore
from conflux_weave.search import GitHubRepositorySearchAdapter, SearchPortError


def run(args, print_json) -> int:
    if args.command == "search-github":
        return _search_github(args, print_json)
    return _import_document(args, print_json)


def _search_github(args, print_json) -> int:
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
        print_json(
            {
                "status": "failed",
                "error_code": getattr(exc, "code", "search_input_invalid"),
                "message": str(exc),
                "retryable": getattr(exc, "retryable", False),
                "status_code": getattr(exc, "status_code", None),
                "artifact_ref": getattr(exc, "artifact_ref", None),
                "recovery_action": getattr(
                    exc, "recovery_action", "修正查询参数后重试。"
                ),
                "provider_called": False,
            }
        )
        return 1
    print_json(
        {
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
    )
    return 0


def _import_document(args, print_json) -> int:
    try:
        importer = LocalDocumentImporter(LocalArtifactStore(args.artifact_root))
        imported = importer.import_path(args.path)
        report = importer.build_report(imported, title=args.title)
    except FileNotFoundError as exc:
        print_json(
            {
                "status": "waiting_for_user",
                "reason": "document_missing",
                "message": str(exc),
            },
            indent=None,
        )
        return 0
    except (UnicodeDecodeError, UnsupportedDocumentError, ValueError) as exc:
        print_json(
            {
                "status": "failed",
                "reason": "document_invalid",
                "message": str(exc),
            },
            indent=None,
        )
        return 1
    print_json(
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
