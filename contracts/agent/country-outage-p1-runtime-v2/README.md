# P1 Runtime v2 S0 运行合同

本目录把 P0 v1.3 已摸索出的业务能力转成 P1 可执行边界，但不会把“能力”与“Tool”混为
一谈。

- `capability-catalog.json`：冻结 17 个首批运行能力，保留 5 个 defer、4 个 reject 和
  8 个 unknown 的处置；每次事件绑定仍须重新协商实际能力。
- `tool-contracts.json`：把 17 个能力收敛为 6 个只读 Tool 和 3 个确定性算子，完整定义
  输入输出、身份、单位、时间、分页、null、错误、权限、超时、证据和禁止用途。
- `oracle.json`：按 `17 个 selected Capability × 6 类情况` 逐能力绑定正常、缺失、null、
  错误身份、不可用和边界样例，展开后共 102 案；多个 Capability 共用 Tool 时也不能
  共用“挂名覆盖”。每案引用 `oracle-fixtures.json` 与可执行 adapter。
- `semantic-plan.schema.json`：开放 `UserGoalPlan` 与封闭 `GroundingPlan` 的机器合同。
- `policy.json`：宿主拥有的能力、权限、事实发布、缺失语义和状态事务规则。

这里的“冻结”表示 S0 设计与机器合同完成，不表示 Tool、聊天页面或最终 P1 已验收。
运行实现必须逐项通过 Oracle，且只能在当前绑定事件协商出的能力范围内执行。

同一 publication、同一 series 响应摘要曾暴露一项 P0 真值错误。经用户明确授权，P0
v1.3 已原位覆盖：`route_interrupted_asn_count=94` 的首个峰值时点按
`first_observed_occurrence` 修正为 `2026-02-28T13:50:00Z`，并同步 ORC-10、P013-D-08、
Validator 与 manifest。P1 的 CAP-005、CAP-016 继续绑定该已校正真值；修正回执保留在
`evaluation/country-outage/p1-runtime-v2/upstream-p0-truth-conflict.json`。
