"""W5 确定性 Renderer 与本地可审计 Delivery。"""

from __future__ import annotations

import csv
from io import StringIO
from typing import Any, Mapping, Sequence

from .country_outage_p2_s1_contract_runtime import canonical_json, digest_hex
from .country_outage_p2_s1_result_set import validate_result_set
from .country_outage_p2_s1_trusted_store import ContentAddressedStore


_FORMATS = {"json", "csv", "markdown"}


class W5DeliveryError(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int = 409) -> None:
        self.code = code
        self.status_code = status_code
        self.retryable = False
        self.next_action = None
        super().__init__(message)


def _columns(members: Sequence[Mapping[str, Any]]) -> list[str]:
    return sorted({key for member in members for key in member})


def _cell(value: Any) -> str:
    return value if isinstance(value, str) else canonical_json(value)


def render_json(members: Sequence[Mapping[str, Any]]) -> bytes:
    return (canonical_json(list(members)) + "\n").encode("utf-8")


def render_csv(members: Sequence[Mapping[str, Any]]) -> bytes:
    output = StringIO(newline="")
    writer = csv.writer(output, dialect="excel", lineterminator="\n")
    columns = _columns(members)
    writer.writerow(columns)
    for member in members:
        writer.writerow([_cell(member.get(column)) for column in columns])
    return output.getvalue().encode("utf-8")


def _markdown_cell(value: Any) -> str:
    return _cell(value).replace("\\", "\\\\").replace("|", "\\|").replace("\r", " ").replace("\n", "<br>")


def render_markdown(members: Sequence[Mapping[str, Any]]) -> bytes:
    columns = _columns(members)
    if not columns:
        return b"| result |\n| --- |\n"
    lines = ["| " + " | ".join(columns) + " |", "| " + " | ".join("---" for _ in columns) + " |"]
    for member in members:
        lines.append("| " + " | ".join(_markdown_cell(member.get(column)) for column in columns) + " |")
    return ("\n".join(lines) + "\n").encode("utf-8")


def render_members(format_name: str, members: Sequence[Mapping[str, Any]]) -> bytes:
    if format_name == "json":
        return render_json(members)
    if format_name == "csv":
        return render_csv(members)
    if format_name == "markdown":
        return render_markdown(members)
    raise W5DeliveryError("export_format_invalid", f"不支持的导出格式：{format_name}", status_code=400)


class DeliveryManager:
    def __init__(self, store: ContentAddressedStore, dispatcher: Any | None = None) -> None:
        self.store = store
        self.dispatcher = dispatcher

    def create_export(
        self,
        *,
        investigation_id: str,
        investigation_revision: int,
        result_set: Mapping[str, Any],
        format_name: str,
        authorization_receipt_digest: str,
    ) -> dict[str, Any]:
        if format_name not in _FORMATS:
            raise W5DeliveryError("export_format_invalid", "format 必须是 json/csv/markdown", status_code=400)
        members = validate_result_set(result_set, self.store)
        if result_set.get("set_completeness") != "complete" or result_set.get("state") != "frozen":
            raise W5DeliveryError("export_source_not_frozen", "仅允许导出 frozen complete ResultSet")
        renderer_unit = {"markdown": "RENDERER-01", "csv": "RENDERER-02", "json": "RENDERER-03"}[format_name]
        renderer_input = {"renderer_unit_id": renderer_unit, "result_set_id": result_set["result_set_id"], "content_digest": result_set["content_digest"], "member_count": len(members)}
        content = {
            "markdown": render_markdown,
            "csv": render_csv,
            "json": render_json,
        }[format_name](members)
        artifact = self.store.put_bytes("result-set-export", content)
        renderer_output = {"renderer_unit_id": renderer_unit, "artifact_sha256": artifact["sha256"], "byte_length": artifact["byte_length"]}
        if self.dispatcher is not None:
            self.dispatcher.record_control_execution(renderer_unit, renderer_input, renderer_output)
        export_id = "exp_" + digest_hex({
            "investigation_id": investigation_id,
            "result_set_id": result_set["result_set_id"],
            "result_set_revision": result_set["result_set_revision"],
            "format": format_name,
            "artifact_sha256": artifact["sha256"],
        })
        manifest_base = {
            "schema_version": "country_outage_p2_s1_w5_export_manifest_v1",
            "export_id": export_id,
            "investigation_id": investigation_id,
            "investigation_revision": investigation_revision,
            "result_set_id": result_set["result_set_id"],
            "result_set_revision": result_set["result_set_revision"],
            "result_set_content_digest": result_set["content_digest"],
            "format": format_name,
            "renderer_unit_id": renderer_unit,
            "delivery_unit_id": "DELIVERY-01",
            "artifact_sha256": artifact["sha256"],
            "artifact_byte_length": artifact["byte_length"],
            "artifact_ref": artifact["artifact_ref"],
            "ordered_member_count": len(members),
            "ordered_member_digests": [digest_hex(member) for member in members],
            "authorization_receipt_digest": authorization_receipt_digest,
            "runtime_boundary": {"local_execution": True, "runtime_implemented": True, "production_deployed": False},
        }
        manifest = {**manifest_base, "manifest_digest": digest_hex(manifest_base)}
        receipt_base = {
            "receipt_kind": "delivery_commit",
            "export_id": export_id,
            "manifest_digest": manifest["manifest_digest"],
            "artifact_sha256": artifact["sha256"],
            "disposition": "committed_local_only",
        }
        receipt = {**receipt_base, "receipt_digest": digest_hex(receipt_base)}
        self.store.put_json("receipt", receipt)
        record = {**manifest, "delivery_receipt_digest": receipt["receipt_digest"]}
        if self.dispatcher is not None:
            delivery_input = {
                "delivery_unit_id": "DELIVERY-01",
                "result_set_id": result_set["result_set_id"], "result_set_revision": result_set["result_set_revision"],
                "format": format_name, "artifact_sha256": artifact["sha256"],
            }
            delivery_output = {
                "delivery_unit_id": "DELIVERY-01",
                "export_id": export_id, "artifact_sha256": artifact["sha256"], "delivery_receipt_digest": receipt["receipt_digest"],
            }
            self.dispatcher.record_control_execution("DELIVERY-01", delivery_input, delivery_output)
        stored = self.store.put_json("export", record)
        return {**record, "object_digest": stored["object_digest"]}

    def artifact(self, export: Mapping[str, Any]) -> tuple[bytes, str, str]:
        format_name = str(export["format"])
        content_type = {
            "json": "application/json",
            "csv": "text/csv; charset=utf-8",
            "markdown": "text/markdown; charset=utf-8",
        }[format_name]
        extension = {"json": "json", "csv": "csv", "markdown": "md"}[format_name]
        content = self.store.get_bytes("result-set-export", export["artifact_sha256"])
        return content, content_type, f"{export['export_id']}.{extension}"


__all__ = [
    "DeliveryManager",
    "W5DeliveryError",
    "render_csv",
    "render_json",
    "render_markdown",
    "render_members",
]
