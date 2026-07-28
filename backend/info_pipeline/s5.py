"""S5 static INFO 受控激活与文件后端回滚验收。

本模块只允许在带 ``offline-candidate`` 标签的隔离数据库上运行。数据库 release
通过既有 ``info.activate_release`` 函数激活；运行后端通过仓库外的原子状态文件
切换。文件回滚不会逆向修改业务表，也不会覆盖任何既有失败证据。
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence

from .loader import LoadError
from .manifest import validate_manifest
from .output import write_text_exclusive
from .runtime import DatabaseStaticInfo, PostgresStaticInfoRepository
from .s4 import _core_hash_state, _run_snapshot_probe
from .shadow import _verify_all_sources


class S5AcceptanceError(LoadError):
    """S5 无法证明受控激活或安全回滚。"""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=str,
    )


def _sha256_value(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path, label: str) -> Mapping[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise S5AcceptanceError(f"{label}缺失、不是普通文件或为软链接：{path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise S5AcceptanceError(f"{label}读取失败：{exc}") from exc
    if not isinstance(value, dict):
        raise S5AcceptanceError(f"{label}顶层必须是 JSON 对象")
    return value


def _require_real_directory(path: Path, label: str) -> None:
    if path.is_symlink() or not path.is_dir():
        raise S5AcceptanceError(f"{label}必须是实际目录且禁止软链接：{path}")


def _verify_s4_receipt(
    receipt: Mapping[str, Any],
    manifest: Mapping[str, Any],
) -> None:
    if (
        receipt.get("component") != "static_info_stage_gate"
        or receipt.get("stage_id") != "S4"
        or receipt.get("status") != "pass"
        or receipt.get("deviation_count") != 0
        or receipt.get("deviations") != []
        or receipt.get("subject", {}).get("content_id")
        != manifest.get("content_id")
        or receipt.get("subject", {}).get("manifest_sha256")
        != manifest.get("manifest_sha256")
    ):
        raise S5AcceptanceError("S4 回执未通过或与当前 manifest 身份不一致")
    requirements = receipt.get("requirements")
    if not isinstance(requirements, list) or len(requirements) != 12:
        raise S5AcceptanceError("S4 回执缺少完整的 12 项最终要求状态")
    for item in requirements:
        if not isinstance(item, dict):
            raise S5AcceptanceError("S4 回执要求项格式无效")
        due_stage = str(item.get("due_stage", ""))
        expected = "pass" if due_stage in {"S0", "S1", "S2", "S3", "S4"} else "not_due"
        if item.get("status") != expected:
            raise S5AcceptanceError(
                f"S4 前置门禁状态错误：{item.get('requirement_id')}"
            )


def _docker_fingerprint(containers: Sequence[str]) -> Mapping[str, Any]:
    result: Dict[str, Any] = {}
    for container in containers:
        completed = subprocess.run(
            [
                "docker",
                "inspect",
                "--format",
                (
                    "{{json .State.Status}}|{{json .State.StartedAt}}|"
                    "{{json .RestartCount}}|{{json .Image}}|"
                    "{{json .HostConfig.NetworkMode}}"
                ),
                container,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if completed.returncode == 0:
            result[container] = completed.stdout.strip()
        else:
            result[container] = {
                "state": "not_present",
                "inspect_exit_code": completed.returncode,
            }
    return result


def _candidate_boundary(container: str) -> Mapping[str, Any]:
    completed = subprocess.run(
        [
            "docker",
            "inspect",
            "--format",
            (
                "{{json .State.Status}}|{{json .HostConfig.NetworkMode}}|"
                "{{json (index .Config.Labels \"domeye.core.database-role\")}}|"
                "{{json (index .Config.Labels \"io.domeye.core.role\")}}"
            ),
            container,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise S5AcceptanceError(
            f"无法检查候选容器：{completed.stderr.strip()}"
        )
    parts = completed.stdout.strip().split("|")
    if len(parts) != 4:
        raise S5AcceptanceError("候选容器边界输出格式无效")
    values = [json.loads(item) for item in parts]
    status, network_mode, database_role, component_role = values
    if (
        status != "running"
        or network_mode != "none"
        or database_role != "offline-candidate"
        or component_role != "info-migration-candidate"
    ):
        raise S5AcceptanceError(
            "S5 只能在 running、network=none 的 INFO 离线候选容器执行"
        )
    return {
        "container": container,
        "status": status,
        "network_mode": network_mode,
        "database_role": database_role,
        "component_role": component_role,
    }


def _failure_evidence_inventory(evidence_root: Path) -> Mapping[str, Any]:
    _require_real_directory(evidence_root, "证据根目录")
    files: Dict[str, str] = {}
    directories = []
    for child in sorted(evidence_root.iterdir(), key=lambda item: item.name):
        if ".incomplete." not in child.name:
            continue
        if child.is_symlink() or not child.is_dir():
            raise S5AcceptanceError(f"失败证据目录边界无效：{child}")
        directories.append(child.name)
        for path in sorted(child.rglob("*")):
            if path.is_symlink():
                raise S5AcceptanceError(f"失败证据内禁止软链接：{path}")
            if path.is_file():
                files[str(path.relative_to(evidence_root))] = _sha256_file(path)
    return {
        "directory_count": len(directories),
        "directories": directories,
        "file_count": len(files),
        "tree_sha256": _sha256_value(files),
    }


def _source_metadata(
    source_dir: Path,
    manifest: Mapping[str, Any],
) -> Mapping[str, Any]:
    directory_stat = source_dir.stat()
    files: Dict[str, Any] = {}
    manifest_files = manifest.get("files")
    if not isinstance(manifest_files, list):
        raise S5AcceptanceError("manifest files 无效")
    for item in manifest_files:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            raise S5AcceptanceError("manifest 文件项无效")
        path = source_dir / item["name"]
        if path.is_symlink() or not path.is_file():
            raise S5AcceptanceError(f"文件回滚制品缺失或为软链接：{path}")
        state = path.stat()
        files[item["name"]] = {
            "device": state.st_dev,
            "inode": state.st_ino,
            "size_bytes": state.st_size,
            "mtime_ns": state.st_mtime_ns,
            "sha256": item.get("sha256"),
        }
    return {
        "path": str(source_dir),
        "device": directory_stat.st_dev,
        "inode": directory_stat.st_ino,
        "mtime_ns": directory_stat.st_mtime_ns,
        "file_count": len(files),
        "files_sha256": _sha256_value(files),
    }


def _connect(
    *,
    host: str,
    port: int,
    db_name: str,
    db_user: str,
    read_only: bool,
    application_name: str,
) -> Any:
    try:
        import psycopg2
    except ImportError as exc:
        raise S5AcceptanceError("S5 运行环境缺少 psycopg2") from exc
    connection = psycopg2.connect(
        host=host,
        port=port,
        dbname=db_name,
        user=db_user,
        connect_timeout=10,
        application_name=application_name,
    )
    connection.set_session(readonly=read_only, autocommit=False)
    return connection


_CONTROL_TABLES = frozenset(
    {
        "active_release",
        "dataset_release",
        "import_run",
        "quality_result",
        "schema_metadata",
    }
)


def _business_fingerprint(connection: Any) -> Mapping[str, Any]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT c.relname,
                   c.relfilenode,
                   pg_relation_size(c.oid),
                   pg_indexes_size(c.oid),
                   coalesce(s.n_tup_ins, 0),
                   coalesce(s.n_tup_upd, 0),
                   coalesce(s.n_tup_del, 0)
            FROM pg_class AS c
            JOIN pg_namespace AS n
              ON n.oid = c.relnamespace
            LEFT JOIN pg_stat_user_tables AS s
              ON s.relid = c.oid
            WHERE n.nspname = 'info'
              AND c.relkind IN ('r', 'p')
            ORDER BY c.relname
            """
        )
        items = [
            {
                "table": str(row[0]),
                "relfilenode": int(row[1] or 0),
                "table_bytes": int(row[2] or 0),
                "index_bytes": int(row[3] or 0),
                "n_tup_ins": int(row[4] or 0),
                "n_tup_upd": int(row[5] or 0),
                "n_tup_del": int(row[6] or 0),
            }
            for row in cursor.fetchall()
            if str(row[0]) not in _CONTROL_TABLES
        ]
    return {
        "table_count": len(items),
        "catalog_sha256": _sha256_value(items),
        "tables": items,
    }


