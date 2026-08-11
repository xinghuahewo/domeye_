# P2-S1 国家中断组合式调查执行单元设计分阶段计划

版本：`country-outage-agent-p2-s1-execution-unit-design-plan-v1`

状态：设计阶段计划，未进入实现

目标版本：`country-outage-agent-p2-s1-execution-unit-design-contract-v1`

冻结日期：2026-08-12

## 一、计划定位

本计划把 28 个保留问题所需的执行单元设计拆为七个顺序 Gate。每个 Gate 只接收已通过的
前序候选，输出新的不可变设计制品与阶段回执；任一 Gate 失败时，不提交当前候选，也不得
提前进入后续阶段。

本计划只设计功能原子的 Tool、Operator、InvestigationPlan、ResultSet、Evidence Graph、
运行时提交一致性和验收 Oracle，不实现新 Tool、Operator、API、页面、数据库、导出服务或
生产运行时。

回答模型顺序冻结为 `sol_then_ds_execution_order`：`gpt-5.6-sol` 先生成教师计划与证据化
参考，DS 后生成最终候选回答。两个模型必须重放同一 GroundingPlan 和 Evidence Bundle；
`evidence_truth_precedes_teacher`，Sol 不是事实真值。

**阶段顺序固定为 S1D-0 → S1D-1 → S1D-2 → S1D-3 → S1D-4 → S1D-5 → S1D-6。**

**S1D-2 只设计 Tool，S1D-3 只设计 Operator；不得合并、倒序或并行退出。**

## 二、统一阶段规则

### 2.1 执行单元功能原子性

本计划中的“原子性”指 `execution_unit_function_atomicity`：一个 Tool 只读取一种事实人口，
一个 Operator 只执行一种确定性变换。读取、跨人口关联、排序、路径结构、集合投影、集合
比较、时间比较、解释和导出必须由不同类型的单元承担，再由 InvestigationPlan 组合。

每个候选单元必须通过 `atomic_split_test`：若内部子能力可以独立复用、授权、版本化、缓存、
计费、取消或产生独立 Evidence，必须拆分。不得通过 mode 参数让一个单元切换事实人口或
派生语义。

### 2.2 阶段候选提交一致性

每个阶段必须生成一个带内容摘要的 `design_candidate_id`。阶段输出先写入隔离的临时候选，
完成 schema、引用闭包、语义、边界与 Hook 检查后，才发布阶段回执。失败候选不得：

- 覆盖前一阶段制品；
- 被后续阶段引用；
- 写入 accepted Registry；
- 被描述为已实现或已部署能力；
- 将不完整表格、半个 ResultSet 或缺 Evidence 的事实标记为通过。

同一候选的全部必需制品、摘要、父候选和阶段回执必须同时可验证，否则整个阶段视为未通过。
这是设计制品提交一致性，不替代执行单元功能原子性。

### 2.3 同一身份与同一候选

所有事件事实接口设计必须显式继承 incident、publication、revision、collector、cohort、
window、data-through 和 finality。所有最终设计验收必须来自同一个 S1D-6 candidate；不得把
不同候选、不同 publication 或不同时间的局部通过拼成“整体通过”。

### 2.4 阶段退出的一般条件

每个阶段退出前必须同时满足：

1. 所有必需制品存在、可解析且无符号链接替换；
2. 父候选摘要、当前制品摘要和引用闭包一致；
3. 28 题映射没有遗漏、重复、静默删除或越界扩张；
4. 每个新 Tool/Operator 具有唯一 `atomic_capability_id` 并通过 `atomic_split_test`；
5. 本阶段的 normal、empty、missing、wrong identity、large result、cancel、tamper 和
   boundary Oracle 已定义到相应深度；
6. 独立 Reviewer 与 Builder 身份不相同；
7. Alignment Hook 对当前阶段返回 `alignment_passed`；
8. 回执明确 `design_only=true`、`runtime_implemented=false`、
   `production_deployed=false`。

### 2.5 Hook 调用规则

阶段检查命令统一为：

```bash
python3 .codex/hooks/country_outage_agent_p2_s1_design_alignment.py \
  --repo-root . \
  --stage S1D-X \
  --output evaluation/country-outage/p2-s1-execution-unit-design/stages/S1D-X.json
```

