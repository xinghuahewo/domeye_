# 原子Tool设计与目录

> 文档口径：基于2026-08-14本地W6 v3工作树。设计Catalog、后续Wave实现和生产状态是三个不同层次，本文分别标注。

## 1. Tool的定义

在当前Agent架构中，Tool不是“任何可调用函数”，而是：

> 在冻结身份和权限下，只读取一个明确事实人口，并返回可验证、可分页、可审计结果的执行单元。

Tool可以有过滤条件，但过滤不能改变事实人口的语义。Tool不负责排名、解释、跨人口联接或回答生成。

## 2. 原子性原则

一个合格Tool应满足：

1. 一次调用只读取一种事实人口；
2. 不调用模型；
3. 不调用其他Tool；
4. 不执行排名、聚合解释或因果推断；
5. 不把预览页伪装为完整人口；
6. 输入绑定incident、publication、revision、collector、cohort和window；
7. 输出携带稳定排序、成员身份、去重键、Evidence引用和完整性语义；
8. 空、缺失、未知、无权限、超时和身份漂移具有不同结果；
9. Tool读取成功不自动代表事实可以进入最终回答；
10. 组合只能发生在Plan和Host中。

## 3. Tool合同最少应包含的内容

| 合同维度 | 说明 |
|---|---|
| 身份 | incident、publication、revision、collector、cohort、window |
| 输入Schema | 必填、可选、互斥参数和类型 |
| 人口 | Tool读取的唯一source population |
| 输出Schema | 成员字段、值状态、Evidence字段 |
| 成员身份 | member identity和dedupe key |
| 排序与分页 | stable sort、page token、total count、continuation约束 |
| 完整性 | complete、partial page、source incomplete等 |
| 时间语义 | 精确时点、窗口、半开区间、data-through含义 |
| 权限 | 所需principal scope |
| 错误 | invalid input、identity mismatch、timeout等 |
| 禁止用途 | 不允许内嵌的计算和不允许发布的结论 |
| 版本身份 | unit ID、version、contract/semantic/implementation digest |

## 4. P1 Tool目录

P1 Tool来自当前统一Registry中的active条目，并由TypeScript执行器实际调用。

| Tool | 名称 | 唯一职责 | 主要限制 |
|---|---|---|---|
| TOOL-01 | resolve_event_binding | 解析事件引用并绑定事件、publication和revision | 不把当前事件能力外推到其他事件 |
| TOOL-02 | read_event_overview | 读取窗口、data-through、finality、质量、当前人口和峰值概览 | data-through不等于事件结束 |
| TOOL-03 | read_metric_series | 读取15轨正式RRC25时序、定义、单位和人口 | 不在Tool内计算趋势或跨单位合并 |
| TOOL-04 | query_affected_asns | 分页查询受影响ASN及指定ASN详情 | 页面或单页不等于完整ASN人口 |
| TOOL-05 | query_path_evidence | 查询路径相邻关联和有限真实AS_PATH样本 | 当前是有限样本，不是完整路径人口 |
| TOOL-06 | read_audit_identity | 读取publication、dataset、实现和文件摘要身份 | 只提供审计身份，不产生业务解释 |

Registry证据：

- [Execution Unit Registry](../country-outage-agent-p2-s1-w6-offline-certification-v3-work/contracts/agent/country-outage-p2-s0a-lifecycle/execution-unit-registry.json)
- [P1执行器](../country-outage-agent-p2-s1-w6-offline-certification-v3-work/agent-sidecar/src/chat/page-capability-executor.ts)

## 5. P2 Tool目录

### 5.1 P2 v1已实现Tool

| Tool | 名称 | 唯一事实人口 | 核心用途 |
|---|---|---|---|
| TOOL-07 | query_fixed_cohort_members | fixed_cohort_member_rows | 读取固定cohort成员，可在同人口内按ASN、prefix、AFI过滤 |
| TOOL-08 | query_prefix_states | prefix_state_rows | 读取前缀状态序列或精确状态记录 |
| TOOL-09 | query_as_states | as_state_rows | 读取ASN状态事实人口 |
| TOOL-10 | query_new_prefix_states | new_prefix_state_rows | 读取固定cohort之后首次观测的新前缀状态 |
| TOOL-11 | query_materialized_route_states_at_time | route_state_rows_at_exact_time | 读取一个精确时点预物化RouteState，不在Tool内回放 |
| TOOL-12 | query_window_path_associations | window_path_association_rows | 读取publication窗口级路径关联Evidence人口 |

