# P1 页面能力语义覆盖加固 Plan（分阶段计划）

版本：`p1-plan-v1.1-page-capability-coverage`
对应目标文档：`Task-Spec-最终验收文档.md`
状态：改进路线合同，尚未表示任何阶段完成

## 一、计划定位

本计划不是固定任务清单，只定义阶段入口、前端与使用效果、后端与评测效果、出口和边界。它在现有
P1 “开放用户目标→封闭确定执行”合同上，专门补齐“页面已经能回答，但 Agent 因语义、Grounding、
算子或回答装配缺口而答不出”的问题。

路线从 S0 至 S4：

```text
S0 页面用户结果与产品真值冻结
 ↓
S1 确定性时序算子与地址族 Grounding 闭合
 ↓
S2 问题探针与单轮多表达覆盖
 ↓
S3 多轮、混合边界与状态事务
 ↓
S4 同候选联合验收与持续回放入口
```

路线可以调整，阶段可在风险可控时交叠，Tool 可以拆分或合并，算子可以组合或重用，模型、Schema、缓存和
UI 实现可以替换；但不能改变最终用户结果、事实身份、证据、权限、状态、缺失语义和 RRC25 边界。

## 二、自由度与不可变约束

### 2.1 允许自由发挥

- 问题探针 Agent 使用何种模型、多少 persona、如何采样和去重；
- UserGoalPlan 如何表示对象、范围、地址族、人口和分析方式；
- 通用时序变化概括算子的名称、内部分解、缓存和性能优化；
- 一个 Tool 支撑多个 Capability，或一个用户结果由多个 Tool/算子组合；
- 探索池的问题数量、普通能力的采样深度和高风险能力的额外采样比例；
- 同步、流式或异步回答，以及前端卡片、证据展开和状态反馈的视觉形式；
- 根据真实失败分布调整阶段内顺序和影响回归范围。

### 2.2 不允许自由改变

- 泛指 IP 默认同时回答 IPv4 和 IPv6；
- 固定 cohort 是主答案，新出现前缀是独立补充，两者不合并；
- 事件内时序概括与历史/跨事件/正式趋势制品分离；
- 用户原目标不得被固定规范问题改成另一种分析方式；
- 问题探针 Agent 与独立产品语义 Reviewer 的角色、输入和权限分离；
- 开放 UserGoalPlan 与封闭 GroundingPlan 的职责分离；
- 事件、incident、publication、revision、RRC25、窗口、data through 和 finality 身份门；
- 事实只能来自合法只读 Tool 结果或登记确定性 DerivedFact；
- null、unknown、unavailable、not configured、0 的区别和 IPv4/IPv6 单位边界；
- 原因、责任、全国中断、真实用户影响、恢复、外部数据和 P2-P5 边界；
- 不能因为实现困难或模型表现不佳而降低最终验收门。

## 三、共同入口

S0 开始前必须能复核：

- P0 v1.3 Capability Discovery Ledger、unknown ledger、Oracle seed、35 案例与 P1 入口回执；
- 当前页面与 API 绑定的事件、publication、revision、RRC25、capabilities 和 data through；
- 页面可见结果、已发布 API、可确定推导结果和越界结果的分层证据；
- “IP 地址变化情况”和“IP 地址变化趋势”的当前原始 Agent API 轨迹或可重现失败记录；
- 旧候选的 implemented、tested 或 accepted 状态不被当作新 Task Spec 修订已通过。

页面用户结果统一使用以下稳定 ID，具体 Tool 和 UI 可以变化：

| ID | 用户结果 |
|---|---|
| `PCO-01` | 事件身份、观测窗口、数据截止和结束状态 |
| `PCO-02` | 概览、峰值、当前状态和事实时间线 |
| `PCO-03` | 固定 cohort 的 IPv4/IPv6 可见地址规模变化 |
| `PCO-04` | 新出现 IPv4/IPv6 前缀及当前可见规模 |
| `PCO-05` | 受影响 AS 范围、列表、筛选和指定 ASN 详情 |
| `PCO-06` | 路径观测关联和真实路径样本 |
| `PCO-07` | 指标定义、单位、统计人口和缺失语义 |
| `PCO-08` | 证据身份、完整度和 RRC25 证明边界 |

