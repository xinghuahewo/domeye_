"""INFO 目录的版本化文件合同。

这里维护的是文件集合、解析格式和来源角色，不维护某次快照的记录数或哈希。
后两者必须来自实际文件清点，不能硬编码为永久基线。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Optional, Tuple


PARSER_VERSION = "info-parser-v5"
FULL_IMPORTER_VERSION = "info-full-importer-v2"
MANIFEST_SCHEMA_VERSION = 1
QUALITY_GATE_VERSION = "info-quality-v1"
CSV_FIELD_SIZE_LIMIT_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True)
class DataFileSpec:
    name: str
    dataset_kind: str
    file_format: str
    role: str
    parser: str
    encoding: Optional[str] = "utf-8-sig"
    delimiter: Optional[str] = None
    source_priority: Optional[int] = None
    required_columns: Tuple[str, ...] = ()
    exact_columns: bool = False

    def canonical_dict(self) -> dict:
        value = asdict(self)
        value["required_columns"] = list(self.required_columns)
        return value


AS_ENTITY_COLUMNS = (
    "asn",
    "as_name",
    "as_country",
    "as_country_cn",
    "as_info",
    "type",
    "type_cn",
    "org_name",
    "org_name_cn",
    "org_country",
    "descr",
    "descr_cn",
    "is_ddos_provider",
    "import_as",
    "export_as",
    "sibling_as",
    "global_rank",
    "country_rank",
    "org_url_ds",
    "org_info_ds",
    "org_info_cn_ds",
    "org_url_qwen",
    "org_info_qwen",
    "org_info_cn_qwen",
    "admin_info",
    "tech_info",
    "abuse_info",
    "v4Upstream",
    "v4Downstream",
    "v4Peer",
    "v6Upstream",
    "v6Downstream",
    "v6Peer",
    "v4Prefixes_num",
    "v6Prefixes_num",
    "v4Peer_num",
    "v6Peer_num",
    "v4Upstream_num",
    "v6Upstream_num",
    "v4Downstream_num",
    "v6Downstream_num",
)

PREFIX_COLUMNS = (
    "prefix",
    "name",
    "descr",
    "route",
    "bgp",
    "admin_prefix",
    "admin_route",
    "admin_bgp",
    "org_handle_prefix",
    "org_name_prefix",
    "org_handle_bgp",
    "org_name_bgp",
    "country",
    "source",
    "domain",
    "domain_num",
    "domain_auth",
    "domain_auth_num",
)

COUNTRY_COLUMNS = (
    "english_full_name",
    "english_short_name",
    "chinese_short_name",
    "two_letter_code",
    "three_letter_code",
    "digital_code",
    "phone_code",
    "jet_lag",
    "latitude",
    "longitude",
)


DATA_FILE_SPECS = (
    DataFileSpec(
        "as_entity.csv",
        "autonomous_system",
        "csv",
        "active",
        "csv",
        delimiter=",",
        required_columns=AS_ENTITY_COLUMNS,
        exact_columns=True,
    ),
    DataFileSpec(
        "important_as.csv",
        "important_as",
        "csv",
        "active",
        "csv",
        delimiter=",",
        required_columns=("aut-num",),
    ),
    DataFileSpec(
        "ip_bgp_entity.csv",
        "prefix",
        "csv",
        "active",
        "csv",
        delimiter=",",
        required_columns=PREFIX_COLUMNS,
        exact_columns=True,
    ),
    DataFileSpec(
        "country.xlsx",
        "country",
        "xlsx",
        "active",
        "excel-magic-read-only",
        encoding=None,
        required_columns=COUNTRY_COLUMNS,
        exact_columns=True,
    ),
    DataFileSpec(
        "website_entity.csv",
        "domain",
        "csv",
        "active",
        "csv",
        delimiter=";",
        source_priority=0,
        required_columns=("url", "title", "industry", "ip", "ip_prefix", "auth_ip"),
    ),
    DataFileSpec(
        "domain_cn.csv",
        "domain",
        "csv",
        "active",
        "csv",
        delimiter=",",
        source_priority=1,
        required_columns=("url", "title", "industry", "ip", "ip_prefix", "auth_ip"),
    ),
    DataFileSpec(
        "pfx2as_dict.txt",
        "as_prefix_history",
        "json",
        "active",
        "json-top-level-object",
    ),
    DataFileSpec(
        "as_rel_dict.txt",
        "as_relation",
        "json",
        "active",
        "json-top-level-object",
    ),
    DataFileSpec(
        "domain_cn_center.txt",
        "important_domain",
        "json",
        "active",
        "json-top-level-object",
    ),
    DataFileSpec(
        "private_as_dict_new.json",
        "private_as_location",
        "json",
        "active",
        "json-top-level-object",
    ),
    DataFileSpec(
        "triplet_20days.csv",
        "route_triplet_baseline",
        "csv",
        "active",
        "csv",
        delimiter=",",
        required_columns=(
            "first_as",
            "second_as",
            "third_as",
            "appear_time",
            "appear_num",
            "stability",
            "is_leak",
        ),
        exact_columns=True,
    ),
    DataFileSpec(
        "ipv4_all_prefix.xls",
        "important_prefix",
        "xls",
        "loaded_not_consumed",
        "excel-magic-read-only",
        encoding=None,
        required_columns=("prefix", "number", "host"),
        exact_columns=True,
    ),
    DataFileSpec(
        "ipv6_all_prefix.xls",
        "important_prefix",
        "xls",
        "loaded_not_consumed",
        "excel-magic-read-only",
        encoding=None,
        required_columns=("prefix", "number", "host"),
        exact_columns=True,
    ),
    DataFileSpec(
        "as_dict.txt",
        "legacy_as_dictionary",
        "json",
        "config_only",
        "json-top-level-object",
    ),
    DataFileSpec(
        "top_nx.csv",
        "dns_observation",
        "csv",
        "config_only",
        "csv",
        delimiter=",",
        required_columns=("domain", "ipaddress"),
    ),
    DataFileSpec(
        "top_ip.txt",
        "dns_observation",
        "line_text",
        "config_only",
        "non-empty-lines",
    ),
    DataFileSpec(
        "as_rank.json",
        "as_rank",
        "json",
        "config_only",
        "json-top-level-object",
    ),
    DataFileSpec(
        "org_entity.csv",
        "organization",
        "csv",
        "config_only",
        "csv",
        delimiter=",",
        required_columns=(
            "uuid",
            "org_country",
            "org_country_cn",
            "org_name",
            "org_name_cn",
            "sibling_as",
            "v4Prefixes",
            "v6Prefixes",
            "sibling_as_num",
            "v4Prefixes_num",
            "v6Prefixes_num",
        ),
        exact_columns=True,
    ),
    DataFileSpec(
        "as_dict_new.txt",
        "legacy_as_dictionary",
        "json",
        "legacy",
        "json-top-level-object",
    ),
    DataFileSpec(
        "as_entity_is_ddos_provider.csv",
        "ddos_provider_evidence",
        "csv",
        "legacy",
        "csv",
        delimiter=",",
    ),
    DataFileSpec(
        "as_rel_dict_old.txt",
        "as_relation",
        "json",
        "legacy",
        "json-top-level-object",
    ),
    DataFileSpec(
        "ases_cn.csv",
        "legacy_as_cn",
        "csv",
        "legacy",
        "csv",
        encoding="gb18030",
        delimiter=",",
    ),
    DataFileSpec(
        "private_as_dict.json",
        "private_as_location",
        "json",
        "legacy",
        "json-top-level-object",
    ),
    DataFileSpec(
        "triplet_20days_1.csv",
        "route_triplet_baseline",
        "csv",
        "legacy",
        "csv",
        delimiter=",",
        required_columns=(
            "first_as",
            "second_as",
            "third_as",
            "appear_time",
            "appear_num",
            "stability",
            "is_leak",
        ),
        exact_columns=True,
    ),
)

SPEC_BY_NAME = {spec.name: spec for spec in DATA_FILE_SPECS}
CORE_PHASE_FILE_NAMES = (
    "as_entity.csv",
    "important_as.csv",
    "ip_bgp_entity.csv",
    "country.xlsx",
)
FULL_PHASE_FILE_NAMES = tuple(
    spec.name
    for spec in DATA_FILE_SPECS
    if spec.name not in CORE_PHASE_FILE_NAMES
)

if len(FULL_PHASE_FILE_NAMES) != 20:
    raise RuntimeError("INFO S2 文件合同必须恰好包含 20 个非核心文件")

if len(SPEC_BY_NAME) != 24 or len(SPEC_BY_NAME) != len(DATA_FILE_SPECS):
    raise RuntimeError("INFO 文件合同必须恰好包含 24 个唯一文件")
