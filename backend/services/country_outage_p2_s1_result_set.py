"""W5 ResultSet 页链装配、冻结、preview 与分页读取。

成员只来自同一 Tool 查询的原始返回页；Host 只验证页链、身份、排序、去重和摘要，
不补成员、不改变顺序，也不把 preview 提升为完整人口。
"""

from __future__ import annotations

import copy
from ipaddress import ip_network
from typing import Any, Mapping, Sequence

from .country_outage_p2_s1_contract_runtime import (
    W5ContractError,
    digest_hex,
    digest_without_fields,
    load_frozen_contract,
    validate_identity,
    validate_json_schema,
)
from .country_outage_p2_s1_tools import CountryOutageP2S1Tools
from .country_outage_p2_s1_trusted_store import ContentAddressedStore


_MEMBER_IDENTITY = {
    "TOOL-07": ("cohort_member_id",),
    "TOOL-08": ("state_point_utc", "afi", "prefix"),
    "TOOL-09": ("asn", "state_point_utc"),
    "TOOL-10": ("new_prefix_state_id",),
    "TOOL-11": ("state_point_utc", "route_observation_key"),
    "TOOL-12": ("path_association_id",),
}

# ResultSet 的公开 member_identity 必须引用成员 Schema 中一个实际且逐成员唯一的字段；
# 多字段 dedupe_key 仍保留用于完整人口去重。TOOL-11 的 state_point_utc 在同一
# exact-time 页内会重复，因此不能把它当作 preview/member lookup 的身份键。
_MEMBER_IDENTITY_NAME = {
    "TOOL-07": "cohort_member_id",
    "TOOL-08": "prefix",
    "TOOL-09": "asn",
    "TOOL-10": "new_prefix_state_id",
    "TOOL-11": "route_observation_key",
    "TOOL-12": "path_association_id",
}


def tool_output_population(tool_id: str) -> dict[str, Any]:
    """把冻结 Tool member $def 投影成 fragment-free、稳定绝对 $id 的人口 Schema。"""

    schema = strict_tool_schema()
    definition_name = f"{tool_id.lower().replace('-', '')}Member"
    if definition_name not in schema.get("$defs", {}):
        raise W5ResultSetError("tool_member_schema_missing", f"{tool_id} member Schema 未冻结")
    schema_ref = f"https://domeye.example/contracts/agent/country-outage-p2-s1/runtime/{tool_id.lower()}-member.schema.json"
    member_schema = {
        "$schema": schema["$schema"], "$id": schema_ref,
        "$ref": f"#/$defs/{definition_name}", "$defs": copy.deepcopy(schema["$defs"]),
    }
    # Host 技术排序投影字段不是业务事实；作为登记输出人口的一部分显式冻结。
    if tool_id in {"TOOL-07", "TOOL-08", "TOOL-10", "TOOL-11", "TOOL-12"}:
        definition = member_schema["$defs"][definition_name]
        definition["properties"]["network_address"] = {"type": "integer", "minimum": 0}
        definition["properties"]["prefix_length"] = {"type": "integer", "minimum": 0, "maximum": 128}
        definition["required"] = [*definition["required"], "network_address", "prefix_length"]
    population_id = {
        "TOOL-07": "fixed_cohort_member_rows", "TOOL-08": "prefix_state_rows",
        "TOOL-09": "as_state_rows", "TOOL-10": "new_prefix_state_rows",
        "TOOL-11": "materialized_route_state_rows_at_exact_time",
        "TOOL-12": "window_path_association_rows",
    }[tool_id]
    return {
        "population_id": population_id,
        "member_schema_ref": schema_ref,
        "member_schema": member_schema,
        "member_schema_digest": digest_without_fields(member_schema),
    }


class W5ResultSetError(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int = 409) -> None:
        self.code = code
        self.status_code = status_code
        self.retryable = False
        self.next_action = None
        super().__init__(message)


def _schema_for_definition(schema: Mapping[str, Any], name: str) -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$ref": f"#/$defs/{name}",
        "$defs": copy.deepcopy(schema["$defs"]),
    }


