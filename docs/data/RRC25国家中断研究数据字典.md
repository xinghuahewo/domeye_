# RRC25 国家中断研究数据字典

## 1. 文档身份与适用范围

| 项目 | 值 |
| --- | --- |
| 字典版本 | `rrc25_country_outage_research_dictionary_v1` |
| 研究 Profile | `config/research/iran-rrc25-202602.json` |
| `study_id` | `iran-rrc25-country-outage-202602-v1` |
| 采集点 | `rrc25` |
| 验收国家 | `IR` |
| 时间基准 | UTC，RFC 3339 秒级时间，必须以 `Z` 结尾 |
| 研究窗口 | `[2026-02-27T16:00:00Z, 2026-03-06T08:40:00Z)` |
| 观察终点 | `2026-03-06T08:40:00Z` |
| 样本粒度 | 300 秒 |
| 规范输出 | 规范 JSONL gzip；Parquet 仅为可选投影 |

本字典逐项解释 `contracts/research/` 下七份 JSON Schema：

1. `research-profile.schema.json`；
2. `country-outage-sample.schema.json`；
3. `country-outage-episode.schema.json`；
4. `country-outage-wave.schema.json`；
5. `country-outage-episode-as.schema.json`；
6. `research-run.schema.json`；
7. `reconciliation-result.schema.json`。

七份合同均使用 JSON Schema 2020-12，顶层 `additionalProperties=false`。本文是中文语义说明；字段类型、必填性、枚举和正则约束以对应 Schema 为机器权威，跨字段约束以 `dev/data_quality/validate_research_contracts.cjs` 为机器权威。新增字段、改变字段含义或改变稳定 ID 截断长度时，必须升级合同版本，不能静默沿用 v1。

## 2. 全局语义

### 2.1 数据流与对象关系

```text
research_profile
  -> research_run
      -> country_outage_sample（五分钟同快照事实）
          -> country_outage_episode（持续异常）
              -> country_outage_wave（同一 episode 内的再次下降）
              -> country_outage_episode_as（逐 ASN、逐地址族影响）
      -> RouteEvent / raw record
      -> reconciliation_result（报告主张逐条对账）
```

- Profile 冻结窗口、输入选择、算法、映射、资源上限和输出政策。
- Sample 是最小不可变国家状态样本。Episode、wave 和逐 ASN 记录都必须能回指 sample。
- 旧 Incident 继续保留旧事实身份。研究对象通过 `incident_mappings`、对账引用和运行输出与 Incident 关联，不修改旧 Incident 的字段含义。
- 新计算得到的 onset、wave 或 recovery 直接保存在研究对象中，不写成旧事实原文。

### 2.2 时间与半开区间

- Profile 的研究窗口固定为 `[start_utc,end_exclusive_utc)`：起点包含，终点不包含。
- 本次窗口的 UPDATE 槽从 `2026-02-27T16:00:00Z` 开始，最后一个槽从 `2026-03-06T08:35:00Z` 开始；`08:40:00Z` 只作为状态和观察边界，不是一个被纳入窗口的 UPDATE 槽。
- 每条 sample 的 `slot.boundary` 固定为 `[start,end)`，`granularity_seconds` 固定为 300；语义校验要求 `slot.end - slot.start = 300 秒`。
- 日期时间必须是合法 UTC 秒级字符串，例如 `2026-02-27T16:00:00Z`；不得写本地时区、毫秒或无时区时间。
- `partial_recovery_at`、`full_recovery_at`、`rebound_at`、逐 ASN 恢复时间和运行结束时间允许为 `null`。`null` 表示相应事实未被确认或当前未知，不能合成为窗口末端，也不能解释为 0 秒。

### 2.3 缺失、真实零与空集合

所有可能缺失的研究测量都遵循“未知不是零”。适用的状态如下：

| 状态 | `value` | `missing_reason` | 含义 |
| --- | --- | --- | --- |
| `observed` | 正数，或非空集合 | `null` | 来源完整且实际观察到非零值/非空集合 |
| `observed_zero` | 数值 `0` | `null` | 来源完整且实际观察到零 |
| `observed_empty` | 空数组 `[]` | `null` | 来源完整且实际观察到空集合 |
| `unknown_source_gap` | `null` | 非空原因码 | 期望输入源缺失 |
| `unknown_parse_failure` | `null` | 非空原因码 | 输入存在但解析失败 |
| `unknown_mapping` | `null` | 非空原因码 | 国家/ASN 映射不足以计算 |
| `unknown_state_gap` | `null` | 非空原因码 | 状态回放连续性中断 |

`country-outage-episode-as` 的 `visibility` 使用同一组 unknown 状态，但字段名为 `visibility_state`，未知时 `fully_invisible=null`。对账主张另使用 `reported`、`recomputed`、`unknown`、`not_applicable`，见第 9 节。

### 2.4 同一快照比例

`country_outage_sample` 中每个 measure 和 ASN set 都必须携带父记录的 `sample_id` 与 `snapshot_id`。`damaged_asn_ratio` 还必须满足：

- ratio 自身、`numerator` 和 `denominator` 的 `sample_id` 均等于父 sample；
- ratio 自身、分子和分母的 `snapshot_id` 均等于父 snapshot；
- 分母至少为 1；
- `value = numerator.value / denominator.value`，语义校验容差为 `1e-12`；
- 比例为 0 时必须标记 `observed_zero`；
- 任一必要成分未知时，分子、分母和比例值全部为 `null`，不得混用其他时点补齐。

因此“受影响 ASN 比例”不能用一个时点的分子除以另一个时点的分母，也不能复用旧事件表中的峰值人数与其他分母拼接。

### 2.5 稳定 ID 与哈希格式

本组 Schema 约束的是格式；具体 canonical identity 输入由相应生成器版本决定。禁止仅为满足正则伪造 ID。

| 对象 | 格式 |
| --- | --- |
| 研究运行 | `research_run_v1_` + 24 位小写十六进制 |
| 五分钟样本 | `sample_v1_` + 24 位小写十六进制 |
| 状态快照 | `snapshot_v1_` + 24 位小写十六进制 |
| Episode | `episode_v1_` + 24 位小写十六进制 |
| Wave | `wave_v1_` + 24 位小写十六进制 |
| Episode-AS | `episode_as_v1_` + 24 位小写十六进制 |
| 对账结果 | `reconciliation_v1_` + 24 位小写十六进制 |
| 对账证据 | `evidence_v1_` + 24 位小写十六进制 |
| 对账主张 | `claim_v1_` + 24 位小写十六进制 |
| RouteEvent | `rte_v1_` + **32 位**小写十六进制 |
| 原始记录引用 | `raw_v1_` + **32 位**小写十六进制 |
| 原始制品 | `art_v1_` + **32 位**小写十六进制 |
| SHA-256 | 64 位小写十六进制 |

`record_ordinal` 与 `element_ordinal` 均为从 0 开始的非负整数。RouteEvent 的可核验原始坐标是 `artifact_id + artifact_sha256 + record_ordinal + element_ordinal`；gzip 流偏移不得替代这组主引用。

