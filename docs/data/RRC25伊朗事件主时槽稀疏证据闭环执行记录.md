# RRC25 伊朗事件主时槽稀疏证据闭环执行记录

## 1. 结论

本次已经跑通一条由真实 MRT 驱动的研究型数据链路：

```text
冻结 Profile
  → 原始制品清单与哈希
  → 有界 seed RIB checkpoint
  → 完整解析一个主 UPDATE 时槽
  → RouteEvent / raw record ref
  → 路由状态投影 / 五分钟样本
  → 主张对账 / 质量门
  → A/B 研究包 / SHA256 复核
```

最终成功运行目录是：

```text
/home/bgpdata/Domeye-Core-dev-data/research-runs/iran-rrc25-p0/20260722T080402Z-main-slot-evidence-v6
```

v6 退出码为 `0`，A/B 两个研究包均通过包内哈希校验和逐文件比对，生成：

- 19,367 条 RouteEvent；
- 19,367 条一一对应、验证状态为 `verified` 的 raw record ref；
- 216 个五分钟样本；
- 一个完整解析的 `2026-02-28T08:10:00Z` UPDATE 时槽；
- 可定位到原始 MRT 物理记录、VP、record ordinal、element ordinal、offset 和 record hash 的证据链。

本次结果只能认定为：

```text
execution_mode = bounded_pilot
quality_run_state = incomplete
acceptance_state = not_accepted
```

不得据此认定伊朗事件已完整验收，也不得宣称原 OpenSpec change 或当前 Goal 已完成。

特别需要说明：216 个样本全部是 `unknown_after_gap`，不是“1 个 observed 加 215 个 gap”。虽然 `08:10` UPDATE 已完整解析，但有界 seed 和此前连续输入缺口使该时点的国家路由状态不能恢复为严格已知。

## 2. 执行边界

本次执行遵守以下边界：

- 旧项目 `/home/bgpdata/Domeye` 只读；
- 数据库连接数为 `0`，数据库写操作为 `0`；
- 不修改 `backend/core/`；
- 不改前端、生产配置或生产服务；
- 不向生产数据库导入 RouteEvent；
- 原始数据只从 `/home/bgpdata/data/ripe` 读取；
- 输出只写入独立研究目录；
- 没有执行真实原始数据的全量双遍 A/B 解析；
- package A/B 来自同一次有界解析结果的两次确定性装配，原始 UPDATE 只读取一次。

因此，A/B 一致性证明的是“同一冻结输入和中间状态可生成相同语义研究包”，不是全窗口原始 MRT 被独立读取、回放两遍。

## 3. v6 身份与哈希

### 3.1 运行身份

| 项目 | 值 |
| --- | --- |
| Run ID | `research_run_v1_1f8e723ca5c48348da14baeb` |
| Release ID | `rrc25_iran_v1_4053cbf6bdeb04fac5e8e582` |
| Study ID | `iran-rrc25-country-outage-202602-v1` |
| Incident ref | `country_outage/2026-02-27 09:12:32/IR/1/r` |
| 执行模式 | `bounded_pilot` |
| 研究包语义指纹 | `4053cbf6bdeb04fac5e8e582da841d197dabaf310e63ec2b3db2601fe7b9b3db` |
| Worker 语义指纹 | `7a05bdcb5d988445592edf5bfd10b9e281b4ec7cf9b3dc2a8f3c11fb1d144ce4` |

### 3.2 v6 运行代码与计划

| 对象 | 语义 SHA256 | 元数据文件 SHA256 |
| --- | --- | --- |
| v6 运行代码身份 | `2d93889d64db6fc380960e0a5d94b12e93b2bc164a42993c7dc5ffbe2ac9912d` | `977a912303077c9adde7f392fd67b4525fcc3b92c75ebe74f0dd71c6e1cdd44c` |
| v6 worker plan | `75f86e139a70916f56f09c19ac2c324ab59c899e71ce48e1d40ef56b2d7869bf` | `a7977e1a15578400855e35d4c54014bd3626849285286981b6813b45e3a2961a` |
| 最终 dry-run | — | `cc4581b3d86acc46bf2fab08deee2c186bb07287cf2496dc86f98de6c67df35f` |