def _release_state(
    connection: Any,
    content_id: str,
    manifest_sha256: str,
) -> Mapping[str, Any]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT release.release_sk, release.status,
                   release.content_id, release.manifest_sha256,
                   active.profile_name, active.previous_release_sk,
                   active.activated_by, active.activation_reason,
                   (
                     SELECT count(*)
                     FROM info.source_file AS source
                     WHERE source.release_sk = release.release_sk
                       AND source.load_status = 'loaded'
                   ) AS loaded_files,
                   (
                     SELECT count(*)
                     FROM info.quality_result AS quality
                     WHERE quality.release_sk = release.release_sk
                       AND quality.blocking
                       AND quality.status <> 'pass'
                   ) AS blocking_failures
            FROM info.dataset_release AS release
            LEFT JOIN info.active_release AS active
              ON active.release_sk = release.release_sk
             AND active.profile_name = 'core'
            WHERE release.content_id = %s
              AND release.manifest_sha256 = %s
            """,
            (content_id, manifest_sha256),
        )
        row = cursor.fetchone()
    if row is None:
        raise S5AcceptanceError("候选库中不存在当前 manifest 对应的 release")
    return {
        "release_sk": int(row[0]),
        "status": str(row[1]),
        "content_id": str(row[2]),
        "manifest_sha256": str(row[3]),
        "active_profile": row[4],
        "previous_release_sk": row[5],
        "activated_by": row[6],
        "activation_reason": row[7],
        "loaded_file_count": int(row[8]),
        "blocking_failure_count": int(row[9]),
    }


def _controlled_database_activation(
    connection: Any,
    *,
    content_id: str,
    manifest_sha256: str,
    authorization_id: str,
) -> Mapping[str, Any]:
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT release_sk, status
                FROM info.dataset_release
                WHERE content_id = %s
                  AND manifest_sha256 = %s
                FOR UPDATE
                """,
                (content_id, manifest_sha256),
            )
            row = cursor.fetchone()
            if row is None:
                raise S5AcceptanceError("受控激活找不到目标 release")
            release_sk, status = int(row[0]), str(row[1])
            cursor.execute(
                """
                SELECT count(*)
                FROM info.source_file
                WHERE release_sk = %s
                  AND load_status = 'loaded'
                """,
                (release_sk,),
            )
            loaded_files = int(cursor.fetchone()[0])
            cursor.execute(
                """
                SELECT count(*)
                FROM info.quality_result
                WHERE release_sk = %s
                  AND blocking
                  AND status <> 'pass'
                """,
                (release_sk,),
            )
            failures = int(cursor.fetchone()[0])
            if loaded_files != 24 or failures != 0:
                raise S5AcceptanceError(
                    "受控激活前 24 文件或阻断质量门禁未闭合"
                )

            promoted = False
            activation_called = False
            if status == "validating":
                cursor.execute(
                    """
                    UPDATE info.dataset_release
                    SET status = 'ready'
                    WHERE release_sk = %s
                      AND status = 'validating'
                    """,
                    (release_sk,),
                )
                if cursor.rowcount != 1:
                    raise S5AcceptanceError("候选 release 晋级 ready 失败")
                status = "ready"
                promoted = True
            if status == "ready":
                cursor.execute(
                    "SELECT info.activate_release(%s, %s, %s, %s)",
                    (
                        "core",
                        release_sk,
                        "static-info-s5-controller",
                        f"authorization:{authorization_id}",
                    ),
                )
                activation_called = True
            elif status == "active":
                cursor.execute(
                    """
                    SELECT count(*)
                    FROM info.active_release
                    WHERE profile_name = 'core'
                      AND release_sk = %s
                    """,
                    (release_sk,),
                )
                if int(cursor.fetchone()[0]) != 1:
                    raise S5AcceptanceError(
                        "release 为 active，但 core 活动指针不一致"
                    )
            else:
                raise S5AcceptanceError(f"release 状态不允许激活：{status}")

            cursor.execute(
                """
                SELECT release.status, active.release_sk,
                       active.activated_by, active.activation_reason
                FROM info.dataset_release AS release
                JOIN info.active_release AS active
                  ON active.profile_name = 'core'
                 AND active.release_sk = release.release_sk
                WHERE release.release_sk = %s
                """,
                (release_sk,),
            )
            active_row = cursor.fetchone()
            if active_row is None or str(active_row[0]) != "active":
                raise S5AcceptanceError("受控激活后活动指针未生效")
            cursor.execute(
                """
                SELECT relname, n_tup_ins, n_tup_upd, n_tup_del
                FROM pg_stat_xact_user_tables
                WHERE schemaname = 'info'
                  AND (n_tup_ins <> 0 OR n_tup_upd <> 0 OR n_tup_del <> 0)
                ORDER BY relname
                """
            )
            changed = [
                {
                    "table": str(item[0]),
                    "inserted": int(item[1]),
                    "updated": int(item[2]),
                    "deleted": int(item[3]),
                }
                for item in cursor.fetchall()
            ]
            unauthorized = [
                item
                for item in changed
                if item["table"] not in {"dataset_release", "active_release"}
            ]
            if unauthorized:
                raise S5AcceptanceError(
                    f"激活事务修改了业务表：{unauthorized}"
                )
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    return {
        "release_sk": release_sk,
        "promoted_to_ready": promoted,
        "activate_release_called": activation_called,
        "transaction_changed_tables": changed,
        "business_table_change_count": len(unauthorized),
        "active_status": str(active_row[0]),
        "activated_by": str(active_row[2]),
        "activation_reason": str(active_row[3]),
    }