### 2.6 兼容视图与修订视图

两套口径必须并行、显式命名，不能互相覆盖：

- Profile 的 `measurement.views` 枚举拼写为 `compatibility`、`revised`；
- 输出记录的 `cohort_view` 和映射引用的 `view` 枚举拼写为 `compatible`、`revised`。

这里 `compatibility` 与 `compatible` 是合同层级不同导致的固定拼写，不能擅自统一。兼容视图使用冻结的旧静态 AS→国家映射；修订视图是独立、非覆盖投影。映射未知与映射冲突必须保留，不能随意选择一个国家后标记为确定事实。

### 2.7 双栈与 MOAS

- IPv4、IPv6 分地址族计算，再形成综合分类。
- IPv4 国家总量使用去重地址并集，并可投影为 `/24` 等价值；IPv6 使用 `/48` 等价值。
- IPv6 `/48` 等价值不是“真实可用地址数”。
- MOAS 必须保留全部 origin 关系。逐 ASN 记录可以让同一前缀关联多个 origin，但这些逐 ASN 地址量不可相加作为国家总量。
- `country_outage_episode_as.address_families.*.moas_semantics` 固定为 `origin_relationship_retained_not_additive`。

## 3. `research-profile`：研究参数合同

`schema_version` 固定为 `research_profile_v1`。Profile 是一次研究的输入和算法约束，不是运行结果。

### 3.1 顶层字段

| 字段 | 类型/约束 | 语义 |
| --- | --- | --- |
| `$schema` | const | `https://domeye.example/contracts/research/research-profile.schema.json` |
| `schema_version` | const | `research_profile_v1` |
| `study_id` | string | 3–128 字符，首字符为小写字母或数字，其余可含 `._-` |
| `profile_kind` | const | `bounded_country_outage_research` |
| `collector_id` | string | 小写稳定采集点 ID；本次为 `rrc25` |
| `country_code` | string | 两位大写国家码；本次为 `IR` |
| `time_basis` | const | `UTC` |
| `window` | object | 研究和观察边界 |
| `input_selection` | object | RIB/UPDATE 输入角色与期望槽数 |
| `country_mapping` | object | 兼容/修订映射政策 |
| `baseline` | object | 基线成员、数值基线和正常带 |
| `measurement` | object | 双栈、聚合、MOAS、缺失口径 |
| `algorithms` | object | episode、wave、恢复算法参数 |
| `resource_limits` | object | 读取、临时空间、运行时间和数据库边界 |
| `output_policy` | object | 文件格式、不可变发布与禁止范围 |

### 3.2 `window`

| 字段 | 固定/允许值 | 本次值或说明 |
| --- | --- | --- |
| `start_utc` | UTC 秒级时间 | `2026-02-27T16:00:00Z` |
| `end_exclusive_utc` | UTC 秒级时间 | `2026-03-06T08:40:00Z` |
| `observation_end_utc` | UTC 秒级时间 | `2026-03-06T08:40:00Z` |
| `granularity_seconds` | 正整数 | `300` |
| `interval_semantics` | const `half_open` | 半开窗口 |
| `end_boundary_role` | const `state_boundary_only_excluded_from_updates` | 终点仅作状态边界，不纳入 UPDATE |

### 3.3 `input_selection`

| 字段路径 | 固定/允许值 | 语义 |
| --- | --- | --- |
| `filename_timestamp_timezone` | const `UTC` | 文件名时间按 UTC 解释 |
| `state_seed_rib.selection_policy` | const `complete_at_start_or_nearest_complete_before` | 优先起点完整 RIB，否则取此前最近完整 RIB |
| `state_seed_rib.allow_at_window_start` | const `true` | 允许起点 RIB 直接作为状态 seed |
| `state_seed_rib.complete_required` | const `true` | seed 必须完整 |
| `baseline_reference_rib.selection_policy` | const `nearest_complete_strictly_before_start` | 取严格早于起点的最近完整 RIB |
| `baseline_reference_rib.strictly_before_window_start` | const `true` | 不允许起点 RIB冒充前置参考 |
| `baseline_reference_rib.complete_required` | const `true` | 前置参考必须完整 |
| `baseline_reference_rib.expected_count` | const `1` | 正好一张参考 RIB |
| `catch_up_updates.condition` | const `required_when_state_seed_precedes_window_start` | seed 早于起点时必须 catch-up |
| `catch_up_updates.start_boundary` | const `state_seed_time_inclusive` | 从 seed 时刻包含开始 |
| `catch_up_updates.end_boundary` | const `window_start_exclusive` | 回放到研究起点之前 |
| `analysis_updates.start_boundary` | const `window_start_inclusive` | 起点包含 |
| `analysis_updates.end_boundary` | const `window_end_exclusive` | 终点不包含 |
| `analysis_updates.slot_interval_seconds` | 正整数 | 本次 300 |
| `analysis_updates.expected_slot_count` | 正整数 | 本次 1,928 |
| `analysis_ribs.start_boundary` | const `window_start_inclusive` | 起点包含 |
| `analysis_ribs.end_boundary` | const `window_end_exclusive` | 终点不包含 |
| `analysis_ribs.slot_interval_seconds` | 正整数 | 本次 28,800 |
| `analysis_ribs.expected_slot_count` | 正整数 | 本次 21 |

`state_seed_rib` 与 `baseline_reference_rib` 是两个输入角色；同一张严格早于起点且完整的 RIB 可以在选择结果中承担两种角色，但每个角色的判定和引用必须显式保存。

### 3.4 `country_mapping`

| 字段 | 固定值 | 语义 |
| --- | --- | --- |
| `compatibility_view` | `frozen_legacy_static_mapping` | 冻结旧静态映射，支持旧口径对照 |
| `revised_view` | `separate_non_overwriting_projection` | 修订映射独立输出，不覆盖兼容结果 |
| `unknown_policy` | `preserve_as_country_mapping_unknown` | 映射未知必须保留 |
| `conflict_policy` | `preserve_as_country_mapping_conflict` | 映射冲突必须保留 |
| `source_binding` | `resolver_manifest_path_and_sha256_required` | 映射来源必须绑定路径和 SHA-256 |

### 3.5 `baseline`

`baseline.membership`：

| 字段 | 固定值 | 语义 |
| --- | --- | --- |
| `source` | `state_seed_rib` | 基线成员从状态 seed 形成 |
| `mapping_view` | `compatibility` | 成员初始归属使用兼容映射 |
| `dynamic_ir_origins` | `include_during_window` | 将窗口中新出现的 IR origin 纳入研究集合 |

`baseline.numeric`：

