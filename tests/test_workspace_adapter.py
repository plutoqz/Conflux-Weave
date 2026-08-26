from pathlib import Path

import pytest

from conflux_weave.harness import (
    LocalWorkspaceAdapter,
    WorkspaceAccess,
    WorkspaceAccessDenied,
    WorkspaceConflict,
    WorkspaceKind,
)
from conflux_weave.runtime import LocalArtifactStore


def adapter(tmp_path: Path) -> LocalWorkspaceAdapter:
    system = tmp_path / "system"
    system.mkdir()
    (system / "template.md").write_text("template", encoding="utf-8")
    return LocalWorkspaceAdapter(
        tmp_path / "workspace",
        system,
        LocalArtifactStore(tmp_path / "artifacts"),
    )


def access() -> WorkspaceAccess:
    return WorkspaceAccess(
        agent_instance_id="research-1",
        run_id="run-1",
        project_ids=("project-1",),
    )


def test_write_read_list_and_publish(tmp_path: Path) -> None:
    workspace = adapter(tmp_path)
    uri = "weave://runs/run-1/results/note.md"

    ref = workspace.write_bytes_atomic(
        uri,
        "# Result\n".encode(),
        access(),
        media_type="text/markdown",
    )
    listed = workspace.list_dir("weave://runs/run-1/results", access())
    artifact = workspace.publish_artifact(
        ref,
        access(),
        producer_step_id="run-1:fixture",
        schema_version="fixture-result.v1",
    )

    assert workspace.read_bytes(uri, access()) == b"# Result\n"
    assert listed == (ref,)
    assert artifact.content_hash == ref.revision


def test_revision_conflict_does_not_overwrite(tmp_path: Path) -> None:
    workspace = adapter(tmp_path)
    uri = "weave://projects/project-1/notes/note.md"
    first = workspace.write_bytes_atomic(
        uri, b"first", access(), media_type="text/markdown"
    )
    workspace.write_bytes_atomic(
        uri,
        b"second",
        access(),
        media_type="text/markdown",
        expected_revision=first.revision,
    )

    with pytest.raises(WorkspaceConflict):
        workspace.write_bytes_atomic(
            uri,
            b"stale",
            access(),
            media_type="text/markdown",
            expected_revision=first.revision,
        )

    assert workspace.read_bytes(uri, access()) == b"second"


@pytest.mark.parametrize(
    "uri",
    [
        "file:///tmp/secret",
        "weave://runs/run-1/../run-2/secret",
        "weave://runs/run-1/%2e%2e/run-2/secret",
        "weave://unknown/value",
    ],
)
def test_invalid_or_escaping_uri_is_rejected(tmp_path: Path, uri: str) -> None:
    with pytest.raises((ValueError, WorkspaceAccessDenied)):
        adapter(tmp_path).resolve(uri, access())


def test_scope_and_system_write_are_rejected(tmp_path: Path) -> None:
    workspace = adapter(tmp_path)

    with pytest.raises(WorkspaceAccessDenied):
        workspace.resolve("weave://scratch/other/file.txt", access(), for_write=True)
    with pytest.raises(WorkspaceAccessDenied):
        workspace.resolve("weave://projects/other/file.txt", access(), for_write=True)
    with pytest.raises(WorkspaceAccessDenied):
        workspace.write_bytes_atomic(
            "weave://system/template.md",
            b"changed",
            access(),
            media_type="text/markdown",
        )


def test_system_listing_is_read_only(tmp_path: Path) -> None:
    refs = adapter(tmp_path).list_dir("weave://system/", access())

    assert refs[0].kind is WorkspaceKind.FILE
    assert refs[0].read_only is True
