# Domeye 敌对评审 22 项整改审计对应表 v1.1

> 形态一：用于审计和防遗漏。本文始终按敌对评审原始顺序 R01–R22 编排，不按开发先后重排。

| 项目 | 内容 |
|---|---|
| 原始评审 | `敌对式架构评审结论(1).docx` |
| 目标架构 | [Domeye Agent 目标架构 v1.1](../architecture/Domeye_Agent_Target_Architecture_v1.1.md) |
| 审计起始代码基线（历史） | [main@6a4bbd41aa712c12080a0126e5f8b1ec1440a9ca](../architecture/Domeye_Current_Code_Baseline_2026-08-16.md) |
| 开发执行版 | [22 项整改开发优先级与阶段计划 v1.1](../roadmap/Domeye_22_Items_Development_Priority_and_Stages_v1.1.md) |
| 文档版本 | v1.1 |
| 日期 | 2026-08-16 |
| 用途 | 证明原始 22 条评审意见均有唯一整改项、代码落点和验收要求 |
| 不代表 | 这些整改已经实现、验证或发布 |

---

## 1. 本文与“开发执行版”的区别

22 点需要保留两种形态：

1. **审计对应版，也就是本文。**按原始评审的 1–22 顺序保存，一条原评审只对应一个 R 编号。它用来回答“有没有遗漏、偷换或弱化原问题”。
2. **开发执行版。**按依赖和风险重新排序，标出优先级、阶段、Gate 和何时可以开工。它用来回答“先做什么、做到什么程度才能进入下一阶段”。

两份文档共用 R01–R22，不重新编号。开发顺序可以改变，审计编号不能改变。

---

## 2. 一一对应规则

- 原评审第 1 条永远对应 R01，第 22 条永远对应 R22。
- 不把两个原问题合成一个整改项，也不把一个问题拆成多个新的 R 编号。
- 可以把一个 R 项分多个阶段交付，但所有阶段仍归属于同一 R 编号。
- 原评审中的事实如果被 `main` 代码审计修正，要明确写成“基线校正”，不能静默改写原评审。
- 新目标架构对原评审建议做了进一步修正，也要明确写成“v1.1 架构修正”。
- R 项关闭必须引用同一候选的代码和验收证据；设计文档只能证明 Designed。

### 2.1 四处必须显式保留的架构修正

原评审的方向总体正确，但以下四处已经被新架构进一步修正：

- **R02：**产品主名称保持为 **Domeye 国家网络中断调查 Agent**；当前 RRC25 只限制首个交付切片和事实边界，不能反向把产品定义成静态事件视图或单数据源查询器。
- **R09：**不是把 Common Plan IR 拆成另一个公共 Execution Graph IR，而是开放任务采用逐步 `Proposal → Admission → Execution → Observation → Replan`；完整 DAG 只能是已准入 Durable Job 的内部实现。
- **R14：**原评审要求解决“Graph 引用不等于陈述受支持”，但新架构不据此建设通用 Claim 状态机和 Publisher；采用 Typed Finding、Answer Context、Response Guard 与确定性回退。
- **R16：**不是“问题 → Capability Family → 组合规则”。Capability Family 只组织合同、文档和评测覆盖；生产运行由 Pi 从过滤后的 Capability View 提出下一项 Capability Proposal。

---

## 3. 原始评审与整改编号总表

