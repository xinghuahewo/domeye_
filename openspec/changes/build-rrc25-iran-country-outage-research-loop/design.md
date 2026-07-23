## Context

当前国家中断事实表是一条可变峰值摘要：`s_time` 表示首次跨越旧检测阈值，`event_info` 可在后续峰值时改写，`outage_ases` 只保存某次最大人数快照，分子、分母和比例也不保证来自同一时点。它不能支持报告所需的真实起点、两个波次、恢复进度、逐 ASN 影响和原始证据。

只读实库核查进一步确认：`feature_country(source=r,country=伊朗)` 在目标窗口有
`1,928/1,928` 个五分钟槽，国家事实的 176 个 ASN 与 `22:34:40` 活跃 AS
事实集合完全一致，并可从 AS/前缀事实计算 2,842 个活跃中断前缀。因此数据库应先
承担研究主体和定位索引，原始 RRC25 只补 VP、报文、路径和阶段状态证据。仓库已有
artifact manifest、UPDATE RouteEvent pilot、稳定 raw identity、Evidence Bundle v2
和质量门禁，可用于关键槽定向取证；它们不再构成先重放完整窗口的理由。

本设计遵循访谈决策：旧项目只读，新项目外围可修改；流程可复用于其他国家但只验收伊朗；新结果不强行匹配旧数字；双栈分别统计；由数据决定 episode/wave；数据库先行、原始数据按字段缺口定向补证；本轮不写数据库、不改前端、不部署生产。

## Goals / Non-Goals

**Goals:**

- 在只读、可复现事务中冻结旧 Incident、国家五分钟曲线、ASN/前缀影响和恢复信号。
- 从数据库生成字段级缺口矩阵，并只为缺失证据选择 RRC25 关键槽和代表实体。
- 在获选的 RRC25 事件窗口中，从可验证 RIB/UPDATE 重建代表实体的双栈逐 VP 状态。
- 生成数据库五分钟国家样本，并为定向样本补逐 ASN/前缀原始证据。
- 区分 episode 与 wave，表达部分恢复、完全恢复和持续时间下界。
- 建立 Incident → sample/episode → RouteEvent → raw record → artifact hash 的闭合证据链。
- 对账报告与旧数据库，并交付可分块、可恢复、可复现的数据库快照与证据制品流程。

**Non-Goals:**

- 不修改旧项目 `/home/bgpdata/Domeye`、原始 MRT 或生产数据库。
- 不修改 `backend/core/` 检测业务逻辑，不回放真实全量 A/B。
- 不把 1,928 个 UPDATE 槽的完整状态重放作为伊朗验收前置条件。
- 不新增前端页面、生产 API、数据库表或生产部署。
- 不证明物理断路、会话关闭、流量影响、政府意图或其他 RRC25 无法单独证明的因果结论。
- 不要求精确复现 `176/556`、`199/595` 或 `73/126`。

## Decisions

### 1. 使用独立研究 profile，不改全局 data profile

新增事件级 profile，显式记录研究窗口、seed 选择、catch-up 范围、观察终点、映射和算法参数。全局 `config/data-profile.json` 继续描述固定二三月产品数据档。

未采用“直接修改全局窗口”，因为这会影响现有 API、测试和发布口径，也无法表达 seed RIB 早于统计窗口的事实。

### 2. 数据库先冻结研究主体，raw 输入只为证据缺口服务

数据库阶段使用固定 reader、`REPEATABLE READ READ ONLY` 和最终 rollback，先冻结：
旧国家事实、1,928 槽国家曲线、AS/前缀事实、关键时刻活跃集合、恢复信号及其来源
字段。业务时间统一按 `Asia/Shanghai` 解释并同时输出 UTC。数据库中的真实零值与
未知必须分开；ASN 月表按活动稀疏表解释。

数据库阶段完成后，才为 `06:35`、`18:45`、`22:30` 三个关键槽以及少量代表
ASN/前缀解析原始数据。每个证据包的输入按需要分为 state seed、baseline
reference、catch-up 和 event UPDATE；没有阶段状态需求的槽不得为形式完整而读取
RIB 或长 catch-up。

未采用“先做完整窗口 state seed + 1,928 UPDATE 回放”，因为它重复计算数据库已经
完整保存的国家指标，扩大资源和审计面，却不能自动解决 VP/raw ref 等字段缺口。

### 3. 定向 raw 输入仍分为 state seed、baseline reference、catch-up 和 analysis 四种角色

resolver 为每个获选证据包选择关键槽之前最近的完整 RIB，并仅对代表实体执行必要
catch-up。严格早于事件窗的最近 RIB可作为 `baseline_reference_rib`。数据库主线
继续使用冻结半开窗口；raw 证据包则使用各自更小的半开区间。全窗口
1,928 个 UPDATE 槽和 22 张 RIB 只作为来源库存对账，不是必须读取的执行清单。