| 字段 | 固定/允许值 | 本次值或语义 |
| --- | --- | --- |
| `metric` | `ipv4_visible_unique_address_count` | 数值基线核心指标 |
| `candidate_start` | `analysis_window_start` | 从研究起点开始寻找候选基线 |
| `initial_duration_seconds` | 正整数 | 21,600 |
| `statistic` | `median` | 中位数 |
| `dispersion` | `median_absolute_deviation` | MAD |
| `max_relative_mad` | 0–1 | 0.001 |
| `extension_direction` | `forward` | 不稳定时向后扩展 |
| `extension_step_seconds` | 正整数 | 21,600 |
| `max_duration_seconds` | 正整数 | 86,400 |
| `stop_before_exclusion_boundary` | const `true` | 基线扩展不得跨过候选排除边界 |
| `exclusion_boundary.at_utc` | 规范 UTC | 本次为 `2026-02-27T22:00:00Z` |
| `exclusion_boundary.role` | `user_supplied_earliest_possible_precursor_boundary` | 用户提供的最早可能前兆边界 |
| `exclusion_boundary.confirmation_state` | `candidate_not_confirmed` | 不是已确认 onset |
| `exclusion_boundary.causal_claim_allowed` | const `false` | 不授权前兆或因果结论 |
| `unstable_exhausted_state` | `incomplete` | 达上限仍不稳定则运行不完整 |
| `record_actual_window` | const `true` | 必须记录实际使用的基线窗口 |

`baseline.normal_band`：

| 字段 | 固定/允许值 | 本次值或语义 |
| --- | --- | --- |
| `method` | `median_plus_minus_max_scaled_mad_and_absolute_floor` | 使用 MAD 与绝对下限共同避免区间退化 |
| `mad_multiplier` | 非负数 | 3 |
| `absolute_floor_ratio` | 0–1 | 0.001 |
| `version` | 非空 string | `robust_normal_band_v1` |

### 3.6 `measurement`

| 字段 | 固定值 | 语义 |
| --- | --- | --- |
| `address_families` | 顺序固定为 `ipv4`,`ipv6` | 双栈都必须计算 |
| `views` | 顺序固定为 `compatibility`,`revised` | 同时输出兼容与修订口径 |
| `country_prefix_aggregation` | `deduplicated_address_union_per_afi` | 按 AFI 对前缀地址集合去重 |
| `ipv4_metrics` | `visible_unique_address_count`,`slash24_equivalent_count` | IPv4 地址并集与 `/24` 等价值 |
| `ipv6_metrics` | `slash48_equivalent_count` | IPv6 `/48` 等价值 |
| `as_visibility_classification` | `per_afi_then_dual_stack` | 先按 AFI，再综合双栈 |
| `moas_policy` | `preserve_all_origins_do_not_sum_as_totals_as_country_total` | 保留 origin，不相加为国家总量 |
| `missing_value_policy` | `preserve_value_state_never_coerce_to_zero` | 未知永不强制为 0 |

### 3.7 `algorithms`

`algorithms.episode`：

| 字段 | 固定/允许值 | 本次值或语义 |
| --- | --- | --- |
| `version` | 非空 string | `country_outage_episode_v1` |
| `combine_rule` | const `any` | 任一异常条件成立即可形成异常槽 |
| `ipv4_visible_ratio_below` | 0–1 | 0.99 |
| `damaged_as_ratio_above` | 0–1 | 0.03 |
| `confirm_consecutive_slots` | 正整数 | 2 |
| `onset_assignment` | `first_anomalous_slot` | onset 回填到连续异常的首槽 |
| `detected_assignment` | `confirmation_slot` | detected 记在确认槽 |

`algorithms.wave`：

| 字段 | 固定/允许值 | 本次值或语义 |
| --- | --- | --- |
| `version` | 非空 string | `country_outage_wave_v1` |
| `significance_method` | `max_baseline_ratio_or_scaled_mad` | 显著阈值取比例下限与缩放 MAD 较大者 |
| `baseline_ratio_floor` | 0–1 | 0.005 |
| `mad_multiplier` | 非负数 | 3 |
| `new_episode_requires_full_recovery` | const `true` | 完全恢复确认后再异常才拆新 episode |
| `inter_wave_relation` | `unknown_not_causal` | 波次间关系未知，不输出因果前兆 |

`algorithms.recovery`：

| 字段 | 固定/允许值 | 本次值或语义 |
| --- | --- | --- |
| `version` | 非空 string | `country_outage_recovery_v1` |
| `partial_visible_ratio_at_least` | 0–1 | 0.99 |
| `partial_confirm_consecutive_slots` | 正整数 | 6 |
| `full_rule` | `within_versioned_baseline_normal_band` | 回到版本化基线正常带 |
| `full_confirm_consecutive_slots` | 正整数 | 6 |
| `duration_states` | 顺序固定为 `exact`,`lower_bound`,`interval`,`unknown` | 允许的持续时间表达 |
| `recovery_states` | 顺序固定为 `ongoing`,`recovering`,`partially_recovered`,`fully_recovered` | Profile 支持的四种恢复状态 |

输出 Episode 合同还允许 `recovery_state=unknown`，用于输入或连续性不足、无法可靠判定恢复阶段的情况。

### 3.8 `resource_limits` 与 `output_policy`

| 字段 | 本次值/固定值 | 语义 |
| --- | --- | --- |
| `resource_limits.max_new_raw_read_bytes` | 50,000,000,000 | 达到该新增原始读取量前必须停止并取得授权 |
| `resource_limits.max_temporary_bytes` | 5,000,000,000 | 达到该临时空间前必须停止并取得授权 |
| `resource_limits.max_worker_runtime_seconds` | 600 | 单 worker 硬上限 |
| `resource_limits.worker_soft_stop_seconds` | 540 | 在完整 record 边界软停止 |
| `resource_limits.database_writes` | `forbidden` | 数据库写入禁止 |
| `resource_limits.output_storage` | `filesystem_only` | 仅文件系统输出 |
| `output_policy.required_format` | `canonical_jsonl_gzip` | 规范必需格式 |
| `output_policy.optional_projection` | `parquet` | 仅可选分析投影 |
| `output_policy.immutable_outputs` | `true` | 发布制品不可变 |
| `output_policy.atomic_publish` | `true` | 组装完成后原子发布 |
| `output_policy.overwrite_existing` | `false` | 不覆盖已存在制品 |
| `output_policy.frontend_changes` | `forbidden` | 本轮禁止前端改动 |
| `output_policy.production_deployment` | `forbidden` | 本轮禁止生产部署 |

## 4. `country-outage-sample`：五分钟不可变样本

`schema_version` 固定为 `country-outage-sample/v1`。每条记录表达一个国家、一个视图、一个五分钟槽结束后的同一状态快照。

### 4.1 顶层字段

| 字段 | 类型/枚举 | 语义 |
| --- | --- | --- |
| `schema_version` | const | `country-outage-sample/v1` |
| `sample_id` | 稳定 ID | `sample_v1_` + 24 hex |
| `run_id` | 稳定 ID | 所属研究运行 |
| `snapshot_id` | 稳定 ID | 本条所有指标和集合共同使用的状态快照 |
| `collector_id` | string | 稳定采集点 ID |
| `country_code` | 两位大写 string | 国家码 |
| `cohort_view` | `compatible` / `revised` | 该样本使用的国家 cohort 口径 |
| `slot` | object | `start`,`end`,`boundary`,`granularity_seconds` |
| `continuity_state` | `continuous` / `unknown_after_gap` | 是否仍可证明状态连续 |
| `metrics` | object | 13 个同快照指标 |
| `asn_sets` | object | `visible`,`damaged`,`baseline` 三个同快照集合 |
| `source_refs` | array，至少 1 项 | 支撑快照的内容引用 |

