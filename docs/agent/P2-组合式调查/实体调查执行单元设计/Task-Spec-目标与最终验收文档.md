# P2-S1 国家中断组合式调查执行单元设计 Task Spec（目标与最终验收文档）

版本：`country-outage-agent-p2-s1-execution-unit-design-v1`

状态：冻结目标，待阶段设计

任务类型：P2 Tool、Operator、InvestigationPlan、ResultSet 与 Evidence Graph 的设计任务

适用产品：`RRC25 国家中断组合式调查助手`

冻结日期：2026-08-12

## 一、文档定位

本 Task Spec 冻结 28 个经人工审查保留的调查问题所要求的最终用户效果、执行单元效果、
证据效果、原子性、边界和验收门。它是后续 Tool/Operator 设计的目标合同，不是实现说明、
接口实现、数据库设计、部署计划或生产能力声明。

本任务的核心不是“给 28 个问题分别写 28 段固定答案”，而是设计一组功能原子、可组合、
可治理、可分页、可导出、可重放的只读 Tool 与确定性 Operator，使复杂问题通过闭合的
`InvestigationPlan` 组合完成。原子性首先指执行单元的功能原子性：一个 Tool 只读取一种
事实人口，一个 Operator 只执行一种确定性变换；不得把读取、关联、排序、解释和导出塞进
同一个单元。

**28 题覆盖必须为 28/28。Tool 设计和 Operator 设计必须分阶段。**

本 Task Spec、阶段计划和 Alignment Hook 通过，只能证明设计目标、映射、阶段和边界没有
发生已知漂移；**设计完成不等于运行时实现**，也不等于代码完成、产品验收、合并、部署或
生产验证。

## 二、最终用户效果

用户围绕一个已绑定的国家中断事件提出组合问题时，系统必须能够：

1. 展示受控 InvestigationPlan，而不是仅显示模型“正在思考”；
2. 在同一 incident、publication、revision、collector、cohort 和 Registry snapshot 下
   组合事件身份、时序、ASN、前缀、RouteState 和 AS_PATH 证据；
3. 从聚合峰值下钻到完整 ASN/前缀成员，而不是只返回总量；
4. 从指定 ASN 下钻到自身固定前缀、状态转移、路径样本和 observed downstream origin；
5. 区分路径包含、直接邻接、path-at-time、集合重叠和时间关系；
6. 对海量结果先返回有界预览，再由用户确认完整导出；
7. 在独立分支失败、缺失或取消时继续其他无依赖分支，同时不产生半提交事实；
8. 将每个事实、派生事实、失败和未知绑定到 Tool/Operator 回执与 Evidence 引用；
9. 支持围绕既有调查节点继续追问、局部重跑和导出调查快照；
10. 只描述控制面时间、集合、路径和指标关系，不把相关性升级为因果；
11. 回答阶段先运行 `gpt-5.6-sol` 教师基线，再让 DS 在同一冻结证据上回答，并展示模型、
    耗时、成本、对齐分数和是否发生降级。

## 三、最终开发者效果

完成设计后，后续实现者必须拥有以下无歧义输入：

- 28 题到 Capability、Tool、Operator、Validator、Delivery 和边界回答的完整映射；
- 每个候选 Tool 的输入、输出、身份、时间、人口、分页、完整性、Evidence、错误、权限、
  超时、成本和取消合同；
- 每个候选 Operator 的纯函数输入、确定性排序、tie、null、unknown、缺失、集合、时间
  容差、复杂度和禁止用途合同；
- InvestigationPlan DAG、依赖、并发 wave、取消、局部重跑和状态机；
- ResultSet 的内容寻址、预览、完整分页、导出和重放合同；
- Evidence Graph 的节点、边、完整性、限制、未知和版本合同；
- Tool 与 Operator 的功能原子性、拆分判据、失败单位和组合边界；
- 调查修订、Evidence、DialogState 和导出的运行时提交一致性约束；
- Sol Teacher、DS Student、同证据重放、差异评测、失败降级和 DS 版本晋级合同；
- normal、missing、null、wrong identity、unavailable、boundary、large result、tamper、
  cancel、rerun 和 partial failure Oracle；
- 费用、性能、权限和独立产品语义 Reviewer 的入口与出口。

## 四、入口基线

### 4.1 已有能力

当前 Registry 已治理 6 个只读 Tool 与 4 个确定性 Operator：

| 单元 | 现有用途 |
|---|---|
| `TOOL-01` | 解析并绑定事件与 publication 身份 |
| `TOOL-02` | 读取概览、窗口、数据截止、质量与 finality |
| `TOOL-03` | 读取 15 条事件聚合时序 |
| `TOOL-04` | 分页、筛选和查询受影响 ASN 汇总 |
| `TOOL-05` | 查询 affected ASN 与 downstream origin 的路径关联及有限样本 |
| `TOOL-06` | 读取数据制品、摘要和审计身份 |
| `OP-01` | 聚合时序极值 |
| `OP-02` | IPv4/IPv6 分单位比较 |
| `OP-03` | 事实时间线排序 |
| `OP-04` | 事件窗口趋势与跨轨形态 |

### 4.2 已有但未安全发布的数据潜力

底层数据已经包含或可投影：

- 固定 cohort 成员及 prefix、AF、country origin、observed origin；
- `prefix_states`、`asn_states`、`new_prefix_states`；
- Prefix×VP 的 RouteState、checkpoint、last event 和 path identity；
- 有序 RouteEvent、announce/withdraw、origin、AS_PATH 与 raw reference；
- 完整 path evidence：affected ASN、known origin、prefix、canonical AS_PATH、peer ASN、
  observation count；
- AS 静态名称、机构和性质的冻结快照。

本任务只设计如何安全投影和组合这些数据，不修改数据生产链，不重新扫描 MRT，不引入第二套
ASN profile，也不接入外部 relationship 数据。

