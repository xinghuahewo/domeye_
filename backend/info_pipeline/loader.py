"""把一期四文件安全、幂等地导入候选 PostgreSQL 容器。"""

from __future__ import annotations

import csv
import hashlib
import ipaddress
import json
import os
import shutil
import stat
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import (
    Any,
    Dict,
    Iterable,
    Iterator,
    Mapping,
    Optional,
    Sequence,
    TextIO,
    Union,
)

from .catalog import (
    CORE_PHASE_FILE_NAMES,
    CSV_FIELD_SIZE_LIMIT_BYTES,
    PARSER_VERSION,
    SPEC_BY_NAME,
)
from .excel import iter_first_sheet_values
from .manifest import validate_manifest
from .quality import parse_asn, parse_literal_list


class LoadError(RuntimeError):
    """候选库导入失败。"""


def _sql_literal(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, int):
        return str(value)
    text = str(value)
    if "\x00" in text:
        raise LoadError("SQL 文本值不能包含 NUL")
    return "'" + text.replace("'", "''") + "'"


def _json_literal(value: Any) -> str:
    return _sql_literal(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    ) + "::jsonb"


def _canonical_record_sha256(value: Mapping[str, Any]) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _optional_int(value: Any) -> Optional[int]:
    if value is None or str(value).strip() == "":
        return None
    text = str(value).strip()
    if text.endswith(".0"):
        text = text[:-2]
    return int(text)


def _optional_float(value: Any) -> Optional[float]:
    if value is None or str(value).strip() == "":
        return None
    return float(str(value).strip())


def _optional_bool(value: Any) -> Optional[bool]:
    if value is None or str(value).strip() == "":
        return None
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y"}:
        return True
    if text in {"0", "false", "no", "n"}:
        return False
    raise ValueError(f"无法转换布尔值：{value!r}")


def _relation_target(value: Any) -> Optional[int]:
    text = str(value).strip()
    if text.upper().startswith("AS"):
        text = text[2:]
    try:
        return parse_asn(text)
    except ValueError:
        return None


