import copy
import json
from pathlib import Path
import subprocess
import unittest

from backend.data_pipeline.evidence import validate_reference_closure
from backend.data_pipeline.route_event import artifact_id_v1, route_event_id_v1, vp_id_v1
from backend.data_pipeline.research.rrc25_country_outage.research_evidence import (
    ResearchEvidenceError,
    build_research_evidence_package,
    build_unavailable_research_evidence_package,
    canonical_research_sidecar_bytes,
    validate_research_evidence_package,
    validate_research_sidecar_reference_closure,
)
from dev.tests.test_rrc25_research_episodes import _sample


ROOT = Path(__file__).resolve().parents[2]
HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64
HASH_D = "d" * 64
RUN_ID = "research_run_v1_" + "a" * 24
EPISODE_ID = "episode_v1_" + "e" * 24
WAVE_ID = "wave_v1_" + "f" * 24
VP_ID = vp_id_v1("rrc25", "192.0.2.1", 64500)
ROUTE_ID = route_event_id_v1(HASH_A, 42, 0)
ARTIFACT_ID = artifact_id_v1(HASH_A)


def _raw_id(file_hash=HASH_A, record_ordinal=42, element_ordinal=0):
    import hashlib

    identity = {
        "schema": "raw_record_ref_id_v1",
        "file_sha256": file_hash,
        "record_ordinal": record_ordinal,
        "element_ordinal": element_ordinal,
    }
    payload = json.dumps(identity, sort_keys=True, separators=(",", ":"))
    return "raw_v1_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


RAW_ID = _raw_id()


def _phase():
    return {
        "source_field": "legacy_country_outage",
        "semantics": "route_observation_not_causal_trace",
        "supports_recovery": False,
        "status": "not_applicable",
        "missing_reason": "legacy_not_applicable",
        "observations": None,
    }


def _incident(index=1):
    incident_id = "inc_v1_{:024x}".format(index)
    detail = "country_outage/2026-02-27 09:12:{:02d}/IR/{}/r".format(31 + index, index)
    return {
        "schema_version": "p0_incident_normalization_v1",
        "incident_id": incident_id,
        "incident_id_schema": "incident_id_v1",
        "event_type": "country_outage",
        "source_code": "r",
        "source_table": "country_outage_202602",
        "source_primary_key": {"source": "r", "country": "IR", "outage_id": index},
        "detail_reference": detail,
        "event_time_utc": "2026-02-27T01:12:{:02d}Z".format(31 + index),
        "end_time_utc": None,
        "duration_seconds": None,
        "risk_level": None,
        "affected_objects": [
            {
                "object_type": "country",
                "object_id": "IR",
                "role": "affected",
                "source_field": "detail_url.problem",
            }
        ],
        "collection_quality": [],
        "phase_coverage": {
            "before": _phase(),
            "during": _phase(),
            "after": _phase(),
        },
        "fact_link_status": "matched",
        "field_quality": [
            {
                "field": "detector_version",
                "status": "not_retained",
                "missing_reason": "legacy_field_not_retained",
            }
        ],
        "collision_group_id": None,
        "quarantine_id": None,
        "detector_version": None,
        "classification": "observation_only",
        "causal_conclusion": None,
    }


def _samples(unknown_second=False):
    first = _sample(0, 900, damaged=0.05)
    second = _sample(
        1,
        880,
        damaged=0.06,
        continuity="unknown_after_gap" if unknown_second else "continuous",
    )
    return [first, second]


