#!/usr/bin/env python3
"""机器核对国家中断通用观测页 S1 会话事实与事件前 cohort。"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
ACCEPTANCE_RECORD = REPOSITORY_ROOT / "docs" / "国家中断通用观测页S1验收记录.md"
PEER_SCHEMA = REPOSITORY_ROOT / "contracts" / "data" / "rrc25-peer-session-store.schema.json"
COHORT_SCHEMA = REPOSITORY_ROOT / "contracts" / "data" / "rrc25-event-cohort-store.schema.json"
REMOTE_HOST = "root@10.99.8.16"
REMOTE_AUDIT_PATH = (
    "/home/bgpdata/Domeye-Core-dev-data/research-runs/"
    "country-outage-general-page-s1-711350f/event-cohorts/S1-AUDIT.json"
)

EXPECTED = {
    "implementation_id": "git:711350f1cafd03dea744ad59424ec00ce67d169e",
    "peer_implementation_id": "git:cff819aaef742dc7bc062e043a666c8666c3f59a",
    "route_event_dataset_id": "route_event_dataset_v1_a408005061499629321017426e99a629",
    "route_event_content_sha256": "9c30e8bce04c83f731c77df239976590be804c8b3ac6799d9da7253c1682849a",
    "route_event_manifest_sha256": "661e5184242b29c164200789ede09938c224cb08a3beb40e8e99dfe2b1f9fbc5",
    "route_state_dataset_id": "route_state_dataset_v1_c2f7f7c7c63c824f4e92ed4c90787bcb",
    "route_state_content_sha256": "54ffc35d94cd3d7a7b195afc664110303fe07a723079bbcf1fb52a0a0b8be7c4",
    "route_state_manifest_sha256": "f810c354b9dd87cdd62ae51b24281f72eac1344552b6616b8cf70b542433b587",
    "mapping_version": "41fa4721c1c8f5eb4fe120987eb9672d32382d694889990b93028f4c881f63c4",
    "mapping_compatible_sha256": "05b9809116c3525769e8dc2bd52497ff810a5b4d063cf3c93442d23ed119f9d5",
    "mapping_revised_sha256": "0c20c3f522170d0838466ab9fa8da729abf60767fe820038efc73a3f62dd510e",
    "peer_dataset_id": "peer_session_dataset_v1_982f87e788af6128b98b7c8107485a74",
    "peer_content_sha256": "185342c0b04b482c7bf04157515148687e88490d1b226e034c701dbbd722878e",
    "peer_manifest_sha256": "c57bae8bab297ad6a84801d4c3217bf6371b9e8cf4a7b9652ace982eed04030b",
    "lifecycle_snapshot_id": "event_lifecycle_snapshot_v1_7a76c506bd8641406c0d87ba2fdd98f4",
    "lifecycle_content_sha256": "7a76c506bd8641406c0d87ba2fdd98f4bb4ecd5da29116e0ef8ea87e412b3426",
    "lifecycle_file_sha256": "1ca402ca2d8ceeae39cf5ffc32dfba9a521af1874f539ba0155ff8793fa2f9cf",
    "cohort_dataset_id": "event_cohort_dataset_v1_11c18b460a735c1acfa5f925d09c1bd8",
    "cohort_content_sha256": "75d53ae4ba355c859d70b79aabf3ca597915fbfc66239f9eaa050b7c6004dd6c",
    "cohort_manifest_sha256": "3bebb14181912e645e0e1d25439edda9be2e327e059e715655852d102455fef6",
}

LOCAL_SOURCE_EXPECTATIONS = {
    "tools/rrc25-iran-replay-go/peer_session_store.go": (
        'PeerSessionStoreVersion     = "rrc25-peer-session-store/v1"',
        "record.Subtype != 0 && record.Subtype != 5",
        'PrefixWithdrawalInference: "not_permitted"',
        'ObservationSemantics: "single_peer_session_transition"',
    ),
    "tools/rrc25-iran-replay-go/event_cohort_store.go": (
        'EventCohortStoreVersion     = "rrc25-event-cohort-store/v1"',
        "LoadRouteStateCheckpoint",
        "applyEventCohortRouteState",
        "one_expected_direction_per_unique_rrc25_peer_asn",
        "prefix_selected_by_country_origin_then_all_visible_peer_asn_directions_frozen",
        "peer_session_down_never_materializes_or_implies_a_route_withdrawal",
        "new_prefixes_or_directions_after_detection_do_not_change_the_frozen_cohort_denominator",
    ),
    "dev/data_quality/country_outage_event_lifecycle_snapshot.py": (
        'SCHEMA_VERSION = "country_outage_event_lifecycle_snapshot/v1"',
        "previous_complete_state_point",
        "requested_window_start = cohort_point - timedelta(hours=1)",
        'lifecycle_state = "event_end_unknown"',
    ),
}

REQUIRED_RECORD_PHRASES = (
    "版本：2.1",
    "RRC25-only",
    "832,942,411",
    "970,176",
    "142 个完整会话身份",
    "81 个 peer ASN",
    "72 个范围内结束",
    "4 个范围外结束",
    "5 个结束未知",
    "不会把 peer down 写成 WITHDRAW",
    "同一 peer ASN 的多个会话只形成一个独立方向",
    "RouteState 仍是唯一状态事实",
    "81 个事件前固定 cohort",
    "通用观测页最终验收回检：S1 一致",
)


REMOTE_DEEP_AUDIT_SCRIPT = r'''
import collections
import datetime as dt
import gzip
import hashlib
import json
import os
import re

PEER_ROOT = "/home/bgpdata/Domeye-Core-dev-data/research-runs/country-outage-general-page-s1-cff819a/peer-sessions"
COHORT_ROOT = "/home/bgpdata/Domeye-Core-dev-data/research-runs/country-outage-general-page-s1-711350f/event-cohorts"
LIFECYCLE_PATH = "/home/bgpdata/Domeye-Core-dev-data/research-runs/country-outage-general-page-s1-cff819a/event-lifecycle-snapshot.json"
ROUTE_EVENT_ROOT = "/home/bgpdata/Domeye-Core-dev-data/research-runs/rrc25-route-events-224-310-s1-843797e"
ROUTE_STATE_ROOT = "/home/bgpdata/Domeye-Core-dev-data/research-runs/rrc25-route-state-224-310-s2-0a0a322"
COMPATIBLE_MAPPING_PATH = "/home/bgpdata/Domeye-Core-dev-data/research-runs/iran-rrc25-full-p0/20260723T094940Z-full-p0/prepared-v3/compatible-mapping.json"
REVISED_MAPPING_PATH = "/home/bgpdata/Domeye-Core-dev-data/research-runs/iran-rrc25-full-p0/20260723T094940Z-full-p0/prepared-v3/revised-mapping.json"
WINDOW_START = dt.datetime.fromisoformat("2026-02-24T00:00:00+00:00")
WINDOW_END = dt.datetime.fromisoformat("2026-03-11T00:00:00+00:00")
SHA = re.compile(r"^[0-9a-f]{64}$")
SAMPLE_POINTS = {
    "IR": "2026-02-27T01:10:00Z",
    "MW": "2026-03-09T14:05:00Z",
}

def load(path):
    with open(path, "rb") as stream:
        raw = stream.read()
    return json.loads(raw), raw

def twin(root):
    manifest, left = load(os.path.join(root, "manifest.json"))
    complete, right = load(os.path.join(root, "COMPLETE.json"))
    assert left == right and manifest == complete
    return manifest, hashlib.sha256(left).hexdigest()

def verify_file(root, meta):
    path = os.path.join(root, meta["path"])
    assert os.path.isfile(path) and not os.path.islink(path)
    assert os.path.getsize(path) == meta["size_bytes"]
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    assert digest.hexdigest() == meta["sha256"]
    return path

def utc(value):
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))

def audit():
    lifecycle, lifecycle_raw = load(LIFECYCLE_PATH)
    assert lifecycle["event_count"] == 81
    assert lifecycle["lifecycle_state_counts"] == {
        "event_end_outside_data_range": 4,
        "event_end_recorded": 72,
        "event_end_unknown": 5,
    }
    assert len({event["legacy_reference"] for event in lifecycle["events"]}) == 81

    route_events, route_event_raw = load(os.path.join(ROUTE_EVENT_ROOT, "manifest.json"))
    peer, peer_manifest_sha = twin(PEER_ROOT)
    assert peer["update_artifact_count"] == len(peer["partitions"]) == 4320
    assert peer["prefix_withdrawal_inference"] == "not_permitted"
    assert peer["observation_semantics"] == "single_peer_session_transition"
    assert peer["source_route_event_dataset_id"] == route_events["dataset_id"]
    physical = observations = 0
    transition_counts = collections.Counter()
    session_ids = set()
    peer_asns = set()
    for index, partition in enumerate(peer["partitions"]):
        assert partition["artifact_index"] == index
        assert partition["artifact"] == route_events["partitions"][index + 1]["artifact"]
        assert partition["physical_record_count"] == route_events["partitions"][index + 1]["physical_record_count"]
        disk, _ = load(os.path.join(PEER_ROOT, "partitions", f"{index:04d}", "manifest.json"))
        assert disk == partition
        path = verify_file(PEER_ROOT, partition["observations"])
        slot_start = utc(partition["artifact"]["artifact_time_utc"])
        rows = 0
        local_transitions = collections.Counter()
        with gzip.open(path, "rt", encoding="utf-8") as stream:
            for line in stream:
                row = json.loads(line)
                assert row["collector_id"] == "rrc25"
                assert row["semantics"] == "single_peer_session_transition"
                assert row["prefix_withdrawal_inference"] == "not_permitted"
                assert 1 <= row["old_state"] <= 6 and 1 <= row["new_state"] <= 6
                assert slot_start <= utc(row["event_time_utc"]) < slot_start + dt.timedelta(minutes=5)
                assert row["artifact_id"] == partition["artifact"]["artifact_id"]
                assert row["file_sha256"] == partition["artifact"]["file_sha256"]
                assert SHA.fullmatch(row["raw_record_sha256"])
                session_ids.add(row["session_id"])
                peer_asns.add(row["peer_asn"])
                local_transitions[f'{row["old_state"]}->{row["new_state"]}'] += 1
                rows += 1
        assert rows == partition["observation_count"] == partition["observations"]["row_count"]
        assert dict(local_transitions) == partition["transition_counts"]
        physical += partition["physical_record_count"]
        observations += rows
        transition_counts.update(local_transitions)
    assert physical == peer["physical_record_count"]
    assert observations == peer["observation_count"]
    assert len(session_ids) == peer["unique_session_count"]
    assert len(peer_asns) == peer["unique_peer_asn_count"]
    assert dict(transition_counts) == peer["transition_counts"]

    route_state, route_state_raw = load(os.path.join(ROUTE_STATE_ROOT, "manifest.json"))
    compatible_mapping, compatible_mapping_raw = load(COMPATIBLE_MAPPING_PATH)
    revised_mapping, revised_mapping_raw = load(REVISED_MAPPING_PATH)
    country_by_asn = {row["asn"]: row["country_code"] for row in compatible_mapping["rows"]}
    country_by_asn.update({row["asn"]: row["country_code"] for row in revised_mapping["rows"]})
    formal_slots = {}
    for name in ("slots-0001-2160.jsonl.gz", "slots-2161-4320.jsonl.gz"):
        with gzip.open(os.path.join(ROUTE_STATE_ROOT, "slot-ledgers", name), "rt", encoding="utf-8") as stream:
            for line in stream:
                row = json.loads(line)
                formal_slots[row["slot"]] = row
    checkpoint0, _ = load(os.path.join(ROUTE_STATE_ROOT, "checkpoints", "slot-0000", "manifest.json"))
    cohort, cohort_manifest_sha = twin(COHORT_ROOT)
    assert cohort["event_count"] == 81
    assert cohort["unique_cohort_count"] == len(cohort["cohorts"]) == 81
    assert cohort["route_state_authority"] == "the_existing_route_state_dataset_is_the_only_route_state_fact"
    assert cohort["session_route_boundary"] == "peer_session_down_never_materializes_or_implies_a_route_withdrawal"
    assert cohort["direction_definition"] == "one_independent_direction_is_one_rrc25_peer_asn_and_multiple_bgp_sessions_do_not_expand_the_denominator"
    assert cohort["source_route_state_dataset_id"] == route_state["dataset_id"]
    assert cohort["source_peer_session_dataset_id"] == peer["dataset_id"]
    assert cohort["source_peer_session_content_sha256"] == peer["content_sha256"]
    assert cohort["mapping_compatible_sha256"] == hashlib.sha256(compatible_mapping_raw).hexdigest()
    assert cohort["mapping_revised_sha256"] == hashlib.sha256(revised_mapping_raw).hexdigest()
    event_path = verify_file(COHORT_ROOT, cohort["events"])
    with gzip.open(event_path, "rt", encoding="utf-8") as stream:
        bindings = [json.loads(line) for line in stream]
    assert len(bindings) == cohort["events"]["row_count"] == 81
    binding_by_reference = {row["legacy_reference"]: row for row in bindings}
    assert len(binding_by_reference) == 81
    cohort_by_id = {row["cohort_id"]: row for row in cohort["cohorts"]}
    assert len(cohort_by_id) == 81
    for event in lifecycle["events"]:
        binding = binding_by_reference[event["legacy_reference"]]
        for key in (
            "incident_id", "country_code", "detected_at_utc", "cohort_state_point_utc",
            "window_start_utc", "requested_window_start_utc", "left_boundary_missing_slot_count",
            "event_end_at_utc", "event_duration_seconds", "projection_end_state_point_utc",
            "lifecycle_state", "is_final_in_data_range",
        ):
            assert binding[key] == event[key]
        assert binding["cohort_id"] in cohort_by_id
        state_point = utc(binding["cohort_state_point_utc"])
        detected = utc(binding["detected_at_utc"])
        assert state_point < detected and (detected - state_point) <= dt.timedelta(minutes=5)
        requested = state_point - dt.timedelta(hours=1)
        assert utc(binding["requested_window_start_utc"]) == requested
        assert utc(binding["window_start_utc"]) == max(requested, WINDOW_START)

    total_members = total_relations = total_observations = 0
    country_summaries = {}
    for listed in cohort["cohorts"]:
        directory = os.path.join(COHORT_ROOT, "cohorts", listed["country_code"], f'slot-{listed["cohort_state_slot"]:04d}')
        disk, _ = load(os.path.join(directory, "manifest.json"))
        assert disk == listed
        slot = listed["cohort_state_slot"]
        if slot == 0:
            assert listed["source_route_state_slot_sha256"] == checkpoint0["content_sha256"]
            assert listed["source_route_state_digest"] == checkpoint0["state_digest"]
        else:
            assert listed["source_route_state_slot_sha256"] == formal_slots[slot]["content_sha256"]
            assert listed["source_route_state_digest"] == formal_slots[slot]["state_digest"]
        assert listed["replayed_update_slot_count"] == slot - listed["replay_start_checkpoint_slot"]
        assert listed["population_semantics"] == "one_member_per_unique_prefix_and_address_family_selected_by_at_least_one_country_origin_route_then_frozen_with_all_visible_rrc25_directions"
        assert listed["direction_semantics"] == "one_expected_direction_per_unique_rrc25_peer_asn_that_sees_the_selected_prefix_regardless_of_observed_origin"
        path = verify_file(COHORT_ROOT, listed["members"])
        member_keys = set()
        country_origins = set()
        observed_origins = set()
        unknown_origin_observations = 0
        members = ipv4 = ipv6 = relations = route_observations = 0
        with gzip.open(path, "rt", encoding="utf-8") as stream:
            for line in stream:
                row = json.loads(line)
                assert row["cohort_id"] == listed["cohort_id"]
                assert row["country_code"] == listed["country_code"]
                key = (row["address_family"], row["prefix"])
                assert key not in member_keys
                member_keys.add(key)
                assert row["country_origin_asns"] == sorted(set(row["country_origin_asns"]))
                assert row["observed_origin_asns"] == sorted(set(row["observed_origin_asns"]))
                assert set(row["country_origin_asns"]) <= set(row["observed_origin_asns"])
                assert len(row["country_origin_asns"]) >= 1
                assert all(country_by_asn.get(asn, "ZZ") == listed["country_code"] for asn in row["country_origin_asns"])
                directions = row["expected_directions"]
                assert row["expected_direction_count"] == len(directions) >= 1
                assert [item["peer_asn"] for item in directions] == sorted({item["peer_asn"] for item in directions})
                member_observed_origins = set()
                member_unknown_origins = 0
                for direction in directions:
                    observations_in_direction = direction["route_observations"]
                    assert direction["route_observation_count"] == len(observations_in_direction) >= 1
                    assert len({item["peer_ip"] for item in observations_in_direction}) == len(observations_in_direction)
                    for observation in observations_in_direction:
                        assert observation["origin_status"] in {"known", "unknown"}
                        assert ("origin_asn" in observation) == (observation["origin_status"] == "known")
                        assert observation["as_path_status"] in {"known", "unknown"}
                        assert ("as_path_id" in observation) == (observation["as_path_status"] == "known")
                        if observation["origin_status"] == "known":
                            member_observed_origins.add(observation["origin_asn"])
                        else:
                            member_unknown_origins += 1
                    route_observations += len(observations_in_direction)
                assert member_observed_origins == set(row["observed_origin_asns"])
                assert member_unknown_origins == row["unknown_origin_route_observation_count"]
                country_origins.update(row["country_origin_asns"])
                observed_origins.update(member_observed_origins)
                unknown_origin_observations += member_unknown_origins
                relations += len(directions)
                members += 1
                ipv4 += row["address_family"] == "ipv4"
                ipv6 += row["address_family"] == "ipv6"
        assert members == listed["member_count"] == listed["members"]["row_count"]
        assert ipv4 == listed["ipv4_member_count"] and ipv6 == listed["ipv6_member_count"]
        assert members == ipv4 + ipv6
        assert len(country_origins) == listed["country_origin_asn_count"]
        assert len(observed_origins) == listed["observed_origin_asn_count"]
        assert unknown_origin_observations == listed["unknown_origin_observation_count"]
        assert relations == listed["expected_direction_relation_count"]
        assert route_observations == listed["route_observation_count"]
        total_members += members
        total_relations += relations
        total_observations += route_observations
        if listed["country_code"] in SAMPLE_POINTS and listed["cohort_state_point_utc"] == SAMPLE_POINTS[listed["country_code"]]:
            country_summaries[listed["country_code"]] = {
                "cohort_id": listed["cohort_id"],
                "state_point": listed["cohort_state_point_utc"],
                "members": members,
                "directions": relations,
                "route_observations": route_observations,
                "country_origin_asns": len(country_origins),
                "observed_origin_asns": len(observed_origins),
                "unknown_origin_observations": unknown_origin_observations,
            }
    assert total_members == cohort["cohort_member_count"]
    assert total_relations == cohort["expected_direction_relation_count"]
    assert total_observations == cohort["route_observation_count"]

    return {
        "status": "pass",
        "lifecycle": {
            "snapshot_id": lifecycle["snapshot_id"],
            "content_sha256": lifecycle["content_sha256"],
            "file_sha256": hashlib.sha256(lifecycle_raw).hexdigest(),
            "event_count": lifecycle["event_count"],
            "state_counts": lifecycle["lifecycle_state_counts"],
        },
        "peer": {
            "dataset_id": peer["dataset_id"], "content_sha256": peer["content_sha256"],
            "manifest_sha256": peer_manifest_sha, "implementation_id": peer["implementation_id"],
            "source_route_event_dataset_id": peer["source_route_event_dataset_id"],
            "source_route_event_content_sha256": peer["source_route_event_content_sha256"],
            "source_route_event_manifest_sha256": peer["source_route_event_manifest_sha256"],
            "actual_route_event_manifest_sha256": hashlib.sha256(route_event_raw).hexdigest(),
            "physical_records": physical, "observations": observations,
            "sessions": len(session_ids), "peer_asns": len(peer_asns),
            "transition_counts": dict(transition_counts),
        },
        "cohort": {
            "dataset_id": cohort["dataset_id"], "content_sha256": cohort["content_sha256"],
            "manifest_sha256": cohort_manifest_sha, "implementation_id": cohort["implementation_id"],
            "source_route_state_dataset_id": cohort["source_route_state_dataset_id"],
            "source_route_state_content_sha256": cohort["source_route_state_content_sha256"],
            "source_route_state_manifest_sha256": cohort["source_route_state_manifest_sha256"],
            "actual_route_state_manifest_sha256": hashlib.sha256(route_state_raw).hexdigest(),
            "mapping_version": cohort["mapping_version"],
            "mapping_compatible_sha256": cohort["mapping_compatible_sha256"],
            "mapping_revised_sha256": cohort["mapping_revised_sha256"],
            "event_count": cohort["event_count"], "unique_cohorts": cohort["unique_cohort_count"],
            "members": total_members, "directions": total_relations,
            "route_observations": total_observations,
        },
        "countries": country_summaries,
    }

try:
    print(json.dumps(audit(), ensure_ascii=False, separators=(",", ":")))
except Exception as error:
    print(json.dumps({"status": "fail", "error_type": type(error).__name__, "error": str(error)}, ensure_ascii=False))
    raise
'''

REMOTE_AUDIT_SCRIPT = r'''
import hashlib
import json
import os

AUDIT_PATH = "/home/bgpdata/Domeye-Core-dev-data/research-runs/country-outage-general-page-s1-711350f/event-cohorts/S1-AUDIT.json"
PEER_ROOT = "/home/bgpdata/Domeye-Core-dev-data/research-runs/country-outage-general-page-s1-cff819a/peer-sessions"
COHORT_ROOT = "/home/bgpdata/Domeye-Core-dev-data/research-runs/country-outage-general-page-s1-711350f/event-cohorts"
LIFECYCLE_PATH = "/home/bgpdata/Domeye-Core-dev-data/research-runs/country-outage-general-page-s1-cff819a/event-lifecycle-snapshot.json"

def raw(path):
    with open(path, "rb") as stream:
        return stream.read()

audit_raw = raw(AUDIT_PATH)
audit = json.loads(audit_raw)
copy = dict(audit)
copy.pop("content_sha256", None)
copy.pop("audit_id", None)
content = hashlib.sha256(json.dumps(copy, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
assert audit["schema_version"] == "country_outage_generalization_s1_formal_audit/v1"
assert audit["status"] == "complete"
assert audit["content_sha256"] == content
assert audit["audit_id"] == "country_outage_s1_audit_v1_" + content[:32]
evidence = audit["evidence"]
peer_manifest = raw(os.path.join(PEER_ROOT, "manifest.json"))
assert peer_manifest == raw(os.path.join(PEER_ROOT, "COMPLETE.json"))
cohort_manifest = raw(os.path.join(COHORT_ROOT, "manifest.json"))
assert cohort_manifest == raw(os.path.join(COHORT_ROOT, "COMPLETE.json"))
assert hashlib.sha256(peer_manifest).hexdigest() == evidence["peer"]["manifest_sha256"]
assert hashlib.sha256(cohort_manifest).hexdigest() == evidence["cohort"]["manifest_sha256"]
assert hashlib.sha256(raw(LIFECYCLE_PATH)).hexdigest() == evidence["lifecycle"]["file_sha256"]
assert json.loads(peer_manifest)["content_sha256"] == evidence["peer"]["content_sha256"]
assert json.loads(cohort_manifest)["content_sha256"] == evidence["cohort"]["content_sha256"]
print(json.dumps(evidence, ensure_ascii=False, separators=(",", ":")))
'''


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def verify_local() -> tuple[list[str], list[str]]:
    errors: list[str] = []
    checks: list[str] = []
    for key in ("cohort_dataset_id", "cohort_content_sha256", "cohort_manifest_sha256"):
        if str(EXPECTED[key]).startswith("__"):
            errors.append(f"S1 正式身份尚未冻结：{key}")
    for path in (ACCEPTANCE_RECORD, PEER_SCHEMA, COHORT_SCHEMA):
        if not path.is_file():
            errors.append(f"缺少 S1 文件：{path.relative_to(REPOSITORY_ROOT)}")
    if errors:
        return errors, checks
    record = read_text(ACCEPTANCE_RECORD)
    for phrase in REQUIRED_RECORD_PHRASES:
        if phrase not in record:
            errors.append(f"S1 验收记录缺少：{phrase}")
    checks.append("S1 验收记录包含正式人口、边界和 Hook 结论")
    for path in (PEER_SCHEMA, COHORT_SCHEMA):
        try:
            schema = json.loads(read_text(path))
        except json.JSONDecodeError as error:
            errors.append(f"合同不是合法 JSON：{path.name}：{error}")
            continue
        if schema.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
            errors.append(f"合同版本漂移：{path.name}")
        if schema.get("additionalProperties") is not False:
            errors.append(f"合同顶层必须拒绝额外字段：{path.name}")
    checks.append("会话与 cohort 合同均为封闭的 draft 2020-12 JSON Schema")
    for relative, phrases in LOCAL_SOURCE_EXPECTATIONS.items():
        path = REPOSITORY_ROOT / relative
        if not path.is_file():
            errors.append(f"S1 源码不存在：{relative}")
            continue
        content = read_text(path)
        for phrase in phrases:
            if phrase not in content:
                errors.append(f"S1 源码语义漂移：{relative} 缺少 {phrase}")
    checks.append("定向 STATE_CHANGE、唯一 RouteState、peer ASN 去重和未知边界仍由源码约束")
    forbidden = (
        "会话掉线等同前缀撤回",
        "重新生成全部 RouteEvent",
        "S2 指标已经完成",
        "页面已经上线",
        "待正式回放完成后冻结",
        "TODO",
    )
    for phrase in forbidden:
        if phrase in record:
            errors.append(f"S1 验收记录越级或改变事实：{phrase}")
    checks.append("S1 未越级声明 S2-S6 或伪造路由事实")
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
        raise RuntimeError(f"远端 S1 审计失败：{detail or '无错误详情'}")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"远端 S1 审计输出无效：{error}") from error
    if not isinstance(payload, dict):
        raise RuntimeError("远端 S1 审计输出必须是对象")
    return payload


def run_remote_audit() -> dict[str, Any]:
    return run_remote_script(REMOTE_AUDIT_SCRIPT, 30)


def create_remote_audit() -> dict[str, Any]:
    evidence = run_remote_script(REMOTE_DEEP_AUDIT_SCRIPT, 900)
    if evidence.get("status") != "pass":
        raise RuntimeError(f"远端 S1 深审未通过：{evidence}")
    payload: dict[str, Any] = {
        "schema_version": "country_outage_generalization_s1_formal_audit/v1",
        "status": "complete",
        "evidence": evidence,
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    content_sha = hashlib.sha256(canonical).hexdigest()
    payload["content_sha256"] = content_sha
    payload["audit_id"] = "country_outage_s1_audit_v1_" + content_sha[:32]
    raw = (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
        + b"\n"
    )
    encoded = base64.b64encode(raw).decode("ascii")
    writer = f'''\
import base64
import os
path = {REMOTE_AUDIT_PATH!r}
raw = base64.b64decode({encoded!r})
if os.path.exists(path):
    if open(path, "rb").read() != raw:
        raise SystemExit("S1-AUDIT.json 已存在且内容不同")
else:
    temporary = path + ".tmp"
    if os.path.exists(temporary):
        raise SystemExit("S1-AUDIT.json 临时文件已存在")
    with open(temporary, "xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    os.rename(temporary, path)
print("ok")
'''
    result = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", REMOTE_HOST, "python3", "-"],
        input=writer,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError((result.stdout + result.stderr).strip() or "无法写入正式 S1 审计")
    return payload


def validate_remote(payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if payload.get("status") != "pass":
        return [f"远端 S1 深审未通过：{payload}"]
    lifecycle = payload.get("lifecycle", {})
    peer = payload.get("peer", {})
    cohort = payload.get("cohort", {})
    expected_pairs = (
        (lifecycle, "snapshot_id", EXPECTED["lifecycle_snapshot_id"]),
        (lifecycle, "content_sha256", EXPECTED["lifecycle_content_sha256"]),
        (lifecycle, "file_sha256", EXPECTED["lifecycle_file_sha256"]),
        (peer, "dataset_id", EXPECTED["peer_dataset_id"]),
        (peer, "content_sha256", EXPECTED["peer_content_sha256"]),
        (peer, "manifest_sha256", EXPECTED["peer_manifest_sha256"]),
        (peer, "implementation_id", EXPECTED["peer_implementation_id"]),
        (peer, "source_route_event_dataset_id", EXPECTED["route_event_dataset_id"]),
        (peer, "source_route_event_content_sha256", EXPECTED["route_event_content_sha256"]),
        (peer, "source_route_event_manifest_sha256", EXPECTED["route_event_manifest_sha256"]),
        (peer, "actual_route_event_manifest_sha256", EXPECTED["route_event_manifest_sha256"]),
        (cohort, "dataset_id", EXPECTED["cohort_dataset_id"]),
        (cohort, "content_sha256", EXPECTED["cohort_content_sha256"]),
        (cohort, "manifest_sha256", EXPECTED["cohort_manifest_sha256"]),
        (cohort, "implementation_id", EXPECTED["implementation_id"]),
        (cohort, "source_route_state_dataset_id", EXPECTED["route_state_dataset_id"]),
        (cohort, "source_route_state_content_sha256", EXPECTED["route_state_content_sha256"]),
        (cohort, "source_route_state_manifest_sha256", EXPECTED["route_state_manifest_sha256"]),
        (cohort, "actual_route_state_manifest_sha256", EXPECTED["route_state_manifest_sha256"]),
        (cohort, "mapping_version", EXPECTED["mapping_version"]),
        (cohort, "mapping_compatible_sha256", EXPECTED["mapping_compatible_sha256"]),
        (cohort, "mapping_revised_sha256", EXPECTED["mapping_revised_sha256"]),
    )
    for source, key, expected in expected_pairs:
        if source.get(key) != expected:
            errors.append(f"S1 正式身份冲突：{key}={source.get(key)!r}，预期 {expected!r}")
    if lifecycle.get("event_count") != 81 or lifecycle.get("state_counts") != {
        "event_end_outside_data_range": 4,
        "event_end_recorded": 72,
        "event_end_unknown": 5,
    }:
        errors.append("S1 生命周期人口冲突")
    if (
        peer.get("physical_records") != 832_942_411
        or peer.get("observations") != 970_176
        or peer.get("sessions") != 142
        or peer.get("peer_asns") != 81
        or peer.get("transition_counts") != {"1->3": 84_653, "3->6": 1_597, "6->1": 883_926}
    ):
        errors.append("S1 会话事实人口冲突")
    if (
        cohort.get("event_count") != 81
        or cohort.get("unique_cohorts") != 81
        or not isinstance(cohort.get("members"), int)
        or cohort["members"] < 1
        or not isinstance(cohort.get("directions"), int)
        or cohort["directions"] < cohort["members"]
        or not isinstance(cohort.get("route_observations"), int)
        or cohort["route_observations"] < cohort["directions"]
    ):
        errors.append("S1 事件 cohort 人口冲突")
    countries = payload.get("countries", {})
    if set(countries) != {"IR", "MW"}:
        errors.append("S1 伊朗/马拉维 cohort 样本缺失")
    else:
        if countries["IR"].get("state_point") != "2026-02-27T01:10:00Z" or countries["MW"].get("state_point") != "2026-03-09T14:05:00Z":
            errors.append("S1 伊朗/马拉维 cohort 冻结点冲突")
        for country in ("IR", "MW"):
            sample = countries[country]
            if (
                not isinstance(sample.get("country_origin_asns"), int)
                or sample["country_origin_asns"] < 1
                or not isinstance(sample.get("observed_origin_asns"), int)
                or sample["observed_origin_asns"] < sample["country_origin_asns"]
                or not isinstance(sample.get("unknown_origin_observations"), int)
                or sample["unknown_origin_observations"] < 0
            ):
                errors.append(f"S1 {country} cohort origin 人口冲突")
    return errors


def verify() -> dict[str, Any]:
    errors, checks = verify_local()
    remote: dict[str, Any] | None = None
    if not errors:
        try:
            remote = run_remote_audit()
        except (OSError, subprocess.SubprocessError, RuntimeError) as error:
            errors.append(str(error))
        else:
            errors.extend(validate_remote(remote))
            checks.append("正式会话、生命周期、81 个 cohort 及全部成员文件逐行深审")
    return {
        "schema_version": "country_outage_generalization_s1_verification_v1",
        "status": "pass" if not errors else "fail",
        "stage": "S1",
        "check_count": len(checks),
        "checks": checks,
        "remote_evidence": remote,
        "errors": errors,
    }


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--create-remote-audit",
        action="store_true",
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
