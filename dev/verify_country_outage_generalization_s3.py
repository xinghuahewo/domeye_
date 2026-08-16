#!/usr/bin/env python3
"""机器核对国家中断通用观测页 S3 的冻结 AS 属性与真实路径关联。"""

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
ACCEPTANCE_RECORD = REPOSITORY_ROOT / "docs" / "国家中断通用观测页S3验收记录.md"
AS_PATH_SCHEMA = REPOSITORY_ROOT / "contracts" / "data" / "rrc25-event-as-path-store.schema.json"
REMOTE_HOST = "root@10.99.8.16"
REMOTE_ROOT = (
    "/home/bgpdata/Domeye-Core-dev-data/research-runs/"
    "country-outage-general-page-s3-18dfdf3/event-as-path"
)
REMOTE_AUDIT_PATH = REMOTE_ROOT + "/S3-AUDIT.json"

EXPECTED = {
    "implementation_id": "git:18dfdf319a883c4fd5172d4ab9d19d085e573e70",
    "dataset_id": "event_as_path_dataset_v1_027b658b0a3121f9ec41d33da3a01504",
    "content_sha256": "1398f13b5271ee96fa6591bf62f3bf9fa94dc2dd594a7cc8673a64b1c65ed797",
    "manifest_sha256": "b286c8973ac0af139e1f309c6362c77e951961b3691887c77cbdf4365b64bfa5",
    "route_event_dataset_id": "route_event_dataset_v1_a408005061499629321017426e99a629",
    "route_event_content_sha256": "9c30e8bce04c83f731c77df239976590be804c8b3ac6799d9da7253c1682849a",
    "route_event_manifest_sha256": "661e5184242b29c164200789ede09938c224cb08a3beb40e8e99dfe2b1f9fbc5",
    "cohort_dataset_id": "event_cohort_dataset_v1_11c18b460a735c1acfa5f925d09c1bd8",
    "cohort_content_sha256": "75d53ae4ba355c859d70b79aabf3ca597915fbfc66239f9eaa050b7c6004dd6c",
    "cohort_manifest_sha256": "3bebb14181912e645e0e1d25439edda9be2e327e059e715655852d102455fef6",
    "metric_dataset_id": "event_metric_dataset_v1_136ef94a1068d83f25f844c0fc85f756",
    "metric_content_sha256": "1b38ec27bc444e6086c3e34c363089b1d739a6ef3bd0dbf156a027d234c70905",
    "metric_manifest_sha256": "c745153178e7e8a0ccf8ba4e5ac285aa76b66cc3980bdeacb28b40939b0d23d5",
    "as_snapshot_sha256": "9ef7bd4dcf07b53d986be392f57e40652e37f352c5e2631d24649cda41ba7da2",
    "as_snapshot_size": 375_961_154,
    "events": 81,
    "affected_as": 2_112,
    "relations": 12_447,
    "concurrent_relations": 5_011,
    "evidence_rows": 5_093_251,
    "known_observations": 4_716_229,
    "unknown_observations": 0,
    "resolved_paths": 222_011,
    "path_partitions": 3_831,
    "path_rows_scanned": 229_996_955,
}

LOCAL_SOURCE_EXPECTATIONS = {
    "tools/rrc25-iran-replay-go/event_as_path_store.go": (
        "first_valid_row_wins_matching_existing_as_feature_loader",
        "downstream_is_the_known_route_origin_when_an_ordered_rrc25_as_path_contains_the_affected_as_before_that_origin",
        "as_set_confederation_or_missing_path_never_creates_an_ordered_downstream_relationship",
        "path_containment_is_observed_control_plane_association_not_dependency_propagation_impact_or_cause",
        "eventASNRouteInterrupted",
        "PeakConcurrentIPv4AddressCount",
        "PeakConcurrentIPv6Slash48Count",
    ),
}

REQUIRED_RECORD_PHRASES = (
    "版本：2.1",
    "RRC25-only",
    "2,112",
    "12,447",
    "5,011",
    "5,093,251",
    "冻结 `as_entity.csv`",
    "AS 路由中断排在受影响 AS 之前",
    "实际有序 RRC25 AS_PATH",
    "路径关联不代表依赖、传播、影响或原因",
    "不使用 AS relationship、customer cone",
    "缺失属性保持未知",
    "通用观测页最终验收回检：S3 一致",
)


