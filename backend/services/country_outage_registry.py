"""国家中断观测发布注册表。

注册表是事件引用、稳定 incident ID 与只读发布制品之间的唯一映射。新增国家
事件只需要发布标准制品并增加注册记录，不需要修改 Python 代码。
"""

from __future__ import annotations

from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping


REGISTRY_ENV = "DOMEYE_COUNTRY_OUTAGE_REGISTRY"
SUPPORTED_SCHEMA_VERSION = "country_outage_observation_registry_v1"
OBSERVATION_STATES = {
    "legacy_summary",
    "aggregate_available",
    "state_partial",
    "state_complete",
    "evidence_complete",
}
CAPABILITY_STATES = {
    "available",
    "building",
    "unavailable",
    "not_applicable",
}
DATA_MODES = {"legacy", "replay", "live", "mixed"}
PROCESSING_STATES = {
    "idle",
    "processing",
    "waiting_for_source",
    "failed",
    "final",
}
MISSING_SLOT_STATES = {
    "source_unavailable",
    "processing_gap",
    "parse_failed",
    "not_observed",
}
IMMUTABLE_IDENTITY_FIELDS = {
    "incident_id",
    "legacy_reference",
    "country",
}


class CountryOutageRegistryError(RuntimeError):
    """注册表不存在、损坏或包含不安全配置。"""


class CountryOutagePublicationNotFound(LookupError):
    """请求的不可变 publication 不存在或尚未发布。"""


def _validate_operational_fields(
    value: Mapping[str, Any],
    *,
    label: str,
) -> None:
    processing = value.get("processing_status")
    if processing is not None:
        if (
            not isinstance(processing, Mapping)
            or processing.get("state") not in PROCESSING_STATES
        ):
            raise CountryOutageRegistryError(f"{label} processing_status 无效")
        if processing.get("state") in {
            "waiting_for_source",
            "failed",
        } and not isinstance(processing.get("reason"), str):
            raise CountryOutageRegistryError(
                f"{label} processing_status 缺少原因"
            )
    missing_slots = value.get("missing_slots")
    if missing_slots is not None:
        if not isinstance(missing_slots, list):
            raise CountryOutageRegistryError(f"{label} missing_slots 无效")
        identities: set[str] = set()
        for missing in missing_slots:
            if (
                not isinstance(missing, Mapping)
                or not isinstance(missing.get("observed_at"), str)
                or missing.get("observed_at") in identities
                or missing.get("slot_state") not in MISSING_SLOT_STATES
                or not isinstance(missing.get("missing_reason"), str)
                or not missing.get("missing_reason")
            ):
                raise CountryOutageRegistryError(
                    f"{label} missing_slots 条目无效"
                )
            identities.add(str(missing["observed_at"]))


def _registry_path() -> Path | None:
    value = os.environ.get(REGISTRY_ENV)
    if not value:
        return None
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise CountryOutageRegistryError(
            f"{REGISTRY_ENV} 必须指向绝对路径"
        )
    return path