## 四、阶段路线

### S0：页面用户结果与产品真值冻结

#### 入口

共同入口证据可复核；当前页面、API 与历史候选的运行身份不混用；团队接受“页面控件不等于 Agent
用户结果”作为盘点原则。

阶段到期映射：

- 用户与前端要求：`P1-UX-01`、`P1-UX-04`、`P1-UX-05`、`P1-UX-09`；
- 确定性合同：P1-CTR-01 至 P1-CTR-07、`P1-CTR-09`、`P1-CTR-17`、`P1-CTR-18`、
  P1-CTR-20 至 P1-CTR-24；
- 联合场景：无新增到期项。

#### 前端与使用效果

本阶段可以只有产品原型或结果卡片合同，但必须能演示泛指 IP 回答的信息分层：固定 cohort 是主答案，
IPv4 与 IPv6 单位分开，新出现前缀是独立补充，数据截止不写成恢复。

#### 后端与评测效果

冻结 `PCO-01`至`PCO-08` Page Capability Outcome Map，每项映射页面/API、输入输出、身份、单位、缺失语义、
确定性推导、限制、候选 Tool/算子和采用状态。冻结泛指 IP、事件内走势、正式趋势产品、固定 cohort 和
新出现前缀的产品语义。

同时形成问题探针 Agent 的只读权限、输入、输出、会话隔离和禁止事项合同，以及独立 Reviewer 的盲审
真值格式。不要求当前实现已经通过。

#### 出口

- 八个 PCO 全部有版本化结果合同和证据指针；
- 两个原始 IP 问题的预期目标、必答点、禁止断言和回答性已由独立 Reviewer 先验确认；
- 问题探针与 Reviewer 不是同一判定角色；
- 未关闭真值分歧有 owner 和下一验证条件。

#### 边界

S0 不实现 Tool、算子、Planner 或前端，不把一张页面截图当成能力真值，不从当前 IR publication 静默外推其他事件，
不因为原有 Tool 存在就宣称用户结果已闭合。

### S1：确定性时序算子与地址族 Grounding 闭合

#### 入口

S0 出口成立；`PCO-03`、`PCO-04` 的原始 series、轨道定义、单位、窗口、null 语义和 publication 身份可回放；
可以在不依赖开放语义模型的情况下构造受控 GroundingPlan。

阶段到期映射：

- 用户与前端要求：`P1-UX-01`、`P1-UX-03`、`P1-UX-04`、`P1-UX-05`、`P1-UX-10`、
  `P1-UX-11`、`P1-UX-12`；
- 确定性合同：P1-CTR-04 至 P1-CTR-10、`P1-CTR-16`、P1-CTR-17 至 P1-CTR-21、`P1-CTR-24`；
- 联合场景：`P1-SCE-01`、`P1-SCE-03`、`P1-SCE-10`、`P1-SCE-13`、`P1-SCE-14`、`P1-SCE-15`、
  `P1-SCE-17`。本阶段的 `P1-SCE-10` 只验收地址时序切片的 null、全 null、空轨、单位错误和身份冲突，
  不表示该场景中其他页面能力已闭合。其他页面用户结果可以探索，但不因 S1 的一条垂直切片提前宣告通过。

#### 前端与使用效果

在受控问题或专用测试入口中，用户可以看到泛指 IP 回答同时呈现 IPv4、IPv6 与新前缀补充，并显示各自单位、
时点、证据和数据截止边界。本阶段不要求所有自然语言表达已经稳定。

#### 后端与评测效果

保留通用 series 读取 Tool，建立或重构可复用的时序变化概括算子。算子输出至少闭合首末值、极值与时点、
净变化、观测/缺失点和原单位。只在指标语义合同允许时输出从相关极值到数据截止的变化，不把累计
新前缀等指标说成“下降后回升”。是否引入更细分段可自由决定，但要有版本化规则和 Oracle。

Grounding 必须对 `ipv4`、`ipv6`、`both`、泛指 IP 默认和真正需要澄清的情形显式建矩阵，消除“非 IPv6
则 IPv4”的隐式分支。回答装配必须使用本轮分析方式，不得把变化概况重写为最大下降。

#### 出口