| 原编号 | 原始评审标题 | 整改编号 | v1.1 整改标题 | 原评审分组 |
|---:|---|---|---|---|
| 1 | W6 是“正确拒绝认证”，不是“能力认证” | R01 | 建立合同回归、Agent 任务认证和生产晋级三层认证 | P0 |
| 2 | 产品名叫“国家中断 Agent”，但最核心的产品命题被系统自己禁止回答 | R02 | 分开目标 Agent、当前 RRC25 切片和当前事实边界 | P0 |
| 3 | Gate ID 语义漂移是直接的安全与审计事故，不是命名瑕疵 | R03 | 用不可复用的 Policy URI 和版本取代裸 Gate 编号 | P0 |
| 4 | 你们实际上正在自研一个低配 Temporal | R04 | 冻结通用调度扩张，真实需要时接入成熟 Durable Workflow | P0 |
| 5 | 当前评测验证的是 fixture 分类器，不是 Agent | R05 | 评测真实滚动 Agent 轨迹和最终环境结果 | P0 |
| 6 | 当前 EvidenceGraph 混合了四种不同的图 | R06 | 拆分三类核心证据语义，并把回答绑定留在 Trace | P0 |
| 7 | “有 digest”不等于“可信” | R07 | 建立规范化、签名和供应链来源链 | P0 |
| 8 | 安全模型仍然偏“静态白名单”，没有覆盖动作序列和数据流 | R08 | 建立逐 Proposal 准入、状态转换和全链数据流控制 | P0 |
| 9 | “统一 Common Plan IR”很可能成为新的架构黑洞 | R09 | 拒绝超级 Plan，采用小合同和滚动执行 | 架构 |
| 10 | 34 个 Operator 中有大量内容是在手工重造关系代数 | R10 | 领域 Operator 保留；通用计算仅在证据触发后下沉 | 架构 |
| 11 | “Tool 全量读取 → ResultSet 冻结 → Operator 计算”会导致严重的数据搬运和物化成本 | R11 | 支持分区 ResultSet、manifest 和可验证聚合 | 架构 |
| 12 | 动态 fan-out 被设计成多 revision Plan，理论上安全，实际上可能重得无法使用 | R12 | Durable Job 内使用受控 Bounded Map | 架构 |
| 13 | Structural Adapter 与 Operator 的边界仍然太主观 | R13 | Adapter 类型化、注册化并证明 lossless | 架构 |
| 14 | Answer Gate 的“事实映射到 Graph 节点”仍不足以证明答案被支持 | R14 | 以 Typed Finding、受限 Answer Context 和 Response Guard 防止自然语言放大 | 架构 |
| 15 | Teacher → Student 链目前很像没有实证依据的复杂化 | R15 | 默认退役双模型链，先做同候选 A/B | 架构 |
| 16 | 28 个 Question Template 很容易变成高级版意图枚举 | R16 | Question Template 退出生产路由，Capability Family 只作建模与评测 | 架构 |
| 17 | 没有看到生产级成本、容量和 SLO 模型 | R17 | 从首个纵向切片开始记录预算、成本、尾延迟和容量 | 架构 |
| 18 | 单 RRC25 的“完整 ResultSet”容易被误解为完整网络事实 | R18 | 把查询、来源、观察者和世界完整性分开 | BGP |
| 19 | AS_PATH 中相邻 ASN 不等于物理直连、流量路径或商业邻接 | R19 | 只发布“观测 AS 序列相邻” | BGP |
| 20 | “首次状态时刻”本质上通常是区间删失，不是精确事件时刻 | R20 | 使用观测槽位、上下界和删失类型 | BGP |
| 21 | 仅按前缀数量或 ASN 数量排名，可能严重误导“严重程度” | R21 | 使用多指标 BGP 控制面变化向量 | BGP |
| 22 | Fixed cohort 会引入选择偏差和幸存者偏差 | R22 | Fixed 与 Dynamic Population 并行建模 | BGP |

完整性检查：原始 22 条全部出现一次；无缺号、无重号、无合并号。

---

# 4. 八个 P0 级原始问题

## R01 — W6 是“正确拒绝认证”，不是“能力认证”

**原评审核心结论**

W6 的 168 个 case 证明 disposition 与冻结预期一致，不证明 28 个问题能被正确回答。原始事实是 `executed_supported=0`、外部模型调用为 0、生产部署为 false。

**v1.1 整改决定**

建立三层认证，名称和证据不能混用：

1. Deterministic Contract Conformance：合同、阻断、延期和确定性函数回归。
2. Agent Task Certification：真实 Pi、真实模型、真实 Gateway、真实 Tool / Operator 和 Finding-to-Answer 链完成任务。
3. Production Promotion：同一候选通过安全、SLO、canary、停用和回滚门。

首批 Agent 认证覆盖五类纵向切片：单事实、时序或状态变化、ASN 排名、集合或路径比较、时间一致性。首轮每类不少于 30 次真实 trial；样本量和阈值预注册，后续可按风险和方差调整。

**当前 `main` 基线**

- 历史 W6 Evidence 记录过 162 个正确阻断、6 个正确延期、实际支持执行数为 0；其 Runtime 和评测目录已退出当前树。
- 历史 P1 有真实模型评测，但源码和可变模型 alias 已退役，不是当前不可变 Candidate。

**架构归属与阶段**

- 主归属：X1 Eval / Release；关联 L1–L7。
- M0 修正认证名称和 Gate；M1 完成首个真实纵向切片；M4 扩展多能力认证；M5 做生产晋级。

**验收证据**

- 轨迹记录 Goal、Proposal、Admission、Action / Job、Observation、Artifact / Finding、Answer Context、Guard Result 和 Outcome。
- 分别报告 Pass@1 和连续稳定性 Pass³；不能用 Pass@k 掩盖不稳定。
- 生产晋级引用同一 candidate、model、Prompt、Registry、Policy、数据和 eval receipt。

**不能宣称**

fixture 168/168、Issue 关闭或代码合并，均不能单独证明 Agent 能力完成。

## R02 — 产品名与证据能力冲突

**原评审核心结论**

用户看到“国家中断 Agent”会自然期待判断是否中断、影响、原因和恢复；当前单 RRC25 数据只能支持受限 BGP 控制面观测事实。

**v1.1 整改决定**

目标产品保持为“Domeye 国家网络中断调查 Agent”；当前交付切片明确为“基于 RRC25 BGP 控制面数据的第一个 Agent 调查闭环”。产品形态与当前能力范围分开：Agent 可以滚动调查，但当前事实性回答只使用 RRC25 观察和登记 Operator 的确定性派生结果。

未来只有正式引入多 collector / VP、主动探测、流量、DNS、HTTP / TLS、peer 健康和外部事件证据，并建立 Outage Evidence Contract 后，才重新评估更强的事实性陈述。

**当前 `main` 基线**

