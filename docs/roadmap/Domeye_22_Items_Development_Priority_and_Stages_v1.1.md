# Domeye 22 项整改开发优先级与阶段计划 v1.1

> 形态二：用于排开发顺序和阶段验收。本文按依赖与风险重排 R01–R22，但不改变其审计编号和原始含义。

| 项目 | 内容 |
|---|---|
| 配套审计文档 | [Domeye 敌对评审 22 项整改审计对应表 v1.1](../requirements/Domeye_Adversarial_Review_22_Items_Traceability_v1.1.md) |
| 目标架构 | [Domeye Agent 目标架构 v1.1](../architecture/Domeye_Agent_Target_Architecture_v1.1.md) |
| 审计起始代码基线（历史） | [main@6a4bbd41aa712c12080a0126e5f8b1ec1440a9ca](../architecture/Domeye_Current_Code_Baseline_2026-08-16.md) |
| 当前首片合同 | [Domeye 首个纵向切片锚点合同 v1.0](../architecture/Domeye_First_Vertical_Slice_Anchor_v1.0.md) |
| 文档版本 | v1.1 |
| 日期 | 2026-08-16 |
| 用途 | 决定 22 项先后顺序、阶段位置、依赖、Gate 和退出证据 |
| 不代表 | 已经创建 GitHub Issue、已经开工或已经完成 |

---

## 1. 如何理解这份排序

原始敌对评审把 R01–R08 定义为八个 P0 级问题，它们全部仍是生产晋级阻断项。本计划没有降低其中任何一项。

但“风险等级”和“编码顺序”不是一回事：

- 有些问题要在 M0 先定合同，主体代码到 M2 才实现，例如 R06。
- 有些问题虽然不在原始八个 P0 中，却必须在第一行代码前纠正，否则 Codex 会继续走错架构，例如 R09、R16。
- 有些问题只有真实数据证明需要后才能建设，例如 R10、R12；提前实现反而是偏离。

因此本文同时给出：

1. **全局执行序号 1–22**：在资源受限的小团队中，优先把精力放在哪里；
2. **阶段位置 M0–M5**：每个 R 项在哪个阶段只定合同、在哪个阶段主建设、在哪个阶段验证；
3. **Decision Gate**：没有触发条件时必须保持 Deferred 的工作。

---

## 2. 排序原则

排序依次考虑：

1. 是否会让后续代码沿错误架构继续增长；
2. 是否会使 Receipt、Typed Finding、回答或产品声明失去可信含义；
3. 是否是首个真实 Agent 纵向切片的前置条件；
4. 是否可以复用当前 `main` 资产，还是需要新平台；
5. 是否必须由真实负载、故障或成本数据触发；
6. 修错成本是否会随代码增长快速上升。

### 2.1 四个执行优先级带

| 优先级带 | 序号 | 含义 |
|---|---:|---|
| A0 — 方向与可信性阻断 | 1–9 | 不先处理，后续开发可能建立在错误目标、错误身份或错误领域语义上 |
| A1 — 首个真实 Agent 切片 | 10–16 | 跑通 `Proposal → Admission → Execution → Observation → Replan → Answer` 的必要工作 |
| B — Artifact、回答硬化与规模化 | 17–20 | 在首个闭环成立后，扩展多 Finding、跨步骤证据和可扩展 Artifact |
| C — 条件基础设施 | 21–22 | 只有 Decision Gate 证明真实需要才开工；不是预定建设承诺 |

---

## 3. 22 项全局开发优先级