包内使用规范 JSON，因此包内文件字节 SHA 与外部 pretty-printed 元数据文件 SHA 不同；语义身份没有变化：

| 包内对象 | 包内文件 SHA256 |
| --- | --- |
| `execution/code-identity.json` | `e45a8fb1080dc1525b3a71eefe4cfcb36c5f49dffb77e5d16b1809b53646912d` |
| `execution/worker-plan.json` | `6c3970f74a8b3b77939a0eed423e6b9de653be8345a33c99289ed2efb04f6fa0` |

### 3.3 运行后审计修复与 Git 交付代码身份

v6 成功后又执行了一轮只读对抗审计。审计发现并修复了累计运行时、计划范围身份、生产目录拒写、恢复 orphan 和 Incident locator 口径问题。没有重新读取真实 MRT，也没有改写 v6 包；因此必须同时保留两类不同身份：

| 身份 | SHA256 | 说明 |
| --- | --- | --- |
| v6 历史运行代码 | `2d93889d64db6fc380960e0a5d94b12e93b2bc164a42993c7dc5ffbe2ac9912d` | 实际生成 v6 A/B 包，永久保持原值 |
| 审计修复后 Git 交付代码 | `29f30c0f1ab387005f0a4c908c972eeafc4a0c89094d023b3558cb9805624b71` | 65 个研究处理文件的当前确定性身份；未用于生成 v6 |

审计修复后的代码增加了以下失败关闭约束：

- 540/600 秒按同一 CLI 进程 wall clock 累计，跨 seed、chunk、artifact、投影、装配和发布阶段不重置；
- checkpoint、coordinator 和 A/B 输出统一拒绝 `/home/bgpdata/Domeye`、`/home/bgpdata/Domeye-Core` 与 `/var/www` 等生产或保护路径；独立 `/home/bgpdata/Domeye-Core-dev-data` 仍是允许的研究根；
- `country_outage/2026-02-27 09:12:32/IR/1/r` 严格复用规范算法，Incident ID 为 `inc_v1_ab52ddcad8926f8882fed33a`；未提供旧事实快照时固定为 `fact_link_status=unresolved`；
- 计划执行前重算 Profile、selection、代码、映射、run ID、bounded/full 范围、剩余区间、blocker 和 chunk 身份，禁止把稀疏 pilot 改标签为 full profile；
- run-state 使用追加式 `state_sequence` 与前驱哈希链，状态只允许 `completed → paused → blocked`；内容一致的恢复 orphan 可只读采用，不一致 orphan 失败关闭。

v6 包内旧 locator 元数据是在上述修复前生成，不能作为规范 Incident 事实匹配证据，也不能把包内旧代码身份替换为交付代码身份。v6 的 MRT、RouteEvent、raw ref、artifact hash 与 A/B 字节一致性证据仍按原哈希保存。

### 3.4 Seed producer、输入与映射

| 对象 | 语义 SHA256 | 文件 SHA256 |
| --- | --- | --- |
| Seed producer code | `e072a6fb417e084654d8a12a6e5a7364a0f1e06666a9f21dcd84300276b4b67a` | `9cb4b6b1d863f4351bdae51671c878256063ed08c95bb244571e8cf23a42cb4c` |
| Seed producer worker plan | `9a08f3ed86ca22edd68bd7063bf3927121ef3eb594d9e9d631a1b1528ad5688d` | `a450bb5dc04c3f0d9cdd162527bb10c691952b886fd004e975b4e8f804c26978` |
| Seed sample checkpoint | — | `78e236925e7681c7b8f8ae6fc08be022390e53b7050ae70555e2624c6ac98915` |
| MRT artifact manifest | — | `329078407b5b0dbce871ba0f8f440f7d423aec9fe6b3c1904f5c5edacba08edc` |
| Manifest verification | — | `211da77b39a3f07a4ebf83b593d4e71a74540d7eefcb2f7fb236d2d60c5c016e` |
| Mapping snapshot | `74ccfa4a63d2718b3345c147c81dc9a3a7f79bf7ed909c0892e2c8571d067122` | `05b9809116c3525769e8dc2bd52497ff810a5b4d063cf3c93442d23ed119f9d5` |
| Sparse selection | `57788289882017493ed4c48943712a1ebb1e41200ce81bbf93e27c0a746a0791` | — |
| Execution UPDATE allowlist | `cf5ea8a2862da6695eb07b0fe8b19712899bba40696a9bf7073e777da90cfe77` | — |