未采用“直接把 `feature_country` 点当 RIB”，因为聚合指标无法恢复 VP、prefix、
AS_PATH 和逐 ASN 状态；也未因此否定它对国家曲线、事件定位和恢复信号的直接价值。

### 4. 在现有 RouteEvent 安全身份上增加经黄金样本验收的 RIB 适配

复用 `artifact_id_v1`、`route_event_id_v1`、`vp_id_v1`、record/element ordinal 和 parser attestation。先建立 TABLE_DUMP/TABLE_DUMP_V2 黄金 fixture，验证一个 physical record 多元素、peer、AFI、prefix、AS_PATH segment 和异常类型，再允许 RIB 进入研究路径。现有 UPDATE pilot 的失败关闭边界在验收前保持不变。

未采用“直接解析 bgpdump 文本并跳过 ordinal 验证”，因为那会产生无法定位或错误定位的 raw_traceable 证据。

### 5. 状态键采用 collector + VP + AFI/SAFI + prefix

RIB snapshot 初始化状态；announce 替换该键的路径状态；withdraw 只删除该 VP 的该前缀。BGP4MP STATE_CHANGE 进入独立 `PeerSessionObservation`：会话失效影响 VP 可观测性，但不能冒充该 VP 所有前缀的显式 withdrawal。MOAS、AS_SET、空路径和 confederation 不被压平成单一 origin，均保留质量标志。回放顺序由 event time、artifact slot、record ordinal、element ordinal确定。

未采用旧 `BGPRib` 的内存对象作为证据，因为它是可变检测状态，且旧持久化丢失严格 VP 身份。

### 6. 定向研究集合来自数据库影响索引与原始映射复核

先从数据库冻结的 176 个 ASN、2,842 个活跃前缀、关键槽和旧路径事实构造候选索引；
再用冻结兼容映射和原始 RouteEvent 校验代表实体是否属于 RRC25 可见 IR cohort。
原始解析只发布关键槽和代表实体的相关状态变化，原始坐标仍保持全文件 ordinal。

数据库集合是定位索引而非原始真值：系统 MUST 保留“不在旧集合但命中目标前缀/
origin”的反向发现，并在对账中报告。未采用“复制所有 RRC25 RouteEvent”，因为本轮
只需单事件的字段级补证且受 5GB 制品边界约束。

### 7. 采用文件分片而不是 SQLite 或生产数据库

中间与最终结果使用规范 JSONL gzip 分片、确定性 JSON manifest 和按需 Parquet 投影；每个分片绑定输入/配置/代码/映射哈希并原子发布。检查点只保存完整 record 边界、已发布分片和内容哈希。大型制品进入服务器研究目录，Git 只保留摘要。

未采用现有 SQLite pilot，因为用户已明确本轮不写数据库，且单库文件不利于受控分块、内容寻址和部分重跑。Parquet 仅作为分析投影，JSONL/raw refs 是审计身份来源。

### 8. 同时输出兼容指标和修订指标

兼容视图复用冻结的旧 AS→国家映射和明确命名的旧 IPv4 等价值算法；修订视图使用去重地址并集、分地址族分类和明确 MOAS 语义。二者不得共用没有口径后缀的字段。

国家聚合前缀地址空间按集合去重；逐 ASN 明细可保留同一 MOAS 前缀对多个 origin 的关系，但必须声明其地址量不可直接相加为国家总量。IPv6 使用 `/48` 等价值，不把巨大整数称为真实可用地址数。

### 9. episode 表达持续异常，wave 表达同一 episode 内再次下降

异常样本默认定义为 IPv4 可见地址并集低于基线 99% 或同快照受损 ASN 比例高于 3%；连续两个异常槽确认开始，首槽为 `onset_at`、确认槽为 `detected_at`。部分恢复要求连续六槽达到基线 99%；完全恢复要求连续六槽回到基线正常波动带。只有完全恢复确认后再次异常才拆成新 episode，否则新下降形成新 wave。wave 的默认显著性为 `max(基线的0.5%, 3×基线MAD)`，所有参数均进入 profile。算法同时输出 `episode_count`、`wave_count` 和 `split_evidence`，不预设“前兆”因果关系。

正常波动带由基线中位数和稳健离散度生成，并设置有版本的绝对下限，避免零波动时区间退化。具体阈值全部进入 profile 和结果，不隐藏在代码常量中。用户提供的 `2026-02-27T22:00:00Z` 仅作为“最早可能前兆”的基线扩展排除边界，状态固定为 `candidate_not_confirmed`，不等同于确认 onset，也不授权前兆或因果结论。

