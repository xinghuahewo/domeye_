# Domeye 当前代码基线

> 本文只回答“`main` 上现在真实存在什么”。它不把设计文档、代码存在、fixture 通过或代码合并写成目标架构已经完成。

| 项目 | 内容 |
|---|---|
| 基线日期 | 2026-08-16 |
| 仓库 | `xinghuahewo/domeye_` |
| 权威分支 | `main` |
| 基线提交 | `6a4bbd41aa712c12080a0126e5f8b1ec1440a9ca` |
| 提交说明 | `merge: 将完整最新代码并入 main` |
| 审计方式 | 仓库静态阅读、入口和调用链追踪、合同与验收资料核对、有限的无副作用检查 |
| 文档状态 | Observed Baseline；不是实现验收、发布证明或生产状态证明 |

---

## 1. 这份基线有什么用

目标架构说明“以后应该是什么样”；当前代码基线说明“现在到底是什么样”。二者必须同时存在。

没有目标架构，开发容易继续沿旧路径加功能。没有代码基线，团队又容易把现有资产全部推倒，或者把旧路径上的实现误认成新架构已经完成。

本文的使用方式是：

1. 新任务先说明它改变了本基线中的哪条路径；
2. 明确要复用、适配、冻结、替换还是退役哪些资产；
3. 实现完成后，用新的 commit 生成下一版基线或变更记录；
4. GitHub 状态和里程碑只能引用同一候选的代码与证据，不能只引用设计文档。

---

## 2. 分支与代码身份

### 2.1 新规则

从本基线开始，`main` 是 Domeye 的：

- 永久主干；
- 默认合并目标；
- 合并后代码的权威位置；
- 当前代码审计、构建、测试和候选取证的默认起点。

当前基线固定在 `main@6a4bbd41aa712c12080a0126e5f8b1ec1440a9ca`。将来 `main` 继续前进时，本文不会自动变成新提交的证明，必须显式更新基线身份。

### 2.2 现存旧规则

仓库 `AGENTS.md` 仍写着永久生产主干为 `codex/prod`。这是与新决定冲突的旧规则，后续要统一修订：

- `AGENTS.md`；
- 分支保护和 Pull Request 默认目标；
- CI 触发条件；
- 发布、回滚和候选身份脚本；
- 仍引用 `codex/prod` 的运行手册和验收资料。

在这些文件修订以前，以本次明确决定为准：**代码主线和合并后代码都看 `main`**。

这不表示 `main` 上每个提交都已经发布。`merged`、`built`、`verified`、`deployed` 和 `released` 仍是不同状态。

---

## 3. 审计做了什么，没有做什么

### 3.1 已完成

- 固定并记录 `main` 的 commit 身份；
- 阅读仓库根目录、现有架构、ADR、Roadmap 和 Agent 工程资料；
- 从前端、Flask API、Sidecar 到数据 API 追踪 P1 聊天主路径；
- 追踪旧报告 Agent 路径；
- 追踪 P2 Investigation W5 本地路径；
- 审查 L1–L7 和 X1–X4 的主要代码资产与断层；
- 在 `backend/` 执行 `sha256sum -c core.sha256`，14 个冻结条目全部匹配；
- 检查当前 GitHub workflow 和测试入口；
- 对两个 fixture 脚本的临时目录失败行为做了最小、安全的 shell 语义验证。

### 3.2 未完成

- 没有启动完整前后端服务；
- 没有调用真实模型 provider；
- 没有连接生产数据、生产主机或当前活动 release；
- 没有安装依赖后运行全量 build / test；
- 没有证明真实用户流量正在运行这个 commit；
- 没有证明当前模型、Prompt、Registry、数据和部署身份与代码提交一致；
- 没有把 P2 fixture 的离线结果提升为真实 Agent 认证。

因此本文可以支持“代码事实”和“迁移判断”，不能支持“生产可用”或“目标架构已经验证”。

---

## 4. 仓库当前包含什么