## 4. 原始制品与解析人口

### 4.1 有界 Seed RIB

```text
/home/bgpdata/data/ripe/rrc25/2026.02/bview.20260227.1600.gz
```

| 项目 | 值 |
| --- | --- |
| Artifact ID | `art_v1_8b897950ad83cc7239aa8638948791f9` |
| 文件 SHA256 | `9b2d5652913b7d50e05a9b1735322bafb2154ff9034667254336425f8dd9b48e` |
| 压缩大小 | 426,797,681 字节 |
| Checkpoint 下一个物理记录 ordinal | 58,068 |
| Seed 保留 RouteEvent / 状态项 | 17,849 |
| Seed 观测 VP | 118 |
| Seed 跟踪前缀 | 407 |

该 checkpoint 只覆盖前 58,068 个完整物理记录边界，不能作为完整 RRC25 路由人口基线。

### 4.2 主 UPDATE 时槽

```text
/home/bgpdata/data/ripe/rrc25/2026.02/updates.20260228.0810.gz
```

| 项目 | 值 |
| --- | --- |
| Artifact ID | `art_v1_440097189065931144ce321194706ff6` |
| 时槽 | `2026-02-28T08:10:00Z` |
| 文件 SHA256 | `892c455e058efb4aebc5efcc27b63a3263391ba11c2673128d97b22f3e04f738` |
| 压缩大小 / 实际读取 | 4,058,237 字节 / 1 次 |
| 物理记录 | 156,852 |
| 路由记录 | 155,970 |
| 路由元素 | 397,431 |
| ANNOUNCE / WITHDRAW | 361,272 / 36,159 |
| STATE_CHANGE | 226 |
| OPEN / NOTIFICATION / KEEPALIVE | 14 / 9 / 633 |
| Record hash chain | `5a0b748eb8f43cf6cd2b8fce7444afcbe4fb896a92a14bf0cb26f22a71eadf96` |

人口恒等式为：

```text
155,970 route record + 226 STATE_CHANGE + 14 OPEN
  + 9 NOTIFICATION + 633 KEEPALIVE = 156,852 physical record

361,272 ANNOUNCE + 36,159 WITHDRAW = 397,431 route element
```

完整物理解析后只保留进入研究跟踪集合或可能与 IR 相关的路由元素：UPDATE RouteEvent 共 1,518 条，其中 ANNOUNCE 1,466 条、WITHDRAW 52 条。这里的 1,518 是研究保留人口，不是原文件的全部路由元素人口；全量计数仍保存在 parser runtime statistics 中。

### 4.3 RouteEvent 与 raw ref 闭合

| 类型 | 数量 |
| --- | ---: |
| Seed `rib_snapshot` RouteEvent | 17,849 |
| UPDATE RouteEvent | 1,518 |
| RouteEvent 总数 | 19,367 |
| Raw record ref 总数 | 19,367 |
| 唯一 RouteEvent ID | 19,367 |
| 唯一 raw ref ID | 19,367 |
| 唯一 `artifact + record + element` 坐标 | 19,367 |
| RouteEvent/raw ref 坐标缺失或多余 | 0 / 0 |

稳定主引用使用：

```text
artifact_id + file_sha256 + record_ordinal + element_ordinal
```

物理记录 offset、length 和 record hash 作为复核字段保存，gzip stream offset 不作为主身份。

## 5. 08:14 主事件分钟抽查

对 `2026-02-28T08:14:00Z` 至 `08:15:00Z` 的保留 RouteEvent 抽查结果：

| 项目 | 数量 |
| --- | ---: |
| RouteEvent / verified raw ref | 294 / 294 |
| ANNOUNCE / WITHDRAW | 288 / 6 |
| IPv4 / IPv6 | 120 / 174 |
| 唯一 VP / 前缀 | 69 / 9 |
| 唯一物理 record ordinal | 292 |

