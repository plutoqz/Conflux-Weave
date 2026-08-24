import hashlib

import pytest

import conflux_weave.runtime.artifacts as artifact_module
from conflux_weave.runtime import ArtifactIntegrityError, LocalArtifactStore


def test_artifact_store_is_content_addressed_and_idempotent(tmp_path) -> None:
    store = LocalArtifactStore(tmp_path)
    content = b"immutable evidence\n"

    first = store.put_bytes(
        content,
        media_type="text/plain",
        producer_step_id="step-1",
        schema_version="text.v1",
    )
    second = store.put_bytes(
        content,
        media_type="text/plain",
        producer_step_id="step-2",
        schema_version="text.v1",
    )

    digest = hashlib.sha256(content).hexdigest()
    assert first.artifact_id == second.artifact_id == f"artifact-sha256-{digest}"
    assert first.content_hash == f"sha256:{digest}"
    assert store.read_bytes(first) == content
    assert len(list(tmp_path.rglob(digest))) == 1
    assert list(tmp_path.rglob("*.tmp")) == []


def test_artifact_store_fails_closed_on_corrupt_existing_content(tmp_path) -> None:
    store = LocalArtifactStore(tmp_path)
    expected = b"expected"
    digest = hashlib.sha256(expected).hexdigest()
    path = store.path_for_digest(digest)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"corrupt")

    with pytest.raises(ArtifactIntegrityError, match="artifact hash mismatch"):
        store.put_bytes(
            expected,
            media_type="application/octet-stream",
            producer_step_id="step-1",
            schema_version="binary.v1",
        )


def test_artifact_store_removes_temporary_file_when_atomic_publish_fails(
    tmp_path, monkeypatch
) -> None:
    store = LocalArtifactStore(tmp_path)
    content = b"fully written before publish"
    digest = hashlib.sha256(content).hexdigest()

    def fail_replace(source, target) -> None:
        raise OSError("injected atomic publish failure")

    monkeypatch.setattr(artifact_module.os, "replace", fail_replace)

    with pytest.raises(OSError, match="injected atomic publish failure"):
        store.put_bytes(
            content,
            media_type="application/octet-stream",
            producer_step_id="step-atomic",
            schema_version="binary.v1",
        )

    assert not store.path_for_digest(digest).exists()
    assert list(tmp_path.rglob("*.tmp")) == []