@lru_cache(maxsize=4)
def _read_registry(path_text: str, modified_ns: int) -> tuple[dict[str, Any], ...]:
    del modified_ns
    path = Path(path_text)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CountryOutageRegistryError("无法读取国家中断观测注册表") from error
    if (
        not isinstance(payload, Mapping)
        or payload.get("schema_version") != SUPPORTED_SCHEMA_VERSION
        or not isinstance(payload.get("observations"), list)
    ):
        raise CountryOutageRegistryError("国家中断观测注册表合同无效")

    observations: list[dict[str, Any]] = []
    incident_ids: set[str] = set()
    references: set[str] = set()
    for ordinal, raw in enumerate(payload["observations"], start=1):
        if not isinstance(raw, Mapping):
            raise CountryOutageRegistryError(
                f"注册表第 {ordinal} 条观测不是对象"
            )
        item = dict(raw)
        incident_id = item.get("incident_id")
        legacy_reference = item.get("legacy_reference")
        country = item.get("country")
        if (
            not isinstance(incident_id, str)
            or not incident_id
            or not isinstance(legacy_reference, str)
            or not legacy_reference
            or not isinstance(country, Mapping)
            or not isinstance(country.get("code"), str)
            or not isinstance(country.get("name"), str)
        ):
            raise CountryOutageRegistryError(
                f"注册表第 {ordinal} 条观测缺少身份或制品字段"
            )
        if incident_id in incident_ids or legacy_reference in references:
            raise CountryOutageRegistryError("注册表存在重复事件身份")
        incident_ids.add(incident_id)
        references.add(legacy_reference)

        publication_state = item.get("publication_state", "published")
        if publication_state not in {"draft", "published", "withdrawn"}:
            raise CountryOutageRegistryError(
                f"注册表第 {ordinal} 条观测发布状态无效"
            )
        observation_state = item.get("observation_state", "state_complete")
        if observation_state not in OBSERVATION_STATES:
            raise CountryOutageRegistryError(
                f"注册表第 {ordinal} 条观测完整度状态无效"
            )
        data_mode = item.get("data_mode", "replay")
        if data_mode not in DATA_MODES:
            raise CountryOutageRegistryError(
                f"注册表第 {ordinal} 条数据来源模式无效"
            )
        revision = item.get("revision", 1)
        if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
            raise CountryOutageRegistryError(
                f"注册表第 {ordinal} 条 revision 无效"
            )
        package_uri = item.get("package_uri")
        _validate_operational_fields(
            item,
            label=f"注册表第 {ordinal} 条观测",
        )
        publications = item.get("publications")
        if publications is not None:
            if not isinstance(publications, list) or not publications:
                raise CountryOutageRegistryError(
                    f"注册表第 {ordinal} 条 publications 无效"
                )
            publication_ids: set[str] = set()
            for publication_ordinal, raw_publication in enumerate(
                publications,
                start=1,
            ):
                if not isinstance(raw_publication, Mapping):
                    raise CountryOutageRegistryError(
                        f"注册表第 {ordinal} 条第 {publication_ordinal} 个 "
                        "publication 不是对象"
                    )
                publication = dict(raw_publication)
                _validate_operational_fields(
                    publication,
                    label=(
                        f"注册表第 {ordinal} 条第 "
                        f"{publication_ordinal} 个 publication"
                    ),
                )
                publication_id = publication.get("publication_id")
                if (
                    not isinstance(publication_id, str)
                    or not publication_id
                    or publication_id in publication_ids
                ):
                    raise CountryOutageRegistryError(
                        f"注册表第 {ordinal} 条 publication ID 无效或重复"
                    )
                if IMMUTABLE_IDENTITY_FIELDS & set(publication):
                    raise CountryOutageRegistryError(
                        f"注册表第 {ordinal} 条 publication 覆盖事件身份"
                    )
                publication_ids.add(publication_id)
                publication_revision = publication.get("revision")
                if (
                    not isinstance(publication_revision, int)
                    or isinstance(publication_revision, bool)
                    or publication_revision < 1
                ):
                    raise CountryOutageRegistryError(
                        f"注册表第 {ordinal} 条 publication revision 无效"
                    )
                if publication.get("data_mode") not in DATA_MODES:
                    raise CountryOutageRegistryError(
                        f"注册表第 {ordinal} 条 publication 来源模式无效"
                    )
                publication_observation_state = publication.get(
                    "observation_state",
                    observation_state,
                )
                if publication_observation_state not in OBSERVATION_STATES:
                    raise CountryOutageRegistryError(
                        f"注册表第 {ordinal} 条 publication 完整度状态无效"
                    )
                if publication.get(
                    "publication_state", "published"
                ) not in {"draft", "published", "withdrawn"}:
                    raise CountryOutageRegistryError(
                        f"注册表第 {ordinal} 条 publication 状态无效"
                    )
                publication_package = publication.get("package_uri")
                if publication_observation_state in {
                    "state_partial",
                    "state_complete",
                    "evidence_complete",
                } and (
                    not isinstance(publication_package, str)
                    or not publication_package
                ):
                    raise CountryOutageRegistryError(
                        f"注册表第 {ordinal} 条增强 publication 缺少 package_uri"
                    )
            current_publication_id = item.get("current_publication_id")
            if current_publication_id not in publication_ids:
                raise CountryOutageRegistryError(
                    f"注册表第 {ordinal} 条 current publication 不存在"
                )
            current_publication = next(
                publication
                for publication in publications
                if publication.get("publication_id")
                == current_publication_id
            )
            if (
                current_publication.get(
                    "publication_state", "published"
                )
                != "published"
            ):
                raise CountryOutageRegistryError(
                    f"注册表第 {ordinal} 条 current publication 未发布"
                )
        if observation_state in {
            "state_partial",
            "state_complete",
            "evidence_complete",
        } and publications is None and (
            not isinstance(package_uri, str) or not package_uri
        ):
            raise CountryOutageRegistryError(
                f"注册表第 {ordinal} 条增强观测缺少 package_uri"
            )
        capabilities = item.get("capabilities")
        if capabilities is not None:
            if not isinstance(capabilities, Mapping):
                raise CountryOutageRegistryError(
                    f"注册表第 {ordinal} 条 capabilities 不是对象"
                )
            for capability, value in capabilities.items():
                if (
                    not isinstance(capability, str)
                    or not isinstance(value, Mapping)
                    or value.get("state") not in CAPABILITY_STATES
                ):
                    raise CountryOutageRegistryError(
                        f"注册表第 {ordinal} 条 capability 状态无效"
                    )
        observations.append(item)
    return tuple(observations)