### 4.3 已确认的语义缺陷

后续设计必须先修复以下语义，不得在新 Evidence Graph 中继续传播：

1. 当前路径关系是“affected ASN 出现在 known origin 之前”，不要求直接相邻；
2. 当前 `concurrent` 是状态点重叠，不是具体 AS_PATH 的 path-at-time；
3. observed downstream origin set 不是 customer cone；
4. AS 自身固定前缀与路径关系关联前缀是不同集合；
5. 普通接口最多 3 条路径样本不等于完整总体只有 3 条；
6. 无观测、数据缺失、unknown、显式撤路和完全不可见是不同状态；
7. 数据窗口完整与事件结束、恢复状态是不同维度。

## 五、固定产品范围

### 5.1 本设计覆盖

- 单一国家事件；
- 单一 RRC25 publication/revision；
- 只读调查；
- 事件全景、时间点下钻、证据一致性；
- ASN、固定前缀、新出现前缀、RouteState、AS_PATH 和 observed downstream；
- 有界预览、完整导出、局部重跑、取消和失败继续；
- 事实、派生事实、限制、未知和外部证据需求的分层表达。

### 5.2 固定非目标

- 不判断全国中断、真实用户、流量、DNS、HTTP 或服务影响；
- 不判断原因、责任、政府行为、正式恢复或 RCA；
- 不把 path order/adjacency 推断成 provider-customer；
- 不在 P2 v1 回答 customer cone；
- 不做跨国家、跨事件或跨 collector 比较；
- 不建设类似 BGPView 的无限制交互拓扑图；
- 不允许 LLM 自由调用文件、数据库或网络；
- 不允许 Tool/Operator 改写事件数据、publication、Registry 或历史 Evidence；
- 不在本设计任务实现候选执行单元、运行时、API、页面或部署。

## 六、证据等级与世界知识

| 等级 | 定义 | 是否可进入事实 Evidence Graph |
|---|---|---|
| A | 同一 publication 直接发布事实 | 是 |
| B | 同身份事实经登记 Operator 确定性派生 | 是，标记 derived |
| C | 后端已有但未通过受控 Tool 发布 | 否，设计完成前只能标记 capability gap |
| D | 需要外部、版本化、可审计来源 | 只进入独立 external evidence 节点 |
| E | 当前证据域不能支持 | 否，进入 unknown/unsupported |

Sol/LLM 可以使用 BGP 世界知识解释概念、选择调查路线、提出可检验假设和解释限制；世界知识
不得生成本事件事实，不得把 customer cone、商业关系、传播、原因或恢复写入事实 Evidence
Graph。解释和假设进入独立 Reasoning/Hypothesis 层，只能引用事实，不得反向改写事实。

### 6.1 双模型回答目标

用户冻结的顺序为 `sol_then_ds_execution_order`：先运行 `gpt-5.6-sol` Teacher，再运行 DS
Student。当前执行环境明确提供 `gpt-5.6-sol` 作为可选模型；设计不得把这一环境内可用性
写成公开 API、其他账户或生产环境已经可用。DS 使用逻辑别名 `ds_student`，进入 S1D-5 前
必须冻结实际 `provider/model/version`；`ds_student_model_id_required`，不得用模糊的“DS
最新版”代替版本身份。

目标不是让 DS 逐字模仿 Sol，而是让 DS 在以下可验证维度逼近 Sol：

- 用户目标和子问题覆盖；
- InvestigationPlan 结构与能力选择；
- 必需事实覆盖和数字一致性；
- Evidence 引用准确性；
- 证据不足、未知和禁止结论边界；
- 时间、集合、路径关系的表达精度；
- 最终回答的结构、清晰度和可继续下钻性。

### 6.2 Sol-first、DS-second 执行顺序

每个问题的回答流程固定为：

```text
用户问题
  → Host 绑定事件/publication/权限
  → gpt-5.6-sol 生成 TeacherSemanticPlan
  → Host Grounding + Tool/Operator 执行
  → gpt-5.6-sol 基于冻结 Evidence 生成 TeacherReference
  → TeacherReference Validator
  → DS 使用同一 GroundingPlan、Evidence Bundle 和已验证 TeacherReference 生成 StudentAnswer
  → Evidence/Boundary Validator
  → DS-Sol Alignment Evaluator
  → 发布 DS 最终回答与双模型回执
```

Sol 的规划和回答都不能直接选择未登记 Tool、发布数字、提升权限或写状态。Host 只把通过
identity、Evidence 和 boundary 校验的 TeacherReference 提供给 DS。不得保存或要求模型私有
思维链；只保存结构化 SemanticPlan、事实引用、回答草案和评测回执。

### 6.3 真值优先级

`evidence_truth_precedes_teacher`，优先级固定为：

```text
publication 直接事实 / 登记 Operator 派生事实
  > Evidence 与边界 Validator
  > 已验证 Sol TeacherReference
  > DS StudentAnswer
```

`teacher_reference_not_ground_truth`：Sol 不是事实 Oracle。Sol 与 Evidence 冲突、引用不存在、
跨 publication、越过控制面边界或增加因果时，该 TeacherReference 失败关闭，不得为了让 DS
“向 Sol 靠拢”而把错误加入 DS 输入或 Golden Answer。

### 6.4 DS 靠拢机制

每个双模型回合产生 `TeacherRun`、`StudentRun` 和 `AlignmentRun` 三个独立回执，至少记录：

- 精确模型身份、prompt/policy 版本和运行参数；
- 同一 question、GroundingPlan、Evidence Bundle 与 Registry snapshot 摘要；
- required fact、Evidence ref、boundary assertion 和 unknown 的集合差异；
- `intent_coverage`、`fact_recall`、`fact_precision`、`evidence_ref_precision`、
  `boundary_compliance`、`structure_coverage`；
