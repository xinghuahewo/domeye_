from __future__ import annotations

from copy import deepcopy
import gzip
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.data_pipeline.research.rrc25_country_outage import (  # noqa: E402
    iran_research_acceptance as acceptance,
)
from backend.data_pipeline.research.rrc25_country_outage import (  # noqa: E402
    full_window_finalize as finalizer,
)
from backend.data_pipeline.research.rrc25_country_outage import (  # noqa: E402
    full_window_finalize_workspace as finalization_workspace,
)
from backend.data_pipeline.research.rrc25_country_outage.analysis_rib_anchor import (  # noqa: E402
    PROJECTION_SEMANTICS,
    _projection_sha256,
)
from backend.data_pipeline.research.rrc25_country_outage.file_artifacts import (  # noqa: E402
    write_canonical_json,
)
from backend.data_pipeline.research.rrc25_country_outage.package_manifest import (  # noqa: E402
    build_package_manifest,
    publish_package_metadata,
)
from dev.data_quality import rrc25_iran_acceptance as acceptance_cli  # noqa: E402
from dev.tests.test_rrc25_full_window_finalize_workspace import (  # noqa: E402
    _completed_journal,
)
from dev.tests.test_rrc25_full_window_segment_product import (  # noqa: E402
    _completed_fixture as _completed_product_fixture,
)


def _projection_row(
    suffix: str,
    *,
    prefix: str,
    path_tail: int,
    vp_id: str | None = None,
) -> dict:
    return {
        "collector_id": "rrc25",
        "vp_id": vp_id or f"vp_v1_{suffix}",
        "afi_safi": "ipv4_unicast",
        "prefix": prefix,
        "peer_ip": f"192.0.2.{int(suffix[-1], 16) + 1}",
        "peer_asn": 64500,
        "as_path": [
            {"segment_type": "as_sequence", "asns": [64500, path_tail]}
        ],
        "origin_state": "resolved",
        "origin_asns": [path_tail],
        "origin_reason": None,
        "quality_flags": [],
    }


def _anchor_receipt(rows: list[dict], *, role: str = "analysis_rib") -> dict:
    return {
        "anchor_id": "rib_anchor_v1_" + "a" * 32,
        "artifact": {
            "artifact_id": "art_v1_" + "b" * 32,
            "role": role,
        },
        "boundary_at_utc": "2026-02-28T00:00:00Z",
        "observed_vp_ids": sorted({row["vp_id"] for row in rows}),
        "projection": {
            "semantics": PROJECTION_SEMANTICS,
            "semantic_sha256": _projection_sha256(rows),
        },
    }


def _visibility(invisible: bool) -> dict:
    return {
        "visibility": {
            "fully_invisible": invisible,
            "visibility_state": "observed",
            "missing_reason": None,
        }
    }


def _link(route_id: str = "rte_v1_" + "1" * 32) -> dict:
    return {
        "route_event_id": route_id,
        "raw_record_ref_id": "raw_v1_" + "2" * 32,
        "artifact_id": "art_v1_" + "3" * 32,
        "artifact_sha256": "3" * 64,
        "record_ordinal": 7,
        "element_ordinal": 1,
    }


def _resolution(route_id: str = "rte_v1_" + "1" * 32) -> dict:
    link = _link(route_id)
    source = {
        "kind": "finalization-segment-payload",
        "path": "segments/payloads/slot-0001-" + "4" * 64 + ".json.gz",
        "sha256": "4" * 64,
        "size_bytes": 100,
        "record_count": 1,
        "embedded_collection": "route_event_rows",
    }
    raw_source = {
        "kind": "finalization-segment-payload",
        "path": source["path"],
        "sha256": source["sha256"],
        "size_bytes": source["size_bytes"],
        "record_count": 1,
        "embedded_collection": "raw_record_ref_rows",
    }
    return {
        "route_event_id": route_id,
        "route_event": {
            "route_event_id": route_id,
            "raw_record_ref_id": link["raw_record_ref_id"],
            "artifact_id": link["artifact_id"],
            "file_sha256": link["artifact_sha256"],
            "record_ordinal": link["record_ordinal"],
            "element_ordinal": link["element_ordinal"],
        },
        "raw_record_ref": {
            "route_event_id": route_id,
            "raw_record_ref_id": link["raw_record_ref_id"],
            "artifact_id": link["artifact_id"],
            "file_sha256": link["artifact_sha256"],
            "record_ordinal": link["record_ordinal"],
            "element_ordinal": link["element_ordinal"],
            "record_hash": "6" * 64,
            "raw_record_sha256": "6" * 64,
            "record_offset": 10,
            "record_length": 20,
            "verification_status": "verified",
        },
        "route_event_source_ref": source,
        "raw_record_source_ref": raw_source,
    }


def _manifest_index() -> dict:
    return {
        "segments/payloads/slot-0001-" + "4" * 64 + ".json.gz": {
            "kind": "finalization-segment-payload",
            "path": "segments/payloads/slot-0001-" + "4" * 64 + ".json.gz",
            "sha256": "4" * 64,
            "size_bytes": 100,
            "record_count": 1,
        },
    }


