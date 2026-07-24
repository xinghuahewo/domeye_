# RRC25 伊朗事件独立 Go 重放执行结果

## 1. 执行结论

独立 Go 重放引擎已经跑通 RRC25 伊朗事件的完整研究窗口，并形成可追溯的
`RIB → UPDATE → Prefix×VP 状态 → 国家/ASN 快照 → Incident/Episode/Wave`
证据链。本次执行不导入旧检测 Core、不写数据库、不调用生产接口，也没有扩大到
1,928 个 UPDATE。

最终结果状态为 `complete`，质量门状态为 `pass`。运行目录中的
`RUNNING.json` 已删除，`COMPLETE.json` 已生成；其中登记的 10 个交付文件
SHA-256 已全部重新计算并匹配。

本次重放确认：

- 研究窗口在北京时间 2026-02-28 18:05 已处于异常状态，因此 onset 只能记为
  “不晚于窗口起点”，精度为 `left_censored_at_window_start`；
- 连续两个状态点确认异常，detected 为北京时间 18:10；
- 受影响 ASN 比例在 22:00 达到窗口峰值；
- 固定 Prefix×VP 可见率在 22:35 达到窗口谷值；
- 截至 23:00，状态有所回升，但不满足部分恢复或完全恢复条件，事件状态为
  `ongoing`；
- catch-up 阶段已经出现连续异常且波动超过检测尺度，不能从本次输入构造可信的
  正常带，`normal_band.state` 因而保持 `unknown`；
- 本次证据只支持 RRC25 控制面观测，不能证明前兆导致主事件，也不能支持政治、
  物理线路或行为意图归因。

## 2. 冻结身份与运行边界

| 项目 | 结果 |
| --- | --- |
| Git 分支 | `codex/rrc25-iran-go-replay` |
| 引擎提交 | `2acb27f55b6cea2359ea650ea9199fa5a9e85dee` |
| 引擎版本 | `rrc25-iran-go-replay/1.0.0` |
| 二进制 SHA-256 | `0c1cbc135d947dbf89b2a2275651d0c0bf586ff01c97a20addbb695a77021ca7` |
| worker / shard | `16 / 32` |
| 原始数据根目录 | `/home/bgpdata/data/ripe`，只读 |
| 结果目录 | `/home/bgpdata/Domeye-Core-dev-data/research-runs/iran-rrc25-full-p0/20260723T094940Z-full-p0/state-replay-1805-2300-go-v1` |
| 结果包占用 | 约 2.7 GiB，包含 checkpoint 与 spool |
| 完成时间 | `2026-07-24T08:46:05Z` |

预检时输出目录不存在，服务器可用 32 个 CPU、约 133 GiB 内存和 1.5 TiB
磁盘；进程文件描述符上限为 1,024。固定 16 worker、32 shard 的设计未接近该
上限。

首次进入 RIB 时发现一个非 AS_PATH 属性值可被误判成 MP_REACH，执行在 RIB
checkpoint 之前失败。修复后仍沿用同一结果目录和运行身份执行 `--resume`；
由于当时尚无 RIB checkpoint，重新读取了 RIB，但没有建立第三个结果目录或第三次
完整运行身份。修复内容是让 RIB 中除 AS_PATH 外的属性保持 opaque，并增加对应
回归测试。

## 3. 固定输入

| 输入 | 数量 | 压缩字节 |
| --- | ---: | ---: |
| `bview.20260228.0800.gz` | 1 | 426,297,361 |
| catch-up UPDATE，08:00–10:00Z | 25 | 计入下项 |
| 正式 UPDATE，10:05–14:55Z | 59 | 计入下项 |
| 全部 UPDATE | 84 | 401,865,192 |
| 合计 | 85 个文件 | 828,162,553 |

状态观察窗为 UTC `[10:05, 15:00]`，即北京时间 `[18:05, 23:00]`。输出包括
窗口起点状态和 59 个 UPDATE 槽末状态，共 60 个观察点。

