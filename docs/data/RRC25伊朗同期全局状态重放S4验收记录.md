# RRC25 伊朗同期全局状态重放 S4 验收记录

日期：2026-07-26  
阶段：S4「国家投影、伊朗对账与非伊朗覆盖闭合」  
运行身份：`global_run_v1_6427ae1ad52037c83d7ec3bb9b5e7757`  
数据集身份：`global_dataset_v1_d015e120c2d02d39596af86ea8f8fb7c`  
revision：`global_replay_r2`  
结论：已修正后通过

## 一、阶段入口与执行边界

S4 从 S3 已闭合的共享全球状态流进入：

- RIB checkpoint 仍绑定 `2026-02-28T08:00:00Z` 的 RRC25 全量 RIB；
- 25 个 catch-up 与 59 个正式 UPDATE 的冻结 spool 身份未改变；
- 正式窗口仍为北京时间 18:05–23:00 的 60 个五分钟状态点；
- 国家投影只消费同一批全球正式产品，没有启动国家级独立重放；
- 非伊朗国家只生成同期状态窗口，不伪造中断、恢复或原因事实；
- 既有伊朗验收包保持只读；
- 未修改 `backend/core/`、旧 Detection、旧业务数据库，也没有切换生产。

最终采用的 r2 目录位于仓库外：

```text
/home/bgpdata/Domeye-Core-dev-data/research-runs/rrc25-global-state/
global_run_v1_6427ae1ad52037c83d7ec3bb9b5e7757-r2
```

## 二、r1 独立核验失败与 r2 修正

r1 已生成 241 个国家包，包内伊朗对账也已通过；但独立 Python 验证器在最后一个
`__UNKNOWN__` 包的首个正式状态点失败：

```text
__UNKNOWN__ 2026-02-28T10:05:00Z 国家与地址族人口不闭合
```

失败日志 SHA-256：

```text
5de4f0b2d0913cf493800bf92b1f0ce9138dc6023f4bcf49c09b9456f3a7fd0d
```

原因是显式未知桶中有 8,419 个 seed 路由无法唯一解析 origin ASN。它们正确进入
了未知国家的总 Prefix×VP 人口，但由于没有 ASN 计数器，r1 地址族投影只从 ASN
计数器求和时遗漏了这些路由。底层 RouteState、国家总人口和状态摘要没有错误，
错误仅位于 IPv4/IPv6 查询投影。

修正后：

- IPv4/IPv6 Prefix×VP 人口直接来自完整固定 cohort 的国家人口计数；
- 具有可解析 origin 的 ASN 分类仍来自 ASN 计数器；
- origin unknown 路由进入地址族人口，但不伪造成某个 ASN；
- 新增 IPv4、IPv6 各一个 unknown-origin 路由的回归测试；
- revision 从 `global_replay_r1` 提升为 `global_replay_r2`；
- r1 目录和失败日志完整保留，没有静默覆盖。

r2 只复用 S2 的 64 个 RIB checkpoint shard。已核对 r1 与 r2 对应 shard 为同一
inode 的硬链接，没有重读原始 RIB。r2 再次严格应用既有派生 spool，以形成新的
不可变查询产品；没有重新解析原始 UPDATE MRT。

修订边界记录 `CORRECTION.json` 的 SHA-256 为：

```text
39519207446e33936b1baaa1940c396502ec3f677a810e279074c350d734ed8c
```

## 三、共享状态与国家交付人口

| 项目 | r2 结果 |
| --- | ---: |
| 国家及显式未知桶 | 241 |
| 每个国家正式状态点 | 60 |
| 国家快照总数 | 14,460 |
| ASN 状态总数 | 5,133,600 |
| 首个正式状态 | `2026-02-28T10:05:00Z` |
| 最后正式状态 | `2026-02-28T15:00:00Z` |
| 最终 RouteState 行数 | 55,772,687 |
| 全球固定 Prefix×VP | 55,729,118 |
| 最终可见固定 Prefix×VP | 55,637,676 |
| 最终当前 Prefix×VP | 55,684,594 |

每个国家包使用同一合同，包含：

- 固定 cohort、cohort ID、seed 时间、mapping 版本和 membership digest；
- origin ASN、IPv4/IPv6、Prefix 与 Prefix×VP 可区分的人口；
- 60 点国家状态、地址族状态、双栈分类和国家归属 UPDATE 活动；
- 60 点 ASN 状态；
- run、dataset、revision、引擎、输入清单、质量和内容哈希；
- `COMPLETE.json` 引用的已核验交付文件。

非伊朗包的 `incident.json` 使用中性
`rrc25-global-country-state-window/v1` 身份，`detected_at`、`onset_at`、
`peak_at` 和恢复时间均为 `null`，并明确写明“只表示同期状态，不构成国家中断
事实”。这些隔离状态切片没有写入真实事件列表。

## 四、显式未知桶闭合

r2 的 `__UNKNOWN__` 固定 cohort 为：

| 项目 | 结果 |
| --- | ---: |
| 固定 Prefix×VP | 13,957 |
| IPv4 Prefix×VP | 10,393 |
| IPv6 Prefix×VP | 3,564 |
| 可落位到未知国家代码的 origin ASN | 239 |
| 无法唯一解析 origin 的 Prefix×VP | 8,419 |

独立验证器已逐槽核对：

```text
IPv4 Prefix×VP + IPv6 Prefix×VP = 国家 Prefix×VP
已知 origin 成员 Prefix×VP + origin unknown Prefix×VP = 固定 cohort
全部国家与显式未知桶之和 = 全球人口
```

三组等式在全部 60 个状态点均成立。

## 五、伊朗基线无损对账