def list_country_outage_observations(
    *,
    published_only: bool = True,
) -> tuple[dict[str, Any], ...]:
    path = _registry_path()
    if path is None:
        return ()
    try:
        modified_ns = path.stat().st_mtime_ns
    except OSError as error:
        raise CountryOutageRegistryError("国家中断观测注册表不存在") from error
    observations = _read_registry(str(path), modified_ns)
    if not published_only:
        return observations
    return tuple(
        item
        for item in observations
        if item.get("publication_state", "published") == "published"
    )


def find_country_outage_by_reference(
    legacy_reference: str,
) -> dict[str, Any] | None:
    return next(
        (
            item
            for item in list_country_outage_observations()
            if item["legacy_reference"] == legacy_reference
        ),
        None,
    )


def find_country_outage_by_incident(
    incident_id: str,
) -> dict[str, Any] | None:
    return next(
        (
            item
            for item in list_country_outage_observations()
            if item["incident_id"] == incident_id
        ),
        None,
    )


def package_directory(registration: Mapping[str, Any]) -> Path:
    package_uri = registration.get("package_uri")
    if not isinstance(package_uri, str) or not package_uri:
        raise CountryOutageRegistryError("该观测没有增强状态制品")
    if package_uri.startswith("file://"):
        package_uri = package_uri[7:]
    path = Path(package_uri).expanduser()
    if not path.is_absolute():
        raise CountryOutageRegistryError("package_uri 必须是绝对文件路径")
    return path


def _derived_publication_id(registration: Mapping[str, Any]) -> str:
    identity = "|".join(
        (
            str(registration["incident_id"]),
            str(registration.get("revision") or 1),
            str(registration.get("data_through") or "no-data"),
            str(registration.get("package_uri") or "legacy"),
        )
    )
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
    return f"publication_v1_{digest}"


def country_outage_publication(
    registration: Mapping[str, Any],
    publication_id: str | None = None,
) -> dict[str, Any]:
    """返回固定 publication 的有效注册，不跨请求读取活动指针。"""

    publications = registration.get("publications")
    if not isinstance(publications, list):
        effective = dict(registration)
        effective["publication_id"] = str(
            registration.get("publication_id")
            or _derived_publication_id(registration)
        )
        effective["_publication_history"] = [
            {
                "publication_id": effective["publication_id"],
                "revision": int(effective.get("revision") or 1),
                "data_through": effective.get("data_through"),
                "updated_at": effective.get("updated_at"),
                "publication_state": effective.get(
                    "publication_state", "published"
                ),
                "supersedes_publication_id": effective.get(
                    "supersedes_publication_id"
                ),
                "correction_reason": effective.get("correction_reason"),
                "publication_kind": effective.get(
                    "publication_kind", "baseline"
                ),
                "processing_status": effective.get("processing_status"),
            }
        ]
        if publication_id not in (None, effective["publication_id"]):
            raise CountryOutagePublicationNotFound(publication_id)
        return effective

    selected_id = publication_id or str(
        registration.get("current_publication_id") or ""
    )
    selected = next(
        (
            dict(candidate)
            for candidate in publications
            if candidate.get("publication_id") == selected_id
            and candidate.get("publication_state", "published")
            == "published"
        ),
        None,
    )
    if selected is None:
        raise CountryOutagePublicationNotFound(selected_id)
    effective = {
        key: value
        for key, value in registration.items()
        if key not in {"publications", "current_publication_id"}
    }
    effective.update(selected)
    effective["publication_id"] = selected_id
    effective["_publication_history"] = [
        {
            key: candidate.get(key)
            for key in (
                "publication_id",
                "revision",
                "data_through",
                "updated_at",
                "publication_state",
                "supersedes_publication_id",
                "correction_reason",
                "publication_kind",
                "processing_status",
            )
        }
        for candidate in publications
    ]
    return effective
