# RRC25 伊朗同期全局状态重放 S0 现状基线

版本：1.0  
核验日期：2026-07-26  
阶段：S0「输入、基线与最终效果冻结」  
最终效果合同：
[RRC25伊朗同期全局状态重放最终验收文档](../RRC25伊朗同期全局状态重放最终验收文档.md)

## 一、阶段结论

S0 冻结范围成立，但“全球状态重放”在本阶段开始前尚未实现。现有独立 Go 引擎
完整读取 RRC25 RIB，却只把确定属于 IR 的 Prefix×VP 留在内存和 checkpoint；
其结果只能作为伊朗不可回退基线，不能作为全球 RouteState 已存在的证据。

本次实施必须复用同一冻结输入和既有 84 槽 RouteEvent spool。全球化不得再次为
每个国家解析 UPDATE，也不得修改旧 Detection、`backend/core/`、旧业务数据库
或既有伊朗不可变交付包。

## 二、冻结输入

远端只读数据根：

```text
/home/bgpdata/data/ripe
```

Seed RIB：

```text
rrc25/2026.02/bview.20260228.0800.gz
size_bytes = 426297361
sha256 = 036e1a5b4d1554eae083d8b4d9de648f0ed95bfcd0ea781c4d001df68a23159c
```

冻结 UPDATE 子集：

```text
UTC [2026-02-28T08:00:00Z, 2026-02-28T15:00:00Z)
count = 84
compressed_bytes = 401865192
first = 2026-02-28T08:00:00Z
last = 2026-02-28T14:55:00Z
canonical_subset_sha256 = e14e4ce8d40ab72faea058ec88807946fbe85c2a9a9c86d6552d662384f3bcad
```

输入选择与 mapping：

| 文件 | SHA-256 |
| --- | --- |
| `prepared-final/full-selection.json` | `7d8a500de84ab497c2bd2c08febfef207e4fe379e19a209ee538414df9dded28` |
| `prepared-final/compatible-mapping.json` | `05b9809116c3525769e8dc2bd52497ff810a5b4d063cf3c93442d23ed119f9d5` |
| `prepared-final/revised-mapping.json` | `0c20c3f522170d0838466ab9fa8da729abf60767fe820038efc73a3f62dd510e` |

上述文件位于：

```text
/home/bgpdata/Domeye-Core-dev-data/research-runs/iran-rrc25-full-p0/
20260723T094940Z-full-p0/prepared-final/
```

## 三、冻结伊朗基线

既有只读交付包：

```text
/home/bgpdata/Domeye-Core-dev-data/research-runs/iran-rrc25-full-p0/
20260723T094940Z-full-p0/state-replay-1805-2300-go-v1/
```

已核验事实：

| 项目 | 冻结值 |
| --- | ---: |
| RIB physical records | 1,398,399 |
| RIB entries | 55,729,118 |
| UPDATE physical records | 15,488,874 |
| RouteEvents | 42,682,699 |
| 伊朗 origin ASN | 563 |
| 伊朗 Prefix×VP | 384,767 |
| IPv4 Prefix×VP | 383,804 |
| IPv6 Prefix×VP | 963 |
| 正式状态点 | 60 |
| 最后状态时间 | `2026-02-28T15:00:00Z` |

不可回退制品哈希：

| 文件 | SHA-256 |
| --- | --- |
| `COMPLETE.json` | `64cc1cddb25bc1b8791ac50f8bf1960319744cef8ce210aad9c96c40d66831bf` |
| `cohort.json` | `e25dfa463c011a487d9e59f3f216975b45e6a404b51e0232b3429e4efcf8c046` |
| `country-snapshots.jsonl.gz` | `b8f06fbd72e4a81b2aa7c1c0ebac2688b2fcb8ec8f4bdbaf00804e8d2353d3e2` |
| `asn-states.jsonl.gz` | `2f1b1125ca5fcac0ef3779f2de948148f0d349e4fac936a5cbb21e34b0e8e2c2` |

## 四、可复用的单一 UPDATE 流

既有 Go 引擎已经把全部 84 个 UPDATE 解析为未按国家过滤的稳定 shard spool：

```text
state-replay-1805-2300-go-v1/spool/
schema = rrc25-update-spool-manifest/v1
engine = rrc25-iran-go-replay/1.0.0
slots = 84
shards = 32
manifest_sha256 = f0719a6168f64b2f4783747920f0b73c4504474cee692427ae3ef292ba0ef08b
```

全球重放必须验证并直接复用这条 RouteEvent 流。只有 23:00 后的增量 UPDATE
允许新增解析；冻结 84 槽不得再次解析。

## 五、当前能力与缺口

### 已有

- RIB、UPDATE、selection 和 mapping 的冻结身份；
- 全量 UPDATE RouteEvent spool；
- 伊朗固定 cohort、60 点状态、ASN 状态和不可变交付包；
- 通用国家中断解析、overview、series、ASN 分页和 audit API 合同；
- 正常追加保持 revision、推进 `data_through` 的 publication 规则。

### 尚未实现

- 包含全部国家与显式未知桶的共享 RouteState；
- 全球、国家、ASN、地址族人口守恒；
- 由同一状态生成的全国家固定 cohort 和 60 点数据；
- 跨国家 origin 迁移和 WITHDRAW 归属对账；
- 全球 checkpoint、恢复和重复运行确定性；
- 从全局状态为任意合法 `country_outage` 事件生成同合同查询制品；
- 23:00 后不重读 RIB 的新 UPDATE 接续与页面新增时间点。

## 六、S0 边界判定

- 本记录只冻结现状，不把设计目标写成当前能力；
- 不启动真实全球 RIB 运行；
- 不修改生产数据、旧核心或既有伊朗交付包；
- S1 必须从共享状态、mapping、cohort、守恒和 checkpoint 合同开始；
- 后续阶段的结构 Hook 通过不能替代真实数据和页面验收。