## 4. 解析与状态规模

### 4.1 RIB

| 指标 | 结果 |
| --- | ---: |
| physical records | 1,398,399 |
| RIB entries | 55,729,118 |
| 固定 IR Prefix×VP | 384,767 |
| 固定 IR origin ASN | 563 |
| origin 不可唯一解析 | 8,419 |
| 映射未知 | 5,538 |
| 明确非 IR | 55,330,394 |

只有 revised mapping 明确属于 IR 且 origin 唯一的 RIB entry 进入固定 cohort。
AS_SET、confederation、未知 origin 和未知映射均未被补成 IR 或 0。

### 4.2 UPDATE

| 指标 | 结果 |
| --- | ---: |
| physical records | 15,488,874 |
| RouteEvent | 42,682,699 |
| ANNOUNCE | 38,967,304 |
| WITHDRAW | 3,715,395 |
| origin 不可唯一解析 | 4,224 |
| 未识别可选属性 | 0 |
| slot spool | 84 |
| shard spool | 2,688 |

正式窗口 59 个 UPDATE 槽自身包含 25,045,118 个 ANNOUNCE 和 2,630,645 个
WITHDRAW。每个 shard 的记录数、字节数和 SHA-256 都在状态应用前完成校验。

## 5. 状态与事件结果

### 5.1 关键状态

| 北京时间 | 语义 | 受影响 ASN | 全不可见 / 部分可见 | 可见 Prefix×VP | 可见率 |
| --- | --- | ---: | ---: | ---: | ---: |
| 18:05 | 窗口起点，左删失 | 89 / 563 | 8 / 81 | 367,215 / 384,767 | 95.4383% |
| 18:10 | 连续两点确认 | 91 / 563 | 27 / 64 | 366,844 / 384,767 | 95.3419% |
| 22:00 | 受影响 ASN 峰值 | 218 / 563 | 84 / 134 | 320,087 / 384,767 | 83.1898% |
| 22:35 | Prefix×VP 可见率谷值 | 210 / 563 | 87 / 123 | 316,733 / 384,767 | 82.3181% |
| 23:00 | 观察窗结束 | 161 / 563 | 67 / 94 | 333,938 / 384,767 | 86.7897% |

“全不可见 / 部分可见”按同一固定 cohort、同一快照和双栈联合口径计算：
一个 ASN 的适用地址族全部没有 baseline Prefix×VP 可见时为全不可见；仍有部分
baseline Prefix×VP 可见时为部分可见。

### 5.2 事件模型

| 字段 | 结果 |
| --- | --- |
| incident | `incident_go_v1_a1de26f854831330c616a72af21597eb` |
| episode | `episode_go_v1_d8bcd1836dab4c32c1ffda9e502eb7fd` |
| onset | `2026-02-28T10:05:00Z`，窗口左删失 |
| detected | `2026-02-28T10:10:00Z` |
| peak | `2026-02-28T14:00:00Z` |
| trough | `2026-02-28T14:35:00Z` |
| partial recovery | `unknown` |
| full recovery | `unknown` |
| observation end | `2026-02-28T15:00:00Z` |
| duration state | `interval` |
| recovery state | `ongoing` |
| wave 数量 | 1 |
| wave 因果关系 | `not_assessed` |

窗口末的可见率高于谷值，说明存在回升信号；但它没有连续 6 个状态点达到固定的
部分恢复门槛，且正常带不可用，因此不能把该回升写成已恢复。

## 6. 与旧数据库及原报告对账

三个数字组使用不同人口、时点和定义，不应直接相减：

| 来源 | 结果 | 本次判断 |
| --- | --- | --- |
| 原报告 | `199/595` 受影响，`73/126` 全不可见/部分可见 | 原报告人口与快照定义未冻结，不能按原口径复现 |
| 旧数据库国家事实 | `176/556` | 旧事实内部一致，但没有逐 VP 同快照状态 |
| 本次 Go 重放 | 峰值 `218/563`，其中 `84/134`；22:35 为 `210/563`，其中 `87/123` | 新固定 cohort 和逐状态口径，可从原始 MRT 重算 |

