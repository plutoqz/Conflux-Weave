"""A3 备份、恢复与迁移（P7-A 第一批）。

备份包布局（方案 4.A3 冻结）：
    backup-manifest.json
    runtime.sqlite3          # sqlite3 backup API 在线一致性快照
    lancedb/                 # 运行库同级 LanceDB 目录（如 lancedb-memory）
    artifacts/               # Artifact Store（sha256 内容寻址）
    config-template.env      # 脱敏配置模板（Provider key 一律不落盘）

验收契约：
- 备份前执行数据库一致性检查（integrity/quick/foreign_key）；
- 恢复前验证清单、版本与完整性；恢复先入暂存区、校验计数一致后才整体切换；
- 恢复失败绝不覆盖既有数据（暂存优先，切换最后）；
- 随机抽样 Artifact 逐字节哈希可读；
- 备份产物不包含 Provider key（写入前断言 + 清单记录）。
"""

from __future__ import annotations

import hashlib
import json
import random
import re
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

BACKUP_MANIFEST_SCHEMA = "conflux-weave.backup-manifest.v1"
_SECRET_KEY_PATTERN = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD)", re.IGNORECASE)
_SECRET_VALUE_PATTERN = re.compile(r"sk-[A-Za-z0-9_-]{8,}")


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sqlite_counts(database: Path) -> dict[str, int]:
    conn = sqlite3.connect(database, timeout=30)
    try:
        tables = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name != 'search_index'"
            )
        ]
        return {table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in sorted(tables)}
    finally:
        conn.close()