| 目录 | 当前作用 | 本次重构的看法 |
|---|---|---|
| `frontend/` | Vue 页面，包括聊天、报告和 Investigation 页面 | 保留产品入口；要随新 Action / Job、Typed Finding 和回答状态调整 |
| `backend/web/api/v2/` | Flask API、读模型接口和 Sidecar 代理 | 保留边界与只读接口；需要接入统一 Trust Kernel |
| `agent-sidecar/` | Pi 模型调用、P1 会话、Plan grounding、Registry 准入和执行 | 重要迁移起点；当前核心规划方式要替换 |
| `backend/services/` | 领域服务、P2 Tool / Operator、ResultSet、EvidenceGraph、CAS 原型 | 大量可复用资产；P2 大型 Runtime 不直接当新内核 |
| `backend/core/` | 冻结 BGP 核心 | 保持冻结，外围适配 |
| `contracts/` | JSON Schema、Registry 和运行合同 | 复用并迁移为小合同；旧 Plan 合同降为 Legacy |
| `config/data-profile.json` | 数据范围、快照时钟和业务时区的统一配置 | 应继续作为数据边界权威源 |
| `evaluation/` | 问题、fixture 和评测材料 | 迁成 Eval Coverage；不能成为生产路由表 |
| `deploy/` | 报告和 P1 聊天部署、检查及发布脚本 | 需要对齐 `main`、CI 和安全临时目录规则 |
| `docs/agent/` | P0 / P1 / P2 的大量历史合同和验收记录 | 保留历史事实，标明哪些目标已被 v1.1 取代 |
| `docs/architecture/`、`docs/roadmap/` | 当前仓库级架构和路线文档 | 仍残留 Plan 中心目标，需要按 v1.1 更新 |

---

## 5. 当前有三条主要运行线

仓库不是“一套已经按 L1–L7 完成的 Agent”。它同时保存三条性质不同的路径。

### 5.1 旧报告 Agent

```text
报告 API
  → Formal Sidecar
  → CountryOutageAgentOrchestrator
  → RuntimeCountryOutageReportService
  → 确定性 ReportCompiler
  → PiReportNarrator 做语言表达
  → Markdown / PDF
```

主要文件：

- `backend/web/api/v2/country_outage_agent_proxy.py`
- `agent-sidecar/src/cli/serve-formal.ts`
- `agent-sidecar/src/cli/formal-sidecar.ts`
- `agent-sidecar/src/application/country-outage-agent-orchestrator.ts`
- `agent-sidecar/src/runtime/services.ts`
- `agent-sidecar/src/pi/pi-report-narrator.ts`

这里的事实和报告骨架主要由确定性服务产生，Pi 被禁止使用 Tool，只负责语言表达。当前顺序是 `narrator.generate()` 先生成草稿，随后调用 `validateReportDraft()`。这提供了“确定性事实先存在、模型只负责表达、输出再做对齐检查”的可复用经验；缺口是尚未统一为 Typed Finding、Answer Context 和明确边界的轻量 Response Guard。

它是一条有价值的旧产品路径，但不是目标 v1.1 中会观察结果并滚动规划的 Agent Cognitive Loop。

处置：保留报告能力和后置表达经验；不要用 `AgentOrchestrator` 的名称证明 L1 已经完成。

### 5.2 P1 聊天主路径

```text
CountryOutageChatPage
  → Flask /api/v2/country-outage/chat/*
  → 本机 P1 Sidecar
  → ConversationService
  → Pi 一次性生成 UserGoalPlan
  → Host 根据 normalized_kind 生成完整 GroundingPlan / DAG
  → Registry 对整份 Plan 准入
  → Executor 顺序执行所有节点
  → Domeye read-model API
  → Host 拼接最终答案
```

主要入口：

- `frontend/src/pages/CountryOutageChatPage.vue`
- `frontend/src/api/countryOutageChat.ts`
- `backend/web/api/v2/country_outage_chat_proxy.py`
- `agent-sidecar/src/cli/serve-formal-p1.ts`
- `agent-sidecar/src/cli/formal-p1-sidecar.ts`
- `agent-sidecar/src/chat/runtime-v2-conversation.ts`
- `agent-sidecar/src/chat/pi-semantic-model.ts`
- `agent-sidecar/src/chat/runtime-v2-semantic.ts`
- `agent-sidecar/src/chat/trend-aware-grounder.ts`
- `agent-sidecar/src/chat/p2-registry-runtime.ts`
- `agent-sidecar/src/chat/page-capability-executor.ts`
- `agent-sidecar/src/chat/general-read-model-provider.ts`

关键代码事实：

- `P1PiSemanticModel` 使用 Pi SDK，但正式调用设置 `noTools: 'all'` 和 `tools: []`；
- 每个 turn 的 provider 请求数上限为 1；
- 模型输出一次性的 `P1UserGoalPlan`；
- `P1RuntimeV2Grounder.ground()` 根据 `normalized_kind` 的固定分支构造完整执行图；
- `P2GovernedRegistryRuntime.admitPlan()` 准入整份 Plan；
- `P1RuntimeV2SemanticTurnService.answer()` 随后遍历并执行所有节点；
- `P1PageCapabilityExecutor` 同时承担 API 调用、部分计算、Evidence 构造和中文结果文本。

