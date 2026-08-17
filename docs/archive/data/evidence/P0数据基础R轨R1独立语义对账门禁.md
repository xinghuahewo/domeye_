# P0 数据基础 R 轨 R1 独立语义对账门禁

阶段：R1 独立语义对账门禁（出口交付物）  
最终效果合同：[P0 数据基础 R 轨最终验收文档](../P0数据基础R轨最终验收文档.md)（RFA-02）  
实现：`dev/data_quality/p0_independent_semantic_reconciliation.py`  
首次运行报告：`./P0数据基础R轨R1独立语义对账报告.json`  
记录日期：2026-07-26  
执行方式：只读（`domeye_core_reader` + `set_session(readonly=True)`），未写入任何数据

## 一、权威卡片

| 项 | 值 |
|---|---|
| 分析对象 | 固定数据档 `feb-mar-2026` 的二三月事实表与特征表 |
| 数据库 | `10.99.8.16` → `127.0.0.1:31627`，`bgp_project` |
| 连接角色 | `domeye_core_reader`（只读） |
| 后端运行身份 | `20260726T131500Z-events-search-timefilter-prod-04` |
| data_release_id | `20260720T160000Z-p0-legacy` |
| 数据来源类型 | 旧事实表（非 Go 重放包，非并发状态候选） |
| 结论类型 | **数据测量**（本文首次出现该类型；R0 为代码事实） |

## 二、门禁定义与命名分离（RFA-02）

| | 源精确对账 | 独立语义对账（本门禁） |
|---|---|---|
| 基准 | 源表逐值比较 | 各指标**自身声明的定义**，读取侧重算 |
| 证明什么 | 旁路**复制保真** | **语义自洽** |
| 不能证明什么 | 旧表本身正确 | 数据正确 |
| 可同时成立 | 差异 = 0 | 仍可失败 |

门禁标识：`independent_semantic_reconciliation`，并在报告中显式声明
`"is_not": "source_exact_reconciliation"`。

`verdict` 语义已写入报告：`consistent` 仅表示所检验恒等式成立，不表示数据正确；
`inconsistent` 只登记不一致事实，不指认责任方，修复不属本门禁。

## 三、已实现恒等式

| 恒等式 | 定义 |
|---|---|
| `as_outage_ratio_identity` | `max_outage_prefix_ratio == max_outage_prefix_num / total_prefix_num` |
| `country_outage_ratio_identity` | `max_outage_as_ratio == max_outage_as_num / total_as_num` |
| `outage_subset_bound` | `outage_num <= total_num` |
| `duration_identity` | `duration == e_time - s_time` |
| `time_order_identity` | `s_time <= e_time` |
| `feature_aggregate_identity` | `collect == sum(per-country)`（审计项 F3 的门禁化） |
| `v4_address_identity` | `v4ip_num == v4prefix_num * 256` |

比率列为 `numeric(4,3)`，重算值按同精度量化后比较，避免把存储舍入误判为不一致。

## 四、首次运行结果

`verdict: inconsistent`，共 **204** 条登记（报告样本上限 200/50）。

| 恒等式 | 命中 | 说明 |
|---|---|---|
| `as_outage_ratio_identity` | 147 | `as_outage_202602` 2/417 行、`as_outage_202603` 145/16041 行 |
| `feature_aggregate_identity` | 46（样本上限） | **真实命中 10,083 / 10,266 槽 = 98.2%** |
| `country_outage_ratio_identity` | 4 | `country_outage_202603` 4/732 行 |
| `time_order_identity` | 3 | 合计 21 行：`event_table_202603` 12 行、`prefix_outage_202603` 9 行 |
| `duration_identity` | 0 | 通过 |
| `outage_subset_bound` | 0 | 通过 |
| `v4_address_identity` | 0 | 通过 |

### 4.1 F3 已由测量确认

审计项 F3（全局计数 ≠ 各国家计数之和）此前只是代码层面的可能性，现已测得量级：

- **10,083 / 10,266 个 collect 槽（98.2%）不满足该恒等式**；
- 样本 `2026-02-24 08:00:00`：`collect_announ=333,475` vs `sum_country=333,446`（差 29）；
  `collect_withdraw=23,187` vs `sum_country=187`（**差 23,000**，即该槽 99.2% 的回撤
  未归属到任何国家）。

结论类型：**数据测量**。差异成因不在本阶段判定，属 R2。

### 4.2 比率恒等式的候选解释（待 R2 确认）

`as_outage_ratio_identity` 样本：`asn=213644, num=3, total=6, stored=0.600, recomputed=0.500`。

候选解释（**代码事实，非本阶段结论**）：`max_outage_prefix_num`、
`max_outage_prefix_ratio`、`total_prefix_num` 在 BGPOutage 中分别独立更新为各自的
运行期最大值，三者取自不同时刻，因此并不构成一个自洽三元组。若成立，则该"比率"
并非所存 num/total 之比，读者按字面理解会得到错误口径。

该解释必须由 R2 以有界原始重算确认，不得据代码阅读结案。

### 4.3 与 P0 §9 阈值的开放问题

P0 数据基础建设计划 §9 将「`end_time < start_time`」列为阻断指标，门槛为 **0**。
本门禁在旧事实表测得 21 行 `s_time > e_time`。

二者未必矛盾：§9 可能针对旁路规范化后的数据，本门禁针对旧事实表原样。
**该差异登记为开放问题，交 R2 判定口径归属**，本阶段不下结论。

## 五、RFA-02 命名纪律审计

仓库内 `对账` 出现 216 处（不含 R 轨文档）。其中：

- `contracts/research/reconciliation-result.schema.json`：「主张级对账」——研究侧主张比对，语义明确；
- `contracts/info/static-info-shadow-diff-v1.schema.json`：「影子语义对账」——已带限定词；
- 其余多数位于 `backend/data_pipeline/research/rrc25_country_outage/`，为伊朗研究的
  主张/覆盖/里程碑比对，均带限定词或上下文明确。

**未发现以无限定词「对账」直接指代数据正确性的表述。** 本门禁引入后，
「独立语义对账」与「源精确对账」两个名称均带限定词，RFA-02 命名分离成立。

## 六、R1 出口判定

| 出口条件 | 状态 |
|---|---|
| 独立语义对账门禁具名存在 | 已满足 |
| 以独立基准重算（不以源表为基准） | 已满足 |
| 只读、可重复执行 | 已满足（只读角色 + 只读事务，无写操作） |
| 产出可归档报告 | 已满足（JSON 报告已归档入库） |
| 无以无限定词「对账」指代正确性的表述 | 已满足（审计 216 处） |
| RFA-02 成立 | 已满足 |

**R1 出口达成。** 门禁首次运行即 `inconsistent`，属预期内——该门禁的价值正在于
它能在源精确对账差异为 0 的同一份数据上失败。

## 七、边界自证

1. 未修改 `backend/core/`；
2. 未修复门禁发现的任何问题（R1 边界：只定义并运行）；
3. 零写操作：使用 `domeye_core_reader` 只读角色并显式 `set_session(readonly=True)`；
4. 未以源精确对账结果宣称数据正确——本门禁与之显式分离；
5. 未以代码阅读结论宣称数据被污染——4.2 的解释明确标注为待 R2 确认的候选；
6. 未把缺测或不一致表示为 0；
7. 未声明 `raw_traceable`；
8. 结论类型已标注为数据测量，与 R0 的代码事实区分。
