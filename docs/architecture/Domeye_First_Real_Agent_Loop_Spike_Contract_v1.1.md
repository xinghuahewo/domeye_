# Domeye 首个真实 Agent Loop Spike 合同 v1.1

> 本文只回答一个问题：**去掉 Host 固定规划以后，Pi 能否根据真实 Observation，自主提出
> 合法且能推进 Goal 的下一项 Capability，并完成首个真实纵向任务。**

| 项目 | 内容 |
|---|---|
| 文档版本 | `domeye.first-real-agent-loop-spike-contract/v1.1` |
| 文档性质 | M1 前置实验合同；不是正式实现合同 |
| 状态 | `Designed`；不代表 Spike 已运行、M1 已实现或 DG1 已通过 |
| 固定功能范围 | 首片锚点中的序列读取、极值计算和最终回答 |
| v1.1 修订 | 去除固定轨迹验收；缩小 Goal State；约束 Capability View 变化；预留 Job Upgrade；拆薄 Gateway；明确 Spike Eval 不等于 DG1 |
| 上位依据 | Agent 目标架构 v1.1、ADR-001、首个纵向切片锚点合同 v1.0 |
| 实验 Gate | 每个 Spike 独立给出 `GO / REPAIR / STOP` |

本文不设计或验收身份、权限、安全策略、DLP、产品交互、发布和部署；这些继续由现有合同
负责。本文也不改变冻结事件、数据、Operator 语义、回答事实和 RRC25 边界。

## 1. 固定范围与核心假设

### 1.1 只验证一条真实任务

当前只处理首片锚点定义的一类目标：读取当前冻结事件的固定前缀可见 IPv4 地址量序列，
计算最低值及首次观测时间，并形成包含首值、末值、最大值和极差的答案。

这条任务存在领域依赖：极值计算需要合格序列，回答需要合格极值结果。但领域依赖不等于
必须由 Host 预生成唯一运行轨迹。不同起始状态可以有不同合法路径，例如：

- 没有任何 Artifact 时，读取序列能够推进 Goal；
- 已有合格序列 Artifact 时，可以直接计算极值，不应强制重读；
- 已有合格极值 Finding 时，可以直接进入完成判断；
- Observation 显示部分结果或失败时，应重试、澄清、停止或请求升级，而不是继续成功链。

因此本文不验收“是否背出 `read → derive → finish`”，而验收：

1. Decision 在当前状态下合法；
2. Decision 满足当前 Capability 前置条件；
3. Decision 能推进 Goal，或明确说明为什么不能推进；
4. 最终任务 Outcome 正确；
5. Host 没有预先提供 Decision 或固定轨迹。

### 1.2 目标运行循环

```text
用户目标
  → Goal State
  → 稳定、可审计的 Capability View
  → Pi 提出一个 Decision
  → Trust Kernel 执行现有准入
  → Registry Resolver 解析业务 Capability
  → Execution Adapter 调用现有 Tool / Operator
  → Observation Builder 形成结构化 Observation
  → Pi Replan / Clarify / Stop / Upgrade / Finish
  → 复用现有 Finding → Answer Context → Renderer → Guard → Answer
```

这里的“Capability Gateway”只表示 Pi Proposal 到现有执行资产之间的协议边界，不要求创建
一个同时拥有准入、Registry、执行、状态和 Observation 的新组件。

### 1.3 核心待证假设

四个 Spike 依次验证：

1. Pi 在看不到标准答案时能产生合法、推进 Goal 的 Decision；
2. 业务 Capability 能通过薄接线调用现有执行资产；
3. Observation 的变化会真正改变 Pi 的后续 Decision；
4. 轨迹评测能区分真实 Agent Loop 与小型固定 Workflow。

任何 Spike 失败都先判断 `REPAIR / STOP`，不得自动扩大成完整 Agent 重构。

## 2. 五个核心难点

### 2.1 下一步决定权归属