未采用旧 `s_time/e_time` 或固定前中后长度，因为它们不能表达长事件、多个波次、短暂跨线和未完全恢复。

### 10. 三类核心研究记录

- `country_outage_sample`：每个五分钟边界的同快照指标、集合、覆盖和来源。
- `country_outage_episode` / `country_outage_wave`：起点、峰值、低谷、恢复、状态、持续时间和分割证据。
- `country_outage_episode_as`：每个 ASN 的首次受损、峰值成员、累计影响、恢复、双栈损失和证据引用。

旧 Incident 保留 source fact 身份，通过显式 mapping 与零个、一个或多个研究 episode 关联。关系只表示对账和观测相交，不表示因果。

### 11. Evidence v2 保留旧事实并引用新研究结果

Evidence Bundle v2 的 Incident 继续承载旧事实表身份；新的 onset、peak、wave 和 recovery 通过研究 sidecar、MetricWindow、Evidence registry 和 RouteEvent/raw refs表达。若现有严格 schema 无法直接承载 episode 字段，则新增内容寻址 sidecar schema并在 Evidence registry 中引用，不静默改变 v2 旧字段含义。

报告中的每条主张单独评级：可复算且一致为 `confirmed`，可复算但口径或数值不同为 `revised`，缺少必要数据源为 `unverifiable`，机制或意图推断为 `hypothesis_only`。

### 12. 统一入口先导出数据库，再按缺口协调原始证据

入口先负责数据库只读快照、缺口矩阵和最小 raw 请求；只有请求状态被显式推进时，
才负责资源估算、关键槽解析、断点恢复、质量门和最终组装。raw worker 默认在九分钟
软限前于 record 边界退出；若固定 artifact、实体、字节和零数据库写入边界不变，
用户可在软停后明确批准记录化的同范围时间延长。累计用量由运行 manifest 汇总。
任何数据库写目标、5GB 临时空间风险或 50GB 新增原始读取风险在执行前拒绝。

未采用“一次长进程跑到底”，因为 RIB 解析和长窗口回放可能超过单次运行审批边界，也不利于失败恢复。

## Risks / Trade-offs

- [RIB ordinal 或属性保真仍未验收] → 先以手工可核对黄金 fixture、异常 MRT 类型和 round-trip raw ref 测试作为硬门；未通过前不跑研究窗口。
- [定向证据仍可能需要较长 catch-up] → 优先使用相邻 RIB 和数据库代表实体 allowlist；不得静默扩大为完整窗口，预计触及排他上限即停止并请求批准。
- [旧国家映射没有可信历史版本] → 保存原文件哈希和兼容结果，另输出修订映射，不把任何一套称为绝对国家归属真相。
- [双栈和 MOAS 使“完全不可见”定义复杂] → 分地址族输出，再给有明确定义的综合分类；所有国家总量使用去重并集。
- [episode/wave 阈值会影响划分] → 参数版本化，输出支撑样本和敏感性摘要；伊朗验收检查边界证据，不要求预定 episode 数。
- [单一 Incident 可能映射多个 episode] → 使用显式多值 mapping 和对账关系，不修改旧 Incident ID。
- [统一入口内部仍需多次执行] → 将“一个入口”解释为可恢复协调命令，每次 worker 严格受资源门约束。
- [Parquet 库在目标环境不可用] → 规范 JSONL gzip 是必需制品，Parquet 是可选投影，不影响证据闭环。

## Migration Plan

1. 只读导出伊朗旧事实、1,928 槽国家曲线、关键时刻活跃集合和恢复信号。
2. 发布数据库缺口矩阵与 `not_executed` 的最小 raw 请求，先完成报告研究主体。
3. 在 `backend/core/` 外保留研究合同、fixture、RIB/UPDATE 黄金样本与定向解析能力。
4. 对三个关键槽和代表实体执行 raw dry-run，确认输入量、临时空间和每进程时间均在边界内。
5. 先生成明确隔离的数据库兼容指标代理 Episode/Wave；定向 raw 只登记为消息旁证，
   不把代理结果升级为正式状态 Episode。
6. 完成伊朗事件主张对账和中文报告；状态证据质量门未通过时发布
   `workflow_completed/not_accepted`，不激活生产、不写数据库。
7. 后续若要完整人口重建、入库、提供 API 或修改前端，另开 OpenSpec change 并重新取得授权。

回滚仅需停止协调器并删除尚未发布的 staging；已发布内容寻址制品保持只读，可通过清单隔离，不影响现有服务。

## Open Questions

无阻断性产品问题。数据库兼容指标代理可回答聚合曲线中的候选 episode/wave；
它不能确认旧系统所称“前兆”的状态级 Episode 身份或因果关系。只有后续明确请求
代表实体状态回放时，才读取相邻 RIB/catch-up 并重新评估正式 Episode。