其中一条完整引用链为：

```text
route_event_id     = rte_v1_00b1d07cf191714a8d6137848d9fd50a
raw_record_ref_id  = raw_v1_062b98ce39278758886279dc6a1ba487
event_time_utc     = 2026-02-28T08:14:39Z
action / prefix    = announce / 151.238.79.0/24
AS_PATH            = 31027 33891 49666 31549
VP                 = vp_v1_743219bdf9d9185fd1bba68c4b6bd896
peer               = AS31027 / 77.243.32.3
artifact_id        = art_v1_440097189065931144ce321194706ff6
file_sha256        = 892c455e058efb4aebc5efcc27b63a3263391ba11c2673128d97b22f3e04f738
record_ordinal     = 146936
element_ordinal    = 0
record_offset      = 24525347
record_length      = 154
record_hash        = 99b483d269b9fddb7345b683d121d99b285fab8a8eebdee2f7661247a0490754
verification       = verified
```

这证明 08:14 附近存在可回溯到原始 MRT 物理记录的双栈路由观测。它不能证明 08:14 是完整国家中断的严格起点，也不能证明全部伊朗人口、物理断路、主动撤回意图、流量影响或政府意图。

## 6. 为什么仍是 `incomplete / not_accepted`

本次冻结的 18 小时 pilot 区间是：

```text
[2026-02-27T16:00:00Z, 2026-02-28T10:00:00Z)
```

该区间期望 216 个五分钟 UPDATE 槽。稀疏选择只有以下 5 个槽：

```text
2026-02-27T22:00:00Z
2026-02-27T22:05:00Z
2026-02-28T08:10:00Z
2026-02-28T08:15:00Z
2026-02-28T08:20:00Z
```

本次 execution allowlist 只打开并完整处理 `2026-02-28T08:10:00Z`。另外四槽是“制品存在但本次明确未处理”，不能写成源文件缺失；其余 211 槽在稀疏选择中保持 gap。

此外：

- Seed RIB 是有界 checkpoint，不是完整 RIB；
- 三张 analysis RIB 只完成选择与哈希绑定，没有在本 worker 中解析；
- `08:10` 前已有连续输入缺口；
- 严格 IR 路由人口完整性和逐槽 VP 参与人口未证明；
- AS_SET、MOAS 和映射不确定项仍被保留。

因此最终结果是：

- 216 个样本全部为 `unknown_after_gap`；
- 数值基线为 `unknown / snapshot_state_gap`；
- Episode、Wave、逐 ASN Episode 记录均为 0；
- 报告 11 项主张中，8 项 `unverifiable`、3 项 `hypothesis_only`；
- 不能复算 `199/595`、`73/126`、旧数据库 `176/556`、IPv4 约 6% 降幅或恢复状态。

“0 个 Episode”只表示当前证据不足以确认 Episode，不表示伊朗没有发生路由中断。

## 7. 质量门与 A/B 包

10 个研究质量门中 6 个通过、4 个失败：

| 质量门 | 状态 | 说明 |
| --- | --- | --- |
| 输入完整性 | fail | bounded pilot 未覆盖冻结 Profile |
| 解析完整性 | pass | 已处理制品单次读取、哈希和计数闭合 |
| 状态连续性 | fail | 前序槽和完整 seed 缺失 |
| VP 覆盖 | fail | 逐槽预期/观测 VP 人口未知 |
| 映射覆盖 | fail | 兼容映射与未决 origin 人口被保留，不能当作严格全人口 |
| 稳定身份 | pass | RouteEvent、raw ref、selection 和包均使用稳定身份 |
| 引用闭合 | pass | RouteEvent 到 raw record/artifact 坐标闭合 |
| 未知值语义 | pass | 所有 gap 使用 null + reason，没有补零 |
| 资源使用 | pass | 时间、读取、临时空间和零 DB 写通过门禁 |
| 派生复现 | pass | 同一真实 worker 内存结果双装配一致 |

Package A/B 均重新通过只读 `verify` 和各自的 27 条 `SHA256SUMS`：