- latency、input/output token、模型费用和重试次数；
- 失败、降级、修订和最终发布处置。

文字相似度只能作为辅助指标，不能替代事实和证据评分。DS 首答低于阈值时，允许 Sol 生成
一次只含差异项的结构化 Feedback，DS 最多修订一次；修订仍须重新通过全部硬门。线上回合
不得自动修改模型权重、prompt 或政策。DS 的改进通过离线汇总失败类型、形成新 prompt/policy
候选、在冻结 28 题和保留集上回归后晋级。

### 6.5 可用性与降级

默认 `teacher_required=true`。Sol 不可用、身份未冻结、TeacherReference 未通过或成本硬上限
触发时，不得伪造 Sol 回执，也不得宣称完成“Sol→DS 对齐回答”。系统可返回
`teacher_unavailable` 并请求用户选择：等待重试，或明确授权一次 `ds_unaligned_degraded` 回答。
未经确认的默认降级禁止。

## 七、28 题能力映射

下表是本任务唯一的用户问题覆盖人口。原 Q11、Q12、Q15、Q25、Q28 已移出用户问题；
其中 Q12 转为内部路径质量 Gate。

<!-- QUESTION_MAP_BEGIN -->
| 问题 | 用户目标 | 当前入口 | 新设计单元 | 处置 |
|---|---|---|---|---|
| Q01 | 核验事件、publication、revision、collector、窗口和终态 | TOOL-01/02/06 | Investigation identity gate | 保留直答 |
| Q02 | 说明聚合、ASN、样本、完整路径与 audit-only 完整性 | TOOL-02/05/06 | ResultSet completeness model | 保留边界回答 |
| Q03 | 并列报告前缀、ASN、IPv4、IPv6 极值 | TOOL-03、OP-01/02 | OP-29 时间关系 | 保留并补齐 IPv6 |
| Q04 | 按不可见程度排序 ASN | TOOL-04 | OP-05 AS severity rank、DELIVERY-01 | 设计新排序与导出 |
| Q05 | 展开 AS49666 前缀、状态和路径概况 | TOOL-04/05 | TOOL-07/08/12、OP-06/07/09/15、RENDERER-01/02/03、DELIVERY-01 | 原子单元组合下钻 |
| Q06 | 列出指定 ASN 的异常前缀及首峰末时间 | 无实体 Tool | TOOL-07/08、OP-06/07/08/09、RENDERER-01/02/03、DELIVERY-01 | 原子单元组合 |
| Q07 | 指定时间点列出 ASN 的 normal/partial/complete 前缀 | 无实体 Tool | TOOL-07/08、PLAN-CAP-01 | Plan参数绑定后读取 |
| Q08 | ASN 首次变化、最长持续和峰值比例排名 | TOOL-04 部分字段 | TOOL-09、OP-06/07/10/11/12/13/14 | 原子单元组合 |
| Q09 | 两个 ASN 的时间、前缀和 downstream 集合重叠 | TOOL-05 部分分页 | TOOL-07/09/12、OP-07/19/25/26/27/28/29 | 原子集合与时间组合 |
| Q10 | 解释 ASN 排名及非关键性边界 | TOOL-04 | OP-05 排序回执 | 修正默认语义 |
| Q13 | 峰值时间点列出具体 partial/complete 前缀 | TOOL-03 只有峰值 | TOOL-08、PLAN-CAP-01、RENDERER-01/02/03、DELIVERY-01 | 峰值时间作为Tool参数 |
| Q14 | 指定前缀的观察方向与状态变化 | 无实体 Tool | TOOL-08/11、OP-06/07/08/30/31/32 | 设计按需下钻 |
| Q16 | 新出现前缀、首次观察、origin、路径和截止状态 | TOOL-03 只有总量 | TOOL-10/11、OP-33、RENDERER-01/02/03、DELIVERY-01 | 原子单元组合成员查询 |
| Q17 | 澄清“新出现”不等于全球新分配 | TOOL-03 定义 | Evidence Validator | 保留边界回答 |
| Q18 | 指定前缀在不同 VP 的状态、origin 与路径差异 | 无 RouteState Tool | TOOL-11、OP-30/31/32 | 三种一致性独立分类 |
| Q19 | AS49666→AS58224 关系与至少 5 条真实样本 | TOOL-05 最多 3 样本 | TOOL-12、OP-15/16/17、RENDERER-01 | 原子路径读取与结构变换 |
| Q20 | 完整列出包含指定 ASN 的路径并导出 | audit-only | TOOL-12、OP-15/20/21/22/23/24、RENDERER-01/02/03、DELIVERY-01 | 完整路径与独立计数 |
| Q21 | 指定 ASN 的直接邻接摘要与有界路径邻域 | TOOL-05 样本可部分派生 | TOOL-12、OP-16/21/22/23/24 | v1 表格，图后置 |
| Q22 | 列出 AS49666→AS58224 的关联前缀 | TOOL-05 只有总数/样本 | TOOL-12、OP-18/22/24、RENDERER-01/02/03、DELIVERY-01 | 完整成员与独立统计 |
| Q23 | 峰值时点实际活动路径包含哪些 ASN | concurrent 不足 | TOOL-11、OP-15 | 设计 path-at-time |
| Q24 | 指定 prefix/peer 的路径变化链 | 无 RouteEvent Tool | TOOL-13、OP-34 | P2.1 deferred 设计 |
| Q26 | 指定 ASN 的 observed downstream origin set | TOOL-05 可分页 | TOOL-05或TOOL-12、OP-19/22/23/24、RENDERER-01/02/03、DELIVERY-01 | 冻结名称来源与集合统计 |
| Q27 | 多 ASN downstream 集合交集、覆盖率与 Jaccard | TOOL-05 数据可派生 | TOOL-12、OP-19/25/26/27/28、RENDERER-01/02/03、DELIVERY-01 | 四种集合变换独立 |
| Q29 | 解释 customer cone 当前不可回答 | 无 relationship 数据 | Evidence Validator | 边界回答，不建外部 Tool |
| Q30 | 判断客户关系证据不足 | 无 relationship 数据 | Evidence Validator | 边界回答，不猜测 |
| Q31 | 判断前缀、ASN、IPv4、IPv6 同槽/错位/非对称 | TOOL-03、OP-01/02/04 | OP-29 | 设计一致性关系 |
| Q32 | 区分峰值错位与证据冲突 | TOOL-03 | OP-29、comparability contract、GATE-04 | 设计一致性分类 |
| Q33 | 拒绝传播、全国、用户、原因、责任和恢复过度结论 | TOOL-02/06 | GATE-04/05、BOUNDARY-01 | 固定边界回答 |
<!-- QUESTION_MAP_END -->