结论：P1 是目前最适合改造的入口，但它还不是目标滚动 Agent。它的真实形态是“一次模型理解 + Host 完整 DAG + 一次准入 + 全部执行”。

### 5.3 P2 Investigation W5

```text
CountryOutageInvestigationPage
  → Flask investigations API
  → 注入的 Python Investigation Runtime
  → fixture planning / grounding
  → 静态完整 DAG
  → 同一请求内顺序执行
  → CAS 提交 ResultSet / EvidenceGraph / Receipt
```

主要文件：

- `frontend/src/pages/CountryOutageInvestigationPage.vue`
- `frontend/src/api/countryOutageInvestigation.ts`
- `backend/web/api/v2/country_outage_investigations.py`
- `backend/services/country_outage_p2_s1_investigation_runtime.py`
- `backend/services/country_outage_p2_s1_trusted_store.py`
- `backend/services/country_outage_p2_s1_registry_dispatcher.py`
- `agent-sidecar/src/cli/formal-p2-s1-w5-sidecar.ts`
- `agent-sidecar/src/chat/p2-s1-planning-grounding-port.ts`

它已经有：

- 静态 Plan 和 DAG 校验；
- revision + digest CAS；
- 内容寻址 Store；
- cancel、rerun、幂等和失败提交；
- Tool / Operator Registry；
- ResultSet、EvidenceGraph 和多类 Receipt；
- 预算和攻击 fixture。

但当前代码和资料也明确说明：

- 是本地隔离、fixture-oriented 的运行时；
- 外部 provider 调用为 0；
- production handler 未集成，production deployed 为 false；
- 普通应用未注入 runtime 时可以返回 503；
- `start_investigation()` 在请求线程中执行完整拓扑；
- 没有已确认的正式 worker 领取、跨进程等待和恢复路径；
- Plan 的回答策略仍包含 `teacher_required` 和 `sol_teacher_then_ds_student`。

结论：它是很有价值的耐久状态和证据原型，但不能标为目标 Durable Investigation Job 已经实现。

---

## 6. P2 的 168 个 case 到底证明了什么

`docs/agent/P2-组合式调查/实体调查实现工程/W6-离线确定性实现验收说明.md` 已经写得很诚实：

- 28 题 × 6 个场景 = 168 个 case；
- 162 个是 `correctly_blocked`；
- 6 个是 `correctly_deferred`；
- `executed_supported` 为 0；
- 完整问题回答认证为 `false`；
- 外部 provider 调用为 0；
- 不授权运行时晋级、生产部署或流量切换。

因此，168/168 表示“机器分类与冻结期望一致”，不是“28 题都回答正确”。

这批资产可以证明：

- 旧候选的合同、阻断、延期、攻击和本地恢复行为可重放；
- 历史候选身份和验收边界较清楚。

它不能证明：

- 真实 Pi 会滚动规划；
- 真实模型和 Tool 能完成开放任务；
- 新架构的 Action → Observation → Replan 已通过；
- 目标 Typed Finding → Answer Context → Response Guard 回答链已经存在；
- 系统已经生产可用。

---

## 7. 按目标架构逐层看当前代码

状态说明：

- **可复用资产**：代码真实存在，但还没有接入目标主链；
- **部分接入**：在旧路径有效，责任边界仍需改；
- **目标缺口**：目标主链还没有形成；
- **仅历史 / fixture**：只能做回归或参考，不能算新架构完成。

