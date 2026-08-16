from __future__ import annotations

import unittest

from backend.data_pipeline.research.resource_gate import (
    DEFAULT_HARD_RUNTIME_SECONDS,
    DEFAULT_MAX_NEW_RAW_READ_BYTES,
    DEFAULT_MAX_TEMPORARY_BYTES,
    DEFAULT_SOFT_RUNTIME_SECONDS,
    ResourceGateInputError,
    ResourceLimits,
    ResourceUsage,
    WriteTarget,
    estimate_resource_usage,
    evaluate_resource_gate,
)


def usage(**overrides: object) -> ResourceUsage:
    values: dict[str, object] = {
        "new_raw_read_bytes": 1,
        "process_runtime_seconds": 1,
        "temporary_bytes": 1,
        "output_bytes": 1,
        "write_targets": (
            WriteTarget(
                label="research-artifact",
                location="/srv/domeye-research/staging/run-1",
                kind="artifact",
            ),
        ),
        "phase": "estimated",
    }
    values.update(overrides)
    return ResourceUsage(**values)  # type: ignore[arg-type]


def finding_codes(result: object) -> set[str]:
    return {finding.code for finding in result.findings}  # type: ignore[attr-defined]


class ResourceLimitsTest(unittest.TestCase):
    def test_defaults_are_decimal_boundaries(self):
        limits = ResourceLimits()

        self.assertEqual(DEFAULT_MAX_NEW_RAW_READ_BYTES, 50_000_000_000)
        self.assertEqual(DEFAULT_MAX_TEMPORARY_BYTES, 5_000_000_000)
        self.assertEqual(DEFAULT_HARD_RUNTIME_SECONDS, 600)
        self.assertEqual(DEFAULT_SOFT_RUNTIME_SECONDS, 540)
        self.assertEqual(limits.max_new_raw_read_bytes, 50_000_000_000)
        self.assertEqual(limits.max_temporary_bytes, 5_000_000_000)

    def test_loads_exact_verified_profile_fields(self):
        limits = ResourceLimits.from_profile(
            {
                "resource_limits": {
                    "max_new_raw_read_bytes": 40_000,
                    "max_temporary_bytes": 4_000,
                    "max_worker_runtime_seconds": 500,
                    "worker_soft_stop_seconds": 450,
                    "database_writes": "forbidden",
                    "output_storage": "filesystem_only",
                }
            }
        )

        self.assertEqual(limits.max_new_raw_read_bytes, 40_000)
        self.assertEqual(limits.hard_runtime_seconds, 500)
        self.assertEqual(limits.soft_runtime_seconds, 450)

    def test_profile_rejects_unknown_or_relaxed_policy(self):
        with self.assertRaisesRegex(ResourceGateInputError, "未知字段"):
            ResourceLimits.from_profile(
                {"resource_limits": {"max_raw_gib": 50}}
            )
        with self.assertRaisesRegex(ResourceGateInputError, "database_writes"):
            ResourceLimits(database_writes="allowed")
        with self.assertRaisesRegex(ResourceGateInputError, "output_storage"):
            ResourceLimits(output_storage="database")

    def test_soft_limit_must_be_strictly_below_hard_limit(self):
        with self.assertRaisesRegex(ResourceGateInputError, "严格小于"):
            ResourceLimits(
                max_worker_runtime_seconds=540,
                worker_soft_stop_seconds=540,
            )


class ResourceEstimateTest(unittest.TestCase):
    def test_estimate_counts_every_planned_pass_and_tracks_output(self):
        result = estimate_resource_usage(
            raw_input_sizes_bytes=(10, 20, 30),
            raw_read_passes=2,
            process_runtime_seconds=12.5,
            temporary_bytes=40,
            output_bytes=50,
            write_targets=usage().write_targets,
        )

        self.assertEqual(result.new_raw_read_bytes, 120)
        self.assertEqual(result.process_runtime_seconds, 12.5)
        self.assertEqual(result.temporary_bytes, 40)
        self.assertEqual(result.output_bytes, 50)

    def test_estimate_rejects_invalid_sizes_and_pass_count(self):
        with self.assertRaisesRegex(ResourceGateInputError, "非负整数"):
            estimate_resource_usage(raw_input_sizes_bytes=(1, -1))
        with self.assertRaisesRegex(ResourceGateInputError, "大于 0"):
            estimate_resource_usage(raw_input_sizes_bytes=(1,), raw_read_passes=0)