`config/data-profile.json` 和现有数据链仍以单 RRC25 publication 为主要边界；代码和历史文档中同时存在更强的“国家中断”名称。

**架构归属与阶段**

- 主归属：X4 Product / Data Boundary；关联 L6、L7。
- M0 必须定边界；M5 发布前再次确认目标产品、当前能力范围和允许措辞一致。

**验收证据**

- UI、Prompt、Capability 描述、Artifact / Finding 和 Answer Context 使用同一 `observer_scope`。
- 单信号下 nationwide outage、真实用户影响、原因、责任和恢复等越界陈述被 Guard 或确定性回退阻断。
- 产品边界测试覆盖成功回答和越界拒绝两条用户旅程。

**不能宣称**

页面加免责声明不能替代 Finding 类型、Answer Context 和 Response Guard 的硬范围检查。

## R03 — Gate ID 语义漂移

**原评审核心结论**

相同的 `GATE-01`、`GATE-05` 在不同文件中表示不同控制语义，使历史 Receipt 无法唯一解释。这是安全与审计问题，不是命名问题。

**v1.1 整改决定**

- 使用不可复用的语义 URI 和版本，例如 `domeye://policy/identity-equality/v1`。
- Receipt 绑定 `policy_id`、版本、合同摘要、实现摘要和 subject 摘要。
- 裸 Gate 编号只保留为 deprecated alias。
- 核心 Policy 是 Trust Kernel 不变量，不再作为 Agent 或 Workflow 可以选择的 DAG 节点。

**当前 `main` 基线**

旧 P1 / P2 Runtime、Dispatcher 和组合合同已从当前树退役。旧 Gate 编号语义只保留在 Git 历史和历史 Evidence 中，不再作为当前实现依据；当前 Candidate 尚未完成语义 URI 迁移的通用化工作。

**架构归属与阶段**

- 主归属：L2 Domeye Trust Kernel；关联 L7。
- M0 阻断项，后续所有 Receipt 和 Gate 都依赖它。

**验收证据**

- 同一 Policy URI 在全仓只有一种合同语义。
- TypeScript / Python 通过同一组 golden vectors。
- 历史 Receipt 有完整映射；不能唯一解释的 Receipt 不用于生产晋级。
- Action、Job 和 Workflow 均不能跳过或重命名 Policy。

**不能宣称**

摘要字节一致不能修复 Gate 语义错误。

## R04 — 自研低配 Temporal 风险

**原评审核心结论**

原设计把 DAG、状态机、CAS、幂等、取消、重跑、恢复、revision、staging 和导出都放进领域 Runtime，正在重复建设通用耐久工作流职责。

**代码基线校正**

旧 P2 的 CAS、取消、rerun、幂等指针、失败终态和隔离 Store 原型已退出当前树。当前 Candidate 没有 Durable Job、正式 worker、跨进程等待、自动恢复或自动重试主路径；相关经验只能从 Git 历史重新审查后提取。

**v1.1 整改决定**

- M0 立即冻结通用 scheduler、timer、retry 和 recovery 的继续扩张。
- 普通 Tool / Operator 由 Interactive Action 直接执行，不进入 Workflow。
- 只有真实任务需要恢复、取消、重试、异步等待、fan-out、跨会话或多次正式提交时，才触发 `DG-DUR`。
- Action 超界时以 `upgrade_required` 结束；随后创建新的 Job Proposal，由 Trust Kernel 独立准入并签发新 `job_id`。
- `DG-DUR` 通过后比较 Temporal、Restate、DBOS 或等价方案；Workflow 只执行已准入工作。

**架构归属与阶段**

- 主归属：L3 Action、Job 与 Workflow；关联 L2。
- M0 冻结错误扩张；M3 只有在 `DG-DUR=GO` 后才实施。

**验收证据**

- Gate 未触发时有正式 Deferred 记录，且没有新增通用调度代码。
- Gate 触发后验证 worker 崩溃、幂等重试、cancel / commit 竞争、撤权和版本升级。
- Pi Session 丢失不导致 Domain Job State 丢失。

**不能宣称**

类名含 Investigation、存在 CAS 或 fixture 重放，不等于 Durable Job 已完成。

## R05 — fixture 分类器不等于 Agent 评测

**原评审核心结论**

脚本化 SemanticPlan、冻结 recipe、Teacher / Student 回放和零外部 provider，不能证明模型能理解真实表达、根据 Tool 结果重规划并完成任务。

**v1.1 整改决定**

建立独立 Agent Eval Harness。每个任务定义初始状态、可用 Capability、真实数据状态、成功条件、grader 和允许副作用；同时评价轨迹和最终环境结果。

覆盖 held-out 表达、多轮指代、信息不足、冲突输入、Tool 失败、分页攻击、BGP 特例、prompt injection、模型漂移；只有 Durable Job 存在后才加入真实恢复、取消和 Action→Job 交接评测。

**当前 `main` 基线**

旧 P1 UserGoalPlan、GroundingPlan / DAG 和对应 fixture 已退出当前树。当前首个纵向切片已形成逐项 propose / admit / observe 闭环，但尚未证明超出固定问题的开放式 replan 泛化。