| 层 | 当前真实资产 | 当前判断 | 主要缺口 | 建议处置 |
|---|---|---|---|---|
| L1 Agent Cognitive | Pi 模型绑定、审计、超时；P1 会话 TTL、互斥、幂等、取消和回滚 | 部分接入 | Pi 一次性输出整轮 UserGoalPlan 目标列表，随后 Host 编译完整 GroundingPlan / DAG；没有逐 Action 的 observe / replan | 保留模型与会话外壳；替换 Planner / Grounder 主路径 |
| L2 Trust Kernel | loopback 认证、publication/revision 固定、权限检查、Registry snapshot、admission receipt | 可复用资产 | 控制分散；准入对象是整份 Plan；缺统一 Permission IR 和撤销传播 | 建单 Action 权威入口；适配现有身份和 Registry 校验 |
| L3 Action & Durable Job | P1 turn 生命周期；P2 CAS、取消、重跑和审计 | 可复用资产 | 没有目标 Interactive Action 合同；P2 不是正式异步 Durable Job | P1 外壳改造成 Action；冻结 P2 大 Runtime，按模块提取耐久资产 |
| L4 Capability Execution | Capability / Unit 元数据、读模型 API、P1 provider、TS Registry、Python Dispatcher | 部分接入 | Pi 看不到 Capability View；Host 用硬编码分支选 unit；两套执行权威并存 | 建受限 Capability View、Resolver 和 typed adapter；确定唯一权威 |
| L5 Deterministic Compute | P1 TOOL-01..06、OP-01..04；P2 TOOL-07..12、OP-05..33 和 OP-35..39；OP-04 趋势实现 | 可复用资产较强 | Tool / Operator 尚未统一接入新 Action；通用计算与领域语义边界仍要整理 | 先复用已验证单元，不先建任意 Compute Engine |
| L6 Artifact / Evidence | P2 ResultSet、EvidenceGraph、内容寻址 Store、Receipt；P1 evidence refs | 可复用设计与代码资产 | 当前对象混合执行、血缘、领域事实和回答支持关系；EvidenceGraph 自报 design-only；新 Action Artifact Envelope 和跨步骤 lineage 未统一 | 分开三类核心证据语义；回答绑定进入 Trace；统一新链路 Artifact / Receipt 身份 |
| L7 Answer Composition & Guard | P1 确定性结果文本与限制；P2 claim 结构和回答 fixture；旧报告后置校验 | 目标缺口 | 没有统一 Typed Finding、Answer Context 和职责受限的 Response Guard；现有回答路径仍按旧问题路由或 fixture 工作 | 复用确定性结果与 narrator；建立最小 Finding-to-Answer 链，不建设通用 Claim 平台 |

### 7.1 L1 的准确判断

L1 不是“没有 Pi”，而是 Pi 当前只做一次语义结构化。需要保留：

- 正式模型绑定；
- provider 审计；
- 超时和取消；
- 会话所有权、幂等和原子状态提交。

需要替换：

- `P1ModelUserGoalPlanner` 一次性输出整轮 UserGoalPlan 的方式；该对象是目标列表、不是执行 DAG，完整 GroundingPlan / DAG 由 Host 随后编译；
- `P1RuntimeV2Grounder` 和 `P1TrendAwareGrounder` 的标签到完整 DAG 路由；
- 一次性完整执行循环。

### 7.2 L2 的准确判断

L2 已有很多安全零件，但还没有统一 Trust Kernel。当前准入主要围绕 Plan 和已经选定的 execution unit。新链路要把准入对象改成当前一个 Proposal，并把 principal、tenant、publication、Policy、预算、Registry snapshot、撤销状态、已发生动作历史、状态转换约束和数据流规则绑定到稳定 Permission IR。Permission IR 不得包含或预先批准未来 Action 序列。

P2 的 `GATE-01`～`GATE-05` 不应继续作为 Agent 可规划的普通节点；核心 Gate 应成为不可绕过的控制面不变量。

### 7.3 L3 的准确判断

P1 有交互生命周期外壳，P2 有持久化和 CAS 思路，但两者之间还没有目标合同。不能简单把 P2 类名中的 Investigation 当成 Durable Job 已完成，也不能按问题长短把 P1/P2 变成永久 Profile。

### 7.4 L4 的准确判断

当前 Pi 的 `tools` 是空数组，它看到的是人为编写的 `normalized_kind` 菜单，不是 Trust Kernel 过滤后的 Capability View。`P1PageCapabilityExecutor` 又把 adapter、计算、Evidence 和渲染混在一起。新链路需要按责任拆分，且不能在 TypeScript Registry、Python Dispatcher 之外再建第三套权威。

### 7.5 L5–L6 的准确判断

这是目前资产最丰富的部分。尤其：

- P2 原子只读 Tool 有清楚的人口、分页和排序合同；
- P2 Domain Operator 有版本化输入输出和 fail-closed 错误；
- ResultSet、内容寻址、CAS、Receipt 和 Evidence 逻辑可复用；
- TOOL-13、OP-34 和 PLAN-CAP-02 被明确 deferred，没有偷算完成。

这些资产应优先接入新 Action 链，而不是重新开发同名零件。

