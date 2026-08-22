# Domeye 首个真实 Agent Loop 纵向切片：目标效果与 Spike-first 实施硬边界 v1.0

> 本文只回答一个问题：**去掉 Host 固定规划以后，Pi 能否根据真实 Observation，自己提出
> 下一项 Capability，并完成首个真实纵向任务。**

| 项目 | 内容 |
|---|---|
| 文档版本 | `domeye.first-real-agent-loop-slice/v1.0` |
| 文档性质 | M1 Agent Loop 目标效果、最小合同和 Spike-first 验证计划 |
| 状态 | `Design Note`；不是实现、验证或生产完成证明 |
| 固定功能范围 | 首片锚点中的 `read_bound_metric_series → derive_series_extrema → Answer` |
| 合并输入 | 目标效果硬边界初稿；GitHub PR #62 commit `91d62dd8f6d364e16ef74e352da363a8dfadb0b4` |
| 上位依据 | Agent 目标架构 v1.1、ADR-001、首个纵向切片锚点合同 v1.0 |
| 实验 Gate | 每个 Spike 独立给出 `GO / REPAIR / STOP` |

本文不设计或验收身份、权限、安全策略、DLP、产品交互、发布和部署；这些继续由现有合同
负责。本文也不改变冻结事件、数据、Operator 语义、回答事实和 RRC25 边界。

## 1. 固定范围与目标效果

### 1.1 只验证一条真实任务

当前只处理首片锚点定义的一类目标：读取当前冻结事件的固定前缀可见 IPv4 地址量序列，
计算最低值及首次观测时间，并形成包含首值、末值、最大值和极差的答案。

成功链的领域顺序仍然是：

```text
读取序列 → 观察结果 → 计算极值 → 观察结果 → 完成回答
```

本次要验证的不是“这条顺序是否存在”，而是：

- Host 不提前生成或泄漏下一步；
- Pi 每次只根据当前 Goal State、Capability View 和真实 Observation 提出一项 Decision；
- 当前 Action 返回以前，没有未来领域动作被预先批准或执行；
- success、partial、rejected 和 failed Observation 会导致不同后续行为；
- 最终任务结果仍然正确。

### 1.2 目标运行循环

```text
用户目标
  → Goal State
  → 过滤后的 Capability View
  → Pi 提出一个 Decision
  → Trust Kernel 对 Proposal 做现有准入
  → Capability Gateway 解析到现有 Execution Unit
  → Tool / Operator 执行
  → 结构化 Observation 返回 Pi
  → Pi Replan / Clarify / Stop / Finish
  → 复用现有 Finding → Answer Context → Renderer → Guard → Answer
```

这是滚动循环，不是入口一次性生成完整 Plan / DAG，也不是把固定 Workflow 包装成 Tool
Calling。

### 1.3 目标完成后的功能效果

达到本文目标后，应能观察到：

1. Pi 从“回显 Host 标准对象”变成真正的当前步决策者；
2. Goal State 明确记录已经知道什么、还缺什么、上一轮发生了什么；
3. Pi 只看到业务 Capability，不知道 TOOL-03、OP-01、handler 或底层接口；
4. Observation 不携带下一步答案，但足以支持 Pi 重选；
5. 失败不会机械重复到循环耗尽；
6. Eval 能判断轨迹是否正确，而不只判断最终固定答案是否出现。

## 2. 五个核心难点

### 2.1 下一步决定权归属

Pi 必须产生当前 Decision。Host 负责组装上下文和调度，Trust Kernel 负责现有准入，Gateway
负责实现解析；它们都不得产生业务上的标准下一步。

关键判据：在需要认知决策的路径上，如果删掉 Pi 调用以后 Host 仍能直接生成完全相同的
`capability_id + arguments + next_state`，则目标未实现。

### 2.2 Goal State / Planning State

第一版不用完整 Planning Graph，只需要能支持当前决策的最小状态：