def _json_compatible_token(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


class DockerPsql:
    """只通过 docker exec 操作已存在的候选容器，不接触生产端口。"""

    def __init__(self, container: str, db_user: str, db_name: str) -> None:
        for label, value in {
            "container": container,
            "db_user": db_user,
            "db_name": db_name,
        }.items():
            if not value or "\x00" in value or "\n" in value:
                raise LoadError(f"{label} 参数无效")
        inspection = subprocess.run(
            [
                "docker",
                "inspect",
                "--format",
                '{{ index .Config.Labels "domeye.core.database-role" }}',
                "--",
                container,
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        candidate_role = (inspection.stdout or "").strip()
        if (
            inspection.returncode != 0
            or candidate_role != "offline-candidate"
        ):
            raise LoadError(
                "拒绝连接未标记为 offline-candidate 的数据库容器："
                f"{container}"
            )
        self._command = [
            "docker",
            "exec",
            "--interactive",
            container,
            "psql",
            "-X",
            "--quiet",
            "--no-align",
            "--tuples-only",
            "--set",
            "ON_ERROR_STOP=1",
            "--username",
            db_user,
            "--dbname",
            db_name,
        ]

    def execute(self, sql: str, *, capture: bool = False) -> str:
        result = subprocess.run(
            self._command,
            input=sql,
            text=True,
            stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            check=False,
        )
        if result.returncode != 0:
            detail = (result.stderr or "").strip()
            raise LoadError(f"候选库 SQL 执行失败：{detail[-4000:]}")
        return (result.stdout or "").strip()

    def execute_file(self, path: Path) -> None:
        with path.open("rb") as stream:
            result = subprocess.run(
                self._command,
                stdin=stream,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                check=False,
            )
        if result.returncode != 0:
            detail = result.stderr.decode("utf-8", errors="replace").strip()
            raise LoadError(f"INFO schema 安装失败：{detail[-4000:]}")

    @contextmanager
    def csv_rows(self, query: str) -> Iterator[Iterator[list[str]]]:
        """以 PostgreSQL COPY CSV 流式读取候选库，不把大结果集装入内存。"""

        normalized_query = query.strip().rstrip(";")
        if not normalized_query:
            raise LoadError("候选库流式查询不能为空")
        copy_sql = (
            "COPY (\n"
            + normalized_query
            + "\n) TO STDOUT WITH (FORMAT CSV, ENCODING 'UTF8');\n"
        )
        process = subprocess.Popen(
            self._command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1024 * 1024,
        )
        assert process.stdin is not None
        assert process.stdout is not None
        assert process.stderr is not None
        process.stdin.write(copy_sql)
        process.stdin.close()
        reader = csv.reader(process.stdout, strict=True)
        completed = False
        try:
            yield reader
            process.stdout.close()
            return_code = process.wait()
            completed = True
            if return_code != 0:
                detail = process.stderr.read().strip()
                raise LoadError(
                    f"候选库流式查询失败：{detail[-4000:]}"
                )
        finally:
            if not completed and process.poll() is None:
                process.kill()
                process.wait()
            process.stdout.close()
            process.stderr.close()

    def copy_streams(
        self,
        segments: Sequence[Union[str, TextIO]],
    ) -> None:
        with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
            process = subprocess.Popen(
                self._command,
                stdin=subprocess.PIPE,
                stdout=stdout,
                stderr=stderr,
                text=True,
            )
            assert process.stdin is not None
            try:
                for segment in segments:
                    if isinstance(segment, str):
                        process.stdin.write(segment)
                        continue
                    segment.seek(0)
                    while True:
                        chunk = segment.read(1024 * 1024)
                        if not chunk:
                            break
                        process.stdin.write(chunk)
                process.stdin.close()
                return_code = process.wait()
            except BaseException:
                process.kill()
                process.wait()
                raise
            if return_code != 0:
                stderr.seek(0)
                detail = stderr.read().decode("utf-8", errors="replace").strip()
                raise LoadError(f"候选库 COPY 事务失败：{detail[-4000:]}")

    def copy_stage(self, head_sql: str, spool: TextIO, tail_sql: str) -> None:
        self.copy_streams((head_sql, spool, "\\.\n", tail_sql))


def _manifest_file_map(manifest: Mapping[str, Any]) -> Dict[str, Mapping[str, Any]]:
    return {str(item["name"]): item for item in manifest["files"]}


def _verify_source_file(path: Path, manifest_item: Mapping[str, Any]) -> None:
    before = path.lstat()
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise LoadError(f"导入来源必须是普通文件且禁止软链接：{path}")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    after = path.lstat()
    identity_before = (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    )
    identity_after = (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    )
    if identity_before != identity_after:
        raise LoadError(f"校验期间来源文件发生变化：{path}")
    if before.st_size != manifest_item["size_bytes"]:
        raise LoadError(f"{path.name} 大小与 manifest 不一致")
    if digest.hexdigest() != manifest_item["sha256"]:
        raise LoadError(f"{path.name} SHA256 与 manifest 不一致")


def _source_file_identity(path: Path) -> tuple[int, int, int, int, int]:
    observed = path.lstat()
    if stat.S_ISLNK(observed.st_mode) or not stat.S_ISREG(observed.st_mode):
        raise LoadError(f"导入来源必须是普通文件且禁止软链接：{path}")
    return (
        observed.st_dev,
        observed.st_ino,
        observed.st_size,
        observed.st_mtime_ns,
        observed.st_ctime_ns,
    )


def _create_stage_spool() -> tuple[TextIO, csv.writer]:
    configured_directory = os.environ.get("DOMEYE_CORE_INFO_SPOOL_DIR")
    spool_directory = None
    if configured_directory:
        candidate = Path(configured_directory)
        if candidate.is_symlink() or not candidate.is_dir():
            raise LoadError(
                "DOMEYE_CORE_INFO_SPOOL_DIR 必须是实际目录且禁止软链接："
                f"{candidate}"
            )
        spool_directory = str(candidate)
    stream = tempfile.TemporaryFile(
        mode="w+",
        encoding="utf-8",
        newline="",
        dir=spool_directory,
    )
    writer = csv.writer(
        stream,
        delimiter="\t",
        quotechar='"',
        quoting=csv.QUOTE_ALL,
        lineterminator="\n",
    )
    return stream, writer


def _spool_root() -> Path:
    configured_directory = os.environ.get("DOMEYE_CORE_INFO_SPOOL_DIR")
    return Path(configured_directory) if configured_directory else Path(
        tempfile.gettempdir()
    )


def _validate_prefix_spool_capacity(manifest_item: Mapping[str, Any]) -> None:
    spool_root = _spool_root()
    if spool_root.is_symlink() or not spool_root.is_dir():
        raise LoadError(f"S1 临时盘目录无效或为软链接：{spool_root}")
    source_size = int(manifest_item["size_bytes"])
    required_free = source_size * 8 + 2 * 1024**3
    available_free = shutil.disk_usage(spool_root).free
    if available_free < required_free:
        raise LoadError(
            "S1 prefix 双 spool 临时空间不足："
            f"available={available_free} required={required_free} "
            f"spool={spool_root}"
        )


def _write_stage_row(
    writer: csv.writer,
    source_row_no: int,
    natural_key: str,
    record: Mapping[str, Any],
    payload: Mapping[str, Any],
) -> None:
    writer.writerow(
        [
            source_row_no,
            natural_key,
            _canonical_record_sha256(record),
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
        ]
    )


def _as_entity_payload(row: Mapping[str, Any]) -> Dict[str, Any]:
    asn = parse_asn(row.get("asn"))
    policies = []
    for direction, field_name in (("import", "import_as"), ("export", "export_as")):
        for ordinal, token in enumerate(
            parse_literal_list(row.get(field_name), field_name=field_name),
            start=1,
        ):
            token_text = _json_compatible_token(token)
            policies.append(
                {
                    "direction": direction,
                    "ordinal": ordinal,
                    "token": token_text,
                    "parsed_asn": _relation_target(token),
                }
            )

    relation_fields = (
        ("sibling_as", "sibling", 0),
        ("v4Upstream", "upstream", 4),
        ("v4Downstream", "downstream", 4),
        ("v4Peer", "peer", 4),
        ("v6Upstream", "upstream", 6),
        ("v6Downstream", "downstream", 6),
        ("v6Peer", "peer", 6),
    )
    relations = []
    for field_name, relation_kind, afi in relation_fields:
        for ordinal, token in enumerate(
            parse_literal_list(row.get(field_name), field_name=field_name),
            start=1,
        ):
            target = _relation_target(token)
            if target is None:
                continue
            relations.append(
                {
                    "target_asn": target,
                    "relation_kind": relation_kind,
                    "afi": afi,
                    "ordinal": ordinal,
                    "source_field": field_name,
                }
            )

    contacts = []
    for kind, field_name in (
        ("admin", "admin_info"),
        ("tech", "tech_info"),
        ("abuse", "abuse_info"),
    ):
        raw = row.get(field_name)
        if raw is not None and str(raw).strip():
            contacts.append(
                {
                    "contact_kind": kind,
                    "ordinal": 1,
                    "contact_value": str(raw),
                }
            )

    sensitive = {"admin_info", "tech_info", "abuse_info"}
    attributes = {key: value for key, value in row.items() if key not in sensitive}
    return {
        "asn": asn,
        "as_name": row.get("as_name") or None,
        "country_code": (row.get("as_country") or "").strip().upper() or None,
        "country_name_cn": row.get("as_country_cn") or None,
        "org_name": row.get("org_name") or None,
        "org_name_cn": row.get("org_name_cn") or None,
        "as_type": row.get("type") or None,
        "description": row.get("descr") or None,
        "description_cn": row.get("descr_cn") or None,
        "is_ddos_provider": _optional_bool(row.get("is_ddos_provider")),
        "global_rank": _optional_int(row.get("global_rank")),
        "country_rank": _optional_int(row.get("country_rank")),
        "v4_prefix_count": _optional_int(row.get("v4Prefixes_num")),
        "v6_prefix_count": _optional_int(row.get("v6Prefixes_num")),
        "v4_peer_count": _optional_int(row.get("v4Peer_num")),
        "v6_peer_count": _optional_int(row.get("v6Peer_num")),
        "v4_upstream_count": _optional_int(row.get("v4Upstream_num")),
        "v6_upstream_count": _optional_int(row.get("v6Upstream_num")),
        "v4_downstream_count": _optional_int(row.get("v4Downstream_num")),
        "v6_downstream_count": _optional_int(row.get("v6Downstream_num")),
        "contacts": contacts,
        "policies": policies,
        "relations": relations,
        "attributes": attributes,
    }


def _prefix_payload(row: Mapping[str, Any]) -> Dict[str, Any]:
    prefix_raw = str(row.get("prefix") or "").strip()
    network = ipaddress.ip_network(prefix_raw, strict=False)
    domains = []
    for role, field_name in (("normal", "domain"), ("authoritative", "domain_auth")):
        for ordinal, domain in enumerate(
            parse_literal_list(row.get(field_name), field_name=field_name),
            start=1,
        ):
            domains.append(
                {
                    "domain_role": role,
                    "ordinal": ordinal,
                    "domain_key_raw": _json_compatible_token(domain),
                }
            )
    return {
        "prefix_raw": prefix_raw,
        "prefix_cidr": str(network),
        "canonical_status": "canonical" if str(network) == prefix_raw else "noncanonical",
        "name": row.get("name") or None,
        "description": row.get("descr") or None,
        "route_raw": row.get("route") or None,
        "bgp_raw": row.get("bgp") or None,
        "country_code": row.get("country") or None,
        "source_name": row.get("source") or None,
        "declared_domain_count": _optional_int(row.get("domain_num")),
        "declared_authoritative_domain_count": _optional_int(
            row.get("domain_auth_num")
        ),
        "domains": domains,
        "attributes": dict(row),
    }


def _spool_prefix(
    path: Path,
    release_sk: int,
    source_file_sk: int,
) -> tuple[TextIO, TextIO, int, int]:
    main_spool, main_writer = _create_stage_spool()
    domain_spool, domain_writer = _create_stage_spool()
    logical_count = 0
    domain_count = 0
    encoding = SPEC_BY_NAME[path.name].encoding
    if encoding is None:
        main_spool.close()
        domain_spool.close()
        raise LoadError(f"{path.name} 文本编码合同缺失")
    try:
        with path.open("r", encoding=encoding, newline="") as source:
            csv.field_size_limit(CSV_FIELD_SIZE_LIMIT_BYTES)
            reader = csv.DictReader(source, strict=True)
            if reader.fieldnames is None:
                raise LoadError(f"{path.name} 缺少 CSV 表头")
            for logical_count, row in enumerate(reader, start=1):
                payload = _prefix_payload(row)
                domains = payload.pop("domains")
                declared = int(payload["declared_domain_count"] or 0) + int(
                    payload["declared_authoritative_domain_count"] or 0
                )
                if len(domains) != declared:
                    raise LoadError(
                        f"{path.name} 第 {logical_count} 行域名声明/实际不一致："
                        f"declared={declared} actual={len(domains)}"
                    )
                record_sha = _canonical_record_sha256(row)
                main_writer.writerow(
                    [
                        logical_count,
                        payload["prefix_raw"],
                        record_sha,
                        json.dumps(
                            payload,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                    ]
                )
                for domain in domains:
                    domain_count += 1
                    domain_writer.writerow(
                        [
                            release_sk,
                            payload["prefix_raw"],
                            domain["domain_key_raw"],
                            domain["domain_role"],
                            domain["ordinal"],
                            source_file_sk,
                            logical_count,
                            record_sha,
                        ]
                    )
        main_spool.flush()
        domain_spool.flush()
        return main_spool, domain_spool, logical_count, domain_count
    except (UnicodeDecodeError, csv.Error) as exc:
        main_spool.close()
        domain_spool.close()
        raise LoadError(f"{path.name} CSV 解析失败：{exc}") from exc
    except BaseException:
        main_spool.close()
        domain_spool.close()
        raise


def _country_payload(header: list[str], values: Iterable[Any]) -> Dict[str, Any]:
    row = dict(zip(header, values))
    alpha2 = str(row.get("two_letter_code") or "").strip().upper() or None
    alpha3 = str(row.get("three_letter_code") or "").strip().upper() or None
    return {
        "english_full_name": row.get("english_full_name") or None,
        "english_short_name": row.get("english_short_name") or None,
        "chinese_short_name": row.get("chinese_short_name") or None,
        "alpha2": alpha2,
        "alpha3": alpha3,
        "digital_code": str(row.get("digital_code") or "").strip() or None,
        "phone_code": str(row.get("phone_code") or "").strip() or None,
        "jet_lag": str(row.get("jet_lag") or "").strip() or None,
        "latitude": _optional_float(row.get("latitude")),
        "longitude": _optional_float(row.get("longitude")),
        "quality_status": (
            "valid"
            if alpha2
            and row.get("latitude") not in (None, "")
            and row.get("longitude") not in (None, "")
            else "incomplete"
        ),
        "attributes": row,
    }


_COPY_HEAD = """\
BEGIN;
SET LOCAL standard_conforming_strings = on;
CREATE TEMP TABLE info_import_stage (
    source_row_no bigint NOT NULL,
    natural_key text NOT NULL,
    source_record_sha256 char(64) NOT NULL,
    payload jsonb NOT NULL
) ON COMMIT DROP;
COPY info_import_stage(
    source_row_no, natural_key, source_record_sha256, payload
) FROM STDIN WITH (
    FORMAT csv,
    DELIMITER E'\\t',
    QUOTE '"',
    ESCAPE '"',
    ENCODING 'UTF8'
);
"""


def _common_tail(source_file_sk: int, logical_count: int) -> str:
    return f"""
UPDATE info.source_file
SET load_status = 'loaded',
    loaded_record_count = {_sql_literal(logical_count)},
    quarantined_record_count = 0,
    loaded_at = clock_timestamp()
WHERE source_file_sk = {_sql_literal(source_file_sk)}
  AND logical_record_count = {_sql_literal(logical_count)};
DO $block$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM info.source_file
        WHERE source_file_sk = {_sql_literal(source_file_sk)}
          AND load_status = 'loaded'
          AND loaded_record_count + quarantined_record_count
              = logical_record_count
    ) THEN
        RAISE EXCEPTION 'source_file 记录数对账失败';
    END IF;
END
$block$;
COMMIT;
"""


def _as_entity_tail(release_sk: int, source_file_sk: int, count: int) -> str:
    return f"""
INSERT INTO info.autonomous_system(
    release_sk, asn, source_file_sk, source_row_no, source_record_sha256,
    as_name, country_code, country_name_cn, org_name, org_name_cn,
    as_type, description, description_cn, is_ddos_provider,
    global_rank, country_rank, v4_prefix_count, v6_prefix_count,
    v4_peer_count, v6_peer_count, v4_upstream_count, v6_upstream_count,
    v4_downstream_count, v6_downstream_count, source_order, attributes
)
SELECT
    {release_sk}, (payload->>'asn')::bigint, {source_file_sk},
    source_row_no, source_record_sha256,
    payload->>'as_name', payload->>'country_code', payload->>'country_name_cn',
    payload->>'org_name', payload->>'org_name_cn', payload->>'as_type',
    payload->>'description', payload->>'description_cn',
    (payload->>'is_ddos_provider')::boolean,
    (payload->>'global_rank')::integer, (payload->>'country_rank')::integer,
    (payload->>'v4_prefix_count')::integer, (payload->>'v6_prefix_count')::integer,
    (payload->>'v4_peer_count')::integer, (payload->>'v6_peer_count')::integer,
    (payload->>'v4_upstream_count')::integer,
    (payload->>'v6_upstream_count')::integer,
    (payload->>'v4_downstream_count')::integer,
    (payload->>'v6_downstream_count')::integer,
    source_row_no, payload->'attributes'
FROM info_import_stage;

INSERT INTO info.as_contact(
    release_sk, asn, contact_kind, ordinal, source_file_sk,
    source_row_no, source_record_sha256, contact_value
)
SELECT
    {release_sk}, (stage.payload->>'asn')::bigint,
    contact->>'contact_kind', (contact->>'ordinal')::integer,
    {source_file_sk}, stage.source_row_no, stage.source_record_sha256,
    contact->'contact_value'
FROM info_import_stage AS stage
CROSS JOIN LATERAL jsonb_array_elements(stage.payload->'contacts') AS contact;

INSERT INTO info.as_policy_member(
    release_sk, asn, direction, ordinal, token, parsed_asn,
    source_file_sk, source_row_no, source_record_sha256
)
SELECT
    {release_sk}, (stage.payload->>'asn')::bigint,
    policy->>'direction', (policy->>'ordinal')::integer, policy->>'token',
    (policy->>'parsed_asn')::bigint,
    {source_file_sk}, stage.source_row_no, stage.source_record_sha256
FROM info_import_stage AS stage
CROSS JOIN LATERAL jsonb_array_elements(stage.payload->'policies') AS policy;

INSERT INTO info.as_relation(
    release_sk, source_asn, target_asn, relation_kind, afi, ordinal,
    source_field, source_file_sk, source_row_no, source_record_sha256,
    source_active
)
SELECT
    {release_sk}, (stage.payload->>'asn')::bigint,
    (relation->>'target_asn')::bigint, relation->>'relation_kind',
    (relation->>'afi')::smallint, (relation->>'ordinal')::integer,
    relation->>'source_field', {source_file_sk}, stage.source_row_no,
    stage.source_record_sha256, true
FROM info_import_stage AS stage
CROSS JOIN LATERAL jsonb_array_elements(stage.payload->'relations') AS relation;
""" + _common_tail(source_file_sk, count)


def _important_as_tail(release_sk: int, source_file_sk: int, count: int) -> str:
    return f"""
INSERT INTO info.important_as(
    release_sk, asn, source_file_sk, source_row_no,
    source_record_sha256, label, attributes
)
SELECT
    {release_sk}, (payload->>'asn')::bigint, {source_file_sk},
    source_row_no, source_record_sha256, payload->>'label',
    payload->'attributes'
FROM info_import_stage;
""" + _common_tail(source_file_sk, count)


def _prefix_copy_middle(
    release_sk: int,
    source_file_sk: int,
) -> str:
    return f"""
SELECT info.ensure_release_partitions({release_sk});
INSERT INTO info.prefix(
    release_sk, prefix_raw, source_file_sk, source_row_no,
    source_record_sha256, prefix_cidr, canonical_status, name,
    description, route_raw, bgp_raw, country_code, source_name,
    declared_domain_count, declared_authoritative_domain_count, attributes
)
SELECT
    {release_sk}, payload->>'prefix_raw', {source_file_sk},
    source_row_no, source_record_sha256,
    (payload->>'prefix_cidr')::cidr, payload->>'canonical_status',
    payload->>'name', payload->>'description', payload->>'route_raw',
    payload->>'bgp_raw', payload->>'country_code', payload->>'source_name',
    (payload->>'declared_domain_count')::integer,
    (payload->>'declared_authoritative_domain_count')::integer,
    payload->'attributes'
FROM info_import_stage;

COPY info.prefix_domain(
    release_sk, prefix_raw, domain_key_raw, domain_role, ordinal,
    source_file_sk, source_row_no, source_record_sha256
) FROM STDIN WITH (
    FORMAT csv,
    DELIMITER E'\\t',
    QUOTE '"',
    ESCAPE '"',
    ENCODING 'UTF8'
);
"""


def _prefix_copy_tail(
    release_sk: int,
    source_file_sk: int,
    logical_count: int,
    domain_count: int,
) -> str:
    return f"""
DO $block$
BEGIN
    IF (
        SELECT count(*)
        FROM info.prefix_domain
        WHERE release_sk = {release_sk}
          AND source_file_sk = {source_file_sk}
    ) <> {domain_count} THEN
        RAISE EXCEPTION
            'prefix_domain 实际记录数与安全解析结果不一致';
    END IF;
END
$block$;
""" + _common_tail(source_file_sk, logical_count)


def _country_tail(release_sk: int, source_file_sk: int, count: int) -> str:
    return f"""
INSERT INTO info.country(
    release_sk, source_file_sk, source_row_no, source_record_sha256,
    english_full_name, english_short_name, chinese_short_name,
    alpha2, alpha3, digital_code, phone_code, jet_lag,
    latitude, longitude, quality_status, attributes
)
SELECT
    {release_sk}, {source_file_sk}, source_row_no, source_record_sha256,
    payload->>'english_full_name', payload->>'english_short_name',
    payload->>'chinese_short_name', payload->>'alpha2', payload->>'alpha3',
    payload->>'digital_code', payload->>'phone_code', payload->>'jet_lag',
    (payload->>'latitude')::double precision,
    (payload->>'longitude')::double precision,
    payload->>'quality_status', payload->'attributes'
FROM info_import_stage;

INSERT INTO info.country_alias(
    release_sk, alias_kind, alias_value, country_sk,
    source_file_sk, source_row_no, source_record_sha256
)
SELECT
    country.release_sk, alias.alias_kind, alias.alias_value,
    country.country_sk, country.source_file_sk,
    country.source_row_no, country.source_record_sha256
FROM info.country AS country
CROSS JOIN LATERAL (
    VALUES
        ('alpha2', country.alpha2),
        ('alpha3', country.alpha3),
        ('english_full_name', country.english_full_name),
        ('english_short_name', country.english_short_name),
        ('chinese_short_name', country.chinese_short_name)
) AS alias(alias_kind, alias_value)
WHERE country.release_sk = {release_sk}
  AND country.source_file_sk = {source_file_sk}
  AND alias.alias_value IS NOT NULL
  AND btrim(alias.alias_value) <> '';
""" + _common_tail(source_file_sk, count)


def _spool_csv(
    path: Path,
    payload_builder,
    natural_key_name: str,
) -> tuple[TextIO, int]:
    spool, writer = _create_stage_spool()
    count = 0
    try:
        encoding = SPEC_BY_NAME[path.name].encoding
        if encoding is None:
            raise LoadError(f"{path.name} 文本编码合同缺失")
        with path.open("r", encoding=encoding, newline="") as source:
            csv.field_size_limit(CSV_FIELD_SIZE_LIMIT_BYTES)
            reader = csv.DictReader(source, strict=True)
            for count, row in enumerate(reader, start=1):
                payload = payload_builder(row)
                natural_key = str(payload[natural_key_name])
                _write_stage_row(writer, count, natural_key, row, payload)
        spool.flush()
        return spool, count
    except BaseException:
        spool.close()
        raise


def _spool_important_as(path: Path) -> tuple[TextIO, int]:
    def build(row: Mapping[str, Any]) -> Dict[str, Any]:
        asn = parse_asn(row.get("aut-num"))
        label = next(
            (
                str(value)
                for key, value in row.items()
                if key != "aut-num" and value is not None and str(value).strip()
            ),
            None,
        )
        return {"asn": asn, "label": label, "attributes": dict(row)}

    return _spool_csv(path, build, "asn")


def _spool_country(path: Path) -> tuple[TextIO, int]:
    spool, writer = _create_stage_spool()
    count = 0
    try:
        rows = iter_first_sheet_values(path)
        header = ["" if value is None else str(value).strip() for value in next(rows)]
        for count, values in enumerate(rows, start=1):
            record = dict(zip(header, values))
            payload = _country_payload(header, values)
            natural_key = payload["alpha2"] or f"source-row:{count}"
            _write_stage_row(writer, count, natural_key, record, payload)
        spool.flush()
        return spool, count
    except BaseException:
        spool.close()
        raise


def _register_release(
    database: DockerPsql,
    manifest: Mapping[str, Any],
    quality_report: Mapping[str, Any],
    code_commit: Optional[str],
) -> tuple[int, int, Dict[str, int], bool]:
    idempotency_key = hashlib.sha256(
        (
            str(manifest["manifest_sha256"])
            + "\0"
            + PARSER_VERSION
            + "\0core_four_files"
        ).encode("utf-8")
    ).hexdigest()
    files = manifest["files"]
    source_values = ",\n".join(
        "("
        + ", ".join(
            [
                _sql_literal(item["name"]),
                _sql_literal(item["dataset_kind"]),
                _sql_literal(item["file_format"]),
                _sql_literal(item["role"]),
                _sql_literal(item["parser"]),
                _sql_literal(item["source_priority"]),
                _sql_literal(item["size_bytes"]),
                _sql_literal(item["sha256"]),
                _json_literal(item["header"]),
                _sql_literal(item["header_sha256"]),
                _sql_literal(item["physical_line_count"]),
                _sql_literal(item["logical_record_count"]),
                _sql_literal(item["count_method"]),
            ]
        )
        + ")"
        for item in files
    )
    rules_values = ",\n".join(
        "("
        + ", ".join(
            [
                _sql_literal(rule["rule_id"]),
                _sql_literal(rule["rule_version"]),
                _sql_literal(rule["blocking"]),
                _sql_literal(rule["status"]),
                _json_literal(rule["observed"]),
                _json_literal(rule["expected"]),
                _json_literal(rule["evidence"]),
            ]
        )
        + ")"
        for rule in quality_report["rules"]
    )
    sql = f"""
BEGIN;
SET LOCAL standard_conforming_strings = on;
SELECT pg_advisory_xact_lock(
    hashtextextended({_sql_literal('static_info:' + str(manifest['manifest_sha256']))}, 0)
);
INSERT INTO info.dataset_release(
    content_id, manifest_sha256, source_release_label, status,
    parser_version, importer_config_sha256, code_commit
) VALUES (
    {_sql_literal(manifest['content_id'])},
    {_sql_literal(manifest['manifest_sha256'])},
    {_sql_literal(manifest['source_release_label'])},
    'loading',
    {_sql_literal(manifest['parser_version'])},
    {_sql_literal(manifest['importer_config_sha256'])},
    {_sql_literal(code_commit)}
)
ON CONFLICT (manifest_sha256) DO NOTHING;

WITH release AS (
    SELECT release_sk
    FROM info.dataset_release
    WHERE manifest_sha256 = {_sql_literal(manifest['manifest_sha256'])}
)
INSERT INTO info.source_file(
    release_sk, name, dataset_kind, file_format, role, parser,
    source_priority, size_bytes, sha256, header, header_sha256,
    physical_line_count, logical_record_count, count_method
)
SELECT *
FROM release
CROSS JOIN (VALUES
{source_values}
) AS source(
    name, dataset_kind, file_format, role, parser,
    source_priority, size_bytes, sha256, header, header_sha256,
    physical_line_count, logical_record_count, count_method
)
ON CONFLICT (release_sk, name) DO NOTHING;

WITH release AS (
    SELECT release_sk
    FROM info.dataset_release
    WHERE manifest_sha256 = {_sql_literal(manifest['manifest_sha256'])}
)
INSERT INTO info.import_run(
    release_sk, idempotency_key, parser_version,
    importer_config_sha256, scope, status
)
SELECT
    release_sk, {_sql_literal(idempotency_key)}, {_sql_literal(PARSER_VERSION)},
    {_sql_literal(manifest['importer_config_sha256'])},
    'core_four_files', 'loading'
FROM release
ON CONFLICT (idempotency_key) DO NOTHING;

WITH release AS (
    SELECT release_sk
    FROM info.dataset_release
    WHERE manifest_sha256 = {_sql_literal(manifest['manifest_sha256'])}
), run AS (
    SELECT import_run_sk
    FROM info.import_run
    WHERE idempotency_key = {_sql_literal(idempotency_key)}
)
INSERT INTO info.quality_result(
    release_sk, import_run_sk, rule_id, rule_version, blocking,
    status, observed, expected, evidence_ref
)
SELECT *
FROM release
CROSS JOIN run
CROSS JOIN (VALUES
{rules_values}
) AS quality(
    rule_id, rule_version, blocking,
    status, observed, expected, evidence_ref
)
ON CONFLICT (release_sk, rule_id, rule_version) DO NOTHING;
COMMIT;

SELECT release.release_sk, run.import_run_sk, run.status
FROM info.dataset_release AS release
JOIN info.import_run AS run ON run.release_sk = release.release_sk
WHERE release.manifest_sha256 = {_sql_literal(manifest['manifest_sha256'])}
  AND run.idempotency_key = {_sql_literal(idempotency_key)};
"""
    result = database.execute(sql, capture=True).splitlines()
    if not result:
        raise LoadError("注册 info release 后未返回 release/run 标识")
    release_text, run_text, run_status = result[-1].split("|")
    release_sk = int(release_text)
    import_run_sk = int(run_text)
    rows = database.execute(
        "SELECT name || '|' || source_file_sk || '|' || load_status "
        "FROM info.source_file "
        f"WHERE release_sk = {release_sk} ORDER BY name;\n",
        capture=True,
    ).splitlines()
    source_ids: Dict[str, int] = {}
    for row in rows:
        name, source_id, _status = row.split("|")
        source_ids[name] = int(source_id)
    return release_sk, import_run_sk, source_ids, run_status == "completed"


def load_core_files(
    source_dir: os.PathLike[str] | str,
    manifest: Mapping[str, Any],
    quality_report: Mapping[str, Any],
    database: DockerPsql,
    *,
    schema_sql: os.PathLike[str] | str,
    code_commit: Optional[str] = None,
) -> Dict[str, Any]:
    """装载四文件 shadow release；一期明确不允许激活为 core。"""

    validate_manifest(manifest)
    if quality_report.get("status") != "pass":
        raise LoadError("一期质量报告未通过，拒绝导入候选库")
    if quality_report.get("content_id") != manifest.get("content_id"):
        raise LoadError("质量报告与 manifest 的 content_id 不一致")
    if quality_report.get("manifest_sha256") != manifest.get("manifest_sha256"):
        raise LoadError("质量报告与 manifest 的 manifest_sha256 不一致")
    root = Path(source_dir)
    if root.is_symlink() or not root.is_dir():
        raise LoadError(f"来源目录无效：{root}")

    database.execute_file(Path(schema_sql))
    release_sk, import_run_sk, source_ids, already_completed = _register_release(
        database, manifest, quality_report, code_commit
    )
    if already_completed:
        return {
            "content_id": manifest["content_id"],
            "manifest_sha256": manifest["manifest_sha256"],
            "release_sk": release_sk,
            "import_run_sk": import_run_sk,
            "scope": "core_four_files",
            "status": "already_completed",
            "database_release_status": "validating",
            "activated": False,
            "next_scope": "domain_and_pfx2as",
        }

    file_map = _manifest_file_map(manifest)
    try:
        for name in CORE_PHASE_FILE_NAMES:
            if _is_source_loaded(database, release_sk, name):
                continue
            source_path = root / name
            source_identity = _source_file_identity(source_path)
            if name == "as_entity.csv":
                stream, count = _spool_csv(
                    source_path,
                    _as_entity_payload,
                    "asn",
                )
                tail = _as_entity_tail(release_sk, source_ids[name], count)
            elif name == "important_as.csv":
                stream, count = _spool_important_as(source_path)
                tail = _important_as_tail(release_sk, source_ids[name], count)
            elif name == "ip_bgp_entity.csv":
                _validate_prefix_spool_capacity(file_map[name])
                stream, domain_stream, count, domain_count = _spool_prefix(
                    source_path,
                    release_sk,
                    source_ids[name],
                )
                tail = None
            elif name == "country.xlsx":
                stream, count = _spool_country(source_path)
                tail = _country_tail(release_sk, source_ids[name], count)
            else:
                raise AssertionError(f"未知的一期文件：{name}")

            try:
                if _source_file_identity(source_path) != source_identity:
                    raise LoadError(f"{name} 在解析期间发生变化")
                _verify_source_file(source_path, file_map[name])
                expected = int(file_map[name]["logical_record_count"])
                if count != expected:
                    raise LoadError(
                        f"{name} 解析记录数与 manifest 不一致："
                        f"observed={count} expected={expected}"
                    )
                if name == "ip_bgp_entity.csv":
                    database.copy_streams(
                        (
                            _COPY_HEAD,
                            stream,
                            "\\.\n",
                            _prefix_copy_middle(
                                release_sk,
                                source_ids[name],
                            ),
                            domain_stream,
                            "\\.\n",
                            _prefix_copy_tail(
                                release_sk,
                                source_ids[name],
                                count,
                                domain_count,
                            ),
                        )
                    )
                else:
                    assert tail is not None
                    database.copy_stage(_COPY_HEAD, stream, tail)
            finally:
                stream.close()
                if name == "ip_bgp_entity.csv":
                    domain_stream.close()

        database.execute(
            f"""
BEGIN;
UPDATE info.dataset_release
SET status = 'validating',
    loaded_scope = ARRAY['core_four_files'],
    quality_summary = {_json_literal({
        'quality_gate_version': quality_report['quality_gate_version'],
        'scope': 'core_four_files',
        'status': quality_report['status'],
        'blocking_failure_count': quality_report['blocking_failure_count'],
    })}
WHERE release_sk = {release_sk};
UPDATE info.import_run
SET status = 'completed',
    checkpoint = {_json_literal({
        'loaded_files': list(CORE_PHASE_FILE_NAMES),
        'next_scope': 'domain_and_pfx2as',
    })},
    finished_at = clock_timestamp()
WHERE import_run_sk = {import_run_sk};
COMMIT;
"""
        )
    except BaseException as exc:
        try:
            database.execute(
                "UPDATE info.import_run "
                "SET status = 'failed', finished_at = clock_timestamp(), "
                f"error_summary = {_sql_literal(str(exc)[:2000])} "
                f"WHERE import_run_sk = {import_run_sk};\n"
            )
        except BaseException:
            pass
        raise
    return {
        "content_id": manifest["content_id"],
        "manifest_sha256": manifest["manifest_sha256"],
        "release_sk": release_sk,
        "import_run_sk": import_run_sk,
        "scope": "core_four_files",
        "status": "completed",
        "database_release_status": "validating",
        "activated": False,
        "next_scope": "domain_and_pfx2as",
    }


def _is_source_loaded(database: DockerPsql, release_sk: int, name: str) -> bool:
    result = database.execute(
        "SELECT load_status FROM info.source_file "
        f"WHERE release_sk = {release_sk} AND name = {_sql_literal(name)};\n",
        capture=True,
    )
    if result not in {"pending", "loading", "loaded", "failed"}:
        raise LoadError(f"source_file 状态缺失或无效：{name}={result!r}")
    return result == "loaded"