本次结果没有“凑数”复现旧数字。在 60 个状态点中，最接近 199 个受影响 ASN 的
状态为 18:30 的 `201/563`；最接近 `73/126` 分类组合的状态为 19:25 的
`82/125`，两者都不是原报告数字的证明。旧数据库的 176 个集合在本次固定 cohort
下也没有对应的同快照状态；最接近的观察点为 18:25 的 `188/563`。

数据库指标代理仍有价值：它把聚合 IPv4 指标异常的 onset 定位在 18:05、detected
定位在 18:10，并在 22:30 给出自身口径的谷值。Go 状态重放对 onset/detected
给出一致的五分钟定位，但 Prefix×VP 可见率谷值为 22:35。五分钟差异来自指标
定义不同：旧代理使用 `v4ip_num` 的 `/24` 等价值，Go 使用固定 cohort 的逐
`Prefix×VP` 状态；二者不能互相替代。

## 7. 交付包验收

| 验收项 | 结果 |
| --- | --- |
| RIB checkpoint | 1 |
| catch-up checkpoint | 25 |
| 正式 checkpoint | 60 |
| 最后观察时间 | `2026-02-28T15:00:00Z` |
| 国家状态行 | 60 |
| ASN 状态行 | 33,780 |
| 路由状态行 | 23,094,550 |
| gzip 完整性 | 3 / 3 通过 |
| `QUALITY.status` | `pass` |
| `QUALITY.failures` | `null` |
| `COMPLETE` 交付哈希 | 10 / 10 重算匹配 |
| `RUNNING` 标记 | 不存在 |

主要交付文件：

- `country-snapshots.jsonl.gz`：国家级 60 个同人口状态；
- `asn-states.jsonl.gz`：逐 ASN、逐快照分类；
- `route-states.jsonl.gz`：逐 Prefix×VP、逐快照状态；
- `cohort.json`：固定人口及映射版本；
- `incident.json`、`episodes.json`、`waves.json`：研究事件模型；
- `QUALITY.json`、`input-summary.json`、`COMPLETE.json`：质量、输入和不可变
  交付身份；
- `重放观察报告.md`：运行目录内的自动中文摘要。

## 8. 验证记录

- `go test -race -count=1 ./...`：通过；
- `go vet ./...`：通过；
- 合成端到端测试：覆盖 84 槽、25 catch-up、60 正式状态和 checkpoint 恢复；
- 09:25 真实 UPDATE 单文件：与冻结 Python 计数完全一致，
  269,491 physical、756,983 RouteEvent、721,043 ANNOUNCE、35,940 WITHDRAW；
- OpenSpec：`openspec validate build-independent-go-rrc25-iran-replay --strict`
  通过；
- 完整结果：检查点数量、观察时间、JSON 标记、gzip、行数和交付哈希全部通过。

## 9. 仍然存在的数据边界

本次流程已经补齐“固定人口、逐 VP 路由状态、五分钟状态时间线和事件级原始重放”
这组 P0 核心缺口，但仍不能回答：

1. 18:05 之前的精确 onset。catch-up 已经异常，且本次只使用 08:00Z RIB；
2. 23:00 之后何时部分或完全恢复。本次没有读取窗口外 UPDATE；
3. RRC25 之外其他 collector 的传播范围和视角差异；
4. 数据面可达性、用户流量、时延和实际服务影响；
5. 前兆与主事件的因果关系，以及政治、物理或行为意图原因。

如果继续研究，应复用当前 Go 引擎和 checkpoint 合同，按明确问题扩展更早基线、
更晚恢复窗或多 collector；不应回到浏览器聚合明细，也不应把未知字段补成零。