| 排名 | R 项 | 优先级带 | 为什么排在这里 | 主建设阶段 | 关键依赖 / Gate |
|---:|---|---|---|---|---|
| 1 | R03 Gate 语义漂移 | A0 | Policy 和 Receipt 的词义不唯一，后续所有证据都会失真 | M0 | 无 |
| 2 | R02 产品与数据边界 | A0 | 不先分开目标产品、当前能力和回答边界，Agent 与评测都没有正确目标 | M0 | 当前代码基线 |
| 3 | R01 认证语义倒置 | A0 | 必须先把“阻断正确”和“任务成功”分开，否则进度继续虚高 | M0 定义；M1/M5 认证 | R02 |
| 4 | R09 拒绝 Common Plan | A0 | 直接决定新运行主链；晚改会继续扩张完整 Plan / DAG | M0 合同；M1 Action | 目标架构 v1.1 |
| 5 | R16 Template 退出路由 | A0 | 防止把能力族改名后继续充当模板分类器 | M0 规则；M1 替换；M4 认证 | R09 |
| 6 | R04 冻结自研 Workflow 扩张 | A0 | 先停止投入错误基础设施，再用真实需求决定 Durable Job | M0 冻结；M3 条件实施 | R09；`DG-DUR` |
| 7 | R07 规范化与供应链身份 | A0 | Action、Artifact / Finding 和回答 Trace 的同候选证据都依赖稳定身份 | M0–M2 | R03；`main` 候选规则 |
| 8 | R18 四种完整性 | A0 | 不先定义完整性的含义，Tool、Finding 和回答会继续产生越界语义 | M0 合同；M1/M2 接入 | R02 |
| 9 | R19 AS_PATH 相邻语义 | A0 | 领域词义错误会被稳定地计算和发布，越晚修历史兼容成本越高 | M0 定义；M1/M2 接入 | R02 |
| 10 | R05 真实 Agent Eval | A1 | M1 必须能证明滚动闭环，而不是再次只测 fixture | M1；M4 扩展 | R01、R09、R16 |
| 11 | R08 逐 Proposal 安全与 DLP | A1 | Pi 的提议必须在第一条真实执行链上受到权威准入 | M1；M2–M5 硬化 | R03、R07、R09 |
| 12 | R13 Typed Adapter | A1 | 历史 Executor 混合多层责任且已退役；扩展首个 Action 时仍需清楚的输入输出边界 | M1 | R03、R09 |
| 13 | R14 Finding-to-Answer 边界 | A1 | 首个真实切片必须能把结构化结果安全表达给用户，不能把回答可信性推迟到另一个平台阶段 | M0 最小不变量；M1 实现；M2–M5 硬化 | R02、R13、R18、R19 |
| 14 | R17 SLO、预算和容量 | A1 | 没有真实成本和上限，就无法判断 Action、Job 或 Compute 需求 | M1 起步；M3–M5 收敛 | R01、R09 |
| 15 | R15 Teacher → Student 默认退役 | A1 | 先简化回答链，避免把双模型复杂性带入首个真实切片 | M1；M4 条件 A/B | R01、R14 |
| 16 | R20 时间删失语义 | A1 | 首个时序类切片必须避免稳定地产生伪精确时间 | M1 Operator / Finding / Guard；M2 扩展 | R18、R13、R14 |
| 17 | R06 三类核心 Evidence 语义 | B | M1 先做最小分离，M2 再扩展跨步骤 lineage；不建设独立 Claim / Finding Support Graph | M1 最小；M2 主建设 | R07、R14、R18 |
| 18 | R22 双人口与分母 | B | 多 Finding 和比例回答必须明确统计人口与分母 | M2；M4 扩展 | R18、R13、R14 |
| 19 | R21 多指标严重度 | B | 先有明确人口和有边界的 Finding，再验证排序和展示是否有产品价值 | M2；M4 认证 | R18、R20、R22 |
| 20 | R11 分区 ResultSet | B | 历史小 fixture 与 Runtime 已退役；若真实需求触发，先测 rows / bytes，再决定分区和聚合优化 | M2 合同；M4 优化 | R17、R18、R22 |
| 21 | R12 Bounded Map | C | 只有真实 Durable Job 和 fan-out 需求出现后才有实现对象 | M3 条件项 | R04、R06、R07、R08、R17、R22；`DG-DUR` |
| 22 | R10 Restricted Compute Engine | C | 只有 Operator 膨胀和负载数据证明收益后才建设，不能先造平台 | M4 条件项 | R11、R13、R17；`DG-COMP` |

### 3.1 关于第 21、22 名

R12 和 R10 不是“不重要”，而是**不能无条件开工**：

- `DG-DUR` 没有证明真实耐久和 fan-out 需求时，R12 保持 Deferred。
- `DG-COMP` 没有证明通用计算下沉的净收益时，R10 保持 Deferred。

两者之间的数值顺序不覆盖 Gate。如果 `DG-COMP` 先被真实数据触发，R10 可以先于 R12 实施。

---

## 4. 阶段路线

### M0 — 架构边界与首个切片合同

**这个阶段解决什么**

让 Codex 知道哪些旧目标已经废止、当前 `main` 上什么是真的、首个纵向切片按什么合同和证据验收。

**主建设**