def _episode_row(
    index: int,
    *,
    ipv4_invisible: bool,
    ipv6_invisible: bool,
    classification: str,
    cumulative: bool,
    end_member: bool,
    with_link: bool = True,
) -> dict:
    return {
        "schema_version": "country-outage-episode-as/v1",
        "episode_as_id": f"episode_as_v1_{index:024x}",
        "episode_id": f"episode_v1_{index:024x}",
        "run_id": "research_run_v1_" + "a" * 24,
        "asn": 65000 + index,
        "country_code": "IR",
        "cohort_view": "compatible",
        "first_damaged_at": "2026-02-28T00:05:00Z" if cumulative else None,
        "last_damaged_at": "2026-02-28T00:10:00Z" if cumulative else None,
        "recovered_at": None,
        "trigger_member": cumulative,
        "peak_member": cumulative,
        "cumulative_member": cumulative,
        "observation_end_member": end_member,
        "address_families": {
            "ipv4": _visibility(ipv4_invisible),
            "ipv6": _visibility(ipv6_invisible),
        },
        "overall_classification": classification,
        "evidence_links": [_link()] if with_link else [],
    }


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(acceptance.canonical_json(value) + "\n", encoding="utf-8")


def _write_rows(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = "".join(acceptance.canonical_json(row) + "\n" for row in rows)
    buffer = io.BytesIO()
    with gzip.GzipFile(
        filename="", mode="wb", compresslevel=9, fileobj=buffer, mtime=0
    ) as stream:
        stream.write(payload.encode("utf-8"))
    path.write_bytes(buffer.getvalue())


def _file_content_ref(
    root: Path,
    relative: str,
    *,
    kind: str,
    record_count: int,
) -> dict:
    raw = (root / relative).read_bytes()
    return {
        "kind": kind,
        "path": relative,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "size_bytes": len(raw),
        "record_count": record_count,
    }


def _business_fixture_files(root: Path, *, business_core: str, segment_core: str) -> dict:
    json_values = {
        "frozen/profile.json": {
            "schema_version": "research-profile/v1",
            "window": {
                "start_utc": "2026-02-27T16:00:00Z",
                "end_exclusive_utc": "2026-03-06T08:40:00Z",
            },
        },
        "frozen/source-fact.json": {"schema_version": "source-fact/v1"},
        "frozen/incident-policy.json": {"schema_version": "incident-policy/v1"},
        "frozen/compatible-mapping.json": {"schema_version": "mapping/v1"},
        "frozen/revised-mapping.json": {"schema_version": "mapping-delta/v1"},
        "frozen/code-identity.json": {"schema_version": "code-identity/v1"},
        "frozen/input-selection.json": {"schema_version": "input-selection/v1"},
        "frozen/claim-inventory.json": {"schema_version": "claim-inventory/v1"},
        "data/compatible-baseline.json": {"schema_version": "baseline/v1"},
        "data/revised-baseline.json": {"schema_version": "baseline/v1"},
        "reconciliation.json": {"schema_version": "reconciliation/v1"},
    }
    for relative, value in json_values.items():
        _write_json(root / relative, value)

    quality = {
        "schema_version": "rrc25-full-window-quality-and-accounting/v1",
        "business_semantic_core_sha256": business_core,
        "finalization_segment_core_sha256": segment_core,
        "research_quality": {
            "gates": [
                {
                    "gate_id": "input_completeness",
                    "status": "pass",
                    "blocking": True,
                },
                {
                    "gate_id": "reproducibility",
                    "status": "fail",
                    "blocking": True,
                },
            ]
        },
        "run_state": "blocked",
        "acceptance_state": "not_accepted",
        "vp_coverage_disclosure": {
            "partial_slot_count": 0,
            "total_slot_count": 1,
        },
        "raw_accounting": {
            "cumulative_reserved_raw_bytes_upper_bound": 1000,
            "peak_temporary_bytes": 1000,
            "database_write_operations": 0,
            "unclosed_attempt_count": 0,
            "max_worker_seconds": 1.0,
        },
        "external_reproduction": {
            "state": "reproduction_pending_not_accepted",
            "semantic_core_sha256": business_core,
        },
    }
    _write_json(root / "quality-and-accounting.json", quality)

    sequence_values = {
        "data/compatible-country-samples.jsonl.gz": [
            {"schema_version": "country-feature-sample/v1"}
        ],
        "data/revised-country-samples.jsonl.gz": [
            {"schema_version": "country-feature-sample/v1"}
        ],
        "data/compatible-sample-measurement-semantics.jsonl.gz": [
            {"schema_version": "rrc25-full-window-sample-measurement-semantics/v1"}
        ],
        "data/revised-sample-measurement-semantics.jsonl.gz": [
            {"schema_version": "rrc25-full-window-sample-measurement-semantics/v1"}
        ],
        "data/compatible-episodes.jsonl.gz": [
            {"schema_version": "country-outage-episode/v1", "episode_id": "episode_v1_a"}
        ],
        "data/compatible-waves.jsonl.gz": [
            {"schema_version": "country-outage-wave/v1", "wave_id": "wave_v1_a"}
        ],
        "data/revised-episodes.jsonl.gz": [
            {"schema_version": "country-outage-episode/v1", "episode_id": "episode_v1_b"}
        ],
        "data/revised-waves.jsonl.gz": [
            {"schema_version": "country-outage-wave/v1", "wave_id": "wave_v1_b"}
        ],
        "data/compatible-episode-as.jsonl.gz": [
            _episode_row(
                1,
                ipv4_invisible=True,
                ipv6_invisible=False,
                classification="ipv4_only_fully_invisible",
                cumulative=True,
                end_member=True,
                with_link=False,
            )
        ],
        "data/compatible-episode-as-measurement-semantics.jsonl.gz": [
            {
                "schema_version": (
                    "rrc25-full-window-episode-as-measurement-semantics/v1"
                )
            }
        ],
        "data/compatible-prefix-impact.jsonl.gz": [
            {
                "schema_version": "rrc25-full-window-episode-prefix-impact/v1",
                "prefix": "192.0.2.0/24",
            }
        ],
        "data/revised-episode-as.jsonl.gz": [
            _episode_row(
                2,
                ipv4_invisible=False,
                ipv6_invisible=False,
                classification="partially_visible",
                cumulative=True,
                end_member=False,
                with_link=False,
            )
        ],
        "data/revised-episode-as-measurement-semantics.jsonl.gz": [
            {
                "schema_version": (
                    "rrc25-full-window-episode-as-measurement-semantics/v1"
                )
            }
        ],
        "data/revised-prefix-impact.jsonl.gz": [
            {
                "schema_version": "rrc25-full-window-episode-prefix-impact/v1",
                "prefix": "2001:db8::/32",
            }
        ],
        "data/incident-episode-mappings.jsonl.gz": [
            {
                "schema_version": "incident-episode-mapping/v1",
                "incident_id": "inc_v1_fixture",
            }
        ],
        "evidence/research-evidence-packages.jsonl.gz": [
            {
                "schema_version": "research-evidence-package/v1",
                "evidence_package_id": "research_evidence_v1_fixture",
            }
        ],
    }
    for relative, rows in sequence_values.items():
        _write_rows(root / relative, rows)
    report_path = root / acceptance._REQUIRED_BUSINESS_REPORT_PATH
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        "# RRC25 伊朗国家路由中断事件复算与对账报告\n\n本报告用于测试复算闭环。\n",
        encoding="utf-8",
    )
    return {relative: len(rows) for relative, rows in sequence_values.items()}