验收要求：映射表必须恰好包含上述 28 个唯一问题 ID；任何问题未映射、重复映射、静默删除
或被改写为 RCA 目标均阻断阶段退出。

## 八、候选 Tool 设计目标

### 8.1 共同合同

每个新 Tool 必须设计并冻结：

- 稳定 ID、SemVer、Capability 映射和 Registry 生命周期入口；
- incident、publication、revision、collector、cohort、window、data-through、finality；
- 输入来源、允许枚举、默认值、非法组合和查询规范化；
- 时间语义、人口、AF、方向、prefix、ASN、VP/peer 和 AS_PATH 语义；
- 结果总数、分页、稳定排序、去重键、完整性和 `result_set_id`；
- Evidence ref、source digest、checkpoint/path identity 和 query receipt；
- null、unknown、missing、unavailable、empty、truncated 和 audit-only；
- 权限、超时、取消、成本预算、最大结果和失败关闭；
- `atomic_capability_id` 和唯一读取语义：一次调用只读取一种事实人口；
- 不内嵌关联、排序、集合比较、时间比较、自然语言解释或导出；
- 单元失败边界：成功返回完整合同输出，失败返回错误回执，不返回可发布的半结果；
- 禁止用途和不能推出的结论。

### 8.2 候选 Tool 清单

<!-- EXECUTION_UNIT_MAP_BEGIN -->
| 单元 | 类型 | 目标 | 首版状态 |
|---|---|---|---|
| TOOL-07 `query_as_prefix_members` | read_tool | 只读取指定 ASN 的固定 cohort 前缀成员 | P2 v1 设计 |
| TOOL-08 `query_prefix_states` | read_tool | 只读取 prefix state 记录，可按 prefix/time/state 过滤 | P2 v1 设计 |
| TOOL-09 `query_as_states` | read_tool | 只读取 ASN state 时间序列 | P2 v1 设计 |
| TOOL-10 `query_new_prefix_states` | read_tool | 只读取 cohort 冻结后首次观察的 new-prefix state 记录 | P2 v1 设计 |
| TOOL-11 `query_route_states_at_time` | read_tool | 只读取精确时点的 RouteState、origin、path、checkpoint | P2 v1 设计 |
| TOOL-12 `query_path_evidence` | read_tool | 只读取规范化 path evidence 行并稳定分页；预览属于Renderer | P2 v1 设计 |
| TOOL-13 `query_route_events` | read_tool | 只读取 prefix/peer 的有序 RouteEvent | P2.1 deferred |
| OP-05 `as_severity_rank` | deterministic_operator | 不可见程度、完全不可见前缀、ASN 稳定排序 | P2 v1 设计 |
| OP-06 `first_state_occurrence` | deterministic_operator | 只选择目标状态第一次出现 | P2 v1 设计 |
| OP-07 `state_intervals` | deterministic_operator | 只生成目标状态最大连续区间 | P2 v1 设计 |
| OP-08 `last_state_at_cutoff` | deterministic_operator | 只选择截止时间的最后状态 | P2 v1 设计 |
| OP-09 `peak_state_observation` | deterministic_operator | 只按冻结严重性序选择窗口峰值状态与时间 | P2 v1 设计 |
| OP-10 `compute_as_peak_complete_ratio` | deterministic_operator | 只计算complete峰值/固定前缀数 | P2 v1 设计 |
| OP-11 `select_longest_interval` | deterministic_operator | 只从区间集合选择最长区间 | P2 v1 设计 |
| OP-12 `rank_first_threshold_crossing` | deterministic_operator | 只按首次越阈时间排名 | P2 v1 设计 |
| OP-13 `rank_longest_duration` | deterministic_operator | 只按最长持续时间排名 | P2 v1 设计 |
| OP-14 `rank_peak_complete_ratio` | deterministic_operator | 只按complete峰值比例排名 | P2 v1 设计 |
| OP-15 `locate_asn_positions_in_path` | deterministic_operator | 只返回ASN在规范路径的位置集合；空集合表示不包含 | P2 v1 设计 |
| OP-16 `path_direct_neighbors` | deterministic_operator | 只投影指定ASN在单条规范路径中的直接邻接 | P2 v1 设计 |
| OP-17 `path_order_relation` | deterministic_operator | 只判断两个ASN在规范路径中的有序先后 | P2 v1 设计 |
| OP-18 `project_path_prefix_set` | deterministic_operator | 只投影唯一前缀集合 | P2 v1 设计 |
| OP-19 `project_downstream_origin_set` | deterministic_operator | 只投影observed downstream origin集合 | P2 v1 设计 |
| OP-20 `project_canonical_path_set` | deterministic_operator | 只投影唯一规范路径集合 | P2 v1 设计 |
| OP-21 `project_peer_direction_set` | deterministic_operator | 只投影唯一观察方向集合 | P2 v1 设计 |
| OP-22 `count_unique_paths` | deterministic_operator | 只计算唯一路径数 | P2 v1 设计 |
| OP-23 `count_unique_prefixes` | deterministic_operator | 只计算唯一前缀数 | P2 v1 设计 |
| OP-24 `count_unique_peer_directions` | deterministic_operator | 只计算唯一观察方向数 | P2 v1 设计 |
| OP-25 `set_intersection` | deterministic_operator | 只计算两个完整集合的交集 | P2 v1 设计 |
| OP-26 `set_difference` | deterministic_operator | 只计算有方向的集合差 | P2 v1 设计 |
| OP-27 `set_coverage_ratio` | deterministic_operator | 只计算指定方向的集合覆盖率 | P2 v1 设计 |
| OP-28 `set_jaccard` | deterministic_operator | 只计算两个完整集合的Jaccard | P2 v1 设计 |
| OP-29 `temporal_evidence_relation` | deterministic_operator | 只计算登记时间关系 | P2 v1 设计 |
| OP-30 `classify_vp_visibility_consistency` | deterministic_operator | 只分类同槽VP可见性一致性 | P2 v1 设计 |
| OP-31 `classify_vp_origin_consistency` | deterministic_operator | 只分类同槽VP origin一致性 | P2 v1 设计 |
| OP-32 `classify_vp_path_consistency` | deterministic_operator | 只分类同槽VP path一致性 | P2 v1 设计 |
| OP-33 `join_new_prefix_route_state` | deterministic_operator | 只按prefix/time连接new-prefix与RouteState | P2 v1 设计 |
| OP-34 `route_change_classifier` | deterministic_operator | 只分类相邻RouteState/Event变化类型 | P2.1 deferred |
| PLAN-CAP-01 `bind_output_to_tool_argument` | host_plan_capability | 只把已验证上游值绑定为下游Tool参数，不生成派生事实 | P2 v1 设计 |
| GATE-01 `identity_gate` | host_validator | 只验证调查身份一致性 | P2 v1 设计 |
| GATE-02 `evidence_ref_gate` | host_validator | 只验证Evidence引用存在性与摘要 | P2 v1 设计 |
| GATE-03 `result_completeness_gate` | host_validator | 只验证结果完整性声明 | P2 v1 设计 |
| GATE-04 `control_plane_boundary_gate` | host_validator | 只验证控制面证据边界 | P2 v1 设计 |
| GATE-05 `prohibited_conclusion_gate` | host_validator | 只验证禁止结论 | P2 v1 设计 |
| BOUNDARY-01 `render_boundary_response` | host_response | 只生成登记的边界回答结构 | P2 v1 设计 |
| RENDERER-01 `render_result_markdown` | pure_renderer | 只将冻结ResultSet渲染为Markdown | P2 v1 设计 |
| RENDERER-02 `render_result_csv` | pure_renderer | 只将冻结ResultSet渲染为CSV | P2 v1 设计 |
| RENDERER-03 `render_result_json` | pure_renderer | 只将冻结ResultSet渲染为JSON | P2 v1 设计 |
| DELIVERY-01 `commit_export_artifact` | host_delivery | 只提交一个已验证的渲染制品 | P2 v1 设计 |
<!-- EXECUTION_UNIT_MAP_END -->