Pi 必须产生当前 Decision。Host 负责组装上下文和调度，Trust Kernel 负责现有准入，Resolver
和 Adapter 负责实现解析与调用；它们都不得产生业务上的标准下一步。

关键判据：在需要认知决策的路径上，如果删除 Pi 调用后，Host 仍能直接生成完全相同的
`capability_id + arguments + next_state`，则 Agent Loop 不存在。

### 2.2 最小 Goal State

Spike 第一版 Goal State 只包含：

```text
Goal
Observed Artifact
Observation History
Budget
```

具体含义：

- `Goal`：当前要完成什么，以及完成判据；
- `Observed Artifact`：Pi 已经观察到的合格 Artifact 引用和最小摘要；
- `Observation History`：按顺序记录发生过什么，供 Pi 判断进展、失败和重复；
- `Budget`：当前互动循环还允许多少轮或多少次可重试动作。

第一版不单独建设 Planning State、完整 attempt graph、unresolved-requirement engine、stall
detector 或 stop-state machine。重试信息由 Observation History 和 Budget 足够表达；只有真实
Spike 证据证明字段不足，才允许在 `REPAIR` 中增加一个最小字段。

Goal State 不能包含预期 Capability、下一步建议、完整 DAG 或已批准未来动作。

### 2.3 Capability View

首片只暴露两个业务能力：

```text
read_bound_metric_series
derive_series_extrema
```

每个 Capability 只描述业务用途、输入 schema、前置条件和输出摘要 schema。Pi 不得看到：

```text
TOOL-03
OP-01
具体 handler
数据库或 HTTP 接口
实现版本选择逻辑
```

两个 Capability 的存在不能被当作隐含 Workflow。View 必须允许 Pi依据当前 Artifact、
Observation 和 Capability 前置条件做判断，而不是由 Host 通过显示顺序、单例裁剪或动态隐藏
能力来暗示唯一轨迹。

### 2.4 Observation → Replan

Observation 最少区分：

- `completed`；
- `partial`；
- `rejected`；
- `failed`。

最小字段包括：

- `observation_id`；
- `capability_id`；
- `status`；
- `artifact_ref` 和结果摘要（存在时）；
- `limitations`；
- `reason_code`；
- `retryable`。

Observation 只能描述发生了什么，禁止包含 `recommended_action`、期望 Capability 或下一状态
答案。

### 2.5 Agent Loop Feasibility Evaluation

Spike 评测关注：

- Goal 是否保持正确；
- Decision 是否满足当前状态、前置条件和推进性；
- Observation 是否真正影响下一步；
- partial / rejected / failed 是否得到不同处理；
- 最终任务 Outcome 是否正确；
- 是否出现 Host 固定链、字符串路由、Capability View 偷改或 oracle 泄漏。

它叫 `Agent Loop Feasibility Evaluation`，不是正式 Agent Eval、Certification 或 DG1。固定脚本
可以验证数据分支，但不能替代真实 Pi 轨迹。

## 3. 必须实现

### 3.1 四个最小合同

#### Goal State

必须是版本化结构，只包含 `Goal / Observed Artifact / Observation History / Budget`。不得从完整
聊天历史临时猜测，也不得保存完整规划图。

#### Capability View

每项至少包含业务 ID、用途、输入 schema、前置条件和输出摘要 schema。View 的每个快照必须有
稳定 ID 或 digest，Pi 的 Decision 必须绑定它实际看到的快照。

#### Decision

Pi 每轮只能返回以下联合类型之一：

```text
invoke_capability(capability_id, arguments)
finish(answer_intent)
clarify(reason_code)
stop(reason_code)
upgrade_to_job(goal, reason_code)
```

`upgrade_to_job` 只为与目标架构保持合同兼容。Spike 不实现 Durable Job；出现该 Decision 时，
实验 Host 只返回 `unsupported_in_spike` Observation，Pi 必须据此停止或澄清。不得借占位类型
顺手实现 Job Engine。

#### Observation

必须把真实执行结果、限制和失败语义返回 Pi；不得只更新 Host 内部状态，也不得告诉 Pi 下一步
应该做什么。