当前 S1D-0 设计任务禁止写 `evaluation/**`，因此只执行无 `--output` 的只读检查。后续阶段
任务必须另行扩展 TASK 合同，允许对应 contracts/evaluation 路径后再生成阶段回执；不得绕过
工作树治理。

## 三、阶段总表

| 阶段 | 单一目标 | 主要输出 | 退出判据 |
|---|---|---|---|
| S1D-0 | 冻结任务目标、28 题人口、阶段和防跑偏规则 | Task Spec、Plan、Alignment Hook、定向测试 | 文档/Hook 同候选通过；未实现运行时 |
| S1D-1 | 把问题拆成可验证 Capability、模型角色与 Oracle 种子 | capability/model-role/oracle contracts | 28/28 闭包，Sol→DS 角色与事实边界完整 |
| S1D-2 | 单独设计只读 Tool 合同 | tool-catalog、tool-contract schema | 输入输出、身份、分页、完整性、成本闭合 |
| S1D-3 | 单独设计确定性 Operator 合同 | operator-catalog、operator-contract schema | 纯函数、排序/tie/null/复杂度和证据闭合 |
| S1D-4 | 设计调查组合、双模型回答与运行时提交一致性 | Plan/ResultSet/Graph/model-flow/commit schemas | DAG、Sol→DS、局部失败、重跑和导出均有状态机 |
| S1D-5 | 建立 Oracle、Sol/DS 对齐评测、预算、安全与独立审查 | oracle、alignment、budget、semantic-review | 28 题同证据对比、正反例和边界检查通过 |
| S1D-6 | 形成同候选最终设计包和实现交接门 | candidate、acceptance-manifest、handoff | 引用闭包与摘要闭合，只准进入实现排期 |

## 四、S1D-0：目标、人口与设计防跑偏基线

### 4.1 入口

- 已人工审查的 33 题草案及 28 题保留决定；
- 用户冻结的 AS 默认排序语义；
- 当前 6 Tool、4 Operator、RRC25 publication 和数据层能力；
- P2 生命周期治理和现有 TASK/Hook 纪律。

### 4.2 工作

1. 冻结 28 个唯一问题 ID、目标、当前入口、候选单元和处置；
2. 明确 Tool/Operator/Validator/Delivery 的类型边界；
3. 冻结 Tool/Operator 功能原子性、拆分测试和只读调查边界；
4. 冻结 `gpt-5.6-sol` Teacher→DS Student 顺序、同证据重放和真值优先级；
5. 编写阶段计划和 Alignment Hook；
6. 用定向反例证明 Hook 能阻止遗漏题目、混合阶段、复合 Tool/Operator、模型顺序漂移和
   Sol 输出冒充事实真值。

### 4.3 本阶段输出

- `Task-Spec-目标与最终验收文档.md`；
- `Plan-Tool与Operator设计分阶段计划.md`；
- `.codex/hooks/country_outage_agent_p2_s1_design_alignment.py`；
- `dev/tests/test_country_outage_p2_s1_design_alignment_hook.py`。

### 4.4 退出门

- 28 题覆盖恰为 28/28；
- AS 排序固定为 `peak_invisible_direction_count DESC`、
  `peak_complete_prefix_count DESC`、`asn ASC`；
- Tool 与 Operator 的设计阶段明确分离；
- 功能原子性、`atomic_split_test`、部分失败继续和不可变重跑均已定义；
- `sol_then_ds_execution_order`、`teacher_required=true`、
  `evidence_truth_precedes_teacher` 均已定义；
- Hook 定向测试和 S1D-0 检查通过；
- 没有任何运行时、Registry、API、数据或部署变更。

只读检查：

```bash
python3 .codex/hooks/country_outage_agent_p2_s1_design_alignment.py --repo-root . --stage S1D-0
```

## 五、S1D-1：问题到 Capability 与 Oracle 种子

### 5.1 入口

已通过的 S1D-0 candidate 和阶段回执。

### 5.2 工作

1. 将每题拆为身份核验、事实读取、确定性派生、边界拒答和交付动作；
2. 为每个子能力冻结输入人口、时间语义、证据等级、完整性和禁止结论；
3. 标注 `existing`、`new_p2_v1`、`deferred_p2_1`、`unsupported`；
4. 为每题建立 normal、empty、missing、wrong identity、boundary 和 large-result Oracle 种子；
5. 对每个子能力执行 `atomic_split_test`，产出单一读取/单一变换的候选人口；
6. 冻结 Sol Teacher、DS Student、Alignment Evaluator 和 Host Validator 的职责、输入、输出、
   模型身份、prompt/policy 版本和禁止权限；
