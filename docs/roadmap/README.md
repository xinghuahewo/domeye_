# Roadmap 导航

Roadmap 用用户旅程、风险和同一 Candidate 的证据管理进度，不用模块数量或文档数量代替完成度。

## 当前权威入口

1. [首个纵向切片锚点合同](../architecture/Domeye_First_Vertical_Slice_Anchor_v1.0.md)：M0/M1 的固定目标、J1–J5 与 DG1。
2. [Capability Map](../architecture/capability-map.md)：当前需要形成的可观察能力。
3. [Epics](epics.md)：按能力和风险组织的工作范围。
4. [Feature Breakdown](feature-breakdown.md)：可产生用户效果或关闭风险的 Feature。
5. [Roadmap 视图](roadmap-view.md)：M0、M1 和 DG1 的推进关系。
6. [进度可视化](progress-visualization.md)：如何区分工作进展、能力成熟度和 Gate 决定。

## 辅助材料

- [Capability Roadmap 模板](capability-roadmap.md)
- [Milestone 模板](milestones.md)
- [开发原则](development-principles.md)

辅助材料不能覆盖锚点合同。其中出现的通用 Plan / Workflow、Answer Publication 或分层完成度，
不得解释为 M0/M1 要建设通用 Plan IR、预生成 DAG 或 Claim 发布体系。

## 使用规则

- 先问一个工作项推进了哪条 J1–J5 旅程或关闭了哪个已证实风险，再创建 Issue。
- 每个实现和评测都绑定同一 Candidate；Candidate 改变后重新评测。
- `Designed → Implemented → Verified → Released` 是能力成熟度，不等于 Issue、PR 或 Project 状态。
- 当前首个纵向切片仍为 `Designed`；只有真实运行、独立 Acceptance Record 和 DG1 才能推进结论。