- “IP 地址变化情况”和“IP 地址变化趋势”在受控 UserGoalPlan 下产生正确、可执行、有证据的 Grounding；
- IPv4、IPv6、both、显式排除新前缀、只看新前缀、null、全 null、空轨、单位错误和身份冲突有 Oracle；
- 事件内时序概括不进入正式历史趋势的不可用分支；
- 失败执行不发布事实、不将空值解释为 0、不提交状态。

#### 边界

S1 不为“情况”、“趋势”、“走势”等同义词分别新建 Tool，不要求模型生成曲线故事，不从最低点后回升推出
恢复，不做跨事件、历史或国家间趋势比较。

### S2：问题探针与单轮多表达覆盖

#### 入口

S1 的确定性事实链可信；八类 PCO 均有可执行或明确不执行边界；问题探针 Agent 可使用隔离会话调用
同一候选的黑盒 API，独立 Reviewer 可以在不看 Planner 输出的情况下产生先验真值。

阶段到期映射：

- 用户与前端要求：`P1-UX-02`、`P1-UX-03`、`P1-UX-05`、`P1-UX-06`、`P1-UX-08`、
  `P1-UX-11`、`P1-UX-12`；
- 确定性合同：P1-CTR-08 至 P1-CTR-13、P1-CTR-17 至 P1-CTR-24；
- 联合场景：P1-SCE-01 至 P1-SCE-06、`P1-SCE-08`、`P1-SCE-10`、`P1-SCE-11`、
  P1-SCE-13 至 P1-SCE-17。

#### 前端与使用效果

用户可以使用标准、口语、泛指、错序和单轮复合表达询问页面已支持的结果。可回答部分给出事实和证据，越界、
缺失或真正歧义部分分别降级，不整句拒答。同一用户结果的不同表达不得出现地址族、人口、时间范围或回答性
的无理由漂移。

#### 后端与评测效果

问题探针从 PCO ×表达类型覆盖矩阵生成候选，优先加深地址规模、时序、单位、null、边界混合和易静默缩窄问题。
具体数量可根据重复率和风险调整；初期可以以约 50—60 个探索候选、20—30 个独立审核后的冻结案例为建议规模，
但验收看覆盖维度、语义真值和原始轨迹，不看凑数。

每个黑盒问题保留原问题、会话初始状态、候选身份、UserGoalPlan、GroundingPlan、Tool/算子回执、evidence refs、
回答、状态提案/提交和错误码。Reviewer 先产生用户目标、实体、歧义、回答性、必答点和禁止断言，再做 Semantic Diff。

#### 出口

- 八类 PCO 都有标准、口语、泛指、复合/边界或异常等按风险选取的候选，不存在整类空白；
- 冻结的页面可回答表达不误 `unsupported`，不静默缩窄，不使用固定问题改写分析方式；
- 问题探针不能修改真值或将自己的解释写成 PASS；
- 失败已按 Planner、Grounding、Capability、Tool、算子、回答、Evidence、Policy 或 State 分层归因。

#### 边界

S2 不训练专用意图分类器作为必选方案，不把每个同义表达写成一条宿主规则，不让探针自问自判，不将生成问题
直接并入黄金问题。语义保持开放，执行仍只能使用登记且通过 Oracle 的只读能力。

### S3：多轮、混合边界与状态事务

#### 入口

S2 单轮覆盖闭合；问题探针能创建隔离的多轮会话；EvidenceState、DialogState、候选状态和提交回执可区分；
支持取消、超时、revision 漂移和事件切换的受控故障注入。

阶段到期映射：

- 用户与前端要求：P1-UX-07 至 P1-UX-12；
- 确定性合同：`P1-CTR-02`、P1-CTR-10 至 P1-CTR-16、`P1-CTR-18`、P1-CTR-20 至 P1-CTR-24；
- 联合场景：P1-SCE-07 至 P1-SCE-17。

#### 前端与使用效果

用户可以连续追问“那 IPv6 呢”、“把新出现前缀也带上”、“不看新增，只看原来的”、“这能说明用户断网吗”。
已验证地址族、人口和指标可以自然继承或覆盖，越界追问不删除前面的可观测事实，失败不会劫持下一个完整问题。

#### 后端与评测效果