**架构归属与阶段**

- 主归属：X1 Eval / Release；关联 L1–L7。
- M1 建最小 Harness；M4 扩展未见组合和模型矩阵；M5 进入发布门。

**验收证据**

- 真实 Pi、模型、Gateway 和 Tool 参与。
- 评分别覆盖 Goal、Proposal、Admission、Observation 后 replan、Artifact / Finding、Answer Context、Guard Result 和 Outcome。
- fixture 只作 deterministic regression，不能抵消真实 Agent 失败。

## R06 — EvidenceGraph 混合四种图语义

**原评审核心结论**

执行失败、数据血缘、领域事实和回答支持关系放在一张 Graph 中，导致调试关系和事实关系互相污染。

**v1.1 整改决定**

逻辑拆分三类核心证据语义：

1. Execution Provenance；
2. Data Lineage；
3. Domain Evidence；

Answer Context 对 Finding 的选择、Renderer 版本和 Guard 结果进入回答 Trace，不建设第四套 Claim / Finding Support Graph。三类核心 Schema 可以共用物理存储，但节点、边和查询含义必须分开。

**当前 `main` 基线**

旧 Python P2 EvidenceGraph 已移除。当前趋势产品仍有用途受限的 EvidenceGraph 产物，但它不能证明 Execution Provenance、Data Lineage 和 Domain Evidence 三类通用语义已经拆分完成。

**架构归属与阶段**

- 主归属：L6 Artifact / Evidence；关联 L7。
- M1 首个切片分开最小三类语义并保存回答 Trace；M2–M4 扩展跨步骤 lineage。

**验收证据**

- Tool timeout 只进入 Execution Provenance，不成为 Domain Fact。
- Derived Fact 可以追到输入 Artifact。
- Derived Finding 可以追到 Domain Evidence、限制和冲突证据。
- Answer Context / Guard Trace 和 Domain Evidence 可以独立查询。

## R07 — digest 不等于可信

**原评审核心结论**

SHA-256 只能说明内容是否变化，不能说明由谁生成、构建来源、是否获授权、当前是否适用，也不能自动保证 TypeScript / Python 规范化一致。

**v1.1 整改决定**

- 统一 RFC 8785 / JCS 或经 ADR 选择的等价规范化合同。
- 用跨语言 golden vectors 固定字节和摘要。
- Receipt 区分 integrity、authenticity、authorization 和 applicability。
- 候选绑定 source commit、builder、依赖锁、runtime image、model、Prompt 和 Tool Schema 身份。
- 关键 Receipt 使用可验证签名或受信服务身份。

**当前 `main` 基线**

旧 TypeScript Registry Runtime 与 Python P2 服务的平行 canonicalization / digest 实现已退役。当前 Candidate 有固定摘要、签名角色和依赖审计，但尚未形成覆盖全仓组件的统一签名构建 provenance 闭环。

**架构归属与阶段**

- 主归属：X2 Security / Supply Chain；关联 L2、L6。
- M0 固定规范化和候选字段；M1–M2 接入 Action、Artifact 和回答 Trace；M5 完成发布供应链证据。

**验收证据**

- 同一 JSON 在 TS / Python 得到相同规范化字节和摘要。
- 构建产物能追到 `main` commit、lock、image 和 builder。
- Registry Snapshot、Action / Job、Artifact / Finding、Renderer / Guard Trace 绑定同一候选链。

## R08 — 静态白名单不足以控制动作序列和数据流

**原评审核心结论**

单个 Tool 合法不代表组合合法；Tool 输出可能携带间接提示注入；静态 allowlist、Pi guardrail 或 `beforeToolCall` 不能构成最终授权证据。

**v1.1 整改决定**

Pi Tool Call 只是一项 Capability Proposal。Trust Kernel 对当前 Proposal 逐项准入，Permission IR 至少绑定 principal、tenant、Capability、resource / publication、数据标签、预算、有效期、撤销状态、已发生动作历史、状态转换约束和数据流规则。

Permission IR **不得包含或预先批准未来 Action 序列**。安全系统根据已经发生的动作判断当前 Proposal 是否允许，而不是先签一条固定未来路径。

输入、Tool 输出、Artifact 组合和 Renderer / Sink 都执行 taint / DLP；Tool 输出默认是 data，没有 instruction authority。

**当前 `main` 基线**

已有 loopback、Bearer、safe HTTP、Registry digest 和多类 fail-closed 检查；尚无统一 Permission IR、跨租户测试和 Renderer 全链 DLP。

**架构归属与阶段**

- 主归属：X2 Security / Supply Chain；关联 L2、L4、L7。
- M1 建逐 Proposal 准入；M2 补 Renderer / DLP；M3 验证跨会话 Job；M4 验证多能力组合；M5 红队发布。

**验收证据**

- 预检通过但 Trust Kernel 拒绝时不执行。
- 权限撤销、跨租户 Artifact、参数走私和历史状态污染硬拒绝。
- Renderer 不能发布未获准数据。

---

# 5. 架构层面的九个原始问题

## R09 — Common Plan IR 架构黑洞

**原评审核心结论**