def _episode(incidents, *, unknown=False):
    samples = _samples(unknown_second=unknown)
    if incidents:
        mappings = [
            {
                "incident_ref": incident["detail_reference"],
                "relation": "legacy_reconciliation",
                "causal": False,
                "evidence_sample_ids": [samples[0]["sample_id"]],
            }
            for incident in incidents
        ]
    else:
        mappings = [
            {
                "incident_ref": "未找到可关联的 legacy Incident",
                "relation": "no_correspondence",
                "causal": False,
                "evidence_sample_ids": [],
            }
        ]
    return {
        "schema_version": "country-outage-episode/v1",
        "episode_id": EPISODE_ID,
        "run_id": RUN_ID,
        "collector_id": "rrc25",
        "country_code": "IR",
        "cohort_view": "compatible",
        "algorithm_version": "country_outage_episode_v1",
        "onset_at": "2026-02-27T16:00:00Z",
        "detected_at": "2026-02-27T16:05:00Z",
        "peak_at": "2026-02-27T16:05:00Z",
        "trough_at": "2026-02-27T16:05:00Z",
        "partial_recovery_at": None,
        "full_recovery_at": None,
        "observation_end_at": "2026-02-27T16:10:00Z",
        "recovery_state": "unknown" if unknown else "ongoing",
        "duration": (
            {
                "duration_state": "unknown",
                "seconds": None,
                "minimum_seconds": None,
                "maximum_seconds": None,
                "measured_to": None,
            }
            if unknown
            else {
                "duration_state": "lower_bound",
                "seconds": None,
                "minimum_seconds": 600,
                "maximum_seconds": None,
                "measured_to": "2026-02-27T16:10:00Z",
            }
        ),
        "supporting_sample_ids": [sample["sample_id"] for sample in samples],
        "wave_ids": [WAVE_ID],
        "split_evidence": [],
        "incident_mappings": mappings,
    }


def _wave(samples):
    return {
        "schema_version": "country-outage-wave/v1",
        "wave_id": WAVE_ID,
        "episode_id": EPISODE_ID,
        "run_id": RUN_ID,
        "ordinal": 1,
        "onset_at": "2026-02-27T16:00:00Z",
        "detected_at": "2026-02-27T16:05:00Z",
        "trough_at": "2026-02-27T16:05:00Z",
        "rebound_at": None,
        "relation_to_previous_wave": "first_wave",
        "causal_relation": "not_assessed",
        "split_evidence": None,
        "supporting_sample_ids": [sample["sample_id"] for sample in samples],
    }


def _program(name, version="1.0.0", digest=HASH_C):
    return {
        "name": name,
        "version": version,
        "code_sha256": digest,
        "config_sha256": HASH_B,
    }


def _bundle_parameters(*, include_raw=True):
    lineage = {
        "parser": _program("mrt-parser") if include_raw else None,
        "importer": _program("route-event-importer") if include_raw else None,
        "detector": None,
        "normalizer": _program("research-incident-normalizer"),
        "bundle_generator": _program("research-evidence-bundle-generator", "2.0.0", HASH_D),
        "import_run_id": "run_v1_0123456789abcdef0123456789abcdef" if include_raw else None,
    }
    values = {
        "data_snapshot": {
            "profile_id": "iran-rrc25-202602",
            "profile_sha256": HASH_A,
            "window_start": "2026-02-01T00:00:00Z",
            "window_end_exclusive": "2026-04-01T00:00:00Z",
            "snapshot_time": "2026-03-31T23:59:59Z",
            "business_timezone": "Asia/Shanghai",
            "database_release_id": "research-read-only",
            "overlay_inventory_sha256": HASH_B,
            "raw_source_status": "full" if include_raw else "partial",
        },
        "processing_lineage": lineage,
        "raw_source_coverage": {
            "expected_count": 1,
            "observed_count": 1 if include_raw else 0,
        },
        "generated_at": "2026-07-22T09:00:00Z",
        "input_snapshot_sha256": HASH_B,
        "query_fingerprint_sha256": HASH_C,
        "source_hash_verification_status": "verified" if include_raw else "partial",
        "source_fact_record_hash": HASH_D,
    }
    if include_raw:
        values["raw_record_refs"] = [
            {
                "raw_record_ref_id": RAW_ID,
                "artifact_id": ARTIFACT_ID,
                "file_sha256": HASH_A,
                "record_offset": 4096,
                "record_length": 128,
                "record_hash": HASH_D,
                "record_ordinal": 42,
                "element_ordinal": 0,
                "collector_id": "rrc25",
                "vp_id": VP_ID,
                "vp_asn": 64500,
                "verification_status": "verified",
            }
        ]
        values["route_event_refs"] = [
            {
                "route_event_id": ROUTE_ID,
                "route_event_id_schema": "route_event_id_v1",
                "schema_version": "route_event_v1",
                "relation": "supports_observation",
                "semantics": "route_observation",
                "lineage_status": "raw_traceable",
                "observed_at": "2026-02-27T16:01:00Z",
                "collector_id": "rrc25",
                "vp_id": VP_ID,
                "vp_asn": 64500,
                "raw_record_ref_ids": [RAW_ID],
                "phase": "during",
            }
        ]
    return values


