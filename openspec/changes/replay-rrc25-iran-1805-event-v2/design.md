## Context

研究窗口固定为北京时间 `[2026-02-28 18:05,23:00)`，UTC 为
`[2026-02-28T10:05:00Z,2026-02-28T15:00:00Z)`。08:00 bview 加 84 个连续
UPDATE 可在不重放 1,928 槽的情况下恢复该窗口状态。

## Decisions

### 1. seed cohort 固定，动态人口分离

基线从 08:00 bview 中 revised 映射明确属于 IR 的 origin 路由冻结。当前可见
Prefix×VP 必须仍在同一个 collector、VP、AFI/SAFI、prefix 键上保持相同 origin。
窗口中新出现的 IR origin 进入 dynamic，不改变基线分母。

### 2. 一个窗口输出 60 个不可变 Observation

25 个 catch-up 槽只用于把 bview 推进到 18:05 并冻结正常带；正式输出包含一个
18:05 零宽状态边界和 59 个槽末状态，最后时刻恰为 23:00。任何缺槽或解析失败都
失败关闭，不跨缺口输出数值。

### 3. Incident 与 Episode 分离

旧记录是 Incident 容器，状态异常阶段是 Episode。检测需连续两槽超过 3%；部分
恢复和完全恢复均需连续六槽。peak 使用最大受影响 ASN 比例，trough 使用最小
Prefix×VP 可见比例，并列均取最早。未恢复时 duration 为 lower bound；窗口起点
已异常时 onset 标记左删失 interval。

### 4. 数据库用全局 v2 表，旧月表只做投影

Observation 按 snapshot ID 追加，payload 冲突拒绝；Incident/Episode 保存当前
派生投影。三者与旧月表投影在同一事务提交。旧 `outage_ases/count/ratio/total`
必须来自同一 peak snapshot，`e_time` 只有完全恢复才写入。

### 5. 旧 Core 缯失 Prefix×VP 时保持 unknown

Core 仍可从 ASN 集合生成结构化实时 Observation，并用两槽确认 onset/peak；
但不会用单槽 ASN 恢复结束事件。未来接入 Prefix×VP 后，同一转换器规则可确认
六槽恢复。

## Failure Handling

- 输入身份、连续性、gzip、SHA 或解析失败：不发布结果目录。
- 数据库任一步失败：rollback 并重新抛出，不留下半写 Observation/Incident。
- 输出目录已存在：create-only 拒绝覆盖。
- 正常带不稳定或已异常：full recovery 保持 unknown，不临时放宽阈值。