一个同时表达 P1、P2、fan-out、replan、Evidence、CAS 和 Answer Gate 的超级 IR 会成为第三套、更复杂的系统。

**v1.1 架构修正**

原评审建议拆 Goal、Execution Graph 和 Compute Plan；新目标进一步取消开放任务入口的公共完整 Execution Graph：

```text
Semantic Goal
→ Capability Proposal
→ Trust Kernel Admission
→ Interactive Action
→ Artifact / Receipt
→ Pi Observation
→ Replan / Clarify / Finish
```

系统只统一 Semantic Goal、Capability Proposal、Admission Decision、Action / Job Spec、Artifact Envelope、Receipt、Typed Finding、Answer Context 和轻量 Guard Result 等小合同。

Action 需要耐久性时，以 `upgrade_required` 结束并创建新 Job Proposal；Job 独立准入。Job Spec 不包含 handler、Tool ID 或未来完整领域 DAG。

**当前 `main` 基线**

旧 P1 GroundingPlan 和 P2 InvestigationPlan / 静态 DAG Runtime 已从当前树退役。当前首个纵向切片只允许 Pi 提出一个下一步 Capability Proposal，并由 Trust Kernel 独立准入。

**架构归属与阶段**

- 主归属：L3 Action、Job 与 Workflow；关联 L1、L2。
- M0 冻结小合同；M1 交付 Interactive Action；M3 只有 `DG-DUR=GO` 才交付 Job。

**验收证据**

- P1 生产 Trace 不再出现 `question / normalized_kind → full DAG → admitPlan → execute all`。
- Pi 一次只提出一个 Proposal，批准后才形成 Action。
- Planning State 不具有执行授权或 Evidence 身份。

## R10 — Operator Catalog 重造关系代数

**原评审核心结论**

count、intersection、difference、join、rank、Jaccard 等通用运算每项都做独立 Operator，会造成 Registry、Schema、Oracle 和维护成本膨胀。

**v1.1 整改决定**

领域语义 Operator 继续独立登记。只有真实调用频率、数据量、维护成本和性能证明通用运算值得下沉时，才通过 `DG-COMP` 决定是否建设受限 Compute Engine。

Compute Contract 只能由 L4 领域编译器在准入后生成；模型不可见，不承载对话规划、Workflow State、Evidence 或回答发布，禁止任意 SQL。

**当前 `main` 基线**

OP-05..33、OP-35..39 只存在于 Git 历史，不是当前可执行能力。当前切片只保留经 Candidate 固定的最小确定性计算；真实负载和维护收益仍未证明需要新 Compute 平台。

**架构归属与阶段**

- 主归属：L5 Deterministic Compute；关联 L4。
- M0 冻结无证据的通用 Operator 扩张；M4 前后用运行数据触发 `DG-COMP`。Gate 不通过则保持 Deferred。

**验收证据**

- shadow run 证明新旧结果、顺序、单位和 lineage 等价。
- Operator 目录增长和运行成本有实测改善。
- 收益不足时不建设平台。

## R11 — 全量搬运与物化成本

**原评审核心结论**

答案基于可验证人口是正确原则，但不等于必须把所有成员搬到 Pi 或 Host 后再计算。

**v1.1 整改决定**

- ResultSet 支持 partition manifest、segment、row count、稳定 cursor 和 aggregate receipt。
- Pi 只读取 summary、preview 和 artifact reference。
- source-side compute 必须消费已登记的 Compute Function / Contract 并生成 query plan 摘要和 Receipt，不能把业务判断藏回 Tool。
- Workflow 历史只保存 Artifact 引用，不复制全集。

**当前 `main` 基线**

旧 Python ResultSet 已从当前树移除。当前没有通用、可执行的分页 ResultSet、partition manifest 或大人口容量证据；相关设计只能作为后续重新立项时的历史输入。

**架构归属与阶段**

- 主归属：L6 Artifact / Evidence；关联 L5、L3。
- M2 先定义 Artifact / manifest；M4 按真实 rows / bytes / cost 做分区和聚合优化；M5 做容量验证。

**验收证据**

- Top-N、count 等任务不把全人口送入 Pi。
- 分区闭合、重放、内存峰值和 aggregate receipt 可验证。
- query complete 与 observer / world completeness 正交。

## R12 — 多 revision Plan fan-out 过重

**原评审核心结论**

每次成员展开都生成新 Plan revision，会带来 Plan churn、重复 Admission、大历史和局部失败重编。

**v1.1 整改决定**

只有 `DG-DUR=GO` 后，才在 Durable Job 内引入 Bounded Map。输入必须是冻结 ResultSet；每个 child 有确定性 ID、单独预算、超时、Receipt、失败阈值和可定位结果。

`child_contract_ref` 是预先登记的 Job 内部执行合同，由 Registry Resolver 按已批准 Job 绑定。Workflow 不解析自然语言、不自行选择模板，也不能创造新领域动作。每个 child 的资源访问仍受 Trust Kernel 和 Domain Service 检查。

**当前 `main` 基线**

多 revision Plan 与 PLAN-CAP-02 只保留在 Git 历史中；当前没有正式 Durable Job / worker 主链。

