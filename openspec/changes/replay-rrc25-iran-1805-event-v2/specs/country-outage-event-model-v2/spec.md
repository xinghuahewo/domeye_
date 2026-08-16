## ADDED Requirements

### Requirement: 九个结构化事件字段
Incident 和 Episode MUST 显式提供 detected_at、onset_at、peak_at、trough_at、
partial_recovery_at、full_recovery_at、observation_end_at、duration_state 和
recovery_state。每个里程碑 MUST 绑定 snapshot、指标、指标值、算法和时间精度。

#### Scenario: 窗口起点已异常
- **WHEN** 起点与下一槽连续超过 3% 且此前正常转异常边界不可见
- **THEN** onset 绑定窗口起点并标记左删失，duration_state 为 interval

### Requirement: 检测与恢复确认窗
onset MUST 由连续两槽超过 3% 确认；部分恢复 MUST 同时满足 ASN 和 Prefix×VP
达到 99% 连续六槽；完全恢复 MUST 同时回到冻结正常带连续六槽。

#### Scenario: 单槽超过恢复线后再次下降
- **WHEN** 只有一个槽达到 99% 后指标再次下降
- **THEN** Episode 保持未关闭，full_recovery_at 仍为 NULL

### Requirement: 同快照旧字段投影
旧 outage_ases、max count、max ratio 和 total MUST 来自 Incident 的同一 peak
snapshot。s_time 只投影 detected_at；e_time 只有 fully_recovered 才投影。

#### Scenario: 成员集合与 peak count 不一致
- **WHEN** Repository 收到的 outage_ases 长度不等于 peak count
- **THEN** 整个事务失败并 rollback

### Requirement: Observation 追加写与事务原子性
Observation MUST 按 snapshot ID 追加；同 ID 相同 payload 幂等，同 ID 不同
payload 拒绝。Incident、Episode、Observation 和旧投影 MUST 单事务提交。

#### Scenario: Episode 写入后 Observation 写入失败
- **WHEN** 数据库在事务中间返回错误
- **THEN** Repository rollback 并重新抛出，不留下 Incident 或 Episode 半成品

### Requirement: 旧 Core 缺失证据不伪造恢复
旧 Core 可以生成 ASN-only Observation，但 Prefix×VP 不可用时 MUST 明确记录
unavailable，recovery_state 保持 unknown，MUST NOT 用单槽恢复结束事件。

#### Scenario: ASN 连续六槽恢复但 Prefix×VP 不可用
- **WHEN** visible ASN 达到 99% 连续六槽而 Prefix×VP 为 unavailable
- **THEN** full_recovery_at 仍为 NULL，旧 e_time 不写入