问题探针执行省略、指代、显式修正、否定、复合支持+越界、事件切换、连接失败和到期旅程。每轮保留 state before、
proposal、commit/rollback 和 state after，并证明事实证据不来自聊天历史。

对混合问题逐子目标验证：可回答节点有对应 Tool/算子和证据，越界节点为零执行且保留原意，共享身份冲突时
整轮回滚。同一 turn id 在事件 generation 变化后不能返回旧 publication 结果。

#### 出口

- 页面能力的省略、修正、否定和混合边界旅程全部有同候选轨迹和独立语义结论；
- 地址族、固定/新前缀人口、ASN、指标和事件只在符合合同时继承或覆盖；
- unsupported、invalid data、Tool 失败、取消、超时、revision 漂移和事件切换的状态回滚闭合；
- 不存在 stale pending clarification、跨事件地址族/ASN 串扰或失败计划污染。

#### 边界

S3 不用超长聊天历史代替状态合同，不把上一轮模型文本当作证据，不让问题探针写入真实用户会话，不把 P1 多意图
升级为 P2 开放调查。

### S4：同候选联合验收与持续回放入口

#### 入口

S0 至 S3 的出口回执闭合且无未处理阻断；候选代码、配置、模型、Prompt、Schema、Capability Catalog、Tool、算子、
Oracle、页面/API 和数据 publication 能唯一绑定；探索池、冻结回归池和真实失败回放池均有版本身份。

阶段到期映射：

- 用户与前端要求：P1-UX-01 至 P1-UX-12；
- 确定性合同：P1-CTR-01 至 P1-CTR-24；
- 联合场景：P1-SCE-01 至 P1-SCE-17。

#### 前端与使用效果

在桌面和窄屏真实浏览器中，用户可以使用页面能力覆盖矩阵中的不同表达完成事件概览、时序、IP 地址变化、
新前缀、ASN、路径、指标和证据边界问答，并完成多轮修正、局部回答、事件切换和失败恢复。关键回答显示
事件身份、单位、人口、证据、数据截止和未知边界。

#### 后端与评测效果

同一候选上执行 P0 v1.3 35 案例、八类 PCO 覆盖矩阵、冻结语义变体、IP 高风险原问题、多轮、边界、
异常、权限、状态和故障注入旅程。每个浏览器回答可回到 Agent API、UserGoalPlan、GroundingPlan、Tool/算子、
evidence refs 和 state receipt。

独立 Reviewer 在当前候选上重跑受影响的产品语义验收，给出 PASS 或阻断。探针只提供问题与原始轨迹，不提供最终真值。

#### 出口

- `Task-Spec-最终验收文档.md` 全部要求有同候选证据；
- 泛指 IP 和事件内 IP 趋势的误拒绝、静默缩窄、人口混合和目标改写为零；
- 独立产品语义 Reviewer 给出 PASS 且没有未关闭阻断；
- 阶段回执、manifest、制品哈希、候选身份和浏览器/API 轨迹一致；
- 真实失败回放池有新样本入口、影响评估和案例晋级机制。

#### 边界

Hook 结构检查通过不等于候选验收；探针问题数、单元测试、模型离线分数、HTTP 200、截图或一次演示都不能代替同候选
联合证据。S4 通过只能表述为“P1 页面能力语义覆盖加固候选通过”，不表示 P2、RCA、已合并、已部署或生产验证。

## 五、问题探针运行合同

### 5.1 输入

问题探针只读取：

- 已审定的 `PCO-01`至`PCO-08` 用户结果和 RRC25 边界；
- 本轮指定的 event/publication/revision 和测试候选身份；
- 要覆盖的表达类型、persona、单轮/多轮和故障模式；
- 已审定的实体字典和越界类别，但不读取当前 Planner 的目标标签偏好作为出题模板。

### 5.2 输出

每个候选问题至少保留：

```json
{
  "case_id": "...",
  "page_outcome_ids": ["PCO-03", "PCO-04"],
  "expression_type": "generic_or_colloquial_or_followup_or_boundary",
  "persona": "...",
  "conversation_seed": [],
  "question": "...",
  "candidate_id": "...",
  "event_identity": {},
  "raw_agent_receipt_ref": "...",
  "review_status": "candidate_or_frozen_or_rejected"
}
```

