from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from backend.data_pipeline.route_event import AsPathSegment, ParsedMrtRecord
from backend.data_pipeline.research.rrc25_country_outage import (
    full_window_finalize as finalizer,
    full_window_finalize_workspace as workspace,
    full_window_journal as journal,
    full_window_segment_product as segment_product,
    full_window_worker as worker,
)
from backend.data_pipeline.research.rrc25_country_outage.country_impact import (
    build_raw_retention_mapping_union,
    mapping_bundle_sha256,
    mapping_view_from_frozen_snapshot,
    mapping_view_from_revised_snapshot,
)
from backend.data_pipeline.research.rrc25_country_outage.country_mapping import (
    freeze_as_country_mapping,
)
from backend.data_pipeline.research.rrc25_country_outage.file_artifacts import (
    canonical_json,
)
from backend.data_pipeline.research.rrc25_country_outage.input_resolver import (
    resolve_research_inputs,
)
from backend.data_pipeline.research.rrc25_country_outage.profile import (
    profile_sha256,
)
from backend.data_pipeline.research.rrc25_country_outage.state_replay import (
    seed_state_from_rib,
)
from dev.data_quality.rrc25_iran_bounded_pilot import build_code_identity
from dev.tests.test_rrc25_full_window_end_to_end import (
    _parser_attestation,
    _revised_snapshot,
    _seed_attestation,
    _seed_evidence,
)
from dev.tests.test_rrc25_full_window_worker import (
    FakeStream,
    advancing_clock,
    element as update_element,
    update_frame,
)
from dev.tests.test_rrc25_iran_research_coordinator import (
    manifest_bundle,
    small_profile,
)


RUN_ID = "research_run_v1_" + "d" * 24


