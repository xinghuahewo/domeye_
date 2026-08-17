# `backend/info` 数据文件用途清单

## 1. 清点范围与口径

- 核验目录：`/home/bgpdata/Domeye/backend/info`
- 核验日期：2026-07-24
- 核验方式：只读 SSH，检查文件集合、大小、表头、JSON 顶层结构，并追踪旧项目实际运行时代码。
- 目录内共有 24 个数据文件，合计 5,835,678,791 字节，约 5.44 GiB；另有一份 12,139 字节的 `README.md`。
- CSV “物理行数”不一定等于逻辑记录数。已确认 `ip_bgp_entity.csv` 有 1,363,337 个物理数据行，但 CSV 解析后是 1,357,067 条逻辑记录；`domain_cn.csv` 有 72,368 个物理数据行，但只有 71,353 条逻辑记录。原因是合法的引号字段中包含换行。
- JSON 的“规模”指顶层键数；Excel 的规模按第一个工作表的逻辑数据行统计。

状态说明：

- **API + 检测活跃**：主站查询服务和核心检测均直接依赖。
- **API 活跃**：查询、详情或报告链使用，核心检测不直接使用。
- **检测活跃**：核心检测直接影响过滤、分级或状态判断。
- **加载但未消费**：当前加载器会读取，但未发现后续业务读取。
- **仅配置**：存在配置常量，但未发现活跃消费者。
- **历史/替代**：当前配置不指向该文件，未发现活跃消费者。

## 2. 逐文件用途与落库去向