- R03：Policy URI、版本和旧 Gate 映射；
- R02：目标产品、当前能力、数据和回答边界；
- R01：三层认证名称、Candidate 和 Gate；
- R09：小合同、滚动执行和 Action / Job 分界；
- R16：Question Template、Capability Family 的非路由规则；
- R04：冻结通用 Workflow Runtime 扩张；
- R07：Canonicalization Contract 和候选身份 v1；
- R18：四种完整性最小 Schema；
- R19：AS_PATH 观测序列相邻语义。

**只定最小合同，不在 M0 大规模实现**

- R05 Eval Harness 任务格式；
- R06 三类核心 Evidence 语义和回答 Trace 的边界；
- R08 Permission IR / PEP 边界；
- R13 Adapter 分类；
- R14 Typed Finding、Answer Context、Response Guard 和安全回退的最小不变量；
- R15 Teacher 默认退出决定；
- R17 最小 Trace 和指标；
- R20 时间区间合同。

**用户能看到什么**

主要是管理和设计口径变得诚实；这一步不应宣传新增了 Agent 产品能力。

**DG0 退出条件**

- 目标架构、当前代码基线和两份 22 项文档成为权威依据；
- `main` 是主干、合并目标和候选取证起点；
- 首个真实任务、数据范围、模型候选、允许 Capability 和验收阈值已写清；
- 旧 Common Plan、Question Template 路由和 `codex/prod` 规则被标记为待迁移旧规则；
- L1–L7、X1–X4 没有空白责任。

### M1 — Interactive Agent 滚动闭环

**这个阶段解决什么**

让一个真实用户任务不再走“一次分类 → 完整 DAG → 全部执行”，而是由 Pi 每次提出一个 Proposal，Trust Kernel 逐项准入，结果返回 Pi 后再决定下一步。

**主建设**

- R05：真实 Agent Eval Harness；
- R08：逐 Proposal 准入和最小 Permission IR；
- R13：第一个 typed Adapter；
- R14：首个 Typed Finding → Answer Context → Renderer → Response Guard 链；
- R17：端到端 Trace、预算和成本画像；
- R15：Teacher / Student 退出默认链；
- R20：时间类 Operator 输出上下界和删失。

**必须集成回归**

- R01：真实 executed_supported 和多 trial；
- R03、R07：每个 Action 的 Policy / Registry / Candidate Receipt；
- R09、R16：没有完整 DAG 和模板路由；
- R18、R19：范围和 AS_PATH 语义不越界；
- R02：回答仍只承诺 RRC25 BGP 控制面证据。

**用户能看到什么**

在一个受限真实场景中，Agent 可以：理解当前目标、请求一项能力、遇到拒绝或失败后重新决定、信息不足时澄清、目标满足时停止。

**DG1 退出条件**

DG1 完全采用锚点合同的 J1–J5，不再以三条概括旅程替代：J1 真实成功链；J2 第二
Action 被独立拒绝；J3 读取失败或身份/单位错误；J4 Renderer 越界并被 Guard 阻断；
J5 并列极值、null、全空、缺槽和错身份等确定性边界。

同一 Candidate 上，J1 至少独立运行 30 次并满足 `Pass@1 ≥ 27/30`、按执行顺序分组的
`Pass³ ≥ 8/10`；J2–J5 的全部预注册样例通过，所有零容忍失败为 0，并具有真实模型、
真实 Tool、Artifact / Receipt、延迟、成本和独立 Acceptance Record。J4 返回 `block` 后
不得再次调用模型，只能使用同一 Answer Context 做确定性安全回退。

### M2 — Artifact、回答硬化与规模化

**这个阶段解决什么**

在 M1 最小 Finding-to-Answer 链已经成立后，扩展多 Finding、跨步骤 lineage、人口与分母、回答 Trace 和真实数据规模；不建设通用 Claim 平台。

**主建设**

- R06：三类核心 Evidence Schema 与跨步骤 lineage；Answer Binding 留在 Trace；
- R22：Fixed / Dynamic Population 和 denominator；
- R14：多 Finding Answer Context、Guard 规则覆盖和确定性回退硬化；
- R21：多指标 BGP 控制面变化向量；
- R11：ResultSet manifest / partition 合同和真实容量测量。

**必须集成回归**

- R02、R18、R19、R20：产品范围、观察者范围、路径和时间语义；
- R07：Artifact / Finding、Renderer / Guard Trace 同候选；
- R08：Renderer / Sink DLP；
- R13：Adapter 不偷做语义变换；
- R15：Pi 只能根据 Answer Context 组织语言。

**用户能看到什么**

