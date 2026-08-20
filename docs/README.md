# Domeye 文档导航

本文把“当前开发依据”和“历史材料”分开。文件名中的“最终验收”“生产”或“已认证”只描述它所绑定的历史对象，不能跨 Candidate 继承。

## 当前 Agent：先读这七份

| 顺序 | 文档 | 作用 |
|---:|---|---|
| 1 | [当前代码基线](architecture/Domeye_Current_Code_Baseline_2026-08-16.md) | 说明 `main` 某一提交上实际存在什么；不是目标架构，也不是生产证明。 |
| 2 | [产品、数据与 Claim 边界 v1.1](architecture/Domeye_Product_Data_Claim_Boundary_v1.1.md) | 固定产品名称、当前 RRC25 数据范围和禁止扩大解释的结论。 |
| 3 | [首个纵向切片锚点合同 v1.0](architecture/Domeye_First_Vertical_Slice_Anchor_v1.0.md) | 固定 M0/M1 的一条真实用户闭环、J1–J5 和 DG1。 |
| 4 | [目标架构 v1.1](architecture/Domeye_Agent_Target_Architecture_v1.1.md) | 说明 Agent、Trust Kernel、Tool、Operator 和回答链的长期职责。 |
| 5 | [22 项整改审计对应表 v1.1](requirements/Domeye_Adversarial_Review_22_Items_Traceability_v1.1.md) | 按 R01–R22 防止遗漏、偷换和弱化评审问题。 |
| 6 | [22 项开发优先级与阶段计划 v1.1](roadmap/Domeye_22_Items_Development_Priority_and_Stages_v1.1.md) | 按依赖、风险和 Decision Gate 安排建设顺序。 |
| 7 | [GitHub 治理套件](governance/README.md) | 说明 Issue、Milestone、Project、Candidate、Evidence 和状态轴。 |

七份文档不是七个并行项目。当前首片合同仍是 `Designed`；代码、PR、历史验收或 Issue 状态不能自动把它提升为 `Implemented`、`Verified` 或 `Released`。

## 回答体验专项

- [用户回答与内部证据边界 v1.0](architecture/Domeye_Agent_User_Answer_and_Internal_Evidence_Boundary_v1.0.md)：定义用户最终看到什么、哪些证据只留在内部，以及成功、澄清、拒绝和失败的表达边界。
- [首个纵向切片回答呈现附加合同 v1.0](architecture/Domeye_First_Vertical_Slice_Answer_Presentation_Addendum_v1.0.md)：保留锚点 v1.0 的事实与执行合同，单独版本化 Answer Context、Renderer Draft、Host 拼接和 Guard 呈现边界。
- [回答风格开发与验收计划 v1.0](roadmap/Domeye_Agent_Answer_Style_Development_and_Acceptance_Plan_v1.0.md)：把实现、新 Candidate、真实评测和生产收口拆成独立阶段。

这三份是横切回答合同、首片附加合同和专项计划，不替代上面的七份核心文档，也不表示新风格已经完成真实评测或发布。

## Current 入口

- [架构导航](architecture/README.md)：当前 M0/M1 执行循环、职责边界和延后事项。
- [Agent 入口](agent/README.md)：说明当前 Agent 文档位置；本目录不再存放旧 P0/P1/P2 计划。
- [Core 入口](core/README.md)：现有 Core 的固定数据档、开发循环和验收边界。
- [ADR](adr/ADR-001-pi-as-agent-runtime.md)：Pi Runtime 选型及其当前适用边界。
- [Roadmap](roadmap/README.md)：Capability、Epic、Feature、Milestone 和进度视图。

## 归档入口

旧文档不删除，但不再放在当前开发入口中：

| 归档区 | 内容 | 阅读规则 |
|---|---|---|
| [Agent 历史材料](archive/agent/README.md) | W6 讨论包、旧 P0/P1/P2 计划与合同、旧 Agent 验收/认证记录。 | 只能作为迁移输入和历史证据；与当前架构冲突时以当前七份核心文档为准。 |
| [Core 历史材料](archive/core/README.md) | 旧事件详情页、通用观测页、趋势分析、伊朗页面与前端叙事设计。 | 说明旧页面或旧 Candidate，不定义新 Agent 的回答边界。 |
| [数据历史材料](archive/data/README.md) | 旧数据层、INFO 落库、P0 数据基础、RRC25 重放和数据证据。 | 使用前核对数据档、publication、revision、collector、时间窗和 Candidate。 |

## 权威顺序

1. 当前 Worktree 的 `.codex/TASK.json` 与 `AGENTS.md`。
2. `main` 对应提交的代码、测试和机器可读合同。
3. 产品、数据与 Claim 边界；首片锚点合同；目标架构及其 ADR。
4. 22 项审计对应表与开发阶段计划。
5. GitHub 管理状态和历史文档。

GitHub 是协调面，不是产品事实的最高权威。需要恢复归档材料中的规则时，必须在当前架构下重新形成一个有边界的任务；不要直接把归档文档重新当作生产路由、完整 Plan/DAG、通用 Claim Publisher 或当前能力证明。

## 新增文档规则

- 当前设计、合同和开发入口放入对应的 `architecture/`、`governance/`、`requirements/`、`roadmap/`、`agent/` 或 `core/`。
- 特定 Candidate 的轨迹、回执、验收报告和旧方案放入 `archive/` 或仓库的 `evaluation/`，并写清绑定身份。
- 不在 `docs/` 根目录继续新增孤立的阶段计划、最终验收或上下文包。
- 只移动路径和导航时，正文历史内容保持不改；如必须修复相对链接，应在提交说明中单独标出。

本次整理从根 `docs/` 和 `docs/agent/` 移出 128 份旧文档，保留原文并建立归档索引；没有修改代码、机器合同 JSON 或 `evaluation/` 制品。