- 当前用户目标；
- 当前 domain binding 引用；
- 已观察 Artifact 引用和摘要；
- 尚未满足的要求；
- 当前 limitation；
- 最近 Observation；
- attempt history；
- 是否终止及 stop reason。

Goal State 是 Pi 的认知输入，不是预先批准的未来步骤集合。

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

Capability View 不得通过动态缩成唯一对象来暗示标准下一步。即使当前只有一个满足前置条件
的 Capability，Pi 仍必须可以选择澄清、停止或完成。

### 2.4 Observation → Replan

Observation 最少区分：

- `completed`；
- `partial`；
- `rejected`；
- `failed`。

最小字段包括：

- `attempt_id`；
- `capability_id`；
- `status`；
- `artifact_ref` 和结果摘要（存在时）；
- `limitations`；
- `reason_code`；
- `retryable`。

Observation 只能描述发生了什么，禁止包含 `recommended_action`、期望 Capability 或下一状态
答案。

### 2.5 Agent Eval

Agent Eval 必须同时判断：

- Goal 是否保持正确；
- 当前 Capability 是否由 Pi 提出；
- Observation 是否真正改变下一步；
- partial / rejected / failed 是否得到正确处理；
- 最终任务是否完成且答案正确；
- 是否出现 Host 固定链、字符串路由、oracle 泄漏或盲重试。

固定脚本可以验证 Gateway 和数据分支，但不能替代真实 Pi 轨迹。

## 3. 必须实现

### 3.1 四个最小合同

#### Goal State

必须是版本化结构，不依赖从完整聊天历史临时猜测。它只保存当前决策所需事实，不保存完整
DAG 或已批准未来动作。

#### Capability View

必须由当前运行态生成，只包含 Pi 可理解的业务能力和前置条件。Capability 与底层 Execution
Unit 必须分离。

#### Decision

Pi 每轮只能返回以下联合类型之一：

```text
invoke_capability(capability_id, arguments)
finish(answer_intent)
clarify(reason_code)
stop(reason_code)
```

Host 可以约束这个结构，但不能预填具体 Decision 值。

#### Observation

必须把真实执行结果、限制和失败语义返回 Pi；不得只更新 Host 内部状态，也不得直接告诉 Pi
下一步应该做什么。

### 3.2 真实 Pi 决策

1. 使用真实 Pi / Provider 路径产生 Decision；scripted model 只能用于单元测试。
2. Provider 请求和 Pi 原始 Decision 必须可审计。
3. Provider 请求中不得出现 Host 计算出的 canonical expected decision。
4. Pi 的 Proposal 必须经过现有 Trust Kernel 准入以后才能执行。
5. 当前 Observation 返回以前，不得生成或准入下一项领域 Action。

“使用 Pi 原生 Tool Calling”是 Spike 要验证的假设，不是先验正确答案。如果它不能在不泄漏
标准 Decision 的条件下工作，应返回 `REPAIR` 或 `STOP`，而不是偷偷恢复 Host 规划。

### 3.3 薄 Capability Gateway

Gateway 只负责：

```text
业务 Capability
  → 现有 Registry / Resolver
  → 现有 TOOL-03 或 OP-01
```

它不得理解自然语言、维护 Goal State、选择下一能力或建设新的全量 Registry。未知 Capability、
不满足前置条件或映射失败时，必须形成 Observation，不能自行改选其他实现。

### 3.4 Observation 驱动的后续行为

至少满足：

- 读取成功后，Pi 才可以提出极值计算；
- 极值成功后，Pi 才可以 `finish`；
- partial 结果不能作为完整输入继续计算；
- retryable failure 只允许在新 attempt 和明确上限内重试；
- terminal failure 或 rejected 必须停止或澄清；
- 无状态变化时不得机械重复同一 Proposal。

### 3.5 正确的任务完成