def _completed_fixture(base: Path):
    """建立两个五分钟槽的完整 journal、sealed workspace 与冻结业务输入。"""

    profile = small_profile()
    manifest, manifest_verification = manifest_bundle()
    selection = resolve_research_inputs(
        manifest, manifest_verification, profile
    )
    mapping_csv = base / "as-country.csv"
    mapping_csv.write_text(
        "asn,as_country\n65001,IR\n65002,IR\n65003,ZZ\n",
        encoding="utf-8",
    )
    compatible_snapshot = freeze_as_country_mapping(mapping_csv)
    revised_source = base / "empty-revised.txt"
    revised_source.write_text("\n", encoding="utf-8")
    revised_snapshot = _revised_snapshot(
        compatible_snapshot, revised_source
    )
    compatible = mapping_view_from_frozen_snapshot(compatible_snapshot)
    revised = mapping_view_from_revised_snapshot(
        revised_snapshot, compatible_snapshot
    )
    code_identity = dict(build_code_identity())
    bindings = {
        "profile_sha256": profile_sha256(profile),
        "input_selection_sha256": selection[
            "semantic_fingerprint_sha256"
        ],
        "code_sha256": code_identity["identity_sha256"],
        "mapping_sha256": mapping_bundle_sha256(
            compatible_snapshot, revised_snapshot
        ),
    }
    seed_event, seed_route_row, seed_raw_row = _seed_evidence(selection)
    seed_state = seed_state_from_rib((seed_event,))
    expected_vps = [seed_event.vp_id]
    checkpoint_file_sha = "9" * 64
    checkpoint_fingerprint = "7" * 64
    vp_source_sha = hashlib.sha256(
        canonical_json(
            {
                "schema": "rrc25_seed_vp_population_source_v1",
                "checkpoint_file_sha256": checkpoint_file_sha,
                "checkpoint_fingerprint_sha256": checkpoint_fingerprint,
                "expected_vp_ids": expected_vps,
            }
        ).encode("utf-8")
    ).hexdigest()
    compact = worker.initialize_compact_state_from_seed(
        seed_state,
        compatible_mapping=compatible,
        revised_mapping=revised,
        tracked_prefixes=("203.0.113.0/24",),
        expected_vp_ids=expected_vps,
        vp_population_source_sha256=vp_source_sha,
    )
    route_state_payload = worker.route_replay_state_to_payload(seed_state)
    seed_attestation = _seed_attestation(
        selection=selection,
        bindings=bindings,
        code_identity=code_identity,
        compatible=compatible,
        revised=revised,
        compact=compact,
        route_state_payload=route_state_payload,
        route_row=seed_route_row,
        raw_row=seed_raw_row,
        expected_vp_ids=expected_vps,
        vp_source_sha=vp_source_sha,
    )
    journal_root = base / "journal"
    head = journal.initialize_full_window_journal(
        journal_root,
        run_id=RUN_ID,
        bindings=bindings,
        total_artifacts=2,
        initial_compact_state=compact,
        preliminary_seed_read_bytes=1,
        seed_artifact_read_bytes=10,
        additional_pre_update_raw_read_bytes=10,
        bootstrap_bytes_per_second=1_000_000,
        genesis_shards=(
            journal.ShardInput(
                "seed_bootstrap_attestation", (seed_attestation,)
            ),
            journal.ShardInput("seed_route_events", (seed_route_row,)),
            journal.ShardInput("seed_raw_record_refs", (seed_raw_row,)),
        ),
    )
    raw_union = build_raw_retention_mapping_union((compatible, revised))
    for index, artifact in enumerate(
        selection["roles"]["analysis_updates"]
    ):
        descriptor = worker.artifact_descriptor_from_manifest(
            index, artifact
        )
        token = journal.begin_artifact_attempt(head, descriptor)
        raw = update_frame(index, peer_ip="192.0.2.1")
        records = (
            ParsedMrtRecord(
                0,
                0,
                raw,
                (
                    update_element(
                        index,
                        peer_ip="192.0.2.1",
                        prefix="203.0.113.0/24",
                        as_path=(
                            AsPathSegment(
                                "as_sequence", (64500, 65001)
                            ),
                        ),
                    ),
                ),
            ),
        )
        head = worker.run_one_update_artifact(
            head,
            token,
            artifact_manifest_row=artifact,
            compatible_mapping=compatible,
            revised_mapping=revised,
            raw_retention_membership=lambda asn: raw_union.decision_for(
                asn
            ).retain,
            update_record_stream_factory=lambda _row, rows=records, item=artifact: FakeStream(
                rows, item
            ),
            parser_attestation=_parser_attestation(artifact),
            clock=advancing_clock(),
        ).head

    workspace_root = base / "finalization-workspace"
    workspace.initialize_finalization_workspace(
        workspace_root,
        journal_root=journal_root,
        bindings=bindings,
        code_identity=code_identity,
        study_id=profile["study_id"],
    )
    completed = workspace.run_finalization_workspace_segment(
        workspace_root,
        compatible_mapping=compatible,
        revised_mapping=revised,
        max_slots=2,
    )
    if not completed.sealed:
        raise AssertionError("测试夹具 finalization workspace 未 sealed")

    source_fact = json.loads(
        Path(
            "config/research/iran-country-outage-source-fact-20260227.json"
        ).read_text(encoding="utf-8")
    )
    incident_policy = json.loads(
        Path(
            "config/research/iran-rrc25-incident-episode-link-policy.json"
        ).read_text(encoding="utf-8")
    )
    claims = copy.deepcopy(
        json.loads(
            Path("config/research/iran-rrc25-report-claims.json").read_text(
                encoding="utf-8"
            )
        )
    )
    claims["study_id"] = profile["study_id"]
    frozen = {
        "profile": profile,
        "source_fact_snapshot": source_fact,
        "incident_policy": incident_policy,
        "compatible_mapping_snapshot": compatible_snapshot,
        "revised_mapping_snapshot": revised_snapshot,
        "code_identity": code_identity,
        "input_selection": selection,
        "claim_inventory": claims,
        "bindings": bindings,
    }
    return workspace_root, journal_root, frozen