class ResourceGateBoundaryTest(unittest.TestCase):
    def test_values_just_below_every_boundary_are_allowed(self):
        result = evaluate_resource_gate(
            usage(
                new_raw_read_bytes=49_999_999_999,
                process_runtime_seconds=539.999,
                temporary_bytes=4_999_999_999,
                output_bytes=99_000_000_000,
            )
        )

        self.assertEqual(result.decision, "allowed")
        self.assertTrue(result.execution_allowed)
        self.assertFalse(result.checkpoint_required)
        self.assertFalse(result.approval_required)

    def test_exact_soft_runtime_boundary_requires_record_checkpoint(self):
        result = evaluate_resource_gate(
            usage(process_runtime_seconds=540, phase="observed")
        )

        self.assertEqual(result.decision, "soft_stop")
        self.assertFalse(result.execution_allowed)
        self.assertTrue(result.checkpoint_required)
        self.assertFalse(result.approval_required)
        self.assertEqual(finding_codes(result), {"runtime_soft_limit_reached"})

    def test_exact_hard_boundaries_fail_closed_and_request_approval(self):
        cases = (
            (
                {"new_raw_read_bytes": 50_000_000_000},
                "new_raw_read_hard_limit_reached",
            ),
            ({"process_runtime_seconds": 600}, "runtime_hard_limit_reached"),
            (
                {"temporary_bytes": 5_000_000_000},
                "temporary_space_hard_limit_reached",
            ),
        )
        for overrides, expected_code in cases:
            with self.subTest(expected_code):
                result = evaluate_resource_gate(usage(**overrides))
                self.assertEqual(result.decision, "approval_required")
                self.assertFalse(result.execution_allowed)
                self.assertTrue(result.checkpoint_required)
                self.assertTrue(result.approval_required)
                self.assertIn(expected_code, finding_codes(result))

    def test_verified_profile_can_set_smaller_explicit_boundaries(self):
        limits = ResourceLimits(
            max_new_raw_read_bytes=100,
            max_temporary_bytes=50,
            max_worker_runtime_seconds=20,
            worker_soft_stop_seconds=15,
        )

        result = evaluate_resource_gate(
            usage(new_raw_read_bytes=100), limits=limits
        )

        self.assertEqual(result.decision, "approval_required")
        self.assertIn("new_raw_read_hard_limit_reached", finding_codes(result))


class ReadOnlyWriteTargetGateTest(unittest.TestCase):
    def test_database_kind_uri_dsn_and_file_suffix_are_all_blocked(self):
        targets = (
            WriteTarget("typed-db", "/tmp/value", kind="database"),
            WriteTarget(
                "postgres-dsn",
                "postgresql://user:secret@example.invalid/db",
                kind="file",
            ),
            WriteTarget("sqlite-file", "/tmp/research.sqlite3", kind="file"),
            WriteTarget(
                "keyword-dsn",
                "host=127.0.0.1 dbname=bgp_project",
                kind="file",
            ),
        )

        result = evaluate_resource_gate(usage(write_targets=targets))

        self.assertEqual(result.decision, "approval_required")
        self.assertFalse(result.execution_allowed)
        self.assertTrue(result.approval_required)
        self.assertEqual(
            [finding.code for finding in result.findings],
            ["database_write_target"] * 4,
        )
        serialized = result.to_dict()
        self.assertNotIn("secret", repr(serialized))
        self.assertNotIn("postgresql://", repr(serialized))

    def test_protected_flag_and_root_both_forbid_execution(self):
        targets = (
            WriteTarget(
                "explicit-protected",
                "/srv/research/output",
                protected=True,
            ),
            WriteTarget(
                "old-project",
                "/home/bgpdata/Domeye/backend/config.py",
            ),
        )

        result = evaluate_resource_gate(
            usage(write_targets=targets),
            protected_roots=("/home/bgpdata/Domeye",),
        )

        self.assertEqual(result.decision, "forbidden")
        self.assertFalse(result.execution_allowed)
        self.assertEqual(
            finding_codes(result), {"protected_write_target"}
        )

    def test_production_flag_kind_and_root_all_forbid_execution(self):
        targets = (
            WriteTarget("flagged", "/srv/research/a", production=True),
            WriteTarget("typed", "/srv/research/b", kind="production"),
            WriteTarget("rooted", "/srv/domeye-production/releases/r1"),
        )

        result = evaluate_resource_gate(
            usage(write_targets=targets),
            production_roots=("/srv/domeye-production",),
        )

        self.assertEqual(result.decision, "forbidden")
        self.assertEqual(
            [finding.code for finding in result.findings],
            ["production_write_target"] * 3,
        )

    def test_relative_unknown_and_non_file_uri_targets_fail_closed(self):
        targets = (
            WriteTarget("relative", "research/output"),
            WriteTarget("unknown", "/srv/research/output", kind="unknown"),
            WriteTarget("remote", "s3://bucket/research/output"),
        )

        result = evaluate_resource_gate(usage(write_targets=targets))

        self.assertEqual(result.decision, "forbidden")
        self.assertEqual(
            [finding.code for finding in result.findings],
            ["unclassified_write_target"] * 3,
        )

    def test_lexical_sibling_does_not_match_protected_root(self):
        result = evaluate_resource_gate(
            usage(
                write_targets=(
                    WriteTarget("sibling", "/home/bgpdata/Domeye-Core/research"),
                )
            ),
            protected_roots=("/home/bgpdata/Domeye",),
        )

        self.assertEqual(result.decision, "allowed")


class InputValidationTest(unittest.TestCase):
    def test_unknown_phase_negative_and_boolean_numbers_are_rejected(self):
        with self.assertRaisesRegex(ResourceGateInputError, "phase"):
            usage(phase="actual")
        with self.assertRaisesRegex(ResourceGateInputError, "非负整数"):
            usage(output_bytes=-1)
        with self.assertRaisesRegex(ResourceGateInputError, "非负整数"):
            usage(new_raw_read_bytes=True)

    def test_invalid_roots_fail_closed_without_path_resolution(self):
        with self.assertRaisesRegex(ResourceGateInputError, "绝对文件路径"):
            evaluate_resource_gate(usage(), protected_roots=("backend/core",))


if __name__ == "__main__":
    unittest.main()
