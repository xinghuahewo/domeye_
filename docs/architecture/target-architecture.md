# Domeye 目标架构

本文只说明当前 M0/M1 的职责边界。固定问题、数据身份、执行单元、回答合同和验收门槛以
[首个纵向切片锚点合同](Domeye_First_Vertical_Slice_Anchor_v1.0.md)为准；能力范围见
[Capability Map](capability-map.md)。

产品主名称固定为 **Domeye 国家网络中断调查 Agent**；当前能力说明是
**当前已接入证据能力：RRC25 BGP 控制面**。产品名不改变首片的单 RRC25 事实边界。

## 1. 当前执行循环

```text
用户问题
→ Pi 提出一个下一步 Action
→ Host 对该 Action 绑定身份并准入
→ Tool 或 Operator 执行并返回结构化结果
→ Pi 观察结果，再决定继续、澄清、重选或停止
```

首个纵向切片中，Pi 先提出 `TOOL-03 read_metric_series`；观察真实 Tool 结果后，才可提出
`OP-01 series_extrema`。每一个新 Action 都必须重新经过 Host 准入，不能把模型生成的整条
计划直接执行。Host 还必须执行登记的 TOOL-01 身份验证，或消费同一 Candidate 中不可变的
已验证回执；手写身份字段不能代替验证。

结果足以回答固定问题后，回答链固定为：

```text
Tool / Operator 结果
→ Host Typed Finding
→ 最小 Answer Context
→ Renderer
→ Response Guard
→ Answer 或同一 Context 的确定性回退
```

## 2. 职责边界

| 部分 | 负责 | 不负责 |
|---|---|---|
| Pi | 理解用户目标；每次提出一个 Action；观察结果后继续、澄清、重选或停止 | 授权执行、提交事实、修改 Registry、决定答案可发布性 |
| Host | 绑定 Candidate 与领域身份；逐 Action 检查权限、预算、版本、输入和超时；生成回执、Typed Finding 与 Answer Context | 把模型提议当作已授权命令 |
| Tool | 按冻结身份读取一个受约束事实人口并返回类型化结果 | 排名、解释、调用模型或其他 Tool |
| Operator | 对合格输入执行一次纯确定性业务变换 | 读数据库、联网、调用模型或写状态 |
| Registry | 描述 Capability，并把获准 Capability 绑定到版本化执行单元 | 把 `active` 自动解释为生产部署 |
| Renderer | 只依据 Answer Context 生成草稿 | 新增、修改或扩大事实 |
| Response Guard | 确定性检查身份、数值、单位、限制和禁止结论，只返回 `pass/block` | 生成事实、改写草稿、授权 Action 或决定 DG1 |

Trace 证明执行过程发生过，不等于 Domain Evidence。实现、PR 合并或单次演示也不等于能力已验证。

## 3. J1–J5 覆盖的架构结果

- **J1**：真实 Pi、逐 Action 准入、TOOL-03、OP-01 和安全回答链在同一 Candidate 内闭合。
- **J2**：未获准的后续 Action 被 Host 拒绝且没有执行。
- **J3**：Tool 失败、不完整或身份错误不会生成成功 Finding。
- **J4**：Renderer 改值、漏限制或扩大范围时，Guard 阻断并进入确定性回退。
- **J5**：并列极值、null、全空、缺槽、错单位和错身份按合同确定性处理。

## 4. 明确延后

M0/M1 不建设通用 Plan IR、预生成整条 DAG、通用 Claim 验证与发布体系、Verified Claim Set、
独立 Claim Support Graph 或新的耐久工作流引擎。只有真实用户旅程证明需要且首个切片通过
DG1 后，才讨论后续抽象。