def _sort_specs(raw: Sequence[Any]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, str):
            raise W5ResultSetError("stable_sort_invalid", "Tool stable_sort 必须是字符串数组")
        parts = item.split()
        if len(parts) != 2 or parts[1] not in {"ASC", "DESC"}:
            raise W5ResultSetError("stable_sort_invalid", f"排序表达式无效：{item}")
        result.append({"field": parts[0], "direction": parts[1], "nulls": "FORBIDDEN"})
    return result


def _sort_value(member: Mapping[str, Any], field: str) -> Any:
    if field == "network_address":
        return int(ip_network(str(member["prefix"]), strict=False).network_address)
    if field == "prefix_length":
        return ip_network(str(member["prefix"]), strict=False).prefixlen
    if field not in member or member[field] is None:
        raise W5ResultSetError("stable_sort_member_field_missing", f"成员缺少冻结排序字段：{field}")
    return member[field]


def _project_sort_keys(member: Mapping[str, Any], stable_sort: Sequence[Mapping[str, str]]) -> dict[str, Any]:
    projected = copy.deepcopy(dict(member))
    if any(item["field"] in {"network_address", "prefix_length"} for item in stable_sort):
        network = ip_network(str(projected["prefix"]), strict=False)
        projected["network_address"] = int(network.network_address)
        projected["prefix_length"] = network.prefixlen
    return projected


def _sort_key(member: Mapping[str, Any], stable_sort: Sequence[Mapping[str, str]]) -> list[Any]:
    # 当前六个 Tool 的冻结排序全部 ASC；仍显式拒绝未来未实现的 DESC，避免错误反转。
    if any(item["direction"] != "ASC" for item in stable_sort):
        raise W5ResultSetError("stable_sort_direction_unsupported", "W5 v1 只接受已冻结 ASC Tool 排序")
    return [_sort_value(member, item["field"]) for item in stable_sort]


def _member_ref(member: Mapping[str, Any], fields: Sequence[str]) -> str:
    values = []
    for field in fields:
        if field not in member or member[field] is None:
            raise W5ResultSetError("member_identity_missing", f"成员缺少 identity 字段：{field}")
        values.append(member[field])
    return "member-sha256:" + digest_hex({"identity_fields": list(fields), "values": values})