输入关键槽缺失或解析失败后，从缺口开始 `continuity_state` 必须为 `unknown_after_gap`，直到有可证明的重新初始化边界；不得继续沿用旧状态并标记为连续。

### 4.2 `slot`

| 字段 | 约束 |
| --- | --- |
| `start` | UTC date-time |
| `end` | UTC date-time |
| `boundary` | const `[start,end)` |
| `granularity_seconds` | const `300` |

### 4.3 Measure 结构

`countMeasure` 用于整数计数；`decimalMeasure` 用于允许小数的等价值。二者均要求 `sample_id`、`snapshot_id`、`value`、`value_state`、`missing_reason`：

- 正值：`countMeasure.value` 为大于 0 的整数，`decimalMeasure.value` 为大于 0 的数，`value_state=observed`；
- 真实零：`value=0`，`value_state=observed_zero`；
- 未知：`value=null`，`value_state` 为四类 unknown 之一，`missing_reason` 为小写原因码，可含 `._:-`。

`ratioMeasure` 在已观测时还要求 `numerator`、`denominator`，二者均包含 `sample_id`、`snapshot_id`、非负整数 `value`；分母最小为 1。比例值范围为 0–1。未知时 `numerator=null`、`denominator=null`、`value=null`。

### 4.4 `metrics`

| 字段 | Measure 类型 | 语义 |
| --- | --- | --- |
| `visible_asn_count` | count | 当前快照仍可见的 cohort ASN 数 |
| `damaged_asn_count` | count | 当前快照受损的 cohort ASN 数 |
| `baseline_asn_count` | count | 当前视图的基线 ASN 数 |
| `visible_ipv4_prefix_count` | count | 可见 IPv4 前缀去重数 |
| `visible_ipv6_prefix_count` | count | 可见 IPv6 前缀去重数 |
| `visible_ipv4_address_union` | count | IPv4 可见地址去重并集大小 |
| `visible_ipv4_24_equivalent` | decimal | IPv4 `/24` 等价值 |
| `visible_ipv6_48_equivalent` | decimal | IPv6 `/48` 等价值 |
| `announce_count` | count | 本槽相关 ANNOUNCE 数 |
| `withdraw_count` | count | 本槽相关 WITHDRAW 数 |
| `vp_expected_count` | count | 本槽预期 VP 数 |
| `vp_observed_count` | count | 本槽实际可观测 VP 数 |
| `damaged_asn_ratio` | ratio | 同快照 `damaged_asn_count / baseline_asn_count` |

### 4.5 `asn_sets`

`visible`、`damaged`、`baseline` 均使用 `asnSet`：

- 非空已观测集合：ASN 为 1–4,294,967,295 的整数，去重，`value_state=observed`；
- 真实空集合：`value=[]`，`value_state=observed_empty`；
- 未知集合：`value=null`，使用四类 unknown 状态和非空原因。

三类集合都必须绑定父 `sample_id` 和 `snapshot_id`。

### 4.6 `source_refs`

| 字段 | 约束 |
| --- | --- |
| `ref_type` | `state_shard` / `route_event_shard` / `input_artifact` / `mapping_snapshot` |
| `ref_id` | 非空稳定引用，只可含字母、数字、`._:/-` |
| `sha256` | 64 hex |

## 5. `country-outage-episode`：持续异常

`schema_version` 固定为 `country-outage-episode/v1`。Episode 是经过连续槽确认的一段持续异常；只有确认完全恢复后再次出现异常，才允许拆成新 episode。

### 5.1 顶层字段

| 字段 | 类型/枚举 | 语义 |
| --- | --- | --- |
| `schema_version` | const | `country-outage-episode/v1` |
| `episode_id` | 稳定 ID | `episode_v1_` + 24 hex |
| `run_id` | 稳定 ID | 所属运行 |
| `collector_id` | string | 采集点 |
| `country_code` | 两位大写 string | 国家码 |
| `cohort_view` | `compatible` / `revised` | cohort 口径 |
| `algorithm_version` | 小写稳定 string | 产生本记录的算法版本 |
| `onset_at` | UTC date-time | 连续异常序列的首个异常槽时点 |
| `detected_at` | UTC date-time | 达到确认槽数的时点，不得早于 onset |
| `peak_at` | UTC date-time | 算法计算的 episode 影响峰值时点 |
| `trough_at` | UTC date-time | 核心可见性指标低谷时点 |
| `partial_recovery_at` | date-time / `null` | 首次确认部分恢复的时点 |
| `full_recovery_at` | date-time / `null` | 首次确认完全恢复的时点 |
| `observation_end_at` | UTC date-time | 本记录实际观察终点，不得早于 detected |
| `recovery_state` | `ongoing` / `recovering` / `partially_recovered` / `fully_recovered` / `unknown` | 观察终点时的恢复状态 |
| `duration` | object | 精确值、下界、区间或未知 |
| `supporting_sample_ids` | 去重数组，至少 2 项 | 支撑 episode 的 sample |
| `wave_ids` | 去重数组，至少 1 项 | 所属 wave |
| `split_evidence` | 去重数组 | episode/wave 分割判定证据 |
| `incident_mappings` | 去重数组，至少 1 项 | 与旧 Incident 的非因果映射 |

### 5.2 `duration`

所有分支都必须带 `duration_state`、`seconds`、`minimum_seconds`、`maximum_seconds`、`measured_to`：

| `duration_state` | `seconds` | `minimum_seconds` | `maximum_seconds` | `measured_to` | 语义 |
| --- | --- | --- | --- | --- | --- |
| `exact` | 非负整数 | `null` | `null` | date-time | 已确认结束，可精确计算 |
| `lower_bound` | `null` | 非负整数 | `null` | date-time | 到观察终点仍未确认结束，只有下界 |
| `interval` | `null` | 非负整数 | 非负整数 | date-time | 缺口使结束只能落在区间内；上界不得小于下界 |
| `unknown` | `null` | `null` | `null` | `null` | 无法可靠计算 |

不得用 `observation_end_at - onset_at` 冒充仍在进行事件的精确持续时间；这种情况应使用 `lower_bound`。

### 5.3 `split_evidence`

| 字段 | 类型/枚举 | 规则 |
| --- | --- | --- |
| `decision` | `same_episode_new_wave` / `new_episode` | 分割决策 |
| `left_sample_id` | sample ID | 分割左侧样本 |
| `right_sample_id` | sample ID | 分割右侧样本 |
| `full_recovery_confirmed` | boolean | 中间是否确认完全恢复 |
| `reason_code` | `full_recovery_six_slots` / `partial_rebound_only` / `continuity_unknown` | 判定原因 |

