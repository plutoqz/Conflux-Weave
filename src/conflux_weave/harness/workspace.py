"""Local adapter for scoped ``weave://`` workspace URIs."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import mimetypes
import os
from pathlib import Path
import tempfile
from urllib.parse import quote, unquote, urlsplit

from conflux_weave.evidence import ArtifactRef
from conflux_weave.harness.contracts import WorkspaceKind, WorkspaceRef
from conflux_weave.runtime.artifacts import LocalArtifactStore


class WorkspaceError(RuntimeError):
    """Base error for workspace resolution and access failures."""


class WorkspaceAccessDenied(WorkspaceError):
    """Raised when a caller cannot access the requested workspace scope."""


class WorkspaceConflict(WorkspaceError):
    """Raised when optimistic concurrency detects a stale revision."""


class WorkspaceNotFound(WorkspaceError):
    """Raised when a workspace resource does not exist."""


@dataclass(frozen=True, slots=True)
class WorkspaceAccess:
    agent_instance_id: str
    run_id: str | None = None
    project_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.agent_instance_id.strip():
            raise ValueError("agent_instance_id must not be empty")
        if len(set(self.project_ids)) != len(self.project_ids):
            raise ValueError("project_ids must be unique")


@dataclass(frozen=True, slots=True)
class _ParsedWorkspaceURI:
    area: str
    scope: str | None
    relative_parts: tuple[str, ...]


class LocalWorkspaceAdapter:
    def __init__(
        self,
        data_root: Path,
        system_root: Path,
        artifact_store: LocalArtifactStore,
    ) -> None:
        self.data_root = data_root.resolve()
        self.system_root = system_root.resolve()
        self.artifact_store = artifact_store

    def resolve(
        self,
        uri: str,
        access: WorkspaceAccess,
        *,
        for_write: bool = False,
    ) -> Path:
        parsed = _parse_uri(uri)
        base = self._authorized_base(parsed, access, for_write=for_write)
        candidate = base.joinpath(*parsed.relative_parts).resolve(strict=False)
        if not candidate.is_relative_to(base.resolve(strict=False)):
            raise WorkspaceAccessDenied("workspace path escapes its authorized root")
        return candidate

    def read_bytes(self, uri: str, access: WorkspaceAccess) -> bytes:
        path = self.resolve(uri, access)
        if not path.is_file():
            raise WorkspaceNotFound(f"workspace file does not exist: {uri}")
        return path.read_bytes()

    def write_bytes_atomic(
        self,
        uri: str,
        content: bytes,
        access: WorkspaceAccess,
        *,
        media_type: str,
        expected_revision: str | None = None,
    ) -> WorkspaceRef:
        if not media_type.strip():
            raise ValueError("media_type must not be empty")
        path = self.resolve(uri, access, for_write=True)
        if path.exists() and not path.is_file():
            raise WorkspaceConflict("workspace target is not a file")
        actual_revision = _revision(path.read_bytes()) if path.exists() else None
        if expected_revision is not None and expected_revision != actual_revision:
            raise WorkspaceConflict(
                f"workspace revision conflict: expected {expected_revision}, got {actual_revision}"
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
        return WorkspaceRef(
            uri=uri,
            kind=WorkspaceKind.FILE,
            revision=_revision(content),
            media_type=media_type,
            read_only=False,
        )

    def list_dir(self, uri: str, access: WorkspaceAccess) -> tuple[WorkspaceRef, ...]:
        path = self.resolve(uri, access)
        if not path.is_dir():
            raise WorkspaceNotFound(f"workspace directory does not exist: {uri}")
        base_uri = uri.rstrip("/")
        refs: list[WorkspaceRef] = []
        for child in sorted(path.iterdir(), key=lambda item: item.name.casefold()):
            child_uri = f"{base_uri}/{quote(child.name, safe='')}"
            if child.is_dir():
                refs.append(
                    WorkspaceRef(
                        uri=child_uri,
                        kind=WorkspaceKind.DIRECTORY,
                        revision=None,
                        media_type=None,
                        read_only=_parse_uri(uri).area == "system",
                    )
                )
            elif child.is_file():
                media_type, _ = mimetypes.guess_type(child.name)
                refs.append(
                    WorkspaceRef(
                        uri=child_uri,
                        kind=WorkspaceKind.FILE,
                        revision=_revision(child.read_bytes()),
                        media_type=media_type or "application/octet-stream",
                        read_only=_parse_uri(uri).area == "system",
                    )
                )
        return tuple(refs)

    def publish_artifact(
        self,
        ref: WorkspaceRef,
        access: WorkspaceAccess,
        *,
        producer_step_id: str,
        schema_version: str,
    ) -> ArtifactRef:
        if ref.kind is not WorkspaceKind.FILE:
            raise ValueError("only workspace files can be published")
        content = self.read_bytes(ref.uri, access)
        actual_revision = _revision(content)
        if ref.revision is not None and ref.revision != actual_revision:
            raise WorkspaceConflict("workspace file changed before publication")
        return self.artifact_store.put_bytes(
            content,
            media_type=ref.media_type or "application/octet-stream",
            producer_step_id=producer_step_id,
            schema_version=schema_version,
        )

    def _authorized_base(
        self,
        parsed: _ParsedWorkspaceURI,
        access: WorkspaceAccess,
        *,
        for_write: bool,
    ) -> Path:
        if parsed.area == "system":
            if for_write:
                raise WorkspaceAccessDenied("system workspace is read-only")
            return self.system_root
        if parsed.area == "projects":
            if parsed.scope not in access.project_ids:
                raise WorkspaceAccessDenied("project workspace is outside caller scope")
            return (self.data_root / "projects" / parsed.scope).resolve(strict=False)
        if parsed.area == "runs":
            if parsed.scope != access.run_id:
                raise WorkspaceAccessDenied("run workspace is outside caller scope")
            return (self.data_root / "runs" / parsed.scope).resolve(strict=False)
        if parsed.area == "scratch":
            if parsed.scope != access.agent_instance_id:
                raise WorkspaceAccessDenied("scratch workspace is private to its Agent")
            return (self.data_root / "scratch" / parsed.scope).resolve(strict=False)
        raise WorkspaceAccessDenied(f"unsupported workspace area: {parsed.area}")


def _parse_uri(uri: str) -> _ParsedWorkspaceURI:
    parsed = urlsplit(uri)
    if parsed.scheme != "weave" or not parsed.netloc or parsed.query or parsed.fragment:
        raise ValueError("workspace URI must use weave:// without query or fragment")
    area = parsed.netloc
    if area not in {"system", "projects", "runs", "scratch"}:
        raise ValueError(f"unsupported workspace area: {area}")
    decoded_path = unquote(parsed.path)
    if "\\" in decoded_path or "\x00" in decoded_path:
        raise ValueError("workspace URI contains an invalid path character")
    parts = tuple(part for part in decoded_path.split("/") if part)
    if any(part in {".", ".."} for part in parts):
        raise ValueError("workspace URI cannot contain dot segments")
    if area == "system":
        return _ParsedWorkspaceURI(area, None, parts)
    if not parts:
        raise ValueError(f"{area} workspace URI requires a scope")
    scope, *relative = parts
    return _ParsedWorkspaceURI(area, scope, tuple(relative))


def _revision(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


__all__ = [
    "LocalWorkspaceAdapter",
    "WorkspaceAccess",
    "WorkspaceAccessDenied",
    "WorkspaceConflict",
    "WorkspaceError",
    "WorkspaceNotFound",
]
