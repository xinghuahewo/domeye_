# 确定性Operator设计与目录

> 文档口径：基于2026-08-14本地W6 v3工作树。Operator实现和本地Dispatcher准入不自动表示问题端到端支持或生产部署。

## 1. Operator的定义

Operator是：

> 对一个已经验证、类型化并满足完整性要求的输入，执行一次确定性业务变换的纯执行单元。

Operator不是数据库查询，不是模型提示词，也不是任意脚本。它的输出是Derived Fact、结构化集合或验证回执，而不是最终自然语言答案。

## 2. 通用执行约束

当前Operator设计合同要求：

- pure function；
- 禁止网络访问；
- 禁止读取数据源；
- 禁止调用模型或其他Tool/Operator；
- 禁止状态写入；
- 禁止读取当前时钟；
- 禁止随机性；
- whole-call失败，不发布部分成功；
- 输入身份必须相等；
- 要求完整输入的Operator不得接受预览页；
- 参数Profile必须绑定版本和摘要；
- 输出必须继承输入Evidence并生成确定性摘要。

权威合同：[Operator Catalog](../country-outage-agent-p2-s1-w6-offline-certification-v3-work/contracts/agent/country-outage-p2-s1-execution-unit-design/operator-catalog.json)

## 3. P1 Operator

| Operator | 名称 | 唯一变换 |
|---|---|---|
| OP-01 | series_extrema | 对一条登记时序计算首末、最小、最大、首个极值时点和差值 |
| OP-02 | compare_address_families | 比较IPv4和IPv6变化，同时保留各自单位 |
| OP-03 | compose_fact_timeline | 按冻结时间和tie键排序已验证事实 |
| OP-04 | event-window-trend | 把同一publication的登记时序转换为事件窗口趋势事实 |

P1 Operator由TypeScript执行器调用，当前不作为P2 Python Dispatcher的统一输入输出节点。

## 4. P2时间、状态与排名Operator

| Operator | 名称 | 唯一变换 |
|---|---|---|
| OP-05 | as_severity_rank | 按冻结三键对完整ASN汇总集合稳定排名 |
| OP-06 | select_first_state_occurrence | 选择目标状态首次观测槽位并标记左删失 |
| OP-07 | derive_state_intervals | 从完整有序状态序列生成连续半开区间 |
| OP-08 | select_last_state_at_cutoff | 选择不晚于截止时间的最后状态 |
| OP-09 | select_peak_state_observation | 按冻结严重性字段选择峰值及全部并列槽 |
| OP-10 | compute_as_peak_complete_ratio | 计算ASN完全不可见前缀峰值与固定前缀数之比 |
| OP-11 | select_longest_interval | 选择最长状态区间及并列结果 |
| OP-12 | rank_as_first_threshold_crossing | 按首次越阈时间对完整ASN回执集合排名 |
| OP-13 | rank_as_longest_duration | 按最长异常区间时长排名ASN |
| OP-14 | rank_as_peak_complete_ratio | 按complete峰值比例排名ASN |
| OP-35 | select_last_state_occurrence | 选择目标状态最后一次被观测的槽位 |
| OP-36 | detect_first_threshold_crossing | 按冻结阈值检测首次穿越并标记左删失 |
| OP-38 | intersect_state_interval_sets | 计算两个同网格完整半开区间集合的时间交集 |

## 5. P2路径、投影、集合与计数Operator

| Operator | 名称 | 唯一变换 |
|---|---|---|
| OP-15 | locate_asn_positions | 定位一个ASN在规范AS_PATH中的全部位置 |
| OP-16 | project_direct_path_neighbors | 从同一路径和OP-15回执投影左右直接邻接 |
| OP-17 | classify_ordered_asn_path_relation | 判断两个ASN在同一路径中的有序关系 |
| OP-18 | project_path_prefix_set | 从完整路径人口投影唯一前缀集合 |
| OP-19 | project_observed_downstream_origin_set | 从已验证anchor-before-origin人口投影观测origin集合 |
| OP-20 | project_canonical_path_set | 投影唯一规范路径集合 |
| OP-21 | project_peer_direction_set | 投影唯一RRC25观察方向集合 |
| OP-22 | count_unique_paths | 计算完整规范路径集合成员数 |
| OP-23 | count_unique_prefixes | 计算完整前缀集合成员数 |
| OP-24 | count_unique_peer_directions | 计算完整观察方向集合成员数 |
| OP-25 | set_intersection | 计算两个同类型完整集合的交集 |
| OP-26 | set_directional_difference | 计算左集合减右集合的有向差 |
| OP-27 | set_directional_coverage | 计算交集相对于指定左集合的覆盖率 |
| OP-28 | set_jaccard | 计算两个同类型完整集合的Jaccard相似度 |
| OP-39 | project_fixed_cohort_prefix_set | 从完整固定cohort人口投影唯一prefix key集合 |