def _augment_workspace_package(
    root: Path,
    *,
    business_core: str,
    segment_core: str,
) -> dict:
    original_manifest = json.loads(
        (root / "package-manifest.json").read_text(encoding="utf-8")
    )
    existing = {row["path"]: dict(row) for row in original_manifest["contents"]}
    bindings = json.loads((root / "frozen/bindings.json").read_text(encoding="utf-8"))

    index_path = root / "segments/index.json"
    segment_index = json.loads(index_path.read_text(encoding="utf-8"))
    index_semantic = {
        key: value
        for key, value in segment_index.items()
        if key not in {"schema_version", "fingerprint_sha256"}
    }
    index_semantic["business_semantic_core_sha256"] = business_core
    index_semantic["finalization_segment_core_sha256"] = segment_core
    segment_index = finalization_workspace._fingerprinted(
        finalization_workspace.WORKSPACE_ASSEMBLY_INDEX_SCHEMA,
        index_semantic,
    )
    _write_json(index_path, segment_index)

    terminal_ref = finalization_workspace._file_ref(root, root / "TERMINAL")
    deep_ref = finalization_workspace._file_ref(root, root / "DEEP-VERIFICATION")
    index_ref = finalization_workspace._file_ref(root, index_path)
    finalization_path = root / "metadata/finalization.json"
    finalization = json.loads(finalization_path.read_text(encoding="utf-8"))
    finalization_semantic = {
        key: value
        for key, value in finalization.items()
        if key not in {"schema_version", "fingerprint_sha256"}
    }
    finalization_semantic.update(
        {
            "business_semantic_core_sha256": business_core,
            "finalization_segment_core_sha256": segment_core,
            "segment_index_ref": index_ref,
            "terminal_ref": terminal_ref,
            "deep_verification_ref": deep_ref,
        }
    )
    finalization = finalization_workspace._fingerprinted(
        finalization_workspace.WORKSPACE_ASSEMBLY_METADATA_SCHEMA,
        finalization_semantic,
    )
    _write_json(finalization_path, finalization)

    sequence_counts = _business_fixture_files(
        root, business_core=business_core, segment_core=segment_core
    )
    _write_json(root / "frozen/bindings.json", bindings)
    kinds = {
        relative: "business-json"
        for relative in acceptance._REQUIRED_BUSINESS_JSON_PATHS
    }
    kinds.update(
        {
            relative: "business-sequence"
            for relative in acceptance._REQUIRED_BUSINESS_SEQUENCE_PATHS
        }
    )
    kinds[acceptance._REQUIRED_BUSINESS_REPORT_PATH] = "report"
    kinds["metadata/finalization.json"] = "segment-assembly-metadata"
    for reserved in ("package-manifest.json", "SHA256SUMS"):
        (root / reserved).unlink()
    contents = []
    for path in sorted(
        item
        for item in root.rglob("*")
        if item.is_file() and not item.is_symlink()
    ):
        relative = path.relative_to(root).as_posix()
        prior = existing.get(relative)
        contents.append(
            _file_content_ref(
                root,
                relative,
                kind=(
                    kinds.get(relative)
                    or (prior.get("kind") if isinstance(prior, dict) else "fixture")
                ),
                record_count=(
                    sequence_counts.get(relative)
                    if relative in sequence_counts
                    else (prior.get("record_count", 1) if isinstance(prior, dict) else 1)
                ),
            )
        )
    manifest = build_package_manifest(
        run_id=original_manifest["run_id"],
        study_id=original_manifest["study_id"],
        incident_ref=original_manifest["incident_ref"],
        execution_mode=original_manifest["execution_mode"],
        acceptance_state=original_manifest["acceptance_state"],
        bindings=bindings,
        contents=contents,
    )
    publish_package_metadata(root, manifest)
    return {
        "manifest": manifest,
        "terminal_ref": terminal_ref,
        "deep_verification_ref": deep_ref,
        "segment_index_ref": index_ref,
    }


