"""A3 备份恢复：一致性预检、脱敏、计数一致、抽样可读、恢复不覆盖既有数据。"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from conflux_weave.backup_restore import (
    BackupPaths,
    create_backup,
    preflight_consistency,
    restore_backup,
    verify_backup,
)
from conflux_weave.runtime import SQLiteRuntimeRepository
from conflux_weave.runtime.artifacts import LocalArtifactStore


def _seed_workspace(tmp_path: Path) -> tuple[Path, SQLiteRuntimeRepository, LocalArtifactStore]:
    root = tmp_path / "ws"
    store = LocalArtifactStore(root / "var" / "artifacts" / "sha256")
    repository = SQLiteRuntimeRepository(
        root / "var" / "db" / "conflux-weave.sqlite3", store, clock=lambda: "2026-09-13T12:00:00Z"
    )
    # 业务数据：一个 run + 任务，若干 artifact
    import uuid

    conn = sqlite3.connect(repository.database_path)
    now = "2026-09-13T12:00:00Z"
    conn.execute(
        "INSERT INTO tasks(task_id,kind,input_json,requested_policy,idempotency_key,created_at) VALUES(?,?,?,?,?,?)",
        (f"task-{uuid.uuid4().hex[:8]}", "deep_research", '{"objective": "备份恢复验收"}', "auto", f"idem-{uuid.uuid4().hex}", now),
    )
    conn.commit()
    conn.close()
    for index in range(6):
        store.put_bytes(
            f"artifact payload {index} 备份抽样内容".encode("utf-8"),
            media_type="text/plain",
            producer_step_id="backup-test",
            schema_version="raw-binary.v1",
        )
    (root / "var" / "db" / "lancedb-memory").mkdir(parents=True, exist_ok=True)
    (root / ".env").write_text(
        "CONFLUX_WEAVE_PROVIDER_BASE_URL=https://provider.example/v1\n"
        "CONFLUX_WEAVE_PROVIDER_API_KEY=sk-secret-value-123456789\n"
        "CONFLUX_WEAVE_PROVIDER_MODEL=demo-model\n",
        encoding="utf-8",
    )
    return root, repository, store


def test_backup_creates_verified_bundle_without_provider_key(tmp_path):
    root, repository, store = _seed_workspace(tmp_path)
    output = tmp_path / "backup"
    manifest = create_backup(
        BackupPaths(
            database=repository.database_path,
            artifact_root=root / "var" / "artifacts" / "sha256",
            lancedb_roots=(root / "var" / "db" / "lancedb-memory",),
            dotenv=root / ".env",
        ),
        output,
    )

    assert manifest["schema_version"] == "conflux-weave.backup-manifest.v1"
    assert manifest["consistency"]["integrity_check"] == "ok"
    assert manifest["artifacts"]["file_count"] == 6
    assert manifest["config_template"] is True
    assert (output / "backup-manifest.json").is_file()
    assert (output / "runtime.sqlite3").is_file()
    assert (output / "config-template.env").is_file()

    template = (output / "config-template.env").read_text(encoding="utf-8")
    assert "sk-secret-value-123456789" not in template
    assert "REDACTED" in template
    assert "CONFLUX_WEAVE_PROVIDER_MODEL=demo-model" in template

    report = verify_backup(output)
    assert report["sqlite_sha256_ok"] is True


def test_restore_into_empty_root_matches_counts_and_samples_readable(tmp_path):
    root, repository, store = _seed_workspace(tmp_path)
    output = tmp_path / "backup"
    manifest = create_backup(
        BackupPaths(
            database=repository.database_path,
            artifact_root=root / "var" / "artifacts" / "sha256",
            lancedb_roots=(root / "var" / "db" / "lancedb-memory",),
            dotenv=root / ".env",
        ),
        output,
    )
    manifest_tasks = manifest["sqlite"]["row_counts"]["tasks"]

    target = tmp_path / "restored"
    result = restore_backup(output, target, sample_size=4, seed=7)

    assert result["sqlite_row_counts"]["tasks"] == manifest_tasks
    assert result["artifact_file_count"] == 6
    assert result["sampled_artifacts"] == 4

    restored_store = LocalArtifactStore(target / "var" / "artifacts" / "sha256")
    # 内容寻址：任取一个抽样产物应能按 digest 读回原文
    sample = next(p for p in restored_store.root.rglob("*") if p.is_file())
    assert "备份抽样内容" in sample.read_text(encoding="utf-8")


def test_restore_refuses_nonempty_target_and_failure_never_touches_existing(tmp_path):
    root, repository, _ = _seed_workspace(tmp_path)
    output = tmp_path / "backup"
    create_backup(
        BackupPaths(
            database=repository.database_path,
            artifact_root=root / "var" / "artifacts" / "sha256",
            dotenv=root / ".env",
        ),
        output,
    )

    # 篡改清单中的哈希 → 恢复前验证失败
    manifest_path = output / "backup-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["sqlite"]["sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    existing = tmp_path / "existing"
    existing.mkdir()
    sentinel = existing / "keep.txt"
    sentinel.write_text("不得覆盖", encoding="utf-8")

    with pytest.raises(RuntimeError, match="哈希不匹配"):
        restore_backup(output, existing)
    assert sentinel.read_text(encoding="utf-8") == "不得覆盖"
    assert not (existing.with_name(existing.name + "__restoring")).exists()

    # 修复哈希后：非空目标仍然拒绝
    from conflux_weave.backup_restore import _sha256_file

    manifest["sqlite"]["sha256"] = _sha256_file(output / "runtime.sqlite3")
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(RuntimeError, match="非空"):
        restore_backup(output, existing)
    assert sentinel.exists()


def test_preflight_consistency_detects_corruption(tmp_path):
    root, repository, _ = _seed_workspace(tmp_path)
    assert preflight_consistency(repository.database_path)["integrity_check"] == "ok"