Tool 成功和 Operator 成功都不等于任务完成。只有 Pi 判断目标满足，并且现有
Finding-to-Answer 链得到正确最终答案，当前 trial 才能记为成功。

本文复用既有 Typed Finding、Answer Context、Renderer 和 Guard，不在本次 Spike 中重建或
扩展它们。

### 3.6 完整轨迹证据

每个真实 trial 至少记录并绑定：

- 输入 Goal State；
- 当轮 Capability View；
- 发给 Provider 的请求摘要；
- Pi 原始 Decision；
- Gateway 解析和实际 Execution Unit；
- Action 结果与 Observation；
- 下一轮 Goal State；
- 最终 `finish / clarify / stop`；
- Tool / Operator 调用计数；
- 最终任务结果。

证据必须能回答“下一步是谁决定的、Pi 看到了什么、为什么状态发生变化”，不能只有最终答案。

## 4. 明确禁止

以下任一项出现，当前 Spike 不得判为 `GO`：

1. **禁止 Host 计算标准 Decision。** 不得用 `requiredDecisionForState` 或等价函数生成完整
   Proposal 后要求模型照抄。
2. **禁止泄漏期望动作。** Prompt、Tool schema、Capability View 和 Observation 中不得包含
   expected decision、expected capability、expected arguments 或下一状态答案。
3. **禁止固定 tool choice 指定业务动作。** 可以强制通用 Decision schema，不能强制某个具体
   Capability。
4. **禁止精确字符串路由。** 不得用 question → template / intent → 固定 Capability 链。
5. **禁止预生成 Plan / DAG。** 当前结果回来前不得生成、批准或持久化未来领域动作。
6. **禁止用 Capability View 偷渡工作流。** 不得仅靠把 View 缩成单一标准动作来编码下一步。
7. **禁止 Observation 给答案。** 不得增加 `recommended_action` 或等价提示。
8. **禁止向 Pi 暴露实现。** 不得暴露 TOOL-03、OP-01、handler、数据库接口或具体实现选择。
9. **禁止失败后盲重试。** 没有新 attempt、重试语义或状态变化时不得重复同一 Proposal。
10. **禁止 oracle 进入运行路径。** oracle、标准 Proposal 和标准答案只能供独立 Eval 比较。
11. **禁止 scripted 轨迹冒充真实 Pi。** scripted fixture 必须与真实 Provider trial 分栏报告。
12. **禁止把重复次数冒充场景覆盖。** 同一输入重复运行只能证明稳定性。
13. **禁止为了通过 Spike 扩建平台。** 不得新增通用 Planner、Common Plan IR、完整 Workflow
    Engine、通用 Compute Engine、多 Agent 或全量 Registry 重构。
14. **禁止越级宣告。** 任一 Spike `GO` 只说明该假设成立，不等于 M1 已实现、已验证或已上线。

## 5. 验收硬门

### 5.1 Spike 通用规则

- 每个 Spike 使用独立任务、独立 worktree 和独立证据；默认 timebox 为一个工作日。
- 超出 timebox 前必须先给出 `GO / REPAIR / STOP`，不得靠扩大范围继续推进。
- `GO` 只允许进入下一 Spike；`REPAIR` 必须列出最小修复和新的验证点；`STOP` 必须停止该
  方案，不得自动切换为大重构。
- 每个 Spike 都必须包含至少一条真实 Pi 路径；纯 scripted 结果不能 `GO`。
- Spike 代码默认是隔离实验，不接生产路由，不声明产品能力。

### 5.2 Spike 1 — Pi Decision Causality

**唯一未知：** 不给标准下一步时，Pi 能否仅依据 Goal State、两个 Capability 和 Observation
产生正确当前 Decision。

**最小范围：**

- 内存 Goal State；
- 两个 Capability schema；
- 通用 Decision schema；
- 可注入的 completed / failed Observation；
- 真实 Pi session；
- 不连接生产 Gateway。