**架构归属与阶段**

- 主归属：L3 Action、Job 与 Workflow；关联 L6。
- M3 条件项；`DG-DUR` 不通过就不创建实现任务。

**验收证据**

- 并发和预算可预测；局部失败只重跑相关 child。
- Trace 中不存在 `question_id → child_contract → fixed DAG` 路径。
- frozen population、child identity 和 reducer 结果均可重放。

## R13 — Structural Adapter 边界主观

**原评审核心结论**

时区、单位、null、路径规范化、去重、排序和成员身份选择都可能改变答案，不能靠“只是搬结构”的主观判断。

**v1.1 整改决定**

Adapter 分为 Structural Projection、Canonicalization、Type Coercion 和 Semantic Transformation。每个 Adapter 声明 source / target Schema、字段映射、null、单位、排序、基数、lossless / lossy 和 Evidence 传播。可能改变业务答案的 Semantic Transformation 必须升级为 Domain Operator。

**当前 `main` 基线**

旧 P1 Page Capability Executor 与 P2 structural context / binding receipt 原型已退出当前树。当前首个纵向切片已有用途受限的 typed read model 和 Trust Kernel 边界，但尚未形成通用 Adapter 合同。

**架构归属与阶段**

- 主归属：L4 Capability Execution；关联 L5。
- M1 首个纵向切片必须交付 typed adapter；M2–M3 随 Artifact / Job 接口硬化。

**验收证据**

- 每个 Adapter 有 golden vectors 和版本。
- lossy、many-to-one、defaulting 不能伪装成 Structural。
- 跨语言输出和 Receipt 摘要一致。

## R14 — Graph 引用不等于 Claim 被支持

**原评审核心结论**

一句话引用某个 Graph 节点，不代表节点蕴含该句。量词、否定、时间、单位、人口、范围和因果都可能被自然语言放大。

**v1.1 整改决定**

Tool / Operator 先产生带范围、单位、人口、时间和限制的 Typed Finding。无需派生计算的 Tool 可以直接产生 Observed Finding；Operator 产生 Derived Finding。回答顺序为：

```text
Artifact / Typed Finding
→ Answer Context
→ 单 Pi 或确定性 Renderer / DLP
→ Response Guard
→ 用户答案；`block` 时丢弃草稿并使用同一 Answer Context 的确定性回退
```

Answer Context 只选择本轮所需 Finding 和必要 limitation，不执行新业务计算。Renderer 不得增加 Finding 中不存在的事实。Response Guard 只检查可确定验证的字段一致性、必要限制、DLP 和领域禁用语义，不宣称证明任意自然语言蕴含。NLI 或反向事实抽取只作告警。

**当前 `main` 基线**

当前首个纵向切片已实现用途受限的 Typed Finding、最小 Answer Context、确定性 Renderer 和 Response Guard。旧 P1 / P2 staging 与最终 CAS 原型已退役；当前缺口是把这些边界扩展到多 Finding 和跨步骤回答，而不是增加独立 Publisher。

**架构归属与阶段**

- 主归属：L7 Answer Composition & Guard；关联 L5、L6、X4。
- M0 冻结最小不变量；M1 首个纵向切片完成最小链；M2 扩展多 Finding / 跨步骤回答；M5 硬化 DLP、回退和发布渠道。

**验收证据**

- 回答中的关键数字、单位、对象和时间可追到 Finding；比例 Finding 带 denominator，公共身份可由 Artifact Envelope 继承。
- “未观测到”不能写成“不存在”，“首次观测”不能写成“实际发生”。
- Renderer 只能读取 Answer Context；Guard 返回 `block` 后不得再次调用模型，只能使用
  同一 Answer Context 生成确定性、局部或明确未知的安全回退。
- 不要求每句话生成 Claim ID，也不建设 Claim 状态机、predicate 词表或普通回答 Publisher。

## R15 — Teacher → Student 缺少收益实证

**原评审核心结论**

双模型链增加费用、延迟和相关错误；Teacher 不是真值，当前也没有真实 provider A/B 证明净收益。

**v1.1 整改决定**

默认路径为：

```text
Typed Finding
→ Answer Context
→ 确定性骨架 / 单 Pi 叙事
→ Response Guard
```

Teacher 如果保留，只能产生 advisory outline，不产生事实、授权或发布决定。是否恢复双模型链由预注册 A/B 决定。

**当前 `main` 基线**

旧 P2 Teacher → Student Plan 与 fixture 已退出当前树。当前首个纵向切片使用单一 Renderer 和确定性回退；这不构成 Teacher 路径的收益证据，也不授权重新引入该链。

**架构归属与阶段**

- 主归属：L1 Agent Cognitive；关联 L7、X1。
- M1 从默认生产 Schema 退出并接入 Finding-to-Answer；M4 只有 A/B 明确获益才重新考虑。

**验收证据**

- 比较事实覆盖、边界违规、Pass³、费用和 P95。
- Teacher 无显著净收益时正式退役。
- 任何模型都不能在 Answer Context 之外增加业务事实。

## R16 — Question Template 退化为意图枚举

