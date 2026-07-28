from __future__ import annotations

from argparse import Namespace
import json
import os
from pathlib import Path
import tempfile
import unittest

from dev.data_quality import p0_reproducibility as repro
from dev.data_quality import p0_single_run_assurance as assurance
from dev.tests.test_p0_reproducibility import (
    Fixture,
    inventory,
    seal,
    sha,
    write_json,
    write_jsonl_gzip,
    write_pretty_json,
)


def build_sample(full_d2: Path, target: Path, *, changed: bool = False) -> Path:
    target.mkdir()
    offset = 1000 if changed else 0
    incidents = [
        {"incident_id": "inc_v1_" + "{:024x}".format(index + offset)}
        for index in range(1, 65)
    ]
    rows = {
        "incidents.jsonl.gz": incidents,
        "links.jsonl.gz": [
            {
                "incident_id": row["incident_id"],
                "detail_reference": "sample/{:02d}".format(index),
            }
            for index, row in enumerate(incidents, 1)
        ],
        "collision_groups.jsonl.gz": [],
        "quarantine.jsonl.gz": [],
    }
    files = {}
    for filename, values in rows.items():
        payload = write_jsonl_gzip(target / filename, values)
        files[filename] = inventory(
            target / filename, row_count=len(values), content=payload
        )
    manifest = json.loads((full_d2 / "manifest.json").read_text(encoding="utf-8"))
    manifest["files"] = files
    manifest["summary"] = {
        "incident_count": 64,
        "link_count": 64,
        "collision_group_count": 0,
        "quarantine_count": 0,
        "unexplained_reverse_orphan_count": 0,
        "unexplained_forward_reference_count": 0,
    }
    manifest["sample"] = {"enabled": True, "max_events": 64, "admissible": False}
    manifest["admission"] = {
        "status": "not_eligible",
        "eligible_for_release_gate": False,
        "blocking_reasons": ["fixture_sample_not_admissible"],
        "raw_traceable": False,
    }
    manifest["candidate_fingerprint_sha256"] = repro._canonical_sha256(
        repro._d2_fingerprint_payload(manifest, "sample fixture")
    )
    write_pretty_json(target / "manifest.json", manifest)
    (target / "摘要.md").write_text("# D2 64 条样本\n", encoding="utf-8")
    seal(target)
    return target


def execution_evidence(
    path: Path,
    candidate: Path,
    *,
    execution_id: str,
    started_at: str,
    finished_at: str,
) -> Path:
    value = {
        "schema_version": assurance.EXECUTION_EVIDENCE_SCHEMA,
        "execution_id": execution_id,
        "started_at": started_at,
        "finished_at": finished_at,
        "exit_code": 0,
        "output_dir": str(candidate.resolve()),
        "command_argv_sha256": sha(("command-" + execution_id).encode("utf-8")),
        "stdout_sha256": sha(("stdout-" + execution_id).encode("utf-8")),
        "stderr_sha256": sha(b""),
        "candidate_sha256sums_sha256": sha(
            (candidate / "SHA256SUMS").read_bytes()
        ),
    }
    write_json(path, value)
    return path


class SingleRunAssuranceTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def inputs(self, *, changed_b: bool = False):
        fixture = Fixture(self.root)
        d2 = fixture.d2("d2-final")
        d3 = fixture.d3("d3-final")
        metric = fixture.metric("metric-final", d2, d3)
        fixture.d3_fingerprint = json.loads(
            (d3 / "p0-artifact-manifest.json").read_text(encoding="utf-8")
        )["manifest_fingerprint_sha256"]
        route = fixture.route("route-final")
        sample_a = build_sample(d2, self.root / "sample-a")
        sample_b = build_sample(d2, self.root / "sample-b", changed=changed_b)
        evidence_a = execution_evidence(
            self.root / "execution-a.json",
            sample_a,
            execution_id="sample-a-run",
            started_at="2026-07-20T16:00:00+08:00",
            finished_at="2026-07-20T16:01:00+08:00",
        )
        evidence_b = execution_evidence(
            self.root / "execution-b.json",
            sample_b,
            execution_id="sample-b-run",
            started_at="2026-07-20T16:02:00+08:00",
            finished_at="2026-07-20T16:03:00+08:00",
        )
        return d2, d3, metric, route, sample_a, sample_b, evidence_a, evidence_b

    def args(self, values, output="assurance"):
        d2, d3, metric, route, sample_a, sample_b, evidence_a, evidence_b = values
        return Namespace(
            d2_final=str(d2),
            d3_final=str(d3),
            metric_final=str(metric),
            route_final=str(route),
            d2_sample_a=str(sample_a),
            d2_sample_b=str(sample_b),
            d2_sample_a_execution_evidence=str(evidence_a),
            d2_sample_b_execution_evidence=str(evidence_b),
            output_dir=str(self.root / output),
        )

    def test_builds_honest_partial_assurance_with_exact_final_bindings(self):
        result = assurance.run(self.args(self.inputs()))

        self.assertEqual(result["schema_version"], "p0_single_run_assurance_v1")
        self.assertEqual(
            result["assurance_mode"],
            "final_single_candidate_plus_d2_bounded_replay_v1",
        )
        self.assertTrue(
            result["final_candidate_integrity"]["all_sha256_closures_verified"]
        )
        self.assertEqual(
            set(result["final_candidate_integrity"]["components"]),
            {"d2", "d3", "metric", "route_event"},
        )
        self.assertTrue(all(result["cross_artifact_binding"]["checks"].values()))
        bounded = result["bounded_replay"]
        self.assertEqual(bounded["status"], "passed")
        self.assertTrue(bounded["byte_identity"]["all_corresponding_files_match"])
        self.assertTrue(bounded["semantic_identity"]["all_results_match"])
        self.assertEqual(
            bounded["generation_independence"]["status"], "externally_attested"
        )
        self.assertFalse(
            bounded["generation_independence"]["cryptographic_independence_proven"]
        )
        self.assertEqual(result["cross_run_coverage"]["status"], "partial")
        self.assertFalse(result["cross_run_coverage"]["population_coverage_claimed"])
        self.assertEqual(result["full_semantic_validation"]["status"], "not_run")
        self.assertEqual(
            result["final_candidate_identity"]["route_event"][
                "parent_d3_manifest_fingerprint_sha256"
            ],
            result["final_candidate_identity"]["d3"][
                "manifest_fingerprint_sha256"
            ],
        )
        self.assertTrue((self.root / "assurance" / "SHA256SUMS").is_file())
        self.assertFalse((self.root / "assurance" / ".sample-id-audit.sqlite3").exists())

    def test_real_sample_difference_is_reported_as_failed(self):
        result = assurance.run(self.args(self.inputs(changed_b=True)))

        self.assertEqual(result["bounded_replay"]["status"], "failed")
        self.assertFalse(
            result["bounded_replay"]["byte_identity"][
                "all_corresponding_files_match"
            ]
        )
        self.assertFalse(
            result["bounded_replay"]["semantic_identity"]["all_results_match"]
        )
        self.assertEqual(
            result["conclusion"]["bounded_d2_replay_status"], "failed"
        )

    def test_same_sample_path_is_rejected_without_output(self):
        values = list(self.inputs())
        values[5] = values[4]
        args = self.args(tuple(values))

        with self.assertRaisesRegex(assurance.AssuranceError, "同一路径"):
            assurance.run(args)
        self.assertFalse(Path(args.output_dir).exists())

    def test_hardlinked_sample_files_are_rejected(self):
        values = list(self.inputs())
        sample_a = values[4]
        sample_b = values[5]
        for path in sample_b.iterdir():
            path.unlink()
            os.link(sample_a / path.name, path)
        values[7] = execution_evidence(
            values[7],
            sample_b,
            execution_id="sample-b-hardlink",
            started_at="2026-07-20T16:04:00+08:00",
            finished_at="2026-07-20T16:05:00+08:00",
        )
        args = self.args(tuple(values))

        with self.assertRaisesRegex(assurance.AssuranceError, "硬链接"):
            assurance.run(args)
        self.assertFalse(Path(args.output_dir).exists())

    def test_execution_evidence_must_bind_current_candidate_closure(self):
        values = self.inputs()
        evidence_path = values[6]
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        evidence["candidate_sha256sums_sha256"] = "0" * 64
        write_json(evidence_path, evidence)
        args = self.args(values)

        with self.assertRaisesRegex(assurance.AssuranceError, "未绑定当前样本"):
            assurance.run(args)
        self.assertFalse(Path(args.output_dir).exists())

    def test_execution_times_must_be_distinct_as_instants(self):
        values = self.inputs()
        evidence_b_path = values[7]
        evidence = json.loads(evidence_b_path.read_text(encoding="utf-8"))
        evidence["started_at"] = "2026-07-20T08:00:00Z"
        write_json(evidence_b_path, evidence)
        args = self.args(values)

        with self.assertRaisesRegex(assurance.AssuranceError, "时刻必须不同"):
            assurance.run(args)
        self.assertFalse(Path(args.output_dir).exists())

    def test_route_profile_must_bind_final_d3(self):
        values = self.inputs()
        route_dir = values[3]
        summary_path = route_dir / "route-event-reconciliation-summary.json"
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        summary["build_scope"]["data_profile"]["id"] = "another-profile"
        write_json(summary_path, summary)
        seal(route_dir)
        args = self.args(values)

        with self.assertRaisesRegex(assurance.AssuranceError, "data_profile"):
            assurance.run(args)
        self.assertFalse(Path(args.output_dir).exists())

    def test_runner_locator_may_differ_when_program_hash_is_identical(self):
        values = list(self.inputs())
        for index, sample in ((4, values[4]), (5, values[5])):
            manifest_path = sample / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["source"]["provenance"].update(
                {
                    "probe_path": "/staging/runner-v23/p0_normalize_candidate.py",
                    "project_root": "/readonly/source",
                    "data_profile_path": "/readonly/source/config/data-profile.json",
                }
            )
            write_pretty_json(manifest_path, manifest)
            seal(sample)
            values[index] = sample
        values[6] = execution_evidence(
            values[6],
            values[4],
            execution_id="sample-a-relocated",
            started_at="2026-07-20T16:06:00+08:00",
            finished_at="2026-07-20T16:07:00+08:00",
        )
        values[7] = execution_evidence(
            values[7],
            values[5],
            execution_id="sample-b-relocated",
            started_at="2026-07-20T16:08:00+08:00",
            finished_at="2026-07-20T16:09:00+08:00",
        )

        result = assurance.run(self.args(tuple(values)))

        self.assertEqual(result["bounded_replay"]["status"], "passed")


if __name__ == "__main__":
    unittest.main()
