"""Local command-line entry point for Conflux-Weave."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from conflux_weave.documents import LocalDocumentImporter, UnsupportedDocumentError
from conflux_weave.runtime import FixedValidationWorkflow, LocalArtifactStore


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
    document.add_argument("--artifact-root", type=Path, default=Path("var") / "artifacts" / "sha256")
    document.add_argument("--title", default=None)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "import-document":
        try:
            importer = LocalDocumentImporter(LocalArtifactStore(args.artifact_root))
            imported = importer.import_path(args.path)
            report = importer.build_report(imported, title=args.title)
        except FileNotFoundError as exc:
            print(json.dumps({"status": "waiting_for_user", "reason": "document_missing", "message": str(exc)}, ensure_ascii=False))
            return 0
        except (UnicodeDecodeError, UnsupportedDocumentError, ValueError) as exc:
            print(json.dumps({"status": "failed", "reason": "document_invalid", "message": str(exc)}, ensure_ascii=False))
            return 1
        print(json.dumps({
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
        }, ensure_ascii=False, indent=2))
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
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0 if execution.error is None else 1