class FullWindowSegmentProductTests(unittest.TestCase):
    def test_sealed_segments_build_complete_business_product_without_observation_reread(
        self,
    ):
        with tempfile.TemporaryDirectory() as directory:
            workspace_root, journal_root, frozen = _completed_fixture(
                Path(directory)
            )
            original_iter = finalizer._iter_shard_rows
            original_read = finalizer._read_stable_regular
            calls = {"record_observations": 0}

            def reject_observation_rows(root, ref):
                if ref.get("kind") == "record_observations":
                    calls["record_observations"] += 1
                    raise AssertionError(
                        "segment product 不得重读 record_observations"
                    )
                yield from original_iter(root, ref)

            def reject_observation_hash(path, *, maximum_bytes):
                if "shards/record_observations/" in str(path):
                    raise AssertionError(
                        "segment product 不得哈希 record_observations"
                    )
                return original_read(path, maximum_bytes=maximum_bytes)

            with (
                patch.object(
                    finalizer,
                    "_iter_shard_rows",
                    side_effect=reject_observation_rows,
                ),
                patch.object(
                    finalizer,
                    "_read_stable_regular",
                    side_effect=reject_observation_hash,
                ),
            ):
                product = segment_product.build_segment_product_inputs(
                    workspace_root, **frozen
                )
                business = (
                    segment_product.derive_business_outputs_from_segment_product(
                        product
                    )
                )

            self.assertEqual(calls["record_observations"], 0)
            self.assertIsInstance(
                product.inputs, finalizer._FinalizationInputs
            )
            self.assertEqual(
                product.verification["record_observation_reread_count"], 0
            )
            self.assertEqual(
                product.verification["verified_segment_count"], 2
            )
            self.assertEqual(
                product.inputs.journal.record_observation_count, 2
            )
            self.assertEqual(len(product.inputs.journal.compatible_slots), 2)
            self.assertEqual(len(product.inputs.journal.revised_slots), 2)
            self.assertEqual(len(product.inputs.journal.route_rows), 3)
            self.assertEqual(len(product.inputs.journal.raw_rows), 3)

            inventory_by_path = {
                row["path"]: row
                for row in product.journal_ancestry_inventory
            }
            copy_by_output = {
                row["output_relative_path"]: row
                for row in product.copy_sources
            }
            self.assertEqual(set(inventory_by_path), set(copy_by_output))
            self.assertTrue(
                any(
                    row["kind"] == "finalization-segment-payload"
                    for row in product.journal_ancestry_inventory
                )
            )
            self.assertTrue(
                any(
                    row["kind"] == "journal-seed_bootstrap_attestation"
                    for row in product.journal_ancestry_inventory
                )
            )
            self.assertTrue(
                any(
                    row["path"].startswith("raw-ledger/")
                    for row in product.journal_ancestry_inventory
                )
            )
            self.assertFalse(
                any(
                    row["kind"]
                    in {
                        "journal-receipt",
                        "journal-attempt",
                        "journal-outcome",
                        "journal-control_records",
                        "journal-record_observations",
                        "journal-route_events",
                        "journal-country_slots",
                    }
                    for row in product.journal_ancestry_inventory
                )
            )
            for output, source in copy_by_output.items():
                self.assertTrue(Path(source["source_path"]).is_file())
                self.assertEqual(
                    inventory_by_path[output]["sha256"], source["sha256"]
                )
            sample_source_paths = {
                row["_source_shard_ref"]["path"]
                for row in (
                    *product.inputs.journal.compatible_slots,
                    *product.inputs.journal.revised_slots,
                )
            }
            self.assertTrue(
                all(
                    path.startswith(
                        "segments/payloads/"
                    )
                    for path in sample_source_paths
                )
            )

            self.assertEqual(
                set(business.object_files),
                {
                    "metadata/finalization.json",
                    "frozen/profile.json",
                    "frozen/source-fact.json",
                    "frozen/incident-policy.json",
                    "frozen/compatible-mapping.json",
                    "frozen/revised-mapping.json",
                    "frozen/code-identity.json",
                    "frozen/input-selection.json",
                    "frozen/claim-inventory.json",
                    "frozen/bindings.json",
                    "data/compatible-baseline.json",
                    "data/revised-baseline.json",
                    "reconciliation.json",
                    "quality-and-accounting.json",
                },
            )
            self.assertIn(
                "evidence/research-evidence-packages.jsonl.gz",
                business.sequence_files,
            )
            self.assertIn(
                "data/compatible-country-samples.jsonl.gz",
                business.sequence_files,
            )
            compatible_samples = business.sequence_files[
                "data/compatible-country-samples.jsonl.gz"
            ][1]
            self.assertTrue(compatible_samples)
            self.assertTrue(
                all(
                    source["ref_id"].startswith("segments/payloads/")
                    for sample in compatible_samples
                    for source in sample["source_refs"]
                )
            )
            report_path = (
                "报告/RRC25伊朗国家路由中断事件复算与对账报告.md"
            )
            self.assertEqual(set(business.byte_files), {report_path})
            report = business.byte_files[report_path][1].decode("utf-8")
            self.assertIn("伊朗", report)
            self.assertIn("证据", report)
            self.assertEqual(
                product.inputs.journal.frozen_head[
                    "cumulative_reserved_raw_bytes"
                ],
                product.inputs.journal.execution[
                    "cumulative_reserved_raw_bytes_upper_bound"
                ],
            )
            self.assertFalse(
                any(
                    "record_observations" in row["source_path"]
                    for row in product.copy_sources
                )
            )
            self.assertTrue(journal_root.is_dir())

    def test_frozen_binding_drift_fails_before_segment_product_is_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace_root, _journal_root, frozen = _completed_fixture(
                Path(directory)
            )
            drifted = dict(frozen)
            drifted["bindings"] = {
                **frozen["bindings"],
                "mapping_sha256": "0" * 64,
            }
            with self.assertRaisesRegex(
                segment_product.FullWindowSegmentProductError,
                "mapping bundle",
            ):
                segment_product.build_segment_product_inputs(
                    workspace_root, **drifted
                )


if __name__ == "__main__":
    unittest.main()
