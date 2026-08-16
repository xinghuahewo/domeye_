import json
from pathlib import Path

import pytest

from backend.info_pipeline.s5 import (
    S5AcceptanceError,
    _read_state,
    _transition_runtime_state,
)


CONTENT_ID = "info_v1_" + "a" * 32
MANIFEST_SHA256 = "b" * 64


def test_runtime_state_transitions_are_generation_checked_and_journaled(tmp_path):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    source_dir = tmp_path / "source"
    source_dir.mkdir()

    first = _transition_runtime_state(
        state_dir,
        backend="file",
        content_id=CONTENT_ID,
        manifest_sha256=MANIFEST_SHA256,
        source_dir=source_dir,
        release_sk=None,
        reason="baseline",
        expected_backend=None,
    )
    second = _transition_runtime_state(
        state_dir,
        backend="database",
        content_id=CONTENT_ID,
        manifest_sha256=MANIFEST_SHA256,
        source_dir=source_dir,
        release_sk=7,
        reason="activation",
        expected_backend="file",
    )

    assert first["generation"] == 1
    assert second["generation"] == 2
    assert _read_state(state_dir / "backend-state.json") == second
    journals = sorted((state_dir / "journal").glob("*.json"))
    assert [item.name for item in journals] == [
        "000001-file.json",
        "000002-database.json",
    ]
    assert json.loads(journals[1].read_text(encoding="utf-8")) == second


def test_runtime_state_transition_rejects_unexpected_previous_backend(tmp_path):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    _transition_runtime_state(
        state_dir,
        backend="file",
        content_id=CONTENT_ID,
        manifest_sha256=MANIFEST_SHA256,
        source_dir=source_dir,
        release_sk=None,
        reason="baseline",
        expected_backend=None,
    )

    with pytest.raises(S5AcceptanceError, match="前置不一致"):
        _transition_runtime_state(
            state_dir,
            backend="database",
            content_id=CONTENT_ID,
            manifest_sha256=MANIFEST_SHA256,
            source_dir=source_dir,
            release_sk=7,
            reason="activation",
            expected_backend="database",
        )


def test_s5_script_keeps_activation_offline_and_runs_stage_hook():
    root = Path(__file__).resolve().parents[2]
    script = (
        root / "deploy" / "database" / "accept-static-info-s5.sh"
    ).read_text(encoding="utf-8")

    assert "domeye_static_info_assert_offline_candidate" in script
    assert "--authorization-id" in script
    assert "--confirm-content-id" in script
    assert "production_activation_authorized == false" in script
    assert '${CORE_BACKEND_ROOT}/.venv/bin/python' in script
    assert script.index("trap cleanup EXIT") < script.index(
        "验收环境缺少 pandas"
    )
    assert "static-info-stage-end-hook.sh" in script
    assert "S5" in script
    assert ".incomplete." in script or "archive_incomplete_evidence" in script