def _strict_v2_fixture(parent: Path) -> tuple[Path, dict]:
    workspace, _journal_root, frozen = _completed_product_fixture(parent)
    reference = parent / "reference"
    reproduction = parent / "reproduction"
    receipt_path = parent / "accepted-v2.json"
    receipt = finalization_workspace.assemble_workspace_reproduction(
        workspace,
        reference_output_root=reference,
        reproduction_output_root=reproduction,
        acceptance_receipt_path=receipt_path,
        **frozen,
    )
    segment_core = receipt["finalization_segment_core_sha256"]
    business_core = "b" * 64
    package_rows = []
    for source_row in receipt["packages"]:
        root = Path(source_row["package_root"])
        augmented = _augment_workspace_package(
            root,
            business_core=business_core,
            segment_core=segment_core,
        )
        resource_path = Path(source_row["resource_receipt_path"])
        resource = json.loads(resource_path.read_text(encoding="utf-8"))
        resource_semantic = {
            key: value
            for key, value in resource.items()
            if key not in {"schema_version", "fingerprint_sha256"}
        }
        resource_semantic.update(
            {
                "package_manifest_sha256": hashlib.sha256(
                    (root / "package-manifest.json").read_bytes()
                ).hexdigest(),
                "package_semantic_fingerprint_sha256": augmented["manifest"][
                    "semantic_fingerprint_sha256"
                ],
                "business_semantic_core_sha256": business_core,
                "finalization_segment_core_sha256": segment_core,
                "segment_index_ref": augmented["segment_index_ref"],
            }
        )
        resource = finalization_workspace._fingerprinted(
            finalization_workspace.WORKSPACE_PACKAGE_RESOURCE_SCHEMA,
            resource_semantic,
        )
        _write_json(resource_path, resource)
        package_rows.append(
            {
                **dict(source_row),
                "release_id": augmented["manifest"]["release_id"],
                "package_manifest_sha256": hashlib.sha256(
                    (root / "package-manifest.json").read_bytes()
                ).hexdigest(),
                "package_semantic_fingerprint_sha256": augmented["manifest"][
                    "semantic_fingerprint_sha256"
                ],
                "resource_receipt_file_sha256": hashlib.sha256(
                    resource_path.read_bytes()
                ).hexdigest(),
                "business_semantic_core_sha256": business_core,
                "finalization_segment_core_sha256": segment_core,
                "terminal_ref": augmented["terminal_ref"],
                "deep_verification_ref": augmented["deep_verification_ref"],
                "segment_index_ref": augmented["segment_index_ref"],
            }
        )
    semantic = {
        key: value for key, value in receipt.items() if key != "receipt_sha256"
    }
    semantic.update(
        {
            "business_semantic_core_sha256": business_core,
            "packages": package_rows,
        }
    )
    receipt = {
        **semantic,
        "receipt_sha256": acceptance._canonical_hash(
            {
                "schema": "rrc25_full_window_reproduction_acceptance_v2",
                "receipt": semantic,
            }
        ),
    }
    _write_json(receipt_path, receipt)
    return receipt_path, receipt