这些Tool已经存在Python handler并进入W5本地隔离执行准入，但不表示生产部署。

实现入口：[P2 Tool实现](../country-outage-agent-p2-s1-w6-offline-certification-v3-work/backend/services/country_outage_p2_s1_tools.py)

### 5.2 P2.1延期Tool

| Tool | 名称 | 状态 | 原因 |
|---|---|---|---|
| TOOL-13 | query_route_events | deferred_p2_1 | RouteEvent人口和相邻状态变化分类不在P2 v1执行范围 |

TOOL-13在Dispatcher中必须被拒绝，不能通过动态handler名称或模型生成调用绕过。

## 6. Tool结果为什么需要ResultSet

Tool一次返回的page不是Operator可以直接当作完整集合使用的制品。

Host需要把分页过程冻结为ResultSet：

```text
Tool Query
  ↓
Page 1 → Page 2 → ... → Page N
  ↓
校验身份、stable sort、continuation、去重和total count
  ↓
冻结ResultSet
  ↓
Operator只消费被声明为完整、且满足合同的输入
```

ResultSet解决四个问题：

- Operator究竟在计算哪个成员人口；
- 页面预览与完整人口的区别；
- 查询期间身份和数据是否漂移；
- 重跑和后续追问是否还能引用同一集合。

## 7. Tool与Host结构适配器的边界

以下动作可以是Host结构适配：

- 从验证过的字段路径取值；
- 将ResultSet成员映射到Operator声明的typed input；
- 复制身份和Evidence引用；
- 生成population binding receipt；
- 做固定且有上限的结构展开；
- 验证成员数、Schema和摘要。

以下动作不能藏在Adapter里：

- 选择“最严重”的ASN；
- 判断两个时间是否一致；
- 计算集合交、差、覆盖率；
- 推断上下游商业关系；
- 选择业务阈值；
- 改变人口或补齐不存在的成员。

判断标准是：如果这个变换可能改变问题的业务答案，它应当是登记的Operator；如果它只是保证同一数据在不同合同之间可验证地搬运，它可以属于Host结构层。

## 8. 当前Tool层的关键断层

当前P1 Tool结果主要进入Evidence Bundle，P2 Tool结果进入ResultSet。P1的TOOL-01～06没有被P2 Python Dispatcher作为同身份执行制品统一消费。

因此当前并不是Tool数量不足，而是：

- 同一个正式Plan不能稳定组合P1和P2 Tool；
- P1分页或事实结果没有统一Typed Artifact桥；
- ResultSet到下游Operator的通用结构绑定不完整；
- 正式Planner没有覆盖所有已实现P2 Tool。

## 9. 权威设计与实现入口

- [Tool Catalog](../country-outage-agent-p2-s1-w6-offline-certification-v3-work/contracts/agent/country-outage-p2-s1-execution-unit-design/tool-catalog.json)
- [Tool Runtime Schema](../country-outage-agent-p2-s1-w6-offline-certification-v3-work/contracts/agent/country-outage-p2-s1-implementation/w1-w2-tool-runtime.schema.json)
- [W1 Registry证据](../country-outage-agent-p2-s1-w6-offline-certification-v3-work/contracts/agent/country-outage-p2-s1-implementation/wave-evidence/registry-runtime/W1.json)
- [W2 Registry证据](../country-outage-agent-p2-s1-w6-offline-certification-v3-work/contracts/agent/country-outage-p2-s1-implementation/wave-evidence/registry-runtime/W2.json)
- [W4 Registry证据](../country-outage-agent-p2-s1-w6-offline-certification-v3-work/contracts/agent/country-outage-p2-s1-implementation/wave-evidence/registry-runtime/W4.json)
