from __future__ import annotations

import gzip
import hashlib
from pathlib import Path
import tempfile
import unittest

import load_route_metric_candidate as loader


class RouteMetricCandidateLoaderTest(unittest.TestCase):
    def _file(self, root: Path, role: str, index: int) -> dict[str, object]:
        header = {
            "country_metric": loader.METRIC_COLUMNS,
            "asn_metric_change": loader.METRIC_COLUMNS,
            "collector_metric": loader.METRIC_COLUMNS,
            "metric_slot": loader.SLOT_COLUMNS,
            "metric_subject": loader.SUBJECT_COLUMNS,
        }[role]
        directory = "registry" if role == "metric_subject" else (
            "quality" if role == "metric_slot" else "metrics"
        )
        relative = f"{directory}/{role}-{index:02d}.tsv.gz"
        raw = ("\t".join(header) + "\n" + "\t".join("0" for _ in header) + "\n").encode()
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as target:
            with gzip.GzipFile(filename="", mode="wb", fileobj=target, mtime=0) as compressed:
                compressed.write(raw)
        compressed = path.read_bytes()
        result: dict[str, object] = {
            "path": relative,
            "role": role,
            "row_count": 1,
            "size_bytes": len(compressed),
            "sha256": hashlib.sha256(compressed).hexdigest(),
            "content_sha256": hashlib.sha256(raw).hexdigest(),
        }
        if role != "metric_subject":
            result["date_utc"] = "2026-02-24"
        return result

    def test_metric_file_population_and_tamper_close(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            files: list[dict[str, object]] = []
            for role in (
                "country_metric", "asn_metric_change", "collector_metric", "metric_slot",
            ):
                files.extend(self._file(root, role, index) for index in range(15))
            files.append(self._file(root, "metric_subject", 0))
            validated = loader.validate_metric_files(root, {"files": files})
            self.assertEqual(len(validated), 61)

            target = root / str(files[0]["path"])
            with target.open("ab") as output:
                output.write(b"tamper")
            with self.assertRaises(loader.LoadError):
                loader.validate_metric_files(root, {"files": files})

    def test_evidence_insert_keeps_content_sha_and_integrity_in_separate_columns(self) -> None:
        class RecordingDatabase:
            def __init__(self) -> None:
                self.statements: list[str] = []

            def psql(self, sql: str) -> None:
                self.statements.append(sql)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            metric_manifest = root / "manifest.json"
            route_event_manifest = root / "route-event-manifest.json"
            route_state_manifest = root / "route-state-manifest.json"
            for path, content in (
                (metric_manifest, b"metric"),
                (route_event_manifest, b"event"),
                (route_state_manifest, b"state"),
            ):
                path.write_bytes(content)
            database = RecordingDatabase()
            manifest = {
                "candidate_id": "candidate_v1",
                "dataset_id": "metric_v1",
                "content_sha256": "c" * 64,
                "source_route_event_dataset_id": "event_v1",
                "source_route_event_content_sha256": "e" * 64,
                "source_route_state_dataset_id": "state_v1",
                "source_route_state_content_sha256": "s" * 64,
            }

            loader.insert_evidence_objects(
                database, root, metric_manifest, manifest, [],
                route_event_manifest, route_state_manifest,
            )

            sql = database.statements[0]
            self.assertIn(
                "row_count,content_sha256,integrity_status) VALUES", sql,
            )
            self.assertIn("NULL, 'e" + "e" * 63 + "', 'verified');", sql)
            self.assertIn("NULL, 's" + "s" * 63 + "', 'verified');", sql)
            self.assertIn("NULL, 'c" + "c" * 63 + "', 'verified');", sql)


if __name__ == "__main__":
    unittest.main()
