## ADDED Requirements

### Requirement: 冻结基线 cohort 与数值基线
系统 MUST 使用窗口起点状态中由兼容映射标记为 IR 的实际可见 origin ASN 集合作为基线 cohort。成员基线来自 seed RIB 回放后的起点状态；数值基线来自异常前稳定六小时五分钟序列的中位数，若不稳定则按冻结规则向前扩展并记录实际窗口。扩展不得跨过用户提供的最早可能前兆排除边界；该边界 MUST 标记为 `candidate_not_confirmed`，MUST NOT 被表达为已确认 onset 或因果/前兆结论。

#### Scenario: 生成兼容与修订 cohort
- **WHEN** 兼容映射和修订映射对 ASN 国家归属产生不同结果
- **THEN** 系统分别输出两套 cohort 和指标，主结果明确声明采用哪套口径，MUST NOT 混合分子与分母

#### Scenario: 基线区间不稳定
- **WHEN** 六小时区间不满足冻结的稳定性判据
- **THEN** 系统向前扩展到允许上限并记录扩展原因；若仍不稳定则标记 `baseline_unresolved` 并停止 episode 定论

### Requirement: 生成五分钟不可变状态样本
系统 MUST 在每个五分钟槽边界生成不可变 `country_outage_sample`，同一样本同时保存可见/受损 ASN、IPv4/IPv6 前缀、IPv4 地址并集、IPv4 `/24` 等价值、IPv6 `/48` 等价值、ANNOUNCE/WITHDRAW、VP 覆盖、分母、比率、数据状态和来源引用。

#### Scenario: 正常生成同一时点快照
- **WHEN** 一个五分钟槽的输入连续且解析完成
- **THEN** 所有分子、分母、成员集合和比率都从同一状态快照派生，并由稳定 sample ID 绑定

#### Scenario: 真实零值与缺失值
- **WHEN** 某指标已完整观测且值为 0
- **THEN** 系统输出 `value=0,value_state=observed_zero`

#### Scenario: 指标无法观测
- **WHEN** 指标因源缺失、解析失败、映射未知或状态断裂不可得
- **THEN** 系统输出 `value=null` 及具体 `value_state/missing_reason`，MUST NOT 补 0

### Requirement: 分离地址族和计量口径
系统 MUST 分别输出 IPv4 与 IPv6 的前缀、等价值和可见性分类。国家级地址量 MUST 对前缀覆盖做集合去重；逐 ASN 结果 MUST 明确 MOAS 的归属语义。旧系统 `v4ip_num` 兼容算法只能作为单独命名的兼容指标，MUST NOT 冒充去重后的地址并集。

#### Scenario: IPv4 全不可见但 IPv6 仍可见
- **WHEN** 某 ASN 的基线 IPv4 前缀全部消失而 IPv6 仍有可见前缀
- **THEN** 系统将其标为 `ipv4_fully_invisible=true`、`ipv6_fully_invisible=false`，双栈综合分类 MUST NOT 写成无条件完全不可见

#### Scenario: 存在重叠前缀或 MOAS
- **WHEN** 多条前缀覆盖同一地址空间或同一前缀有多个 origin
- **THEN** 国家地址并集只计一次，并在逐 ASN 结果中保留 origin 关系和去重说明

### Requirement: 数据驱动的 episode 与 wave
系统 MUST 从连续状态样本识别异常状态、episode 和 wave，而不是从旧 `s_time` 向前后截取固定窗口。默认异常开始规则为 IPv4 可见地址并集低于数值基线 99% 或同快照受损 ASN 比例高于 3%，并连续至少两个五分钟槽；系统将第一异常槽记录为 `onset_at`，将确认槽记录为 `detected_at`。episode 只有在满足冻结的持续完全恢复规则后才能关闭；未达到关闭条件的多次下降 MUST 表达为同一 episode 的多个 wave。

#### Scenario: 两次下降之间完全恢复
- **WHEN** 第一次下降后连续 30 分钟回到异常前正常波动范围，之后再次满足异常开始规则
- **THEN** 系统生成两个 episode，并保存分割证据

#### Scenario: 两次下降之间未完全恢复
- **WHEN** 可见性仅部分回升后再次下降
- **THEN** 系统生成一个 episode 和至少两个 wave，并保存波次边界和支撑样本

#### Scenario: 旧系统事件划分与新结果不同
- **WHEN** 数据驱动结果与旧数据库单条事件划分不一致
- **THEN** 系统保留旧 Incident 映射作为对账对象，但 MUST NOT 强制新 episode 服从旧划分

#### Scenario: 同一 episode 内识别新 wave
- **WHEN** 前一低谷后指标回升达到 `max(基线的0.5%, 3×基线MAD)`，随后再次连续两个槽下降至少同等幅度，但中间未满足完全恢复
- **THEN** 系统在同一 episode 下生成新 wave，并将回升幅度、再次下降幅度和支撑样本写入 `split_evidence`

### Requirement: 恢复与持续时间语义
系统 MUST 将连续 30 分钟达到基线 99% 以上标为部分恢复，将连续 30 分钟回到基线正常波动范围标为完全恢复。到观察截止仍未完全恢复时，系统 MUST 使用 `duration_state=lower_bound` 和相应 `recovery_state`，MUST NOT 伪造结束时间。

#### Scenario: 短暂跨过 99%
- **WHEN** 指标单点或不足六个连续槽达到基线 99%
- **THEN** 系统不标记部分恢复，并记录该候选跨线未通过确认窗

#### Scenario: 截止时仅部分恢复
- **WHEN** 已通过部分恢复确认但截至观察终点未通过完全恢复确认
- **THEN** 系统输出 `recovery_state=partially_recovered`、`full_recovery_at=null` 和持续时间下界

### Requirement: 逐 ASN 与前缀影响人口
系统 MUST 为每个基线或动态 IR ASN 输出首次受损、峰值成员、累计受影响、最后受损、恢复时间、IPv4/IPv6 分类、前缀与地址损失及 RouteEvent/raw refs。触发集合、峰值集合、累计集合和观察终点集合 MUST 分开表达。

#### Scenario: 同人数但成员替换
- **WHEN** 相邻样本受影响 ASN 数量相同但成员不同
- **THEN** 系统保留两个完整成员集合和每个 ASN 的演化，MUST NOT 因计数未增长而丢失变化

#### Scenario: 对账 176/556 与 199/595
- **WHEN** 伊朗研究运行完成
- **THEN** 系统从同一 cohort 和同一时点快照计算新分子分母，并逐项解释与两组旧值的差异来源，而不是把任一旧值设为强制验收答案