原始 Agent 回执必须独立保存，不得只保留通过计数、问题和最终文本。
`case_id` 必须唯一，全部案例 `page_outcome_ids` 的并集必须等于该制品声明的 PCO 覆盖集；不得只在 wrapper
声明覆盖八类结果，而实际案例仍留有整类空白。

### 5.3 禁止权限

问题探针不得：

- 修改产品真值、冻结案例、合同、Prompt、实现、状态或候选身份；
- 调用生产写接口、外部数据源或 P2-P5 能力；
- 使用自己生成的预期答案宣告系统 PASS；
- 为了提高通过率删除反例、改写用户问题或继承前一隔离会话的状态。

## 六、路线调整机制

发生以下情况时可以改 Plan：

- 页面或 API 发布了新的用户结果或原有结果语义变更；
- Oracle 证明 Tool/算子边界不合理，需要拆分、合并或替换实现；
- 探针失败分布表明某个表达类型或用户结果风险更高；
- 新的真实用户失败进入回放池；
- 性能、状态或权限风险要求提前建设某项门禁。

调整前必须记录触发证据、受影响的 PCO/要求编号、原路线、替代路线和定向回归范围。可以改 Plan，但不能静默
修改 Task Spec 来消除已暴露的产品偏离。如果真实产品目标变化，必须建立新 Task Spec revision 并保留旧版。

未达到当前阶段出口时不得把阶段标记为完成。可以并行探索不依赖当前出口的方案，但下游验收不能借用尚未成立的
上游结论。不因 S1 的一条垂直切片提前宣告通过其他 PCO 或整个 P1。

## 七、Alignment Hook 与阶段回执

每个阶段结束必须调用 P1 Alignment Hook。只检查设计合同结构时可以运行：

```bash
python3 .codex/hooks/country_outage_agent_p1_alignment.py --stage S0
python3 .codex/hooks/country_outage_agent_p1_alignment.py --stage S4
```

无 evidence manifest 的调用只证明文档、阶段映射和任务边界没有已知结构偏离，不得用于宣告阶段出口。

真实阶段结束必须传入带制品哈希的阶段回执：

```bash
python3 .codex/hooks/country_outage_agent_p1_alignment.py \
  --stage S0 \
  --evidence-manifest evaluation/country-outage/p1-page-coverage/stage-receipts/S0.json
```

阶段回执使用 `country_outage_p1_page_coverage_stage_receipt_v1` schema version，至少包含：

```json
{
  "schema_version": "country_outage_p1_page_coverage_stage_receipt_v1",
  "stage": "S0",
  "task_spec_version": "p1-task-spec-v1.1-page-capability-coverage",
  "plan_version": "p1-plan-v1.1-page-capability-coverage",
  "candidate_id": "design-contract-or-runtime-candidate-id",
  "status": "PASS",
  "requirement_ids": [],
  "page_outcome_ids": [],
  "artifacts": [
    {"kind": "page_capability_outcome_map", "path": "...", "sha256": "..."}
  ],
  "semantic_review": {
    "role_separated": true,
    "verdict": "PASS",
    "receipt_ref": "..."
  },
  "unresolved_blockers": [],
  "prohibited_claims": {
    "p2_complete": false,
    "rca_complete": false,
    "deployed": false,
    "production_verified": false
  }
}
```

阶段必需制品类型：

| 阶段 | 必需 `artifact.kind` |
|---|---|
| S0 | `page_capability_outcome_map`、`product_semantic_truth`、`question_explorer_contract` |
| S1 | `deterministic_series_operator_oracle`、`ip_question_execution_trace`、`independent_semantic_review` |
| S2 | `question_explorer_results`、`single_turn_semantic_diff`、`independent_semantic_review` |
| S3 | `multiturn_state_trace`、`mixed_boundary_trace`、`failure_rollback_trace`、`independent_semantic_review` |
| S4 | `same_candidate_manifest`、`browser_api_tool_evidence_state_trace`、`independent_semantic_review`、`unclosed_unknowns` |