**真实验证：** 使用三个全新 session，捕获完整 Provider 请求和原始 Decision；分别验证初始
状态、读取成功状态和读取失败状态。

**GO：**

- 3/3 session 的 Provider 请求均无 expected decision；
- 初始状态提出 `read_bound_metric_series`；
- 读取成功后提出 `derive_series_extrema`；
- 读取失败后不提出极值计算；
- Decision 是 Pi 原始输出，不由 Host 补值。

**REPAIR：** Decision schema、Goal State 字段或 Prompt 可通过局部修改修复，且不需要 Host
泄漏下一步。

**STOP：** 只有 Host 预填动作、固定 tool choice 或新增 Planner 才能稳定工作。

**停止扩建：** 不建设 Gateway、Registry、Workflow、UI 或正式 Eval。

### 5.3 Spike 2 — Capability Gateway

**唯一未知：** 能否用一个薄 Gateway 把业务 Capability 接到现有执行资产，而不把实现细节
暴露给 Pi。

**最小范围：**

- 复用 Spike 1 Decision；
- 接现有 Trust Kernel 调用位置；
- 两条映射：`read_bound_metric_series → TOOL-03`、
  `derive_series_extrema → OP-01`；
- 真实冻结数据；
- 未知 Capability 和前置条件失败夹具。

**真实验证：** Pi 只提交业务 Capability；Gateway 实际调用现有 TOOL-03 / OP-01，并从冻结
数据得到锚点 oracle；轨迹分别保存业务 ID 和内部 Execution Unit。

**GO：**

- Pi 输入输出均不含 TOOL-03、OP-01、handler 或端点；
- 两个 Capability 均映射并真实执行成功；
- 未知 Capability 和不满足前置条件时 Execution Unit 调用次数为 0；
- Gateway 没有自然语言路由和下一步选择逻辑。

**REPAIR：** 仅需修复映射、schema 或现有合同适配。

**STOP：** 必须重写 Registry、引入通用 Gateway 平台或把实现编号重新暴露给 Pi。

**停止扩建：** 不新增 Registry 生命周期、通用 Capability Catalog 或 Compute Engine。

### 5.4 Spike 3 — Observation Replan

**唯一未知：** 真实执行结果变化时，Pi 是否会改变后续 Decision。

**最小范围：** 复用前两个 Spike，只增加 `completed / partial / rejected / failed` Observation
及最小 attempt history。

**真实验证：** 用真实 Pi 分别运行：读取完成、部分结果、可重试失败、终止失败和 rejected；
失败由受控 Execution Unit fixture 产生，但 Pi 路径不得 scripted。

**GO：**

- completed 后进入正确下一能力；
- partial 不进入 Operator；
- retryable failure 只在新 attempt 和一次明确上限内重试，之后停止；
- terminal failure 和 rejected 不重试；
- 五类 Observation 至少产生三种不同后续 Decision；
- Observation 中不存在 recommended action。

**REPAIR：** 仅缺少 Goal State、Observation 字段或局部循环控制。

**STOP：** 只有 Host 注入推荐动作、固定失败状态机或 Workflow Engine 才能分支。

**停止扩建：** 不建设通用 retry 平台、stall detector 或 Durable Workflow。

### 5.5 Spike 4 — Agent Trajectory Eval

**唯一未知：** 能否用独立轨迹指标区分“Pi 在规划”和“固定流程碰巧得到正确答案”。

**最小用例集：**

- 规范问法 1 个；
- 独立保留的语义等价问法 3 个；
- partial、可重试失败、终止失败、rejected 各 1 个；
- 干扰 Goal State / Observation 2 个；
- 每个用例一条真实 Pi 轨迹，共 10 条；
- 另做规范问法连续 3 次，仅报告最小重复稳定性。

**每条轨迹判断：** Goal、首个 Decision、Observation 后 Decision、Tool / Operator 调用、终态、
最终任务结果，以及是否出现 expected decision 泄漏。

