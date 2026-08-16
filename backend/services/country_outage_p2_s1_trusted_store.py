"""P2-S1 W5 本地可信内容寻址存储与 CAS 指针。

对象写入是不可覆盖的；可变 current pointer 只在文件锁内通过 CAS 更新。崩溃时
旧指针保持可读，重复幂等请求返回同一 outcome，不同请求复用 key 会被拒绝。
"""

from __future__ import annotations

import copy
import fcntl
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import stat
import tempfile
from typing import Any, Mapping

from .country_outage_p2_s1_contract_runtime import (
    W5ContractError,
    canonical_json,
    digest_prefixed,
    strict_json_loads,
    validate_prefixed_digest,
)


_COMPONENT = re.compile(r"^[A-Za-z0-9_.:-]{1,180}$")
_MAX_JSON_BYTES = 64 * 1024 * 1024
_MAX_ARTIFACT_BYTES = 64 * 1024 * 1024


class W5StoreError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 409,
        retryable: bool = False,
        next_action: str | None = None,
    ) -> None:
        self.code = code
        self.status_code = status_code
        self.retryable = retryable
        self.next_action = next_action
        super().__init__(message)


def _component(value: str, label: str) -> str:
    if not isinstance(value, str) or _COMPONENT.fullmatch(value) is None:
        raise W5StoreError("unsafe_store_key", f"{label} 不是安全存储 key", status_code=400)
    return value


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class ContentAddressedStore:
    """单进程/多进程安全的本地隔离 Store。"""

    def __init__(self, root: str | os.PathLike[str]) -> None:
        supplied_path = Path(root).expanduser()
        absolute = supplied_path.absolute()
        current = Path(absolute.anchor)
        for part in absolute.parts[1:]:
            current /= part
            if current.exists() and current.is_symlink() and str(current) != "/var":
                raise W5StoreError("store_root_symlink", f"trusted store 祖先不得是符号链接：{current}")
        self.root = supplied_path.resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        info = self.root.lstat()
        if not stat.S_ISDIR(info.st_mode) or self.root.is_symlink() or info.st_mode & 0o022:
            raise W5StoreError("store_root_unsafe", "Store 根必须是禁止组/其他用户写入的实体目录")
        if hasattr(os, "getuid") and info.st_uid != os.getuid():
            raise W5StoreError("store_root_unsafe", "Store 根必须归当前进程用户所有")
        for name in ("objects", "artifacts", "pointers", "locks", "idempotency", "journals", "staging"):
            path = self.root / name
            path.mkdir(mode=0o700, exist_ok=True)
            child_info = path.lstat()
            if path.is_symlink() or not stat.S_ISDIR(child_info.st_mode) or child_info.st_mode & 0o077:
                raise W5StoreError("store_root_unsafe", f"Store 子目录不安全：{name}")
            if hasattr(os, "getuid") and child_info.st_uid != os.getuid():
                raise W5StoreError("store_root_unsafe", f"Store 子目录 owner 不安全：{name}")

    def put_json(self, kind: str, payload: Mapping[str, Any] | list[Any]) -> dict[str, Any]:
        kind = _component(kind, "kind")
        value = copy.deepcopy(payload)
        object_digest = digest_prefixed(value)
        envelope = {
            "schema_version": "country_outage_p2_s1_w5_stored_json_v1",
            "kind": kind,
            "object_digest": object_digest,
            "payload": value,
        }
        raw = (canonical_json(envelope) + "\n").encode("utf-8")
        if len(raw) > _MAX_JSON_BYTES:
            raise W5StoreError("store_object_too_large", "JSON 对象超过 64 MiB", status_code=413)
        directory = self.root / "objects" / kind
        directory.mkdir(mode=0o700, exist_ok=True)
        target = directory / f"{object_digest.removeprefix('sha256:')}.json"
        self._publish_immutable(target, raw)
        return {"kind": kind, "object_digest": object_digest, "object_ref": str(target.relative_to(self.root))}

    def get_json(self, kind: str, object_digest: str) -> Any:
        kind = _component(kind, "kind")
        validate_prefixed_digest(object_digest, "object_digest")
        path = self.root / "objects" / kind / f"{object_digest.removeprefix('sha256:')}.json"
        raw = self._read_regular(path, _MAX_JSON_BYTES)
        value = strict_json_loads(raw)
        if not isinstance(value, dict) or set(value) != {"schema_version", "kind", "object_digest", "payload"}:
            raise W5StoreError("store_object_invalid", "Store JSON 信封字段不闭合")
        if (
            value.get("schema_version") != "country_outage_p2_s1_w5_stored_json_v1"
            or value.get("kind") != kind
            or value.get("object_digest") != object_digest
            or digest_prefixed(value.get("payload")) != object_digest
            or raw != (canonical_json(value) + "\n").encode("utf-8")
        ):
            raise W5StoreError("store_object_digest_mismatch", "Store JSON 摘要或 canonical bytes 不一致")
        return copy.deepcopy(value["payload"])

    def put_bytes(self, kind: str, content: bytes) -> dict[str, Any]:
        kind = _component(kind, "artifact kind")
        if not isinstance(content, bytes) or len(content) > _MAX_ARTIFACT_BYTES:
            raise W5StoreError("artifact_bytes_invalid", "制品必须是小于等于 64 MiB 的 bytes", status_code=413)
        content_digest = "sha256:" + sha256(content).hexdigest()
        directory = self.root / "artifacts" / kind
        directory.mkdir(mode=0o700, exist_ok=True)
        target = directory / f"{content_digest.removeprefix('sha256:')}.bin"
        self._publish_immutable(target, content)
        return {
            "kind": kind,
            "sha256": content_digest,
            "byte_length": len(content),
            "artifact_ref": str(target.relative_to(self.root)),
        }

    def get_bytes(self, kind: str, content_digest: str) -> bytes:
        kind = _component(kind, "artifact kind")
        validate_prefixed_digest(content_digest, "artifact digest")
        path = self.root / "artifacts" / kind / f"{content_digest.removeprefix('sha256:')}.bin"
        raw = self._read_regular(path, _MAX_ARTIFACT_BYTES)
        if "sha256:" + sha256(raw).hexdigest() != content_digest:
            raise W5StoreError("artifact_digest_mismatch", "制品字节摘要不一致")
        return raw

    def read_pointer(self, namespace: str, key: str) -> dict[str, Any] | None:
        path = self._pointer_path(namespace, key)
        if not path.exists():
            return None
        raw = self._read_regular(path, 64 * 1024)
        value = strict_json_loads(raw)
        expected = {
            "schema_version",
            "namespace",
            "key",
            "object_digest",
            "revision",
            "idempotency_key",
            "request_digest",
            "outcome_digest",
        }
        if not isinstance(value, dict) or set(value) != expected:
            raise W5StoreError("pointer_invalid", "current pointer 字段不闭合")
        if value.get("namespace") != namespace or value.get("key") != key:
            raise W5StoreError("pointer_invalid", "current pointer 身份不一致")
        validate_prefixed_digest(value.get("object_digest"), "pointer.object_digest")
        validate_prefixed_digest(value.get("request_digest"), "pointer.request_digest")
        validate_prefixed_digest(value.get("outcome_digest"), "pointer.outcome_digest")
        material = {key: value[key] for key in ("namespace", "key", "object_digest", "revision", "idempotency_key", "request_digest")}
        if value["outcome_digest"] != digest_prefixed(material) or raw != (canonical_json(value) + "\n").encode("utf-8"):
            raise W5StoreError("pointer_digest_mismatch", "current pointer 摘要或 canonical bytes 不一致")
        return copy.deepcopy(value)

    def compare_and_swap_pointer(
        self,
        namespace: str,
        key: str,
        *,
        expected_current_digest: str | None,
        expected_revision: int | None,
        new_object_digest: str,
        new_revision: int,
        idempotency_key: str,
        request_digest: str,
    ) -> dict[str, Any]:
        namespace = _component(namespace, "namespace")
        key = _component(key, "pointer key")
        idempotency_key = _component(idempotency_key, "idempotency_key")
        if expected_current_digest is not None:
            validate_prefixed_digest(expected_current_digest, "expected_current_digest")
        validate_prefixed_digest(new_object_digest, "new_object_digest")
        validate_prefixed_digest(request_digest, "request_digest")
        if isinstance(new_revision, bool) or not isinstance(new_revision, int) or new_revision < 1:
            raise W5StoreError("revision_invalid", "new_revision 必须是正整数", status_code=400)
        lock_path = self.root / "locks" / f"{sha256(f'{namespace}:{key}'.encode()).hexdigest()}.lock"
        lock_descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(lock_descriptor, fcntl.LOCK_EX)
            self._recover_key_locked(namespace, key)
            current = self.read_pointer(namespace, key)
            if current is not None and current.get("idempotency_key") == idempotency_key:
                if current.get("request_digest") != request_digest:
                    raise W5StoreError("idempotency_conflict", "同一幂等 key 对应不同请求")
                return {**current, "replayed": True}
            prior = self.read_idempotency(namespace, key, idempotency_key)
            if prior is not None:
                if prior.get("request_digest") != request_digest:
                    raise W5StoreError("idempotency_conflict", "同一幂等 key 对应不同请求")
                return {**prior["outcome"], "replayed": True}
            live_digest = current.get("object_digest") if current else None
            live_revision = current.get("revision") if current else None
            if live_digest != expected_current_digest or live_revision != expected_revision:
                raise W5StoreError(
                    "compare_and_swap_conflict",
                    "current pointer 已变化",
                    retryable=True,
                    next_action="读取最新 investigation 后使用新 revision/digest 重试",
                )
            if current is None and (new_revision != 1 or expected_revision is not None):
                raise W5StoreError("revision_conflict", "首次提交 revision 必须为 1")
            if current is not None and new_revision != current["revision"] + 1:
                raise W5StoreError("revision_conflict", "revision 必须严格递增 1")
            outcome_material = {
                "namespace": namespace,
                "key": key,
                "object_digest": new_object_digest,
                "revision": new_revision,
                "idempotency_key": idempotency_key,
                "request_digest": request_digest,
            }
            pointer = {
                "schema_version": "country_outage_p2_s1_w5_current_pointer_v1",
                **outcome_material,
                "outcome_digest": digest_prefixed(outcome_material),
            }
            self._write_prepare(namespace, key, idempotency_key, request_digest, pointer)
            self._replace_pointer(self._pointer_path(namespace, key), (canonical_json(pointer) + "\n").encode("utf-8"))
            self._write_idempotency(namespace, key, idempotency_key, request_digest, pointer)
            return {**pointer, "replayed": False}
        finally:
            try:
                fcntl.flock(lock_descriptor, fcntl.LOCK_UN)
            finally:
                os.close(lock_descriptor)

    def read_idempotency(self, namespace: str, key: str, idempotency_key: str) -> dict[str, Any] | None:
        namespace = _component(namespace, "namespace")
        key = _component(key, "key")
        idempotency_key = _component(idempotency_key, "idempotency_key")
        directory = self.root / "idempotency" / namespace / key
        path = directory / f"{sha256(idempotency_key.encode()).hexdigest()}.json"
        if not path.exists():
            return None
        raw = self._read_regular(path, 128 * 1024)
        value = strict_json_loads(raw)
        if not isinstance(value, dict) or set(value) != {"schema_version", "namespace", "key", "idempotency_key", "request_digest", "outcome"}:
            raise W5StoreError("idempotency_record_invalid", "幂等回执字段不闭合")
        if value.get("namespace") != namespace or value.get("key") != key or value.get("idempotency_key") != idempotency_key:
            raise W5StoreError("idempotency_record_invalid", "幂等回执身份不一致")
        validate_prefixed_digest(value.get("request_digest"), "idempotency.request_digest")
        outcome = value.get("outcome")
        if not isinstance(outcome, Mapping) or outcome.get("namespace") != namespace or outcome.get("key") != key or outcome.get("idempotency_key") != idempotency_key or outcome.get("request_digest") != value["request_digest"]:
            raise W5StoreError("idempotency_record_invalid", "幂等 outcome 与 key/request 未闭包")
        material = {field: outcome.get(field) for field in ("namespace", "key", "object_digest", "revision", "idempotency_key", "request_digest")}
        if outcome.get("outcome_digest") != digest_prefixed(material) or raw != (canonical_json(value) + "\n").encode("utf-8"):
            raise W5StoreError("idempotency_digest_mismatch", "幂等回执摘要或 canonical bytes 不一致")
        return copy.deepcopy(value)

    def list_json(self, kind: str) -> list[dict[str, Any]]:
        kind = _component(kind, "kind")
        directory = self.root / "objects" / kind
        if not directory.exists():
            return []
        result = []
        for path in sorted(directory.glob("*.json")):
            if path.is_symlink():
                raise W5StoreError("store_object_unsafe", "对象目录包含 symlink")
            digest = "sha256:" + path.stem
            result.append(self.get_json(kind, digest))
        return result

    def _pointer_path(self, namespace: str, key: str) -> Path:
        namespace = _component(namespace, "namespace")
        key = _component(key, "pointer key")
        directory = self.root / "pointers" / namespace
        directory.mkdir(mode=0o700, exist_ok=True)
        return directory / f"{key}.json"

    def _write_idempotency(
        self,
        namespace: str,
        key: str,
        idempotency_key: str,
        request_digest: str,
        outcome: Mapping[str, Any],
    ) -> None:
        directory = self.root / "idempotency" / namespace / key
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        path = directory / f"{sha256(idempotency_key.encode()).hexdigest()}.json"
        value = {
            "schema_version": "country_outage_p2_s1_w5_idempotency_record_v1",
            "namespace": namespace,
            "key": key,
            "idempotency_key": idempotency_key,
            "request_digest": request_digest,
            "outcome": copy.deepcopy(dict(outcome)),
        }
        self._publish_immutable(path, (canonical_json(value) + "\n").encode("utf-8"))

    def _write_prepare(
        self,
        namespace: str,
        key: str,
        idempotency_key: str,
        request_digest: str,
        outcome: Mapping[str, Any],
    ) -> None:
        directory = self.root / "journals" / namespace / key
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        value = {
            "schema_version": "country_outage_p2_s1_w5_cas_prepare_v1",
            "namespace": namespace,
            "key": key,
            "idempotency_key": idempotency_key,
            "request_digest": request_digest,
            "outcome": copy.deepcopy(dict(outcome)),
        }
        path = directory / f"{sha256(idempotency_key.encode()).hexdigest()}.json"
        self._publish_immutable(path, (canonical_json(value) + "\n").encode("utf-8"))

    def _recover_key_locked(self, namespace: str, key: str) -> None:
        directory = self.root / "journals" / namespace / key
        if not directory.exists():
            return
        current = self.read_pointer(namespace, key)
        if current is None:
            return
        for path in sorted(directory.glob("*.json")):
            raw = self._read_regular(path, 128 * 1024)
            value = strict_json_loads(raw)
            expected = {"schema_version", "namespace", "key", "idempotency_key", "request_digest", "outcome"}
            if not isinstance(value, Mapping) or set(value) != expected or raw != (canonical_json(value) + "\n").encode("utf-8"):
                raise W5StoreError("cas_journal_invalid", "CAS prepare journal 不闭合")
            outcome = value.get("outcome")
            if value.get("namespace") != namespace or value.get("key") != key or not isinstance(outcome, Mapping):
                raise W5StoreError("cas_journal_invalid", "CAS prepare journal 身份无效")
            if outcome.get("outcome_digest") != current.get("outcome_digest"):
                continue
            if outcome != current or value.get("request_digest") != current.get("request_digest") or value.get("idempotency_key") != current.get("idempotency_key"):
                raise W5StoreError("cas_journal_mismatch", "CAS prepare journal 与 current pointer 冲突")
            if self.read_idempotency(namespace, key, current["idempotency_key"]) is None:
                self._write_idempotency(namespace, key, current["idempotency_key"], current["request_digest"], current)

    def _replace_pointer(self, target: Path, raw: bytes) -> None:
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
        temporary = Path(temporary_name)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb", closefd=True) as output:
                output.write(raw)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, target)
            _fsync_directory(target.parent)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    def _publish_immutable(self, target: Path, raw: bytes) -> None:
        if target.parent.is_symlink():
            raise W5StoreError("store_path_unsafe", "对象父目录不能是 symlink")
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
        temporary = Path(temporary_name)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb", closefd=True) as output:
                output.write(raw)
                output.flush()
                os.fsync(output.fileno())
            try:
                os.link(temporary, target)
            except FileExistsError:
                existing = self._read_regular(target, max(_MAX_JSON_BYTES, _MAX_ARTIFACT_BYTES))
                if existing != raw:
                    raise W5StoreError("content_address_collision", "同摘要目标存在不同字节")
            _fsync_directory(target.parent)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    @staticmethod
    def _read_regular(path: Path, limit: int) -> bytes:
        try:
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(path, flags)
        except FileNotFoundError as error:
            raise LookupError(f"可信对象不存在：{path.name}") from error
        except OSError as error:
            raise W5StoreError("store_object_unsafe", "可信对象不得是 symlink") from error
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
                raise W5StoreError("store_object_unsafe", "可信对象必须是大小受限的实体文件")
            with os.fdopen(descriptor, "rb", closefd=False) as source:
                raw = source.read(limit + 1)
            if len(raw) > limit:
                raise W5StoreError("store_object_unsafe", "可信对象超限")
            return raw
        finally:
            os.close(descriptor)


__all__ = ["ContentAddressedStore", "W5StoreError"]
