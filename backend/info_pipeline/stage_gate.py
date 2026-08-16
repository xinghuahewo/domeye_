"""static INFO 阶段结束偏差门禁。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .output import write_text_exclusive


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = (
    REPOSITORY_ROOT
    / "contracts"
    / "info"
    / "static-info-final-acceptance-v1.json"
)
_MISSING = object()


class StageGateError(ValueError):
    """阶段结束门禁无法安全执行。"""


def _reject_duplicate_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise StageGateError(f"JSON 存在重复键：{key}")
        result[key] = value
    return result


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path, label: str) -> Tuple[Mapping[str, Any], str]:
    if path.is_symlink() or not path.is_file():
        raise StageGateError(f"{label}必须是普通文件且禁止软链接：{path}")
    try:
        raw = path.read_text(encoding="utf-8")
        value = json.loads(raw, object_pairs_hook=_reject_duplicate_pairs)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StageGateError(f"{label}读取失败：{exc}") from exc
    if not isinstance(value, dict):
        raise StageGateError(f"{label}顶层必须是 JSON 对象")
    return value, hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _validate_contract(
    contract: Mapping[str, Any],
    repository_root: Path,
) -> Tuple[List[Mapping[str, Any]], Dict[str, int], Dict[str, str]]:
    if contract.get("schema_version") != 1:
        raise StageGateError("验收合同 schema_version 必须为 1")
    contract_id = contract.get("contract_id")
    if not isinstance(contract_id, str) or not contract_id:
        raise StageGateError("验收合同 contract_id 缺失")

    document_hashes: Dict[str, str] = {}
    for key in ("acceptance_document", "stage_plan_document"):
        item = contract.get(key)
        if not isinstance(item, dict):
            raise StageGateError(f"验收合同缺少 {key}")
        relative = item.get("path")
        expected_sha = item.get("sha256")
        if not isinstance(relative, str) or not re.fullmatch(
            r"[0-9a-f]{64}",
            str(expected_sha),
        ):
            raise StageGateError(f"验收合同 {key} 路径或哈希无效")
        relative_path = Path(relative)
        if relative_path.is_absolute() or ".." in relative_path.parts:
            raise StageGateError(f"验收合同 {key} 路径越过仓库边界")
        document_path = repository_root / relative
        try:
            document_path.resolve().relative_to(repository_root.resolve())
        except (OSError, ValueError) as exc:
            raise StageGateError(
                f"验收合同 {key} 路径越过仓库边界"
            ) from exc
        if document_path.is_symlink() or not document_path.is_file():
            raise StageGateError(f"合同引用文档缺失或为软链接：{relative}")
        observed_sha = _sha256(document_path)
        if observed_sha != expected_sha:
            raise StageGateError(
                f"合同引用文档发生漂移：{relative} "
                f"observed={observed_sha} expected={expected_sha}"
            )
        document_hashes[key] = observed_sha

    requirements = contract.get("requirements")
    stages = contract.get("stages")
    if not isinstance(requirements, list) or not isinstance(stages, list):
        raise StageGateError("验收合同 requirements/stages 必须为数组")
    if not stages:
        raise StageGateError("验收合同至少需要一个阶段")

    stage_index: Dict[str, int] = {}
    previous_id: Optional[str] = None
    for index, stage in enumerate(stages):
        if not isinstance(stage, dict):
            raise StageGateError("验收合同阶段项必须是对象")
        stage_id = stage.get("id")
        predecessor = stage.get("predecessor")
        if not isinstance(stage_id, str) or stage_id in stage_index:
            raise StageGateError(f"验收合同阶段 ID 缺失或重复：{stage_id!r}")
        if predecessor != previous_id:
            raise StageGateError(
                f"阶段 {stage_id} 前序必须为 {previous_id!r}，"
                f"实际为 {predecessor!r}"
            )
        stage_index[stage_id] = index
        previous_id = stage_id

    requirement_ids = set()
    declared_due: Dict[str, str] = {}
    for requirement in requirements:
        if not isinstance(requirement, dict):
            raise StageGateError("验收合同要求项必须是对象")
        requirement_id = requirement.get("id")
        due_stage = requirement.get("due_stage")
        if (
            not isinstance(requirement_id, str)
            or requirement_id in requirement_ids
            or due_stage not in stage_index
        ):
            raise StageGateError(
                f"验收要求 ID 重复、缺失或到期阶段无效：{requirement_id!r}"
            )
        requirement_ids.add(requirement_id)
        declared_due[requirement_id] = due_stage

    listed_due = set()
    for stage in stages:
        newly_due = stage.get("newly_due_requirements")
        if not isinstance(newly_due, list):
            raise StageGateError(
                f"阶段 {stage.get('id')} newly_due_requirements 必须为数组"
            )
        for requirement_id in newly_due:
            if (
                requirement_id not in requirement_ids
                or requirement_id in listed_due
                or declared_due[requirement_id] != stage["id"]
            ):
                raise StageGateError(
                    f"阶段 {stage['id']} 到期要求映射无效：{requirement_id!r}"
                )
            listed_due.add(requirement_id)
    if listed_due != requirement_ids:
        missing = sorted(requirement_ids - listed_due)
        raise StageGateError(f"验收要求没有唯一到期阶段：{missing}")

    return stages, stage_index, document_hashes


def _json_pointer(value: Any, pointer: str) -> Any:
    if pointer == "":
        return value
    if not isinstance(pointer, str) or not pointer.startswith("/"):
        return _MISSING
    current = value
    for raw_part in pointer[1:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict):
            if part not in current:
                return _MISSING
            current = current[part]
        elif isinstance(current, list):
            try:
                index = int(part)
            except ValueError:
                return _MISSING
            if index < 0 or index >= len(current):
                return _MISSING
            current = current[index]
        else:
            return _MISSING
    return current


def _display(value: Any) -> Any:
    if value is _MISSING:
        return "<missing>"
    return value


def _evaluate_operator(observed: Any, operator: str, expected: Any) -> bool:
    if observed is _MISSING:
        return False
    if operator == "eq":
        return type(observed) is type(expected) and observed == expected
    if operator == "in":
        return isinstance(expected, list) and observed in expected
    if operator == "regex":
        return isinstance(observed, str) and re.fullmatch(
            str(expected),
            observed,
        ) is not None
    if operator == "length_eq":
        return isinstance(observed, (dict, list, str)) and len(observed) == expected
    if operator == "object_keys_eq":
        return (
            isinstance(observed, dict)
            and isinstance(expected, list)
            and all(isinstance(item, str) for item in expected)
            and sorted(observed) == sorted(expected)
        )
    if operator == "array_object_field_values_eq":
        if (
            not isinstance(observed, list)
            or not isinstance(expected, dict)
            or not isinstance(expected.get("field"), str)
            or not isinstance(expected.get("values"), list)
        ):
            return False
        field = expected["field"]
        values = expected["values"]
        return (
            all(isinstance(item, dict) and field in item for item in observed)
            and [item[field] for item in observed] == values
        )
    if operator == "array_objects_by_key_fields_eq":
        if (
            not isinstance(observed, list)
            or not isinstance(expected, dict)
            or not isinstance(expected.get("key_field"), str)
            or not isinstance(expected.get("items"), dict)
        ):
            return False
        key_field = expected["key_field"]
        expected_items = expected["items"]
        indexed = {}
        for item in observed:
            if (
                not isinstance(item, dict)
                or not isinstance(item.get(key_field), str)
                or item[key_field] in indexed
            ):
                return False
            indexed[item[key_field]] = item
        if set(indexed) != set(expected_items):
            return False
        for key, expected_fields in expected_items.items():
            if not isinstance(expected_fields, dict):
                return False
            actual = indexed[key]
            for field, expected_value in expected_fields.items():
                if not _evaluate_operator(
                    actual.get(field, _MISSING),
                    "eq",
                    expected_value,
                ):
                    return False
        return True
    if operator == "all_items_match":
        if (
            not isinstance(observed, list)
            or not observed
            or not isinstance(expected, dict)
            or not isinstance(expected.get("field_checks"), list)
        ):
            return False
        for item in observed:
            if not isinstance(item, dict):
                return False
            for check in expected["field_checks"]:
                if (
                    not isinstance(check, dict)
                    or not isinstance(check.get("field"), str)
                    or not isinstance(check.get("operator"), str)
                    or not _evaluate_operator(
                        item.get(check["field"], _MISSING),
                        check["operator"],
                        check.get("expected"),
                    )
                ):
                    return False
        return True
    if operator == "all_values_match":
        if (
            not isinstance(observed, dict)
            or not observed
            or not isinstance(expected, dict)
        ):
            return False
        field_checks = expected.get("field_checks", [])
        sum_checks = expected.get("sum_checks", [])
        equal_field_checks = expected.get("equal_field_checks", [])
        if (
            not isinstance(field_checks, list)
            or not isinstance(sum_checks, list)
            or not isinstance(equal_field_checks, list)
        ):
            return False
        for item in observed.values():
            if not isinstance(item, dict):
                return False
            for check in field_checks:
                if (
                    not isinstance(check, dict)
                    or not isinstance(check.get("field"), str)
                    or not isinstance(check.get("operator"), str)
                    or not _evaluate_operator(
                        item.get(check["field"], _MISSING),
                        check["operator"],
                        check.get("expected"),
                    )
                ):
                    return False
            for check in sum_checks:
                if (
                    not isinstance(check, dict)
                    or not isinstance(check.get("total_field"), str)
                    or not isinstance(check.get("part_fields"), list)
                    or not check["part_fields"]
                    or not all(
                        isinstance(field, str)
                        for field in check["part_fields"]
                    )
                ):
                    return False
                total = item.get(check["total_field"], _MISSING)
                parts = [
                    item.get(field, _MISSING)
                    for field in check["part_fields"]
                ]
                if (
                    not isinstance(total, int)
                    or isinstance(total, bool)
                    or any(
                        not isinstance(value, int)
                        or isinstance(value, bool)
                        for value in parts
                    )
                    or sum(parts) != total
                ):
                    return False
            for check in equal_field_checks:
                if (
                    not isinstance(check, list)
                    or len(check) != 2
                    or not all(isinstance(field, str) for field in check)
                    or item.get(check[0], _MISSING)
                    != item.get(check[1], _MISSING)
                    or item.get(check[0], _MISSING) is _MISSING
                ):
                    return False
        return True
    if operator == "lte":
        return (
            isinstance(observed, (int, float))
            and not isinstance(observed, bool)
            and isinstance(expected, (int, float))
            and not isinstance(expected, bool)
            and observed <= expected
        )
    if operator == "gte":
        return (
            isinstance(observed, (int, float))
            and not isinstance(observed, bool)
            and isinstance(expected, (int, float))
            and not isinstance(expected, bool)
            and observed >= expected
        )
    raise StageGateError(f"验收合同使用未知操作符：{operator}")


def _artifact_path(evidence_dir: Path, relative: str) -> Path:
    if not isinstance(relative, str):
        raise StageGateError("证据路径必须为字符串")
    relative_path = Path(relative)
    if relative_path.is_absolute() or ".." in relative_path.parts:
        raise StageGateError(f"证据路径必须位于证据目录内：{relative!r}")
    target = evidence_dir / relative_path
    try:
        target.resolve(strict=False).relative_to(evidence_dir.resolve())
    except ValueError as exc:
        raise StageGateError(f"证据路径越过证据目录：{relative!r}") from exc
    return target


def _check_artifacts(
    artifact_specs: Sequence[Mapping[str, Any]],
    evidence_dir: Path,
    artifacts: Dict[str, Mapping[str, Any]],
    artifact_hashes: Dict[str, str],
    check_results: List[Mapping[str, Any]],
    deviations: List[Mapping[str, Any]],
) -> None:
    for artifact_spec in artifact_specs:
        relative = artifact_spec.get("path")
        if not isinstance(relative, str):
            raise StageGateError("验收合同 artifact.path 必须为字符串")
        try:
            value, digest = _read_json(
                _artifact_path(evidence_dir, relative),
                f"阶段证据 {relative} ",
            )
        except StageGateError as exc:
            deviations.append(
                {
                    "check_id": f"ARTIFACT-{relative}",
                    "message": str(exc),
                }
            )
            continue
        artifacts[relative] = value
        artifact_hashes[relative] = digest
        checks = artifact_spec.get("checks")
        if not isinstance(checks, list):
            raise StageGateError(f"证据 {relative} checks 必须为数组")
        for check in checks:
            check_id = check.get("id")
            pointer = check.get("pointer")
            operator = check.get("operator")
            expected = check.get("expected")
            if not all(isinstance(item, str) for item in (check_id, pointer, operator)):
                raise StageGateError(f"证据 {relative} 存在无效检查定义")
            observed = _json_pointer(value, pointer)
            passed = _evaluate_operator(observed, operator, expected)
            result = {
                "check_id": check_id,
                "artifact": relative,
                "pointer": pointer,
                "operator": operator,
                "observed": _display(observed),
                "expected": expected,
                "status": "pass" if passed else "fail",
            }
            check_results.append(result)
            if not passed:
                deviations.append(
                    {
                        "check_id": check_id,
                        "message": (
                            f"{relative}{pointer} 不符合最终验收合同："
                            f"observed={_display(observed)!r} "
                            f"operator={operator} expected={expected!r}"
                        ),
                    }
                )


def _check_cross_artifacts(
    cross_checks: Sequence[Mapping[str, Any]],
    artifacts: Mapping[str, Mapping[str, Any]],
    check_results: List[Mapping[str, Any]],
    deviations: List[Mapping[str, Any]],
) -> None:
    for check in cross_checks:
        check_id = check.get("id")
        left_name = check.get("left_artifact")
        right_name = check.get("right_artifact")
        left_pointer = check.get("left_pointer")
        right_pointer = check.get("right_pointer")
        operator = check.get("operator")
        if not all(
            isinstance(item, str)
            for item in (
                check_id,
                left_name,
                right_name,
                left_pointer,
                right_pointer,
                operator,
            )
        ):
            raise StageGateError("验收合同存在无效跨证据检查")
        left_value = _json_pointer(artifacts.get(left_name, {}), left_pointer)
        right_value = _json_pointer(artifacts.get(right_name, {}), right_pointer)
        passed = (
            left_value is not _MISSING
            and right_value is not _MISSING
            and _evaluate_operator(left_value, operator, right_value)
        )
        check_results.append(
            {
                "check_id": check_id,
                "left_artifact": left_name,
                "left_pointer": left_pointer,
                "right_artifact": right_name,
                "right_pointer": right_pointer,
                "operator": operator,
                "observed_left": _display(left_value),
                "observed_right": _display(right_value),
                "status": "pass" if passed else "fail",
            }
        )
        if not passed:
            deviations.append(
                {
                    "check_id": check_id,
                    "message": (
                        f"跨证据身份或语义不一致："
                        f"{left_name}{left_pointer}={_display(left_value)!r}, "
                        f"{right_name}{right_pointer}={_display(right_value)!r}"
                    ),
                }
            )


def _check_previous_receipt(
    previous_receipt: Optional[Path],
    predecessor: Optional[str],
    contract: Mapping[str, Any],
    contract_sha256: str,
    document_hashes: Mapping[str, str],
    subject: Mapping[str, Any],
    artifact_hashes: Mapping[str, str],
) -> Tuple[Optional[str], List[Mapping[str, Any]]]:
    deviations: List[Mapping[str, Any]] = []
    if predecessor is None:
        if previous_receipt is not None:
            deviations.append(
                {
                    "check_id": "PREVIOUS-UNEXPECTED",
                    "message": "S0 不接受前序回执；来源变化必须从 S0 重新建立链",
                }
            )
        return None, deviations
    if previous_receipt is None:
        deviations.append(
            {
                "check_id": "PREVIOUS-MISSING",
                "message": f"阶段缺少前序 {predecessor} 通过回执",
            }
        )
        return None, deviations
    try:
        receipt, receipt_sha = _read_json(previous_receipt, "前序阶段回执 ")
    except StageGateError as exc:
        deviations.append(
            {
                "check_id": "PREVIOUS-INVALID",
                "message": str(exc),
            }
        )
        return None, deviations

    expected_values = {
        "component": "static_info_stage_gate",
        "status": "pass",
        "stage_id": predecessor,
        "contract_id": contract["contract_id"],
        "contract_sha256": contract_sha256,
        "acceptance_document_sha256": document_hashes["acceptance_document"],
        "stage_plan_document_sha256": document_hashes["stage_plan_document"],
    }
    for key, expected in expected_values.items():
        if receipt.get(key) != expected:
            deviations.append(
                {
                    "check_id": f"PREVIOUS-{key.upper()}",
                    "message": (
                        f"前序回执 {key} 不匹配："
                        f"observed={receipt.get(key)!r} expected={expected!r}"
                    ),
                }
            )
    if receipt.get("deviation_count") != 0 or receipt.get("deviations") != []:
        deviations.append(
            {
                "check_id": "PREVIOUS-DEVIATIONS",
                "message": "前序回执自称通过，但仍包含偏离项",
            }
        )
    previous_requirements = receipt.get("requirements")
    requirements_invalid = not isinstance(previous_requirements, list)
    if isinstance(previous_requirements, list):
        predecessor_match = re.fullmatch(r"S([0-9]+)", predecessor)
        predecessor_number = (
            int(predecessor_match.group(1)) if predecessor_match else -1
        )
        seen_requirement_ids = set()
        for item in previous_requirements:
            if not isinstance(item, dict):
                requirements_invalid = True
                break
            requirement_id = item.get("requirement_id")
            if (
                requirement_id in seen_requirement_ids
                or requirement_id
                not in {entry["id"] for entry in contract["requirements"]}
            ):
                requirements_invalid = True
                break
            seen_requirement_ids.add(requirement_id)
            due_match = re.fullmatch(r"S([0-9]+)", str(item.get("due_stage")))
            if due_match is None:
                requirements_invalid = True
                break
            due_number = int(due_match.group(1))
            expected_status = "pass" if due_number <= predecessor_number else "not_due"
            if item.get("status") != expected_status:
                requirements_invalid = True
                break
        if seen_requirement_ids != {
            entry["id"] for entry in contract["requirements"]
        }:
            requirements_invalid = True
    if requirements_invalid:
        deviations.append(
            {
                "check_id": "PREVIOUS-REQUIREMENTS",
                "message": "前序回执的到期最终要求并非全部通过",
            }
        )
    previous_artifact_hashes = receipt.get("artifact_sha256")
    if not isinstance(previous_artifact_hashes, dict):
        deviations.append(
            {
                "check_id": "PREVIOUS-ARTIFACT-HASHES",
                "message": "前序回执缺少证据文件哈希",
            }
        )
    else:
        for relative, current_sha in artifact_hashes.items():
            if (
                relative in previous_artifact_hashes
                and previous_artifact_hashes[relative] != current_sha
            ):
                deviations.append(
                    {
                        "check_id": f"PREVIOUS-ARTIFACT-{relative}",
                        "message": (
                            f"前序通过后证据被替换：{relative} "
                            f"previous={previous_artifact_hashes[relative]!r} "
                            f"current={current_sha!r}"
                        ),
                    }
                )
    previous_subject = receipt.get("subject")
    for key in ("content_id", "manifest_sha256"):
        current_value = subject.get(key)
        previous_value = (
            previous_subject.get(key)
            if isinstance(previous_subject, dict)
            else None
        )
        if not current_value or current_value != previous_value:
            deviations.append(
                {
                    "check_id": f"PREVIOUS-SUBJECT-{key.upper()}",
                    "message": (
                        f"前序回执与当前证据的 {key} 不一致："
                        f"previous={previous_value!r} current={current_value!r}；"
                        "来源变化必须从 S0 重启"
                    ),
                }
            )
    return receipt_sha, deviations


def run_stage_gate(
    stage_id: str,
    evidence_dir: os.PathLike[str] | str,
    output: os.PathLike[str] | str,
    *,
    previous_receipt: Optional[os.PathLike[str] | str] = None,
    repository_root: Path = REPOSITORY_ROOT,
    contract_path: Optional[Path] = None,
) -> Mapping[str, Any]:
    """执行阶段结束门禁并排他写入通过或偏差回执。"""

    root = Path(repository_root)
    contract_source = contract_path or (
        root
        / "contracts"
        / "info"
        / "static-info-final-acceptance-v1.json"
    )
    contract, contract_sha256 = _read_json(contract_source, "最终验收机器合同 ")
    stages, stage_index, document_hashes = _validate_contract(contract, root)
    if stage_id not in stage_index:
        raise StageGateError(f"未知阶段：{stage_id}")
    stage = stages[stage_index[stage_id]]

    evidence_root = Path(evidence_dir)
    if evidence_root.is_symlink() or not evidence_root.is_dir():
        raise StageGateError(f"证据目录必须是实际目录且禁止软链接：{evidence_root}")
    output_path = Path(output)
    if output_path.parent.resolve() != evidence_root.resolve():
        raise StageGateError("阶段回执必须直接写在当前证据目录内")

    artifacts: Dict[str, Mapping[str, Any]] = {}
    artifact_hashes: Dict[str, str] = {}
    check_results: List[Mapping[str, Any]] = []
    deviations: List[Mapping[str, Any]] = []
    common_specs = contract.get("common_artifacts")
    stage_specs = stage.get("artifacts")
    if not isinstance(common_specs, list) or not isinstance(stage_specs, list):
        raise StageGateError("验收合同 common_artifacts/stage artifacts 无效")
    _check_artifacts(
        [*common_specs, *stage_specs],
        evidence_root,
        artifacts,
        artifact_hashes,
        check_results,
        deviations,
    )
    cross_checks = stage.get("cross_checks")
    if not isinstance(cross_checks, list):
        raise StageGateError(f"阶段 {stage_id} cross_checks 必须为数组")
    _check_cross_artifacts(
        cross_checks,
        artifacts,
        check_results,
        deviations,
    )

    manifest = artifacts.get("static-info-manifest.json", {})
    subject = {
        "content_id": manifest.get("content_id"),
        "manifest_sha256": manifest.get("manifest_sha256"),
    }
    receipt_sha, receipt_deviations = _check_previous_receipt(
        Path(previous_receipt) if previous_receipt is not None else None,
        stage.get("predecessor"),
        contract,
        contract_sha256,
        document_hashes,
        subject,
        artifact_hashes,
    )
    deviations.extend(receipt_deviations)

    requirements = []
    current_index = stage_index[stage_id]
    newly_due = set(stage["newly_due_requirements"])
    for requirement in contract["requirements"]:
        due_index = stage_index[requirement["due_stage"]]
        if due_index > current_index:
            status = "not_due"
        elif deviations and (
            requirement["id"] in newly_due
            or receipt_deviations
            or any(
                item["check_id"].startswith("COMMON-")
                or item["check_id"].startswith("ARTIFACT-static-info-manifest")
                for item in deviations
            )
        ):
            status = "fail"
        else:
            status = "pass"
        requirements.append(
            {
                "requirement_id": requirement["id"],
                "title": requirement["title"],
                "due_stage": requirement["due_stage"],
                "status": status,
            }
        )

    report = {
        "schema_version": 1,
        "component": "static_info_stage_gate",
        "contract_id": contract["contract_id"],
        "contract_sha256": contract_sha256,
        "acceptance_document_sha256": document_hashes["acceptance_document"],
        "stage_plan_document_sha256": document_hashes["stage_plan_document"],
        "stage_id": stage_id,
        "stage_name": stage["name"],
        "status": "pass" if not deviations else "fail",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "subject": subject,
        "previous_receipt_sha256": receipt_sha,
        "artifact_sha256": dict(sorted(artifact_hashes.items())),
        "requirements": requirements,
        "checks": check_results,
        "deviation_count": len(deviations),
        "deviations": deviations,
    }
    write_text_exclusive(
        output_path,
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
    )
    return report


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="按最终验收合同检查 static INFO 阶段是否偏离",
    )
    parser.add_argument("--stage", required=True, choices=[f"S{i}" for i in range(7)])
    parser.add_argument("--evidence-dir", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--previous-receipt")
    parser.add_argument(
        "--repository-root",
        help="用于离线复核已归档合同与文档的根目录；默认使用当前仓库",
    )
    parser.add_argument(
        "--contract",
        help="最终验收机器合同路径；默认使用 repository-root 下的固定合同",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        report = run_stage_gate(
            args.stage,
            args.evidence_dir,
            args.output,
            previous_receipt=args.previous_receipt,
            repository_root=(
                Path(args.repository_root)
                if args.repository_root is not None
                else REPOSITORY_ROOT
            ),
            contract_path=(
                Path(args.contract) if args.contract is not None else None
            ),
        )
    except (OSError, StageGateError) as exc:
        sys.stderr.write(f"阶段结束 Hook 错误：{exc}\n")
        return 2
    if report["status"] != "pass":
        for deviation in report["deviations"]:
            sys.stderr.write(
                f"偏离 {deviation['check_id']}：{deviation['message']}\n"
            )
        return 1
    sys.stdout.write(
        f"static INFO 阶段 {report['stage_id']} 未偏离最终验收合同；"
        f"回执：{args.output}\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