同时必须保留现状边界：当前 EvidenceGraph 生成结果明确带有 `design_only=true`、`runtime_implemented=false`、`production_deployed=false`。现有 completeness 主要是 `complete`、`partial_page`、`source_incomplete`，还没有统一表达 query、source、observer 和 world 四个正交维度；人口类 Artifact / Finding 也尚未统一传播 `population_strategy`，比例 Finding 尚未统一绑定 `denominator_ref`。

### 7.6 L7 的准确判断

当前 P1 会在 executor 中直接构造中文结果，P2 仍保留 Teacher / Student 回答策略，旧报告路径则是先生成草稿、再验证。另有 `country_outage_trend_product.py` 已经存在“先构造结构化事实再生成答案”的有用经验，但其 `answer_trend_question_v1` 仍按问题关键词选路。这些资产可迁移为 Typed Finding 和轻量表达检查，不需要补建独立 Publisher 或 Verified Claim Set。

新目标需要把业务事实的产生权保留在 Tool / Operator，将公共身份、范围和完整性放在 Artifact Envelope，再由 Answer Context 选择本轮所需 Finding。旧 narrator 可以继续负责语言组织，但不能成为事实来源；其输出由 Response Guard 检查可测试的表达漂移。

---

## 8. 四个横切面的当前状态

| 横切面 | 现有资产 | 尚未证明什么 | 当前结论 |
|---|---|---|---|
| X1 Eval / Release | 大量 fixture、合同检查、P0/P1/P2 验收材料、W6 同候选意识；历史 P1 有 28 次真实模型评测 | 真实模型 + 真实 Tool 的滚动任务成功、稳定性、当前 commit 的完整 CI；历史 P1 证据与当前源码及可变模型 alias 不是同一不可变候选 | 离线回归较强，当前候选的 Agent 认证缺失 |
| X2 Security / Supply Chain | loopback、bearer、Registry digest、文件与 symlink 检查、fail-closed 路径 | 统一 JCS、签名身份、Permission IR、跨租户、全链 DLP、Prompt / model / dependency 身份闭合 | 有零件，没有完整安全权威链 |
| X3 Observability / SLO | provider audit、usage / cost 字段、事件和执行 Receipt、部分预算 | 覆盖 Goal → Proposal → Admission → Action → Artifact / Finding → Answer Context → Guard Result → Outcome 的统一 trace；端到端 P95/P99、队列、backpressure、degraded mode、retention 和生产 SLO | 可观测数据分散，尚未形成回答质量门 |
| X4 Product / Data Boundary | RRC25 单 publication 边界、BGP 语义限制、`config/data-profile.json` | query/source/observer/world completeness 的全链传播；统一时间上下界与 censoring；population_strategy / denominator_ref；Response Guard 的硬范围检查和安全回退 | 边界意识强，但未成为统一回答约束 |

---

## 9. 明确可复用的资产

### 9.1 模型和会话

- `P1PiSemanticModel` 的模型证书、审计、取消和超时；
- `P1RuntimeV2ConversationService` 的 TTL、互斥、幂等、revision 漂移检查和原子回滚；
- Sidecar 的 loopback 安全和 timing-safe token 检查。

### 9.2 Capability 和数据读取

- 已有 general read-model API；
- `HttpP1GeneralReadModelProvider` 的身份一致性检查；
- Registry snapshot 的版本、摘要、双向映射和 active 状态检查。

### 9.3 确定性计算

- P1 已存在以 TOOL-01..06、OP-01..04 标识的能力行为和算法，但仍嵌在旧 `P1PageCapabilityExecutor` 中；可复用的是底层 API、校验和算法，不能直接把这些分支认定为目标架构下已经拆分完成的 Tool / Operator；
- `event-window-trend.ts` 中的 OP-04 / `CAP-TREND-001`；
- P2 `country_outage_p2_s1_tools.py` 中 TOOL-07..12；
- P2 `country_outage_p2_s1_operators.py` 中 OP-05..33、OP-35..39；
- BGP 路径、时间、集合、人口和边界语义合同。

### 9.4 Artifact 和耐久性

- P2 ResultSet；
- P2 EvidenceGraph 的数据结构和验证经验；
- 内容寻址 Store、revision + digest CAS；
- cancel、rerun、幂等、失败关闭和 Receipt 思路。

### 9.5 冻结核心

`backend/core.sha256` 记录 14 个冻结条目。本次检查全部通过。重构应继续在外围建立适配层，不能为了让新 Agent 好接而直接修改冻结核心。

---

## 10. 应退出、冻结或降级的旧资产

