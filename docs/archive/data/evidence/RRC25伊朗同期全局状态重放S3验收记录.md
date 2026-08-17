# RRC25 伊朗同期全局状态重放 S3 验收记录

日期：2026-07-26  
阶段：S3「单 UPDATE 流与 60 点状态闭合」  
运行身份：`global_run_v1_6427ae1ad52037c83d7ec3bb9b5e7757`  
数据集身份：`global_dataset_v1_bd146c1286e7e904cbbd5d4299cb7eb4`  
revision：`global_replay_r1`  
结论：通过

## 一、执行边界

- 从 S2 的全球 RIB checkpoint 启动，没有重新读取原始 RIB；
- 只消费冻结 spool 中的 25 个 catch-up 和 59 个正式 UPDATE；
- 84 个槽按 `slot → record ordinal → element ordinal` 形成一条有序状态流；
- 每个槽只应用一次，没有启动国家级独立重放；
- 本阶段没有读取 UTC `15:00` 之后的真实 UPDATE；
- 未修改 `backend/core/`、旧 Detection、旧业务数据库或既有伊朗不可变交付包。

运行目录位于仓库外：

```text
/home/bgpdata/Domeye-Core-dev-data/research-runs/rrc25-global-state/global_run_v1_6427ae1ad52037c83d7ec3bb9b5e7757
```

## 二、状态流与输出时间轴

| 项目 | 结果 |
| --- | ---: |
| UPDATE 制品 | 84 |
| catch-up 制品 | 25 |
| 正式制品 | 59 |
| physical records | 15,488,874 |
| RouteEvents | 42,682,699 |
| ANNOUNCE | 38,967,304 |
| WITHDRAW | 3,715,395 |
| catch-up 派生产品 | 25 |
| 正式状态点 | 60 |
| 每个正式状态点的国家及未知桶 | 241 |
| 每个正式状态点的 ASN 行 | 85,560 |
| 正式窗口 ASN 行总数 | 5,133,600 |

正式状态时间严格为：

```text
首点  2026-02-28T10:05:00Z  北京时间 18:05
末点  2026-02-28T15:00:00Z  北京时间 23:00
点数  60
间隔  5 分钟
```

60 个时间值唯一且严格递增，没有缺槽、重复槽或窗口外补点。25 个 catch-up 产品
只用于形成正式窗口起点状态，不作为正式时间轴中的额外点。

## 三、确定性顺序与一次应用

- 冻结 spool manifest SHA-256 为
  `f0719a6168f64b2f4783747920f0b73c4504474cee692427ae3ef292ba0ef08b`；
- 32 个 spool shard 使用 k-way merge，以原始 record ordinal、element ordinal
  的全序应用；
- `processed_slot=83`、`processed_update_count=84`、`product_sequence=85`；
- 85 个派生产品形成前向哈希链，最后产品 SHA-256 为
  `417b8aaa6d379c2ebaca43b03fbe3f96f49f657b46b5371e7015c29191d46aa5`；
- 所有国家观测均随同一槽产品一次生成，不存在按国家重新解析或应用 UPDATE 的
  分支。

## 四、状态迁移与质量计数

| 质量项 | 结果 |
| --- | ---: |
| origin 国家迁移 | 21,636 |
| replacement ANNOUNCE | 135,441 |
| duplicate ANNOUNCE | 35,178,025 |
| duplicate WITHDRAW | 17,033 |
| withdraw without state | 17,033 |
| UPDATE unknown origin | 4,224 |
| unknown-country ANNOUNCE | 6,381 |
| unknown-country WITHDRAW | 17,773 |
| unknown optional attributes | 0 |
| 质量门 | pass |

ANNOUNCE 的新归属来自新路径 origin；替换和跨国家迁移同时移除旧状态归属并加入
新状态归属；WITHDRAW 使用 RouteState 中保存的上一归属，不从撤回报文重新推断
国家。重复报文、无现存状态撤回和未知国家活动分别计数，没有伪造为正常零值。

每槽产品同时保留：

- collector-wide `update_counts`，维持既有伊朗交付包的 UPDATE 活动语义；
- country-attributed `country_update_counts`，作为后续通用国家页面的新能力；
- 固定 seed cohort 与当前动态状态，两者不混用分母。