7. 判定 `VALIDATOR-01` 与 `DELIVERY-01` 的治理类型，不以名称规避副作用审查。

### 5.3 计划输出

```text
contracts/agent/country-outage-p2-s1-execution-unit-design/
├── question-capability-map.json
├── question-oracle-seed.json
├── execution-unit-decomposition.json
└── model-role-contract.json
```

### 5.4 退出门

- 28 个问题全部至少映射一个可执行能力或明确的边界拒答；
- 每项 C 级数据潜力都有“待发布 Tool”，不能冒充 A/B 级事实；
- 原 `query_entity_states` 和 `observed_path_structure` 被明确判定 `split_required`；
- Q24、TOOL-13、OP-14 明确 deferred，不混入 P2 v1 完成声明；
- customer cone、原因、恢复等问题没有伪造执行路径；
- 所有能力都有 owner kind、Evidence 入口和非目标。
- Sol 与 DS 的结构化输入都不能越过 Host Grounding；DS 版本不得留作“latest”。

## 六、S1D-2：Tool 设计

### 6.1 入口

已通过的 S1D-1 capability map 与 Oracle seed。

### 6.2 工作

本阶段只设计 Tool，不设计派生算法。冻结：

- `TOOL-07 query_as_prefix_members`；
- `TOOL-08 query_prefix_states`；
- `TOOL-09 query_as_states`；
- `TOOL-10 query_new_prefix_states`；
- `TOOL-11 query_route_states_at_time`；
- `TOOL-12 query_path_evidence`；
- `TOOL-13 query_route_events` 的 deferred 合同边界。

每个 Tool 必须定义 Typed Tool Contract：身份输入、查询维度、默认值、互斥参数、人口、
时间点/时间窗、稳定排序、游标、总数、去重键、ResultSet、Evidence、checkpoint、完整性、
null/unknown/missing、超时、取消、权限、最大扫描量、成本估算和错误枚举。

`TOOL-12` 必须支持至少 5 条路径预览和受控完整导出数据源；`TOOL-11` 必须证明
path-at-time，不得复用 `concurrent` 充当路径时点证据。

每个 Tool 只允许一种 `output_population`；任何“查询并排序”“读取两种 state 并关联”或
“路径查询并生成 downstream set”的设计均阻断。

### 6.3 计划输出

```text
contracts/agent/country-outage-p2-s1-execution-unit-design/
├── tool-catalog.json
├── tool-contract.schema.json
└── tool-atomicity-review.json
```

### 6.4 退出门

- Tool 只读取同一绑定 publication，不读取外部世界知识；
- 分页全量与预览有明确 `complete|sample|truncated|audit_only`；
- AS 自身前缀、关系关联前缀、路径、origin 和 VP 人口不可混用；
- 取消、超时、缺页或摘要不一致时 node 不提交；
- P2.1 Tool 只有设计占位，不被标记 active；
- 本阶段制品不包含排名、Jaccard、时间关系等 Operator 实现逻辑。
- 每个 Tool 均通过 `tool_single_read_semantic` 和 `atomic_split_test`。

## 七、S1D-3：Operator 设计

### 7.1 入口

已通过的 S1D-2 Tool 合同和冻结 ResultSet 输入语义。

### 7.2 工作

本阶段只设计确定性 Operator，不改变 Tool 查询合同。冻结：

- `OP-05 as_severity_rank`；
- `OP-06 entity_time_join`；
- `OP-07 prefix_state_transition`；
- `OP-08 path_contains_asn`；
- `OP-09 path_direct_neighbors`；
- `OP-10 project_path_prefix_set`；
- `OP-11 project_downstream_origin_set`；
- `OP-12 set_relation`；
- `OP-13 temporal_evidence_relation`；
- `OP-14 route_change_classifier` 的 deferred 合同边界。

每个 Operator 必须定义输入 schema/digest、纯函数参数、排序、tie、null、unknown、missing、
空集合、时间容差、复杂度、内存上限、确定性摘要、Evidence 继承和禁止解释。

### 7.3 计划输出

```text
contracts/agent/country-outage-p2-s1-execution-unit-design/
├── operator-catalog.json
├── operator-contract.schema.json
└── operator-atomicity-review.json
```

### 7.4 退出门