| 对象 | A / B 结果 |
| --- | --- |
| `release_id` | `rrc25_iran_v1_4053cbf6bdeb04fac5e8e582` |
| 语义指纹 | `4053cbf6bdeb04fac5e8e582da841d197dabaf310e63ec2b3db2601fe7b9b3db` |
| `package-manifest.json` SHA256 | `d7751567d336dc42a394ef87dd8a1ba3d037e83799938bf167b784bb52b5e25b` |
| `SHA256SUMS` SHA256 | `6884729136d88ac33536f960696f2875575d644373f5192f8be7590ff24d22dc` |
| 包目录字节数 | 45,930,128 / 45,930,128 |
| `diff -qr` | 0 字节输出，无差异 |

服务器最终证据索引：

```text
metadata/FINAL-EVIDENCE-SHA256SUMS
SHA256 = ff49a59c2a2c5a8eb6410ce62fb2ba65dc8a67c1d1051424d496636e56a59fba

metadata/FINAL-EVIDENCE-VERIFY.txt
SHA256 = 42833410b2976ddb718ff71652970e2dfd40cb9352d26548ade166580b47618f
```

## 8. 资源与时间口径

必须区分以下时间：

| 时间口径 | 值 | 含义 |
| --- | ---: | --- |
| v6 外部命令 wall clock | 407.60 秒 | 本次完整 CLI，包括装载、解析、派生和发布 |
| v6 当前 worker gate runtime | 298.475588898 秒 | 当前受控 worker 阶段 |
| Seed producer + 当前 worker 累计 | 838.897001105 秒 | 两个独立进程的累计值 |
| 记录的单 worker 最大值 | 540.421412207 秒 | 早先 seed producer 在 540 秒软停后的首个完整 record 边界退出 |

v6 外部命令本身在 540 秒软停前完成。seed producer 的 540.421 秒是 record-boundary soft stop 的既有来源事实，比阈值晚 0.421 秒，但仍低于 600 秒硬上限；不能把它写成 v6 当前 worker 耗时。

| 资源 | 值 |
| --- | ---: |
| v6 User / System CPU | 368.18 / 109.49 秒 |
| v6 最大 RSS | 557,692 KiB |
| 累计新增原始读取 | 27,117,963 字节 |
| 其中 seed / 当前 UPDATE | 23,059,726 / 4,058,237 字节 |
| 峰值临时空间 | 36,269,189 字节 |
| 数据库连接 / 写操作 | 0 / 0 |
| 外部 timeout / 退出码 | 590 秒 / 0 |

所有进程均低于 600 秒硬上限；累计读取远低于 50 GB，临时空间远低于 5 GB。

## 9. v4、v5 失败与有界性能诊断

### 9.1 v4

```text
/home/bgpdata/Domeye-Core-dev-data/research-runs/iran-rrc25-p0/20260722T063548Z-main-slot-evidence-v4
```

外层运行观察在 590 秒终止，未发布 package 或 continuation checkpoint，结果临时文件为空。v4 目录本身没有成功落下完整退出/耗时证据，因此不能仅凭 v4 精确断定卡在哪个内部阶段；后续 v5 计时、小样本和代码 profile 才把问题收窄到解析调度与重复纯派生的组合成本。

### 9.2 v5

```text
/home/bgpdata/Domeye-Core-dev-data/research-runs/iran-rrc25-p0/20260722T070425Z-main-slot-evidence-v5
```

| 项目 | 值 |
| --- | --- |
| Run ID | `research_run_v1_f7856b0d4b2fead3746c714e` |
| Code identity | `44d832b18adb66feec203078b97ddcecf72cc06b2b1f7e7f932d362e90d04f1d` |
| Worker plan | `7eab87d15ed096e2059c2beb2e650687e163c64f11859eb8b8ac957520fb23ad` |
| Wall clock / exit | 590.04 秒 / 124 |
| User / System CPU | 550.01 / 109.84 秒 |
| 最大 RSS | 509,556 KiB |
| Voluntary context switches | 30,388,986 |
| Package / checkpoint | 0 / 0 |

v5 已增加 route element 有界保留，但仍对共享的 17,849 条状态人口在 216 个槽中重复做 JSON、哈希、origin 和国家影响投影。

