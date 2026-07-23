from __future__ import annotations

from dataclasses import replace
import gzip
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from backend.data_pipeline.research.rrc25_country_outage import (
    full_window_finalize_workspace as workspace,
    full_window_segment_package as segment_package,
    full_window_segment_product as segment_product,
    iran_research_acceptance as acceptance,
)
from dev.tests.test_rrc25_full_window_segment_product import (
    _completed_fixture,
)


def _product_and_business(base: Path):
    workspace_root, _journal_root, frozen = _completed_fixture(base)
    product = segment_product.build_segment_product_inputs(
        workspace_root, **frozen
    )
    business = segment_product.derive_business_outputs_from_segment_product(
        product
    )
    return workspace_root, product, business


def _generated_json(plan, relative: str):
    item = next(
        item for item in plan.items if item.relative_path == relative
    )
    if item.generated_bytes is None:
        raise AssertionError(f"{relative} 不是 generated item")
    return json.loads(item.generated_bytes.decode("utf-8"))


class FullWindowSegmentPackageTests(unittest.TestCase):
    def test_complete_v2_plan_covers_acceptance_population_and_binds_two_cores(
        self,
    ):
        with tempfile.TemporaryDirectory() as directory:
            workspace_root, product, business = _product_and_business(
                Path(directory)
            )
            before = {
                path.relative_to(workspace_root).as_posix(): (
                    path.stat().st_size,
                    path.stat().st_mtime_ns,
                )
                for path in workspace_root.rglob("*")
                if path.is_file() and not path.is_symlink()
            }
            original_open = os.open
            opened = []

            def guarded_open(path, flags, *args, **kwargs):
                rendered = str(path)
                if "record_observations" in rendered:
                    raise AssertionError(
                        "package plan 不得打开 record_observations"
                    )
                if flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT):
                    raise AssertionError("package plan 不得写文件")
                opened.append(rendered)
                return original_open(path, flags, *args, **kwargs)

            with patch.object(
                segment_package.os, "open", side_effect=guarded_open
            ):
                plan = (
                    segment_package.build_full_window_segment_package_plan(
                        product, business
                    )
                )

            after = {
                path.relative_to(workspace_root).as_posix(): (
                    path.stat().st_size,
                    path.stat().st_mtime_ns,
                )
                for path in workspace_root.rglob("*")
                if path.is_file() and not path.is_symlink()
            }
            self.assertEqual(before, after)
            self.assertEqual(
                {Path(path).name for path in opened},
                {"TERMINAL", "DEEP-VERIFICATION"},
            )
            paths = {item.relative_path for item in plan.items}
            self.assertTrue(acceptance._REQUIRED_BUSINESS_PATHS <= paths)
            self.assertTrue(
                segment_package.REQUIRED_FIXED_PACKAGE_PATHS <= paths
            )
            self.assertTrue(any(path.startswith("seed/") for path in paths))
            self.assertTrue(
                any(path.startswith("raw-ledger/") for path in paths)
            )
            self.assertTrue(
                any(
                    path.startswith("segments/receipts/")
                    for path in paths
                )
            )
            self.assertTrue(
                any(
                    path.startswith("segments/payloads/")
                    for path in paths
                )
            )
            self.assertTrue(
                any(
                    path.startswith("segments/deep-receipts/")
                    for path in paths
                )
            )
            self.assertEqual(
                {
                    item.materialization
                    for item in plan.items
                },
                {
                    segment_package.MATERIALIZATION_CANONICAL_JSON,
                    segment_package.MATERIALIZATION_CANONICAL_JSONL_GZIP,
                    segment_package.MATERIALIZATION_BYTES,
                    segment_package.MATERIALIZATION_VERIFIED_COPY_SOURCE,
                },
            )
            self.assertEqual(plan.database_write_operations, 0)
            self.assertEqual(plan.record_observation_reads, 0)
            self.assertEqual(plan.real_mrt_raw_bytes_read, 0)
            self.assertLess(
                plan.projected_regular_bytes,
                segment_package.DEFAULT_MAX_PROJECTED_BYTES,
            )
            terminal = workspace._verify_fingerprinted(
                workspace._load_json(
                    workspace_root / "TERMINAL", "TERMINAL"
                ),
                workspace.WORKSPACE_TERMINAL_SCHEMA,
                "TERMINAL",
            )
            deep = workspace._verify_fingerprinted(
                workspace._load_json(
                    workspace_root / "DEEP-VERIFICATION",
                    "DEEP-VERIFICATION",
                ),
                workspace.WORKSPACE_DEEP_VERIFICATION_SCHEMA,
                "DEEP-VERIFICATION",
            )
            self.assertEqual(
                plan.finalization_segment_core_sha256,
                workspace._workspace_semantic_core(terminal, deep),
            )
            self.assertEqual(
                plan.business_semantic_core_sha256,
                business.semantic_core_sha256,
            )
            metadata = _generated_json(
                plan, "metadata/finalization.json"
            )
            quality = _generated_json(
                plan, "quality-and-accounting.json"
            )
            index = _generated_json(plan, "segments/index.json")
            for value in (metadata, quality, index):
                self.assertEqual(
                    value["business_semantic_core_sha256"],
                    plan.business_semantic_core_sha256,
                )
                self.assertEqual(
                    value["finalization_segment_core_sha256"],
                    plan.finalization_segment_core_sha256,
                )
            self.assertEqual(
                metadata["semantic_core_sha256"],
                plan.finalization_segment_core_sha256,
            )
            self.assertEqual(
                index["semantic_core_sha256"],
                plan.finalization_segment_core_sha256,
            )
            self.assertEqual(
                index["record_observation_reads_during_assembly"], 0
            )
            self.assertEqual(index["database_write_operations"], 0)
            verification = (
                segment_package.verify_full_window_segment_package_plan(
                    plan
                )
            )
            self.assertTrue(verification["verified"])
            self.assertEqual(
                verification["content_item_count"], len(plan.items)
            )

    def test_plan_and_canonical_gzip_are_byte_deterministic(self):
        with tempfile.TemporaryDirectory() as directory:
            _workspace_root, product, business = _product_and_business(
                Path(directory)
            )
            first = segment_package.build_full_window_segment_package_plan(
                product, business
            )
            second = (
                segment_package.build_full_window_segment_package_plan(
                    product, business
                )
            )
            self.assertEqual(first, second)
            sequence = next(
                item
                for item in first.items
                if item.relative_path
                == "evidence/research-evidence-packages.jsonl.gz"
            )
            self.assertEqual(
                sequence.materialization,
                segment_package.MATERIALIZATION_CANONICAL_JSONL_GZIP,
            )
            self.assertIsNotNone(sequence.generated_bytes)
            rows = gzip.decompress(sequence.generated_bytes).decode("utf-8")
            self.assertTrue(rows.endswith("\n"))
            self.assertTrue(rows.strip())
            for line in rows.splitlines():
                value = json.loads(line)
                self.assertEqual(
                    line,
                    json.dumps(
                        value,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                        allow_nan=False,
                    ),
                )

    def test_business_path_collision_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            _workspace_root, product, business = _product_and_business(
                Path(directory)
            )
            collision_path = (
                "data/compatible-country-samples.jsonl.gz"
            )
            collided = replace(
                business,
                object_files={
                    **business.object_files,
                    collision_path: ("collision", {"unexpected": True}),
                },
            )
            with self.assertRaisesRegex(
                segment_package.FullWindowSegmentPackageError,
                "path 冲突",
            ):
                segment_package.build_full_window_segment_package_plan(
                    product, collided
                )

    def test_projected_bytes_is_strictly_exclusive_and_never_above_5gb(
        self,
    ):
        with tempfile.TemporaryDirectory() as directory:
            _workspace_root, product, business = _product_and_business(
                Path(directory)
            )
            baseline = (
                segment_package.build_full_window_segment_package_plan(
                    product, business
                )
            )
            with self.assertRaisesRegex(
                segment_package.FullWindowSegmentPackageError,
                "5GB 排他边界",
            ):
                segment_package.build_full_window_segment_package_plan(
                    product,
                    business,
                    maximum_projected_bytes=(
                        baseline.projected_regular_bytes
                    ),
                )
            with self.assertRaisesRegex(
                segment_package.FullWindowSegmentPackageError,
                "十进制 5GB",
            ):
                segment_package.build_full_window_segment_package_plan(
                    product,
                    business,
                    maximum_projected_bytes=(
                        segment_package.DEFAULT_MAX_PROJECTED_BYTES + 1
                    ),
                )


if __name__ == "__main__":
    unittest.main()