**原评审核心结论**

给 28 题逐题写模板，会变成 `LLM 分类 → 固定模板 → 固定流程`，不能证明组合泛化。

**v1.1 架构修正**

原评审曾建议“Question → Capability Family → Typed Composition Rules”。新目标进一步规定：

- 28 题和 paraphrase 只进入 Eval Coverage Map。
- Capability Family 只组织合同、版本、文档、产品边界和评测覆盖。
- Trust Kernel 根据身份、publication、数据和生命周期生成过滤后的 Capability View。
- Pi 从 View 中提出一个 Capability Proposal；Trust Kernel 批准后才形成 Action。
- 同一目标允许多条有效轨迹，不维护“能力族 + 固定组合规则”的生产路由表。
- 固定 Workflow 只能是 Job 内部已登记实现，由 Registry Resolver 在 Job 准入后绑定。

**当前 `main` 基线**

旧 P1 `normalized_kind → DAG` 与关键词选路实现已退出当前树。当前首个纵向切片不按问题模板绑定固定 DAG，但尚未用 held-out 表达和未见组合证明更广泛泛化。

**架构归属与阶段**

- 主归属：L1 Agent Cognitive；关联 L3、L4，横切 X1。
- M0 定义退出规则；M1 替换生产路由；M4 用 held-out 表达和未见组合认证。

**验收证据**

- 新增表达不修改路由代码或 Question Template。
- 组合任务不新增专用模板。
- 生产 Trace 不存在 `question_id → template_id / family_id → DAG`。
- Capability Family 覆盖率不自动等于能力 Released。

## R17 — 缺少成本、容量和 SLO

**原评审核心结论**

没有 P95、最大人口、Artifact 大小、存储、重试、队列、租户费用和历史增长数据，就无法判断当前设计是否可用，也无法判断何时需要 Workflow 或 Compute Engine。

**v1.1 整改决定**

从 M1 首个真实纵向切片开始记录 P50 / P95 / P99、模型和 Tool 调用、rows、bytes、tokens、费用、重试、排队、失败原因和新增 Artifact 字节。

Budget Policy 同时定义 Interactive Action 边界和 Job 交接阈值。达到 tool / row / byte / time / cost / fan-out 任一上限时，当前 Action 必须拒绝继续或以 `upgrade_required` 结束；Pi 不能自行突破预算。

**当前 `main` 基线**

已有 provider audit、usage / cost 字段、activity ledger、Receipt 和部分预算，但没有覆盖新循环的统一 Trace，也没有当前候选的生产 SLO。

**架构归属与阶段**

- 主归属：X3 Observability / SLO；关联 L1–L7。
- M1 建最小画像；M3 为 Job 提供队列和恢复指标；M4 形成容量决策；M5 形成发布 SLO。

**验收证据**

- Trace 覆盖 Goal → Proposal → Admission → Action → Artifact / Finding → Answer Context → Guard Result → Outcome。
- Trace 明确不是 Domain Evidence。
- `DG-DUR` 和 `DG-COMP` 都引用真实运行数据。

---

# 6. 五个 BGP 领域硬伤

## R18 — ResultSet 完整不等于网络事实完整

**原评审核心结论**

RRC25 publication 内的查询分页闭合，只能证明声明查询人口被枚举完，不能证明其他 collector、全球 BGP、数据平面或用户可达性一致。

**v1.1 整改决定**

至少拆分：

- `query_enumeration_complete`；
- `source_snapshot_complete`；
- `observer_scope_known`；
- `world_complete`，当前通常为 false 或 unknown。

Artifact Envelope / Finding 必须传播 collector / VP / peer set、peer health、source population 和 observation coverage，并由 Answer Context 选择进入本轮回答。

**当前 `main` 基线**

旧 P2 completeness 实现已移除。当前首个切片只表达自身冻结读模型所需的范围与限制，尚未用统一合同覆盖四个正交完整性维度。

**架构归属与阶段**

- 主归属：X4 Product / Data Boundary；关联 L6、L7。
- M0 冻结词义和 Schema；M1 Tool / Finding 输出携带 scope，并由 Answer Context 与 Guard 硬检查；M2 扩展跨步骤传播。

**验收证据**

- query complete 与 source incomplete 可以同时表达。
- 单 RRC25 回答不出现 global / universal 语义。
- collector / peer 异常传播为 limitation。

## R19 — AS_PATH 相邻语义过度解释

**原评审核心结论**

Route Server、AS_SET、confederation、prepending 等使 AS_PATH 相邻不能自动证明物理直连、商业关系或真实流量路径。

**v1.1 整改决定**

- 定义版本化 AS_PATH Canonicalization Profile。
- 输出关系命名为 `observed_sequence_adjacency` 或等价词。
- Finding 类型、Renderer 词义规则和 Response Guard 禁止自动转换成 direct link、customer / provider、traffic transit 或真实传播方向。

**当前 `main` 基线**

旧 OP-15..17、TOOL-12 与相关误读字段已退出当前树。当前 `path-downstreams` 等页面能力仍必须明确只是 RRC25 AS_PATH 观测关系，不能推出物理拓扑、商业关系或真实流量。