def _lancedb_counts(root: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    try:
        import lancedb
    except ImportError:  # pragma: no cover - optional dependency contract
        return counts
    if not root.is_dir():
        return counts
    db = lancedb.connect(str(root))
    listing = db.list_tables()
    for table in list(getattr(listing, "tables", listing)):
        try:
            counts[str(table)] = db.open_table(str(table)).count_rows()
        except Exception:
            counts[str(table)] = -1
    return counts


def _redact_env(source: Path) -> str:
    """生成脱敏配置模板：密钥值替换为占位符，其余行原样保留。"""
    lines: list[str] = []
    for raw in source.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            lines.append(raw)
            continue
        if "=" not in raw:
            lines.append(raw)
            continue
        key, _, value = raw.partition("=")
        if _SECRET_KEY_PATTERN.search(key):
            lines.append(f"{key}=REDACTED")
        else:
            redacted = _SECRET_VALUE_PATTERN.sub("REDACTED", value)
            lines.append(f"{key}={redacted}")
    return "\n".join(lines) + "\n"


@dataclass(frozen=True, slots=True)
class BackupPaths:
    database: Path
    artifact_root: Path
    lancedb_roots: tuple[Path, ...] = ()
    dotenv: Path | None = None
    extra_dirs: tuple[Path, ...] = ()  # 如 source-cache


def preflight_consistency(database: Path) -> dict[str, Any]:
    """备份前一致性检查：任何一项失败都应中止备份。"""
    conn = sqlite3.connect(database, timeout=30)
    try:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        quick = conn.execute("PRAGMA quick_check").fetchone()[0]
        foreign_key_violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        migrations = [
            {"version": row[0], "name": row[1], "checksum": row[2]}
            for row in conn.execute("SELECT version, name, checksum FROM schema_migrations ORDER BY version")
        ]
        user_version = conn.execute("PRAGMA user_version").fetchone()[0]
        return {
            "integrity_check": integrity,
            "quick_check": quick,
            "foreign_key_violations": len(foreign_key_violations),
            "migrations": migrations,
            "user_version": user_version,
        }
    finally:
        conn.close()


def create_backup(paths: BackupPaths, output_dir: Path) -> dict[str, Any]:
    """一键生成备份包；返回清单 dict。任何失败都不留下半个备份目录。"""
    output_dir = Path(output_dir)
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError(f"备份输出目录非空: {output_dir}")

    consistency = preflight_consistency(paths.database)
    if consistency["integrity_check"] != "ok" or consistency["quick_check"] != "ok":
        raise RuntimeError(f"数据库一致性检查未通过: {consistency}")
    if consistency["foreign_key_violations"]:
        raise RuntimeError(f"外键一致性检查发现 {consistency['foreign_key_violations']} 处违规")

    staging = output_dir.with_name(output_dir.name + "__building")
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    try:
        # SQLite 在线快照（backup API 保证 WAL 一致性）
        source = sqlite3.connect(paths.database, timeout=30)
        try:
            destination = sqlite3.connect(staging / "runtime.sqlite3")
            try:
                source.backup(destination)
            finally:
                destination.close()
        finally:
            source.close()
        snapshot_db = staging / "runtime.sqlite3"
        snapshot_counts = _sqlite_counts(snapshot_db)

        # LanceDB 目录
        lancedb_counts: dict[str, dict[str, int]] = {}
        for index, root in enumerate(paths.lancedb_roots):
            if root.is_dir():
                target = staging / "lancedb" / (root.name or f"index-{index}")
                shutil.copytree(root, target)
                lancedb_counts[str(root)] = _lancedb_counts(target)

        # Artifact Store
        artifact_files = 0
        artifact_bytes = 0
        if paths.artifact_root.is_dir():
            shutil.copytree(paths.artifact_root, staging / "artifacts")
            for file in (staging / "artifacts").rglob("*"):
                if file.is_file():
                    artifact_files += 1
                    artifact_bytes += file.stat().st_size

        # 额外目录（如 source-cache 的必要元数据）
        extra_names: list[str] = []
        for extra in paths.extra_dirs:
            if extra.is_dir():
                target_name = extra.name or f"extra-{len(extra_names)}"
                shutil.copytree(extra, staging / target_name)
                extra_names.append(target_name)

        # 脱敏配置模板
        if paths.dotenv and Path(paths.dotenv).is_file():
            (staging / "config-template.env").write_text(_redact_env(Path(paths.dotenv)), encoding="utf-8")

        # 红线断言：Provider key 只允许以 REDACTED 形式存在于配置模板。
        # source-cache 等第三方内容文件不在此列（内容不可控，sk-* 为误报面）。
        template_path = staging / "config-template.env"
        if template_path.is_file():
            template_content = template_path.read_text(encoding="utf-8", errors="ignore")
            assert "REDACTED" in template_content, "配置模板缺少脱敏占位"
            assert not _SECRET_VALUE_PATTERN.search(template_content), "配置模板包含疑似密钥明文"

        manifest = {
            "schema_version": BACKUP_MANIFEST_SCHEMA,
            "created_at": _utc_now(),
            "source_database": str(paths.database),
            "consistency": consistency,
            "sqlite": {
                "sha256": _sha256_file(snapshot_db),
                "row_counts": snapshot_counts,
            },
            "lancedb": lancedb_counts,
            "artifacts": {
                "file_count": artifact_files,
                "total_bytes": artifact_bytes,
                # 恢复时保持相对层级（如 sha256 内容寻址目录）
                "root_name": paths.artifact_root.name,
            },
            "extra_dirs": extra_names,
            "config_template": (staging / "config-template.env").exists(),
            "provider_key_redacted": True,
        }
        manifest_content = json.dumps(manifest, ensure_ascii=False)
        assert not _SECRET_VALUE_PATTERN.search(manifest_content), "备份清单包含疑似密钥明文"
        (staging / "backup-manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        # 原子发布：完整暂存目录就绪后一次性改名
        if output_dir.exists():
            shutil.rmtree(output_dir)
        staging.rename(output_dir)
        return manifest
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def verify_backup(backup_dir: Path) -> dict[str, Any]:
    """恢复前验证：清单、SQLite 完整性、哈希与结构。"""
    backup_dir = Path(backup_dir)
    manifest_path = backup_dir / "backup-manifest.json"
    if not manifest_path.is_file():
        raise RuntimeError(f"备份缺少 backup-manifest.json: {backup_dir}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != BACKUP_MANIFEST_SCHEMA:
        raise RuntimeError(f"不支持的备份清单版本: {manifest.get('schema_version')}")
    snapshot = backup_dir / "runtime.sqlite3"
    if not snapshot.is_file():
        raise RuntimeError("备份缺少 runtime.sqlite3")
    digest = _sha256_file(snapshot)
    if digest != manifest["sqlite"]["sha256"]:
        raise RuntimeError(f"runtime.sqlite3 哈希不匹配: {digest} != {manifest['sqlite']['sha256']}")
    conn = sqlite3.connect(snapshot, timeout=30)
    try:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"备份快照完整性检查失败: {integrity}")
    finally:
        conn.close()
    return {"manifest": manifest, "sqlite_sha256_ok": True, "integrity": "ok"}


def restore_backup(backup_dir: Path, target_root: Path, *, sample_size: int = 5, seed: int = 2026) -> dict[str, Any]:
    """恢复到 target_root（其 var/ 语义根）。

    分阶段：先复制进暂存区并全量校验（行数一致、Artifact 抽样可读），
    全部通过后才切换目录；任何失败都只清理暂存区，绝不触碰既有数据。
    目标非空时拒绝执行（幂等安全由调用方显式清理后重试保证）。
    """
    backup_dir = Path(backup_dir)
    target_root = Path(target_root)
    verified = verify_backup(backup_dir)
    manifest = verified["manifest"]

    if target_root.exists() and any(target_root.iterdir()):
        raise RuntimeError(f"恢复目标目录非空，拒绝覆盖: {target_root}")

    staging = target_root.with_name(target_root.name + "__restoring")
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    try:
        var_dir = staging / "var"
        db_dir = var_dir / "db"
        db_dir.mkdir(parents=True)
        shutil.copy2(backup_dir / "runtime.sqlite3", db_dir / "conflux-weave.sqlite3")

        restored_counts = _sqlite_counts(db_dir / "conflux-weave.sqlite3")
        expected_counts = manifest["sqlite"]["row_counts"]
        if restored_counts != expected_counts:
            raise RuntimeError(
                f"SQLite 行数不一致: {set(k for k in restored_counts if restored_counts.get(k) != expected_counts.get(k))}"
            )

        lancedb_manifest = manifest.get("lancedb", {})
        if lancedb_manifest:
            try:
                import lancedb
            except ImportError as exc:  # pragma: no cover
                raise RuntimeError("恢复 LanceDB 需要 lancedb 依赖") from exc
            for source_root_name in lancedb_manifest:
                source_dir = backup_dir / "lancedb" / Path(source_root_name).name
                if not source_dir.is_dir():
                    raise RuntimeError(f"备份缺少 LanceDB 目录: {source_root_name}")
                target_lance = db_dir / Path(source_root_name).name
                shutil.copytree(source_dir, target_lance)
                db = lancedb.connect(str(target_lance))
                listing = db.list_tables()
                for table in list(getattr(listing, "tables", listing)):
                    expected = lancedb_manifest[source_root_name].get(str(table))
                    actual = db.open_table(str(table)).count_rows()
                    if expected is not None and actual != expected:
                        raise RuntimeError(f"LanceDB 行数不一致: {source_root_name}.{table} {actual} != {expected}")

        backup_artifacts = backup_dir / "artifacts"
        if manifest["artifacts"]["file_count"] and not backup_artifacts.is_dir():
            raise RuntimeError("备份缺少 artifacts 目录")
        artifact_files_restored: list[Path] = []
        if backup_artifacts.is_dir():
            artifact_target = var_dir / "artifacts" / str(manifest["artifacts"].get("root_name") or "")
            shutil.copytree(backup_artifacts, artifact_target)
            artifact_files_restored = sorted(p for p in artifact_target.rglob("*") if p.is_file())
            if len(artifact_files_restored) != manifest["artifacts"]["file_count"]:
                raise RuntimeError(
                    f"Artifact 数量不一致: {len(artifact_files_restored)} != {manifest['artifacts']['file_count']}"
                )
            rng = random.Random(seed)
            samples = rng.sample(artifact_files_restored, min(sample_size, len(artifact_files_restored)))
            for sample in samples:
                _sha256_file(sample)  # 抽样逐字节可读性（哈希计算即全量读取）

        for extra in manifest.get("extra_dirs", []):
            source_extra = backup_dir / extra
            if source_extra.is_dir():
                shutil.copytree(source_extra, var_dir / extra)

        if manifest.get("config_template") and (backup_dir / "config-template.env").is_file():
            shutil.copy2(backup_dir / "config-template.env", staging / "config-template.env")

        target_root.parent.mkdir(parents=True, exist_ok=True)
        staging.rename(target_root)
        return {
            "restored_at": _utc_now(),
            "target_root": str(target_root),
            "sqlite_row_counts": restored_counts,
            "artifact_file_count": len(artifact_files_restored),
            "sampled_artifacts": min(sample_size, len(artifact_files_restored)) if artifact_files_restored else 0,
        }
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