`DELIVERY-01` 不是 read Tool，也不是纯 Operator。后续设计必须通过 S0A 生命周期影响分析决定
是扩展 Execution Unit kind，还是作为 Host Delivery Adapter 独立治理；不得为了复用 Registry
而把有文件输出副作用的导出伪装成纯 Operator。

原 `query_entity_states`、`entity_time_join`、`prefix_state_transition`、
`observed_path_structure`、`set_relation`、复合 Evidence Validator 和复合导出单元均因包含
多个可独立复用/失败/版本化的动作而废止。上述拆分是功能原子性校正，不是功能扩张。

## 九、候选 Operator 设计目标

### 9.1 确定性共同规则

Operator 必须是对冻结输入的纯确定性变换：

- 不读网络、数据库、文件或当前时间；
- 不直接调用模型；
- 不自行切换 publication、Registry snapshot 或时间窗；
- 不填补 null、unknown、missing 或 audit-only；
- 不产生没有输入 Evidence ref 的事实；
- 同一输入摘要、Operator 版本和参数必须得到相同输出摘要；
- 不覆盖输入 ResultSet 或历史 derived fact；
- 费用主要是 CPU/内存，不产生外部模型费用。

### 9.2 AS 严重性排序

`OP-05` 默认排序冻结为：

```text
peak_invisible_direction_count DESC,
peak_complete_prefix_count DESC,
asn ASC
```

必须同时输出 `severity_rank_global`、`result_position` 和完整排序回执。第一字段指当前
publication 中该 ASN 的不可见观察方向峰值，第二字段仅在第一字段相等时使用。不得将排名
解释为拓扑关键性、流量、用户影响或原因。

### 9.3 时间与状态

`OP-06..14` 分别承担首次出现、连续区间、截止状态、峰值状态、complete比例、最长区间和三种
固定排名；不得重新合并成“状态分析”算子。`OP-29` 只承担两个已形成时间证据的关系分类。
它们必须冻结：

- 五分钟槽位对齐和原始 UTC 时间；
- first/tie 规则；
- 时间容差只来自版本化参数；
- 连续区间遇到 missing/unknown 的断开规则；
- 不同人口/单位的可比性矩阵；
- `same_slot`、`precedes_within`、`follows_within`、`diverges`、`missing`、
  `not_comparable`；
- 任何时间关系都不得生成 `causes`。

### 9.4 路径结构