- `decision=new_episode` 时必须 `full_recovery_confirmed=true` 且 `reason_code=full_recovery_six_slots`。
- `decision=same_episode_new_wave` 时必须 `full_recovery_confirmed=false`。
- 连续性未知时只能如实记录 `continuity_unknown`，不能补造恢复边界。

### 5.4 `incident_mappings`

| 字段 | 类型/枚举 | 语义 |
| --- | --- | --- |
| `incident_ref` | 非空 string | 旧 Incident 引用 |
| `relation` | `temporal_overlap` / `legacy_reconciliation` / `possible_correspondence` / `no_correspondence` | 对账关系 |
| `causal` | const `false` | 映射永不表示因果 |
| `evidence_sample_ids` | 去重 sample ID 数组 | 支撑关系的样本，可为空 |

一个 Incident 可对应零个、一个或多个 episode；一个 episode 也可保存多个 Incident mapping。

## 6. `country-outage-wave`：Episode 内波次

`schema_version` 固定为 `country-outage-wave/v1`。Wave 表达同一 episode 内经过显著回升后再次显著下降，不等于“前兆”，也不表达因果关系。

### 6.1 顶层字段

| 字段 | 类型/枚举 | 语义 |
| --- | --- | --- |
| `schema_version` | const | `country-outage-wave/v1` |
| `wave_id` | 稳定 ID | `wave_v1_` + 24 hex |
| `episode_id` | 稳定 ID | 所属 episode |
| `run_id` | 稳定 ID | 所属运行 |
| `ordinal` | 正整数 | episode 内波次序号，从 1 开始 |
| `onset_at` | UTC date-time | 本波次下降起点 |
| `detected_at` | UTC date-time | 本波次确认时点，不得早于 onset |
| `trough_at` | UTC date-time | 本波次低谷，不得早于 onset |
| `rebound_at` | date-time / `null` | 本波次后显著回升时点；未观察到则为 `null` |
| `relation_to_previous_wave` | `first_wave` / `same_episode_after_partial_rebound` | 与前一波关系 |
| `causal_relation` | `not_assessed` / `unknown` | 只允许未评估或未知 |
| `split_evidence` | object / `null` | 非首波的显著回升与再次下降证据 |
| `supporting_sample_ids` | 去重数组，至少 2 项 | 支撑本波次的 sample |

### 6.2 Wave `split_evidence`

| 字段 | 类型/约束 | 语义 |
| --- | --- | --- |
| `previous_trough_sample_id` | sample ID | 前一低谷样本 |
| `rebound_sample_id` | sample ID | 回升样本 |
| `new_decline_sample_id` | sample ID | 再次下降样本 |
| `rebound_amplitude` | amplitude | 回升幅度 |
| `new_decline_amplitude` | amplitude | 再次下降幅度 |
| `significance_threshold` | amplitude | 本次分割使用的显著阈值 |
| `full_recovery_between_waves` | const `false` | 两波之间没有确认完全恢复 |

每个 amplitude 都包含非负 `value`、固定单位 `ipv4_equivalent_address` 和对应 `sample_id`。语义校验要求 `rebound_amplitude.sample_id = rebound_sample_id`，`new_decline_amplitude.sample_id = new_decline_sample_id`。两波之间若已经确认完全恢复，应新建 episode，而不是继续写 wave。

## 7. `country-outage-episode-as`：逐 ASN 影响

`schema_version` 固定为 `country-outage-episode-as/v1`。该合同当前专用于伊朗验收，`country_code` 固定为 `IR`。

### 7.1 顶层字段

| 字段 | 类型/枚举 | 语义 |
| --- | --- | --- |
| `schema_version` | const | `country-outage-episode-as/v1` |
| `episode_as_id` | 稳定 ID | `episode_as_v1_` + 24 hex |
| `episode_id` | 稳定 ID | 所属 episode |
| `run_id` | 稳定 ID | 所属运行 |
| `asn` | 1–4,294,967,295 整数 | ASN |
| `country_code` | const `IR` | 本轮验收国家 |
| `cohort_view` | `compatible` / `revised` | 归属口径 |
| `mapping_evidence` | object | 映射状态、视图、哈希与来源 |
| `first_damaged_at` | date-time / `null` | 首次确认受损 |
| `last_damaged_at` | date-time / `null` | 最后一次确认受损 |
| `recovered_at` | date-time / `null` | 确认恢复；不得早于最后受损 |
| `trigger_member` | boolean | 是否属于 episode 触发快照受损集合 |
| `peak_member` | boolean | 是否属于峰值快照受损集合 |
| `cumulative_member` | boolean | 是否在 episode 任一时点受损 |
| `observation_end_member` | boolean | 观察终点是否仍受损 |
| `address_families` | object | `ipv4`、`ipv6` 两份影响事实 |
| `overall_classification` | enum | 双栈综合分类 |
| `evidence_links` | 去重数组 | RouteEvent 到原始制品的闭合引用 |

### 7.2 `mapping_evidence`

| 字段 | 类型/枚举 | 语义 |
| --- | --- | --- |
| `mapping_state` | `mapped` / `unknown` / `conflict` | 映射是否确定 |
| `mapping_view` | `compatible` / `revised` | 映射口径 |
| `mapping_sha256` | 64 hex | 冻结映射文件哈希 |
| `source_ref` | 非空 string | 映射来源引用 |

`mapping_state=unknown` 或 `conflict` 时，依赖确定国家归属的派生值应使用 `unknown_mapping`，不能把未知行丢弃后把剩余样本当作完整总体。

### 7.3 `address_families.*`

IPv4 与 IPv6 都使用 `familyImpact`，字段如下：

| 字段 | 类型/固定值 | 语义 |
| --- | --- | --- |
| `afi` | IPv4 固定 `ipv4`；IPv6 固定 `ipv6` | 地址族 |
| `baseline_prefix_count` | measure | 该 ASN 在基线中的前缀数 |
| `lost_prefix_count_at_peak` | measure | 影响峰值丢失前缀数 |
| `lost_equivalent_at_peak` | measure | 影响峰值丢失地址等价值 |
| `equivalent_unit` | IPv4 `ipv4_equivalent_address`；IPv6 `ipv6_48_equivalent` | 地址量单位 |
| `visibility` | object | 是否完全不可见及其观测状态 |
| `trigger_prefixes` | prefixSet | 触发快照受损前缀 |
| `peak_prefixes` | prefixSet | 峰值快照受损前缀 |
| `cumulative_prefixes` | prefixSet | episode 内累计受损前缀并集 |
| `observation_end_prefixes` | prefixSet | 观察终点仍受损前缀 |
| `moas_semantics` | const `origin_relationship_retained_not_additive` | origin 关系保留、不可相加 |

这里的 measure 与第 4 节相似，但不携带 sample/snapshot ID：正数为 `observed`，真实零为 `observed_zero`，未知为 `null` 加四类 unknown 状态。`prefixSet` 使用 `observed` 非空数组、`observed_empty` 空数组或 unknown `null`；前缀必须为 CIDR 字符串且去重。