### 3.2 真实 Pi 决策

1. 使用真实 Pi / Provider 路径产生 Decision；scripted model 只能用于单元测试。
2. Provider 请求和 Pi 原始 Decision 必须可审计。
3. Provider 请求中不得出现 Host 计算出的 canonical expected decision。
4. Eval 必须按“合法 Decision 集 + 是否推进 Goal + 最终 Outcome”判断，不得只比较一条固定轨迹。
5. Pi 的 Proposal 必须经过现有 Trust Kernel 准入以后才能执行。
6. 当前 Observation 返回以前，不得生成或准入下一项领域 Action。

“使用 Pi 原生 Tool Calling”是待验证假设，不是先验正确答案。如果它不能在不泄漏标准
Decision 的条件下工作，应返回 `REPAIR` 或 `STOP`，不能偷偷恢复 Host 规划。

### 3.3 Capability View 变更规则

Capability View 表示当前业务能力空间，不是 Host 的下一步控制器。

同一轮或同一稳定运行上下文中，单次 `completed / partial / rejected / failed` Observation
不得直接导致 Host 隐藏、增加、排序或改写 Capability。View 只有在以下外部事实确实变化时
才允许生成新快照：

- Registry 版本或生命周期发生变化；
- domain binding 发生变化；
- Budget 发生变化；
- Capability 合同或可用性发生变化。

每次变化必须记录 `previous_view_digest`、`new_view_digest` 和结构化 `change_reason`。错误处理应
主要通过 Observation 和 Pi Decision 完成，不能通过 Host 偷换棋盘实现。

### 3.4 薄执行边界

逻辑职责固定拆开：

| 边界 | 唯一职责 | 不得拥有 |
|---|---|---|
| Trust Kernel | 对当前 Proposal 运行现有准入 | Goal State、下一步选择 |
| Registry Resolver | 将业务 Capability 解析为登记的 Execution Unit | 自然语言、重试和 Replan |
| Execution Adapter | 调用现有 Tool / Operator 并返回原始结果 | Capability 选择、Observation 策略 |
| Observation Builder | 把原始结果归一为 Observation | 推荐下一步、修改 Capability View |

可以有一个很薄的 Gateway facade 负责依次调用这些边界，但它不得持久化 Goal State、判断任务
完成、选择 Capability、拥有 Registry 生命周期或把四种职责重新合成一个 God Host。

### 3.5 Observation 驱动的后续行为

必须验证的是状态约束和 Outcome，而不是唯一动作序列：

- 没有合格序列时，不得执行需要序列的极值计算；
- 已有合格序列时，可以直接计算，不得强制重读；
- 已有合格极值结果时，可以直接完成，不得强制再执行前序动作；
- partial 结果不能作为完整输入继续计算；
- retryable failure 只允许在 Budget 范围内形成新 Decision；
- terminal failure 或 rejected 后必须停止、澄清或请求升级；
- Observation 和 Goal State 无变化时不得机械重复同一 Proposal。

### 3.6 正确任务结果

Tool 成功和 Operator 成功都不等于任务完成。只有 Pi 判断 Goal 满足，并且现有
Finding-to-Answer 链得到正确最终答案，当前 trial 才能记为成功。

本文复用既有 Typed Finding、Answer Context、Renderer 和 Guard，不在 Spike 中重建或扩展
它们。

### 3.7 完整轨迹证据

每个真实 trial 至少记录并绑定：

- 输入 Goal State；
- Capability View digest 和完整内容；
- 发给 Provider 的完整请求制品，必须保留 system prompt、messages、Tool / Decision schema、
  `tool_choice` 和 Capability View；持久化前只允许按显式字段规则去除凭据值，不得省略可能
  泄漏 expected decision 的语义内容；
- Pi 原始 Decision；
- 当前状态对应的合法 Decision 集及判定依据；
- Resolver 结果和实际 Execution Unit；
- Action 结果与 Observation；
- 下一轮 Goal State；
- 最终 `finish / clarify / stop / upgrade_to_job`；
- Tool / Operator 调用计数；
- 最终 Outcome。

