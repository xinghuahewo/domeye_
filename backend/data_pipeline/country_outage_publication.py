"""国家中断观测制品的原子发布指针。

该模块不解析 BGP、不修改旧事实表，也不写 RouteState。它只在一个不可变查询
制品已经完整落盘后，把该制品加入注册表，并以同目录 ``fsync + os.replace``
原子推进活动 publication。
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import tempfile
from datetime import datetime, timedelta
from typing import Any, Mapping

from services.country_outage_registry import (
    SUPPORTED_SCHEMA_VERSION,
    _read_registry,
    country_outage_publication,
)
from services.event_story_service import inspect_country_outage_package


PUBLICATION_KINDS = {"append", "correction", "status"}
REQUIRED_PUBLICATION_FIELDS = {
    "package_uri",
    "revision",
    "publication_state",
    "observation_state",
    "data_mode",
    "data_through",
    "updated_at",
    "is_final",
}
PUBLICATION_COPY_FIELDS = {
    "package_uri",
    "revision",
    "publication_state",
    "observation_state",
    "data_mode",
    "data_through",
    "updated_at",
    "is_final",
    "run_id",
    "artifact_set_id",
    "route_state_row_count",
    "resource_source",
    "capabilities",
    "collector_ids",
    "vantage_point_count",
    "interval_seconds",
    "processing_status",
    "missing_slots",
    "supersedes_publication_id",
    "correction_reason",
    "publication_kind",
}


class CountryOutagePublicationError(RuntimeError):
    """候选 publication 不能安全发布。"""


def _load_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CountryOutagePublicationError(f"无法读取{label}") from error
    if not isinstance(payload, Mapping):
        raise CountryOutagePublicationError(f"{label}必须是 JSON 对象")
    return dict(payload)


def _package_path(package_uri: str) -> Path:
    value = package_uri[7:] if package_uri.startswith("file://") else package_uri
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise CountryOutagePublicationError("package_uri 必须是绝对路径")
    if not path.is_dir() or not (path / "COMPLETE.json").is_file():
        raise CountryOutagePublicationError(
            "候选制品目录不存在或没有 COMPLETE.json"
        )
    return path


def _timestamp(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise CountryOutagePublicationError(f"{label} 必须是非空 ISO 时间")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise CountryOutagePublicationError(f"{label} 不是有效 ISO 时间") from error
    if parsed.tzinfo is None:
        raise CountryOutagePublicationError(f"{label} 必须包含时区")
    return parsed


def _inspect_package(path: Path) -> dict[str, Any]:
    try:
        return inspect_country_outage_package(path)
    except Exception as error:
        raise CountryOutagePublicationError(
            f"候选制品未通过查询消费门：{error}"
        ) from error


def _publication_id(
    incident_id: str,
    publication: Mapping[str, Any],
) -> str:
    explicit = publication.get("publication_id")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()
    identity = "|".join(
        (
            incident_id,
            str(publication.get("revision")),
            str(publication.get("data_through")),
            str(publication.get("package_uri")),
            str(publication.get("updated_at")),
            json.dumps(
                publication.get("processing_status"),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
        )
    )
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
    return f"publication_v1_{digest}"


def _single_publication(
    registration: Mapping[str, Any],
) -> dict[str, Any]:
    effective = country_outage_publication(registration)
    return {
        "publication_id": effective["publication_id"],
        **{
            key: effective[key]
            for key in PUBLICATION_COPY_FIELDS
            if key in effective
        },
    }


def _validate_candidate(
    incident_id: str,
    current: Mapping[str, Any],
    candidate: Mapping[str, Any],
    *,
    kind: str,
) -> dict[str, Any]:
    if kind not in PUBLICATION_KINDS:
        raise CountryOutagePublicationError(
            "发布类型只允许 append、status 或 correction"
        )
    publication = dict(candidate)
    for key in PUBLICATION_COPY_FIELDS - {
        "processing_state",
        "processing_status",
        "supersedes_publication_id",
        "correction_reason",
    }:
        if key not in publication and key in current:
            publication[key] = current[key]
    publication["publication_kind"] = kind
    missing = sorted(REQUIRED_PUBLICATION_FIELDS - set(publication))
    if missing:
        raise CountryOutagePublicationError(
            "候选 publication 缺少字段：" + "、".join(missing)
        )
    if publication.get("publication_state") != "published":
        raise CountryOutagePublicationError("活动 publication 必须已经 published")
    if not isinstance(publication.get("revision"), int) or isinstance(
        publication.get("revision"), bool
    ):
        raise CountryOutagePublicationError("revision 必须是正整数")
    candidate_data_through = _timestamp(
        publication.get("data_through"),
        "data_through",
    )
    _timestamp(publication.get("updated_at"), "updated_at")
    if not isinstance(publication.get("is_final"), bool):
        raise CountryOutagePublicationError("is_final 必须是布尔值")
    package_uri = publication.get("package_uri")
    if not isinstance(package_uri, str):
        raise CountryOutagePublicationError("package_uri 必须是字符串")
    package = _inspect_package(_package_path(package_uri))
    if package.get("incident_id") != incident_id:
        raise CountryOutagePublicationError("候选制品 incident_id 与注册事件不一致")
    if package.get("last_observation_at") != publication.get("data_through"):
        raise CountryOutagePublicationError(
            "data_through 必须等于制品最后完整观测时间"
        )
    if kind != "status" and package_uri == current.get("package_uri"):
        raise CountryOutagePublicationError("新 publication 不得复用当前制品目录")

    current_revision = int(current.get("revision") or 1)
    current_data_through = current.get("data_through")
    if kind in {"append", "correction"}:
        interval_seconds = publication.get(
            "interval_seconds",
            current.get("interval_seconds"),
        )
        if (
            not isinstance(interval_seconds, int)
            or isinstance(interval_seconds, bool)
            or interval_seconds <= 0
        ):
            raise CountryOutagePublicationError(
                "有缺槽语义的 publication 必须提供 interval_seconds"
            )
        candidate_slots = package.get("slot_fingerprints")
        if not isinstance(candidate_slots, list) or not candidate_slots:
            raise CountryOutagePublicationError("候选制品缺少槽位指纹")
        try:
            observed_times = [
                _timestamp(slot["observed_at"], "状态槽时间")
                for slot in candidate_slots
            ]
        except (KeyError, TypeError) as error:
            raise CountryOutagePublicationError("候选槽位指纹无效") from error
        expected_missing: set[str] = set()
        cursor = observed_times[0]
        observed_set = set(observed_times)
        while cursor <= observed_times[-1]:
            if cursor not in observed_set:
                expected_missing.add(
                    cursor.isoformat(timespec="seconds").replace(
                        "+00:00", "Z"
                    )
                )
            cursor += timedelta(seconds=interval_seconds)
        raw_missing = publication.get("missing_slots") or []
        if not isinstance(raw_missing, list):
            raise CountryOutagePublicationError("missing_slots 必须是数组")
        declared_missing = {
            item.get("observed_at"): item
            for item in raw_missing
            if isinstance(item, Mapping)
        }
        if set(declared_missing) != expected_missing:
            raise CountryOutagePublicationError(
                "候选制品的全部缺槽必须被完整、唯一声明"
            )
        if any(
            item.get("slot_state")
            not in {"source_unavailable", "not_observed"}
            or not isinstance(item.get("missing_reason"), str)
            or not item.get("missing_reason")
            for item in declared_missing.values()
        ):
            raise CountryOutagePublicationError(
                "已推进 data_through 的缺槽只能是已确认源缺失"
            )

    if kind == "append":
        current_package_uri = current.get("package_uri")
        if not isinstance(current_package_uri, str):
            raise CountryOutagePublicationError(
                "当前 publication 没有可核验的 package_uri"
            )
        current_package = _inspect_package(
            _package_path(current_package_uri)
        )
        if current_package.get("last_observation_at") != current_data_through:
            raise CountryOutagePublicationError(
                "当前 publication 的 data_through 与制品不一致"
            )
        if (
            current_package.get("cohort_sha256")
            != package.get("cohort_sha256")
        ):
            raise CountryOutagePublicationError(
                "正常追加不得改变固定 cohort"
            )
        current_slots = current_package.get("slot_fingerprints")
        candidate_slots = package.get("slot_fingerprints")
        if (
            not isinstance(current_slots, list)
            or not isinstance(candidate_slots, list)
            or candidate_slots[: len(current_slots)] != current_slots
            or len(candidate_slots) <= len(current_slots)
        ):
            raise CountryOutagePublicationError(
                "正常追加必须保持全部已发布槽位不变"
            )
        current_missing = current.get("missing_slots") or []
        current_cutoff = _timestamp(
            str(current_data_through),
            "当前 data_through",
        )
        candidate_historical_missing = [
            item
            for item in publication.get("missing_slots") or []
            if _timestamp(item.get("observed_at"), "缺槽时间")
            <= current_cutoff
        ]
        if candidate_historical_missing != current_missing:
            raise CountryOutagePublicationError(
                "正常追加不得改变已发布缺槽"
            )
        if publication["revision"] != current_revision:
            raise CountryOutagePublicationError("正常追加必须保持 revision 不变")
        if (
            not isinstance(current_data_through, str)
            or candidate_data_through
            <= _timestamp(current_data_through, "当前 data_through")
        ):
            raise CountryOutagePublicationError(
                "正常追加必须推进连续 data_through"
            )
        if publication.get("supersedes_publication_id") is not None:
            raise CountryOutagePublicationError(
                "正常追加不得声明历史修正替代关系"
            )
        if publication.get("correction_reason") is not None:
            raise CountryOutagePublicationError(
                "正常追加不得携带 correction_reason"
            )
    elif kind == "correction":
        if publication["revision"] != current_revision + 1:
            raise CountryOutagePublicationError(
                "历史补正必须把 revision 精确增加 1"
            )
        if (
            publication.get("supersedes_publication_id")
            != current.get("publication_id")
        ):
            raise CountryOutagePublicationError(
                "历史补正必须显式替代当前 publication"
            )
        reason = publication.get("correction_reason")
        if not isinstance(reason, str) or not reason.strip():
            raise CountryOutagePublicationError(
                "历史补正必须说明 correction_reason"
            )
    else:
        if package_uri != current.get("package_uri"):
            raise CountryOutagePublicationError(
                "状态发布不得更换查询制品"
            )
        if publication["revision"] != current_revision:
            raise CountryOutagePublicationError(
                "状态发布不得改变 revision"
            )
        if publication["data_through"] != current_data_through:
            raise CountryOutagePublicationError(
                "状态发布不得推进 data_through"
            )
        status = publication.get("processing_status")
        if (
            not isinstance(status, Mapping)
            or status.get("state")
            not in {"processing", "waiting_for_source", "failed"}
        ):
            raise CountryOutagePublicationError(
                "状态发布必须携带处理中、等待源或失败状态"
            )
        if status.get("state") in {
            "waiting_for_source",
            "failed",
        } and (
            not isinstance(status.get("reason"), str)
            or not status.get("reason")
        ):
            raise CountryOutagePublicationError(
                "等待源或失败状态必须说明原因"
            )
        if publication.get("supersedes_publication_id") is not None:
            raise CountryOutagePublicationError(
                "状态发布不得声明历史补正替代关系"
            )

    publication["publication_id"] = _publication_id(
        incident_id,
        publication,
    )
    return publication


def build_registry_update(
    registry: Mapping[str, Any],
    *,
    incident_id: str,
    publication: Mapping[str, Any],
    kind: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """构建但不落盘的注册表更新，便于门禁和单元测试。"""

    if (
        registry.get("schema_version") != SUPPORTED_SCHEMA_VERSION
        or not isinstance(registry.get("observations"), list)
    ):
        raise CountryOutagePublicationError("注册表合同无效")
    updated = json.loads(json.dumps(registry, ensure_ascii=False))
    registration = next(
        (
            item
            for item in updated["observations"]
            if isinstance(item, dict) and item.get("incident_id") == incident_id
        ),
        None,
    )
    if registration is None:
        raise CountryOutagePublicationError("注册表中不存在该 incident_id")

    current = country_outage_publication(registration)
    candidate = _validate_candidate(
        incident_id,
        current,
        publication,
        kind=kind,
    )
    publications = registration.get("publications")
    if not isinstance(publications, list):
        publications = [_single_publication(registration)]
    if any(
        item.get("publication_id") == candidate["publication_id"]
        for item in publications
        if isinstance(item, Mapping)
    ):
        raise CountryOutagePublicationError("publication_id 已存在")

    registration["publications"] = [*publications, candidate]
    registration["current_publication_id"] = candidate["publication_id"]
    return updated, candidate


def _atomic_write_registry(path: Path, payload: Mapping[str, Any]) -> None:
    encoded = (
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        _read_registry.cache_clear()
        _read_registry(str(temporary), temporary.stat().st_mtime_ns)
        os.replace(temporary, path)
        directory_descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        temporary.unlink(missing_ok=True)
        _read_registry.cache_clear()


def publish_country_outage(
    registry_path: Path,
    *,
    incident_id: str,
    publication: Mapping[str, Any],
    kind: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    """在单写者锁内校验并原子推进活动 publication。"""

    path = registry_path.expanduser().resolve()
    if not path.is_file():
        raise CountryOutagePublicationError("注册表不存在")
    lock_path = path.with_name(f".{path.name}.lock")
    with lock_path.open("a+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        registry = _load_json_object(path, "国家中断注册表")
        updated, candidate = build_registry_update(
            registry,
            incident_id=incident_id,
            publication=publication,
            kind=kind,
        )
        if not dry_run:
            _atomic_write_registry(path, updated)
    return {
        "status": "validated" if dry_run else "published",
        "kind": kind,
        "incident_id": incident_id,
        "publication_id": candidate["publication_id"],
        "revision": candidate["revision"],
        "data_through": candidate["data_through"],
        "processing_state": (
            candidate.get("processing_status") or {}
        ).get("state"),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="原子发布国家中断观测查询制品",
    )
    parser.add_argument("--registry", required=True, type=Path)
    parser.add_argument("--incident-id", required=True)
    parser.add_argument("--publication-json", required=True, type=Path)
    parser.add_argument(
        "--kind",
        required=True,
        choices=sorted(PUBLICATION_KINDS),
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        publication = _load_json_object(
            args.publication_json,
            "候选 publication",
        )
        result = publish_country_outage(
            args.registry,
            incident_id=args.incident_id,
            publication=publication,
            kind=args.kind,
            dry_run=args.dry_run,
        )
    except CountryOutagePublicationError as error:
        print(json.dumps({"status": "failed", "reason": str(error)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "CountryOutagePublicationError",
    "build_registry_update",
    "publish_country_outage",
]