| 对象 | 处置 | 说明 |
|---|---|---|
| `P1ModelUserGoalPlanner` 一次性整轮目标列表输出 | 替换 | 它不是执行 DAG；应改成当前 Goal State 下的一项 Proposal，阻止 Host 随后一次性编译整图 |
| `P1RuntimeV2Grounder` / `P1TrendAwareGrounder` 生产路由 | 退役生产、保留回归 | 不能继续用标签编译完整 DAG |
| `P2GovernedRegistryRuntime.admitPlan()` | 改语义 | 从整 Plan 准入改为单 Action 准入和解析 |
| `P1PageCapabilityExecutor` | 拆分 | 不再同时做 adapter、计算、Evidence 和渲染 |
| `P1GroundingPlan` / Common Plan IR 目标 | Legacy | 历史记录可保留，不再作为新公共合同 |
| `CountryOutageP2S1InvestigationRuntime` 整体 | 冻结 | 只提取可复用模块，不继续扩大 God Runtime |
| P2 Teacher / Student 默认回答链 | 仅实验 / fixture | 默认路径改为 Typed Finding + 单 Pi / 确定性骨架表达；Teacher 仅在 A/B 证明收益后考虑 |
| 28 Question Template 生产映射 | 退出生产路由 | 保留为 Eval Coverage |
| `InvestigationPlan` | Job 内部 Legacy 资产 | 不能由自然语言问题直接命中 |
| 旧报告 `PiReportNarrator` | 后置表达适配 | 只能消费 Answer Context，不得读取原始未约束文本后新增业务事实 |

历史验收资料不应为了符合新术语而静默改写。正确做法是保留原文，并标注其目标已由 v1.1 哪一条决定取代。

---

## 11. 当前仓库级文档的冲突

### 11.1 `docs/architecture/target-architecture.md`

目前只有非常简短的七层列表，并仍写 `Plan / Workflow`。它不足以约束新闭环，应该由《Domeye Agent 目标架构 v1.1》取代。

### 11.2 `docs/adr/ADR-001-pi-as-agent-runtime.md`

核心决定仍可保留：Pi 负责 Agent loop，Domeye 保留身份、授权、Registry、准入、Artifact / Evidence 和正式状态控制。

需要收紧：

- `tool calling` 改成 Pi 提出 Capability，Domeye 执行；
- `response generation` 改成 Pi 只能根据 Answer Context 中的 Typed Finding 组织语言，并接受轻量 Response Guard；
- 验收必须是滚动循环，不是一次语义请求。

### 11.3 Roadmap 与 requirements

以下资料仍包含 Common Plan IR、Plan / Workflow 中心或旧 R01–R22 映射：

- `docs/roadmap/epics.md`
- `docs/roadmap/feature-breakdown.md`
- `docs/roadmap/roadmap-view.md`
- `docs/requirements/adversarial-review-tracking.md`

它们应在目标架构和当前基线入库后更新，不能继续指导新开发。

### 11.4 分支规则

`AGENTS.md` 中的 `codex/prod` 永久主干规则已由新的 `main` 规则取代。必须显式修订，而不是让两个相反规则长期共存。

---

## 12. 构建、CI 与测试基线

### 12.1 当前 GitHub workflow

仓库当前只有 `.github/workflows/codex-version-boundary.yml`。它在 Pull Request 上：

- 检查冻结路径和版本依赖；
- 运行 `dev.tests.test_codex_task_guard`。

它不是完整的 build / unit / integration / Agent eval / release pipeline。当前基线提交也没有一组可用于证明全部通过的组合状态。

### 12.2 Makefile 有检查入口，但本次未全量运行

Makefile 包含 `check-fast`、`check-integration`、`check-release`、`check-release-full` 等入口。这说明仓库已有检查组织，但本次审计没有安装依赖并运行全套：当前环境是 Python 3.12，而 `backend/pyproject.toml` 要求 `>=3.10,<3.11`；Python 测试依赖和前端、Sidecar 的 `node_modules` 也未准备。因此不能把“有 target”写成“目标在当前 commit 通过”。

### 12.3 已发现的 fixture 安全问题

以下脚本把临时目录硬编码在 `/private/tmp`：

- `deploy/country-outage-agent/tests/run-fixtures.sh`
- `deploy/country-outage-agent/p1-chat/tests/run-fixtures.sh`

当前 Linux 环境没有 `/private/tmp`。更危险的是：

```bash
readonly FIXTURE_ROOT="$(mktemp -d /private/tmp/...XXXXXX)"
```