回答可以组合多个 Finding，并说明范围、人口、单位、时间、证据和限制；系统不能把“未观测到”写成“不存在”，也不能把单 RRC25 结果写成全国事实。

**DG2 退出条件**

- 对外关键数字、单位、对象和时间全部可追到 Typed Finding；
- Renderer 只能读取 Answer Context，不执行新业务计算或增加 Context 外事实；
- Answer Context 包含必要范围、人口、分母、完整性和 limitation；
- 失败 Trace 不进入 Domain Evidence；
- Response Guard 能阻断领域语义放大；`block` 后不再调用模型，并使用同一 Answer
  Context 做确定性回退；
- 不要求 Claim ID、通用 predicate 词表、Claim 状态机或普通回答 Publisher。

### M3 — Durable Investigation Job（条件阶段）

**进入条件**

只有 `DG-DUR=GO` 才进入。触发证据必须来自真实任务，证明 Interactive Action 无法满足以下至少一项：跨请求等待、进程恢复、取消、自动重试、受控 fan-out、跨会话状态或多次正式提交。

**主建设**

- R04：选择成熟 Durable Workflow，通过 Port 接入；
- R12：冻结人口上的 Bounded Map；
- R17：队列、重试、恢复、历史和存储 SLO。

**跨层要求**

- Action 以 `upgrade_required` 结束，新 Job 独立准入；
- R03 / R07 / R08：Job、child 和恢复后的每次正式操作仍绑定 Policy、Candidate 和 Permission；
- R06 / R22：Workflow 只保存 Artifact 引用和冻结人口；
- R14：进行中、部分和最终 Finding / limitation 分开，回答 Trace 不把中间结果冒充最终结果。

**用户能看到什么**

真正需要长时间或跨会话的调查可以暂停、恢复、取消，并且局部失败不会静默扩大范围或重复发布结果。

**DG3 退出条件**

- worker 崩溃后可恢复；
- cancel / commit 竞争正确；
- retry 幂等；
- 权限撤销后不能继续；
- 部分失败和最终结论被明确区分；
- 如果 `DG-DUR` 未通过，本阶段正式记录 Deferred，不创建占位实现。

### M4 — 无固定 DAG 的动态能力组合

**这个阶段解决什么**

证明新 Agent 不只是把第一条路径跑通，而是可以处理未见表达和多能力组合，同时不建立 Question Template 或 Capability Family 路由表。

**主建设与扩展**

- R05：held-out 表达、多轮、失败和模型漂移矩阵；
- R16：未见能力组合和多条有效轨迹；
- R17：按 Capability、模型和任务类型观察成本与成功率；
- R21：验证多指标排序的产品价值；
- R11：根据真实 rows / bytes 优化分区和聚合；
- R15：如需 Teacher，做预注册同候选 A/B。

**条件决策**

真实负载证明 Operator 膨胀或搬运成本后，才运行 `DG-COMP`。只有 Gate 为 GO 才实施 R10；否则明确 Deferred。

**用户能看到什么**

用户换一种没见过的说法，或者提出需要两三种能力共同完成的任务，不需要开发者再新增专用问题模板。

**DG4 退出条件**

- held-out 表达通过真实轨迹评测；
- 未见能力组合不新增模板或固定 DAG；
- 每个 Proposal 仍独立准入；
- 同一目标允许多条合规成功轨迹；
- Capability Family 覆盖率与 Released 状态分开。

### M5 — 窄范围生产晋级

**这个阶段解决什么**

把一个已经在同一候选上通过功能、证据、安全和 SLO 的窄范围能力交给真实用户，而不是一次性宣称整个七层架构完成。

**主建设与验收**

- R01：Production Promotion；
- R02：最终产品声明、当前能力、数据和回答范围；
- R07：构建、依赖、镜像、模型、Prompt、Registry 和数据身份；
- R08：跨租户、注入、撤权、Renderer / DLP 红队；
- R17：生产 SLO、错误预算、容量、保留和回滚；
- R05：当前候选真实用户任务认证。

其他 R 项全部做同候选回归，不能因为“本阶段不主建设”而空白。

**用户能看到什么**

一个范围明确、证据可追溯、可以停用和回滚的真实 Agent 能力，而不是“所有国家中断调查能力已经完成”。

**DG5 退出条件**

- canary、SLO、安全、成本、停用和回滚均通过；
- 当前部署身份与 `main` 候选一致；
- Verified 和 Released 有独立证据；
- 产品声明不超过真实数据和评测覆盖。