| 文件 | 实测规模 | 当前状态 | 当前代码用途 | 建议落库去向 |
| --- | ---: | --- | --- | --- |
| `as_entity.csv` | 359 MiB；133,117 条逻辑记录 | API + 检测活跃 | `data_loader` 生成 `as_info`、国家列表和“前 1000 行 ASN”；`BGPInfo` 提供 AS 名称、国家、组织、类型、联系人、import/export、反 DDoS、IPv4/IPv6 Peer、Sibling 等。直接参与劫持、子劫持、泄露过滤和事件详情补全。 | `info.autonomous_system`、`info.as_contact`、`info.as_policy_member`、`info.as_relation` |
| `important_as.csv` | 32 KiB；1,420 条逻辑记录 | API + 检测活跃 | 以 `aut-num` 为键判断重点 AS；参与报告模板及劫持、子劫持、泄露、中断事件分级。 | `info.important_as` |
| `ip_bgp_entity.csv` | 673 MiB；1,357,067 条逻辑记录 | API + 检测活跃 | 形成 `prefix_info`；提供 prefix 名称、route、BGP 标记、普通域名和权威域名列表及数量。参与详情补全、查询回填、劫持/泄露分级；现有部分图表还把它误当成 prefix 白名单。 | `info.prefix`、`info.prefix_origin`、`info.prefix_domain` |
| `country.xlsx` | 35 KiB；251 条逻辑记录 | API + 检测活跃 | 国家中英文名、两/三位代码、数字代码、电话代码、时差和经纬度；用于国家别名归一、地图、拓扑和检测结果补全。 | `info.country`、`info.country_alias` |
| `website_entity.csv` | 2.7 GiB；5,129,097 条逻辑记录 | API 活跃 | 以分号分隔；与 `domain_cn.csv` 合并后形成延迟加载的 `domain_info`，用于域名标题、行业、IP、前缀和权威 IP 补全。合并时本文件优先。 | `info.domain_record`、`info.domain_address` |
| `domain_cn.csv` | 38 MiB；71,353 条逻辑记录 | API 活跃 | 国内域名补充源；在 `website_entity.csv` 之后合并，以 URL 去重并保留先出现记录。实测有 2 条空 URL，需要隔离。 | `info.domain_record`、`info.domain_address` |
| `pfx2as_dict.txt` | 23 MiB；74,417 个 ASN、1,040,413 个 ASN-prefix 关联 | API + 检测活跃 | JSON 结构为 `{asn: {prefix: 数值}}`。API 用于 ASN 前缀资源计算和 IP/prefix 回填；劫持检测用于判断双方是否都在历史 MOAS 集合。现有代码只使用 prefix 键，未解释内部数值语义。 | `info.as_prefix_history`；数值先保留为 `source_value`，不得擅自命名 |
| `as_rel_dict.txt` | 32 MiB；77,787 个 ASN | 检测活跃 | 提供 `provider`、`customer`、`peers` 关系，参与 PCP 路由泄露识别以及劫持/子劫持关系过滤。文件同时含 `peer` 与 `peers`，实测二者逐 ASN 完全相同；provider/customer 反向关系闭合。 | `info.as_relation` |
| `domain_cn_center.txt` | 43 KiB；434 个域名 | 检测活跃 | 重要域名中心库；当受影响 prefix 包含重要域名时提升劫持、子劫持或泄露事件等级。 | `info.important_domain` |
| `private_as_dict_new.json` | 95 KiB；3 个公网 ASN、1,087 个公网/私网 ASN 组合 | 检测活跃 | 结构为 `{public_asn: {private_asn: {ip_num, city}}}`；为 `公网ASN_私网ASN` 形式补城市。 | `info.private_as_location` |
| `triplet_20days.csv` | 380 MiB；约 1,409,624 个物理数据行 | 检测活跃 | 字段为 `first_as/second_as/third_as/appear_time/appear_num/stability/is_leak`；核心只将三元组及 `stability` 装入内存。PCP 候选三元组稳定度 `>= 0.2` 时被过滤。 | `info.route_triplet_baseline` |
| `ipv4_all_prefix.xls` | 25 MiB；86,489 条逻辑记录 | 加载但未消费 | 与 IPv6 文件合并成 `important_prefix_dict`，但当前活跃代码未发现对该字典的业务读取。实测有 1 条空 prefix。 | `info.important_prefix`，空键进入 quarantine |
| `ipv6_all_prefix.xls` | 744 KiB；3,300 条逻辑记录 | 加载但未消费 | 同上。实测有 1 条空 prefix；两文件合并并按 prefix 首条保留后，旧内存字典包含一个无业务意义的空键。 | `info.important_prefix`，空键进入 quarantine |
| `as_dict.txt` | 336 MiB；105,759 个 ASN | 仅配置 | `AS_INFO_OLD_FILE` 指向它，但未发现活跃读取；是旧式反规范化 AS 字典，包含描述、国家、import/export、Peer、传播路径等。 | `info.legacy_record`，按 ASN 拆行保存，不作为当前真值源 |
| `top_nx.csv` | 149 MiB；约 4,124,192 个物理数据行 | 仅配置 | 配置为 `TOP_NX_FILE`，表头含未命名索引列、`domain`、`ipaddress`；未发现活跃消费者。仅可推断为域名/IP 类派生清单，生成口径待生产者确认。 | `info.dns_observation`，标记 `dataset_kind=top_nx` |
| `top_ip.txt` | 26 MiB；907,919 行 | 仅配置 | 配置为 `TOP_IP_FILE`，每行为域名与 IP；未发现活跃消费者，来源和刷新口径未记录。 | `info.dns_observation`，标记 `dataset_kind=top_ip` |
| `as_rank.json` | 15 MiB；112,490 个 ASN | 仅配置 | 配置常量存在，但活跃代码实际使用 `as_entity.csv.global_rank`，未读取本文件。字段为国家、组织、AS 名称、类型和 rank。 | `info.as_rank`，保留独立来源，不覆盖 `as_entity` 排名 |
| `org_entity.csv` | 33 MiB；约 78,606 个物理数据行 | 仅配置 | 组织维度实体、国家、Sibling ASN、IPv4/IPv6 prefix 及数量；未发现活跃消费者。 | `info.organization`、`info.organization_as`、`info.organization_prefix` |
| `as_dict_new.txt` | 341 MiB；124,040 个 ASN | 历史/替代 | 新版反规范化 AS 字典候选，但配置仍指向 `as_dict.txt`，未发现活跃消费者。 | `info.legacy_record`，`dataset_kind=as_dict_new` |
| `as_entity_is_ddos_provider.csv` | 1.6 MiB；约 129,144 个物理数据行 | 历史/替代 | ASN 与反 DDoS 标记的独立来源；当前检测读取的是已合并进 `as_entity.csv` 的 `is_ddos_provider`。 | `info.legacy_record` 或 `info.as_attribute_evidence`，用于来源对账 |
| `as_rel_dict_old.txt` | 27 MiB；71,747 个 ASN | 历史/替代 | 旧 AS 关系字典；字段覆盖不齐，和当前文件内容不同。 | `info.as_relation`，通过 `source_file_sk` 标记旧版本，默认不进入当前视图 |
| `ases_cn.csv` | 2.6 MiB；约 6,819 个物理数据行 | 历史/替代 | 中国 ASN 的多源增强表，含重要性、组织、描述、Peer/Prefix 数量、分类等；未发现活跃消费者。 | `info.legacy_record`，后续确认来源后再提升为正式证据表 |
| `private_as_dict.json` | 95 KiB；3 个公网 ASN | 历史/替代 | 旧私有 AS 城市映射；与 `private_as_dict_new.json` 哈希不同，当前配置不使用。 | `info.private_as_location`，旧 `source_file_sk`，默认不进入当前视图 |
| `triplet_20days_1.csv` | 421 MiB；约 1,645,110 个物理数据行 | 历史/替代 | 另一版三元组基线，和当前文件内容、规模不同；当前配置不使用。 | `info.route_triplet_baseline`，旧 `source_file_sk`，默认不进入当前视图 |

