"""为 24 个 INFO 数据文件生成可复算的内容清单。"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence, Tuple

from .catalog import (
    CSV_FIELD_SIZE_LIMIT_BYTES,
    DATA_FILE_SPECS,
    FULL_IMPORTER_VERSION,
    MANIFEST_SCHEMA_VERSION,
    PARSER_VERSION,
    SPEC_BY_NAME,
    DataFileSpec,
)
from .excel import ExcelReadError, detect_excel_container, iter_first_sheet_values
from .output import write_text_exclusive
from .stream_json import iter_top_level_object


class ManifestError(ValueError):
    """来源目录或清单不符合冻结合同。"""


_DOCUMENTATION_FILE_NAME = "README.md"
_CONTENT_FILE_KEYS = (
    "name",
    "dataset_kind",
    "file_format",
    "role",
    "parser",
    "encoding",
    "delimiter",
    "source_priority",
    "size_bytes",
    "sha256",
    "header_sha256",
    "logical_record_count",
    "count_method",
)


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256_value(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def importer_config_sha256() -> str:
    return _sha256_value(
        {
            "parser_version": PARSER_VERSION,
            "full_importer_version": FULL_IMPORTER_VERSION,
            "files": [spec.canonical_dict() for spec in DATA_FILE_SPECS],
        }
    )


def _stable_stat(path: Path) -> os.stat_result:
    result = path.lstat()
    if stat.S_ISLNK(result.st_mode) or not stat.S_ISREG(result.st_mode):
        raise ManifestError(f"来源必须是普通文件且禁止软链接：{path}")
    return result


def _stat_identity(result: os.stat_result) -> Tuple[int, int, int, int, int]:
    return (
        result.st_dev,
        result.st_ino,
        result.st_size,
        result.st_mtime_ns,
        result.st_ctime_ns,
    )


def _hash_and_physical_lines(path: Path) -> Tuple[str, int]:
    digest = hashlib.sha256()
    newline_count = 0
    last_byte = b""
    size = 0
    with path.open("rb") as stream:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            newline_count += chunk.count(b"\n")
            size += len(chunk)
            last_byte = chunk[-1:]
    physical_lines = newline_count + (1 if size and last_byte != b"\n" else 0)
    return digest.hexdigest(), physical_lines


def _normalise_header(values: Iterable[Any]) -> list[str]:
    return ["" if value is None else str(value).strip() for value in values]


def _validate_header(spec: DataFileSpec, header: Sequence[str]) -> None:
    if not header or not any(header):
        raise ManifestError(f"{spec.name} 缺少可识别的 CSV/Excel 表头")
    duplicates = sorted({name for name in header if name and header.count(name) > 1})
    if duplicates:
        raise ManifestError(f"{spec.name} 表头含重复字段：{duplicates}")
    if spec.exact_columns and tuple(header) != spec.required_columns:
        raise ManifestError(
            f"{spec.name} 表头与精确合同不一致；"
            f"实际={list(header)!r}，预期={list(spec.required_columns)!r}"
        )
    missing = [name for name in spec.required_columns if name not in header]
    if missing:
        raise ManifestError(f"{spec.name} 缺少合同字段：{missing}")


def _inspect_csv(path: Path, spec: DataFileSpec) -> Tuple[list[str], int, str]:
    try:
        with path.open("r", encoding=spec.encoding, newline="") as stream:
            csv.field_size_limit(CSV_FIELD_SIZE_LIMIT_BYTES)
            reader = csv.reader(stream, delimiter=spec.delimiter or ",", strict=True)
            try:
                header = _normalise_header(next(reader))
            except StopIteration as exc:
                raise ManifestError(f"{spec.name} 是空 CSV") from exc
            _validate_header(spec, header)
            count = 0
            for count, row in enumerate(reader, start=1):
                if len(row) != len(header):
                    raise ManifestError(
                        f"{spec.name} 第 {count} 条逻辑记录列数为 {len(row)}，"
                        f"表头列数为 {len(header)}"
                    )
    except (UnicodeDecodeError, csv.Error) as exc:
        raise ManifestError(f"{spec.name} CSV 解析失败：{exc}") from exc
    return (
        header,
        count,
        "csv-logical-records-excluding-header"
        f"-field-limit-{CSV_FIELD_SIZE_LIMIT_BYTES}",
    )


def _inspect_excel(path: Path, spec: DataFileSpec) -> Tuple[list[str], int, str]:
    try:
        container = detect_excel_container(path)
        rows = iter_first_sheet_values(path)
        try:
            header = _normalise_header(next(rows))
        except StopIteration as exc:
            raise ManifestError(f"{spec.name} 第一个工作表为空") from exc
        _validate_header(spec, header)
        count = sum(1 for _ in rows)
    except ManifestError:
        raise
    except ExcelReadError as exc:
        raise ManifestError(f"{spec.name} Excel 解析失败：{exc}") from exc
    return (
        header,
        count,
        f"excel-{container}-by-magic-first-sheet-logical-rows-excluding-header",
    )


def _inspect_xlsx(path: Path, spec: DataFileSpec) -> Tuple[list[str], int, str]:
    return _inspect_excel(path, spec)


def _inspect_xls(path: Path, spec: DataFileSpec) -> Tuple[list[str], int, str]:
    return _inspect_excel(path, spec)


def _inspect_json(path: Path, spec: DataFileSpec) -> Tuple[list[str], int, str]:
    try:
        with path.open("r", encoding=spec.encoding, newline="") as stream:
            count = sum(1 for _ in iter_top_level_object(stream))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ManifestError(f"{spec.name} JSON 解析失败：{exc}") from exc
    return ["<top-level-object-key>"], count, "json-top-level-object-entries"


def _inspect_line_text(path: Path, spec: DataFileSpec) -> Tuple[list[str], int, str]:
    count = 0
    try:
        with path.open("r", encoding=spec.encoding, newline="") as stream:
            for line in stream:
                if line.strip():
                    count += 1
    except UnicodeDecodeError as exc:
        raise ManifestError(f"{spec.name} 文本解码失败：{exc}") from exc
    return ["<non-empty-line>"], count, "utf8-non-empty-lines"


def _inspect_file(path: Path, spec: DataFileSpec) -> Dict[str, Any]:
    before = _stable_stat(path)
    digest, physical_line_count = _hash_and_physical_lines(path)
    if spec.file_format == "csv":
        header, logical_count, count_method = _inspect_csv(path, spec)
    elif spec.file_format == "xlsx":
        header, logical_count, count_method = _inspect_xlsx(path, spec)
    elif spec.file_format == "xls":
        header, logical_count, count_method = _inspect_xls(path, spec)
    elif spec.file_format == "json":
        header, logical_count, count_method = _inspect_json(path, spec)
    elif spec.file_format == "line_text":
        header, logical_count, count_method = _inspect_line_text(path, spec)
    else:
        raise ManifestError(f"{spec.name} 使用了未知格式：{spec.file_format}")
    after = _stable_stat(path)
    if _stat_identity(before) != _stat_identity(after):
        raise ManifestError(f"读取期间来源文件发生变化：{path}")

    return {
        "name": spec.name,
        "dataset_kind": spec.dataset_kind,
        "file_format": spec.file_format,
        "role": spec.role,
        "parser": spec.parser,
        "encoding": spec.encoding,
        "delimiter": spec.delimiter,
        "source_priority": spec.source_priority,
        "size_bytes": before.st_size,
        "sha256": digest,
        "modified_time_ns": before.st_mtime_ns,
        "inode": before.st_ino,
        "physical_line_count": physical_line_count,
        "header": header,
        "header_sha256": _sha256_value(header),
        "logical_record_count": logical_count,
        "count_method": count_method,
    }


def _inspect_documentation(path: Path) -> Dict[str, Any]:
    before = _stable_stat(path)
    digest, physical_line_count = _hash_and_physical_lines(path)
    after = _stable_stat(path)
    if _stat_identity(before) != _stat_identity(after):
        raise ManifestError(f"读取期间文档文件发生变化：{path}")
    return {
        "name": path.name,
        "size_bytes": before.st_size,
        "sha256": digest,
        "modified_time_ns": before.st_mtime_ns,
        "physical_line_count": physical_line_count,
    }


def _content_descriptor(manifest: Mapping[str, Any]) -> Dict[str, Any]:
    files = manifest.get("files")
    if not isinstance(files, list):
        raise ManifestError("manifest.files 必须是数组")
    content_files = []
    for item in files:
        if not isinstance(item, Mapping):
            raise ManifestError("manifest.files 的成员必须是对象")
        try:
            content_files.append({key: item.get(key) for key in _CONTENT_FILE_KEYS})
        except AttributeError as exc:
            raise ManifestError("manifest.files 成员格式无效") from exc
    return {
        "schema_version": manifest.get("schema_version"),
        "component": manifest.get("component"),
        "parser_version": manifest.get("parser_version"),
        "full_importer_version": manifest.get("full_importer_version"),
        "importer_config_sha256": manifest.get("importer_config_sha256"),
        "files": content_files,
    }


def build_manifest(
    source_dir: os.PathLike[str] | str,
    *,
    source_release_label: str,
    generated_at: Optional[str] = None,
) -> Dict[str, Any]:
    """严格清点来源目录，并生成与路径、mtime 无关的内容身份。"""

    root = Path(source_dir)
    root_stat = root.lstat()
    if stat.S_ISLNK(root_stat.st_mode) or not stat.S_ISDIR(root_stat.st_mode):
        raise ManifestError(f"来源必须是实际目录且禁止软链接：{root}")
    if not source_release_label or source_release_label.strip() != source_release_label:
        raise ManifestError("source_release_label 不能为空或包含首尾空白")

    allowed_names = set(SPEC_BY_NAME) | {_DOCUMENTATION_FILE_NAME}
    actual_names = {entry.name for entry in root.iterdir()}
    missing = sorted(set(SPEC_BY_NAME) - actual_names)
    unknown = sorted(actual_names - allowed_names)
    if missing or unknown:
        raise ManifestError(f"INFO 文件白名单不一致：missing={missing}，unknown={unknown}")

    files = [_inspect_file(root / spec.name, spec) for spec in DATA_FILE_SPECS]
    documentation_files = []
    readme_path = root / _DOCUMENTATION_FILE_NAME
    if readme_path.exists() or readme_path.is_symlink():
        documentation_files.append(_inspect_documentation(readme_path))

    created = generated_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    manifest: Dict[str, Any] = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "component": "static_info",
        "source_release_label": source_release_label,
        "generated_at": created,
        "parser_version": PARSER_VERSION,
        "full_importer_version": FULL_IMPORTER_VERSION,
        "importer_config_sha256": importer_config_sha256(),
        "file_count": len(files),
        "files": files,
        "documentation_files": documentation_files,
    }
    manifest_sha256 = _sha256_value(_content_descriptor(manifest))
    manifest["manifest_sha256"] = manifest_sha256
    manifest["content_id"] = f"info_v1_{manifest_sha256[:32]}"
    validate_manifest(manifest)
    return manifest


def validate_manifest(manifest: Mapping[str, Any]) -> None:
    """验证清单自身结构和可复算内容身份，不重新读取来源文件。"""

    if manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise ManifestError("static_info manifest schema_version 不受支持")
    if manifest.get("component") != "static_info":
        raise ManifestError("static_info manifest component 无效")
    if (
        not isinstance(manifest.get("source_release_label"), str)
        or not manifest["source_release_label"]
        or manifest["source_release_label"].strip()
        != manifest["source_release_label"]
    ):
        raise ManifestError("static_info manifest source_release_label 无效")
    if manifest.get("parser_version") != PARSER_VERSION:
        raise ManifestError("static_info manifest parser_version 与当前实现不一致")
    if manifest.get("full_importer_version") != FULL_IMPORTER_VERSION:
        raise ManifestError(
            "static_info manifest full_importer_version 与当前实现不一致"
        )
    if manifest.get("importer_config_sha256") != importer_config_sha256():
        raise ManifestError("static_info manifest importer_config_sha256 与当前合同不一致")
    files = manifest.get("files")
    if not isinstance(files, list) or len(files) != len(DATA_FILE_SPECS):
        raise ManifestError("static_info manifest 必须包含 24 个文件")
    if manifest.get("file_count") != len(DATA_FILE_SPECS):
        raise ManifestError("static_info manifest file_count 必须是 24")
    if [item.get("name") for item in files if isinstance(item, Mapping)] != [
        spec.name for spec in DATA_FILE_SPECS
    ]:
        raise ManifestError("static_info manifest 文件顺序或集合与合同不一致")

    for item, spec in zip(files, DATA_FILE_SPECS):
        if not isinstance(item, Mapping):
            raise ManifestError(f"{spec.name} 的 manifest 项不是对象")
        for key in ("sha256", "header_sha256"):
            value = item.get(key)
            if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
                raise ManifestError(f"{spec.name}.{key} 不是 SHA256")
        for key in ("size_bytes", "physical_line_count", "logical_record_count"):
            value = item.get(key)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ManifestError(f"{spec.name}.{key} 必须是非负整数")
        if item.get("dataset_kind") != spec.dataset_kind:
            raise ManifestError(f"{spec.name}.dataset_kind 与合同不一致")
        if (
            item.get("file_format") != spec.file_format
            or item.get("role") != spec.role
            or item.get("parser") != spec.parser
            or item.get("delimiter") != spec.delimiter
            or item.get("source_priority") != spec.source_priority
        ):
            raise ManifestError(f"{spec.name} 的格式、角色或解析合同不一致")
        header = item.get("header")
        if (
            not isinstance(header, list)
            or not all(isinstance(value, str) for value in header)
            or item.get("header_sha256") != _sha256_value(header)
        ):
            raise ManifestError(f"{spec.name}.header 无法通过内容哈希复算")

    expected_sha = _sha256_value(_content_descriptor(manifest))
    if manifest.get("manifest_sha256") != expected_sha:
        raise ManifestError("static_info manifest_sha256 无法从内容描述复算")
    if manifest.get("content_id") != f"info_v1_{expected_sha[:32]}":
        raise ManifestError("static_info content_id 与 manifest_sha256 不一致")


def write_manifest(manifest: Mapping[str, Any], output: os.PathLike[str] | str) -> None:
    """以确定性 JSON 格式写出已经过验证的 manifest。"""

    validate_manifest(manifest)
    write_text_exclusive(
        output,
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
    )