- `OP-05` 默认排序三键与 S1D-0 完全一致，并保留并列语义；
- path contains/position 与 direct adjacency 分别由 `OP-08`、`OP-09` 输出；
- prefix set 与 downstream origin set 分别由 `OP-10`、`OP-11` 投影；
- `OP-12` 的交集、差集、coverage 和 Jaccard 在空集合/缺页时失败关闭；
- `OP-13` 仅输出时间/可比性关系，不输出因果；
- 所有 Operator 同输入同版本同参数得到同摘要；
- 本阶段不得回改 Tool 结果人口来迎合 Operator。
- 每个 Operator 均通过 `operator_single_transform_semantic` 和 `atomic_split_test`。

## 八、S1D-4：组合调查、双模型回答与运行时提交一致性设计

### 8.1 入口

已通过的 Tool 与 Operator 合同。

### 8.2 工作

1. 冻结 InvestigationPlan DAG、依赖、并行 wave、状态机和预算；
2. 冻结 ResultSet 的 content address、分页、预览、缓存、过期和重放；
3. 冻结 Evidence Graph 节点、边、限制、未知、失败和版本；
4. 冻结 Sol Teacher run→TeacherReference validation→DS Student run→Alignment Evaluation 的
   状态机和同证据摘要；
5. 冻结 Sol 不可用、TeacherReference 失败、DS 失败、对齐低分和用户授权降级的处置；
6. 冻结节点结果、调查修订、Evidence Graph、DialogState 和导出的提交一致性协议；
7. 冻结取消、部分失败继续、局部重跑、新 revision 和导出授权；
8. 冻结 DialogState 只在 Evidence commit 与最终 DS Answer Validator 成功后推进的顺序。

### 8.3 计划输出

```text
contracts/agent/country-outage-p2-s1-execution-unit-design/
├── investigation-plan.schema.json
├── result-set.schema.json
├── evidence-graph.schema.json
├── dual-model-answer-flow.schema.json
└── runtime-commit-consistency-contract.json
```

### 8.4 退出门

- 节点失败不产生半事实、半边或半 ResultSet；
- 无依赖分支可以继续，但调查 revision 明确 `partial`；
- 重跑创建新 revision，不覆盖旧 Evidence；
- DialogState 不引用未提交或失败节点；
- 导出只针对冻结的完整 ResultSet，临时文件失败不替换最终文件；
- 每个状态转移都有幂等键、父 revision、digest 和恢复规则。
- 运行时事务设计不得把已拆分的 Tool/Operator 重新包进复合执行单元。
- TeacherRun、StudentRun、AlignmentRun 各有独立回执；DS 必须引用同一 Evidence digest。
- `teacher_required=true` 时 Sol 失败不得静默切到 DS；用户授权降级必须形成新计划修订。

## 九、S1D-5：Oracle、性能、成本、安全与独立审查

### 9.1 入口

已通过的完整设计合同。

### 9.2 工作

1. 将 28 题 Oracle seed 扩为可执行设计 Oracle；
2. 建立身份错配、缺页、游标篡改、未来状态读取、路径语义、分页重复和摘要篡改反例；
3. 对每个 Tool/Operator 执行复合动词、mode 漂移、人口漂移和内部编排反例；
4. 对 28 题运行 Sol Teacher 与 DS Student 同证据回放，按意图、事实、Evidence、边界、结构
   分项评分；
5. 注入 Sol 错误数字、无效引用、因果和恢复断言，证明 TeacherReference Validator 会拒绝；
6. 比较 DS 首答与最多一次差异反馈修订，建立版本候选、保留集和非回退晋级门；
7. 为 AS/前缀/路径/downstream 海量结果冻结 preview、预算、确认和导出阈值；
8. 分角色评估 Sol planning/reference/feedback 与 DS answer/revision 的 token、费用和延迟；
9. 评估逐 ASN 扫描、Prefix×VP、path-at-time、集合比较的时间/内存/存储上限；
10. 独立产品语义 Reviewer 检查事实、推导、世界知识、因果和边界；
11. 独立 BGP Reviewer 检查 AS_PATH、origin、prepend、AS_SET/confederation 内部质量门。

### 9.3 计划输出

```text
contracts/agent/country-outage-p2-s1-execution-unit-design/
├── oracle.json
├── cost-performance-budget.json
├── model-alignment-evaluation.json
└── product-semantic-review.json
```

