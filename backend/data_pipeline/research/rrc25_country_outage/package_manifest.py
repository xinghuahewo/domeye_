"""RRC25 伊朗研究包的内容清单、SHA256SUMS 与复现核验。

该模块只面向调用方显式给出的独立研究输出目录。它不创建数据库、不识别
生产路径，也不覆盖任何已有文件。运行时日期、主机名和绝对路径不进入语义
指纹，因此两个空目录可验证同一冻结输入的语义复现。
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import stat
from typing import Any, Iterable, Mapping, Sequence


SCHEMA_VERSION = "rrc25-country-outage-research-package/v1"
FINGERPRINT_SCHEMA = "rrc25_country_outage_research_package_fingerprint_v1"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_RUN_ID_RE = re.compile(r"^research_run_v1_[0-9a-f]{24}$")
_CODE_RE = re.compile(r"^[a-z][a-z0-9._-]{0,63}$")


class ResearchPackageError(ValueError):
    """研究包清单、文件身份或发布边界非法。"""


def canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as error:
        raise ResearchPackageError("研究包包含不可规范序列化值") from error


def _safe_relative(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ResearchPackageError(f"{field} 必须是非空相对路径")
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or any(
        part in {"", ".", ".."} for part in path.parts
    ):
        raise ResearchPackageError(f"{field} 必须是安全相对路径")
    return path.as_posix()


def _sha(value: object, field: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise ResearchPackageError(f"{field} 必须是 64 位小写 SHA256")
    return value


def _nonnegative(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ResearchPackageError(f"{field} 必须是非负整数")
    return value


def _normalize_contents(
    contents: Iterable[Mapping[str, Any]],
) -> tuple[dict[str, Any], ...]:
    if isinstance(contents, (str, bytes, Mapping)):
        raise ResearchPackageError("contents 必须是对象序列")
    normalized = []
    paths = set()
    for index, raw in enumerate(contents):
        if not isinstance(raw, Mapping):
            raise ResearchPackageError(f"contents[{index}] 必须是对象")
        if set(raw) != {"kind", "path", "sha256", "size_bytes", "record_count"}:
            raise ResearchPackageError("content ref 字段必须精确闭合")
        kind = raw.get("kind")
        if not isinstance(kind, str) or _CODE_RE.fullmatch(kind) is None:
            raise ResearchPackageError("content.kind 非法")
        path = _safe_relative(raw.get("path"), "content.path")
        if path in paths:
            raise ResearchPackageError("content.path 不得重复")
        if path in {"package-manifest.json", "SHA256SUMS"}:
            raise ResearchPackageError("content 不得占用包元数据保留路径")
        paths.add(path)
        normalized.append(
            {
                "kind": kind,
                "path": path,
                "sha256": _sha(raw.get("sha256"), "content.sha256"),
                "size_bytes": _nonnegative(raw.get("size_bytes"), "content.size_bytes"),
                "record_count": _nonnegative(
                    raw.get("record_count"), "content.record_count"
                ),
            }
        )
    if not normalized:
        raise ResearchPackageError("研究包至少需要一个内容文件")
    return tuple(sorted(normalized, key=lambda item: (item["kind"], item["path"])))


def _normalize_bindings(bindings: Mapping[str, Any]) -> dict[str, str]:
    if not isinstance(bindings, Mapping) or not bindings:
        raise ResearchPackageError("bindings 必须是非空对象")
    normalized = {}
    for key, value in bindings.items():
        if not isinstance(key, str) or _CODE_RE.fullmatch(key) is None:
            raise ResearchPackageError("binding 名称非法")
        normalized[key] = _sha(value, f"bindings.{key}")
    return dict(sorted(normalized.items()))


def build_package_manifest(
    *,
    run_id: str,
    study_id: str,
    incident_ref: str,
    execution_mode: str,
    acceptance_state: str,
    bindings: Mapping[str, str],
    contents: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """构造不含运行时元数据的确定性研究包清单。"""

    if not isinstance(run_id, str) or _RUN_ID_RE.fullmatch(run_id) is None:
        raise ResearchPackageError("run_id 非法")
    if not isinstance(study_id, str) or not study_id:
        raise ResearchPackageError("study_id 不能为空")
    if not isinstance(incident_ref, str) or not incident_ref:
        raise ResearchPackageError("incident_ref 不能为空")
    if execution_mode not in {"full_profile", "bounded_pilot"}:
        raise ResearchPackageError("execution_mode 非法")
    if acceptance_state not in {"accepted", "not_accepted", "pending"}:
        raise ResearchPackageError("acceptance_state 非法")
    normalized_contents = _normalize_contents(contents)
    semantic = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "study_id": study_id,
        "incident_ref": incident_ref,
        "execution_mode": execution_mode,
        "acceptance_state": acceptance_state,
        "bindings": _normalize_bindings(bindings),
        "contents": list(normalized_contents),
        "runtime_metadata_excluded_from_semantic_fingerprint": True,
    }
    fingerprint = hashlib.sha256(
        canonical_json(
            {"schema": FINGERPRINT_SCHEMA, "package": semantic}
        ).encode("utf-8")
    ).hexdigest()
    return {
        **semantic,
        "release_id": "rrc25_iran_v1_" + fingerprint[:24],
        "semantic_fingerprint_sha256": fingerprint,
    }


def _manifest_semantic(manifest: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(manifest, Mapping):
        raise ResearchPackageError("manifest 必须是对象")
    expected = {
        "schema_version",
        "run_id",
        "study_id",
        "incident_ref",
        "execution_mode",
        "acceptance_state",
        "bindings",
        "contents",
        "runtime_metadata_excluded_from_semantic_fingerprint",
        "release_id",
        "semantic_fingerprint_sha256",
    }
    if set(manifest) != expected:
        raise ResearchPackageError("manifest 顶层字段不闭合")
    rebuilt = build_package_manifest(
        run_id=manifest.get("run_id"),
        study_id=manifest.get("study_id"),
        incident_ref=manifest.get("incident_ref"),
        execution_mode=manifest.get("execution_mode"),
        acceptance_state=manifest.get("acceptance_state"),
        bindings=manifest.get("bindings"),
        contents=manifest.get("contents"),
    )
    if dict(manifest) != rebuilt:
        raise ResearchPackageError("manifest 内容寻址身份不一致")
    return rebuilt


def _regular_file_hash(path: Path) -> tuple[str, int]:
    try:
        initial = path.lstat()
    except OSError as error:
        raise ResearchPackageError(f"研究包文件不可读：{path.name}") from error
    if stat.S_ISLNK(initial.st_mode) or not stat.S_ISREG(initial.st_mode):
        raise ResearchPackageError("研究包内容必须是非符号链接普通文件")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    digest = hashlib.sha256()
    size = 0
    try:
        before = os.fstat(descriptor)
        fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        if any(getattr(initial, field) != getattr(before, field) for field in fields):
            raise ResearchPackageError("研究包文件在打开前发生变化")
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                break
            digest.update(block)
            size += len(block)
        after = os.fstat(descriptor)
        if any(getattr(before, field) != getattr(after, field) for field in fields):
            raise ResearchPackageError("研究包文件在核验期间发生变化")
    finally:
        os.close(descriptor)
    return digest.hexdigest(), size


def _regular_file_bytes(path: Path, *, maximum_bytes: int) -> bytes:
    """以拒绝符号链接和读中变更的方式读取小型元数据文件。"""

    if not isinstance(maximum_bytes, int) or isinstance(maximum_bytes, bool) or maximum_bytes <= 0:
        raise ResearchPackageError("maximum_bytes 必须是正整数")
    try:
        initial = path.lstat()
    except OSError as error:
        raise ResearchPackageError(f"研究包元数据不可读：{path.name}") from error
    if stat.S_ISLNK(initial.st_mode) or not stat.S_ISREG(initial.st_mode):
        raise ResearchPackageError("研究包元数据必须是非符号链接普通文件")
    if initial.st_size > maximum_bytes:
        raise ResearchPackageError(f"研究包元数据超过限制：{path.name}")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    chunks: list[bytes] = []
    total = 0
    try:
        before = os.fstat(descriptor)
        fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        if any(getattr(initial, field) != getattr(before, field) for field in fields):
            raise ResearchPackageError("研究包元数据在打开前发生变化")
        while True:
            block = os.read(descriptor, 64 * 1024)
            if not block:
                break
            total += len(block)
            if total > maximum_bytes:
                raise ResearchPackageError(f"研究包元数据超过限制：{path.name}")
            chunks.append(block)
        after = os.fstat(descriptor)
        if any(getattr(before, field) != getattr(after, field) for field in fields):
            raise ResearchPackageError("研究包元数据在核验期间发生变化")
    finally:
        os.close(descriptor)
    return b"".join(chunks)


def verify_package_directory(
    root: os.PathLike[str] | str,
    manifest: Mapping[str, Any],
    *,
    require_metadata_files: bool = False,
) -> dict[str, Any]:
    """核验清单中的每个文件，并拒绝未登记内容和符号链接。"""

    rebuilt = _manifest_semantic(manifest)
    directory = Path(root)
    try:
        metadata = directory.lstat()
    except OSError as error:
        raise ResearchPackageError("研究包目录不存在") from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise ResearchPackageError("研究包根必须是非符号链接目录")
    expected = {item["path"] for item in rebuilt["contents"]}
    if require_metadata_files:
        expected |= {"package-manifest.json", "SHA256SUMS"}
    observed = set()
    for path in directory.rglob("*"):
        relative = path.relative_to(directory).as_posix()
        if path.is_dir() and not path.is_symlink():
            continue
        observed.add(relative)
    if observed != expected:
        raise ResearchPackageError(
            "研究包文件集合不闭合；missing={} extra={}".format(
                sorted(expected - observed), sorted(observed - expected)
            )
        )
    for item in rebuilt["contents"]:
        digest, size = _regular_file_hash(directory / item["path"])
        if digest != item["sha256"] or size != item["size_bytes"]:
            raise ResearchPackageError(f"研究包文件哈希或大小不一致：{item['path']}")
    return rebuilt


def _publish_bytes(destination: Path, payload: bytes, mode: int = 0o640) -> None:
    parent = destination.parent
    metadata = parent.lstat()
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise ResearchPackageError("发布父目录必须是非符号链接目录")
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(f"研究包文件已存在，拒绝覆盖：{destination}")
    temporary = parent / (
        f".{destination.name}.tmp-{os.getpid()}-{secrets.token_hex(8)}"
    )
    descriptor = None
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise ResearchPackageError("研究包元数据写入未取得进展")
            view = view[written:]
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        try:
            os.link(temporary, destination, follow_symlinks=False)
        except FileExistsError:
            raise FileExistsError(f"研究包文件已存在，拒绝覆盖：{destination}")
        directory_fd = os.open(parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def publish_package_metadata(
    root: os.PathLike[str] | str,
    manifest: Mapping[str, Any],
) -> tuple[Path, Path]:
    """在已核验内容目录中不可覆盖地发布 manifest 与 SHA256SUMS。"""

    directory = Path(root)
    manifest_path = directory / "package-manifest.json"
    sums_path = directory / "SHA256SUMS"
    if (
        manifest_path.exists()
        or manifest_path.is_symlink()
        or sums_path.exists()
        or sums_path.is_symlink()
    ):
        raise FileExistsError("研究包元数据已存在，拒绝覆盖")
    rebuilt = verify_package_directory(directory, manifest)
    manifest_bytes = (canonical_json(rebuilt) + "\n").encode("utf-8")
    _publish_bytes(manifest_path, manifest_bytes)
    rows = [
        (item["sha256"], item["path"]) for item in rebuilt["contents"]
    ]
    rows.append((hashlib.sha256(manifest_bytes).hexdigest(), manifest_path.name))
    sums = "".join(f"{digest}  {path}\n" for digest, path in sorted(rows, key=lambda row: row[1]))
    _publish_bytes(sums_path, sums.encode("utf-8"))
    return manifest_path, sums_path


def verify_published_package(root: os.PathLike[str] | str) -> dict[str, Any]:
    """从磁盘重新读取 manifest/SHA256SUMS 并完成整包闭合核验。"""

    directory = Path(root)
    manifest_path = directory / "package-manifest.json"
    raw = _regular_file_bytes(manifest_path, maximum_bytes=16 * 1024 * 1024)
    try:
        manifest = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ResearchPackageError("package-manifest.json 非严格 UTF-8 JSON") from error
    rebuilt = verify_package_directory(
        directory, manifest, require_metadata_files=True
    )
    sums_path = directory / "SHA256SUMS"
    expected = {
        item["path"]: item["sha256"] for item in rebuilt["contents"]
    }
    expected[manifest_path.name] = hashlib.sha256(raw).hexdigest()
    observed = {}
    try:
        sums_text = _regular_file_bytes(
            sums_path, maximum_bytes=16 * 1024 * 1024
        ).decode("utf-8")
    except UnicodeDecodeError as error:
        raise ResearchPackageError("SHA256SUMS 不是 UTF-8") from error
    for line in sums_text.splitlines():
        if len(line) < 67 or line[64:66] != "  ":
            raise ResearchPackageError("SHA256SUMS 行格式非法")
        digest, path = line[:64], line[66:]
        if path in observed:
            raise ResearchPackageError("SHA256SUMS 路径重复")
        observed[path] = _sha(digest, "SHA256SUMS.digest")
    if observed != expected:
        raise ResearchPackageError("SHA256SUMS 与 manifest 不闭合")
    return rebuilt


__all__ = (
    "ResearchPackageError",
    "build_package_manifest",
    "canonical_json",
    "publish_package_metadata",
    "verify_package_directory",
    "verify_published_package",
)
