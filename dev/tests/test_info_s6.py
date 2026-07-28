import json
from pathlib import Path

import pytest

from backend.info_pipeline.runtime import (
    StaticInfoRuntimeError,
    read_runtime_backend_state,
)


CONTENT_ID = "info_v1_" + "a" * 32
MANIFEST_SHA256 = "b" * 64


def _state(**updates):
    value = {
        "schema_version": 1,
        "component": "static_info_runtime_backend_state",
        "generation": 4,
        "backend": "database",
        "content_id": CONTENT_ID,
        "manifest_sha256": MANIFEST_SHA256,
        "release_sk": 7,
        "changed_at": "2026-07-26T00:00:00+00:00",
        "reason": "test",
    }
    value.update(updates)
    return value


def test_runtime_state_accepts_only_explicit_database_release(tmp_path):
    state_path = tmp_path / "state.json"
    state_path.write_text(
        json.dumps(_state(), ensure_ascii=False),
        encoding="utf-8",
    )

    state = read_runtime_backend_state(state_path)

    assert state.backend == "database"
    assert state.generation == 4
    assert state.content_id == CONTENT_ID
    assert state.release_sk == 7


def test_runtime_state_rejects_implicit_file_fallback(tmp_path):
    state_path = tmp_path / "state.json"
    state_path.write_text(
        json.dumps(_state(backend="file"), ensure_ascii=False),
        encoding="utf-8",
    )

    with pytest.raises(StaticInfoRuntimeError, match="只接受"):
        read_runtime_backend_state(state_path)


def test_runtime_state_rejects_symlink_and_duplicate_keys(tmp_path):
    target = tmp_path / "target.json"
    target.write_text(json.dumps(_state()), encoding="utf-8")
    link = tmp_path / "link.json"
    link.symlink_to(target)
    with pytest.raises(StaticInfoRuntimeError, match="软链接"):
        read_runtime_backend_state(link)

    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text(
        '{"schema_version":1,"schema_version":1}',
        encoding="utf-8",
    )
    with pytest.raises(StaticInfoRuntimeError, match="重复键"):
        read_runtime_backend_state(duplicate)


def test_s6_script_requires_strace_and_final_hook():
    root = Path(__file__).resolve().parents[2]
    script = (
        root / "deploy" / "database" / "accept-static-info-s6.sh"
    ).read_text(encoding="utf-8")

    assert "strace" in script
    assert "runtime_direct_info_file_read_count == 0" in script
    assert "legacy_database_connection_count == 0" in script
    assert "--minimum-process-runs 12" in script
    assert "--minimum-observation-seconds 60" in script
    assert "static-info-stage-end-hook.sh" in script
    assert "S6" in script