`OP-15` 只定位 ASN 位置，位置集合为空表达不包含；`OP-16` 只投影直接邻接；`OP-17` 只判断
两个 ASN 的路径先后；`OP-18..21` 分别投影 prefix、downstream origin、canonical path 和
peer direction 集合；`OP-22..24` 分别计算三种唯一计数。它们不得合并：

- AS_SET、confederation、missing 或 unordered 段不得强制线性化；
- prepend 必须保留原始证据并按合同确定是否折叠展示；
- `49666 48159 58224` 只能推出 49666 与 48159 直接相邻；
- path order/adjacency 不产生 provider/customer/peer 关系；
- `concurrent` 不得作为 path-at-time 输入。

### 9.5 集合关系

`OP-25..28` 分别只计算 intersection、directional difference、directional coverage 与
Jaccard。必须冻结成员 identity、去重键、空集合、缺页和 incomplete 集合语义。只有两个
输入 ResultSet 都完整时才能发布总体关系；任一输入截断时不得用样本计算总体相似度。

### 9.6 VP 一致性、受控连接与 deferred

- `OP-30/31/32` 分别只分类同槽 VP visibility、origin 和 path 一致性；时间算子不能替代；
- `OP-33` 只按 prefix/time 连接 new-prefix state 与 RouteState，不能推广为通用实体 join；
- `OP-34` 只分类相邻 RouteEvent/RouteState 的变化类型，并保持 P2.1 deferred；
- 极值时间传给下游 Tool 属于 `PLAN-CAP-01` 参数绑定，不是 Operator，也不产生派生事实。

## 十、InvestigationPlan 目标模型

InvestigationPlan 是 Host 生成和准入的闭合 DAG，不是模型自由执行清单。至少包含：

- `investigation_id`、`plan_revision`、`binding_generation`；
- incident/publication/revision/collector/cohort/window/data-through/finality；
- `registry_snapshot_id`；
- 调查目标、节点、依赖、并发 wave、预算、权限和取消策略；
- 每节点的 execution unit ID/version/digests、输入来源、预期 Evidence 和完整性要求；
- `required`、`optional`、`deferred` 和 `boundary_only` 处置；
- 失败传播：依赖节点 `skipped_dependency_failed`，无依赖分支继续；
- 计划准入失败时 Executor 不得启动。

建议状态机：

```text
draft
  → admitted
  → running
  → partially_completed | completed | failed | cancelled
```

每次合法状态变化创建新 `investigation_revision`，不得原地改写旧修订。

## 十一、ResultSet 与大结果交付

### 11.1 ResultSet

所有成员级查询统一返回：

```text
result_set_id
source_identity
normalized_query
stable_sort
member_identity
total
returned
is_complete
next_page_token
items
query_receipt
evidence_refs
content_digest
```

`result_set_id` 必须由 publication/Registry snapshot/Tool version/规范化查询/排序/来源摘要
内容寻址生成。分页不得改变人口、排序或身份；跨页重复、缺页或摘要漂移使完整结果失败关闭。

### 11.2 预览策略

| 结果类型 | 默认预览 |
|---|---:|
| ASN 排名 | 10 |
| 前缀成员 | 20 |
| 路径样本 | 5 |
| downstream origin | 10 |
| 集合关系成员 | 10 |

回答必须显示 `returned/total`、排序和 `is_complete`，并询问是否导出完整结果。任何预览不得
冒充完整集合。

### 11.3 导出

用户确认后，`DELIVERY-01` 只消费冻结 `result_set_id`：

- Markdown 用于人工审查；
- CSV 用于表格分析；
- JSON 保留完整字段和 Evidence；
- 导出文件包含查询、身份、排序、总数、完整性、限制和 SHA-256；
- 导出不得调用 LLM 重新生成成员；
- 导出失败不改变 ResultSet 或调查 Evidence。

## 十二、Evidence Graph 目标

### 12.1 节点类型

- `observed_fact`：Tool 直接事实；
- `derived_fact`：Operator 派生事实；
- `result_set`：完整或预览成员集合；
- `limitation`：collector、样本、人口、单位和路径限制；
- `unknown`：缺失、不可用、外部证据需求；
- `execution_failure`：失败、取消、跳过回执。

知识解释和假设不作为事实节点，可进入独立 Reasoning/Hypothesis 图。

### 12.2 边类型

- `derived_from`；
- `member_of`；
- `at_time`；
- `precedes`、`same_window`、`follows`；
- `path_contains`、`directly_adjacent_in_path`；
- `set_intersects`、`set_contains`；
- `supports`、`conflicts_with`、`limited_by`；
- `requires_external_evidence`。

事实图中禁止 `causes`、`responsible_for`、`customer_of`、`recovered_from`，除非未来独立
阶段引入相应版本化外部证据和因果合同。

## 十三、Tool 与 Operator 功能原子性合同

### 13.1 核心定义

`execution_unit_function_atomicity` 是本任务所称的原子性：一个执行单元只有一个稳定的
`atomic_capability_id`、一个主要动作、一个事实人口或一种确定性变换、一个失败单位。复杂
问题必须由 InvestigationPlan 在单元外组合，不能靠一个“万能 Tool”或“复合 Operator”内部
编排多个能力。

功能原子性不等于“函数代码很短”，也不要求每个过滤条件变成新 Tool。判断标准是：改变某个
参数是否改变了单元的事实人口、输出语义、权限、Evidence 类型或失败边界；如果改变，就必须
拆分。

### 13.2 Tool 功能原子性

`tool_single_read_semantic` 要求每个 Tool：

- 只读取一种已命名的事实人口，例如 AS-prefix membership、prefix state、ASN state、
  new-prefix state、RouteState、path evidence 或 RouteEvent；
- 可以对同一人口做结构化过滤、稳定排序和分页，但不能在 Tool 内跨人口 join；
- 不计算严重性排名、状态区间、路径邻接、集合相似度或时间关系；
- 不生成自然语言解释，不调用 LLM，不执行导出；
- 成功返回一种 Typed ResultSet；失败返回该读取动作的失败回执，不返回可发布的半事实。

