## Context

当前国家中断事实表是一条可变峰值摘要：`s_time` 表示首次跨越旧检测阈值，`event_info` 可在后续峰值时改写，`outage_ases` 只保存某次最大人数快照，分子、分母和比例也不保证来自同一时点。它不能支持报告所需的真实起点、两个波次、恢复进度、逐 ASN 影响和原始证据。

RRC25 目标窗口的原始数据基本完整，现有 `feature_country` 也能复现国家级 IPv4 主曲线。仓库已有 artifact manifest、UPDATE RouteEvent pilot、稳定 raw identity、Evidence Bundle v2 和质量门禁，但当前 `BgpdumpRecordStreamFactory` 对 RIB 固定失败关闭，pilot 使用 SQLite 且只覆盖少量 UPDATE，不能直接承担本轮无数据库、长窗口状态重建。

本设计遵循访谈决策：旧项目只读，新项目外围可修改；流程可复用于其他国家但只验收伊朗；新结果不强行匹配旧数字；双栈分别统计；由数据决定 episode/wave；保存所有相关状态变化；本轮不写数据库、不改前端、不部署生产。

## Goals / Non-Goals

**Goals:**

- 在冻结的 RRC25 窗口中，从可验证 RIB/UPDATE 重建双栈逐 VP 状态。
- 生成同一时点一致的五分钟国家样本和逐 ASN/前缀影响人口。
- 区分 episode 与 wave，表达部分恢复、完全恢复和持续时间下界。
- 建立 Incident → sample/episode → RouteEvent → raw record → artifact hash 的闭合证据链。
- 对账报告与旧数据库，并交付可分块、可恢复、可复现的文件制品流程。

**Non-Goals:**

- 不修改旧项目 `/home/bgpdata/Domeye`、原始 MRT 或生产数据库。
- 不修改 `backend/core/` 检测业务逻辑，不回放真实全量 A/B。
- 不新增前端页面、生产 API、数据库表或生产部署。
- 不证明物理断路、会话关闭、流量影响、政府意图或其他 RRC25 无法单独证明的因果结论。
- 不要求精确复现 `176/556`、`199/595` 或 `73/126`。

## Decisions

### 1. 使用独立研究 profile，不改全局 data profile

新增事件级 profile，显式记录研究窗口、seed 选择、catch-up 范围、观察终点、映射和算法参数。全局 `config/data-profile.json` 继续描述固定二三月产品数据档。

未采用“直接修改全局窗口”，因为这会影响现有 API、测试和发布口径，也无法表达 seed RIB 早于统计窗口的事实。

### 2. 输入分为 state seed、baseline reference、catch-up 和 analysis 四种角色

resolver 优先选择窗口起点时刻的完整 RIB 作为 `state_seed_rib`，否则选择此前最近的完整 RIB并回放其后至起点的 UPDATE。严格早于起点的最近 RIB另作为 `baseline_reference_rib`，用于成员稳定性对照。catch-up UPDATE 只参与状态初始化，不计入研究窗口指标。窗口末端 `08:40Z` 是状态边界，UPDATE 输入仍遵守半开区间。伊朗验收清单必须对账为半开窗口 UPDATE 1,928槽、窗口内 RIB 21张和额外 baseline reference RIB 1张。

未采用“直接把窗口首个 feature_country 点当 RIB”，因为聚合指标无法恢复 VP、prefix、AS_PATH 和逐 ASN 状态。

### 3. 在现有 RouteEvent 安全身份上增加经黄金样本验收的 RIB 适配

复用 `artifact_id_v1`、`route_event_id_v1`、`vp_id_v1`、record/element ordinal 和 parser attestation。先建立 TABLE_DUMP/TABLE_DUMP_V2 黄金 fixture，验证一个 physical record 多元素、peer、AFI、prefix、AS_PATH segment 和异常类型，再允许 RIB 进入研究路径。现有 UPDATE pilot 的失败关闭边界在验收前保持不变。

