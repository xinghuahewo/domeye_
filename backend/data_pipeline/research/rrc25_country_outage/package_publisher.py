"""RRC25 伊朗研究包的一次性、不可覆盖内容发布。

调用方必须显式给出一个已存在的空目录、全部内容字节以及与之
完全匹配的 package manifest。本模块不识别生产路径，不把本机绝对
路径写入语义内容，也不覆盖任何已存在的文件。
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path, PurePosixPath
import secrets
import stat
from typing import Any, Mapping

from .package_manifest import (
    ResearchPackageError,
    build_package_manifest,
    publish_package_metadata,
    verify_package_directory,
    verify_published_package,
)


_CONTENT_MODE = 0o440
_DIRECTORY_MODE = 0o750


def _validated_manifest(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """在写盘前重建 manifest，拒绝额外字段或被篡改的内容寻址身份。"""

    if not isinstance(manifest, Mapping):
        raise ResearchPackageError("manifest 必须是对象")
    expected_fields = {
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
    if set(manifest) != expected_fields:
        raise ResearchPackageError("manifest 顶层字段不闭合")
    try:
        rebuilt = build_package_manifest(
            run_id=manifest.get("run_id"),
            study_id=manifest.get("study_id"),
            incident_ref=manifest.get("incident_ref"),
            execution_mode=manifest.get("execution_mode"),
            acceptance_state=manifest.get("acceptance_state"),
            bindings=manifest.get("bindings"),
            contents=manifest.get("contents"),
        )
    except ResearchPackageError:
        raise
    except (KeyError, TypeError) as error:
        raise ResearchPackageError("manifest 结构非法") from error
    if dict(manifest) != rebuilt:
        raise ResearchPackageError("manifest 内容寻址身份不一致")
    return rebuilt


def _validated_contents(
    contents: Mapping[str, bytes],
    manifest: Mapping[str, Any],
) -> dict[str, bytes]:
    """将字节集与清单在内存中闭合，避免 mismatch 留下半包。"""

    if not isinstance(contents, Mapping):
        raise ResearchPackageError("contents 必须是路径到 bytes 的映射")
    normalized: dict[str, bytes] = {}
    for path, payload in contents.items():
        if not isinstance(path, str):
            raise ResearchPackageError("contents 路径必须是字符串")
        if not isinstance(payload, bytes):
            raise ResearchPackageError(f"contents[{path!r}] 必须是 bytes")
        normalized[path] = payload

    refs = {item["path"]: item for item in manifest["contents"]}
    observed = set(normalized)
    expected = set(refs)
    if observed != expected:
        raise ResearchPackageError(
            "contents 与 manifest 路径集不闭合；missing={} extra={}".format(
                sorted(expected - observed), sorted(observed - expected)
            )
        )

    pure_paths = {path: PurePosixPath(path) for path in expected}
    for path, pure in pure_paths.items():
        for parent in pure.parents:
            if parent == PurePosixPath("."):
                break
            if parent.as_posix() in expected:
                raise ResearchPackageError(
                    f"contents 路径存在文件/目录冲突：{parent.as_posix()} 与 {path}"
                )

        payload = normalized[path]
        if len(payload) != refs[path]["size_bytes"]:
            raise ResearchPackageError(f"contents 大小与 manifest 不一致：{path}")
        if hashlib.sha256(payload).hexdigest() != refs[path]["sha256"]:
            raise ResearchPackageError(f"contents 哈希与 manifest 不一致：{path}")
    return normalized


def _empty_regular_directory(root: Path) -> None:
    try:
        metadata = root.lstat()
    except OSError as error:
        raise ResearchPackageError("研究包输出目录不存在或不可读") from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise ResearchPackageError("研究包输出根必须是非符号链接目录")
    try:
        with os.scandir(root) as entries:
            first = next(entries, None)
    except OSError as error:
        raise ResearchPackageError("研究包输出目录不可扫描") from error
    if first is not None:
        if first.is_symlink():
            raise ResearchPackageError("研究包输出目录内含符号链接，拒绝发布")
        raise FileExistsError("研究包输出目录非空，拒绝覆盖")


def _checked_directory(path: Path) -> None:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise ResearchPackageError("研究包发布父目录不存在或不可读") from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise ResearchPackageError("研究包发布父路径必须是非符号链接目录")


def _create_parent_directories(root: Path, paths: set[str]) -> None:
    parents = {
        parent
        for value in paths
        for parent in PurePosixPath(value).parents
        if parent != PurePosixPath(".")
    }
    for relative in sorted(parents, key=lambda value: (len(value.parts), value.as_posix())):
        directory = root.joinpath(*relative.parts)
        try:
            directory.mkdir(mode=_DIRECTORY_MODE)
        except FileExistsError as error:
            # 输入根在发布前已验证为空；此处的已存在只能是竞态或干预。
            raise FileExistsError(f"研究包路径已存在，拒绝复用：{relative.as_posix()}") from error
        _checked_directory(directory)


def _atomic_publish_bytes(destination: Path, payload: bytes) -> None:
    """在目标同目录落盘，再用 hard-link 完成 create-if-absent 发布。"""

    parent = destination.parent
    _checked_directory(parent)
    try:
        destination.lstat()
    except FileNotFoundError:
        pass
    except OSError as error:
        raise ResearchPackageError("研究包目标路径不可检查") from error
    else:
        raise FileExistsError(f"研究包文件已存在，拒绝覆盖：{destination.name}")

    temporary = parent / (
        f".{destination.name}.tmp-{os.getpid()}-{secrets.token_hex(8)}"
    )
    descriptor = None
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(temporary, flags, _CONTENT_MODE)
        os.fchmod(descriptor, _CONTENT_MODE)
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise ResearchPackageError("研究包内容写入未取得进展")
            view = view[written:]
        os.fsync(descriptor)
        written_meta = os.fstat(descriptor)
        if not stat.S_ISREG(written_meta.st_mode):
            raise ResearchPackageError("研究包临时内容不是普通文件")
        if stat.S_IMODE(written_meta.st_mode) != _CONTENT_MODE:
            raise ResearchPackageError("研究包内容权限不是 0440")
        os.close(descriptor)
        descriptor = None
        try:
            os.link(temporary, destination, follow_symlinks=False)
        except FileExistsError as error:
            raise FileExistsError(
                f"研究包文件已存在，拒绝覆盖：{destination.name}"
            ) from error
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


def _make_read_only(path: Path) -> None:
    """将既有发布 helper 产生的元数据收紧为与内容相同的只读模式。"""

    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ResearchPackageError("研究包元数据不是普通文件")
        os.fchmod(descriptor, _CONTENT_MODE)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def publish_research_package(
    root: os.PathLike[str] | str,
    contents: Mapping[str, bytes],
    manifest: Mapping[str, Any],
) -> dict[str, Any]:
    """将一个已装配研究包发布到显式给出的空目录。

    所有输入身份在首个字节落盘前完成验证。内容文件通过同目录
    临时文件与 hard-link 不可覆盖地发布，随后使用现有 manifest
    helper 生成元数据，并从磁盘重新闭合核验整包。
    """

    rebuilt = _validated_manifest(manifest)
    payloads = _validated_contents(contents, rebuilt)
    directory = Path(root)
    _empty_regular_directory(directory)
    _create_parent_directories(directory, set(payloads))

    for relative, payload in sorted(payloads.items()):
        pure = PurePosixPath(relative)
        destination = directory.joinpath(*pure.parts)
        _atomic_publish_bytes(destination, payload)

    verify_package_directory(directory, rebuilt)
    metadata_paths = publish_package_metadata(directory, rebuilt)
    for metadata_path in metadata_paths:
        _make_read_only(metadata_path)
    verified = verify_published_package(directory)
    if verified != rebuilt:
        raise ResearchPackageError("发布后研究包 manifest 与输入不一致")
    return verified


__all__ = ("publish_research_package",)