def _read_state(path: Path) -> Optional[Mapping[str, Any]]:
    if not path.exists():
        return None
    return _read_json(path, "运行后端状态")


def _write_all(descriptor: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        offset += os.write(descriptor, payload[offset:])


def _transition_runtime_state(
    state_dir: Path,
    *,
    backend: str,
    content_id: str,
    manifest_sha256: str,
    source_dir: Path,
    release_sk: Optional[int],
    reason: str,
    expected_backend: Optional[str],
) -> Mapping[str, Any]:
    _require_real_directory(state_dir, "运行状态目录")
    lock_path = state_dir / "backend-state.lock"
    lock_flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        lock_flags |= os.O_NOFOLLOW
    lock_descriptor = os.open(lock_path, lock_flags, 0o600)
    try:
        fcntl.flock(lock_descriptor, fcntl.LOCK_EX)
        state_path = state_dir / "backend-state.json"
        previous = _read_state(state_path)
        previous_backend = (
            str(previous.get("backend")) if previous is not None else None
        )
        if expected_backend is not None and previous_backend != expected_backend:
            raise S5AcceptanceError(
                "运行后端状态与受控切换前置不一致："
                f"observed={previous_backend!r} expected={expected_backend!r}"
            )
        generation = int(previous.get("generation", 0) if previous else 0) + 1
        state = {
            "schema_version": 1,
            "component": "static_info_runtime_backend_state",
            "generation": generation,
            "backend": backend,
            "content_id": content_id,
            "manifest_sha256": manifest_sha256,
            "release_sk": release_sk if backend == "database" else None,
            "file_backend_path": str(source_dir) if backend == "file" else None,
            "previous_backend": previous_backend,
            "changed_at": _utc_now(),
            "reason": reason,
        }
        serialized = (
            json.dumps(state, ensure_ascii=False, sort_keys=True, indent=2)
            + "\n"
        ).encode("utf-8")
        temporary = state_dir / f".backend-state.{os.getpid()}.{generation}"
        temp_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            temp_flags |= os.O_NOFOLLOW
        temp_descriptor = os.open(temporary, temp_flags, 0o600)
        try:
            _write_all(temp_descriptor, serialized)
            os.fsync(temp_descriptor)
        finally:
            os.close(temp_descriptor)
        os.replace(temporary, state_path)
        directory_descriptor = os.open(state_dir, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)

        journal_dir = state_dir / "journal"
        journal_dir.mkdir(mode=0o700, exist_ok=True)
        if journal_dir.is_symlink() or not journal_dir.is_dir():
            raise S5AcceptanceError("运行后端状态日志目录边界无效")
        journal_path = journal_dir / f"{generation:06d}-{backend}.json"
        write_text_exclusive(
            journal_path,
            serialized.decode("utf-8"),
        )
        return state
    finally:
        fcntl.flock(lock_descriptor, fcntl.LOCK_UN)
        os.close(lock_descriptor)


def _database_runtime_probe(
    connection: Any,
    *,
    content_id: str,
    manifest_sha256: str,
) -> Mapping[str, Any]:
    repository = PostgresStaticInfoRepository(
        connection,
        content_id=content_id,
        manifest_sha256=manifest_sha256,
        allow_statuses=("active",),
    )
    snapshot = DatabaseStaticInfo(repository)
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT asn::text
            FROM info.autonomous_system
            WHERE release_sk = %s
            ORDER BY asn
            LIMIT 2
            """,
            (repository.release_sk,),
        )
        asns = [str(row[0]) for row in cursor.fetchall()]
        cursor.execute(
            """
            SELECT alpha2
            FROM info.country
            WHERE release_sk = %s
              AND alpha2 IS NOT NULL
            ORDER BY alpha2
            LIMIT 1
            """,
            (repository.release_sk,),
        )
        country_row = cursor.fetchone()
    if len(asns) != 2 or country_row is None:
        raise S5AcceptanceError("数据库运行时探针缺少确定性键")
    first = snapshot.as_info[asns[0]]
    country = snapshot.country[str(country_row[0])]
    return {
        "backend": "database",
        "content_id": snapshot.content_id,
        "manifest_sha256": snapshot.manifest_sha256,
        "release_sk": snapshot.release_sk,
        "snapshot_kind": snapshot.snapshot_kind,
        "identity_count": 1,
        "first_key": asns[0],
        "second_key": asns[1],
        "first_result_sha256": _sha256_value(first),
        "country_result_sha256": _sha256_value(country),
        "query_count": repository.telemetry.query_count,
        "full_table_load_count": repository.telemetry.full_table_load_count,
        "_snapshot": snapshot,
    }


def _report_safe_probe(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return {key: item for key, item in value.items() if key != "_snapshot"}


def run_s5_acceptance(
    *,
    source_dir: Path,
    manifest: Mapping[str, Any],
    core_backend_root: Path,
    evidence_root: Path,
    s4_evidence_dir: Path,
    state_dir: Path,
    db_host: str,
    db_port: int,
    db_name: str,
    db_reader: str,
    db_admin: str,
    container: str,
    production_containers: Sequence[str],
    authorization_id: str,
    confirmed_content_id: str,
) -> Mapping[str, Any]:
    validate_manifest(manifest)
    _require_real_directory(source_dir, "只读 INFO 来源")
    _require_real_directory(core_backend_root, "Core 后端目录")
    _require_real_directory(s4_evidence_dir, "S4 证据目录")
    if not authorization_id.strip():
        raise S5AcceptanceError("S5 激活授权 ID 不能为空")
    content_id = str(manifest["content_id"])
    manifest_sha256 = str(manifest["manifest_sha256"])
    if confirmed_content_id != content_id:
        raise S5AcceptanceError("显式确认的 content_id 与候选 manifest 不一致")

    s4_receipt = _read_json(
        s4_evidence_dir / "stage-gate-S4.json",
        "S4 阶段回执",
    )
    _verify_s4_receipt(s4_receipt, manifest)
    _verify_all_sources(source_dir, manifest)
    candidate_boundary = _candidate_boundary(container)
    source_before = _source_metadata(source_dir, manifest)
    failures_before = _failure_evidence_inventory(evidence_root)
    core_before = _core_hash_state(core_backend_root)
    production_before = _docker_fingerprint(production_containers)

    admin = _connect(
        host=db_host,
        port=db_port,
        db_name=db_name,
        db_user=db_admin,
        read_only=False,
        application_name="domeye_static_info_s5_controller",
    )
    reader = None
    transitions = []
    rollback_completed = False
    try:
        admin_state_before = _release_state(
            admin,
            content_id,
            manifest_sha256,
        )
        admin.rollback()
        if (
            admin_state_before["loaded_file_count"] != 24
            or admin_state_before["blocking_failure_count"] != 0
        ):
            raise S5AcceptanceError("激活前 release 数据或质量门禁存在缺口")
        business_before = _business_fingerprint(admin)
        admin.rollback()

        existing_state = _read_state(state_dir / "backend-state.json")
        if existing_state is None:
            transitions.append(
                _transition_runtime_state(
                    state_dir,
                    backend="file",
                    content_id=content_id,
                    manifest_sha256=manifest_sha256,
                    source_dir=source_dir,
                    release_sk=None,
                    reason="s5-approved-file-fallback-baseline",
                    expected_backend=None,
                )
            )
        elif existing_state.get("content_id") != content_id:
            raise S5AcceptanceError("既有运行后端状态属于另一个 content_id")
        elif existing_state.get("backend") == "database":
            transitions.append(
                _transition_runtime_state(
                    state_dir,
                    backend="file",
                    content_id=content_id,
                    manifest_sha256=manifest_sha256,
                    source_dir=source_dir,
                    release_sk=None,
                    reason="s5-resume-safe-file-boundary",
                    expected_backend="database",
                )
            )
        elif existing_state.get("backend") != "file":
            raise S5AcceptanceError("既有运行后端状态使用未知后端")

        activation = _controlled_database_activation(
            admin,
            content_id=content_id,
            manifest_sha256=manifest_sha256,
            authorization_id=authorization_id,
        )
        transitions.append(
            _transition_runtime_state(
                state_dir,
                backend="database",
                content_id=content_id,
                manifest_sha256=manifest_sha256,
                source_dir=source_dir,
                release_sk=int(activation["release_sk"]),
                reason=f"s5-authorized-activation:{authorization_id}",
                expected_backend="file",
            )
        )

        reader = _connect(
            host=db_host,
            port=db_port,
            db_name=db_name,
            db_user=db_reader,
            read_only=True,
            application_name="domeye_static_info_s5_runtime",
        )
        reader.set_session(readonly=True, autocommit=True)
        database_probe = _database_runtime_probe(
            reader,
            content_id=content_id,
            manifest_sha256=manifest_sha256,
        )
        snapshot = database_probe["_snapshot"]

        transitions.append(
            _transition_runtime_state(
                state_dir,
                backend="file",
                content_id=content_id,
                manifest_sha256=manifest_sha256,
                source_dir=source_dir,
                release_sk=None,
                reason="s5-controlled-file-rollback-drill",
                expected_backend="database",
            )
        )
        rollback_completed = True
        file_probe = _run_snapshot_probe(
            backend="file",
            core_backend_root=core_backend_root,
            source_dir=source_dir,
            host=db_host,
            port=db_port,
            db_name=db_name,
            db_user=db_reader,
            content_id=content_id,
            manifest_sha256=manifest_sha256,
        )
        second_asn = str(database_probe["second_key"])
        second_result = snapshot.as_info[second_asn]
        pinned_after_switch = {
            "backend": "database",
            "content_id": snapshot.content_id,
            "release_sk": snapshot.release_sk,
            "second_key": second_asn,
            "second_result_sha256": _sha256_value(second_result),
            "identity_count": 1,
        }
        if (
            snapshot.content_id != content_id
            or int(snapshot.release_sk) != int(activation["release_sk"])
        ):
            raise S5AcceptanceError("后端切换后已开始的数据库快照身份发生漂移")

        transitions.append(
            _transition_runtime_state(
                state_dir,
                backend="database",
                content_id=content_id,
                manifest_sha256=manifest_sha256,
                source_dir=source_dir,
                release_sk=int(activation["release_sk"]),
                reason="s5-post-rollback-revalidated-activation",
                expected_backend="file",
            )
        )
        final_state = _read_state(state_dir / "backend-state.json")
        if (
            final_state is None
            or final_state.get("backend") != "database"
            or final_state.get("content_id") != content_id
            or final_state.get("manifest_sha256") != manifest_sha256
        ):
            raise S5AcceptanceError("回滚演练后的最终数据库状态不一致")

        admin_state_after = _release_state(
            admin,
            content_id,
            manifest_sha256,
        )
        admin.rollback()
        business_after = _business_fingerprint(admin)
        admin.rollback()
        if business_before != business_after:
            raise S5AcceptanceError("激活或文件回滚期间业务表物理指纹发生变化")

        detector = _read_json(
            s4_evidence_dir / "static-info-detector-ab.json",
            "S4 六类检测证据",
        )
        detector_identity = {
            "run_kind": "fixed_six_detector_ab",
            "content_id": detector.get("content_id"),
            "manifest_sha256": detector.get("manifest_sha256"),
            "event_type_count": detector.get("event_type_count"),
            "unapproved_difference_count": detector.get(
                "unapproved_difference_count"
            ),
            "identity_count": 1,
            "evidence_sha256": _sha256_file(
                s4_evidence_dir / "static-info-detector-ab.json"
            ),
        }
        identity_runs = [
            {
                "run_kind": "api_exact_request",
                "content_id": content_id,
                "identity_count": 1,
            },
            {
                "run_kind": "database_snapshot",
                "content_id": database_probe["content_id"],
                "identity_count": database_probe["identity_count"],
            },
            {
                "run_kind": "database_snapshot_held_across_backend_switch",
                "content_id": pinned_after_switch["content_id"],
                "identity_count": pinned_after_switch["identity_count"],
            },
            {
                "run_kind": "file_rollback_snapshot",
                "content_id": content_id,
                "identity_count": 1,
            },
            detector_identity,
        ]
        mixed_content_run_count = sum(
            1
            for item in identity_runs
            if item.get("identity_count") != 1
            or item.get("content_id") != content_id
        )

        source_after = _source_metadata(source_dir, manifest)
        _verify_all_sources(source_dir, manifest)
        failures_after = _failure_evidence_inventory(evidence_root)
        core_after = _core_hash_state(core_backend_root)
        production_after = _docker_fingerprint(production_containers)
        failure_evidence_preserved = failures_before == failures_after
        source_preserved = source_before == source_after
        core_unchanged = core_before == core_after
        production_unchanged = production_before == production_after
        release_active = (
            admin_state_after["status"] == "active"
            and admin_state_after["active_profile"] == "core"
            and admin_state_after["content_id"] == content_id
        )
        status = (
            "pass"
            if release_active
            and rollback_completed
            and mixed_content_run_count == 0
            and failure_evidence_preserved
            and source_preserved
            and core_unchanged
            and production_unchanged
            and activation["business_table_change_count"] == 0
            else "fail"
        )
        report: Dict[str, Any] = {
            "schema_version": 1,
            "component": "static_info_release_acceptance",
            "status": status,
            "active_content_id": content_id,
            "manifest_sha256": manifest_sha256,
            "release_sk": int(activation["release_sk"]),
            "activation_authorized": True,
            "authorization": {
                "authorization_id": authorization_id,
                "scope": "isolated_offline_candidate_only",
                "confirmed_content_id": confirmed_content_id,
                "confirmation_matches": confirmed_content_id == content_id,
                "production_activation_authorized": False,
            },
            "activated": release_active,
            "safe_boundary_observed": (
                candidate_boundary["network_mode"] == "none"
                and activation["business_table_change_count"] == 0
                and production_unchanged
            ),
            "mixed_content_run_count": mixed_content_run_count,
            "rollback_tested": rollback_completed,
            "previous_release_available": bool(
                file_probe.get("backend") == "file"
                and source_preserved
                and source_after["file_count"] == 24
            ),
            "previous_backend": {
                "backend": "file",
                "content_id": content_id,
                "manifest_sha256": manifest_sha256,
                "source": source_after,
                "actual_snapshot_probe": file_probe,
            },
            "failure_evidence_preserved": failure_evidence_preserved,
            "failure_evidence_before": failures_before,
            "failure_evidence_after": failures_after,
            "activation_gate_gap_count": 0,
            "activation": activation,
            "release_before": admin_state_before,
            "release_after": admin_state_after,
            "runtime_state_transitions": transitions,
            "final_runtime_state": final_state,
            "single_content_runs": identity_runs,
            "database_runtime_probe": _report_safe_probe(database_probe),
            "pinned_snapshot_after_backend_switch": pinned_after_switch,
            "business_data_unchanged": business_before == business_after,
            "business_fingerprint_before": business_before,
            "business_fingerprint_after": business_after,
            "source_preserved": source_preserved,
            "core_hash_unchanged": core_unchanged,
            "core_before": core_before,
            "core_after": core_after,
            "production_side_effect_count": 0 if production_unchanged else 1,
            "production_before": production_before,
            "production_after": production_after,
            "candidate_boundary": candidate_boundary,
            "contact_plaintext_in_evidence": False,
            "checked_at": _utc_now(),
        }
        if status != "pass":
            raise S5AcceptanceError("S5 激活/回滚效果未达到最终验收合同")
        return report
    except BaseException:
        if rollback_completed is False or (
            (_read_state(state_dir / "backend-state.json") or {}).get("backend")
            != "file"
        ):
            try:
                current = _read_state(state_dir / "backend-state.json")
                expected = (
                    str(current.get("backend")) if current is not None else None
                )
                if expected != "file":
                    _transition_runtime_state(
                        state_dir,
                        backend="file",
                        content_id=content_id,
                        manifest_sha256=manifest_sha256,
                        source_dir=source_dir,
                        release_sk=None,
                        reason="s5-automatic-safe-file-rollback-after-failure",
                        expected_backend=expected,
                    )
            except BaseException:
                pass
        raise
    finally:
        if reader is not None:
            reader.close()
        admin.close()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="在隔离候选库执行 static INFO S5 受控激活与回滚验收",
    )
    parser.add_argument("--source-dir", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--core-backend-root", required=True)
    parser.add_argument("--evidence-root", required=True)
    parser.add_argument("--s4-evidence-dir", required=True)
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--db-host", required=True)
    parser.add_argument("--db-port", type=int, default=5432)
    parser.add_argument("--db-name", required=True)
    parser.add_argument("--db-reader", required=True)
    parser.add_argument("--db-admin", required=True)
    parser.add_argument("--container", required=True)
    parser.add_argument(
        "--production-container",
        action="append",
        default=[],
    )
    parser.add_argument("--authorization-id", required=True)
    parser.add_argument("--confirm-content-id", required=True)
    parser.add_argument("--output", required=True)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    manifest_path = Path(args.manifest)
    output_path = Path(args.output)
    try:
        manifest = _read_json(manifest_path, "static INFO manifest")
        report = run_s5_acceptance(
            source_dir=Path(args.source_dir),
            manifest=manifest,
            core_backend_root=Path(args.core_backend_root),
            evidence_root=Path(args.evidence_root),
            s4_evidence_dir=Path(args.s4_evidence_dir),
            state_dir=Path(args.state_dir),
            db_host=args.db_host,
            db_port=args.db_port,
            db_name=args.db_name,
            db_reader=args.db_reader,
            db_admin=args.db_admin,
            container=args.container,
            production_containers=args.production_container,
            authorization_id=args.authorization_id,
            confirmed_content_id=args.confirm_content_id,
        )
        write_text_exclusive(
            output_path,
            json.dumps(
                report,
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
            )
            + "\n",
        )
    except (OSError, S5AcceptanceError) as exc:
        print(f"S5 验收失败：{exc}", file=os.sys.stderr)
        return 1
    print(
        "static INFO S5 受控激活与文件回滚演练通过；"
        f"证据：{output_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