REMOTE_DEEP_AUDIT_SCRIPT = r'''
import collections
import csv
import gzip
import hashlib
import ipaddress
import json
import os

ROOT = "/home/bgpdata/Domeye-Core-dev-data/research-runs/country-outage-general-page-s3-18dfdf3/event-as-path"
METRIC_ROOT = "/home/bgpdata/Domeye-Core-dev-data/research-runs/country-outage-general-page-s2-4b3559a/event-metrics"
COHORT_ROOT = "/home/bgpdata/Domeye-Core-dev-data/research-runs/country-outage-general-page-s1-711350f/event-cohorts"
AS_SNAPSHOT = "/home/bgpdata/Domeye-Core-dev-data/api/info/as_entity.csv"
SAMPLES = {
    "IR": "country_outage/2026-02-27 09:12:32/IR/1/r",
    "MW": "country_outage/2026-03-09 22:09:38/MW/2/r",
}

def raw(path):
    with open(path, "rb") as stream:
        return stream.read()

def load(path):
    value = raw(path)
    return json.loads(value), value

def digest(path):
    value = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            value.update(block)
    return value.hexdigest()

def verified_rows(root, meta):
    path = os.path.join(root, meta["path"])
    assert os.path.isfile(path) and not os.path.islink(path)
    assert os.path.getsize(path) == meta["size_bytes"]
    assert digest(path) == meta["sha256"]
    count = 0
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        for line in stream:
            count += 1
            yield json.loads(line)
    assert count == meta["row_count"], (path, count, meta["row_count"])

class Node:
    __slots__ = ("direct", "covered", "children")
    def __init__(self):
        self.direct = 0
        self.covered = 0
        self.children = [None, None]

class Coverage:
    def __init__(self, family):
        self.bits = 32 if family == "ipv4" else 48
        self.root = Node()
        self.members = collections.Counter()
    def adjust(self, prefix, delta):
        network = ipaddress.ip_network(prefix, strict=True)
        assert (network.version == 4) == (self.bits == 32)
        key = str(network)
        self.members[key] += delta
        assert self.members[key] >= 0
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
                assert delta > 0
                node.children[branch] = Node()
            node = node.children[branch]
            path.append(node)
        node.direct += delta
        assert node.direct >= 0
        for at in range(len(path) - 1, -1, -1):
            node = path[at]
            if node.direct:
                node.covered = 1 << (self.bits - at)
            else:
                node.covered = sum(child.covered for child in node.children if child is not None)
    @property
    def covered(self):
        return self.root.covered

def nullable(value):
    value = value.strip()
    if not value or value == "未知" or value.lower() == "unknown":
        return None
    return value

def profile_tuple(name, organization, nature):
    return (
        name, organization, nature,
        "unknown" if name is None else "observed",
        "unknown" if organization is None else "observed",
        "unknown" if nature is None else "observed",
    )

def register_profile(expected, asn, value):
    previous = expected.setdefault(asn, value)
    assert previous == value, (asn, previous, value)

def interrupted(value):
    return value in {"partially_interrupted", "completely_interrupted"}

manifest, manifest_raw = load(os.path.join(ROOT, "manifest.json"))
complete, complete_raw = load(os.path.join(ROOT, "COMPLETE.json"))
assert manifest_raw == complete_raw and manifest == complete
assert manifest["schema_version"] == "rrc25-event-as-path-store/v1"
assert manifest["status"] == "complete" and manifest["collector_id"] == "rrc25"
assert manifest["window_start_utc"] == "2026-02-24T00:00:00Z"
assert manifest["window_end_exclusive_utc"] == "2026-03-11T00:00:00Z"
assert manifest["path_relationship_semantics"] == "downstream_is_the_known_route_origin_when_an_ordered_rrc25_as_path_contains_the_affected_as_before_that_origin"
assert manifest["unordered_path_policy"] == "as_set_confederation_or_missing_path_never_creates_an_ordered_downstream_relationship"
assert manifest["causal_boundary"] == "path_containment_is_observed_control_plane_association_not_dependency_propagation_impact_or_cause"
metric_manifest, metric_raw = load(os.path.join(METRIC_ROOT, "manifest.json"))
assert metric_raw == raw(os.path.join(METRIC_ROOT, "COMPLETE.json"))
cohort_manifest, cohort_raw = load(os.path.join(COHORT_ROOT, "manifest.json"))
assert cohort_raw == raw(os.path.join(COHORT_ROOT, "COMPLETE.json"))
assert manifest["source_event_metric_dataset_id"] == metric_manifest["dataset_id"]
assert manifest["source_event_metric_content_sha256"] == metric_manifest["content_sha256"]
assert manifest["source_event_metric_manifest_sha256"] == hashlib.sha256(metric_raw).hexdigest()
assert manifest["source_event_cohort_dataset_id"] == cohort_manifest["dataset_id"]
assert manifest["source_event_cohort_content_sha256"] == cohort_manifest["content_sha256"]
assert manifest["source_event_cohort_manifest_sha256"] == hashlib.sha256(cohort_raw).hexdigest()
assert os.path.getsize(AS_SNAPSHOT) == manifest["as_attribute_snapshot_size_bytes"]
assert digest(AS_SNAPSHOT) == manifest["as_attribute_snapshot_sha256"]

metric_by_id = {item["event_metric_id"]: item for item in metric_manifest["events"]}
cohort_by_id = {item["cohort_id"]: item for item in cohort_manifest["cohorts"]}
expected_profiles = {}
samples = {}
totals = collections.Counter()

for event in manifest["events"]:
    child, _ = load(os.path.join(ROOT, event["directory"], "manifest.json"))
    assert child == event
    metric = metric_by_id[event["event_metric_id"]]
    cohort = cohort_by_id[event["cohort_id"]]
    assert metric["cohort_id"] == cohort["cohort_id"] == event["cohort_id"]
    assert metric["legacy_reference"] == event["legacy_reference"]
    assert metric["window_start_utc"] == event["window_start_utc"]
    assert metric["projection_end_state_point_utc"] == event["projection_end_state_point_utc"]

    affected_rows = list(verified_rows(ROOT, event["affected_as"]))
    downstream_rows = list(verified_rows(ROOT, event["path_downstreams"]))
    assert len(affected_rows) == event["affected_as_count"]
    assert len(downstream_rows) == event["path_downstream_relation_count"]
    affected_by_asn = {}
    previous_sort = None
    for row in affected_rows:
        assert row["schema_version"] == "rrc25-event-affected-as/v1"
        assert row["event_as_path_id"] == event["event_as_path_id"]
        assert row["event_metric_id"] == event["event_metric_id"]
        assert row["cohort_id"] == event["cohort_id"]
        assert row["static_attribute_snapshot_sha256"] == manifest["as_attribute_snapshot_sha256"]
        assert row["fixed_prefix_count"] > 0
        assert row["event_classification"] in {"affected", "route_interrupted"}
        for value, state in (
            (row["as_name"], row["name_state"]),
            (row["organization"], row["organization_state"]),
            (row["nature"], row["nature_state"]),
        ):
            assert state == ("unknown" if value is None else "observed")
        profile = (
            row["as_name"], row["organization"], row["nature"],
            row["name_state"], row["organization_state"], row["nature_state"],
        )
        register_profile(expected_profiles, row["asn"], profile)
        severity = 0 if row["event_classification"] == "route_interrupted" else 1
        current_sort = (severity, -row["peak_complete_prefix_count"], -row["path_downstream_asn_count"], row["asn"])
        assert previous_sort is None or previous_sort < current_sort
        previous_sort = current_sort
        assert row["asn"] not in affected_by_asn
        affected_by_asn[row["asn"]] = row

    relations = {}
    previous_key = None
    unique_downstreams = set()
    for row in downstream_rows:
        assert row["schema_version"] == "rrc25-event-path-downstream/v1"
        assert row["event_as_path_id"] == event["event_as_path_id"]
        assert row["event_metric_id"] == event["event_metric_id"]
        assert row["cohort_id"] == event["cohort_id"]
        assert row["path_relationship_semantics"] == "ordered_rrc25_as_path_contains_affected_as_before_known_origin"
        key = (row["affected_asn"], row["downstream_asn"])
        assert previous_key is None or previous_key < key
        previous_key = key
        assert key not in relations and key[0] in affected_by_asn and key[0] != key[1]
        assert row["observed_path_count"] > 0
        assert row["associated_fixed_prefix_count"] > 0
        assert row["independent_direction_count"] > 0
        assert row["route_observation_count"] > 0
        if row["concurrent_state_point_count"] == 0:
            assert row["first_concurrent_state_point_utc"] is None
            assert row["last_concurrent_state_point_utc"] is None
            assert row["peak_concurrent_interrupted_prefix_count"] == 0
            assert row["peak_concurrent_ipv4_address_count"] == 0
            assert row["peak_concurrent_ipv6_slash48_count"] == 0
        else:
            assert row["first_concurrent_state_point_utc"] is not None
            assert row["last_concurrent_state_point_utc"] is not None
            assert row["first_concurrent_state_point_utc"] <= row["last_concurrent_state_point_utc"]
            assert row["peak_concurrent_interrupted_prefix_count"] > 0
            assert row["peak_concurrent_ipv4_address_count"] > 0 or row["peak_concurrent_ipv6_slash48_count"] > 0
        profile = (
            row["downstream_as_name"], row["downstream_organization"], row["downstream_nature"],
            row["downstream_name_state"], row["downstream_organization_state"], row["downstream_nature_state"],
        )
        assert profile[3] == ("unknown" if profile[0] is None else "observed")
        assert profile[4] == ("unknown" if profile[1] is None else "observed")
        assert profile[5] == ("unknown" if profile[2] is None else "observed")
        register_profile(expected_profiles, row["downstream_asn"], profile)
        relations[key] = {
            "row": row, "paths": set(), "members": set(), "peers": set(), "observations": 0,
            "current": 0, "ipv4": Coverage("ipv4"), "ipv6": Coverage("ipv6"),
            "points": 0, "first": None, "last": None,
            "peak_prefixes": 0, "peak_ipv4": 0, "peak_ipv6": 0,
        }
        unique_downstreams.add(row["downstream_asn"])
    assert len(unique_downstreams) == event["path_downstream_asn_count"]

    member_from_evidence = {}
    previous_evidence = None
    evidence_count = 0
    for row in verified_rows(ROOT, event["path_evidence"]):
        evidence_count += 1
        assert row["schema_version"] == "rrc25-event-path-evidence/v1"
        assert row["event_as_path_id"] == event["event_as_path_id"]
        assert row["cohort_id"] == event["cohort_id"]
        assert row["relationship_state"] == "observed_ordered_path_association"
        key = (row["affected_asn"], row["downstream_asn"])
        assert key in relations
        evidence_sort = (key[0], key[1], row["prefix"], row["as_path_id"])
        assert previous_evidence is None or previous_evidence <= evidence_sort
        previous_evidence = evidence_sort
        network = ipaddress.ip_network(row["prefix"], strict=True)
        assert str(network) == row["prefix"]
        assert row["address_family"] == ("ipv4" if network.version == 4 else "ipv6")
        sequence = row["as_path_canonical"].split()
        assert sequence and all(value.isdigit() for value in sequence)
        numbers = [int(value) for value in sequence]
        assert numbers[-1] == row["downstream_asn"]
        assert row["affected_asn"] in numbers[:-1]
        assert row["independent_peer_asns"] == sorted(set(row["independent_peer_asns"]))
        assert row["independent_peer_asns"] and row["route_observation_count"] > 0
        member_value = (row["prefix"], row["address_family"])
        assert member_from_evidence.setdefault(row["cohort_member_id"], member_value) == member_value
        relation = relations[key]
        relation["paths"].add(row["as_path_id"])
        relation["members"].add(row["cohort_member_id"])
        relation["peers"].update(row["independent_peer_asns"])
        relation["observations"] += row["route_observation_count"]
    assert evidence_count == event["path_evidence_count"]

    for relation in relations.values():
        row = relation["row"]
        assert len(relation["paths"]) == row["observed_path_count"]
        assert len(relation["members"]) == row["associated_fixed_prefix_count"]
        assert len(relation["peers"]) == row["independent_direction_count"]
        assert relation["observations"] == row["route_observation_count"]

    member_meta = {}
    fixed_prefixes_by_asn = collections.Counter()
    for row in verified_rows(COHORT_ROOT, cohort["members"]):
        member_id = row["cohort_member_id"]
        value = (row["prefix"], row["address_family"], tuple(row["country_origin_asns"]))
        assert member_id not in member_meta
        member_meta[member_id] = value
        for asn in value[2]:
            fixed_prefixes_by_asn[asn] += 1
    assert len(member_meta) == cohort["member_count"] == metric["fixed_prefix_count"]
    for member_id, value in member_from_evidence.items():
        assert member_id in member_meta and member_meta[member_id][:2] == value

    reverse_relations = collections.defaultdict(list)
    for key, relation in relations.items():
        for member_id in relation["members"]:
            reverse_relations[member_id].append(key)

    prefix_state = {}
    asn_state = {}
    invisible_by_asn = collections.Counter()
    peak_invisible_by_asn = collections.Counter()
    summaries = {}
    prefix_iterator = iter(verified_rows(METRIC_ROOT, metric["prefix_states"]))
    asn_iterator = iter(verified_rows(METRIC_ROOT, metric["asn_states"]))
    prefix_next = next(prefix_iterator, None)
    asn_next = next(asn_iterator, None)
    previous_slot = None
    points = 0

    def apply_prefix(row):
        member_id = row["cohort_member_id"]
        assert member_id in member_meta
        prefix, family, origins = member_meta[member_id]
        assert row["prefix"] == prefix and row["address_family"] == family
        assert row["expected_direction_count"] == row["visible_direction_count"] + row["invisible_direction_count"] + row["unknown_direction_count"]
        old = prefix_state.get(member_id)
        if old is None:
            assert row["record_kind"] == "baseline"
        else:
            for asn in origins:
                invisible_by_asn[asn] -= old[1]
                assert invisible_by_asn[asn] >= 0
            if interrupted(old[0]):
                for key in reverse_relations.get(member_id, ()):
                    relation = relations[key]
                    relation["current"] -= 1
                    relation[family].adjust(prefix, -1)
        value = (row["classification"], row["invisible_direction_count"])
        prefix_state[member_id] = value
        for asn in origins:
            invisible_by_asn[asn] += value[1]
        if interrupted(value[0]):
            for key in reverse_relations.get(member_id, ()):
                relation = relations[key]
                relation["current"] += 1
                relation[family].adjust(prefix, 1)

    def apply_asn(row):
        assert row["fixed_prefix_count"] == row["normal_prefix_count"] + row["partial_prefix_count"] + row["complete_prefix_count"] + row["unknown_prefix_count"]
        if row["unknown_prefix_count"]:
            assert row["classification"] == "unknown"
        elif row["complete_prefix_count"] == row["fixed_prefix_count"]:
            assert row["classification"] == "route_interrupted"
        elif row["partial_prefix_count"] + row["complete_prefix_count"]:
            assert row["classification"] == "affected"
        else:
            assert row["classification"] == "normal"
        current = summaries.setdefault(row["asn"], {
            "fixed": row["fixed_prefix_count"], "peak_partial": 0, "peak_complete": 0,
            "ever_affected": False, "ever_route": False,
        })
        assert current["fixed"] == row["fixed_prefix_count"]
        current["peak_partial"] = max(current["peak_partial"], row["partial_prefix_count"])
        current["peak_complete"] = max(current["peak_complete"], row["complete_prefix_count"])
        current["ever_affected"] |= row["classification"] in {"affected", "route_interrupted"}
        current["ever_route"] |= row["classification"] == "route_interrupted"
        asn_state[row["asn"]] = row["classification"]

    for point in verified_rows(METRIC_ROOT, metric["series"]):
        slot = point["state_slot"]
        if previous_slot is not None:
            assert slot == previous_slot + 1
        previous_slot = slot
        while prefix_next is not None and prefix_next["state_slot"] == slot:
            apply_prefix(prefix_next)
            prefix_next = next(prefix_iterator, None)
        while asn_next is not None and asn_next["state_slot"] == slot:
            apply_asn(asn_next)
            asn_next = next(asn_iterator, None)
        for asn in affected_by_asn:
            peak_invisible_by_asn[asn] = max(peak_invisible_by_asn[asn], invisible_by_asn[asn])
        for key, relation in relations.items():
            if asn_state.get(key[0]) not in {"affected", "route_interrupted"} or relation["current"] == 0:
                continue
            relation["points"] += 1
            relation["first"] = relation["first"] or point["state_point_utc"]
            relation["last"] = point["state_point_utc"]
            relation["peak_prefixes"] = max(relation["peak_prefixes"], relation["current"])
            relation["peak_ipv4"] = max(relation["peak_ipv4"], relation["ipv4"].covered)
            relation["peak_ipv6"] = max(relation["peak_ipv6"], relation["ipv6"].covered)
        points += 1
    assert prefix_next is None and asn_next is None
    assert points == metric["state_point_count"]
    assert len(prefix_state) == metric["fixed_prefix_count"]
    assert len(asn_state) == metric["fixed_asn_count"]

    expected_affected = {asn for asn, summary in summaries.items() if summary["ever_affected"]}
    assert expected_affected == set(affected_by_asn)
    for asn, row in affected_by_asn.items():
        summary = summaries[asn]
        assert row["fixed_prefix_count"] == summary["fixed"] == fixed_prefixes_by_asn[asn]
        assert row["peak_partial_prefix_count"] == summary["peak_partial"]
        assert row["peak_complete_prefix_count"] == summary["peak_complete"]
        assert row["peak_invisible_direction_count"] == peak_invisible_by_asn[asn]
        expected_class = "route_interrupted" if summary["ever_route"] else "affected"
        assert row["event_classification"] == expected_class
        assert row["path_downstream_asn_count"] == sum(1 for key in relations if key[0] == asn)
        assert row["concurrent_downstream_asn_count"] == sum(1 for key, value in relations.items() if key[0] == asn and value["points"] > 0)

    for relation in relations.values():
        row = relation["row"]
        assert row["concurrent_state_point_count"] == relation["points"]
        assert row["first_concurrent_state_point_utc"] == relation["first"]
        assert row["last_concurrent_state_point_utc"] == relation["last"]
        assert row["peak_concurrent_interrupted_prefix_count"] == relation["peak_prefixes"]
        assert row["peak_concurrent_ipv4_address_count"] == relation["peak_ipv4"]
        assert row["peak_concurrent_ipv6_slash48_count"] == relation["peak_ipv6"]

    assert event["route_interrupted_as_count"] == sum(row["event_classification"] == "route_interrupted" for row in affected_rows)
    assert event["affected_only_as_count"] == sum(row["event_classification"] == "affected" for row in affected_rows)
    assert event["unknown_static_name_count"] == sum(row["name_state"] == "unknown" for row in affected_rows)
    assert event["unknown_static_organization_count"] == sum(row["organization_state"] == "unknown" for row in affected_rows)
    assert event["unknown_static_nature_count"] == sum(row["nature_state"] == "unknown" for row in affected_rows)
    assert event["concurrent_downstream_relation_count"] == sum(value["points"] > 0 for value in relations.values())
    totals["events"] += 1
    totals["affected_as"] += len(affected_rows)
    totals["relations"] += len(relations)
    totals["concurrent_relations"] += event["concurrent_downstream_relation_count"]
    totals["evidence_rows"] += evidence_count
    totals["known_observations"] += event["known_as_path_observation_count"]
    totals["unknown_observations"] += event["unknown_as_path_observation_count"]
    for code, reference in SAMPLES.items():
        if event["legacy_reference"] == reference:
            samples[code] = {
                "legacy_reference": reference,
                "country_code": event["country_code"],
                "affected_as": len(affected_rows),
                "route_interrupted_as": event["route_interrupted_as_count"],
                "relations": len(relations),
                "concurrent_relations": event["concurrent_downstream_relation_count"],
                "evidence_rows": evidence_count,
                "top_affected_as": [
                    {"asn": row["asn"], "classification": row["event_classification"], "downstreams": row["path_downstream_asn_count"]}
                    for row in affected_rows[:5]
                ],
            }

assert set(samples) == set(SAMPLES)
assert totals["events"] == manifest["event_count"] == len(manifest["events"])
assert totals["affected_as"] == manifest["affected_as_count"]
assert totals["relations"] == manifest["path_downstream_relation_count"]
assert totals["concurrent_relations"] == manifest["concurrent_downstream_relation_count"]
assert totals["evidence_rows"] == manifest["path_evidence_count"]
assert totals["known_observations"] == manifest["known_as_path_observation_count"]
assert totals["unknown_observations"] == manifest["unknown_as_path_observation_count"]

snapshot_profiles = {}
with open(AS_SNAPSHOT, "r", encoding="utf-8", newline="") as stream:
    reader = csv.DictReader(stream)
    required = {"asn", "as_name", "org_name", "org_name_cn", "type", "type_cn"}
    assert required <= set(reader.fieldnames or ())
    for row in reader:
        try:
            asn = int(row["asn"].strip())
        except (TypeError, ValueError):
            continue
        if asn not in expected_profiles or asn in snapshot_profiles or not 0 <= asn <= 4294967295:
            continue
        organization = nullable(row["org_name_cn"]) or nullable(row["org_name"])
        nature = nullable(row["type_cn"]) or nullable(row["type"])
        snapshot_profiles[asn] = profile_tuple(nullable(row["as_name"]), organization, nature)
for asn, expected in expected_profiles.items():
    actual = snapshot_profiles.get(asn, profile_tuple(None, None, None))
    assert actual == expected, (asn, actual, expected)

print(json.dumps({
    "status": "pass",
    "as_path": {
        "dataset_id": manifest["dataset_id"],
        "content_sha256": manifest["content_sha256"],
        "manifest_sha256": hashlib.sha256(manifest_raw).hexdigest(),
        "implementation_id": manifest["implementation_id"],
        "source_route_event_dataset_id": manifest["source_route_event_dataset_id"],
        "source_route_event_content_sha256": manifest["source_route_event_content_sha256"],
        "source_route_event_manifest_sha256": manifest["source_route_event_manifest_sha256"],
        "source_event_cohort_dataset_id": manifest["source_event_cohort_dataset_id"],
        "source_event_cohort_content_sha256": manifest["source_event_cohort_content_sha256"],
        "source_event_cohort_manifest_sha256": manifest["source_event_cohort_manifest_sha256"],
        "source_event_metric_dataset_id": manifest["source_event_metric_dataset_id"],
        "source_event_metric_content_sha256": manifest["source_event_metric_content_sha256"],
        "source_event_metric_manifest_sha256": manifest["source_event_metric_manifest_sha256"],
        "as_attribute_snapshot_sha256": manifest["as_attribute_snapshot_sha256"],
        "as_attribute_snapshot_size_bytes": manifest["as_attribute_snapshot_size_bytes"],
        "events": totals["events"],
        "affected_as": totals["affected_as"],
        "relations": totals["relations"],
        "concurrent_relations": totals["concurrent_relations"],
        "evidence_rows": totals["evidence_rows"],
        "known_observations": totals["known_observations"],
        "unknown_observations": totals["unknown_observations"],
        "resolved_paths": manifest["resolved_as_path_count"],
        "path_partitions": manifest["scanned_as_path_partition_count"],
        "path_rows_scanned": manifest["scanned_as_path_row_count"],
        "static_profile_asns_checked": len(expected_profiles),
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
assert audit["schema_version"] == "country_outage_generalization_s3_formal_audit/v1"
assert audit["status"] == "complete"
assert audit["content_sha256"] == content
assert audit["audit_id"] == "country_outage_s3_audit_v1_" + content[:32]
manifest = raw(os.path.join(ROOT, "manifest.json"))
assert manifest == raw(os.path.join(ROOT, "COMPLETE.json"))
assert hashlib.sha256(manifest).hexdigest() == audit["evidence"]["as_path"]["manifest_sha256"]
assert json.loads(manifest)["content_sha256"] == audit["evidence"]["as_path"]["content_sha256"]
print(json.dumps(audit["evidence"], ensure_ascii=False, separators=(",", ":")))
'''


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def verify_local() -> tuple[list[str], list[str]]:
    errors: list[str] = []
    checks: list[str] = []
    for path in (ACCEPTANCE_RECORD, AS_PATH_SCHEMA):
        if not path.is_file():
            errors.append(f"缺少 S3 文件：{path.relative_to(REPOSITORY_ROOT)}")
    if errors:
        return errors, checks
    record = read_text(ACCEPTANCE_RECORD)
    for phrase in REQUIRED_RECORD_PHRASES:
        if phrase not in record:
            errors.append(f"S3 验收记录缺少：{phrase}")
    checks.append("S3 验收记录包含冻结属性、路径关联人口、因果边界与 Hook 结论")
    try:
        schema = json.loads(read_text(AS_PATH_SCHEMA))
    except json.JSONDecodeError as error:
        errors.append(f"S3 合同不是合法 JSON：{error}")
    else:
        if schema.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
            errors.append("S3 合同版本漂移")
        if schema.get("additionalProperties") is not False:
            errors.append("S3 合同顶层必须拒绝额外字段")
        for name in ("event", "affected_as_row", "downstream_row", "path_evidence_row"):
            if schema.get("$defs", {}).get(name, {}).get("additionalProperties") is not False:
                errors.append(f"S3 合同定义未关闭额外字段：{name}")
    checks.append("S3 制品与三类明细行均使用封闭 JSON Schema")
    for relative, phrases in LOCAL_SOURCE_EXPECTATIONS.items():
        content = read_text(REPOSITORY_ROOT / relative)
        for phrase in phrases:
            if phrase not in content:
                errors.append(f"S3 源码语义漂移：{relative} 缺少 {phrase}")
    checks.append("AS 属性快照、真实有序路径、并发子集与非因果边界仍由源码约束")
    for phrase in (
        "customer cone 已完成", "AS relationship 已完成", "确认依赖", "确认故障传播",
        "根因是", "API 已完成", "页面已上线", "TODO", "待正式投影完成后冻结",
    ):
        if phrase in record:
            errors.append(f"S3 验收记录越级或改变事实：{phrase}")
    checks.append("S3 未越级声明关系推断、API、页面、部署或生产效果")
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
        raise RuntimeError(f"远端 S3 审计失败：{detail or '无错误详情'}")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"远端 S3 审计输出无效：{error}") from error
    if not isinstance(payload, dict):
        raise RuntimeError("远端 S3 审计输出必须是对象")
    return payload