def validate_result_set(result_set: Mapping[str, Any], store: ContentAddressedStore) -> list[dict[str, Any]]:
    required = {
        "schema_version", "result_set_id", "result_set_revision", "parent_result_set_revision",
        "state", "source_identity", "source_tool", "normalized_query", "query_digest",
        "stable_sort", "stable_sort_digest", "source_population_id", "source_population_schema_ref",
        "source_population_schema_digest", "source_dataset_digest", "member_identity", "dedupe_key",
        "page_manifest", "returned_count", "total_count", "set_completeness", "resume_page_token",
        "member_segments", "query_receipt_digest", "source_completeness_receipt_digest", "evidence_refs",
        "limitations", "manifest_digest", "content_digest", "freeze_receipt_digest", "preview_views",
        "generation_origin", "design_boundary",
    }
    if not isinstance(result_set, Mapping) or set(result_set) != required:
        raise W5ResultSetError("result_set_fields_invalid", "ResultSet 字段集合不闭合")
    if result_set.get("schema_version") != "country_outage_p2_result_set_v1" or result_set.get("state") != "frozen":
        raise W5ResultSetError("result_set_state_invalid", "ResultSet 必须是 W5 frozen")
    validate_identity(result_set["source_identity"])
    pages = result_set.get("page_manifest")
    segments = result_set.get("member_segments")
    if not isinstance(pages, list) or not isinstance(segments, list) or len(pages) != len(segments) or not pages:
        raise W5ResultSetError("result_set_page_chain_invalid", "page/segment 人口不闭合")
    receipts = {
        item.get("receipt_digest"): item
        for item in [*store.list_json("receipt"), *store.list_json("receipt-candidate")]
        if isinstance(item.get("receipt_digest"), str)
        and item.get("receipt_digest") == digest_without_fields(item, "receipt_digest")
    }
    query_receipt = receipts.get(result_set.get("query_receipt_digest"))
    if not isinstance(query_receipt, Mapping) or any(query_receipt.get(field) != expected for field, expected in {
        "receipt_kind": "query",
        "query_digest": result_set["query_digest"],
        "identity_digest": result_set["source_identity"]["identity_digest"],
        "source_population_id": result_set["source_population_id"],
        "source_population_schema_digest": result_set["source_population_schema_digest"],
        "source_dataset_digest": result_set["source_dataset_digest"],
        "total_count": result_set["total_count"],
        "disposition": "passed",
    }.items()):
        raise W5ResultSetError("result_set_query_receipt_invalid", "Host query receipt 未解析或绑定漂移")
    members: list[dict[str, Any]] = []
    expected_token: str | None = None
    seen_refs: set[str] = set()
    previous_key: list[Any] | None = None
    for index, (page, segment) in enumerate(zip(pages, segments)):
        if page.get("page_index") != index or segment.get("page_index") != index:
            raise W5ResultSetError("result_set_page_chain_invalid", "页号必须从 0 连续")
        if page.get("token_in") != expected_token or page.get("member_segment_ref") != segment.get("segment_ref"):
            raise W5ResultSetError("result_set_page_chain_invalid", "页 token 或 segment 链断裂")
        payload = store.get_json("result-set-segment", segment["segment_ref"])
        if not isinstance(payload, Mapping) or set(payload) != {"members"} or not isinstance(payload["members"], list):
            raise W5ResultSetError("result_set_segment_invalid", "成员 segment 无效")
        page_members = payload["members"]
        if segment.get("member_count") != len(page_members) or page.get("member_count") != len(page_members):
            raise W5ResultSetError("result_set_count_mismatch", "页成员计数不一致")
        if segment.get("segment_digest") != digest_hex({"members": page_members}):
            raise W5ResultSetError("result_set_segment_digest_mismatch", "segment 摘要不一致")
        if page.get("page_content_digest") != digest_hex({"page_index": index, "member_segment_ref": segment["segment_ref"], "members": page_members}):
            raise W5ResultSetError("result_set_page_digest_mismatch", "page 摘要不一致")
        page_receipt = receipts.get(page.get("page_receipt_digest"))
        if not isinstance(page_receipt, Mapping) or any(page_receipt.get(field) != expected for field, expected in {
            "receipt_kind": "page",
            "page_index": index,
            "page_content_digest": page["page_content_digest"],
            "identity_digest": result_set["source_identity"]["identity_digest"],
            "query_digest": result_set["query_digest"],
            "source_population_id": result_set["source_population_id"],
            "source_population_schema_digest": result_set["source_population_schema_digest"],
            "source_dataset_digest": result_set["source_dataset_digest"],
            "member_count": page["member_count"],
            "disposition": "passed",
        }.items()):
            raise W5ResultSetError("result_set_page_receipt_invalid", "Host page receipt 未解析或绑定漂移")
        first_key = _sort_key(page_members[0], result_set["stable_sort"]) if page_members else None
        last_key = _sort_key(page_members[-1], result_set["stable_sort"]) if page_members else None
        if page.get("first_sort_key") != first_key or page.get("last_sort_key") != last_key:
            raise W5ResultSetError("result_set_sort_summary_mismatch", "页首尾排序摘要不一致")
        for member in page_members:
            current_key = _sort_key(member, result_set["stable_sort"])
            if previous_key is not None and current_key < previous_key:
                raise W5ResultSetError("result_set_sort_regression", "成员稳定排序发生回退")
            previous_key = current_key
            ref = _member_ref(member, result_set["dedupe_key"])
            if ref in seen_refs:
                raise W5ResultSetError("result_set_duplicate_member", "ResultSet 存在重复成员")
            seen_refs.add(ref)
            members.append(copy.deepcopy(member))
        expected_token = page.get("token_out")
    if result_set.get("set_completeness") == "complete" and expected_token is not None:
        raise W5ResultSetError("result_set_complete_mismatch", "complete ResultSet 末页 token 必须为空")
    if result_set.get("returned_count") != len(members) or result_set.get("total_count") != len(members):
        raise W5ResultSetError("result_set_count_mismatch", "冻结 complete ResultSet 人口未对账")
    if result_set.get("manifest_digest") != digest_hex({"page_manifest": pages, "member_segments": segments}):
        raise W5ResultSetError("result_set_manifest_digest_mismatch", "manifest_digest 不一致")
    if result_set.get("content_digest") != digest_hex({"members": members}):
        raise W5ResultSetError("result_set_content_digest_mismatch", "content_digest 不一致")
    freeze_receipt = receipts.get(result_set.get("freeze_receipt_digest"))
    if not isinstance(freeze_receipt, Mapping) or any(freeze_receipt.get(field) != expected for field, expected in {
        "receipt_kind": "freeze",
        "result_set_id": result_set["result_set_id"],
        "result_set_revision": result_set["result_set_revision"],
        "manifest_digest": result_set["manifest_digest"],
        "content_digest": result_set["content_digest"],
        "returned_count": result_set["returned_count"],
        "total_count": result_set["total_count"],
        "set_completeness": result_set["set_completeness"],
        "source_population_id": result_set["source_population_id"],
        "source_population_schema_digest": result_set["source_population_schema_digest"],
        "source_dataset_digest": result_set["source_dataset_digest"],
        "disposition": "passed",
    }.items()):
        raise W5ResultSetError("result_set_freeze_receipt_invalid", "Host freeze receipt 未解析或绑定漂移")
    return members


