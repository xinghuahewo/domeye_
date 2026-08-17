# Domeye 进度可视化规则

进度首先回答“哪条用户旅程已经被同一 Candidate 的证据证明”，而不是“完成了多少层、文件或 Issue”。
固定旅程和阈值以[首个纵向切片锚点合同](../architecture/Domeye_First_Vertical_Slice_Anchor_v1.0.md)为准。

## 1. 五个状态轴与 Gate 必须分开

| 状态轴 | 允许值 | 回答的问题 |
|---|---|---|
| Work Status | Todo / In Progress / Blocked / In Review / Done / Cancelled | 这项工作的执行进度是什么？ |
| Governance State | Untracked / Governed / Reference / Excluded | 它是否属于当前受管范围？ |
| Plan State | Unplanned / Planned / Committed / Deferred / Cancelled | 它是否进入正式阶段承诺？ |
| Delivery Maturity | Not Assessed / Designed / Implemented / Verified / Released | 它对交付成熟度证明到哪一级？ |
| Evidence State | Missing / Present / Accepted / Rejected / Superseded | 与当前 Candidate 绑定的证据处于什么状态？ |

Gate Decision 另设为 `Not Applicable / Pending / GO / REPAIR / STOP`，不能由其他轴自动
计算。`Not Synced / Pending / Partial / Synced / Blocked` 是 Issue 幂等收尾回执状态，
不是 Work Status，也不能推进能力成熟度或替代 DG1。

## 2. 首个切片旅程看板

| 旅程 | 需要证明的用户效果 | 当前合同基线 | 推进所需证据 |
|---|---|---|---|
| J1 | Pi 滚动提出 Action，Host 逐次准入，Tool/Operator 结果形成安全 Answer | Designed | 同一 Candidate 的真实轨迹、Typed Finding、Answer Context、Guard、Pass@1 与 Pass³ |
| J2 | 未获准的后续 Action 被拒绝且未执行 | Designed | 拒绝回执和零执行证据 |
| J3 | Tool 失败、不完整或错身份时不生成成功 Finding | Designed | 预注册失败轨迹与失败分类 |
| J4 | Renderer 改值、漏限制或扩大范围时被 Guard 阻断 | Designed | 敌对草稿、`block` 结果和同 Context 确定性回退 |
| J5 | 极值、null、全空、缺槽、错单位和错身份确定性处理 | Designed | Operator、身份检查与边界回归证据 |

这里的 `Designed` 只说明合同已经规定目标，不说明实现、验证或发布已经完成。

## 3. 最小 Project 视图

只保留三个主要视图：

- **Current Slice**：M0 / M1、J1–J5、当前阻塞和 Candidate。
- **Roadmap**：Feature、Milestone 与 Delivery Maturity。
- **Evidence & Gates**：Evidence State、Gate Decision、Evidence Index 和被拒绝或已过期证据。

不需要再为风险、成熟度或架构层分别创建额外视图，也不按通用 Plan、DAG、Claim
Validator/Publisher 或架构层完成百分比建立看板。

## 4. 每次进度评审只问五件事

1. 本次推进了 J1–J5 中哪条旅程或关闭了哪个已证实风险？
2. Pi 是否观察真实结果后才提出下一 Action，且每个 Action 都重新经过 Host 准入？
3. Tool/Operator 结果是否通过 Typed Finding、Answer Context、Renderer 和 Guard 到达用户？
4. Evidence 是否绑定同一 Candidate，并经过独立验收？
5. 哪些内容仍是 Designed、Blocked 或明确延后？
