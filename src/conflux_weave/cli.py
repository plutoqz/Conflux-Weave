"""Local command-line entry point for Conflux-Weave."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from conflux_weave.cli_commands.durable_paper import run as run_durable_paper
from conflux_weave.cli_commands.live_research import run as run_live_research
from conflux_weave.cli_commands.local_validation import run as run_local_validation
from conflux_weave.cli_commands.source_ingestion import run as run_source_ingestion
from conflux_weave.runtime import OutcomeScenario


_LIVE_RESEARCH_COMMANDS = frozenset(
    {"discover-papers", "research-repository", "review-document"}
)
_SOURCE_INGESTION_COMMANDS = frozenset({"search-github", "import-document", "manifest-corpus", "import-corpus", "retrieve-corpus"})
_LOCAL_VALIDATION_COMMANDS = frozenset({"validate-outcome", "validate-workflow"})


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
    manifest = subparsers.add_parser("manifest-corpus", help="create a read-only PDF corpus manifest")
    manifest.add_argument("path", type=Path)
    manifest.add_argument("--output", type=Path, default=Path("var") / "acceptance" / "v0.3-s1" / "corpus-manifest.json")
    corpus = subparsers.add_parser("import-corpus", help="import a PDF corpus with hash deduplication")
    corpus.add_argument("path", type=Path)
    corpus.add_argument("--artifact-root", type=Path, default=Path("var") / "artifacts" / "sha256")
    corpus.add_argument("--output", type=Path, default=Path("var") / "acceptance" / "v0.3-s1" / "corpus-import-manifest.json")
    retrieve = subparsers.add_parser("retrieve-corpus", help="run live BM25, LanceDB, RRF and rerank retrieval")
    retrieve.add_argument("--query", required=True)
    retrieve.add_argument("--import-manifest", type=Path, default=Path("var") / "acceptance" / "v0.3-s1" / "corpus-import-manifest.json")
    retrieve.add_argument("--lancedb", type=Path, default=Path("var") / "acceptance" / "v0.3-s1" / "lancedb")
    retrieve.add_argument("--table", default="paper_chunks")
    retrieve.add_argument("--dotenv", type=Path, default=Path(".env"))
    retrieve.add_argument("--artifact-root", type=Path, default=Path("var") / "artifacts" / "sha256")
    retrieve.add_argument("--output", type=Path, default=Path("var") / "acceptance" / "v0.3-s1" / "retrieval-run.json")
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
    outcome.add_argument(
        "--scenario",
        required=True,
        choices=[item.value for item in OutcomeScenario],
    )
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
    live_review = subparsers.add_parser(
        "review-document",
        help="run the W1.5 live cited reading-note workflow for a local PDF/Markdown document",
    )
    live_review.add_argument("path", type=Path)
    live_review.add_argument("--query", required=True)
    live_review.add_argument("--dotenv", type=Path, default=Path(".env"))
    live_review.add_argument(
        "--artifact-root",
        type=Path,
        default=Path("var") / "artifacts" / "sha256",
    )
    paper_discovery = subparsers.add_parser(
        "discover-papers",
        help="run the W2.5 bounded live arXiv paper-discovery workflow",
    )
    paper_discovery.add_argument("--query", required=True)
    paper_discovery.add_argument("--search-query", required=True)
    paper_discovery.add_argument("--max-results", type=int, default=15)
    paper_discovery.add_argument("--dotenv", type=Path, default=Path(".env"))
    paper_discovery.add_argument(
        "--artifact-root",
        type=Path,
        default=Path("var") / "artifacts" / "sha256",
    )
    durable = subparsers.add_parser(
        "durable-paper",
        help="submit, advance, or inspect the bounded W3 paper-discovery workflow",
    )
    durable_subparsers = durable.add_subparsers(dest="durable_command", required=True)
    durable_submit = durable_subparsers.add_parser(
        "submit", help="persist one frozen Run without making an external call"
    )
    durable_submit.add_argument("--query", required=True)
    durable_submit.add_argument("--strategy", choices=("fixed", "bounded"), default="fixed")
    durable_submit.add_argument("--search-query")
    durable_submit.add_argument("--max-results", type=int, default=15)
    durable_submit.add_argument("--task-summary")
    durable_submit.add_argument("--include", action="append", default=[])
    durable_submit.add_argument("--exclude", action="append", default=[])
    durable_submit.add_argument("--hard-constraint", action="append", default=[])
    durable_submit.add_argument("--dotenv", type=Path, default=Path(".env"))
    durable_worker = durable_subparsers.add_parser(
        "worker", help="claim and execute at most one durable Step"
    )
    durable_worker.add_argument("--once", action="store_true", required=True)
    durable_worker.add_argument("--strategy", choices=("fixed", "bounded"), default="fixed")
    durable_worker.add_argument("--dotenv", type=Path, default=Path(".env"))
    durable_status = durable_subparsers.add_parser(
        "status", help="read Run, Step, budget, Error, and Delivery state"
    )
    durable_status.add_argument("--run-id", required=True)
    for durable_parser in (durable_submit, durable_worker, durable_status):
        durable_parser.add_argument(
            "--database",
            type=Path,
            default=Path("var") / "db" / "conflux-weave.sqlite3",
        )
        durable_parser.add_argument(
            "--artifact-root",
            type=Path,
            default=Path("var") / "artifacts" / "sha256",
        )
    serve = subparsers.add_parser(
        "serve", help="start the single local FastAPI application and Worker"
    )
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--dotenv", type=Path, default=Path(".env"))
    serve.add_argument(
        "--database", type=Path, default=Path("var") / "db" / "conflux-weave.sqlite3"
    )
    serve.add_argument(
        "--artifact-root", type=Path, default=Path("var") / "artifacts" / "sha256"
    )
    serve.add_argument(
        "--workspace-root", type=Path, default=Path("var") / "workspace"
    )
    subparsers.add_parser(
        "offline-smoke",
        help="run the deterministic no-network package and Workbench smoke path",
    ).add_argument(
        "--data-root",
        type=Path,
        default=None,
        help="isolated output root; defaults to a temporary directory",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    _configure_stdout()
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "durable-paper":
        return run_durable_paper(args, _print_json)
    if args.command == "serve":
        from conflux_weave.server import build_local_app
        uvicorn = __import__("uvicorn")

        uvicorn.run(
            build_local_app(
                database=args.database,
                artifact_root=args.artifact_root,
                workspace_root=args.workspace_root,
                dotenv_path=args.dotenv,
            ),
            host=args.host,
            port=args.port,
            workers=1,
        )
        return 0
    if args.command == "offline-smoke":
        from conflux_weave.offline_smoke import main as run_offline_smoke

        smoke_args = [] if args.data_root is None else ["--data-root", str(args.data_root)]
        return run_offline_smoke(smoke_args)
    if args.command in _LIVE_RESEARCH_COMMANDS:
        return run_live_research(args, _print_json)
    if args.command in _SOURCE_INGESTION_COMMANDS:
        return run_source_ingestion(args, _print_json)
    if args.command in _LOCAL_VALIDATION_COMMANDS:
        return run_local_validation(args, _print_json)
    parser.error(f"unsupported command: {args.command}")