### 9.3 5,000-record 样本

```text
/home/bgpdata/Domeye-Core-dev-data/research-runs/iran-rrc25-p0/20260722T074527Z-queue-benchmark-5000
```

| 样本 | 耗时 | 物理记录 | 路由元素 | 保留 RouteEvent |
| --- | ---: | ---: | ---: | ---: |
| 首 5,000 条，queue=4 | 7.072 秒 | 5,000 | 12,822 | 仅 parser |
| 首 5,000 条，queue=4096 | 6.721 秒 | 5,000 | 12,822 | 仅 parser |
| 首 5,000 条，worker 热路径 | 8.869 秒 | 5,000 | 12,822 | 5 |
| ordinal 145000–149999 | 6.898 秒 | 5,000 | 7,122 | 74 |

这些测量说明 parser 热路径可在有界时间内推进；剩余主要问题是解析调度与解析后重复派生的组合，而不是把 4 MB 压缩文件误解为 4 MB 的语义工作量。

### 9.4 v6 优化

v6 在不改变研究语义的前提下完成：

- 完整 ANNOUNCE/WITHDRAW 统计与研究 RouteEvent 保留分离；
- 只保留已跟踪或可能与 IR 相关的路由元素，原 element ordinal 不重排；
- 合法 OPEN、NOTIFICATION、KEEPALIVE 作为零 RouteEvent 控制记录单独计数；
- stdout 队列 item 上限 4096、源字节上限 8 MiB、保守 retained heap 上界 528 MiB；
- 共享不可变状态的 ID、origin 和国家影响批量复用；
- UPDATE 软停时先原子发布不可恢复诊断 checkpoint，再关闭 parser；
- 同一真实解析结果双装配，不重复读取原始 MRT。

17,849 条状态 × 216 个共享 gap 槽的合成基准中，国家 cohort + impacts 为 1.120 秒，完整派生装配为 2.219 秒；批量结果与原逐槽结果一致。

## 10. 验证结果

v6 运行时及运行后审计已执行：

- v6 原始运行阶段 `test_rrc25*.py`：273 项通过；
- 运行后审计修复阶段 `test_rrc25*.py`：293 项通过；
- `test_p0_bgpdump_adapter`：31 项通过；
- 研究合同：8 个 Schema、7 个正例、8 个反例通过；
- OpenSpec strict：通过；
- `git diff --check`：通过；
- `backend/core.sha256`：13/13；
- 风险门禁新增显式研究路径规则，未知文件继续失败关闭；
- `make check-fast`：637 项开发测试、19 项受影响后端测试、核心 13/13 通过。

- `make check-integration`：637 项开发测试、140 项后端测试、67 项 P0 质量门 fixture、核心 13/13 通过；
- `make check-release`：唯一数据档、637 项开发测试、4 个既有 P0 Schema（10 正例、17 反例、31 文件唯一键）、OpenAPI 类型、30 项前端测试、前端生产构建、140 项后端测试、核心 13/13 和全部发布脚本语法均通过。

三个检查环都是本地只读验证，没有恢复数据库、切换生产或部署。后端测试仍有一条既有 pandas `FutureWarning`，不影响结果。

## 11. 验证与复算命令

当前已发布包只应执行只读验证：

```bash
RUN_DIR=/home/bgpdata/Domeye-Core-dev-data/research-runs/iran-rrc25-p0/20260722T080402Z-main-slot-evidence-v6
cd "$RUN_DIR/workspace/repo"

python3 dev/data_quality/rrc25_iran_bounded_pilot.py verify \
  --package "$RUN_DIR/outputs/package-a"
python3 dev/data_quality/rrc25_iran_bounded_pilot.py verify \
  --package "$RUN_DIR/outputs/package-b"

(cd "$RUN_DIR/outputs/package-a" && sha256sum -c SHA256SUMS)
(cd "$RUN_DIR/outputs/package-b" && sha256sum -c SHA256SUMS)
diff -qr "$RUN_DIR/outputs/package-a" "$RUN_DIR/outputs/package-b"

cd /home/bgpdata/Domeye-Core-dev-data/research-runs/iran-rrc25-p0/20260722T080402Z-main-slot-evidence-v6
sha256sum -c metadata/FINAL-EVIDENCE-SHA256SUMS
```

