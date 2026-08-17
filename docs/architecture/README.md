# 架构导航

本目录回答两个问题：Domeye 当前要闭合哪条用户旅程，以及各部分分别承担什么职责。
M0/M1 不以架构层、文件或 Issue 数量衡量进度。

## 阅读顺序

1. [首个纵向切片锚点合同](Domeye_First_Vertical_Slice_Anchor_v1.0.md)：M0/M1 的权威问题、身份、执行链、J1–J5 与 DG1 门槛。
2. [Capability Map](capability-map.md)：按可观察能力查看建设范围和成熟度。
3. [目标架构](target-architecture.md)：理解 Pi、Host、Tool、Operator、Finding 和回答安全链的职责边界。

发生冲突时，以首个纵向切片锚点合同为准。合同目前为 `Designed`；文档存在不代表能力已经实现、验证或发布。

## 当前 M0/M1 边界

- Pi 每次只提出一个下一步 Action，观察真实结果后再继续、澄清、重选或停止。
- Host 对每个 Action 分别完成身份、权限、预算、版本和输入准入。
- Tool 读取冻结事实，Operator 对合格输入做确定性变换。
- Host 生成 Typed Finding 和最小 Answer Context；Renderer 草拟回答；Response Guard 只做 `pass/block`。
- J1–J5 共同验收正常链、拒绝、执行失败、回答越界和确定性边界情况。

通用 Plan IR、预生成整条 DAG、Claim Candidate / Validator / Publisher、Verified Claim Set 和新的耐久工作流引擎都不是 M0/M1 目标。