`README.md` 不是业务数据，不计入 24 文件 manifest、`content_id` 或
`info.source_file`。如需审计，应把它作为独立文档制品记录 SHA256、大小和文本
版本；正文不塞入业务表，README 的变化也不能伪装成数据 release 变化。

## 3. 关键调用关系

本清单的主要代码锚点如下（均为旧项目只读路径）：

- `backend/config/config.py:71-89`：18 个配置文件名；
- `backend/utils/data_loader.py:39-124`：API 侧 AS、prefix、国家和域名加载；
- `backend/core/BGPInfo.py:88-163`：检测侧重型加载；
- `backend/core/BGPDetection.py:103-115`：每个检测进程只构造一次 `BGPInfo`；
- `backend/core/BGPLeak.py:108-139,280-312`：AS 关系和三元组稳定度过滤；
- `backend/core/BGPHijack.py:247-417`：prefix、import/export、关系、反 DDoS、Sibling 和事件分级；
- `backend/core/BGPSubHijack.py:169-352`：子前缀劫持的同类过滤与分级；
- `backend/services/features_service.py:187-188,352-355`：pfx2as 直接加载和 ASN 资源计算；
- `backend/services/data_query_service.py:600,1183`：pfx2as 和国家文件直接加载；
- `backend/database/as_info.py:13-70,166-227`：已弃用的 8 列 `as_info` 表及未接入启动流程的导入函数。

### 3.1 API/Service 路径

```text
data_loader._load_core_data()
  ├─ as_entity.csv       -> as_info / country_list / ases_1000
  ├─ important_as.csv    -> important_as_dict
  ├─ ip_bgp_entity.csv   -> prefix_info
  └─ country.xlsx        -> country_info

data_loader.load_domain_data()
  ├─ website_entity.csv
  └─ domain_cn.csv
       -> 合并、按 URL 首条保留 -> domain_info
```

`features_service.py`、`data_query_service.py` 和 `report_data.py` 还会绕过 `data_loader`，直接读取 `pfx2as_dict.txt`；`data_query_service.py` 会直接重新读取 `country.xlsx`。

### 3.2 核心检测路径

```text
BGPDetection.__init__()
  -> BGPInfo.load_info()
       ├─ AS、国家、重要 AS
       ├─ AS 关系、历史 ASN-prefix
       ├─ prefix 与重要域名
       ├─ 私有 AS 城市
       └─ 路由三元组稳定度
  -> BGPHijack / BGPSubHijack / BGPLeak / BGPOutage
```

这些静态数据会改变事件是否被过滤、事件级别和事件描述，不能按普通展示字典处理。

## 4. 已确认的兼容语义

1. `as_entity.csv`、`important_as.csv`、`ip_bgp_entity.csv` 均按自然键去重并保留第一条。
2. 域名数据按 `website_entity.csv` 在前、`domain_cn.csv` 在后的顺序合并；URL 冲突时前者胜出。
3. `ases_1000` 实际是 `as_entity.csv` 的前 1000 行，不是代码显式按 `global_rank` 排序的前 1000 名。
4. `important_as_dict` 的内存键是整数；AS、AS 关系和 pfx2as 的主要内存键是字符串。数据库适配器必须在边界保持这一行为，或一次性修改全部消费者。
5. `pfx2as_dict.txt` 的内部数值当前未被业务代码消费，迁移时只能原样保存为来源值。
6. `as_rel_dict.txt` 没有 `sibling` 字段；Sibling 过滤实际还依赖 `as_entity.csv.sibling_as`。不得在兼容层凭空合并成旧字典字段。
7. `ipv4_all_prefix.xls` 和 `ipv6_all_prefix.xls` 虽被加载，当前未发现检测或 API 消费。

## 5. 用途不明文件的处理原则

对 `top_nx.csv`、`top_ip.txt`、`ases_cn.csv` 等来源口径不完整的数据：

- 先进入带 `source_file_sk`、`source_row_no` 和原始字段的保真表；
- 不进入 `info.current_*` 业务视图；
- 不和正式实体静默合并；
- 补齐生产者、生成脚本、时间范围、刷新周期和字段字典后，才能提升为规范数据源。