因此，ASN→成员读取和成员→状态读取是两个 Tool；path evidence 读取和从路径投影 downstream
origin set 也是两个执行单元，后者必须是 Operator。

### 13.3 Operator 功能原子性

`operator_single_transform_semantic` 要求每个 Operator：

- 只执行一种可命名的确定性变换；
- 只消费冻结输入，不读取数据库、网络、文件、当前时间或其他 Tool；
- 不通过 mode 参数在“排序、关联、集合、时间比较、路径邻接”之间切换；
- 一个输出对象可以包含同一变换不可分割的回执字段，但不能夹带第二种派生事实；
- 相同输入摘要、版本和参数产生相同输出摘要；任一必要输入不完整时整个派生失败关闭。

例如，“路径是否包含 ASN 及其位置”属于一个变换；“该 ASN 的直接邻接”是另一个变换；
“投影路径前缀集合”和“投影 downstream origin 集合”因成员 identity 不同，必须拆成两个
Operator。

### 13.4 原子拆分测试

`atomic_split_test` 对每个候选单元逐项提问：

1. 是否包含两个以上业务动词，例如“查询并排序”“读取并比较”“关联并解释”？
2. 子能力能否被不同问题独立复用、授权、缓存、计费、版本化、取消或审计？
3. 不同 mode 是否改变事实人口、Evidence 类型或输出成员 identity？
4. 一个子能力失败时，另一个子能力的结果是否仍有独立意义？
5. 是否需要在单元内部调用另一个 Tool/Operator 才能完成？

任一答案为“是”，默认判定 `split_required`。只有证明这些步骤属于同一不可分割算法，并由
独立 Reviewer 接受，才可判定 `atomic_as_designed`。不得以“减少 Tool 数量”“模型调用方便”
或“接口已经这样实现”为理由豁免。

### 13.5 功能失败单位

`execution_unit_failure_atomicity` 只围绕当前单一功能：

- Tool 的一次规范化查询要么形成合同允许的 complete/preview/incomplete ResultSet 与回执，
  要么形成失败回执；不得混入另一能力的部分输出；
- Operator 要么对整个冻结输入完成单一变换并产生摘要，要么不产生派生事实；
- preview/incomplete 是显式结果状态，不是执行单元借机执行第二种补偿逻辑；
- retry、fallback、跨单元降级由 InvestigationPlan/Host 决定，不得藏在单元内部。

### 13.6 调查运行时提交一致性（独立约束）

以下仍需设计，但它们是组合运行时的事务一致性，不是本任务“Tool/Operator 功能原子性”的
定义：

- `node_result_commit_consistency`：结果、Evidence、identity、digest 与 receipt 一致提交；
- `investigation_revision_commit_consistency`：部分调查以新 revision 提交，不覆盖旧修订；
- `evidence_graph_commit_consistency`：节点、边与引用闭包同时有效；
- `dialog_state_commit_consistency`：只继承已验证并提交的调查状态；
- `export_commit_consistency`：冻结 ResultSet 校验完成后再替换最终导出文件。

这些约束不能用来掩盖复合执行单元。即使一个万能 Tool 能以数据库事务一次提交，它仍然不
满足功能原子性。

## 十四、部分失败、取消与重跑

| 情况 | 行为 |
|---|---|
| 可选路径 Tool 不可用 | 其他时间线/ASN/前缀分支继续，最终标记路径证据缺失 |
| 必需身份 Tool 失败 | 整个计划失败关闭，其他 Tool 不启动 |
| 某页查询失败 | ResultSet 保持 incomplete，不发布完整集合 Operator 结果 |
| Operator 输入 incomplete | 按合同返回 lower-bound/partial/insufficient，不猜总体 |
| 用户取消耗时步骤 | 当前节点 cancelled，依赖节点 skipped，无依赖分支可继续 |
| 用户重跑一个步骤 | 复用同一身份和输入或显式新参数，创建新 execution revision |
| publication 漂移 | 当前计划失败，禁止把新旧结果混合 |
| Registry snapshot 漂移 | 已开始计划继续固定旧快照或由 Host 原子取消，不热切换 |

## 十五、权限、安全、费用和性能

### 15.1 权限

- 查询 Tool 只读，默认 `country_outage:read`；
- audit-only 完整路径需独立细粒度权限；
- Delivery 需要用户明确确认；
- LLM 不能提升权限、扩大分页、跳过确认或改变 export format；
- 所有 Tool 只能读取当前计划绑定的 publication。
- Sol Teacher 与 DS Student 使用相同的事实读取权限；TeacherReference 不能成为权限凭据；
- DS 不得根据 Sol 文本发起额外 Tool 调用，只能由 Host 准入新计划修订。

### 15.2 安全

- prefix、ASN、query、path pattern、page token 和文件名必须结构化验证；
- 导出路径由 Host 分配，用户输入不得直接成为文件系统路径；
- 不读取任意本地文件，不允许 URL/SQL/path traversal；
- 不在 Evidence、日志或导出中写入凭据；
- 超量查询先返回成本/规模提示，等待确认。

### 15.3 费用和性能

- `OP-05` 对 ASN 摘要排序应为低成本；
- 首次变化和持续区间应在 publication 投影时预计算，避免每轮扫描全部 ASN×slot；
- 全路径查询必须分页、限时、限内存并报告 scanned/returned；
- ResultSet 导出不占用模型 token 生成成员；
- 设计阶段必须为每个单元冻结 soft/hard budget、timeout、cancel point 和超限错误；
- 任何优化不得改变成员、排序、Evidence 或摘要。
- 双模型预算分别记录 Sol planning、Sol reference、DS first answer、可选 Sol feedback 和 DS
  revision；不能只报告合计费用掩盖某一角色超限；