制品 `path` 必须是仓库内相对路径，并指向 JSON 证据回执。每个 JSON 证据回执至少包含
`schema_version`、`artifact_kind`、`stage`、`candidate_id`、`status=PASS` 和非空 `evidence_refs`；
`artifact_kind`、`stage` 和 `candidate_id` 必须与阶段回执一致。同一阶段回执内的 `artifact.kind` 和规范化 `path`
必须各自唯一，禁止用同类多制品的后写覆盖改变 Reviewer 或同候选交叉校验对象。

`evidence_refs` 不得是 `claim:anything` 一类自报字符串，每项必须是
`{kind, path, sha256}` 对象。`path` 指向证据根目录内的 JSON 原始回执，原始回执至少包含
`schema_version`、`evidence_kind`、`candidate_id`、`stage`、`run_id` 和带时区的 `captured_at`。Hook 必须读取原始回执并
校验内容身份，不能只校验路径和哈希。

`sha256` 必须与实际 JSON 文件匹配；`semantic_review.receipt_ref` 必须指向已列入 `artifacts` 且验签的
Reviewer 回执。Reviewer 回执还必须包含 `reviewer_role=product_semantic_truth_reviewer`、
`independent_from_question_explorer=true`、`verdict=PASS` 和非空 `reviewed_items`。`page_capability_outcome_map` 和
`question_explorer_results` 必须声明完整 `PCO-01`至`PCO-08`；`ip_question_execution_trace` 必须包含两个冻结 IP 原问题。

Reviewer 回执还必须绑定不同的 `case_author_actor_id/run_id` 和 `reviewer_actor_id/run_id`，两个已验签的角色运行
回执必须声明禁止权限和不同的 orchestrator 回执身份。被审输入必须绑定出题者运行，先验真值必须绑定 Reviewer
独立运行；先验真值、被审输入和系统输出必须分别验签，且先验真值的原始 `captured_at` 早于系统输出暴露时间。
每个先验真值项至少包含 `case_id`、`expected_goals`、`expected_entities`、`answerability`、
`required_answer_points` 和 `forbidden_claims`；每个 `reviewed_item` 至少包含 `case_id`、`verdict` 和
`semantic_diff`，且必须与先验真值案例集对齐。Reviewer 总体 `verdict=PASS` 时，不得存在任一
`reviewed_item.verdict=FAIL`；候选题被拒绝可以通过独立的 disposition 记录，但失败案例不得被上卷成阶段 PASS。
S2 问题探针结果必须绑定 `question_explorer_actor_id/run_id`、规范化案例集哈希和原始 Agent 回执；Reviewer 的
`reviewed_input` 必须引用当前该探针回执与案例集哈希，不得由 Reviewer 运行伪装探针输入。
S4 的 `same_candidate_manifest` 中每个组件身份均为 `{identity, evidence_kind, sha256}` 对象，必须绑定独立原始回执；
原始回执中的 `component` 和 `identity` 必须与 manifest 声明逐项相同，再由规范化组件身份派生
`candidate_identity_sha256` 并绑定 `component_manifest`；浏览器、API、UserGoalPlan、GroundingPlan、Tool、EvidenceState 和
DialogState 原始回执必须共享同一 `candidate_id`、`candidate_identity_sha256`、`run_id` 和 `journey_id`。
`unclosed_unknowns.unknowns` 的每项至少包含 `unknown_id`、`subject`、`blocking`、`next_validation` 和 `owner`；
`blocking_count` 必须由 `blocking=true` 的条目数计算，阶段 PASS 时计算值必须为 0。

Hook 必须检查 Task Spec/Plan 版本、stage、候选身份、到期 requirement IDs、阶段必需制品类型、文件 SHA-256、
PCO 覆盖、Reviewer 角色分离、未关闭阻断和禁止越级声明。哈希不匹配、制品不存在、Reviewer 不独立、存在 blocker 或
宣称 P2/RCA/部署/生产验证均必须阻断阶段通过。

Hook 产生的结构结论还必须结合实际证据和独立产品语义审核。最终答复包含：

```text
国家中断 Agent P1 最终验收回检：Sx 一致 / 已修正 / 存在待处理偏离
```

## 八、计划完成定义

只有 S4 出口全部成立，才能称为“P1 页面能力语义覆盖加固候选已通过验收”。该结论不表示已合并、已部署或
生产验证，也不表示已具备 P2 组合调查、P3 假设、P4 多源证据或 P5 RCA。