def _links(samples, *, unknown_second=False):
    return [
        {
            "sample_id": samples[0]["sample_id"],
            "link_state": "linked",
            "route_event_ids": [ROUTE_ID],
            "missing_reason_zh": None,
        },
        {
            "sample_id": samples[1]["sample_id"],
            "link_state": "unknown" if unknown_second else "linked",
            "route_event_ids": [] if unknown_second else [ROUTE_ID],
            "missing_reason_zh": "连续性缺口后无法建立确定的 RouteEvent 样本血缘。" if unknown_second else None,
        },
    ]


def _candidate(samples):
    return {
        "kind": "partial",
        "start_at": "2026-02-27T16:05:00Z",
        "supporting_sample_ids": [samples[1]["sample_id"]],
        "confirmed": False,
        "reason_code": "unknown_metric_interrupted_candidate",
    }


def _build(incidents=None, *, unknown=False, include_raw=True):
    incidents = [_incident()] if incidents is None else incidents
    samples = _samples(unknown_second=unknown)
    return build_research_evidence_package(
        incidents=incidents,
        episode=_episode(incidents, unknown=unknown),
        waves=[_wave(samples)],
        samples=samples,
        recovery_candidates=[_candidate(samples)],
        sample_route_event_links=_links(samples, unknown_second=unknown) if incidents else [],
        evidence_bundle_parameters=_bundle_parameters(include_raw=include_raw) if incidents else None,
        limitations_zh=["本测试只验证研究型证据引用闭环。"],
    )


def _assert_schema_valid(test_case, package):
    ajv_module = ROOT / "frontend" / "node_modules" / "@redocly" / "ajv" / "dist" / "2020"
    bundle_schema = ROOT / "contracts" / "data" / "evidence-bundle-v2.schema.json"
    sidecar_schema = ROOT / "contracts" / "research" / "research-evidence-sidecar.schema.json"
    package_schema = ROOT / "contracts" / "research" / "research-evidence-package.schema.json"
    script = r"""
const fs = require('fs')
const Ajv2020 = require(process.argv[1]).default
const bundleSchema = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'))
const sidecarSchema = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'))
const packageSchema = JSON.parse(fs.readFileSync(process.argv[4], 'utf8'))
const payload = JSON.parse(fs.readFileSync(0, 'utf8'))
const ajv = new Ajv2020({allErrors: true, allowUnionTypes: true, strict: true, validateFormats: true})
ajv.addFormat('date-time', {
  type: 'string',
  validate: (value) => {
    if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/.test(value)) return false
    const time = Date.parse(value)
    return Number.isFinite(time) && new Date(time).toISOString().replace('.000Z', 'Z') === value
  },
})
ajv.addSchema(bundleSchema)
ajv.addSchema(sidecarSchema)
const validateBundle = ajv.getSchema(bundleSchema.$id)
const validateSidecar = ajv.getSchema(sidecarSchema.$id)
const validatePackage = ajv.compile(packageSchema)
for (const bundle of payload.bundles) {
  if (!validateBundle(bundle)) {
    process.stderr.write(ajv.errorsText(validateBundle.errors, {separator: '; '}))
    process.exit(1)
  }
}
if (!validateSidecar(payload.sidecar)) {
  process.stderr.write(ajv.errorsText(validateSidecar.errors, {separator: '; '}))
  process.exit(1)
}
if (!validatePackage(payload)) {
  process.stderr.write(ajv.errorsText(validatePackage.errors, {separator: '; '}))
  process.exit(1)
}
"""
    result = subprocess.run(
        [
            "node",
            "-e",
            script,
            str(ajv_module),
            str(bundle_schema),
            str(sidecar_schema),
            str(package_schema),
        ],
        input=json.dumps(package, ensure_ascii=False, allow_nan=False),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(ROOT),
        check=False,
    )
    test_case.assertEqual(result.returncode, 0, result.stderr)