- 默认最多两次 Sol 调用、一次 DS 首答和一次经差异反馈的 DS 修订；超过预算必须停止并返回
  回执，不能静默减少证据或跳过 Validator；
- 同步交互必须冻结 latency soft/hard limit；若双模型流程超出交互上限，应转为可取消的调查
  任务，而不是让前端无限等待。

## 十六、最终验收旅程

### J01 身份闭合

Q01/Q02 的所有下游节点继承同一 publication/revision/collector/cohort/Registry snapshot；任一
身份冲突失败关闭。

### J02 完整极值

Q03 同时返回前缀、ASN、IPv4 和 IPv6 `/48` 极值，保持不同单位，不遗漏 IPv6，不生成总
严重度。

### J03 AS 排序

Q04/Q10 按 `peak_invisible_direction_count DESC → peak_complete_prefix_count DESC →
asn ASC` 返回，tie fixture 证明第二、第三排序项，输出 global rank 与 result position。

### J04 AS49666 前缀

Q05/Q06 返回自身固定前缀成员和状态时间线；不得将关系关联前缀混入自身集合。预览和完整
导出成员数、身份与摘要对账。

### J05 时间点前缀

Q07/Q13 从指定时间点返回成员级 normal/partial/complete/unknown，分类人口与聚合轨合同对账，
不使用无关聚合数字替代具体成员。

### J06 新前缀

Q16 返回新前缀成员、首次观察、origin、路径摘要和截止状态；Q17 阻止“全球新分配”误读。

### J07 Prefix×VP

Q14/Q18 对指定 prefix 按需返回方向状态与 RouteState，不预生成全量 VP 矩阵，不把 VP 差异
写成传播。

### J08 路径样本与完整导出

Q19/Q20 默认至少 5 条真实样本并显示总体；完整导出来自冻结 ResultSet，不受普通页面 3 条
样本限制，不把样本写成总体。

### J09 路径结构

Q21 正确区分 path contains 与 direct adjacency；AS_SET/confed/unordered 负例失败关闭；交互图
不作为 v1 验收。

### J10 关系前缀与 downstream 集合

Q22/Q26/Q27 的完整集合、intersection、coverage 和 Jaccard 只在输入完整时发布，预览可导出，
observed downstream 不改名为 customer cone。

### J11 Path-at-time

Q23 必须来自 RouteState at time，并包含 time/prefix/VP/peer/origin/AS_PATH/checkpoint；现有
`concurrent` 不能通过 Oracle。

### J12 外部关系边界

Q29/Q30 返回外部证据需求，不用路径顺序、名称或世界知识猜客户关系。

### J13 证据一致性

Q31/Q32 只能输出同槽、先后、错位、部分一致、冲突、缺失或不可比较；不同人口的峰值错位
不自动判冲突。

### J14 结论边界

Q33 和 Answer Validator 阻止全国中断、用户影响、原因、责任、恢复和 RCA 结论。

### J15 Tool/Operator 功能原子性

向候选目录注入“查询并排序”“读取状态并判断时间关系”“路径包含并计算邻接与集合”的复合
单元，Hook 必须判定 `split_required`。逐个候选证明一个 Tool 只读取一种事实人口、一个
Operator 只执行一种确定性变换，组合只发生在 InvestigationPlan。

### J16 运行时提交一致性

注入 page 3 失败、Operator 超时、publication 漂移、取消、重跑和导出失败，证明旧修订不变、
无半节点、无半边、无伪 complete 文件和无 DialogState 污染。该旅程不能替代 J15。

### J17 Sol→DS 对齐回答

对 28 题逐题执行 `gpt-5.6-sol` Teacher 后再执行冻结版本的 DS Student。证明两者消费同一
GroundingPlan/Evidence digest；Sol 植入错误数字、无效 Evidence ref、customer cone、因果或
恢复结论时 TeacherReference 必须被拒绝，DS 不得学习该内容。DS 首答和最多一次修订分别
评分，最终发布答案的事实精度、Evidence 引用和边界硬门必须全部通过。

## 十七、最终设计出口

本设计计划只有同时交付以下结果才可结束：

1. 本目标与最终验收文档；
2. 中文分阶段计划；
3. 28/28 question-capability-execution mapping 与 Oracle seed；
4. TOOL-07..12 的功能原子设计合同，TOOL-13 的 deferred 合同；
5. OP-05..33 的功能原子设计合同，OP-34 的 deferred 合同；
6. 执行单元功能原子性机器合同与逐单元 `atomic_split_test` 回执；
7. InvestigationPlan、ResultSet、Evidence Graph 与运行时提交一致性机器合同草案；
8. Delivery/Validator 的治理处置与生命周期影响分析；
9. 完整 Oracle、费用/性能预算、安全与权限矩阵；
10. 独立产品语义 Reviewer 回执；
11. Sol Teacher、DS Student、Alignment Evaluator 的模型身份、prompt/policy、预算与晋级合同；
12. 28 题 Sol/DS 同证据回放矩阵、差异报告与 DS 非回退证明；
13. 每阶段 Alignment Hook 回执和最终设计候选 manifest；
14. 明确的 P2-S1 实现任务交接，而不是在本任务中直接实施。

## 十八、最终边界声明

本任务的最终状态只能是：`design_contract_accepted_for_implementation_handoff`。

以下表达均禁止：

- “P2 已实现”；
- “28 题已经可以由生产系统回答”；
- “Tool/Operator 已接入运行时”；
- “Evidence Graph 已上线”；
- “页面、API 或导出已经可用”；
- “已合并、已部署、已生产验证”；
- “已经具备 RCA”。

生产部署：禁止。远程写入：禁止。运行时实现：本任务不执行。