证据必须能回答“下一步是谁决定的、Pi 看到了什么、Decision 是否合法且推进 Goal、为什么状态
发生变化”，不能只有最终答案。

## 4. 明确禁止

以下任一项出现，当前 Spike 不得判为 `GO`：

1. **禁止 Host 计算标准 Decision。** 不得用 `requiredDecisionForState` 或等价函数生成完整
   Proposal 后要求模型照抄。
2. **禁止泄漏期望动作。** Prompt、Tool schema、Capability View 和 Observation 中不得包含
   expected decision、expected capability、expected arguments 或下一状态答案。
3. **禁止固定 tool choice 指定业务动作。** 可以强制通用 Decision schema，不能强制某个具体
   Capability。
4. **禁止按固定轨迹判定成功。** 不得只接受 `read → derive → finish`；必须接受当前状态下其他
   合法且能推进 Goal 的 Decision 和正确 Outcome。
5. **禁止精确字符串路由。** 不得用 question → template / intent → 固定 Capability 链。
6. **禁止预生成 Plan / DAG。** 当前结果回来前不得生成、批准或持久化未来领域动作。
7. **禁止用 Capability View 偷渡 Workflow。** 不得用显示顺序、单例裁剪或 Observation 后动态
   隐藏能力来编码下一步。
8. **禁止 Observation 给答案。** 不得增加 `recommended_action` 或等价提示。
9. **禁止根据 Observation 直接修改 Capability View。** View 变化必须来自明确外部事实，留下
   前后摘要和原因。
10. **禁止向 Pi 暴露实现。** 不得暴露 TOOL-03、OP-01、handler、数据库接口或具体实现选择。
11. **禁止失败后盲重试。** Goal State、Observation History 和 Budget 没有变化时不得重复同一
    Proposal。
12. **禁止 oracle 进入运行路径。** oracle、标准 Proposal 和标准答案只能供独立 Eval 比较。
13. **禁止 scripted 轨迹冒充真实 Pi。** scripted fixture 必须与真实 Provider trial 分栏报告。
14. **禁止把重复次数冒充场景覆盖。** 同一输入重复运行只能证明稳定性。
15. **禁止把 Gateway 做成 God Host。** Gateway 不得同时拥有 Goal、准入、Registry、执行、
    Observation 策略和完成判断。
16. **禁止实现 Job Upgrade。** Spike 只保留 Decision schema，占位不能触发 Durable Job 建设。
17. **禁止为了通过 Spike 扩建平台。** 不得新增通用 Planner、Common Plan IR、完整 Workflow
    Engine、通用 Compute Engine、多 Agent 或全量 Registry 重构。
18. **禁止越级宣告。** 任一 Spike `GO` 只说明该假设成立，不等于 M1 已实现、已验证或已上线。

## 5. Spike-first 验收硬门

### 5.1 通用规则

- 每个 Spike 使用独立任务、独立 worktree 和独立证据；默认 timebox 为一个工作日。
- 超出 timebox 前必须先给出 `GO / REPAIR / STOP`，不得靠扩大范围继续推进。
- `GO` 只允许进入下一 Spike；`REPAIR` 必须列出最小修复和新的验证点；`STOP` 必须停止当前
  方案，不得自动切换为大重构。
- 每个 Spike 都必须包含至少一条真实 Pi 路径；纯 scripted 结果不能 `GO`。
- Spike 代码默认是隔离实验，不接生产路由，不声明产品能力。

#### 跨 Spike 不可变输入

后继 Spike 不能读取兄弟 worktree、未提交文件、旧会话上下文或可变本地路径。每个 Spike
`GO` 后必须先：

1. 将实现、版本化合同、测试和 `spike-result` manifest 提交并推送；
2. 在 manifest 中绑定 Spike ID、完整 source commit、输入/输出合同 digest、Evidence digest、
   Gate 决定和非目标；