## 五、末状态人口守恒

```text
全球固定 Prefix×VP       55,729,118
全部国家与未知桶固定之和  55,729,118
全球可见固定 Prefix×VP    55,637,676
全部国家可见固定人口之和  55,637,676
全球当前 Prefix×VP       55,684,594
全部国家当前人口之和      55,684,594
RouteState 行数           55,772,687
显式未知桶固定人口            13,957
显式未知桶当前人口            13,843
状态                         pass
```

正式窗口末状态 digest：

```text
3a8c63ea5c667d6d62d1ff1b1da9b6abfa4f2a927aad9cb9e70f160935d2ba00
```

## 六、checkpoint 与独立恢复

catch-up checkpoint：

| 项目 | 结果 |
| --- | --- |
| processed slot | 24 |
| UPDATE 数 | 25 |
| data through | `2026-02-28T10:05:00Z` |
| 状态 digest | `85d43acac040791c3358524bc861712909166d9eb1089ecacaed3970755454a8` |
| manifest SHA-256 | `8b7c2d282e0dd99a21505e8f7d942674a1253762ee346697a62237e10ec3750f` |

正式 checkpoint：

| 项目 | 结果 |
| --- | --- |
| processed slot | 83 |
| UPDATE 数 | 84 |
| 正式状态点 | 60 |
| data through | `2026-02-28T15:00:00Z` |
| 状态 digest | `3a8c63ea5c667d6d62d1ff1b1da9b6abfa4f2a927aad9cb9e70f160935d2ba00` |
| manifest SHA-256 | `e3b8e387e996761272e854194c59dc15b3b05e828d91e2a435e3bb8388fe8aab` |

另起独立进程执行 `verify-delta --checkpoint-phase formal`，流程为：

```text
只读取 64 个 RIB checkpoint shard
→ 核验冻结 spool
→ 按同一全序重施 84 个 UPDATE
→ 核验 85 个产品哈希
→ 对比正式 checkpoint
```

恢复结果：

- `route_state_rows=55,772,687`；
- `processed_slot=83`；
- `data_through=2026-02-28T15:00:00Z`；
- 所有全球、国家、当前和固定人口守恒均为 `pass`；
- 恢复状态 digest 与连续运行完全相同；
- 恢复日志 SHA-256 为
  `80ddf427cc3472fb5318ba038d737284861dd6fa46eb4352b1a1ea2665d9859f`。

该恢复入口没有读取原始 RIB，也没有覆盖连续运行产品。

## 七、关键文件身份

| 文件 | SHA-256 |
| --- | --- |
| `checkpoints/catch-up/manifest.json` | `8b7c2d282e0dd99a21505e8f7d942674a1253762ee346697a62237e10ec3750f` |
| `checkpoints/formal/manifest.json` | `e3b8e387e996761272e854194c59dc15b3b05e828d91e2a435e3bb8388fe8aab` |
| `update-quality.json` | `3a36a3b88b436e2d05b1fff807cf54509b4baede0e4b244919da8c8bbdb10bbe` |
| `updates-summary.json` | `eacd3d1211616692a2005c509edc3965314e8199808fdebf6aef8af4f4b9e42b` |
| `progress.json` | `a56141ee1e70e29cdf5b75e11ba561e6c375764c4550daccea236393be88ccbb` |

## 八、阶段判定

- GSR-03：84 个 UPDATE 仅形成一条确定性共享状态流；
- GSR-06：全部国家共享北京时间 18:05–23:00 的 60 点严格时间轴；
- GSR-08：替换、跨国迁移、撤回、重复和未知活动均显式计数并守恒；
- GSR-11：RIB、catch-up、正式 checkpoint 可识别，独立恢复与连续运行末状态
  完全相同；
- GSR-12：输入、哈希、顺序和产品校验均失败关闭，`data_through` 只推进至最后
  完整槽。

S3 出口已成立，可以进入 S4。S4 只能从这一共享状态流分发国家投影，不得按国家
重放输入；必须逐项对账伊朗既有 60 点，并覆盖大、中、小非伊朗 cohort 样本。

RRC25 全局状态重放最终验收回检：S3 已修正。