`visibility` 的已观测分支为 `fully_invisible=true/false`、`visibility_state=observed`；未知分支为 `fully_invisible=null`、四类 unknown `visibility_state` 和非空原因。

### 7.4 `overall_classification`

允许值：

- `dual_stack_fully_invisible`：IPv4 与 IPv6 均已观测为完全不可见；
- `ipv4_only_fully_invisible`：IPv4 完全不可见、IPv6 已观测为非完全不可见；
- `ipv6_only_fully_invisible`：IPv6 完全不可见、IPv4 已观测为非完全不可见；
- `partially_visible`：仍存在可见性但受到部分影响；
- `not_affected`：双栈均未观察到受损；
- `unknown`：缺少足够状态、来源或映射，无法分类。

语义校验至少强制前三种布尔组合与分类一致；未知栈不能被当作“可见”来拼出确定的双栈分类。

### 7.5 `evidence_links`

| 字段 | 约束 | 语义 |
| --- | --- | --- |
| `route_event_id` | `rte_v1_` + **32 hex** | 规范 RouteEvent |
| `raw_record_ref_id` | `raw_v1_` + **32 hex** | 原始 record 引用 |
| `artifact_id` | `art_v1_` + **32 hex** | 原始 MRT 制品 |
| `artifact_sha256` | 64 hex | 原始制品完整哈希 |
| `record_ordinal` | 非负整数 | MRT 逻辑 record 序号 |
| `element_ordinal` | 非负整数 | record 内 route element 序号 |

证据链接可以为空数组，表示当前记录尚无可发布的 RouteEvent 引用；这时质量门和对账必须显式暴露缺口。不得生成假的 ID 来让数组非空。

## 8. `research-run`：运行与准入

`schema_version` 固定为 `research-run/v1`。该记录绑定配置、输入、映射、代码、资源使用、质量门和输出制品。

### 8.1 顶层字段

| 字段 | 类型/枚举 | 语义 |
| --- | --- | --- |
| `schema_version` | const | `research-run/v1` |
| `run_id` | 稳定 ID | `research_run_v1_` + 24 hex |
| `incident_ref` | 非空 string | 本轮目标 Incident |
| `profile_ref` | contentRef | Profile 路径与哈希 |
| `input_manifest_ref` | contentRef | 解析输入清单路径与哈希 |
| `mapping_refs` | 去重数组，至少 1 项 | 兼容/修订映射来源 |
| `code_identity` | object | Git 与源代码身份 |
| `run_state` | `planned` / `running` / `incomplete` / `completed` | 执行状态 |
| `acceptance_state` | `pending` / `accepted` / `not_accepted` | 验收状态 |
| `execution` | object | 实际资源与起止时间 |
| `quality_gates` | 去重数组，至少 1 项 | 质量门结果 |
| `outputs` | 去重数组 | 发布输出引用 |
| `semantic_fingerprint_sha256` | 64 hex / `null` | 语义内容指纹；未组装完成可为 `null` |
| `runtime_metadata_excluded_from_semantic_fingerprint` | const `true` | 非确定运行元数据不进入语义指纹 |

### 8.2 内容、映射和代码引用

- `contentRef`：`path` 非空、`sha256` 为 64 hex。
- `mappingRef`：`view` 为 `compatible` 或 `revised`，并带 `path` 与 `sha256`。
- `code_identity.git_sha`：40 hex；`dirty`：boolean；`source_sha256`：64 hex。

`dirty=true` 不是自动 accepted；是否允许仅由质量门与本轮验收政策决定，并必须让代码身份可复核。

### 8.3 `execution`

| 字段 | Schema 约束 | 语义 |
| --- | --- | --- |
| `database_write_operations` | const `0` | 本轮不得有数据库写入 |
| `new_raw_bytes_read` | 大于等于 0 且小于 50,000,000,000 | 实际新增原始读取字节；十进制 50 GB 为排他硬边界 |
| `peak_temporary_bytes` | 大于等于 0 且小于 5,000,000,000 | 峰值临时空间；十进制 5 GB 为排他硬边界 |
| `max_worker_seconds` | 大于等于 0 且小于 600 | 最长 worker 用时；600 秒为排他硬边界 |
| `started_at` | UTC date-time | 运行开始 |
| `finished_at` | date-time / `null` | 运行结束；存在时不得早于开始 |

Schema、Profile 和资源门禁统一使用十进制 50,000,000,000/5,000,000,000 字节；达到边界即停止，不能按 50 GiB/5 GiB 放宽。

### 8.4 `quality_gates`

每项含 `gate_id`、`blocking`、`status`、`evidence_ref`。`status` 只允许 `pass`、`fail`、`pending`；`gate_id` 必须唯一。允许的十个门为：

1. `input_integrity`；
2. `parse_integrity`；
3. `state_continuity`；
4. `vp_coverage`；
5. `mapping_coverage`；
6. `stable_identity`；
7. `reference_closure`；
8. `unknown_missingness`；
9. `resource_usage`；
10. `reproducibility`。

`acceptance_state=accepted` 时，十个门必须全部存在，且所有 `blocking=true` 的门都必须为 `pass`。失败运行可以保留质量报告和失败证据，但不能发布为 accepted。

### 8.5 `outputs`

每项含 `kind`、非空 `path`、64 hex `sha256` 和非负整数 `record_count`。`kind` 允许：

`samples`、`episodes`、`waves`、`episode_as`、`route_events`、`raw_refs`、`reconciliation`、`quality_report`、`report_zh`、`manifest`。

`record_count=0` 表示一个已成功产出但内容确实为空的制品；输出缺失不能用一条伪造的零记录引用替代，应由运行状态和质量门表达。

## 9. `reconciliation-result`：报告主张级对账

`schema_version` 固定为 `reconciliation-result/v1`。对账不改写原报告；它把报告主张、重新计算结果、证据、反证和限制并列保存。

### 9.1 顶层字段

| 字段 | 类型/约束 | 语义 |
| --- | --- | --- |
| `schema_version` | const | `reconciliation-result/v1` |
| `reconciliation_id` | 稳定 ID | `reconciliation_v1_` + 24 hex |
| `run_id` | 稳定 ID | 所属研究运行 |
| `incident_ref` | 非空 string | 被对账的 Incident |
| `report_source` | object | 原报告身份 |
| `evidence_registry` | 去重数组，至少 1 项 | 本文件可引用证据注册表 |
| `claims` | 去重数组，至少 11 项 | 十一类主张逐条评级 |
| `summary` | object | 四类评级的精确计数 |

`report_source` 必须包含非空 `title`、64 hex `sha256` 和固定为 `true` 的 `preserved_unmodified`。报告原文只能引用和对账，不得就地编辑后继续沿用原哈希。

### 9.2 `evidence_registry`

每项包含：