class ResearchEvidencePackageTest(unittest.TestCase):
    def test_matched_incident_without_episode_keeps_source_fact_and_empty_chain(self):
        package = build_research_evidence_package(
            incidents=[_incident()],
            episode=None,
            run_id=RUN_ID,
            waves=(),
            samples=(),
            recovery_candidates=(),
            evidence_bundle_parameters=_bundle_parameters(include_raw=False),
            limitations_zh=["本测试验证零 Episode 的事件级证据包。"],
        )
        bundle = package["bundles"][0]
        sidecar = package["sidecar"]

        self.assertEqual(package["evidence_package_state"], "available_no_episode")
        self.assertRegex(package["package_id"], r"^research_package_v1_[0-9a-f]{24}$")
        self.assertEqual(package["run_id"], RUN_ID)
        self.assertEqual(package["episode_ids"], [])
        self.assertEqual(bundle["coverage_summary"]["admission_level"], "legacy_compatible")
        self.assertIsNone(sidecar["episode_ref"])
        self.assertEqual(sidecar["incident_episode_links"], [])
        self.assertEqual(sidecar["wave_refs"], [])
        self.assertEqual(sidecar["sample_refs"], [])
        self.assertEqual(sidecar["sample_route_event_links"], [])
        self.assertTrue(sidecar["legacy_source_fact_refs"])
        self.assertEqual(
            sidecar["reference_closure"]["overall"],
            "passed_with_explicit_no_episode",
        )
        self.assertTrue(any("未伪造 episode" in text for text in sidecar["limitations_zh"]))
        validate_reference_closure(bundle)
        validate_research_sidecar_reference_closure(sidecar, bundles=package["bundles"])
        validate_research_evidence_package(package)
        _assert_schema_valid(self, package)

    def test_unresolved_source_fact_yields_unavailable_descriptor_not_fake_bundle(self):
        incident = _incident()
        incident["fact_link_status"] = "unresolved"

        package = build_unavailable_research_evidence_package(
            run_id=RUN_ID,
            incident=incident,
        )

        self.assertEqual(
            package["evidence_package_state"],
            "unavailable_source_fact_unresolved",
        )
        self.assertEqual(package["incident_ids"], [incident["incident_id"]])
        self.assertEqual(package["episode_ids"], [])
        self.assertEqual(package["incident_locators"], [incident])
        self.assertEqual(package["bundles"], [])
        self.assertIsNone(package["sidecar"])
        self.assertRegex(package["package_id"], r"^research_package_v1_[0-9a-f]{24}$")
        self.assertTrue(any("unresolved" in text for text in package["limitations_zh"]))
        validate_research_evidence_package(package)

    def test_unresolved_descriptor_rejects_causal_root_cause_and_precursor_assertions(self):
        cases = (
            ("causal_conclusion", "错误的因果结论"),
            ("root_cause", "物理链路中断"),
            ("precursor_assertion", "该事件是后续中断的前兆"),
        )
        for field, value in cases:
            with self.subTest(field=field):
                incident = _incident()
                incident["fact_link_status"] = "unresolved"
                incident[field] = value
                with self.assertRaisesRegex(
                    ResearchEvidenceError, "因果|根因|前兆"
                ):
                    build_unavailable_research_evidence_package(
                        run_id=RUN_ID,
                        incident=incident,
                    )

    def test_unresolved_descriptor_rejects_generic_nested_causal_text_but_allows_explicit_unknown(self):
        incident = _incident()
        incident["fact_link_status"] = "unresolved"
        incident["analysis"] = {
            "assessment": "该事件是后续中断的前兆，根因是物理链路中断。"
        }
        with self.assertRaisesRegex(ResearchEvidenceError, "因果|根因|前兆"):
            build_unavailable_research_evidence_package(
                run_id=RUN_ID,
                incident=incident,
            )

        incident["analysis"] = {
            "assessment": "目前无法判断是否为前兆，根因尚未确定。"
        }
        package = build_unavailable_research_evidence_package(
            run_id=RUN_ID,
            incident=incident,
        )
        self.assertEqual(
            package["evidence_package_state"],
            "unavailable_source_fact_unresolved",
        )

    def test_no_episode_fabricated_recovery_fails_after_content_id_recomputed(self):
        from backend.data_pipeline.research.rrc25_country_outage import research_evidence

        fixture = json.loads(
            (
                ROOT
                / "contracts/research/fixtures/research-evidence-sidecar"
                / "invalid-no-episode-fabricated-recovery-with-valid-content-id.json"
            ).read_text("utf-8")
        )
        self.assertEqual(
            fixture["sidecar_id"],
            research_evidence._stable_id(
                "research_sidecar_v1_",
                research_evidence._sidecar_identity_payload(fixture),
            ),
        )
        with self.assertRaisesRegex(ResearchEvidenceError, "恢复|unknown"):
            validate_research_sidecar_reference_closure(fixture)

    def test_single_incident_closes_full_chain_and_preserves_legacy_fact(self):
        package = _build()
        bundle = package["bundles"][0]
        sidecar = package["sidecar"]

        self.assertEqual(sidecar["mapping"]["mapping_state"], "exact")
        self.assertEqual(sidecar["reference_closure"]["overall"], "passed")
        self.assertEqual(sidecar["route_event_refs"][0]["route_event_id"], ROUTE_ID)
        self.assertEqual(sidecar["raw_record_refs"][0]["raw_record_ref_id"], RAW_ID)
        self.assertEqual(sidecar["artifact_refs"][0]["artifact_id"], ARTIFACT_ID)
        self.assertEqual(
            sidecar["legacy_source_fact_refs"][0]["source_fact_id"],
            bundle["source_fact_mapping"]["source_facts"][0]["source_fact_id"],
        )
        self.assertEqual(len(sidecar["recovery_assessment"]["candidates"]), 1)
        self.assertFalse(sidecar["recovery_assessment"]["candidates"][0]["confirmed"])
        validate_reference_closure(bundle)
        validate_research_sidecar_reference_closure(sidecar, bundles=package["bundles"])
        _assert_schema_valid(self, package)

    def test_zero_incident_is_explicit_and_does_not_fabricate_bundle_or_route(self):
        package = _build(incidents=[])
        sidecar = package["sidecar"]

        self.assertEqual(package["bundles"], [])
        self.assertEqual(sidecar["mapping"]["mapping_state"], "unmapped")
        self.assertEqual(sidecar["mapping"]["incident_ids"], [])
        self.assertEqual(sidecar["route_event_refs"], [])
        self.assertEqual(sidecar["raw_record_refs"], [])
        self.assertEqual(sidecar["artifact_refs"], [])
        self.assertTrue(
            all(link["link_state"] == "not_applicable" for link in sidecar["sample_route_event_links"])
        )
        self.assertEqual(
            sidecar["reference_closure"]["overall"], "passed_with_explicit_unmapped"
        )
        _assert_schema_valid(self, package)

    def test_multiple_incidents_are_explicit_without_collapsing_source_facts(self):
        incidents = [_incident(2), _incident(1)]
        package = _build(incidents=incidents)
        sidecar = package["sidecar"]

        self.assertEqual(sidecar["mapping"]["mapping_state"], "multiple")
        self.assertEqual(len(package["bundles"]), 2)
        self.assertEqual(len(sidecar["bundle_refs"]), 2)
        self.assertEqual(len(sidecar["incident_episode_links"]), 2)
        self.assertEqual(len(sidecar["legacy_source_fact_refs"]), 2)
        self.assertEqual(len({row["source_fact_id"] for row in sidecar["legacy_source_fact_refs"]}), 2)
        self.assertEqual(len(sidecar["route_event_refs"][0]["bundle_ids"]), 2)
        _assert_schema_valid(self, package)

    def test_unknown_continuity_and_duration_remain_null_not_zero(self):
        package = _build(unknown=True)
        sidecar = package["sidecar"]
        duration = sidecar["recovery_assessment"]["duration"]

        self.assertEqual(sidecar["recovery_assessment"]["continuity_status"], "unknown")
        self.assertEqual(duration["duration_state"], "unknown")
        self.assertIsNone(duration["seconds"])
        self.assertIsNone(duration["minimum_seconds"])
        self.assertEqual(
            sidecar["reference_closure"]["overall"], "passed_with_explicit_unknown"
        )
        self.assertTrue(any("连续性缺口" in text for text in sidecar["limitations_zh"]))
        _assert_schema_valid(self, package)

    def test_interval_duration_is_preserved_without_fabricating_exact_seconds(self):
        incident = _incident()
        samples = _samples()
        episode = _episode([incident], unknown=True)
        episode["duration"] = {
            "duration_state": "interval",
            "seconds": None,
            "minimum_seconds": 600,
            "maximum_seconds": 1200,
            "measured_to": "2026-02-27T16:10:00Z",
        }
        package = build_research_evidence_package(
            incidents=[incident],
            episode=episode,
            waves=[_wave(samples)],
            samples=samples,
            recovery_candidates=[_candidate(samples)],
            sample_route_event_links=_links(samples),
            evidence_bundle_parameters=_bundle_parameters(),
        )
        duration = package["sidecar"]["recovery_assessment"]["duration"]

        self.assertEqual(duration["duration_state"], "interval")
        self.assertIsNone(duration["seconds"])
        self.assertEqual((duration["minimum_seconds"], duration["maximum_seconds"]), (600, 1200))
        _assert_schema_valid(self, package)

    def test_unresolved_sample_route_reference_is_rejected(self):
        incident = _incident()
        samples = _samples()
        links = _links(samples)
        links[0]["route_event_ids"] = ["rte_v1_" + "f" * 32]

        with self.assertRaisesRegex(ResearchEvidenceError, "sample→RouteEvent"):
            build_research_evidence_package(
                incidents=[incident],
                episode=_episode([incident]),
                waves=[_wave(samples)],
                samples=samples,
                recovery_candidates=[_candidate(samples)],
                sample_route_event_links=links,
                evidence_bundle_parameters=_bundle_parameters(),
            )

    def test_mapped_research_rejects_legacy_bundle_without_raw_chain(self):
        with self.assertRaisesRegex(ResearchEvidenceError, "raw_traceable"):
            _build(include_raw=False)

    def test_causal_claim_is_rejected_by_existing_bundle_boundary(self):
        incident = _incident()
        incident["causal_conclusion"] = "错误的根因结论"
        with self.assertRaisesRegex(ResearchEvidenceError, "因果结论"):
            _build(incidents=[incident])

    def test_sidecar_and_bundle_output_are_deterministic(self):
        incidents = [_incident(1), _incident(2)]
        first = _build(incidents=incidents)
        second = _build(incidents=list(reversed(incidents)))

        self.assertEqual(second, first)
        self.assertEqual(
            canonical_research_sidecar_bytes(second["sidecar"]),
            canonical_research_sidecar_bytes(first["sidecar"]),
        )

    def test_tampered_raw_artifact_reference_fails_closure(self):
        package = _build()
        broken = copy.deepcopy(package["sidecar"])
        broken["raw_record_refs"][0]["artifact_id"] = "art_v1_" + "f" * 32
        # 先重算 ID，证明失败原因来自引用闭合而非外层内容 ID。
        from backend.data_pipeline.research.rrc25_country_outage import research_evidence

        broken["sidecar_id"] = research_evidence._stable_id(
            "research_sidecar_v1_", research_evidence._sidecar_identity_payload(broken)
        )
        with self.assertRaisesRegex(ResearchEvidenceError, "raw→artifact"):
            validate_research_sidecar_reference_closure(broken)

    def test_research_contract_validator_accepts_positive_and_rejects_negative_fixture(self):
        result = subprocess.run(
            ["node", str(ROOT / "dev" / "data_quality" / "validate_research_contracts.cjs")],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(ROOT),
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertIn("13 个 Schema", result.stdout)


if __name__ == "__main__":
    unittest.main()