### 9.4 退出门

- normal、empty、missing、null、wrong identity、unavailable、boundary、large result、
  tamper、cancel、rerun 和 partial failure 均有 Oracle；
- Reviewer 与 Builder 身份分离，意见有处置回执；
- 任何未闭合高风险语义均阻断，不降级成“已知限制后通过”；
- 成本超预算时必须缩小计划或请求用户确认，不能静默截断；
- 内部路径质量 Gate 不被包装成新的用户事实问题。
- 任一执行单元未通过 `atomic_split_test` 时不得以“功能完整”为由放行。
- DS 的事实精度、Evidence 引用和 boundary compliance 是硬门；文本相似度不得单独放行；
- Sol 样本未通过 Evidence Validator 时不得进入 DS prompt、评测真值或离线改进集。

## 十、S1D-6：同候选最终设计验收与实现交接

### 10.1 入口

已通过 S1D-0 至 S1D-5 的阶段回执和全部不可变制品。

### 10.2 工作

1. 构造单一 final design candidate；
2. 对全部文档、schema、catalog、Oracle、预算和 Review 计算摘要；
3. 验证 28 题→Capability→Tool/Operator→Plan→Evidence→Oracle 的引用闭包；
4. 验证 Sol/DS 模型身份、prompt/policy、Evidence digest、对齐矩阵和费用回执闭包；
5. 生成实现交接清单、依赖顺序、风险、明确 deferred 项；
6. 明确下一任务只能进入实现排期或实现 S0，不得跳到部署。

### 10.3 计划输出

```text
contracts/agent/country-outage-p2-s1-execution-unit-design/
├── candidate.json
└── acceptance-manifest.json
```

### 10.4 退出门

- 所有验收证据来自同一 candidate；
- manifest 无悬空引用，摘要和父候选闭合；
- 28 题覆盖 28/28，P2 v1 与 P2.1 deferred 可机器区分；
- 28 题均有 Sol Teacher→DS Student 同证据回放结果，DS 晋级无硬门回退；
- `design_only=true`、`runtime_implemented=false`、`production_deployed=false`；
- 结论仅为“设计合同可交接实施”，不能写成“P2 已完成”。

最终检查阶段别名为 `final`；它必须重验 S1D-0 至 S1D-6 的全部前置阶段，而不是只检查
最终两个 JSON 文件。

## 十一、建议的未来实施顺序（非本计划交付）

设计最终通过后，建议另立 TASK 按以下顺序实施，每项仍需独立开发与验收：

1. 先做 TOOL-07/08/09/10 与 OP-05/06/07，闭合 ASN/前缀问题；
2. 再做 TOOL-12 与 OP-08/09/10/11/12，闭合路径查询、预览和集合；
3. 再做 TOOL-11 与 OP-13，闭合 path-at-time 和一致性；
4. 最后组合 InvestigationPlan、Evidence Graph、局部重跑和受控导出；
5. TOOL-13/OP-14 留在 P2.1，除非重新通过范围与成本 Gate。

这一顺序只是实施依赖建议，不构成任何实现授权或完成声明。

## 十二、阶段性停止条件

出现以下任一情况立即停止当前 Gate，不继续“补写后续内容”：

- 28 题人口或用户冻结排序发生未授权变化；
- Tool 与 Operator 被合并为一个不可审计的模型步骤；
- 任一 Tool 同时读取两个事实人口，或内嵌 join/rank/compare/export；
- 任一 Operator 通过 mode 参数承担两种派生语义，或在内部调用 Tool；
- DS 先于 Sol 执行，或 Sol/DS 消费不同 GroundingPlan/Evidence digest；
- Sol 输出绕过 Evidence Validator 成为事实、Golden Answer 或 DS 强制模仿目标；
- DS 模型身份未冻结，或用文本相似度代替事实/Evidence/边界硬门；
- C/D/E 级内容被写成 A/B 级事件事实；
- `concurrent` 被当作 path-at-time；
- observed downstream 被当作 customer cone/customer；
- 部分分页、有限样本或失败节点被标记 complete；
- 当前候选覆盖历史 Evidence/阶段制品；
- Hook、单测或独立 Reviewer 失败；
- 文档/源码测试被表述为运行时、部署或生产通过。

发生停止条件后，只能修复当前阶段并生成新 candidate，不能原地篡改已发布回执，也不能以
“后续阶段会补齐”为由放行。