---

## 5. 每个 R 项的阶段定位

说明：`主`表示该阶段主要实现；`定`表示只冻结合同或边界；`验`表示必须做集成回归；`条件`表示 Gate 通过后才实施；`—`表示本阶段没有新增工作，但仍受最终回归约束。

| R 项 | M0 | M1 | M2 | M3 | M4 | M5 |
|---|---|---|---|---|---|---|
| R01 | 定：三层认证 | 主：首个切片 | 验：多 Finding 回答 | 验：Job 轨迹 | 主：组合认证 | 主：生产晋级 |
| R02 | 主：产品边界 | 验 | 验 | 验 | 验 | 主：发布声明 |
| R03 | 主：Policy URI | 验 | 验 | 验 | 验 | 验 |
| R04 | 主：冻结扩张 | 验：Action 边界 | 验 | 条件：Durable Job | 验 | 验 |
| R05 | 定：Harness | 主：真实轨迹与回答 | 验：多 Finding / 漂移 | 验：恢复 | 主：组合/漂移 | 主：发布认证 |
| R06 | 定：三语义/Trace 边界 | 主：最小分离 | 主：lineage 硬化 | 主/验：Job lineage | 主：跨步骤 | 验 |
| R07 | 主：规范化/身份 | 主：Action / Answer Trace | 主：跨步骤候选链 | 主/验：Job | 验 | 主：供应链 |
| R08 | 定：PEP / IR | 主：逐 Proposal | 主：Renderer DLP | 主：跨会话 | 主：组合安全 | 主：红队 |
| R09 | 主：小合同 | 主：Interactive Action | 验 | 条件：Job Spec | 验：动态组合 | 验 |
| R10 | 冻结扩张 | 测量 | 测量 | 测量 | 条件：DG-COMP | 验 |
| R11 | 定：方向 | 测量 | 主：manifest | 验：Job 引用 | 主：分区/聚合 | 主：容量 |
| R12 | — | — | 定：人口合同 | 条件：DG-DUR | 验 | 验 |
| R13 | 定：分类 | 主：首 Adapter | 主：Finding 输入 | 主：Job Adapter | 验 | 验 |
| R14 | 定：最小不变量 | 主：Finding-to-Answer | 主：多 Finding / 回退硬化 | 主/验：部分结果 | 主：复杂组合 | 主：渠道边界 |
| R15 | 主：退出默认 | 主：单 Pi / 骨架 | 验：Answer Context | 验 | 条件：A/B | 验 |
| R16 | 主：退出规则 | 主：替换路由 | 验 | 验 | 主：未见组合 | 验 |
| R17 | 定：指标 | 主：Trace/预算 | 主：Artifact 成本 | 主：Job SLO | 主：容量模型 | 主：生产 SLO |
| R18 | 主：四完整性 | 主：Tool / Finding scope | 主：跨步骤传播 | 验 | 验 | 验 |
| R19 | 主：术语/Profile | 主：Operator / Guard | 验：组合路径 | 验 | 验 | 验 |
| R20 | 定：区间合同 | 主：Operator / Finding / Guard | 主：时间组合 | 验：恢复时间 | 验 | 验 |
| R21 | 定：指标边界 | 测量 | 主：向量 | 验 | 主：产品验证 | 验 |
| R22 | 定：人口策略 | 测量 | 主：双人口/分母 | 主：冻结人口 | 主：组合分析 | 验 |

---

## 6. 架构层与整改项的主归属

这个表用来判断“某一层为什么还不能算完成”，不是用来把项目按层串行开发。

| 架构区域 | 主整改项 | 完成含义 |
|---|---|---|
| L1 Agent Cognitive | R15、R16；R05 横切验证 | Pi 能逐步 Proposal、观察后 replan；不走模板路由，不把猜测变成 Finding |
| L2 Domeye Trust Kernel | R03；R07、R08 横切 | Policy、身份、预算、Registry、撤权和正式状态有唯一权威 |
| L3 Action / Job / Workflow | R04、R09、R12 | Action 与 Job 按耐久性区分；没有 Common Plan；Workflow 不做认知规划 |
| L4 Capability Execution | R13；R08 横切 | 已准入 Capability 被解析到登记实现；Adapter 不偷做语义 |
| L5 Deterministic Compute | R10、R19、R20、R21 | Tool / Operator 语义确定、可重放、不过度解释；Compute 平台按证据建设 |
| L6 Artifact / Evidence | R06、R11、R22；R18 横切 | ResultSet、人口、完整性、三类核心证据关系和 lineage 可验证；回答绑定留在 Trace |
| L7 Answer Composition & Guard | R14；R02、R18–R22 约束 | Renderer 只消费 Answer Context；Guard 阻断可测试的表达漂移并安全回退 |
| X1 Eval / Release | R01、R05 | 真实任务、同候选、多 trial 和环境结果形成发布证据 |
| X2 Security / Supply Chain | R07、R08 | 逐 Proposal 权限、数据流和候选来源闭合 |
| X3 Observability / SLO | R17 | 延迟、成本、容量、预算和停止原因能反向驱动架构决策 |
| X4 Product / Data Boundary | R02、R18；R19–R22 提供领域约束 | 产品声明不超过数据、观察者、人口和指标边界 |