OP-19的输出只能称为“观测下游origin集合”，不得重命名为customer cone或客户区。

## 6. P2时间关系、VP一致性与精确连接Operator

| Operator | 名称 | 唯一变换 |
|---|---|---|
| OP-29 | classify_temporal_evidence_relation | 按冻结可比性Profile分类两个类型化时间事实的有向关系 |
| OP-30 | classify_vp_visibility_consistency | 按expected direction人口分类同prefix同槽可见性一致性 |
| OP-31 | classify_vp_origin_consistency | 分类同prefix同槽origin集合一致性 |
| OP-32 | classify_vp_path_consistency | 分类同prefix同槽规范路径一致性 |
| OP-33 | join_new_prefix_route_state | 按精确prefix、AFI和槽位连接new-prefix与RouteState |
| OP-37 | classify_evidence_consistency | 依据OP-29回执和冻结Profile分类证据一致、部分一致、冲突、缺失或不可比 |

## 7. P2.1延期Operator

| Operator | 名称 | 状态 | 原因 |
|---|---|---|---|
| OP-34 | classify_route_change | deferred_p2_1 | 依赖TOOL-13 RouteEvent与相邻前后RouteState，未进入P2 v1 |

OP-34不能因Python函数存在、模型会生成输入或调用者提供自述Evidence而越过Dispatcher准入。

## 8. Profile为什么必须冻结

部分Operator的结果取决于业务Profile，例如：

- OP-05的排名键和并列规则；
- OP-06、OP-35的目标状态；
- OP-29的可比性和时间容差；
- OP-36的阈值与穿越方向；
- OP-37的一致性分类规则。

这些参数不能由模型临时创造。输入和输出必须复制相同的 `parameter_profile_id` 与 `parameter_profile_digest`。摘要漂移意味着执行合同改变，应拒绝执行，而不是让模型“解释一下”。

## 9. 完整性与空值语义

Operator必须区分：

| 状态 | 含义 |
|---|---|
| empty | 合法的空集合或没有匹配结果 |
| missing | 必须字段或成员缺失，通常whole-call失败 |
| unknown | 数据明确表达未知，不应自动当作0或false |
| not_computable | 输入存在但根据合同无法计算 |
| incomplete | 输入人口未闭合，不得运行要求完整输入的Operator |

例如，第一页路径样本即使有5条记录，也不能直接交给OP-22宣称“总共有5条唯一路径”。

## 10. Operator与Host的边界

Host可以验证和搬运结构，但不能替Operator计算业务结果。

```text
合法Host行为：取字段、类型检查、身份复制、Evidence绑定、生成投影回执
必须Operator化：排序、阈值判断、区间生成、集合运算、时间关系、一致性分类
```

如果Host Adapter加入了会改变答案的业务规则，这个Adapter实际上已经成为未登记Operator。

## 11. 当前实现状态

P2 Python侧已实现除OP-34外的OP-05～39，并在W1～W4形成handler和测试证据。W5 Dispatcher将这些Operator准入为本地隔离执行单元。

但“Operator存在并已准入”与“问题可以执行”不同。当前正式Planner、上游ResultSet适配、P1制品桥和问题模板仍不足，因此W6没有任何一题达到 `executed_supported`。

实现入口：

- [P2 Operator实现](../country-outage-agent-p2-s1-w6-offline-certification-v3-work/backend/services/country_outage_p2_s1_operators.py)
- [W1 Registry证据](../country-outage-agent-p2-s1-w6-offline-certification-v3-work/contracts/agent/country-outage-p2-s1-implementation/wave-evidence/registry-runtime/W1.json)
- [W2 Registry证据](../country-outage-agent-p2-s1-w6-offline-certification-v3-work/contracts/agent/country-outage-p2-s1-implementation/wave-evidence/registry-runtime/W2.json)
- [W3 Registry证据](../country-outage-agent-p2-s1-w6-offline-certification-v3-work/contracts/agent/country-outage-p2-s1-implementation/wave-evidence/registry-runtime/W3.json)
- [W4 Registry证据](../country-outage-agent-p2-s1-w6-offline-certification-v3-work/contracts/agent/country-outage-p2-s1-implementation/wave-evidence/registry-runtime/W4.json)