| 字段 | 类型/枚举 |
| --- | --- |
| `evidence_id` | `evidence_v1_` + 24 hex |
| `kind` | `sample` / `episode` / `wave` / `episode_as` / `route_event` / `raw_record` / `source_fact` / `report_page` / `limitation` |
| `ref` | 非空内容引用 |
| `sha256` | 64 hex |

所有 `evidence_id` 必须唯一。主张的 `evidence_refs` 与 `counterevidence_refs` 必须全部能在本注册表闭合。RouteEvent 和 raw record 可作为注册表证据，逐 ASN 的原始坐标则使用第 7.5 节的 `evidence_links`。

### 9.3 `claimValue`

| `value_state` | `value` | `unit` / `snapshot_id` | `missing_reason` | 语义 |
| --- | --- | --- | --- | --- |
| `reported` | number/string/boolean | string 或 `null` | `null` | 报告原文声称的值 |
| `recomputed` | number/string/boolean | string 或 `null` | `null` | 本研究重新计算的值 |
| `unknown` | `null` | 均为 `null` | 非空说明 | 必要数据不足 |
| `not_applicable` | `null` | 均为 `null` | 非空说明 | 该比较不适用 |

`snapshot_id` 在“报告时间点”或外部定性主张中可以为 `null`；可绑定研究快照的重新计算值应保留 snapshot ID。未知值不得填 0、空字符串或 `false`。

### 9.4 `claims`

每条主张字段如下：

| 字段 | 类型/枚举 | 语义 |
| --- | --- | --- |
| `claim_id` | `claim_v1_` + 24 hex | 稳定主张 ID，文件内必须唯一 |
| `claim_type` | 十一类枚举 | 主张类型 |
| `source_claim_zh` | 非空中文 string | 报告原主张的中文摘要 |
| `original_value` | claimValue | 报告值 |
| `recomputed_value` | claimValue | 研究重算值或未知 |
| `rating` | `confirmed` / `revised` / `unverifiable` / `hypothesis_only` | 评级 |
| `evidence_scope` | `rrc25_only` / `rrc25_with_external_corroboration` | 证据范围 |
| `causal_level` | `observation` / `association` / `mechanism_hypothesis` / `causal_hypothesis` / `intent_hypothesis` | 推断层级 |
| `evidence_refs` | 去重 evidence ID 数组 | 支持证据 |
| `counterevidence_refs` | 去重 evidence ID 数组 | 反向证据 |
| `limitations_zh` | 至少 1 条非空中文说明 | 数据与推断限制 |
| `rationale_zh` | 非空中文 string | 评级理由 |

十一类 `claim_type` 必须各至少出现一次：

1. `report_event_time`；
2. `ipv4_decline`；
3. `recovery_state`；
4. `report_affected_asn_ratio`；
5. `report_visibility_class_counts`；
6. `database_affected_asn_ratio`；
7. `active_withdrawal_intent`；
8. `physical_cut`；
9. `bgp_session_closed`；
10. `traffic_impact`；
11. `government_intent`。

`rating=confirmed` 或 `revised` 时，`recomputed_value.value_state` 必须为 `recomputed`，且至少有一条支持证据。评级语义为：

- `confirmed`：相同口径下可复算，并与报告主张一致；
- `revised`：可以复算，但数值、边界或口径需要修订；
- `unverifiable`：当前证据源无法验证或反驳；
- `hypothesis_only`：仅能保留为机制、因果或意图假设。

当 `evidence_scope=rrc25_only` 时，`active_withdrawal_intent`、`physical_cut`、`bgp_session_closed`、`traffic_impact`、`government_intent` 不得评为 `confirmed` 或 `revised`，且 `causal_level` 只能是 `mechanism_hypothesis`、`causal_hypothesis` 或 `intent_hypothesis`。RRC25 路由观测可以证明可见路由状态变化，不能单独证明物理链路、会话机制、实际流量或政府意图。

### 9.5 `summary`

`summary` 必须恰好包含非负整数 `confirmed`、`revised`、`unverifiable`、`hypothesis_only`。语义校验会逐条统计 `claims[].rating`，四个数字必须与实际计数完全一致。

## 10. Episode、Wave、恢复与旧事实对账口径

### 10.1 Episode 与 Wave

- 异常槽：本次 Profile 规定 IPv4 可见地址并集低于基线 99%，或同快照受损 ASN 比例高于 3%；两者任一满足即可。
- Episode 确认：连续 2 个异常槽；`onset_at` 为第一个异常槽，`detected_at` 为确认槽。
- 同一 episode 内出现显著回升后再次显著下降，且期间没有确认完全恢复，记录为新 wave。
- wave 显著阈值为 `max(基线的 0.5%, 3 × 基线 MAD)`。
- 只有连续 6 槽回到版本化基线正常带并确认完全恢复，后续异常才允许拆成新 episode。
- “前兆”不是合同枚举。现阶段只能用 wave、时间重叠或 `possible_correspondence` 表达，不得把顺序关系升级为因果关系。

### 10.2 恢复

- 部分恢复：连续 6 槽达到基线可见量的 99%。
- 完全恢复：连续 6 槽处于版本化基线正常带。
- 未达到确认槽数时不能填写恢复时间。
- 观察窗口结束仍未恢复，使用 `ongoing`/`recovering`/`partially_recovered` 与 `duration_state=lower_bound`；连续性不足则使用 `unknown` 或 `interval`，不合成精确结束时间。

### 10.3 兼容、修订和旧数据库

- 兼容结果用于说明旧映射/旧口径下“若按原规则会得到什么”；修订结果用于去重地址并集、双栈和明确 MOAS 语义。
- 两套结果允许不同，不能要求修订结果强行复现旧事件表的分子、分母或峰值人数。
- 旧 Incident 是 `source_fact`，研究 episode 是重建结果。`legacy_reconciliation` 表示做过旧事实对账，不表示旧事实已经被研究结果证实。
- 对旧报告中的事件时间、IPv4 下降、恢复、受影响 ASN 比例和可见性分类，应提供 `reported` 与 `recomputed` 并列值；无法由 RRC25 证明的机制与意图必须标记证据边界。

## 11. 研究对象与原始引用闭环

完整引用链至少包含：

```text
Incident
  -> reconciliation_result / episode.incident_mappings
  -> sample / episode / wave / episode_as
  -> evidence_registry 或 episode_as.evidence_links
  -> route_event_id
  -> raw_record_ref_id
  -> artifact_id + artifact_sha256 + record_ordinal + element_ordinal
```

各层职责：

- `sample.source_refs` 证明五分钟快照由哪些状态分片、RouteEvent 分片、输入制品和映射快照产生；
- `episode.supporting_sample_ids`、`wave.supporting_sample_ids` 和 split evidence 证明边界判定；
- `episode_as.evidence_links` 提供逐 ASN 路由事实的原始坐标；
- `reconciliation_result.evidence_registry` 将研究记录、旧事实、报告页、限制、RouteEvent 和 raw record 统一为主张可引用的证据；
- `research_run.outputs` 绑定研究对象、对账、质量报告和中文报告的路径、哈希与记录数；
- `reference_closure` 质量门验证引用存在，`stable_identity` 验证 ID 稳定，`reproducibility` 验证同输入与同版本可重建相同语义指纹。