class IranResearchAcceptanceTests(unittest.TestCase):
    def test_prepare_is_dispatched_to_one_bounded_process_group(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            args = SimpleNamespace(
                workspace_root=str(workspace),
                update_acceptance_receipt=str(Path(directory) / "update.json"),
                analysis_rib_anchor_root=str(Path(directory) / "anchors"),
            )
            child = {
                "workspace_root": str(workspace.resolve()),
                "genesis_fingerprint_sha256": "1" * 64,
            }
            observed = {
                "successful": True,
                "policy": {
                    "observation_seconds": 420.0,
                    "term_seconds": 540.0,
                    "kill_seconds": 590.0,
                    "parent_exit_seconds_exclusive": 596.0,
                    "is_frozen_acceptance_policy": True,
                },
                "actions": {
                    "observation_boundary_crossed": False,
                    "term_sent": False,
                    "kill_sent": False,
                    "child_reaped_within_parent_deadline": True,
                },
                "child_exit_code": 0,
                "elapsed_seconds": 1.0,
                "stdout": json.dumps(child),
                "stderr": "",
            }
            with (
                patch.object(
                    acceptance_cli, "_supervise_child", return_value=observed
                ) as supervisor,
                patch.object(
                    acceptance_cli,
                    "initialize_acceptance_workspace",
                    side_effect=AssertionError(
                        "公开 prepare 父进程不得直接遍历两个包"
                    ),
                ),
            ):
                result = acceptance_cli._prepare(args)
            command = supervisor.call_args.args[0]
            self.assertIn("_prepare-child", command)
            self.assertEqual(result["workspace_root"], str(workspace.resolve()))
            self.assertEqual(
                result["prepare_supervision"]["policy"]["kill_seconds"], 590.0
            )

    def test_legacy_v1_update_receipt_cannot_bypass_segmented_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "accepted-v1.json"
            semantic = {
                "schema_version": "rrc25-full-window-reproduction-acceptance/v1",
                "acceptance_state": "accepted",
                "reproduction_scope": "pure_derivation_from_same_frozen_journal",
                "raw_replay_reproduction": "not_performed_by_user_choice",
                "semantic_core_sha256": "1" * 64,
                "input_bindings": {},
                "packages": [],
                "checks": {},
            }
            receipt = {
                **semantic,
                "receipt_sha256": acceptance._canonical_hash(
                    {
                        "schema": "rrc25_full_window_reproduction_acceptance_v1",
                        "receipt": semantic,
                    }
                ),
            }
            path.write_text(
                acceptance.canonical_json(receipt) + "\n", encoding="utf-8"
            )
            with patch.object(
                acceptance._finalization_workspace,
                "verify_workspace_reproduction_acceptance_receipt",
                side_effect=AssertionError("v1 不应进入 v2 verifier"),
            ):
                with self.assertRaisesRegex(
                    acceptance.IranResearchAcceptanceError, "指纹/语义"
                ):
                    acceptance._update_acceptance_light(path)

    def test_v2_update_receipt_requires_both_cores_and_calls_real_workspace_verifier(self):
        with tempfile.TemporaryDirectory() as directory:
            path, receipt = _strict_v2_fixture(Path(directory))
            real_verifier = (
                finalization_workspace.verify_workspace_reproduction_acceptance_receipt
            )
            with patch.object(
                acceptance._finalization_workspace,
                "verify_workspace_reproduction_acceptance_receipt",
                wraps=real_verifier,
            ) as verifier:
                verified = acceptance._update_acceptance_light(path)
            verifier.assert_called_once_with(path.absolute())
            self.assertEqual(
                verified["receipt"]["business_semantic_core_sha256"],
                "b" * 64,
            )
            self.assertEqual(len(verified["packages"]), 2)
            self.assertTrue(
                all(
                    row["business_gate"]["verified_population_counts"][
                        "data/compatible-episode-as.jsonl.gz"
                    ]
                    == 1
                    for row in verified["packages"]
                )
            )

            missing_business = {
                key: value
                for key, value in receipt.items()
                if key != "receipt_sha256"
            }
            del missing_business["business_semantic_core_sha256"]
            damaged = {
                **missing_business,
                "receipt_sha256": acceptance._canonical_hash(
                    {
                        "schema": "rrc25_full_window_reproduction_acceptance_v2",
                        "receipt": missing_business,
                    }
                ),
            }
            _write_json(path, damaged)
            with self.assertRaisesRegex(
                acceptance.IranResearchAcceptanceError,
                "business semantic core",
            ):
                acceptance._update_acceptance_light(path)

    def test_v2_gate_never_reads_journal_or_record_observation(self):
        with tempfile.TemporaryDirectory() as directory:
            path, _receipt = _strict_v2_fixture(Path(directory))
            with (
                patch.object(
                    finalizer,
                    "_read_record_observation_shard_once",
                    side_effect=AssertionError("不得回读 record observation"),
                ),
                patch.object(
                    acceptance._journal_contract,
                    "load_full_window_head",
                    side_effect=AssertionError("不得回到 journal_root"),
                ),
            ):
                verified = acceptance._update_acceptance_light(path)
            self.assertIn(
                "without_record_observation_reread",
                verified["verification_scope"],
            )

    def test_v2_terminal_deep_index_resource_and_core_tamper_are_rejected(self):
        targets = (
            ("TERMINAL", "reference/TERMINAL"),
            ("DEEP", "reference/DEEP-VERIFICATION"),
            ("index", "reference/segments/index.json"),
        )
        for label, relative in targets:
            with self.subTest(target=label), tempfile.TemporaryDirectory() as directory:
                parent = Path(directory)
                path, _receipt = _strict_v2_fixture(parent)
                target = parent / relative
                damaged = bytearray(target.read_bytes())
                damaged[-2] ^= 1
                target.write_bytes(damaged)
                with self.assertRaisesRegex(
                    acceptance.IranResearchAcceptanceError,
                    "verifier|指纹|manifest|核心",
                ):
                    acceptance._update_acceptance_light(path)

        with self.subTest(target="resource"), tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            path, receipt = _strict_v2_fixture(parent)
            resource = Path(receipt["packages"][0]["resource_receipt_path"])
            damaged = bytearray(resource.read_bytes())
            damaged[-2] ^= 1
            resource.write_bytes(damaged)
            with self.assertRaisesRegex(
                acceptance.IranResearchAcceptanceError, "verifier|resource|核心"
            ):
                acceptance._update_acceptance_light(path)

        with self.subTest(target="dual-core"), tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            path, receipt = _strict_v2_fixture(parent)
            semantic = {
                key: deepcopy(value)
                for key, value in receipt.items()
                if key != "receipt_sha256"
            }
            semantic["business_semantic_core_sha256"] = "c" * 64
            for row in semantic["packages"]:
                row["business_semantic_core_sha256"] = "c" * 64
            damaged = {
                **semantic,
                "receipt_sha256": acceptance._canonical_hash(
                    {
                        "schema": "rrc25_full_window_reproduction_acceptance_v2",
                        "receipt": semantic,
                    }
                ),
            }
            _write_json(path, damaged)
            with self.assertRaisesRegex(
                acceptance.IranResearchAcceptanceError,
                "双 core|business|不一致",
            ):
                acceptance._update_acceptance_light(path)

    def test_sealed_segment_chain_uses_payload_state_without_observation_reread(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            journal_root, compatible, revised, bindings = _completed_journal(
                parent, slots=2
            )
            root = parent / "finalization-workspace"
            finalization_workspace.initialize_finalization_workspace(
                root, journal_root=journal_root, bindings=bindings
            )
            finalization_workspace.run_finalization_workspace_segment(
                root,
                compatible_mapping=compatible,
                revised_mapping=revised,
                max_slots=2,
            )
            terminal = finalization_workspace._verify_fingerprinted(
                finalization_workspace._load_json(root / "TERMINAL", "TERMINAL"),
                finalization_workspace.WORKSPACE_TERMINAL_SCHEMA,
                "TERMINAL",
            )
            receipt_refs = terminal["segment_receipt_refs"]
            payload_refs = [
                finalization_workspace._load_segment_receipt(root, ref)[
                    "segment_payload_ref"
                ]
                for ref in receipt_refs
            ]
            contents = []
            for ref in receipt_refs:
                contents.append(
                    {
                        "kind": "finalization-segment-receipt",
                        **dict(ref),
                        "record_count": 1,
                    }
                )
            for ref in payload_refs:
                contents.append(
                    {
                        "kind": "finalization-segment-payload",
                        **dict(ref),
                        "record_count": 1,
                    }
                )
            package = {
                "package_root": str(root),
                "manifest": {"contents": contents},
                "terminal": terminal,
                "segment_index": {
                    "segment_receipt_refs": receipt_refs,
                    "segment_payload_refs": payload_refs,
                },
            }
            with patch.object(
                finalizer,
                "_read_record_observation_shard_once",
                side_effect=AssertionError(
                    "overall acceptance 不得回读 record observations"
                ),
            ):
                chain = acceptance._sealed_segment_chain(package)
            self.assertEqual(len(chain), 2)
            self.assertTrue(
                all(
                    row["payload"]["record_observation_summary"]
                    and row["receipt"]["next_compact_state"]
                    for row in chain
                )
            )
            damaged = deepcopy(package)
            damaged["segment_index"]["segment_payload_refs"] = list(
                reversed(payload_refs)
            )
            with self.assertRaisesRegex(
                acceptance.IranResearchAcceptanceError,
                "receipt/payload/state",
            ):
                acceptance._sealed_segment_chain(damaged)

    def test_projection_difference_classes_and_semantic_sha_are_complete(self):
        matched = _projection_row("1", prefix="10.0.0.0/24", path_tail=65001)
        changed_rib = _projection_row("2", prefix="10.0.1.0/24", path_tail=65002)
        changed_update = deepcopy(changed_rib)
        changed_update["as_path"][0]["asns"][-1] = 65022
        changed_update["origin_asns"] = [65022]
        only_rib = _projection_row("3", prefix="10.0.2.0/24", path_tail=65003)
        only_update = _projection_row("4", prefix="10.0.3.0/24", path_tail=65004)
        rib_rows = sorted([matched, changed_rib, only_rib], key=acceptance._projection_key)
        update_rows = sorted([matched, changed_update, only_update], key=acceptance._projection_key)
        update = {
            "semantics": PROJECTION_SEMANTICS,
            "semantic_sha256": _projection_sha256(update_rows),
            "rows": update_rows,
        }
        result = acceptance._compare_projections(
            boundary_at_utc="2026-02-28T00:00:00Z",
            anchor_receipt=_anchor_receipt(rib_rows),
            rib_rows=rib_rows,
            update_projection=update,
        )
        self.assertEqual(
            result["counts"],
            {
                "matched": 1,
                "missing_in_update": 1,
                "missing_in_rib": 1,
                "path_changed": 1,
            },
        )
        self.assertEqual(len(result["semantic_sha256"]), 64)
        self.assertEqual(result["update_curve_action"], "none_independent_reconciliation_only")

        tampered = deepcopy(update)
        tampered["rows"][0]["peer_asn"] = 1
        with self.assertRaisesRegex(acceptance.IranResearchAcceptanceError, "语义 SHA"):
            acceptance._compare_projections(
                boundary_at_utc="2026-02-28T00:00:00Z",
                anchor_receipt=_anchor_receipt(rib_rows),
                rib_rows=rib_rows,
                update_projection=tampered,
            )

    def test_baseline_reference_cannot_masquerade_as_update_boundary(self):
        rows = [_projection_row("1", prefix="10.0.0.0/24", path_tail=65001)]
        update = {
            "semantics": PROJECTION_SEMANTICS,
            "semantic_sha256": _projection_sha256(rows),
            "rows": rows,
        }
        with self.assertRaisesRegex(acceptance.IranResearchAcceptanceError, "baseline"):
            acceptance._compare_projections(
                boundary_at_utc="2026-02-28T00:00:00Z",
                anchor_receipt=_anchor_receipt(rows, role="baseline_reference_rib"),
                rib_rows=rows,
                update_projection=update,
            )

    def test_four_category_scan_closes_raw_links_and_discloses_empty_populations(self):
        rows = (
            _episode_row(
                1,
                ipv4_invisible=True,
                ipv6_invisible=False,
                classification="ipv4_only_fully_invisible",
                cumulative=True,
                end_member=True,
            ),
            _episode_row(
                2,
                ipv4_invisible=False,
                ipv6_invisible=False,
                classification="partially_visible",
                cumulative=True,
                end_member=False,
            ),
        )
        route_id = _link()["route_event_id"]
        result = acceptance._four_category_scan(
            rows,
            resolutions={route_id: _resolution()},
            manifest_index=_manifest_index(),
        )
        by_id = {row["category_id"]: row for row in result["categories"]}
        self.assertEqual(by_id["ipv4_fully_invisible"]["population_count"], 1)
        self.assertEqual(by_id["partially_visible"]["population_count"], 1)
        self.assertEqual(by_id["ipv6_still_visible"]["population_count"], 2)
        self.assertEqual(by_id["observation_end_not_recovered"]["population_count"], 1)
        self.assertTrue(result["reference_closure"])

        no_population = (
            _episode_row(
                3,
                ipv4_invisible=False,
                ipv6_invisible=False,
                classification="not_affected",
                cumulative=False,
                end_member=False,
                with_link=False,
            ),
        )
        empty = acceptance._four_category_scan(
            no_population, resolutions={}, manifest_index=_manifest_index()
        )
        self.assertTrue(
            all(
                row["scan_state"] == "not_observed_after_full_scan"
                for row in empty["categories"]
            )
        )
        self.assertTrue(
            all(row["empty_population_is_not_sample_success"] for row in empty["categories"])
        )

    def test_population_without_evidence_is_blocking_and_raw_ref_tamper_fails(self):
        rows = (
            _episode_row(
                1,
                ipv4_invisible=True,
                ipv6_invisible=True,
                classification="dual_stack_fully_invisible",
                cumulative=True,
                end_member=True,
                with_link=False,
            ),
        )
        result = acceptance._four_category_scan(
            rows, resolutions={}, manifest_index=_manifest_index()
        )
        self.assertFalse(result["reference_closure"])
        self.assertTrue(any(row["blocking"] for row in result["categories"]))

        linked = (deepcopy(rows[0]),)
        linked[0]["evidence_links"] = [_link()]
        broken = _resolution()
        broken["raw_record_ref"]["record_ordinal"] = 8
        with self.assertRaisesRegex(acceptance.IranResearchAcceptanceError, "同一 RouteEvent/raw"):
            acceptance._four_category_scan(
                linked,
                resolutions={_link()["route_event_id"]: broken},
                manifest_index=_manifest_index(),
            )

    def test_aggregate_requires_exact_twenty_one_boundaries_and_one_separate_baseline(self):
        boundaries = []
        for index in range(21):
            boundaries.append(
                {
                    "boundary_at_utc": (
                        acceptance._time("2026-02-27T16:00:00Z")
                        + acceptance.timedelta(hours=8 * index)
                    ).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "counts": {
                        "matched": 1,
                        "missing_in_update": 2,
                        "missing_in_rib": 3,
                        "path_changed": 4,
                    },
                    "semantic_sha256": f"{index:064x}"[-64:],
                }
            )
        baseline = {
            "role": "baseline_reference_rib",
            "compared_to_update_boundary": False,
        }
        result = acceptance._aggregate_reconciliation(boundaries, baseline)
        self.assertEqual(result["aggregate_counts"]["matched"], 21)
        self.assertEqual(result["baseline_reference_rib_count"], 1)
        with self.assertRaisesRegex(acceptance.IranResearchAcceptanceError, "21"):
            acceptance._aggregate_reconciliation(boundaries[:-1], baseline)

    def test_supervisor_sends_term_and_kill_for_uncooperative_child(self):
        child = (
            "import signal,time; "
            "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
            "time.sleep(10)"
        )
        result = acceptance_cli._supervise_child(
            [sys.executable, "-c", child],
            observation_seconds=0.02,
            term_seconds=0.04,
            kill_seconds=0.08,
            poll_seconds=0.005,
        )
        self.assertFalse(result["successful"])
        self.assertTrue(result["actions"]["observation_boundary_crossed"])
        self.assertTrue(result["actions"]["term_sent"])
        self.assertTrue(result["actions"]["kill_sent"])

    def test_create_only_overall_receipt_rejects_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "overall.json"
            candidate = {
                "workspace_root": str(root.resolve()),
                "acceptance_state": "accepted",
                "acceptance_semantics": acceptance.ACCEPTANCE_SEMANTICS,
            }
            supervision = acceptance.build_successful_supervision_evidence(
                command_kind="overall-acceptance-finalize", elapsed_seconds=1.0
            )
            acceptance.publish_overall_research_acceptance(
                root,
                output_receipt_path=output,
                candidate=candidate,
                supervision=supervision,
            )
            with self.assertRaises(FileExistsError):
                acceptance.publish_overall_research_acceptance(
                    root,
                    output_receipt_path=output,
                    candidate=candidate,
                    supervision=supervision,
                )

    def test_status_reports_missing_anchor_gate_without_claiming_ready(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "workspace"
            (root / "segments").mkdir(parents=True)
            (root / "supervisors").mkdir()
            plans = [
                {
                    "segment_index": index,
                    "role": "fixture",
                    "start_receipt_sequence_inclusive": index,
                    "end_receipt_sequence_inclusive": index,
                    "boundary_at_utc": None,
                    "analysis_rib_artifact_id": None,
                }
                for index in range(22)
            ]
            genesis = acceptance._fingerprinted(
                acceptance.WORKSPACE_GENESIS_SCHEMA_VERSION,
                {"segment_plan": plans},
            )
            write_canonical_json(root / "GENESIS.json", genesis, kind="fixture")
            status = acceptance.acceptance_workspace_status(root)
            self.assertFalse(status["anchor_deep_verification_gate_ready"])
            self.assertEqual(status["next_segment_index"], 0)
            self.assertFalse(status["ready_to_finalize"])

    def test_segment_is_create_only_and_detects_byte_tamper(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "workspace"
            (root / "segments").mkdir(parents=True)
            (root / "supervisors").mkdir()
            plans = [
                {
                    "segment_index": index,
                    "role": "fixture",
                    "start_receipt_sequence_inclusive": index,
                    "end_receipt_sequence_inclusive": index,
                    "boundary_at_utc": None,
                    "analysis_rib_artifact_id": None,
                }
                for index in range(22)
            ]
            genesis = acceptance._fingerprinted(
                acceptance.WORKSPACE_GENESIS_SCHEMA_VERSION,
                {"segment_plan": plans},
            )
            write_canonical_json(root / "GENESIS.json", genesis, kind="fixture")
            candidate = {
                "segment_index": 0,
                "workspace_genesis_fingerprint_sha256": genesis[
                    "fingerprint_sha256"
                ],
                "plan": plans[0],
                "predecessor_segment_ref": None,
                "ending_compact_state": {},
                "boundary_reconciliation": None,
                "baseline_reference": None,
                "evidence_resolutions": [],
                "source_refs": [],
                "source_refs_semantic_sha256": "0" * 64,
                "resources": {
                    "real_mrt_raw_bytes_read": 0,
                    "record_observation_shard_reads": 0,
                    "database_writes": 0,
                    "temporary_bytes_exclusive_limit": 5_000_000_000,
                },
            }
            supervision = acceptance.build_successful_supervision_evidence(
                command_kind="reconciliation-segment-00", elapsed_seconds=1.0
            )
            acceptance.publish_reconciliation_segment(
                root, candidate=candidate, supervision=supervision
            )
            with self.assertRaises(FileExistsError):
                acceptance.publish_reconciliation_segment(
                    root, candidate=candidate, supervision=supervision
                )
            path = root / acceptance._segment_relative(0)
            damaged = bytearray(path.read_bytes())
            damaged[-1] ^= 1
            path.chmod(0o640)
            path.write_bytes(damaged)
            with self.assertRaises(acceptance.IranResearchAcceptanceError):
                acceptance._load_segment(root, 0)


if __name__ == "__main__":
    unittest.main()