未采用“直接解析 bgpdump 文本并跳过 ordinal 验证”，因为那会产生无法定位或错误定位的 raw_traceable 证据。

### 4. 状态键采用 collector + VP + AFI/SAFI + prefix

RIB snapshot 初始化状态；announce 替换该键的路径状态；withdraw 只删除该 VP 的该前缀。BGP4MP STATE_CHANGE 进入独立 `PeerSessionObservation`：会话失效影响 VP 可观测性，但不能冒充该 VP 所有前缀的显式 withdrawal。MOAS、AS_SET、空路径和 confederation 不被压平成单一 origin，均保留质量标志。回放顺序由 event time、artifact slot、record ordinal、element ordinal确定。

未采用旧 `BGPRib` 的内存对象作为证据，因为它是可变检测状态，且旧持久化丢失严格 VP 身份。

### 5. 研究集合是基线 IR 前缀与动态 IR origin 的并集

首先从起点状态和冻结兼容映射形成 IR ASN cohort 与前缀集合；随后扫描全部 UPDATE 的轻量头部/route element，将窗口中新出现的 IR origin 前缀加入动态集合。最终只发布这些相关前缀的完整状态变化，但原始坐标保持全文件 ordinal。

未采用“先按旧 outage_ases 过滤”，因为该集合不是全事件并集；也未采用“复制所有 RRC25 RouteEvent”，因为本轮只需单事件闭环且受 5GB 制品边界约束。

### 6. 采用文件分片而不是 SQLite 或生产数据库

中间与最终结果使用规范 JSONL gzip 分片、确定性 JSON manifest 和按需 Parquet 投影；每个分片绑定输入/配置/代码/映射哈希并原子发布。检查点只保存完整 record 边界、已发布分片和内容哈希。大型制品进入服务器研究目录，Git 只保留摘要。

未采用现有 SQLite pilot，因为用户已明确本轮不写数据库，且单库文件不利于受控分块、内容寻址和部分重跑。Parquet 仅作为分析投影，JSONL/raw refs 是审计身份来源。

### 7. 同时输出兼容指标和修订指标

兼容视图复用冻结的旧 AS→国家映射和明确命名的旧 IPv4 等价值算法；修订视图使用去重地址并集、分地址族分类和明确 MOAS 语义。二者不得共用没有口径后缀的字段。

国家聚合前缀地址空间按集合去重；逐 ASN 明细可保留同一 MOAS 前缀对多个 origin 的关系，但必须声明其地址量不可直接相加为国家总量。IPv6 使用 `/48` 等价值，不把巨大整数称为真实可用地址数。

### 8. episode 表达持续异常，wave 表达同一 episode 内再次下降

异常样本默认定义为 IPv4 可见地址并集低于基线 99% 或同快照受损 ASN 比例高于 3%；连续两个异常槽确认开始，首槽为 `onset_at`、确认槽为 `detected_at`。部分恢复要求连续六槽达到基线 99%；完全恢复要求连续六槽回到基线正常波动带。只有完全恢复确认后再次异常才拆成新 episode，否则新下降形成新 wave。wave 的默认显著性为 `max(基线的0.5%, 3×基线MAD)`，所有参数均进入 profile。算法同时输出 `episode_count`、`wave_count` 和 `split_evidence`，不预设“前兆”因果关系。

正常波动带由基线中位数和稳健离散度生成，并设置有版本的绝对下限，避免零波动时区间退化。具体阈值全部进入 profile 和结果，不隐藏在代码常量中。

未采用旧 `s_time/e_time` 或固定前中后长度，因为它们不能表达长事件、多个波次、短暂跨线和未完全恢复。

### 9. 三类核心研究记录

- `country_outage_sample`：每个五分钟边界的同快照指标、集合、覆盖和来源。
- `country_outage_episode` / `country_outage_wave`：起点、峰值、低谷、恢复、状态、持续时间和分割证据。
- `country_outage_episode_as`：每个 ASN 的首次受损、峰值成员、累计影响、恢复、双栈损失和证据引用。

