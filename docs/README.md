# Domeye 文档导航

本文只导航当前开发依据。历史方案、阶段记录和旧验收材料已从当前树删除；需要追溯时使用 Git 历史或明确的发布 tag，不能把旧结论跨 Candidate 继承。

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

这两份是横切回答合同和首片附加合同，不替代上面的七份核心文档。实现、评测与发布状态必须从同一 Candidate 的机器合同和验收记录读取。

## Current 入口

- [架构导航](architecture/README.md)：当前 M0/M1 执行循环、职责边界和延后事项。
- [Agent 入口](agent/README.md)：说明当前 Agent 文档位置；本目录不再存放旧 P0/P1/P2 计划。
- [Core 入口](core/README.md)：现有 Core 的固定数据档、开发循环和验收边界。
- [ADR](adr/ADR-001-pi-as-agent-runtime.md)：Pi Runtime 选型及其当前适用边界。
- [Roadmap](roadmap/README.md)：当前锚点、整改对应表、阶段计划和 Capability Map。
- [static INFO 最终验收合同](INFO目录数据落库最终验收文档.md)与[分阶段计划](INFO目录数据落库分阶段计划.md)：仍被 `contracts/info/static-info-final-acceptance-v1.json` 按精确 SHA 消费，因此保留原路径与原字节。
- [数据层 224–310 最终验收合同](Domeye数据层224-310最终验收文档.md)：仍被独立 RRC25 影子迁移候选生成器用作 DLAE-01 至 DLAE-16 的语义来源。

## 权威顺序

1. 当前 Worktree 的 `.codex/TASK.json` 与 `AGENTS.md`。
2. `main` 对应提交的代码、测试和机器可读合同。
3. 产品、数据与 Claim 边界；首片锚点合同；目标架构及其 ADR。
4. 22 项审计对应表与开发阶段计划。
5. GitHub 管理状态。

GitHub 是协调面，不是产品事实的最高权威。需要恢复历史材料中的规则时，必须从对应 Git 对象取证，并在当前架构下重新形成一个有边界的任务；不要直接把旧文档当作生产路由、完整 Plan/DAG、通用 Claim Publisher 或当前能力证明。

## 新增文档规则

- 当前设计、合同和开发入口放入对应的 `architecture/`、`governance/`、`requirements/`、`roadmap/`、`agent/` 或 `core/`。
- 仍被当前 Candidate 消费的轨迹、回执和验收记录放入 `evaluation/`，并写清绑定身份；已被取代的阶段材料不继续堆积在当前树。
- 不在 `docs/` 根目录继续新增孤立的阶段计划、最终验收或上下文包；上面的 static INFO 两份文件仅因现行机器合同固定路径与 SHA 而保留。
- 需要长期保留的规则应进入现行合同或 ADR；一次性计划完成后删除，不建立新的文档归档堆。