命令替换失败可能被 `readonly` 赋值语句掩盖，使脚本继续运行且 `FIXTURE_ROOT` 为空。后面的 `${FIXTURE_ROOT}/project`、`${FIXTURE_ROOT}/runtime` 等就会退化为根目录下的 `/project`、`/runtime`。

本次没有运行完整 fixture，只做了安全的 shell 语义复现。该问题应作为优先测试安全缺陷处理：

- 使用可移植的 `mktemp -d` 或显式可配置的安全目录；
- 将创建临时目录与 `readonly` 赋值拆成两步；
- 失败后立即退出；
- 在清理前验证变量非空且目标位于预期临时根；
- 加 Linux CI 回归。

### 12.4 当前可诚实写出的验证状态

| 项目 | 状态 |
|---|---|
| `main` commit 身份 | 已固定 |
| 工作树静态审计 | 已完成 |
| 冻结 core SHA-256 | 14/14 通过 |
| 目标 Agent loop | 未实现 / 未验证 |
| 全量 build | 未验证 |
| 全量 unit / integration test | 未验证 |
| 真实模型任务评测 | 未验证 |
| 生产部署与流量 | 未验证 |
| P2 离线 fixture | 有历史证据，但不等于当前目标认证 |

---

## 13. 与 22 项整改的直接关系

本基线确认，不应只把 R01–R22 当成 22 个独立 Issue。它们落在不同代码层和横切面：

- R09 要求拒绝 Common Plan；R16 要求 Question Template 退出生产路由；
- R04 要求停止自研通用 Durable Workflow Runtime，把 Interactive Action 与真正需要耐久性的 Job 分开；
- R03、R07、R08 要把当前分散的准入、身份和供应链零件收敛到 Trust Kernel；
- R06、R11、R12、R22 要复用并修正 P2 的 ResultSet、Evidence 和人口资产；
- R10、R13、R19–R21 要保留确定性计算优势，同时收紧 Adapter、路径、时间和影响语义；
- R14、R15、R18 要建立 Finding-first 回答链，取代执行器自由写答案或 Teacher / Student 默认链，但不建设通用 Claim 平台；
- R01、R05、R17 要把真实 Agent 轨迹、失败恢复、成本和 SLO 纳入同候选认证；
- R02 则要求产品声明始终受 RRC25 数据边界约束。

也就是说，22 点整改应以本基线中的真实入口和资产为落点，而不是重新设计一套脱离代码的空目录。

---

## 14. 建议的迁移起点

这不是完整 Roadmap，只说明从当前代码出发最合理的第一步顺序。

### 第一步：先冻结事实

- 批准目标架构 v1.1；
- 批准本代码基线；
- 把 `main` 规则写回仓库治理文件；
- 给旧 Plan、Question Template 和 P2 fixture 标明 Legacy 边界；
- 修复临时目录测试安全问题，建立最小 Linux CI。

### 第二步：替换一个真实 P1 纵向切片

保留 P1 会话、认证、Registry 校验和 read-model provider，替换：

```text
UserGoalPlan → GroundingPlan → admitPlan → execute all
```

为：

```text
Goal State
  → one Capability Proposal
  → Trust Kernel admission
  → Resolver / Adapter / Tool or Operator
  → Artifact + Receipt
  → Pi Observation
  → replan / clarify / finish
```

同一个候选必须按后续[锚点合同](Domeye_First_Vertical_Slice_Anchor_v1.0.md)验证 J1–J5；
成功、拒绝和读取失败只是其中三类，不能替代回答越界和确定性边界旅程。

### 第三步：补齐 Finding-to-Answer

首个切片的最终回答必须走：

```text
Artifact / Typed Finding
  → Answer Context
  → renderer
  → Response Guard
  → answer；`block` 时丢弃草稿并使用同一 Context 的确定性回退
```

Response Guard 只检查数字、单位、时间、对象、范围、必要 limitation、DLP 和领域禁用
语义，只返回 `pass/block`。`block` 后不得再次调用模型。没有这一步，不能把“有 evidence
ref 的中文文本”当成已对齐回答。

### 第四步：真实需要时再接 P2 耐久资产

只有出现恢复、取消、重试、等待、fan-out、跨会话或多次正式提交需求时，才进入 Durable Job。届时从 P2 提取 CAS、Store、Receipt、预算和恢复经验，不直接扩写当前大型 `CountryOutageP2S1InvestigationRuntime`。

### 第五步：扩展多能力滚动组合

