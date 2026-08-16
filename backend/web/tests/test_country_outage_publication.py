import json

import pytest

from data_pipeline.country_outage_publication import (
    CountryOutagePublicationError,
    publish_country_outage,
)
from services.country_outage_registry import country_outage_publication


INCIDENT_ID = "incident-publication-test"


def _package(path):
    path.mkdir()
    (path / "COMPLETE.json").write_text(
        '{"status":"complete"}\n',
        encoding="utf-8",
    )
    return path


def _registry(path, package):
    payload = {
        "schema_version": "country_outage_observation_registry_v1",
        "observations": [
            {
                "incident_id": INCIDENT_ID,
                "legacy_reference": (
                    "country_outage/2026-03-01 00:00:00/ZZ/1/r"
                ),
                "country": {"code": "ZZ", "name": "验收样本"},
                "package_uri": str(package),
                "revision": 1,
                "publication_state": "published",
                "observation_state": "state_complete",
                "data_mode": "live",
                "interval_seconds": 300,
                "data_through": "2026-03-01T00:00:00Z",
                "updated_at": "2026-03-01T00:00:01Z",
                "is_final": False,
            }
        ],
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def _candidate(package, **overrides):
    candidate = {
        "package_uri": str(package),
        "revision": 1,
        "publication_state": "published",
        "observation_state": "state_complete",
        "data_mode": "live",
        "data_through": "2026-03-01T00:05:00Z",
        "updated_at": "2026-03-01T00:05:01Z",
        "is_final": False,
    }
    candidate.update(overrides)
    return candidate


def test_normal_append_is_atomic_and_retains_previous_publication(
    tmp_path,
    monkeypatch,
):
    first_package = _package(tmp_path / "package-a")
    second_package = _package(tmp_path / "package-b")
    registry_path = _registry(tmp_path / "registry.json", first_package)
    before = registry_path.read_bytes()
    monkeypatch.setattr(
        "data_pipeline.country_outage_publication._inspect_package",
        lambda path: (
            {
                "incident_id": INCIDENT_ID,
                "last_observation_at": "2026-03-01T00:00:00Z",
                "cohort_sha256": "same-cohort",
                "slot_fingerprints": [
                    {
                        "observed_at": "2026-03-01T00:00:00Z",
                        "sha256": "a",
                    }
                ],
            }
            if path.name == "package-a"
            else {
                "incident_id": INCIDENT_ID,
                "last_observation_at": "2026-03-01T00:05:00Z",
                "cohort_sha256": "same-cohort",
                "slot_fingerprints": [
                    {
                        "observed_at": "2026-03-01T00:00:00Z",
                        "sha256": "a",
                    },
                    {
                        "observed_at": "2026-03-01T00:05:00Z",
                        "sha256": "b",
                    },
                ],
            }
        ),
    )

    dry_run = publish_country_outage(
        registry_path,
        incident_id=INCIDENT_ID,
        publication=_candidate(second_package),
        kind="append",
        dry_run=True,
    )
    assert dry_run["status"] == "validated"
    assert registry_path.read_bytes() == before

    result = publish_country_outage(
        registry_path,
        incident_id=INCIDENT_ID,
        publication=_candidate(second_package),
        kind="append",
    )
    payload = json.loads(registry_path.read_text(encoding="utf-8"))
    observation = payload["observations"][0]
    assert result["status"] == "published"
    assert result["revision"] == 1
    assert observation["current_publication_id"] == result["publication_id"]
    assert len(observation["publications"]) == 2
    assert observation["publications"][0]["data_through"] == (
        "2026-03-01T00:00:00Z"
    )
    assert observation["publications"][1]["data_through"] == (
        "2026-03-01T00:05:00Z"
    )


def test_failed_append_does_not_modify_registry(tmp_path, monkeypatch):
    first_package = _package(tmp_path / "package-a")
    second_package = _package(tmp_path / "package-b")
    registry_path = _registry(tmp_path / "registry.json", first_package)
    before = registry_path.read_bytes()
    monkeypatch.setattr(
        "data_pipeline.country_outage_publication._inspect_package",
        lambda path: (
            {
                "incident_id": INCIDENT_ID,
                "last_observation_at": "2026-03-01T00:00:00Z",
                "cohort_sha256": "same-cohort",
                "slot_fingerprints": [
                    {
                        "observed_at": "2026-03-01T00:00:00Z",
                        "sha256": "a",
                    }
                ],
            }
            if path.name == "package-a"
            else {
                "incident_id": INCIDENT_ID,
                "last_observation_at": "2026-03-01T00:00:00Z",
                "cohort_sha256": "same-cohort",
                "slot_fingerprints": [
                    {
                        "observed_at": "2026-03-01T00:00:00Z",
                        "sha256": "a",
                    },
                    {
                        "observed_at": "2026-03-01T00:05:00Z",
                        "sha256": "b",
                    },
                ],
            }
        ),
    )

    with pytest.raises(
        CountryOutagePublicationError,
        match="推进连续 data_through",
    ):
        publish_country_outage(
            registry_path,
            incident_id=INCIDENT_ID,
            publication=_candidate(
                second_package,
                data_through="2026-03-01T00:00:00Z",
            ),
            kind="append",
        )

    assert registry_path.read_bytes() == before


def test_append_rejects_changed_published_slot(tmp_path, monkeypatch):
    first_package = _package(tmp_path / "package-a")
    second_package = _package(tmp_path / "package-b")
    registry_path = _registry(tmp_path / "registry.json", first_package)
    before = registry_path.read_bytes()
    monkeypatch.setattr(
        "data_pipeline.country_outage_publication._inspect_package",
        lambda path: (
            {
                "incident_id": INCIDENT_ID,
                "last_observation_at": "2026-03-01T00:00:00Z",
                "cohort_sha256": "same-cohort",
                "slot_fingerprints": [
                    {
                        "observed_at": "2026-03-01T00:00:00Z",
                        "sha256": "original",
                    }
                ],
            }
            if path.name == "package-a"
            else {
                "incident_id": INCIDENT_ID,
                "last_observation_at": "2026-03-01T00:05:00Z",
                "cohort_sha256": "same-cohort",
                "slot_fingerprints": [
                    {
                        "observed_at": "2026-03-01T00:00:00Z",
                        "sha256": "changed",
                    },
                    {
                        "observed_at": "2026-03-01T00:05:00Z",
                        "sha256": "new",
                    },
                ],
            }
        ),
    )

    with pytest.raises(
        CountryOutagePublicationError,
        match="已发布槽位不变",
    ):
        publish_country_outage(
            registry_path,
            incident_id=INCIDENT_ID,
            publication=_candidate(second_package),
            kind="append",
        )

    assert registry_path.read_bytes() == before


def test_correction_requires_new_revision_and_explicit_supersession(
    tmp_path,
    monkeypatch,
):
    first_package = _package(tmp_path / "package-a")
    second_package = _package(tmp_path / "package-b")
    registry_path = _registry(tmp_path / "registry.json", first_package)
    monkeypatch.setattr(
        "data_pipeline.country_outage_publication._inspect_package",
        lambda path: {
            "incident_id": INCIDENT_ID,
            "last_observation_at": "2026-03-01T00:05:00Z",
            "cohort_sha256": "same-cohort",
            "slot_fingerprints": [
                {
                    "observed_at": "2026-03-01T00:05:00Z",
                    "sha256": "b",
                }
            ],
        },
    )

    with pytest.raises(
        CountryOutagePublicationError,
        match="revision 精确增加 1",
    ):
        publish_country_outage(
            registry_path,
            incident_id=INCIDENT_ID,
            publication=_candidate(second_package),
            kind="correction",
        )

    current_payload = json.loads(registry_path.read_text(encoding="utf-8"))
    assert "publications" not in current_payload["observations"][0]


def test_status_publication_preserves_data_and_exposes_failure(
    tmp_path,
    monkeypatch,
):
    first_package = _package(tmp_path / "package-a")
    registry_path = _registry(tmp_path / "registry.json", first_package)
    monkeypatch.setattr(
        "data_pipeline.country_outage_publication._inspect_package",
        lambda path: {
            "incident_id": INCIDENT_ID,
            "last_observation_at": "2026-03-01T00:00:00Z",
            "cohort_sha256": "same-cohort",
            "slot_fingerprints": [
                {
                    "observed_at": "2026-03-01T00:00:00Z",
                    "sha256": "a",
                }
            ],
        },
    )

    result = publish_country_outage(
        registry_path,
        incident_id=INCIDENT_ID,
        publication=_candidate(
            first_package,
            data_through="2026-03-01T00:00:00Z",
            updated_at="2026-03-01T00:06:00Z",
            processing_status={
                "state": "failed",
                "updated_at": "2026-03-01T00:06:00Z",
                "attempted_through": "2026-03-01T00:05:00Z",
                "reason": "parse_failed",
                "last_complete_data_through": "2026-03-01T00:00:00Z",
            },
        ),
        kind="status",
    )

    payload = json.loads(registry_path.read_text(encoding="utf-8"))
    observation = payload["observations"][0]
    current = country_outage_publication(observation)
    assert result["processing_state"] == "failed"
    assert current["revision"] == 1
    assert current["data_through"] == "2026-03-01T00:00:00Z"
    assert current["processing_status"]["reason"] == "parse_failed"
    assert len(observation["publications"]) == 2


def test_correction_keeps_old_publication_and_increments_revision(
    tmp_path,
    monkeypatch,
):
    first_package = _package(tmp_path / "package-a")
    corrected_package = _package(tmp_path / "package-corrected")
    registry_path = _registry(tmp_path / "registry.json", first_package)
    monkeypatch.setattr(
        "data_pipeline.country_outage_publication._inspect_package",
        lambda path: (
            {
                "incident_id": INCIDENT_ID,
                "last_observation_at": "2026-03-01T00:00:00Z",
                "cohort_sha256": "same-cohort",
                "slot_fingerprints": [
                    {
                        "observed_at": "2026-03-01T00:00:00Z",
                        "sha256": "a",
                    }
                ],
            }
            if path.name == "package-a"
            else {
                "incident_id": INCIDENT_ID,
                "last_observation_at": "2026-03-01T00:05:00Z",
                "cohort_sha256": "same-cohort",
                "slot_fingerprints": [
                    {
                        "observed_at": "2026-03-01T00:00:00Z",
                        "sha256": "corrected-a",
                    },
                    {
                        "observed_at": "2026-03-01T00:05:00Z",
                        "sha256": "b",
                    },
                ],
            }
        ),
    )
    initial_payload = json.loads(registry_path.read_text(encoding="utf-8"))
    initial_publication = country_outage_publication(
        initial_payload["observations"][0]
    )["publication_id"]

    result = publish_country_outage(
        registry_path,
        incident_id=INCIDENT_ID,
        publication=_candidate(
            corrected_package,
            revision=2,
            supersedes_publication_id=initial_publication,
            correction_reason="迟到源数据补齐并重算历史槽",
            missing_slots=[],
        ),
        kind="correction",
    )

    payload = json.loads(registry_path.read_text(encoding="utf-8"))
    observation = payload["observations"][0]
    current = country_outage_publication(observation)
    old = country_outage_publication(observation, initial_publication)
    assert result["revision"] == 2
    assert current["supersedes_publication_id"] == initial_publication
    assert current["correction_reason"] == "迟到源数据补齐并重算历史槽"
    assert old["revision"] == 1
    assert len(observation["publications"]) == 2
