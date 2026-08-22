# 架构导航

本目录回答两个问题：Domeye 当前要闭合哪条用户旅程，以及各部分分别承担什么职责。
M0/M1 不以架构层、文件或 Issue 数量衡量进度。

## 阅读顺序

1. [当前代码基线](Domeye_Current_Code_Baseline_2026-08-16.md)：确认 `main@6a4bbd4…` 的真实起点，不把设计当实现。
2. [产品、数据与 Claim 边界 v1.1](Domeye_Product_Data_Claim_Boundary_v1.1.md)：确认产品主名称、当前数据范围和禁止结论。
3. [首个纵向切片锚点合同](Domeye_First_Vertical_Slice_Anchor_v1.0.md)：确认 M0/M1 的固定问题、身份、执行链、J1–J5 与 DG1 门槛。
4. [首个纵向切片目标效果与实现硬边界 v1.0](Domeye_First_Vertical_Slice_Target_Effect_v1.0.md)：合并五个核心难点，以四个 Spike 验证 Pi 决策因果、Capability Gateway、Observation Replan 和轨迹 Eval。
5. [目标架构 v1.1](Domeye_Agent_Target_Architecture_v1.1.md)：理解长期职责和迁移方向，不将其当作当前建设清单。
6. [用户回答与内部证据边界 v1.0](Domeye_Agent_User_Answer_and_Internal_Evidence_Boundary_v1.0.md)：明确用户正文、内部证据、简洁表达和完成语义；当前状态为 `Designed`。
7. [首个纵向切片回答呈现附加合同 v1.0](Domeye_First_Vertical_Slice_Answer_Presentation_Addendum_v1.0.md)：在不改写锚点 v1.0 的前提下，固定 Answer Context v2、结构化 Draft、Host 拼接和 Guard v2。
8. [M0/M1 目标架构摘要](target-architecture.md)：只看首片直接涉及的 Pi、Host、Tool、Operator、Finding 和回答安全链。
9. [Capability Map](capability-map.md)：按可观察能力查看建设范围和成熟度。

产品定义冲突看产品边界；首片固定身份、执行链和验收冲突看锚点合同；代码行为冲突看
对应 Candidate 的测试、机器合同和代码。合同目前为 `Designed`；文档存在不代表能力已经
实现、验证或发布。

## 当前 M0/M1 边界

- Pi 每次只提出一个下一步 Action，观察真实结果后再继续、澄清、重选或停止。
- Host 对每个 Action 分别完成身份、权限、预算、版本和输入准入。
- Tool 读取冻结事实，Operator 对合格输入做确定性变换。
- Host 生成 Typed Finding 和最小 Answer Context；Renderer 草拟回答；Response Guard 只做 `pass/block`。
- J1–J5 共同验收正常链、拒绝、执行失败、回答越界和确定性边界情况。

通用 Plan IR、预生成整条 DAG、Claim Candidate / Validator / Publisher、Verified Claim Set 和新的耐久工作流引擎都不是 M0/M1 目标。