3. 由独立只读复核确认 commit、manifest 和 Evidence 一致；
4. 后继 Spike 从该完整 GO commit 新建 worktree，并把它写成新任务合同的 `baseCommit`；
5. 后继任务只从当前 worktree 中已经由基线提交带入的版本化合同和 Adapter 读取前序结果。

前序 Spike 未提交、未推送、未形成 `GO` manifest 或基线不一致时，后继 Spike 不得启动。
这种线性继承只用于实验可重放，不表示前序 Spike 已经产品化或进入正式运行路径。

### 5.2 Spike 1 — Pi Decision Causality

**唯一未知：** 不给标准下一步时，Pi 能否根据最小 Goal State、稳定 Capability View 和
Observation 产生合法且能推进 Goal 的 Decision。

**最小范围：**

- 内存 Goal State；
- 两个同时可见、带前置条件的 Capability；
- 通用 Decision schema；
- 可注入的 Observed Artifact 和 Observation History；
- 真实 Pi session；
- 不连接生产执行边界。

**真实验证状态：**

1. 无 Artifact、Goal 信息完整；
2. 已有合格序列 Artifact；
3. 已有合格极值 Finding；
4. 最近 Observation 为 retryable failure 且 Budget 允许；
5. 最近 Observation 为 terminal failure；
6. 信息不足，需要澄清。

每个状态由评测器预注册“合法 Decision 集”和“能否推进 Goal”的判定，不预注册唯一完整轨迹。

**GO：**

- 6/6 状态均产生合法 Decision；
- 需要执行时，Decision 满足 Capability 前置条件并推进 Goal；
- 已有 Artifact 时不重复无必要前序动作；
- terminal failure 后不继续成功链；
- Provider 请求均无 expected decision；
- Decision 是 Pi 原始输出，不由 Host 补值。

**REPAIR：** Decision schema、Goal State 字段或 Prompt 可通过局部修改修复，且不需要泄漏标准
Decision。

**STOP：** 只有 Host 预填动作、固定 tool choice、单例 View 或新增 Planner 才能稳定工作。

**停止扩建：** 不建设执行 Gateway、Registry、Workflow、UI 或正式 Eval。

### 5.3 Spike 2 — Thin Execution Boundary

**唯一未知：** 能否把业务 Capability 接到现有执行资产，同时保持 Kernel、Resolver、Adapter、
Observation Builder 职责分离。

**最小范围：**

- 从 Spike 1 `GO` commit 继承版本化 Decision schema 和最小循环实现，不读取 Spike 1 的兄弟
  worktree 或临时运行输出；
- 接现有 Trust Kernel 调用位置；
- 两条 Resolver 映射；
- 两个薄 Execution Adapter；
- 一个无推荐动作的 Observation Builder；
- 真实冻结数据；
- 未知 Capability 和前置条件失败夹具。

**真实验证：** Pi 只提交业务 Capability；边界实际调用现有 TOOL-03 / OP-01，并从冻结数据得到
锚点结果；轨迹分别保存业务 ID 和内部 Execution Unit。

**GO：**

- Pi 输入输出均不含 TOOL-03、OP-01、handler 或端点；
- 两个 Capability 均映射并真实执行成功；
- 未知 Capability 和不满足前置条件时 Execution Unit 调用次数为 0；
- 四个逻辑边界的状态、输入输出和测试可独立观察；
- 不存在一个同时选择 Capability、执行、构造下一步和判断完成的 Gateway 类或模块。

**REPAIR：** 仅需修复映射、schema、Adapter 或现有合同适配。

**STOP：** 必须重写 Registry、引入通用 Gateway 平台或把实现编号重新暴露给 Pi。

**停止扩建：** 不新增 Registry 生命周期、通用 Capability Catalog 或 Compute Engine。

### 5.4 Spike 3 — Observation Replan

**唯一未知：** 真实执行结果变化时，Pi 是否会在稳定 Capability View 中改变后续 Decision。