class ResultSetManager:
    def __init__(self, store: ContentAddressedStore, tools: CountryOutageP2S1Tools) -> None:
        self.store = store
        self.tools = tools

    def freeze_tool_pages(
        self,
        *,
        identity: Mapping[str, Any],
        pages: Sequence[Mapping[str, Any]],
        tool_contract_digest: str,
        preview_limit: int = 20,
    ) -> dict[str, Any]:
        source_identity = validate_identity(identity)
        source_identity.pop("binding_digest", None)
        source_identity.pop("identity_digest", None)
        source_identity["registry_snapshot_digest"] = str(source_identity["registry_snapshot_digest"]).removeprefix("sha256:")
        source_identity["identity_digest"] = digest_without_fields(source_identity, "identity_digest")
        if not isinstance(pages, Sequence) or isinstance(pages, (str, bytes)) or not pages:
            raise W5ResultSetError("tool_pages_missing", "至少需要一个 Tool page")
        tool_schema = strict_tool_schema()
        first = pages[0]
        tool_id = first.get("tool_id")
        if tool_id not in _MEMBER_IDENTITY:
            raise W5ResultSetError("tool_not_supported", f"不支持的 Tool：{tool_id}")
        member_identity_fields = _MEMBER_IDENTITY[tool_id]
        output_population = tool_output_population(tool_id)
        query_digest = first.get("query_digest")
        stable_sort_raw = first.get("stable_sort")
        stable_sort_digest = first.get("stable_sort_digest")
        source_population_id = first.get("source_population_id")
        source_dataset_digest = first.get("source_dataset_digest")
        normalized_query = first.get("normalized_query")
        identity_digest = first.get("identity_digest")
        stable_sort = _sort_specs(stable_sort_raw)
        if stable_sort_digest != digest_hex({"stable_sort": stable_sort_raw}):
            raise W5ResultSetError("stable_sort_digest_mismatch", "Tool stable_sort 摘要无法重算")
        frozen_stable_sort_digest = digest_hex({"stable_sort": stable_sort})
        if query_digest != digest_hex({"normalized_query": normalized_query}):
            raise W5ResultSetError("query_digest_mismatch", "Tool query 摘要无法重算")
        first_tool_receipt = first.get("query_receipt")
        if not isinstance(first_tool_receipt, Mapping):
            raise W5ResultSetError("tool_query_receipt_missing", "Tool 根查询回执缺失")
        query_receipt_base = {
            "receipt_kind": "query",
            "query_digest": query_digest,
            "identity_digest": identity_digest,
            "tool_run_id": first["tool_run_id"],
            "source_population_id": source_population_id,
            "source_population_schema_digest": output_population["member_schema_digest"],
            "source_dataset_digest": source_dataset_digest,
            "total_count": first["total_count"],
            "atomic_tool_query_receipt_digest": first["query_receipt_digest"],
            "disposition": "passed",
        }
        query_receipt = {**query_receipt_base, "receipt_digest": digest_hex(query_receipt_base)}
        self.store.put_json("receipt-candidate", query_receipt)
        page_manifest: list[dict[str, Any]] = []
        member_segments: list[dict[str, Any]] = []
        all_members: list[dict[str, Any]] = []
        expected_offset = 0
        token_in: str | None = None
        evidence_refs: set[str] = set()
        for page_index, page_value in enumerate(pages):
            page = copy.deepcopy(dict(page_value))
            definition_name = f"{str(tool_id).lower().replace('-', '')}ResultPage"
            validate_json_schema(page, _schema_for_definition(tool_schema, definition_name), f"{tool_id} page")
            for field, expected in {
                "tool_id": tool_id,
                "query_digest": query_digest,
                "identity_digest": identity_digest,
                "source_population_id": source_population_id,
                "source_dataset_digest": source_dataset_digest,
                "stable_sort_digest": stable_sort_digest,
                "normalized_query": normalized_query,
                "stable_sort": stable_sort_raw,
            }.items():
                if page.get(field) != expected:
                    raise W5ResultSetError("tool_page_identity_drift", f"第 {page_index} 页 {field} 漂移")
            if page.get("page_offset") != expected_offset or page.get("returned_count") != len(page.get("members", [])):
                raise W5ResultSetError("tool_page_chain_invalid", "Tool page offset/count 链无效")
            receipt = page.get("query_receipt")
            if not self.tools.verify_query_receipt(receipt) or page.get("query_receipt_digest") != receipt.get("receipt_digest"):
                raise W5ResultSetError("tool_page_receipt_invalid", "Tool query receipt 验证失败")
            raw_members = copy.deepcopy(page["members"])
            members = [_project_sort_keys(member, stable_sort) for member in raw_members]
            for member_index, member in enumerate(members):
                validate_json_schema(member, output_population["member_schema"], f"{tool_id} projected member[{page_index}:{member_index}]")
                projection_base = {
                    "receipt_kind": "result_set_member_sort_projection", "tool_id": tool_id,
                    "page_index": page_index, "member_index": member_index,
                    "raw_member_digest": digest_hex(raw_members[member_index]),
                    "projected_member_digest": digest_hex(member),
                    "projected_sort_fields": [item["field"] for item in stable_sort if item["field"] not in raw_members[member_index]],
                    "disposition": "passed_without_business_reordering",
                }
                self.store.put_json("receipt-candidate", {**projection_base, "receipt_digest": digest_hex(projection_base)})
            segment_payload = {"members": members}
            segment_ref = self.store.put_json("result-set-segment", segment_payload)["object_digest"]
            segment_digest = digest_hex(segment_payload)
            segment = {
                "segment_ref": segment_ref,
                "page_index": page_index,
                "member_count": len(members),
                "segment_digest": segment_digest,
            }
            page_content_digest = digest_hex({"page_index": page_index, "member_segment_ref": segment_ref, "members": members})
            page_receipt_base = {
                "receipt_kind": "page",
                "page_index": page_index,
                "page_content_digest": page_content_digest,
                "identity_digest": identity_digest,
                "query_digest": query_digest,
                "source_population_id": source_population_id,
                "source_population_schema_digest": output_population["member_schema_digest"],
                "source_dataset_digest": source_dataset_digest,
                "member_count": len(members),
                "atomic_tool_query_receipt_digest": page["query_receipt_digest"],
                "disposition": "passed",
            }
            page_receipt = {**page_receipt_base, "receipt_digest": digest_hex(page_receipt_base)}
            self.store.put_json("receipt-candidate", page_receipt)
            page_item = {
                "page_index": page_index,
                "token_in": token_in,
                "token_out": page.get("next_page_token"),
                "identity_digest": identity_digest,
                "query_digest": query_digest,
                "stable_sort_digest": frozen_stable_sort_digest,
                "source_population_id": source_population_id,
                "source_population_schema_digest": output_population["member_schema_digest"],
                "source_dataset_digest": source_dataset_digest,
                "first_sort_key": _sort_key(members[0], stable_sort) if members else None,
                "last_sort_key": _sort_key(members[-1], stable_sort) if members else None,
                "member_count": len(members),
                "member_segment_ref": segment_ref,
                "page_content_digest": page_content_digest,
                "page_receipt_digest": page_receipt["receipt_digest"],
                "evidence_refs": sorted(set(page["evidence_refs"])),
            }
            page_manifest.append(page_item)
            member_segments.append(segment)
            all_members.extend(members)
            evidence_refs.update(page["evidence_refs"])
            expected_offset += len(members)
            token_in = page.get("next_page_token")
        if token_in is not None:
            raise W5ResultSetError("tool_page_chain_incomplete", "冻结 ResultSet 前必须拉取至 terminal page")
        if any(page.get("total_count") != len(all_members) for page in pages):
            raise W5ResultSetError("tool_page_total_mismatch", "Tool total_count 与完整页人口不一致")
        result_identity = {
            "source_identity": source_identity,
            "source_tool": {
                "tool_id": tool_id,
                "tool_version": first["tool_version"],
                "contract_digest": str(tool_contract_digest).removeprefix("sha256:"),
                "tool_run_id": first["tool_run_id"],
            },
            "normalized_query": normalized_query,
            "stable_sort": stable_sort,
            "source_population_id": source_population_id,
            "source_population_schema_ref": output_population["member_schema_ref"],
            "source_population_schema_digest": output_population["member_schema_digest"],
            "source_dataset_digest": source_dataset_digest,
        }
        result_set_id = "result-set-sha256:" + digest_hex(result_identity)
        member_identity_name = _MEMBER_IDENTITY_NAME[tool_id]
        refs = [str(member[member_identity_name]) for member in all_members]
        preview_without_digest = {
            "view_id": f"preview:{result_set_id}:1:{preview_limit}",
            "source_result_set_id": result_set_id,
            "source_result_set_revision": 1,
            "limit": preview_limit,
            "returned_count": min(preview_limit, len(refs)),
            "stable_sort_digest": frozen_stable_sort_digest,
            "member_refs": refs[:preview_limit],
            "represents_complete_population": False,
        }
        preview = {**preview_without_digest, "view_digest": digest_hex(preview_without_digest)}
        freeze_without_receipt = {
            "schema_version": "country_outage_p2_result_set_v1",
            "result_set_id": result_set_id,
            "result_set_revision": 1,
            "parent_result_set_revision": None,
            "state": "frozen",
            **result_identity,
            "query_digest": query_digest,
            "stable_sort_digest": frozen_stable_sort_digest,
            "member_identity": member_identity_name,
            "dedupe_key": list(member_identity_fields),
            "page_manifest": page_manifest,
            "returned_count": len(all_members),
            "total_count": len(all_members),
            "set_completeness": "complete",
            "resume_page_token": None,
            "member_segments": member_segments,
            "query_receipt_digest": query_receipt["receipt_digest"],
            "source_completeness_receipt_digest": None,
            "evidence_refs": sorted(evidence_refs),
            "limitations": [
                {"code": "rrc25_control_plane_only", "scope": "collector", "description": "仅表达 RRC25 BGP 控制面观测。"},
                *[
                    {"code": f"tool_limitation_{index + 1}", "scope": "source", "description": str(item)}
                    for index, item in enumerate(first["limitations"])
                ],
            ],
            "manifest_digest": digest_hex({"page_manifest": page_manifest, "member_segments": member_segments}),
            "content_digest": digest_hex({"members": all_members}),
            "preview_views": [preview],
            "generation_origin": "tool_pagination_without_llm_member_generation",
            "design_boundary": {"design_only": True, "runtime_implemented": False, "production_deployed": False},
        }
        freeze_receipt_without_digest = {
            "receipt_kind": "freeze",
            "result_set_id": result_set_id,
            "result_set_revision": 1,
            "manifest_digest": freeze_without_receipt["manifest_digest"],
            "content_digest": freeze_without_receipt["content_digest"],
            "returned_count": len(all_members),
            "total_count": len(all_members),
            "set_completeness": "complete",
            "source_population_id": source_population_id,
            "source_population_schema_digest": output_population["member_schema_digest"],
            "source_dataset_digest": source_dataset_digest,
            "disposition": "passed",
        }
        freeze_receipt = {**freeze_receipt_without_digest, "receipt_digest": digest_hex(freeze_receipt_without_digest)}
        self.store.put_json("receipt-candidate", freeze_receipt)
        result_set = {**freeze_without_receipt, "freeze_receipt_digest": freeze_receipt["receipt_digest"]}
        validate_json_schema(result_set, load_frozen_contract("result-set"), "frozen ResultSet")
        validate_result_set(result_set, self.store)
        self.store.put_json("result-set-candidate", result_set)
        return result_set

    def page(self, result_set: Mapping[str, Any], *, page_size: int, page_token: str | None) -> dict[str, Any]:
        members = validate_result_set(result_set, self.store)
        if isinstance(page_size, bool) or not isinstance(page_size, int) or not 1 <= page_size <= 200:
            raise W5ResultSetError("page_size_invalid", "page_size 必须为 1..200", status_code=400)
        offset = 0
        if page_token is not None:
            cursor = self.store.get_json("result-page-cursor", page_token)
            if not isinstance(cursor, Mapping) or cursor.get("result_set_id") != result_set["result_set_id"] or cursor.get("result_set_revision") != result_set["result_set_revision"] or cursor.get("content_digest") != result_set["content_digest"]:
                raise W5ResultSetError("page_token_context_mismatch", "page token 不属于当前 ResultSet", status_code=400)
            offset = cursor.get("offset")
            if isinstance(offset, bool) or not isinstance(offset, int) or not 0 <= offset <= len(members):
                raise W5ResultSetError("page_token_invalid", "page token offset 无效", status_code=400)
        selected = members[offset : offset + page_size]
        next_token = None
        if offset + len(selected) < len(members):
            next_token = self.store.put_json(
                "result-page-cursor",
                {
                    "result_set_id": result_set["result_set_id"],
                    "result_set_revision": result_set["result_set_revision"],
                    "content_digest": result_set["content_digest"],
                    "offset": offset + len(selected),
                },
            )["object_digest"]
        return {
            "schema_version": "country_outage_p2_s1_w5_result_set_page_v1",
            "result_set_id": result_set["result_set_id"],
            "result_set_revision": result_set["result_set_revision"],
            "set_completeness": result_set["set_completeness"],
            "members": selected,
            "returned_count": len(selected),
            "total_count": len(members),
            "page_token": page_token,
            "next_page_token": next_token,
            "content_digest": result_set["content_digest"],
        }


def strict_tool_schema() -> dict[str, Any]:
    from .country_outage_p2_s1_contract_runtime import repository_root, strict_json_loads

    path = repository_root() / "contracts/agent/country-outage-p2-s1-implementation/w1-w2-tool-runtime.schema.json"
    value = strict_json_loads(path.read_bytes())
    if not isinstance(value, dict):
        raise W5ResultSetError("tool_schema_invalid", "Tool runtime Schema 不是对象")
    return value


__all__ = ["ResultSetManager", "W5ResultSetError", "tool_output_population", "validate_result_set"]
