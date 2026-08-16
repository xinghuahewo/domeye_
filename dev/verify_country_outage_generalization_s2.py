#!/usr/bin/env python3
"""机器核对国家中断通用观测页 S2 前缀、AS 与 IP 事件投影。"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
ACCEPTANCE_RECORD = REPOSITORY_ROOT / "docs" / "国家中断通用观测页S2验收记录.md"
METRIC_SCHEMA = REPOSITORY_ROOT / "contracts" / "data" / "rrc25-event-metric-store.schema.json"
REMOTE_HOST = "root@10.99.8.16"
REMOTE_ROOT = (
    "/home/bgpdata/Domeye-Core-dev-data/research-runs/"
    "country-outage-general-page-s2-4b3559a/event-metrics"
)
REMOTE_AUDIT_PATH = REMOTE_ROOT + "/S2-AUDIT.json"

EXPECTED = {
    "implementation_id": "git:4b3559abbd21355b89c0c5fb58e1d80493b83e68",
    "route_event_dataset_id": "route_event_dataset_v1_a408005061499629321017426e99a629",
    "route_event_content_sha256": "9c30e8bce04c83f731c77df239976590be804c8b3ac6799d9da7253c1682849a",
    "route_event_manifest_sha256": "661e5184242b29c164200789ede09938c224cb08a3beb40e8e99dfe2b1f9fbc5",
    "route_state_dataset_id": "route_state_dataset_v1_c2f7f7c7c63c824f4e92ed4c90787bcb",
    "route_state_content_sha256": "54ffc35d94cd3d7a7b195afc664110303fe07a723079bbcf1fb52a0a0b8be7c4",
    "route_state_manifest_sha256": "f810c354b9dd87cdd62ae51b24281f72eac1344552b6616b8cf70b542433b587",
    "cohort_dataset_id": "event_cohort_dataset_v1_11c18b460a735c1acfa5f925d09c1bd8",
    "cohort_content_sha256": "75d53ae4ba355c859d70b79aabf3ca597915fbfc66239f9eaa050b7c6004dd6c",
    "cohort_manifest_sha256": "3bebb14181912e645e0e1d25439edda9be2e327e059e715655852d102455fef6",
    "peer_dataset_id": "peer_session_dataset_v1_982f87e788af6128b98b7c8107485a74",
    "peer_content_sha256": "185342c0b04b482c7bf04157515148687e88490d1b226e034c701dbbd722878e",
    "mapping_version": "41fa4721c1c8f5eb4fe120987eb9672d32382d694889990b93028f4c881f63c4",
    "mapping_compatible_sha256": "05b9809116c3525769e8dc2bd52497ff810a5b4d063cf3c93442d23ed119f9d5",
    "mapping_revised_sha256": "0c20c3f522170d0838466ab9fa8da729abf60767fe820038efc73a3f62dd510e",
    "metric_dataset_id": "event_metric_dataset_v1_136ef94a1068d83f25f844c0fc85f756",
    "metric_content_sha256": "1b38ec27bc444e6086c3e34c363089b1d739a6ef3bd0dbf156a027d234c70905",
    "metric_manifest_sha256": "c745153178e7e8a0ccf8ba4e5ac285aa76b66cc3980bdeacb28b40939b0d23d5",
    "state_points": 13_488,
    "fixed_prefixes": 112_584,
    "directions": 4_518_501,
}

LOCAL_SOURCE_EXPECTATIONS = {
    "tools/rrc25-iran-replay-go/event_metric_store.go": (
        'EventMetricStoreVersion     = "rrc25-event-metric-store/v1"',
        "the_existing_route_state_dataset_is_the_only_route_state_fact",
        "multiple_bgp_sessions_do_not_expand_the_denominator",
        "regardless_of_origin",
        "never_materialize_or_imply_route_withdrawals",
        "never_offset_fixed_cohort_interruptions",
        "never_coerced_to_zero",
        "deduplicated_unique_ipv4_address_union",
        "deduplicated_unique_ipv6_slash48_equivalent_union",
    ),
    "tools/rrc25-iran-replay-go/prefix_coverage.go": (
        "prefixCoverage",
        "familyBits",
        "coverage.members",
    ),
}

REQUIRED_RECORD_PHRASES = (
    "版本：2.1",
    "RRC25-only",
    "13,488",
    "112,584",
    "4,518,501",
    "前缀路由中断",
    "完全中断前缀",
    "受影响 AS",
    "AS 路由中断",
    "IPv4 唯一地址并集",
    "IPv6 `/48` 等价并集",
    "新前缀独立轨道",
    "缺槽和未知不补零",
    "RouteState 仍是唯一状态事实",
    "通用观测页最终验收回检：S2 一致",
)


REMOTE_DEEP_AUDIT_SCRIPT = r'''
import collections
import gzip
import hashlib
import ipaddress
import json
import os

ROOT = "/home/bgpdata/Domeye-Core-dev-data/research-runs/country-outage-general-page-s2-4b3559a/event-metrics"
SAMPLES = {
    "IR": "country_outage/2026-02-27 09:12:32/IR/1/r",
    "MW": "country_outage/2026-03-09 22:09:38/MW/2/r",
}

def load(path):
    with open(path, "rb") as stream:
        raw = stream.read()
    return json.loads(raw), raw

def verify_file(meta):
    path = os.path.join(ROOT, meta["path"])
    assert os.path.isfile(path) and not os.path.islink(path)
    assert os.path.getsize(path) == meta["size_bytes"]
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    assert digest.hexdigest() == meta["sha256"]
    return path

def rows(meta):
    path = verify_file(meta)
    count = 0
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        for line in stream:
            count += 1
            yield json.loads(line)
    assert count == meta["row_count"]

class Node:
    __slots__ = ("direct", "covered", "children")
    def __init__(self):
        self.direct = 0
        self.covered = 0
        self.children = [None, None]

class Coverage:
    def __init__(self, afi):
        self.bits = 32 if afi == "ipv4" else 48
        self.root = Node()
        self.members = collections.Counter()
    def adjust(self, value, delta):
        network = ipaddress.ip_network(value, strict=True)
        assert (network.version == 4) == (self.bits == 32), (
            "coverage address family mismatch", value, network.version, self.bits, delta
        )
        key = str(network)
        self.members[key] += delta
        assert self.members[key] >= 0, ("coverage member underflow", key, delta)
        if self.members[key] == 0:
            del self.members[key]
        depth = min(network.prefixlen, self.bits)
        integer = int(network.network_address)
        if network.version == 6:
            integer >>= 80
        path = [self.root]
        node = self.root
        for at in range(depth):
            branch = (integer >> (self.bits - at - 1)) & 1
            if node.children[branch] is None:
                assert delta > 0, ("coverage removal missing path", key, at)
                node.children[branch] = Node()
            node = node.children[branch]
            path.append(node)
        node.direct += delta
        assert node.direct >= 0, ("coverage direct underflow", key, delta, node.direct)
        for at in range(len(path) - 1, -1, -1):
            node = path[at]
            if node.direct:
                node.covered = 1 << (self.bits - at)
            else:
                node.covered = sum(child.covered for child in node.children if child is not None)
    @property
    def covered(self):
        return self.root.covered

def prefix_visible(state):
    return state[3] > 0

def audit_event(event):
    disk, _ = load(os.path.join(ROOT, event["directory"], "manifest.json"))
    assert disk == event
    series = rows(event["series"])
    prefix_iter = iter(rows(event["prefix_states"]))
    asn_iter = iter(rows(event["asn_states"]))
    new_iter = iter(rows(event["new_prefix_states"]))
    prefix_next = next(prefix_iter, None)
    asn_next = next(asn_iter, None)
    new_next = next(new_iter, None)
    prefix_state = {}
    prefix_classes = collections.Counter()
    direction_counts = collections.Counter()
    fixed_cover = {"ipv4": Coverage("ipv4"), "ipv6": Coverage("ipv6")}
    asn_state = {}
    asn_classes = collections.Counter()
    new_state = {}
    new_current_count = collections.Counter()
    new_cumulative_count = collections.Counter()
    new_current = {"ipv4": Coverage("ipv4"), "ipv6": Coverage("ipv6")}
    new_cumulative = {"ipv4": Coverage("ipv4"), "ipv6": Coverage("ipv6")}
    first_slot = None
    previous_slot = None
    last_values = None
    peaks = collections.Counter()
    points = 0

    def apply_prefix(row):
        key = row["cohort_member_id"]
        current = prefix_state.get(key)
        if current is None:
            assert row["record_kind"] == "baseline"
        else:
            prefix_classes[current[0]] -= 1
            direction_counts["expected"] -= current[2]
            direction_counts["visible"] -= current[3]
            direction_counts["invisible"] -= current[4]
            direction_counts["unknown"] -= current[5]
            if prefix_visible(current):
                fixed_cover[current[7]].adjust(current[6], -1)
        state = (
            row["classification"], row["state_slot"], row["expected_direction_count"],
            row["visible_direction_count"], row["invisible_direction_count"],
            row["unknown_direction_count"], row["prefix"], row["address_family"],
        )
        assert state[2] == state[3] + state[4] + state[5]
        prefix_state[key] = state
        prefix_classes[state[0]] += 1
        direction_counts["expected"] += state[2]
        direction_counts["visible"] += state[3]
        direction_counts["invisible"] += state[4]
        direction_counts["unknown"] += state[5]
        if prefix_visible(state):
            fixed_cover[state[7]].adjust(state[6], 1)

    def apply_asn(row):
        key = row["asn"]
        current = asn_state.get(key)
        if current is None:
            assert row["record_kind"] == "baseline"
        else:
            asn_classes[current[0]] -= 1
        state = (
            row["classification"], row["fixed_prefix_count"], row["normal_prefix_count"],
            row["partial_prefix_count"], row["complete_prefix_count"], row["unknown_prefix_count"],
        )
        assert state[1] == sum(state[2:])
        if state[5]:
            assert state[0] == "unknown"
        elif state[4] == state[1]:
            assert state[0] == "route_interrupted"
        elif state[3] + state[4]:
            assert state[0] == "affected"
        else:
            assert state[0] == "normal"
        asn_state[key] = state
        asn_classes[state[0]] += 1

    def apply_new(row):
        key = (row["address_family"], row["prefix"])
        current = new_state.get(key)
        visible = row["visibility_state"] == "visible"
        if current is None:
            assert row["record_kind"] == "first_observed", ("bad new-prefix first record", row)
            assert row["state_slot"] == row["first_observed_slot"], (
                "bad new-prefix first observation", row
            )
            new_cumulative_count[key[0]] += 1
            new_cumulative[key[0]].adjust(key[1], 1)
        else:
            assert row["record_kind"] == "change" and current[0] != visible, (
                "bad new-prefix change", row, current
            )
            if current[0]:
                new_current_count[key[0]] -= 1
                new_current[key[0]].adjust(key[1], -1)
        if visible and (current is None or not current[0]):
            new_current_count[key[0]] += 1
            new_current[key[0]].adjust(key[1], 1)
        new_state[key] = (visible, row["first_observed_slot"])

    for point in series:
        slot = point["state_slot"]
        if first_slot is None:
            first_slot = slot
        if previous_slot is not None:
            assert slot == previous_slot + 1
        previous_slot = slot
        assert point["schema_version"] == "rrc25-event-metric-series/v1"
        assert point["event_metric_id"] == event["event_metric_id"]
        assert point["cohort_id"] == event["cohort_id"]
        assert point["value_state"] == "observed" and point["missing_reason"] is None
        while prefix_next is not None and prefix_next["state_slot"] == slot:
            apply_prefix(prefix_next)
            prefix_next = next(prefix_iter, None)
        while asn_next is not None and asn_next["state_slot"] == slot:
            apply_asn(asn_next)
            asn_next = next(asn_iter, None)
        while new_next is not None and new_next["state_slot"] == slot:
            apply_new(new_next)
            new_next = next(new_iter, None)
        value = point["values"]
        assert value is not None
        assert value["fixed_prefix_count"] == len(prefix_state) == event["fixed_prefix_count"]
        assert value["normal_prefix_count"] == prefix_classes["normal"]
        assert value["partially_interrupted_prefix_count"] == prefix_classes["partially_interrupted"]
        assert value["completely_interrupted_prefix_count"] == prefix_classes["completely_interrupted"]
        assert value["unknown_prefix_count"] == prefix_classes["unknown"]
        assert value["interrupted_prefix_count"] == (
            value["partially_interrupted_prefix_count"] + value["completely_interrupted_prefix_count"]
        )
        assert value["expected_direction_count"] == direction_counts["expected"]
        assert value["visible_direction_count"] == direction_counts["visible"]
        assert value["invisible_direction_count"] == direction_counts["invisible"]
        assert value["unknown_direction_count"] == direction_counts["unknown"]
        assert value["fixed_asn_count"] == len(asn_state) == event["fixed_asn_count"]
        assert value["normal_asn_count"] == asn_classes["normal"]
        assert value["affected_asn_count"] == asn_classes["affected"]
        assert value["route_interrupted_asn_count"] == asn_classes["route_interrupted"]
        assert value["unknown_asn_count"] == asn_classes["unknown"]
        assert value["fixed_visible_ipv4_address_count"] == fixed_cover["ipv4"].covered
        assert value["fixed_visible_ipv6_slash48_count"] == fixed_cover["ipv6"].covered
        for afi in ("ipv4", "ipv6"):
            assert value[f"new_visible_{afi}_prefix_count"] == new_current_count[afi]
            assert value[f"new_cumulative_{afi}_prefix_count"] == new_cumulative_count[afi]
        assert value["new_visible_ipv4_address_count"] == new_current["ipv4"].covered
        assert value["new_visible_ipv6_slash48_count"] == new_current["ipv6"].covered
        assert value["new_cumulative_ipv4_address_count"] == new_cumulative["ipv4"].covered
        assert value["new_cumulative_ipv6_slash48_count"] == new_cumulative["ipv6"].covered
        peaks["interrupted_prefix"] = max(peaks["interrupted_prefix"], value["interrupted_prefix_count"])
        peaks["complete_prefix"] = max(peaks["complete_prefix"], value["completely_interrupted_prefix_count"])
        peaks["affected_asn"] = max(peaks["affected_asn"], value["affected_asn_count"])
        peaks["route_interrupted_asn"] = max(peaks["route_interrupted_asn"], value["route_interrupted_asn_count"])
        last_values = value
        points += 1
    assert prefix_next is None and asn_next is None and new_next is None
    assert points == event["state_point_count"] == event["series"]["row_count"]
    assert len(prefix_state) == event["fixed_prefix_count"]
    assert len(asn_state) == event["fixed_asn_count"]
    assert len(new_state) == event["new_prefix_count"]
    assert last_values == event["final_values"]
    return {
        "event_metric_id": event["event_metric_id"],
        "legacy_reference": event["legacy_reference"],
        "country_code": event["country_code"],
        "state_points": points,
        "fixed_prefixes": len(prefix_state),
        "fixed_asns": len(asn_state),
        "new_prefixes": len(new_state),
        "peaks": dict(peaks),
        "final": last_values,
    }

manifest, left = load(os.path.join(ROOT, "manifest.json"))
complete, right = load(os.path.join(ROOT, "COMPLETE.json"))
assert left == right and manifest == complete
assert manifest["schema_version"] == "rrc25-event-metric-store/v1"
assert manifest["status"] == "complete" and manifest["collector_id"] == "rrc25"
assert manifest["event_count"] == len(manifest["events"]) == 81
summaries = []
samples = {}
for event in manifest["events"]:
    summary = audit_event(event)
    summaries.append(summary)
    for code, reference in SAMPLES.items():
        if event["legacy_reference"] == reference:
            samples[code] = summary
assert set(samples) == set(SAMPLES)
assert sum(item["state_points"] for item in summaries) == manifest["state_point_count"] == manifest["series_row_count"]
assert sum(item["fixed_prefixes"] for item in summaries) == manifest["fixed_prefix_count"]
assert sum(event["expected_direction_relation_count"] for event in manifest["events"]) == manifest["expected_direction_relation_count"]
print(json.dumps({
    "status": "pass",
    "metric": {
        "dataset_id": manifest["dataset_id"],
        "content_sha256": manifest["content_sha256"],
        "manifest_sha256": hashlib.sha256(left).hexdigest(),
        "implementation_id": manifest["implementation_id"],
        "source_route_event_dataset_id": manifest["source_route_event_dataset_id"],
        "source_route_event_content_sha256": manifest["source_route_event_content_sha256"],
        "source_route_event_manifest_sha256": manifest["source_route_event_manifest_sha256"],
        "source_route_state_dataset_id": manifest["source_route_state_dataset_id"],
        "source_route_state_content_sha256": manifest["source_route_state_content_sha256"],
        "source_route_state_manifest_sha256": manifest["source_route_state_manifest_sha256"],
        "source_event_cohort_dataset_id": manifest["source_event_cohort_dataset_id"],
        "source_event_cohort_content_sha256": manifest["source_event_cohort_content_sha256"],
        "source_event_cohort_manifest_sha256": manifest["source_event_cohort_manifest_sha256"],
        "source_peer_session_dataset_id": manifest["source_peer_session_dataset_id"],
        "source_peer_session_content_sha256": manifest["source_peer_session_content_sha256"],
        "mapping_version": manifest["mapping_version"],
        "mapping_compatible_sha256": manifest["mapping_compatible_sha256"],
        "mapping_revised_sha256": manifest["mapping_revised_sha256"],
        "events": manifest["event_count"],
        "state_points": manifest["state_point_count"],
        "fixed_prefixes": manifest["fixed_prefix_count"],
        "directions": manifest["expected_direction_relation_count"],
        "prefix_state_rows": manifest["prefix_state_row_count"],
        "asn_state_rows": manifest["asn_state_row_count"],
        "new_prefix_state_rows": manifest["new_prefix_state_row_count"],
        "unique_new_prefixes_across_events": sum(item["new_prefixes"] for item in summaries),
    },
    "samples": samples,
}, ensure_ascii=False, separators=(",", ":")))
'''

REMOTE_AUDIT_SCRIPT = f'''
import hashlib
import json
import os

AUDIT_PATH = {REMOTE_AUDIT_PATH!r}
ROOT = {REMOTE_ROOT!r}
def raw(path):
    with open(path, "rb") as stream:
        return stream.read()
audit = json.loads(raw(AUDIT_PATH))
copy = dict(audit)
copy.pop("content_sha256", None)
copy.pop("audit_id", None)
content = hashlib.sha256(json.dumps(copy, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
assert audit["schema_version"] == "country_outage_generalization_s2_formal_audit/v1"
assert audit["status"] == "complete"
assert audit["content_sha256"] == content
assert audit["audit_id"] == "country_outage_s2_audit_v1_" + content[:32]
manifest = raw(os.path.join(ROOT, "manifest.json"))
assert manifest == raw(os.path.join(ROOT, "COMPLETE.json"))
assert hashlib.sha256(manifest).hexdigest() == audit["evidence"]["metric"]["manifest_sha256"]
assert json.loads(manifest)["content_sha256"] == audit["evidence"]["metric"]["content_sha256"]
print(json.dumps(audit["evidence"], ensure_ascii=False, separators=(",", ":")))
'''


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def verify_local() -> tuple[list[str], list[str]]:
    errors: list[str] = []
    checks: list[str] = []
    for key in ("metric_dataset_id", "metric_content_sha256", "metric_manifest_sha256"):
        if str(EXPECTED[key]).startswith("__"):
            errors.append(f"S2 正式身份尚未冻结：{key}")
    for path in (ACCEPTANCE_RECORD, METRIC_SCHEMA):
        if not path.is_file():
            errors.append(f"缺少 S2 文件：{path.relative_to(REPOSITORY_ROOT)}")
    if errors:
        return errors, checks
    record = read_text(ACCEPTANCE_RECORD)
    for phrase in REQUIRED_RECORD_PHRASES:
        if phrase not in record:
            errors.append(f"S2 验收记录缺少：{phrase}")
    checks.append("S2 验收记录包含正式人口、指标规则和 Hook 结论")
    try:
        schema = json.loads(read_text(METRIC_SCHEMA))
    except json.JSONDecodeError as error:
        errors.append(f"S2 合同不是合法 JSON：{error}")
    else:
        if schema.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
            errors.append("S2 合同版本漂移")
        if schema.get("additionalProperties") is not False:
            errors.append("S2 合同顶层必须拒绝额外字段")
    checks.append("S2 制品合同是封闭的 draft 2020-12 JSON Schema")
    for relative, phrases in LOCAL_SOURCE_EXPECTATIONS.items():
        content = read_text(REPOSITORY_ROOT / relative)
        for phrase in phrases:
            if phrase not in content:
                errors.append(f"S2 源码语义漂移：{relative} 缺少 {phrase}")
    checks.append("固定 cohort、peer ASN 方向、新前缀、未知和资源并集仍由源码约束")
    for phrase in (
        "S3 路径关联已完成", "API 已完成", "页面已上线",
        "使用 customer cone 生成", "确认客户依赖",
        "待正式投影完成后冻结", "TODO",
    ):
        if phrase in record:
            errors.append(f"S2 验收记录越级或改变事实：{phrase}")
    checks.append("S2 未越级声明 S3-S6 或推断原因与依赖")
    return errors, checks


def run_remote_script(script: str, timeout: int) -> dict[str, Any]:
    result = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", REMOTE_HOST, "python3", "-"],
        input=script,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stdout + result.stderr).strip()
        raise RuntimeError(f"远端 S2 审计失败：{detail or '无错误详情'}")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"远端 S2 审计输出无效：{error}") from error
    if not isinstance(payload, dict):
        raise RuntimeError("远端 S2 审计输出必须是对象")
    return payload


def create_remote_audit() -> dict[str, Any]:
    evidence = run_remote_script(REMOTE_DEEP_AUDIT_SCRIPT, 1800)
    if evidence.get("status") != "pass":
        raise RuntimeError(f"远端 S2 深审未通过：{evidence}")
    payload: dict[str, Any] = {
        "schema_version": "country_outage_generalization_s2_formal_audit/v1",
        "status": "complete",
        "evidence": evidence,
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    content_sha = hashlib.sha256(canonical).hexdigest()
    payload["content_sha256"] = content_sha
    payload["audit_id"] = "country_outage_s2_audit_v1_" + content_sha[:32]
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8") + b"\n"
    encoded = base64.b64encode(raw).decode("ascii")
    writer = f'''
import base64
import os
path = {REMOTE_AUDIT_PATH!r}
raw = base64.b64decode({encoded!r})
if os.path.exists(path):
    if open(path, "rb").read() != raw:
        raise SystemExit("S2-AUDIT.json 已存在且内容不同")
else:
    temporary = path + ".tmp"
    if os.path.exists(temporary):
        raise SystemExit("S2-AUDIT.json 临时文件已存在")
    with open(temporary, "xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    os.rename(temporary, path)
print("ok")
'''
    result = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", REMOTE_HOST, "python3", "-"],
        input=writer, capture_output=True, text=True, timeout=30, check=False,
    )
    if result.returncode != 0:
        raise RuntimeError((result.stdout + result.stderr).strip() or "无法写入正式 S2 审计")
    return payload


def validate_remote(payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if payload.get("status") != "pass":
        return [f"远端 S2 深审未通过：{payload}"]
    metric = payload.get("metric", {})
    pairs = (
        ("dataset_id", "metric_dataset_id"),
        ("content_sha256", "metric_content_sha256"),
        ("manifest_sha256", "metric_manifest_sha256"),
        ("implementation_id", "implementation_id"),
        ("source_route_event_dataset_id", "route_event_dataset_id"),
        ("source_route_event_content_sha256", "route_event_content_sha256"),
        ("source_route_event_manifest_sha256", "route_event_manifest_sha256"),
        ("source_route_state_dataset_id", "route_state_dataset_id"),
        ("source_route_state_content_sha256", "route_state_content_sha256"),
        ("source_route_state_manifest_sha256", "route_state_manifest_sha256"),
        ("source_event_cohort_dataset_id", "cohort_dataset_id"),
        ("source_event_cohort_content_sha256", "cohort_content_sha256"),
        ("source_event_cohort_manifest_sha256", "cohort_manifest_sha256"),
        ("source_peer_session_dataset_id", "peer_dataset_id"),
        ("source_peer_session_content_sha256", "peer_content_sha256"),
        ("mapping_version", "mapping_version"),
        ("mapping_compatible_sha256", "mapping_compatible_sha256"),
        ("mapping_revised_sha256", "mapping_revised_sha256"),
    )
    for actual, expected in pairs:
        if metric.get(actual) != EXPECTED[expected]:
            errors.append(f"S2 正式身份冲突：{actual}={metric.get(actual)!r}")
    if (
        metric.get("events") != 81
        or metric.get("state_points") != EXPECTED["state_points"]
        or metric.get("fixed_prefixes") != EXPECTED["fixed_prefixes"]
        or metric.get("directions") != EXPECTED["directions"]
    ):
        errors.append("S2 根人口冲突")
    samples = payload.get("samples", {})
    if set(samples) != {"IR", "MW"}:
        errors.append("S2 伊朗/马拉维样本缺失")
    else:
        for code in ("IR", "MW"):
            sample = samples[code]
            if sample.get("country_code") != code or sample.get("state_points", 0) < 1:
                errors.append(f"S2 {code} 样本人口冲突")
            peaks = sample.get("peaks", {})
            if not all(isinstance(peaks.get(key), int) and peaks[key] >= 0 for key in (
                "interrupted_prefix", "complete_prefix", "affected_asn", "route_interrupted_asn"
            )):
                errors.append(f"S2 {code} 峰值人口无效")
    return errors


def verify() -> dict[str, Any]:
    errors, checks = verify_local()
    remote: dict[str, Any] | None = None
    if not errors:
        try:
            remote = run_remote_script(REMOTE_AUDIT_SCRIPT, 30)
        except (OSError, subprocess.SubprocessError, RuntimeError) as error:
            errors.append(str(error))
        else:
            errors.extend(validate_remote(remote))
            checks.append("正式 81 个事件的 series、前缀、AS、新前缀与 IP 资源逐点重建深审")
    return {
        "schema_version": "country_outage_generalization_s2_verification_v1",
        "status": "pass" if not errors else "fail",
        "stage": "S2",
        "check_count": len(checks),
        "checks": checks,
        "remote_evidence": remote,
        "errors": errors,
    }


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--create-remote-audit", action="store_true",
        help="对正式制品执行一次逐点深审并以 create-only 方式冻结审计结果。",
    )
    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()
    if arguments.create_remote_audit:
        try:
            payload = create_remote_audit()
        except (OSError, subprocess.SubprocessError, RuntimeError) as error:
            json.dump({"status": "fail", "error": str(error)}, sys.stdout, ensure_ascii=False, indent=2)
            sys.stdout.write("\n")
            return 1
        json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return 0
    payload = verify()
    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0 if payload["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