**最小范围：** 从 Spike 2 `GO` commit 线性继承前两个 Spike 的版本化合同与薄执行边界，只增加
`completed / partial / rejected / failed` Observation、Observation History 和 Budget。

**真实验证：** 用真实 Pi 运行读取完成、已有 Artifact、部分结果、可重试失败、终止失败和
rejected；失败由受控 Execution Unit fixture 产生，但 Pi 路径不得 scripted。

**GO：**

- 每个 Decision 都属于当前状态的合法集合并能推进 Goal，或明确停止/澄清；
- 已有 Artifact 时允许跳过不必要读取；
- partial 不进入需要完整输入的 Operator；
- retryable failure 只在 Budget 内产生新 attempt，耗尽后停止；
- terminal failure 和 rejected 不继续成功链；
- Capability 条目、schema 和前置条件在这些 Observation 前后保持不变；若 Budget 消耗触发
  新 View 快照，只允许出现与 Budget 直接对应的可用性变化，并必须记录前后 digest 和
  `change_reason=budget_changed`；
- Observation 中不存在 recommended action。

**REPAIR：** 仅缺少 Goal State、Observation 字段、Budget 表达或局部循环控制。

**STOP：** 只有 Host 注入推荐动作、改写 Capability View 或建设 Workflow Engine 才能分支。

**停止扩建：** 不建设通用 retry 平台、stall detector 或 Durable Workflow。

### 5.5 Spike 4 — Agent Loop Feasibility Evaluation

**唯一未知：** 能否用小型独立轨迹评测区分“Pi 在规划”和“固定 Workflow 得到正确答案”。

**最小用例集：**

- 规范问法 1 个；
- 独立保留的语义等价问法 3 个；
- 不同起始 Artifact 状态 2 个；
- partial、可重试失败、终止失败、rejected 各 1 个；
- 干扰 Observation 2 个；
- 每个用例一条真实 Pi 轨迹，共 12 条；
- 另做规范起始状态连续 3 次，只报告最小重复稳定性。

**每条轨迹判断：** Goal、起始状态、每个 Decision 的合法性和推进性、Observation 后 Decision、
实际 Tool / Operator 调用、终态、最终 Outcome，以及是否出现 expected decision 或 View 偷改。

**GO：**

- 12/12 场景 Outcome 与轨迹约束符合预注册期望；
- 规范起始状态连续 3/3 首次完成；
- 所有成功答案正确；
- 未出现 Host 标准 Decision、固定轨迹比较、字符串路由、oracle 运行依赖、View 偷改或 scripted Pi；
- 输出分别报告 `unique_goal_expression_count`、`unique_initial_state_count`、
  `unique_observation_scenario_count`、`real_pi_trial_count` 和 `scripted_fixture_count`。

**REPAIR：** 失败能归因到局部 Prompt、合同或评测判断，修复不需要改变核心边界。

**STOP：** 只能依靠固定问句、唯一轨迹、Host 固定链、expected decision、oracle 或 scripted model
才能通过。

**停止扩建：** 不扩大到 28 个问题、跨事件泛化、正式 Agent Eval、Certification 或产品化。

### 5.6 Spike 4 后的强制暂停

Spike 1–4 全部 `GO` 只证明最小 Agent Loop 技术路线可行。此时必须停止，提交
`Agent Loop Feasibility Evaluation` 报告，由用户明确选择：

1. `GO PRODUCTIZE`：进入正式 M1 实现和 Candidate 验收；
2. `REPAIR`：针对已知局部问题再做一个有边界 Spike；
3. `STOP`：保留实验结论，不进入产品代码。

不得把 Spike 代码直接接入生产，也不得因 Spike 4 `GO` 自动创建大规模 Epic。

## 6. 完成定义

### 6.1 Spike 合同完成

同时满足以下条件，只能写成“Agent Loop Spike 可行性 `GO`”：

