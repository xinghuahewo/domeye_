# Roadmap 导航

Roadmap 用用户旅程、风险和同一 Candidate 的证据管理进度，不用模块数量或文档数量代替完成度。

## 当前权威入口

1. [首个纵向切片锚点合同](../architecture/Domeye_First_Vertical_Slice_Anchor_v1.0.md)：M0/M1 的固定目标、J1–J5 与 DG1。
2. [22 项整改审计对应表](../requirements/Domeye_Adversarial_Review_22_Items_Traceability_v1.1.md)：按 R01–R22 原顺序防遗漏，不是 22 个平行功能。
3. [22 项开发优先级与阶段计划](Domeye_22_Items_Development_Priority_and_Stages_v1.1.md)：按依赖和风险安排阶段，并保留条件建设项。
4. [Capability Map](../architecture/capability-map.md)：当前需要形成的可观察能力。

## 回答体验专项

- 目标效果以[用户回答与内部证据边界 v1.0](../architecture/Domeye_Agent_User_Answer_and_Internal_Evidence_Boundary_v1.0.md)和[首片回答呈现附加合同](../architecture/Domeye_First_Vertical_Slice_Answer_Presentation_Addendum_v1.0.md)为准。

该专项不改变 22 项总路线，也不把文档完成、前端隐藏或旧 Candidate 验收写成新回答风格已经完成。

已删除与上述权威入口重复的通用 Epics、Feature Breakdown 和开发原则。新的工作项直接从锚点、整改对应表、阶段计划与 Capability Map 推导，不再维护第二套规划文档。

## 使用规则

- 先问一个工作项推进了哪条 J1–J5 旅程或关闭了哪个已证实风险，再创建 Issue。
- 每个实现和评测都绑定同一 Candidate；Candidate 改变后重新评测。
- `Designed → Implemented → Verified → Released` 是能力成熟度，不等于 Issue、PR 或 Project 状态。
- 当前首个纵向切片仍为 `Designed`；只有真实运行、独立 Acceptance Record 和 DG1 才能推进结论。
- R01–R22 只作审计和风险检查维度；不得为了“清单完成率”提前建设通用平台。