def create_remote_audit() -> dict[str, Any]:
    evidence = run_remote_script(REMOTE_DEEP_AUDIT_SCRIPT, 3600)
    if evidence.get("status") != "pass":
        raise RuntimeError(f"远端 S3 深审未通过：{evidence}")
    payload: dict[str, Any] = {
        "schema_version": "country_outage_generalization_s3_formal_audit/v1",
        "status": "complete",
        "evidence": evidence,
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    content_sha = hashlib.sha256(canonical).hexdigest()
    payload["content_sha256"] = content_sha
    payload["audit_id"] = "country_outage_s3_audit_v1_" + content_sha[:32]
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8") + b"\n"
    encoded = base64.b64encode(raw).decode("ascii")
    writer = f'''
import base64
import os
path = {REMOTE_AUDIT_PATH!r}
raw = base64.b64decode({encoded!r})
if os.path.exists(path):
    if open(path, "rb").read() != raw:
        raise SystemExit("S3-AUDIT.json 已存在且内容不同")
else:
    temporary = path + ".tmp"
    if os.path.exists(temporary):
        raise SystemExit("S3-AUDIT.json 临时文件已存在")
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
        raise RuntimeError((result.stdout + result.stderr).strip() or "无法写入正式 S3 审计")
    return payload


def validate_remote(payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if payload.get("status") != "pass":
        return [f"远端 S3 深审未通过：{payload}"]
    value = payload.get("as_path", {})
    pairs = (
        ("dataset_id", "dataset_id"),
        ("content_sha256", "content_sha256"),
        ("manifest_sha256", "manifest_sha256"),
        ("implementation_id", "implementation_id"),
        ("source_route_event_dataset_id", "route_event_dataset_id"),
        ("source_route_event_content_sha256", "route_event_content_sha256"),
        ("source_route_event_manifest_sha256", "route_event_manifest_sha256"),
        ("source_event_cohort_dataset_id", "cohort_dataset_id"),
        ("source_event_cohort_content_sha256", "cohort_content_sha256"),
        ("source_event_cohort_manifest_sha256", "cohort_manifest_sha256"),
        ("source_event_metric_dataset_id", "metric_dataset_id"),
        ("source_event_metric_content_sha256", "metric_content_sha256"),
        ("source_event_metric_manifest_sha256", "metric_manifest_sha256"),
        ("as_attribute_snapshot_sha256", "as_snapshot_sha256"),
        ("as_attribute_snapshot_size_bytes", "as_snapshot_size"),
        ("events", "events"),
        ("affected_as", "affected_as"),
        ("relations", "relations"),
        ("concurrent_relations", "concurrent_relations"),
        ("evidence_rows", "evidence_rows"),
        ("known_observations", "known_observations"),
        ("unknown_observations", "unknown_observations"),
        ("resolved_paths", "resolved_paths"),
        ("path_partitions", "path_partitions"),
        ("path_rows_scanned", "path_rows_scanned"),
    )
    for actual, expected in pairs:
        if value.get(actual) != EXPECTED[expected]:
            errors.append(f"S3 正式身份或人口冲突：{actual}={value.get(actual)!r}")
    samples = payload.get("samples", {})
    if set(samples) != {"IR", "MW"}:
        errors.append("S3 伊朗/马拉维样本缺失")
    else:
        for code in ("IR", "MW"):
            sample = samples[code]
            if sample.get("country_code") != code or sample.get("affected_as", 0) < 1:
                errors.append(f"S3 {code} 样本人口冲突")
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
            checks.append("81 个事件的 AS 属性、排序、路径、并发子集、资源量与源制品逐项深审")
    return {
        "schema_version": "country_outage_generalization_s3_verification_v1",
        "status": "pass" if not errors else "fail",
        "stage": "S3",
        "check_count": len(checks),
        "checks": checks,
        "remote_evidence": remote,
        "errors": errors,
    }


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--create-remote-audit", action="store_true",
        help="对正式制品执行一次逐行深审并以 create-only 方式冻结审计结果。",
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