- Spike 1–4 均有独立真实证据和明确 `GO`；
- Host 不再提供标准 Decision 或固定轨迹；
- Goal State 保持最小，没有演化成 Planning Graph；
- Capability View 没有编码 Workflow，也没有被 Observation 偷改；
- Capability 与 Execution Unit 分离，执行边界没有形成新 God Host；
- Observation 确实改变 Pi 后续行为；
- Feasibility Evaluation 能识别固定 Workflow 伪装；
- 没有超出本文最小范围。

这不等于 M1 已实现、正式 Candidate 已通过或生产架构完成。

### 6.2 M1 功能实现完成

只有用户在强制暂停点明确选择 `GO PRODUCTIZE` 后，才允许进入本阶段。完成必须满足：

1. Spike 证明过的边界进入正式运行代码，且没有恢复 Host 固定规划；
2. 首片锚点 J1 使用全新 Candidate 执行 30 次真实运行，达到 `Pass@1 ≥ 27/30`；
3. 30 次按执行顺序形成 10 组三连，达到 `Pass³ ≥ 8/10`；
4. J2–J5 的确定性分支全部通过，零容忍项为 0；
5. Spike 4 的真实 Pi 场景在正式 Candidate 上重新执行并全部通过；
6. 最终任务 Outcome 和答案正确，Tool / Operator 成功不能替代最终成功；
7. 正式评测另行报告 held-out、多 trial、Pass@1、Pass³、成本和 P95；Spike 4 的 12 条轨迹
   不得冒充正式 Eval；
8. 独立评审逐项核对 Decision 合法性、Goal 推进性和 Observation Replan，不只检查最终答案。

现有生产版本、旧 Candidate、旧 30 次重复运行和历史回执不能证明这些条件已经满足。

## 7. J1–J5 与 DG1 对应关系

| 锚点旅程 | 本文验证重点 |
|---|---|
| J1 | Spike 1、2、4：Pi 提议、合法推进、真实执行、最终 Outcome |
| J2 | Spike 2、3：rejected Observation 回到 Pi，未执行下游 |
| J3 | Spike 3、4：partial / failed 后重试、澄清、升级或停止，不生成成功结果 |
| J4 | 复用现有 Finding-to-Answer 与 Guard 测试；不在 Spike 中重建 |
| J5 | Spike 2 的现有 Tool / Operator 接线与确定性夹具；不交给 Pi 重新计算 |

**Spike Gate 不替代 DG1。** Spike 4 后先做产品化决定；只有正式 M1 Candidate 满足首片合同，
才可以形成 DG1 判断。

## 8. 明确不建设

本轮不建设：

- Durable Workflow Engine；
- Common Plan IR 或完整 Planning Graph；
- 通用 Compute Engine；
- 多 Agent；
- 全量 Registry 重构；
- 通用问题分类器、Question Template 平台或 28 问扩展；
- 新的 Claim / Evidence 平台；
- 新的 Renderer、Guard 或回答合同；
- UI、产品交互、发布和部署。

这些能力只有在独立真实需求和新的明确授权出现时才能另立任务，不能作为任一 Spike 的顺手
扩展。

## 9. 与现有文档的关系

- [Agent 目标架构 v1.1](Domeye_Agent_Target_Architecture_v1.1.md)定义长期职责；本文只验证其中最小 Agent Loop。
- [ADR-001](../adr/ADR-001-pi-as-agent-runtime.md)确定 Pi Runtime 方向；本文把“Pi 是否真能提议”作为待验证假设。
- [首个纵向切片锚点合同 v1.0](Domeye_First_Vertical_Slice_Anchor_v1.0.md)继续定义固定数据、J1–J5、DG1 和答案事实。
- [当前代码基线](Domeye_Current_Code_Baseline_2026-08-16.md)只说明历史基线，不证明本文目标已实现。

v1.1 取代 v1.0 合并稿作为唯一 Spike 入口。后续实现、Issue 和评测只引用本文稳定路径；不得
继续并行维护《Agent Loop 五个核心难点与实施计划》或 v1.0 目标效果稿。
