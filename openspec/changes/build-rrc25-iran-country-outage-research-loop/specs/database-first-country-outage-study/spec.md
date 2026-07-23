## ADDED Requirements

### Requirement: 独立数据库先于原始重放形成研究主体
系统 MUST 在解析原始 MRT 前，使用固定 reader 对现有独立数据库执行
`REPEATABLE READ READ ONLY` 事务，冻结旧 Incident、国家五分钟指标、
AS/前缀事实和恢复信号。事务 MUST 最终 rollback，reader MUST 没有数据库、
schema 或业务表写权限。

#### Scenario: 伊朗数据库研究窗完整
- **WHEN** 系统导出北京时间半开窗口 `[2026-02-28 00:00:00, 2026-03-06 16:40:00)`
- **THEN** `feature_country(source=r,country=伊朗)` 必须对账为 `1,928/1,928` 个五分钟槽，并分别报告缺槽、NULL 和真实零值

#### Scenario: 数据库安全门失败
- **WHEN** 当前角色不是固定 reader、事务不是只读、release/state 身份漂移或角色具有写权限
- **THEN** 系统在任何业务查询或文件发布前失败关闭

### Requirement: 分离旧起点、摘要刷新与研究波次
系统 MUST 将 `country_outage.s_time`、`event_info` 内嵌时刻和研究指标识别的
波次作为不同时间字段保存，并同时输出 `Asia/Shanghai` 与 UTC。系统 MUST NOT
用后写摘要时刻覆盖首次检测时刻，也 MUST NOT 把时间先后表述为因果。

#### Scenario: 伊朗旧记录包含两个时间锚
- **WHEN** `s_time=2026-02-27 09:12:32` 而 `event_info` 包含
  `2026-02-28 22:34:40`
- **THEN** 系统分别保存 `legacy_detected_at` 和 `legacy_summary_updated_at`，
  记录二者差值，并把 `06:35` 候选扰动及后续显著波次标为
  `observation_only`

### Requirement: 对账数据库影响集合与指标口径
系统 MUST 对账国家事实的受影响 ASN 集合、关键时刻仍活跃的 AS/前缀事实集合、
国家 ANNOUNCE/WITHDRAW 和 IPv4/IPv6 等效资源。IPv4 `/24`、IPv4 等效地址与
IPv6 `/48` MUST 使用不同单位；ASN 活动稀疏表缺行 MUST NOT 解释为不可见。

#### Scenario: 关键时刻集合闭合
- **WHEN** 系统查询 `2026-02-28 22:34:40+08:00` 的伊朗活跃事实
- **THEN** 系统输出国家事实 176 ASN 与活跃 AS 集合的双向差集、活跃前缀数，
  并将生成算法/阈值未持久化标为限制

#### Scenario: 报告数字与数据库不一致
- **WHEN** 报告使用 `199/595` 或 `73/126`，但当前数据库对账为
  `176/556` 及其他全部/部分集合
- **THEN** 系统标记为 `revised` 或 `unverifiable`，MUST NOT 为匹配报告改写
  查询窗口、分母或稀疏表语义

### Requirement: 先发布缺口矩阵再决定最小 raw 请求
系统 MUST 将问题分为 `database_direct`、`database_derived`、
`targeted_raw_required` 和 `unverifiable`。原始请求 MUST 绑定具体缺失字段、
关键槽、代表实体和最大资源边界，初始状态为 `not_executed`。

#### Scenario: 数据库不能提供稳定 VP 或 raw ref
- **WHEN** 旧事实只有时间到 AS_PATH 集合，且没有 peer IP、稳定 VP、artifact
  hash 或 record/element 坐标
- **THEN** 系统只为 `06:35`、`18:45`、`22:30` 关键槽及代表 ASN/前缀生成
  定向证据请求，MUST NOT 自动启动 1,928 UPDATE 全窗口重放

#### Scenario: 主张需要外部因果数据
- **WHEN** 主张涉及前兆因果、物理断线、流量影响或政府意图
- **THEN** 系统标记为 `unverifiable` 或 `hypothesis_only`，且 MUST NOT
  通过扩大 RRC25 重放窗口伪造可验证性

### Requirement: 隔离数据库兼容指标代理与正式状态 Episode
系统 MAY 使用完整的国家五分钟聚合曲线生成指标级 Episode/Wave 候选，但 MUST
将其标记为 `database_metric_proxy`、`candidate_only=true`，并明确
`ipv4_address_equivalent` 是旧 `/24` 等价值乘 256，而不是去重 IPv4 地址并集。
代理结果 MUST NOT 序列化为 `country-outage-sample/v1`、
`country-outage-episode/v1` 或逐 ASN 可见性分类，也 MUST NOT 将缺失的
同快照 cohort、VP 覆盖、RIB 状态或受损 ASN 比例补零。

#### Scenario: 数据库曲线满足冻结阈值
- **WHEN** 旧 IPv4 等价值连续两个五分钟槽低于六小时中位数的 99%
- **THEN** 系统输出带 metric basis、阈值、支持槽和限制的代理 onset/detected/
  trough/recovery/wave 候选，并将正式状态 Episode 保留为未知

#### Scenario: 定向 UPDATE 已提供消息证据
- **WHEN** 代表前缀在固定 13 槽内形成带 VP 与 raw 坐标的 RouteEvent
- **THEN** 系统只将其登记为 `message_observation_only` 旁证，MUST NOT
  据此升级代理 Episode 为状态闭环或生成完整传播、恢复和因果结论
