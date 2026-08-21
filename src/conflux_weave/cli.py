"""Local command-line entry point for Conflux-Weave."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

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
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

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