v6 的输出目录非空，真实 worker 的失败关闭 preflight 会拒绝覆盖。若要复跑，必须创建新的研究目录，重新生成 code identity、dry-run、worker plan 和 execution allowlist，再将所有输出指向新目录；不得把上述验证命令改成覆盖 v6 的写入命令。

## 12. 当前数据缺口

### 12.1 路由数据缺口

- Seed RIB 未完整解析；
- 18 小时 pilot 的 216 个 UPDATE 只处理 1 个；
- 三张 analysis RIB 未解析；
- 原 Profile 剩余区间未处理；
- 事件前、事件中、事件后的连续状态未形成；
- 当前无法区分输入缺口与真实不可见；
- VP 逐槽覆盖人口不完整；
- 动态新出现 IR 前缀的前置上下文未知；
- AS_SET、MOAS 和映射不确定关系仍需保留。

### 12.2 事件结论缺口

当前不能验证：

- 报告所称受影响 ASN 199；
- 73 个完全不可见、126 个部分不可见；
- 旧数据库所称 176/556；
- IPv4 路由资源下降约 6%；
- 是否部分恢复及恢复时间；
- 2 月 27 日“前兆”与 2 月 28 日主事件是否属于同一 Episode；
- 完整受影响 ASN、前缀和地址空间人口。

### 12.3 RRC25 单源能力边界

即使完成全窗口路由回放，RRC25 仍不能单独证明物理链路中断、真实业务流量影响、运营方主动撤回意图、政府或其他主体意图，以及全球所有观测点的一致可见性。这些结论需要流量遥测、链路遥测、多 collector 或外部事实证据。

## 13. OpenSpec / Goal 状态

| 能力 | 当前状态 |
| --- | --- |
| Profile、清单、哈希、映射冻结 | 已完成 |
| 有界 seed checkpoint | 已完成 |
| 单主 UPDATE 时槽完整物理解析 | 已完成 |
| RouteEvent 与 raw ref 稳定闭合 | 已完成 |
| bounded package A/B 确定性校验 | 已完成 |
| 18 小时连续 pilot 回放 | 未完成 |
| 完整 seed 与 analysis RIB | 未完成 |
| 全 Profile 连续回放 | 未完成 |
| Episode / Wave / 逐 ASN 影响 | 未形成 |
| 报告 `199/73/126` 等主张复算 | 未完成 |
| 伊朗事件完整验收 | 未完成 |
| OpenSpec change / Goal | 未完成 |

当前最准确的交付描述是：

> 已完成 RRC25 伊朗事件 bounded pilot 的真实数据流程贯通和主时槽原始证据闭合；完整事件研究闭环仍未完成。

## 14. 后续授权选择

### A. 保持当前停点

保留 v6 为工程闭环证据，不再读取更多原始 MRT。可以证明流程能跑通和主时槽 RouteEvent 可追溯；继续保持 `incomplete/not_accepted`，OpenSpec 和 Goal 不关闭。

### B1. 闭合 18 小时 pilot

需要新授权分片处理完整 seed、216 个五分钟 UPDATE、3 张 analysis RIB，并明确 baseline reference 与 seed 角色。B1 可形成 18 小时连续样本和事件候选，但仍不等于完整 Profile 或原 OpenSpec/Goal。

### B2. 完成原 OpenSpec / Goal

需要新授权执行完整冻结半开窗口：1,928 个五分钟 UPDATE、21 张 analysis RIB、额外 baseline reference RIB、完整 seed、连续状态回放、Episode/Wave、逐 ASN/前缀/双栈/恢复状态，以及报告、旧数据库和研究结果三方对账。只有 B2 成功并通过质量门后，才能关闭原 OpenSpec change 和 Goal。

### C. 只扩展另外四个稀疏时槽

处理 `22:00`、`22:05`、`08:15`、`08:20` 可以增加前兆和主事件附近的 raw-traceable 观测，但连续输入和完整 seed 仍缺失，结果仍必须保持 `incomplete/not_accepted`，不能关闭 OpenSpec 或 Goal。