AS_PATH、RouteEvent 和 peer session 状态都是观测证据，不是数据包传播路径或因果证明。BGP4MP `STATE_CHANGE` 应作为会话观测单独保存；它不能冒充该 VP 对全部前缀发出了 withdrawal。

## 12. 发布与验收不变量

只有同时满足以下条件，研究运行才能标记 `accepted`：

1. 输入清单、Profile、映射和代码身份都有路径与 SHA-256；
2. 半开窗口槽位和输入角色完整对账；
3. RIB、UPDATE、STATE_CHANGE 的解析身份和 record/element ordinal 可复核；
4. 状态连续性、VP 覆盖和映射覆盖达到本轮门槛，所有 unknown 如实保留；
5. sample 的 measure、集合、比例全部来自父 sample 的同一 snapshot；
6. episode/wave/恢复边界均有 sample 证据，未恢复事件使用下界或未知；
7. RouteEvent/raw/artifact 引用闭合，三个原始身份均使用 32 hex 后缀；
8. 十个质量门齐全，所有阻断门均为 `pass`；
9. 数据库写操作为 0，实际资源使用严格低于 Profile 与 Schema 中更严格的排他上限；
10. 输出不可变、原子发布、不覆盖已有制品，不修改前端、不部署生产。

任何条件失败都应产出 `incomplete` 或 `not_accepted` 的运行记录和失败证据，不得把未知填为 0、把旧峰值摘要包装成同快照事实，或为通过合同伪造 Evidence 引用。

## 13. 完整窗口 UPDATE 计数与 partial VP 投影

- `country-outage-sample/v1.metrics.announce_count` 与 `withdraw_count` 只表示当前 carried-state 工作集实际保留的 tracked-prefix UPDATE 数，即 `retained_announce` / `retained_withdraw`；它们不是采集器全量报文数，也不表示 IR 或任何主体的主动宣告、主动撤回或意图。
- 采集器全量槽级计数使用 `collector_total_announce_count` / `collector_total_withdraw_count`，只发布在 `rrc25-full-window-sample-measurement-semantics/v1` sidecar，不能与 retained 计数混用。
- 当 VP 覆盖不完整时，v1 样本中的受影响数值与 ASN 集合必须投影为 `unknown_state_gap + null`；实际 carried-state 部分观测值、原始 partial value state、down VP 和计数 scope 仅保存在由相同 `sample_id`、`snapshot_id` 与不可变 shard 引用绑定的状态数据中。
- peer session down 不是隐式 WITHDRAW。partial carried-state 可供显式披露的研究算法使用，但不得在对外样本中伪装为完整 observed 人口。
- 双目录复现仅证明同一冻结 journal 的纯派生业务语义一致；若没有重放真实 MRT，必须标记 `raw_replay_reproduction=not_performed_by_user_choice`，不得称为原始全链 A/B。

## 14. 完整窗口原始读取账本与 seed 离线闭包

- 原始读取硬门按 `genesis + 全部 attempt reservation` 的不退款累计上界核验；失败、重试和发布后失败均不得从 50 GB 累计中扣回。
- 正常完成或可在同进程精确观测的失败使用 `observed_compressed_bytes_state=exact`，并要求实测值与上下界相等。进程被强制终止且无法可信取得 gzip 已读量时，必须使用 `unknown_after_process_termination`，精确值为 `null`，区间保守记为 `[0, artifact.size_bytes]`。
- 只要存在上述未知终止，汇总 `observed_compressed_bytes_sum` 与 `new_raw_bytes_read` 必须为 `null`；同时发布 `observed_compressed_bytes_lower_bound_sum` / `observed_compressed_bytes_upper_bound_sum` 与对应读取上下界，不能把 reservation 或上界伪装成实测值。资源准入仍以不退款 reservation 上界为准。
- `ACCUMULATOR` 只用于 attempt 热路径的滚动累计；最终化与离线验包必须扫描完整 create-only attempt ledger 重算，并与其指纹、attempt 数、genesis 引用和累计 reservation 对账。`ACTIVE` 是可变执行租约状态，不是证据；存在时必须先在执行锁内完成 reconcile，最终化不得收录或绕过。
- seed bootstrap 包含 full-seed v2 checkpoint 的身份哈希、完整 seed RouteEvent/raw 引用投影、可离线重放的 route state、初始 compact state、spool attestation、parser 身份和退役收据。checkpoint 原字节未封包，因此离线范围固定为 `checkpoint_identity_and_seed_evidence_projection_without_checkpoint_bytes`，不得描述为 checkpoint 字节级复现。
- seed 退役成功收据与 raw verification attempt 必须分别验证自身 schema 和 fingerprint，并闭合到同一 selection、checkpoint、spool、压缩 seed 原件及 genesis raw accounting；只重算外层 attestation fingerprint 不能替代这些内层证明。

## 15. 来源时间边界、物理记录流与最终化软停止门

- 旧 Incident 的 locator `2026-02-27T01:12:32Z` 只用于稳定源记录身份，不能冒充研究事件起点。
- 来源身份时间不改变研究窗口。`MetricWindow`、五分钟样本、Episode、Wave 和恢复判断仍严格限定在 Profile 半开窗口 `[2026-02-27T16:00:00Z, Profile.end_exclusive)`；`[2026-02-27T01:12:32Z,2026-02-27T16:00:00Z)` 明确标记为不属于本次 RRC25 MRT 研究覆盖。原始来源状态必须保持 `partial`，不能因 Profile 窗口内 RouteEvent 已闭合就改写为 `full`。
- `record_observations` 是逐物理 MRT record 的完整观测流，真实全窗可能达到百万或千万行。最终化只能逐 receipt、逐 shard 流式校验，并保存总记录数和有域分隔的有序 shard 语义哈希链；`_JournalData` 不得长期持有全窗 observation tuple。原始不可变 shard 仍按原字节复制进最终包，离线验包使用同一流式算法重算 count 与语义链。
- 每个 retained `raw_record_ref` 必须按 `(artifact_id, record_ordinal)` 与对应 `record_observation` 的 `raw_record_sha256`、`record_offset`、`record_length` 精确一致，且 `record_hash` 必须等于该物理 record SHA-256。每个 retained RouteEvent 只能指向 `record_kind=update` 的 observation；合法格式但内容伪造的 64 位哈希同样失败关闭。
- BGP4MP_ET 的事件时间可带规范小数秒，例如 `2026-02-27T16:00:01.123456Z`。RouteEvent、control record 与 record observation 使用事件时间规范器保留该精度；Profile、槽起止和输入制品时间仍必须是秒级 UTC。
- 最终化在 journal load、独立逐槽复算、ancestry inventory、复制、fsync 和发布前核验的长循环中执行 540 秒合作式软停止检查。达到软门即正常失败且不得进入原子发布；600 秒仍是不可突破的资源硬边界。本阶段不实现最终化断点续跑。