伊朗仍保持既有验收口径：

| 项目 | 结果 |
| --- | ---: |
| origin ASN | 563 |
| Prefix×VP | 384,767 |
| IPv4 Prefix×VP | 383,804 |
| IPv6 Prefix×VP | 963 |
| 状态点 | 60 |
| ASN 状态 | 33,780 |

逐项比较范围包括：

- cohort ID、mapping 版本、563 个 ASN、成员与 Prefix×VP 人口；
- 60 个 snapshot ID、时间、槽边界、可见人口、比例、地址族、双栈分类和
  collector-wide UPDATE 活动；
- 33,780 条 ASN 的 snapshot、时间、cohort、ASN、分类和
  IPv4 不可见但 IPv6 可见标记。

比较状态为 `pass`。全球化新增的 `country_update_counts` 不替换或改写既有伊朗
`update_counts` 语义。

## 六、非伊朗大、中、小样本

样本仅增加验收覆盖，实际数据仍生成到全部 241 个国家及未知桶。

| 国家 | 规模 | origin ASN | Prefix×VP | IPv4 | IPv6 | 60 点 ASN 状态 |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| MS | 小 | 1 | 43 | 43 | 0 | 60 |
| KG | 中 | 56 | 19,627 | 19,144 | 483 | 3,360 |
| US | 大 | 18,136 | 14,405,316 | 12,304,563 | 2,100,753 | 1,088,160 |

三个样本均使用通用国家包生成逻辑，没有国家代码专用输入、重放分支或字段。
页面消费属于 S5 到期范围，本阶段只验收同合同数据和数值闭合。

## 七、独立验证结果

独立验证器没有复用生成器的完成结论，而是重新读取交付文件并核对：

| 检查项 | 结果 |
| --- | --- |
| 全部交付文件与 `COMPLETE` 哈希 | pass |
| 共享 60 点时间轴 | pass |
| 国家与 IPv4/IPv6 人口闭合 | pass |
| ASN 分类与固定 ASN 人口闭合 | pass |
| 国家 UPDATE 活动与全球活动求和 | pass |
| 全球、全部国家与未知桶人口守恒 | pass |
| 国家包与共享全球产品投影同一性 | pass |
| 伊朗既有基线无损比较 | pass |
| MS、KG、US 数值样本 | pass |

r2 正式状态 digest 仍为：

```text
3a8c63ea5c667d6d62d1ff1b1da9b6abfa4f2a927aad9cb9e70f160935d2ba00
```

它与 r1 相同，证明修订没有改变底层 RouteState。r2 正式 manifest 因 revision
和查询投影修正形成新的不可变身份：

```text
48e5e5e08f47469a16466490397b2d7562d8214ed03fda1cc937e25139ee88c5
```

另起进程执行正式 delta checkpoint 恢复，重新从 RIB checkpoint 应用 84 个派生
spool 槽并核对 85 个产品哈希，最终状态、游标与人口守恒均为 `pass`。

## 八、关键文件身份

| 文件 | SHA-256 |
| --- | --- |
| `checkpoints/rib/manifest.json` | `5d1788be48516d300c1eef639d0a2e8682c59162e27d334bc25c5870c24838c9` |
| `checkpoints/catch-up/manifest.json` | `e02a54e725d9d1e42815e64cbff1d9319b99806ee8ee2da7ecb2a54229c67209` |
| `checkpoints/formal/manifest.json` | `48e5e5e08f47469a16466490397b2d7562d8214ed03fda1cc937e25139ee88c5` |
| `updates-summary.json` | `b376df4fb97802f18d5f28c4b4fd00c9bfe0dc575f4e4523015378d0ad75f8fd` |
| `country-packages/COMPLETE.json` | `eb3046bc623bf3b208a195f09d3d9616ec45d18672e838fff2d08d4c1590d20d` |
| `country-packages/catalog.json` | `ee10674fb08fc7323df74cf5031849edb3dbdaf8c8d7d7529ac6999e33fd9f06` |
| `country-packages/iran-baseline-comparison.json` | `1b9ff28c4988969b34b7dcd7162fd53de3a5245f177f8ec0727a716adc5fda35` |
| `country-packages-verification.json` | `9b4ef8220943f93fcb4714b50a6c22acd24c7277e0bd7a75feacc422d1a44b64` |
| `country-packages-verification.log` | `e7f381ac69c477802676cab90033fcfc2f9dbbd908f314b9c415624cebb60961` |
| `verify-delta-r2.log` | `80ddf427cc3472fb5318ba038d737284861dd6fa46eb4352b1a1ea2665d9859f` |

## 九、阶段判定

- GSR-05：241 个国家及未知桶均由同一 seed RIB 和 mapping 冻结可审计 cohort；
- GSR-07：全球、国家、未知桶、地址族和 ASN 分类全部逐槽闭合，r1 偏离已由
  r2 修正；
- GSR-09：伊朗 cohort、60 点、ASN 状态和既有 UPDATE 活动逐项无损一致；
- GSR-10：全部非零 cohort 国家均有同合同数据，MS、KG、US 完成大、中、小
  数值对账，未伪造业务事件；
- GSR-13：S4 到期的 schema、run、dataset、revision、mapping、引擎、输入、
  质量和输出哈希身份已经闭合；API audit 和页面消费效果留待 S5。

S4 出口已成立，可以进入 S5。S5 必须通过隔离候选环境验证通用 overview、
series、ASN 分页和 audit 合同，并从 23:00 checkpoint 接续至少一批连续 UPDATE；
不得把本次历史全球重放标记为生产实时。

RRC25 全局状态重放最终验收回检：S4 已修正。