旧 Incident 保留 source fact 身份，通过显式 mapping 与零个、一个或多个研究 episode 关联。关系只表示对账和观测相交，不表示因果。

### 10. Evidence v2 保留旧事实并引用新研究结果

Evidence Bundle v2 的 Incident 继续承载旧事实表身份；新的 onset、peak、wave 和 recovery 通过研究 sidecar、MetricWindow、Evidence registry 和 RouteEvent/raw refs表达。若现有严格 schema 无法直接承载 episode 字段，则新增内容寻址 sidecar schema并在 Evidence registry 中引用，不静默改变 v2 旧字段含义。

报告中的每条主张单独评级：可复算且一致为 `confirmed`，可复算但口径或数值不同为 `revised`，缺少必要数据源为 `unverifiable`，机制或意图推断为 `hypothesis_only`。

### 11. 统一入口是协调器，不是不可中断的单进程

提供一个入口命令负责 dry-run、资源估算、分块调度、断点恢复、质量门和最终组装。每个 worker 在九分钟软限前于 record 边界退出，避免越过十分钟审批线；累计用量由运行 manifest 汇总。任何数据库目标、5GB 临时空间风险或 50GB 新增原始读取风险在执行前拒绝。

未采用“一次长进程跑到底”，因为 RIB 解析和长窗口回放可能超过单次运行审批边界，也不利于失败恢复。

## Risks / Trade-offs

- [RIB ordinal 或属性保真仍未验收] → 先以手工可核对黄金 fixture、异常 MRT 类型和 round-trip raw ref 测试作为硬门；未通过前不跑研究窗口。
- [完整相关状态变化仍可能达到或超过 5GB] → dry-run 估算、按 artifact/record 分片、gzip、只发布相关变化；预计触及排他上限即停止并请求批准。
- [旧国家映射没有可信历史版本] → 保存原文件哈希和兼容结果，另输出修订映射，不把任何一套称为绝对国家归属真相。
- [双栈和 MOAS 使“完全不可见”定义复杂] → 分地址族输出，再给有明确定义的综合分类；所有国家总量使用去重并集。
- [episode/wave 阈值会影响划分] → 参数版本化，输出支撑样本和敏感性摘要；伊朗验收检查边界证据，不要求预定 episode 数。
- [单一 Incident 可能映射多个 episode] → 使用显式多值 mapping 和对账关系，不修改旧 Incident ID。
- [统一入口内部仍需多次执行] → 将“一个入口”解释为可恢复协调命令，每次 worker 严格受资源门约束。
- [Parquet 库在目标环境不可用] → 规范 JSONL gzip 是必需制品，Parquet 是可选投影，不影响证据闭环。

## Migration Plan

1. 只提交 OpenSpec 方案并通过严格校验，不运行数据处理。
2. 在 `backend/core/` 外实现研究合同、fixture 和纯函数；先运行定向单元测试与核心哈希检查。
3. 用极小 RIB/UPDATE 黄金样本验收解析身份和状态回放。
4. 对伊朗窗口执行只读 dry-run，确认输入量、临时空间和分块计划均在审批边界内。
5. 分块生成服务器研究候选制品，质量门失败时保留失败证据但不发布 accepted 包。
6. 完成伊朗事件对账、轻量浏览验收和中文报告；不激活生产、不写数据库。
7. 后续若要入库、提供 API 或修改前端，另开 OpenSpec change 并重新取得授权。

回滚仅需停止协调器并删除尚未发布的 staging；已发布内容寻址制品保持只读，可通过清单隔离，不影响现有服务。

## Open Questions

无阻断性产品问题。旧系统所称“前兆”和主事件究竟构成两个 episode 还是一个 episode 的两个 wave，由本次数据结果决定；当前不预设因果关系。