---

## 7. 关键依赖链

### 7.1 信任与回答链

```text
R03 Policy语义
  → R07 候选与Receipt身份
  → R08 当前Proposal准入与数据流
  → R06 Evidence逻辑
  → R14 Finding-to-Answer边界
```

### 7.2 Agent 主链

```text
R09 小合同和滚动执行
  + R16 模板退出生产路由
  + R05 真实Agent评测
  + R13 Typed Adapter
  + R17 Trace/预算
  → DG1 Interactive Agent Loop
```

### 7.3 BGP 语义链

```text
R02 产品边界
  → R18 Observer/Completeness
  → R19 路径语义
  → R20 时间区间
  → R22 人口与分母
  → R21 多指标严重度
  → R14 有边界回答
```

### 7.4 条件基础设施链

```text
真实恢复/fan-out需求 + R17运行数据
  → DG-DUR
  → R04 Durable Workflow
  → R12 Bounded Map

真实Operator膨胀/搬运成本 + R11/R17运行数据
  → DG-COMP
  → R10 Restricted Compute Engine
```

---

## 8. 哪些内容现在可以进入开发，哪些必须等待

### 可以立即进入 M0 / M1 设计或实现

R03、R02、R01、R09、R16、R04 的冻结动作、R07、R18、R19、R05、R08、R13、R14、R17、R15、R20。

其中“进入开发”不表示每项立刻拆大量代码；M0 项优先交付最小合同、旧路径处置和可执行验收条件。

### 应在 DG1 后作为 M2 主建设

R06、R22、R21、R11，以及 R14 的多 Finding / 跨步骤硬化。

它们的 Schema 和边界可在 M0 先定义，但不应阻塞首个真实 Interactive Agent 闭环。

### 暂不创建无条件实现任务

- R12：等待 `DG-DUR`；
- R10：等待 `DG-COMP`。

可以创建“决策门 / 测量”任务，但不能创建已经承诺建设平台的实现任务。

---

## 9. 阶段和 R 项的状态规则

- 一个 R 项跨多个阶段时，只完成前段不能关闭整个 R 项。
- Milestone 关闭要求本阶段所有“主建设”有 Accepted Evidence，所有“集成回归”有同候选证据。
- Gate 只返回 GO、REPAIR 或 STOP / DEFERRED，不能用 Issue 关闭数代替。
- `Implemented` 表示代码存在；`Verified` 表示同一候选证据被接受；`Released` 还需要真实发布和回滚证据。
- R10、R12 的 Deferred 是正确工程结果，不是项目失败。
- 任何新工作若重新引入 `question_id → template/family → fixed DAG`、完整 Common Plan 或 Pi 自授权，必须回到 M0 重新审查。

---

## 10. 本计划的第一条实际开发主线

22 项不是 22 个彼此独立、同时开工的功能。第一条代码主线应集中服务一个真实纵向切片：

```text
Pi 更新 Goal State
  → 提出一项 Capability Proposal
  → Trust Kernel 按当前身份、范围、预算和 Registry 准入
  → Resolver 绑定已登记执行单元
  → Typed Adapter
  → Tool / Domain Operator
  → Artifact + Receipt
  → Pi Observation
  → Replan / Clarify / Finish
  → Typed Finding / Answer Context
  → Renderer / Response Guard
  → 用户回答；`block` 时丢弃草稿并使用同一 Context 的确定性回退
```

这条主线首先关闭 R09、R16 的生产路径问题，并为 R05、R08、R13、R14、R17 提供真实验收对象。R06、R22 等 M2 工作随后把首个有边界回答扩展到多 Finding、跨步骤证据和真实数据规模。
