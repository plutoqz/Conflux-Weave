"""Content-addressed local artifact storage for the W1 single-process runtime."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from conflux_weave.evidence import ArtifactRef


class ArtifactIntegrityError(RuntimeError):
    """Raised when stored bytes do not match their content address."""


class LocalArtifactStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def put_json(
        self,
        value: dict[str, Any],
        *,
        producer_step_id: str,
        schema_version: str,
    ) -> ArtifactRef:
        payload = (
            json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
        return self.put_bytes(
            payload,
            media_type="application/json",
            producer_step_id=producer_step_id,
            schema_version=schema_version,
        )

    def put_bytes(
        self,
        content: bytes,
        *,
        media_type: str,
        producer_step_id: str,
        schema_version: str,
    ) -> ArtifactRef:
        digest = hashlib.sha256(content).hexdigest()
        path = self.path_for_digest(digest)
        path.parent.mkdir(parents=True, exist_ok=True)

        if path.exists():
            self._verify(path, digest)
        else:
            try:
                with path.open("xb") as handle:
                    try:
                        handle.write(content)
                        handle.flush()
                        os.fsync(handle.fileno())
                    except BaseException:
                        handle.close()
                        path.unlink(missing_ok=True)
                        raise
            except FileExistsError:
                self._verify(path, digest)

        return ArtifactRef(
            artifact_id=f"artifact-sha256-{digest}",
            media_type=media_type,
            content_hash=f"sha256:{digest}",
            storage_uri=path.resolve().as_uri(),
            producer_step_id=producer_step_id,
            schema_version=schema_version,
        )

    def read_bytes(self, artifact: ArtifactRef) -> bytes:
        digest = self._parse_content_hash(artifact.content_hash)
        path = self.path_for_digest(digest)
        self._verify(path, digest)
        return path.read_bytes()

    def path_for_digest(self, digest: str) -> Path:
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise ValueError("digest must be a lowercase SHA-256 hex string")
        return self.root / digest[:2] / digest

    @staticmethod
    def _parse_content_hash(content_hash: str) -> str:
        algorithm, separator, digest = content_hash.partition(":")
        if separator != ":" or algorithm != "sha256":
            raise ValueError("ArtifactRef.content_hash must use sha256:<hex>")
        return digest

    @staticmethod
    def _verify(path: Path, expected_digest: str) -> None:
        if not path.is_file():
            raise ArtifactIntegrityError(f"artifact is not a file: {path}")
        actual_digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual_digest != expected_digest:
            raise ArtifactIntegrityError(
                f"artifact hash mismatch: expected {expected_digest}, got {actual_digest}"
            )