用 held-out 表达和未见能力组合证明不需要新 Question Template 或固定 DAG，再讨论受限 Compute Engine 是否有必要。

---

## 15. 当前开放决定

以下事项在本次代码审计时无法从代码中自动得出。后续锚点合同已经冻结第 1 项和第 4 项
中首片直接需要的最小边界；其余仍不能在实现时偷偷决定：

1. **已由锚点冻结：**首个真实纵向切片的用户问题、数据身份、TOOL-03、OP-01、J1–J5
   与无模型确定性回退；
2. TypeScript P1 Registry 与 Python P2 Dispatcher 最终谁是唯一权威，怎样迁移；
3. Durable Job 采用哪种 worker / workflow 实现；
4. **部分由锚点冻结：**首片 Typed Finding、Answer Context、Guard 与安全回退的最低不变量；
   具体机器 Schema 仍须由实现任务确认；
5. Permission IR 第一版字段、撤销和跨租户模型；
6. `main` 的分支保护、required checks 和发布候选规则；
7. 当前旧报告路径在新架构迁移期间保留多久；
8. P2 大型 Runtime 是归档、冻结回归，还是分阶段拆出模块；
9. 真实模型、数据和生产 SLO 的第一版阈值。

这些决定应通过 ADR 或 Gate 明确记录，不应靠类名、目录名或旧文档默认推断。

---

## 16. 代码索引

| 主题 | 主要文件 |
|---|---|
| Pi 语义模型 | `agent-sidecar/src/chat/pi-semantic-model.ts` |
| P1 会话 | `agent-sidecar/src/chat/runtime-v2-conversation.ts` |
| P1 Planner / Grounder / Turn | `agent-sidecar/src/chat/runtime-v2-semantic.ts` |
| 趋势 Grounder | `agent-sidecar/src/chat/trend-aware-grounder.ts` |
| P1 Registry 准入 | `agent-sidecar/src/chat/p2-registry-runtime.ts` |
| P1 执行器 | `agent-sidecar/src/chat/page-capability-executor.ts` |
| P1 数据 Provider | `agent-sidecar/src/chat/general-read-model-provider.ts` |
| P1 正式 Sidecar 组装 | `agent-sidecar/src/cli/formal-p1-sidecar.ts` |
| 旧报告 Orchestrator | `agent-sidecar/src/application/country-outage-agent-orchestrator.ts` |
| 旧报告 Narrator | `agent-sidecar/src/pi/pi-report-narrator.ts` |
| P2 Investigation Runtime | `backend/services/country_outage_p2_s1_investigation_runtime.py` |
| P2 Registry / Dispatcher | `backend/services/country_outage_p2_s1_registry_dispatcher.py` |
| P2 Tool | `backend/services/country_outage_p2_s1_tools.py` |
| P2 Operator | `backend/services/country_outage_p2_s1_operators.py` |
| P2 ResultSet | `backend/services/country_outage_p2_s1_result_set.py` |
| P2 EvidenceGraph | `backend/services/country_outage_p2_s1_evidence_graph.py` |
| P2 Store | `backend/services/country_outage_p2_s1_trusted_store.py` |
| Investigation API | `backend/web/api/v2/country_outage_investigations.py` |
| 数据边界 | `config/data-profile.json` |
| 冻结核心清单 | `backend/core.sha256` |
| 当前 CI | `.github/workflows/codex-version-boundary.yml` |
| P2 W6 真实边界 | `docs/agent/P2-组合式调查/实体调查实现工程/W6-离线确定性实现验收说明.md` |

---

## 17. 基线结论

当前 `main` 不是空仓库，也不是目标 Agent 已完成。最准确的说法是：

- Domeye 已经拥有较强的确定性领域计算、身份检查、Registry、Artifact、Evidence 和本地耐久原型；
- P1 已有可改造的 Pi 调用和会话外壳；
- 但生产主路径仍是一次性语义规划、Host 生成完整 DAG、整 Plan 准入和一次性执行；
- P2 是可复用的本地 fixture / 状态原型，不是正式 Durable Job；
- Finding-first 的回答组织与边界检查链尚未形成；
- 当前 commit 的全量 CI、真实模型评测和生产部署状态都没有被本次审计证明。

所以，重构的正确起点不是推倒全部代码，也不是继续包装旧 Plan，而是：**保留可信零件，先用一个真实 P1 纵向切片建立“Proposal → Admission → Execution → Observation → Replan → Typed Finding → Answer”的新主链。**