**GO：**

- 10/10 场景轨迹符合预注册期望；
- 规范问法连续 3/3 首次完成；
- 所有成功答案正确；
- 未出现 Host 标准 Decision、字符串路由、oracle 运行依赖或 scripted Pi；
- 输出分别报告 `unique_goal_expression_count`、`unique_observation_scenario_count`、
  `real_pi_trial_count` 和 `scripted_fixture_count`。

**REPAIR：** 失败能归因到局部 Prompt、合同或 Eval 判断，修复不需要改变核心边界。

**STOP：** 只能依靠固定问句、Host 固定链、expected decision、oracle 或 scripted model 才能通过。

**停止扩建：** 不扩大到 28 个问题、跨事件泛化、通用 Agent Certification 或产品化。

### 5.6 Spike 4 后的强制暂停

Spike 1–4 全部 `GO` 只证明最小 Agent Loop 技术路线可行。此时必须停止，提交 Spike 报告，
由用户明确选择：

1. `GO PRODUCTIZE`：进入正式 M1 实现和 Candidate 验收；
2. `REPAIR`：针对已知局部问题再做一个有边界 Spike；
3. `STOP`：保留实验结论，不进入产品代码。

不得把 Spike 代码直接接入生产，也不得因 Spike 4 `GO` 自动创建大规模 Epic。

## 6. 完成定义

### 6.1 Spike 计划完成

同时满足以下条件，只能写成“Agent Loop Spike 可行性 `GO`”：

- Spike 1–4 均有独立真实证据和明确 `GO`；
- Host 不再提供标准 Decision；
- Capability 与 Execution Unit 分离；
- Observation 确实改变 Pi 后续行为；
- 轨迹 Eval 能识别固定流程伪装；
- 没有超出本文最小范围。

这不等于 M1 已实现、正式 Candidate 已通过或生产架构完成。

### 6.2 M1 功能实现完成

只有用户在强制暂停点明确选择 `GO PRODUCTIZE` 后，才允许进入本阶段。完成必须满足：

1. Spike 证明过的边界进入正式运行代码，且没有恢复 Host 固定规划；
2. 首片锚点 J1 使用全新 Candidate 执行 30 次真实运行，达到 `Pass@1 ≥ 27/30`；
3. 30 次按执行顺序形成 10 组三连，达到 `Pass³ ≥ 8/10`；
4. J2–J5 的确定性分支全部通过，零容忍项为 0；
5. Spike 4 的真实 Pi 场景在正式 Candidate 上重新执行并全部通过；
6. 最终任务答案正确，Tool / Operator 成功不能替代最终成功；
7. 独立评审逐项核对 Decision 因果和 Observation Replan，不只检查最终答案。

现有生产版本、旧 Candidate、旧 30 次重复运行和历史回执不能证明这些条件已经满足。

## 7. J1–J5 对应关系

| 锚点旅程 | 本文验证重点 |
|---|---|
| J1 | Spike 1、2、4：Pi 提议、真实执行、最终任务完成 |
| J2 | Spike 2、3：rejected Observation 回到 Pi，未执行下游 |
| J3 | Spike 3、4：partial / failed 后重试、澄清或停止，不生成成功结果 |
| J4 | 复用现有 Finding-to-Answer 与 Guard 测试；不在 Spike 中重建 |
| J5 | Spike 2 的现有 Tool / Operator 接线与确定性夹具；不交给 Pi 重新计算 |

本文的 Spike Gate 不替代 DG1。Spike 4 后先做产品化决定；只有正式 M1 Candidate 满足首片
合同，才可以形成 DG1 判断。

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

PR #62 中的《Agent Loop 五个核心难点与实施计划》内容已经合并进本文。该原文件不应再作为
第二份并行权威；后续实现、Issue 和评测应只引用本文稳定路径。