**架构归属与阶段**

- 主归属：L5 Deterministic Compute；关联 L7、X4。
- M0 定语义；M1 更新 Operator、Artifact / Finding 和 Guard；M2 扩展组合路径。

**验收证据**

- 覆盖 Route Server、AS_SET、confed、prepending、private ASN 和 poisoning fixture。
- UI 和答案只说“RRC25 观测路径表示中的相邻序列项”。

## R20 — 伪精确时间

**原评审核心结论**

离散 slot 只能证明状态变化位于两个有效观测之间；首次或末次观测槽位不是精确发生时刻。

**v1.1 整改决定**

时间 Fact 分开 `observed_at` 和 `event_time_interval`，至少包含 `lower_bound`、`upper_bound`、`censoring_type`、sampling interval、gap status 和 observer health。

没有前一有效观测时是 left-censored；窗口结束仍未退出时是 right-censored。回答用“首次观测到”或“可能发生于该区间”。

**当前 `main` 基线**

旧 OP-06、OP-35、OP-36 已退出当前树。当前仍没有覆盖左 / 右删失、阈值和观察槽的统一时间 Finding 与回答表达合同；需要时只能从 Git 历史重新审查相关经验。

**架构归属与阶段**

- 主归属：L5 Deterministic Compute；关联 L6、L7。
- M1 更新 Operator / Finding 输出并进入 Answer Context 与 Guard；M2 扩展多步骤时间组合；M3 Job 恢复后继续验证时间语义。

**验收证据**

- gap、窗口边界、缺前序观测和未恢复场景均有测试。
- 未知真实时刻不能渲染成精确事件时刻。

## R21 — 前缀数 / ASN 数不等于影响严重程度

**原评审核心结论**

一个 IPv4 `/8` 和 `/24` 都算一个前缀；单一对象计数不能代表地址空间、用户影响或事件重要性。

**v1.1 整改决定**

使用多指标“BGP 控制面变化向量”，至少分别显示 prefix count、IPv4 / IPv6 coverage、ASN、固定人口比例、VP agreement、baseline deviation 和 observer coverage。若排序，必须公开 Profile、键、权重和局限。

主动探测和流量只能作为独立 corroboration，不与 BGP 指标静默混成未经验证的总分。

**当前 `main` 基线**

旧 OP-05 severity rank Profile 已退出当前树。当前没有可执行的严重度排名能力，也不得把控制面指标渲染成用户影响或全国影响排名。

**架构归属与阶段**

- 主归属：L5 Deterministic Compute；关联 L7、X4。
- M2 完成最小指标向量；M4 用真实任务验证排序价值。

**验收证据**

- IPv4 / IPv6 分开。
- 结果展示排序依据，不只显示一个综合名次。
- 产品文案固定为“BGP 控制面变化程度”。

## R22 — Fixed Cohort 的选择偏差和幸存者偏差

**原评审核心结论**

Fixed cohort 有利于可比，但会漏掉新前缀、deaggregation、更具体路由、origin 迁移和事件中才出现的对象。

**v1.1 整改决定**

正式保留两套正交人口：

- Fixed Cohort View；
- Dynamic Observed Population View。

每个 ResultSet 强制声明 `population_strategy`；每个比例或恢复程度 Finding 强制引用具体 `denominator_ref`。两类人口不能静默混合，比较由确定性 Operator 完成。

**当前 `main` 基线**

旧 TOOL-07、TOOL-10 和 P1 fixed / new Runtime 已退出当前树。当前切片只固定单一读模型人口，统一的 `population_strategy` 和比例 Finding `denominator_ref` 尚未形成。

**架构归属与阶段**

- 主归属：L6 Artifact / Evidence；关联 L5、L7、X4。
- M2 建合同和双人口 Artifact；M4 扩展组合分析。

**验收证据**

- 每个比例能回答“分母来自哪套人口”。
- 新前缀不会因为不在 fixed cohort 中而被错误解释为恢复。
- 同一指标可以明确比较 fixed 与 dynamic 结果。

---

# 7. 关闭一个 R 项需要什么

每个 R 项必须同时满足：

1. 原始问题在本文中的对应关系未被改写；
2. 目标合同和禁止路径已经写清；
3. 当前 `main` 上有明确代码候选身份；
4. 与该候选相同的测试、评测或审查证据被接受；
5. 相关 L1–L7、X1–X4 的集成回归没有空白；
6. 文档、Issue 和 Project 状态没有把 Designed、Implemented、Verified、Released 混写。

如果某项只完成了第一阶段，例如 R04 只完成“冻结通用运行时扩张”，应记录阶段性完成，不得关闭整个 R04。

---

# 8. 与其他基石文档的关系

- 本文从原始敌对评审保护“问题没有丢”。
- 《Domeye Agent 目标架构 v1.1》决定整改后系统的责任边界。
- 《Domeye 当前代码基线》决定现有资产和缺口的事实口径。
- 《Domeye 22 项整改开发优先级与阶段计划 v1.1》决定实施顺序。
- GitHub Issue、Milestone 和 Project 只能跟踪这些决定，不能重新定义这些决定。
