# Domeye 文档导航

本页按“现在用来做决定，还是只用于追溯”整理文档。分类不移动、不删除文件，也不改变
历史制品摘要；它只规定当前阅读方式。

## 先看权威优先级

| 优先级 | 判断范围 | 权威来源 |
|---:|---|---|
| 1 | 当前任务允许改什么、必须检查什么 | 当前 Worktree 的 `.codex/TASK.json` 与 [AGENTS.md](../AGENTS.md) |
| 2 | 代码现在实际做什么 | 当前测试、机器合同和代码；文档不能覆盖运行事实 |
| 3 | 产品主名称、数据范围和对外事实强度 | [产品、数据与 Claim 边界 v1.1](architecture/Domeye_Product_Data_Claim_Boundary_v1.1.md) |
| 4 | M0/M1 冻结身份、执行链、J1–J5 与 DG1 | [首个纵向切片锚点合同](architecture/Domeye_First_Vertical_Slice_Anchor_v1.0.md) |
| 5 | 长期职责、R01–R22 完整性和阶段顺序 | [目标架构 v1.1](architecture/Domeye_Agent_Target_Architecture_v1.1.md)、[审计对应表](requirements/Domeye_Adversarial_Review_22_Items_Traceability_v1.1.md)、[阶段计划](roadmap/Domeye_22_Items_Development_Priority_and_Stages_v1.1.md) |
| 6 | GitHub、主干、发布和状态管理 | [GitHub 治理套件](governance/README.md)、[主干治理规范](主干开发与发布归一治理规范.md) |
| 7 | 旧设计、旧计划和历史 Evidence | 只能解释过去，不能覆盖前六项或证明当前 Candidate |

同一级发生冲突时，机器合同优先于说明文档；后发布且明确替代旧语义的合同优先。文件名
中的“最终验收”“已认证”或“生产”只描述该文件绑定的历史对象，不能跨 Candidate 继承。

## Current

这些文档用于当前开发或当前 M0/M1 决策。`Current` 表示“当前可引用”，不表示其中设计
已经实现。

### Agent 重构

七份核心交接文档按以下顺序阅读：

| 顺序 | 文档 | 地位 |
|---:|---|---|
| 1 | [当前代码基线](architecture/Domeye_Current_Code_Baseline_2026-08-16.md) | 固定 `main@6a4bbd4…` 的观察事实；不会自动代表后续提交 |
| 2 | [产品、数据与 Claim 边界 v1.1](architecture/Domeye_Product_Data_Claim_Boundary_v1.1.md) | 产品主名称、当前数据范围与禁止陈述 |
| 3 | [首个纵向切片锚点合同 v1.0](architecture/Domeye_First_Vertical_Slice_Anchor_v1.0.md) | 当前唯一 M0/M1 交付、J1–J5 与 DG1；状态为 `Designed` |
| 4 | [目标架构 v1.1](architecture/Domeye_Agent_Target_Architecture_v1.1.md) | 长期职责与禁止路径；不是 M0/M1 建设清单 |
| 5 | [22 项整改审计对应表 v1.1](requirements/Domeye_Adversarial_Review_22_Items_Traceability_v1.1.md) | 按 R01–R22 原顺序防遗漏 |
| 6 | [22 项开发优先级与阶段计划 v1.1](roadmap/Domeye_22_Items_Development_Priority_and_Stages_v1.1.md) | 按依赖、风险和 Decision Gate 排序 |
| 7 | [Domeye GitHub 治理套件 v1.0](governance/README.md) | 管理 Issue、Project、Candidate、Evidence 和状态同步 |

专项导航仍保留：[架构导航](architecture/README.md)、[M0/M1 架构摘要](architecture/target-architecture.md)、
[Capability Map](architecture/capability-map.md)、[Architecture Refactor Epics](roadmap/epics.md)、
[Agent Feature Breakdown](roadmap/feature-breakdown.md)、[Roadmap 导航](roadmap/README.md)、
[Roadmap 视图](roadmap/roadmap-view.md)和[进度可视化规则](roadmap/progress-visualization.md)。
它们解释具体工作，但不能覆盖七份核心文档。

[ADR-001：使用 Pi 作为 Agent Runtime](adr/ADR-001-pi-as-agent-runtime.md)的 Pi 选型仍适用；
其中旧 Claim Publication 用语已由首切片的
`Typed Finding → Answer Context → Renderer → Response Guard` 边界修订。

### Domeye Core 现有运行时

- [二三月固定开发模式](二三月固定开发模式.md)：当前数据档、时钟、端口和禁止边界。
- [开发与验收流水线](开发与验收流水线.md)：快速开发、集成、发布检查及副作用边界。
- [主干开发与发布归一治理规范](主干开发与发布归一治理规范.md)：`main`、Candidate、
  不可变制品、部署与验证状态。
- [后端说明](../backend/README.md)：API、配置、数据库和冻结 Core 边界。
- [部署说明](../deploy/README.md)：制品、数据库、运行管理、验收、发布与回滚入口。
- [数据档机器配置](../config/data-profile.json)：数据范围、快照时钟和业务时区的唯一配置源。

## Legacy-Frozen

这些文档保留旧目标、旧阶段或讨论上下文。在当前 M0/M1 中按“冻结参考”阅读，不继续
把其中的通用 Plan/DAG、Claim Validator/Publisher、Verified Claim Set、28/35 题平台
或通用 RCA 当成首切片依赖。

| 文档或集合 | 当前用途 |
|---|---|
| [旧国家中断 Agent 建设总纲](agent/国家中断Agent建设总纲.md) | 保留 P0–P5 历史工程语境；不再定义当前 M0/M1 建设清单 |
| [旧 P0–P2 Agent 计划与合同目录](agent/) | 兼容旧实现、定位旧 Candidate 与追溯设计来源 |
| [22 项旧追踪矩阵](requirements/adversarial-review-tracking.md) | 保留 Review ID；旧 Claim Verification 等映射不能替代当前 Epics/Features |
| [旧需求追踪格式](requirements/requirements-traceability.md) | 仅作字段参考；状态值不得覆盖当前五轴治理规则 |
| [事件详情页产品合同](事件详情页产品合同.md) | 现有 Core 产品设计背景；不能扩大首切片的单 RRC25 证据边界 |
| [国家中断通用观测页 API 合同](国家中断通用观测页API合同.md) | 旧页面/API 兼容语义；不是新 Agent 的执行合同 |
| [前端事件研判叙事设计](前端事件研判叙事设计.md) | 旧页面叙事背景；不能作为首切片 Answer Context 或 Guard 规则 |

除 `Current`、`Historical Evidence` 和 `Template` 明确列出的文件外，原有阶段计划、最终
验收目标、上下文包和研究方案默认归入 `Legacy-Frozen`。运维与身份说明必须逐文件
对照当前代码、机器合同和任务范围判断，不能仅因未列在本页就归为旧资料。需要恢复旧
文档中的某条规则时，应先对照当前锚点并通过新任务重新评审。

## Historical Evidence

这些内容保存过去某个数据档、合同、Candidate 或运行的输入、轨迹、回执和结论。它们
可以用于复现、回归和发现风险，但不能直接证明首个纵向切片已经
`Implemented / Verified / Released`。

| Evidence 集合 | 说明 |
|---|---|
| [evaluation/](../evaluation/) | 旧 P0/P1/P2 Candidate 的案例、原始轨迹、回执、oracle 和评审结果 |
| [docs/data/](data/) | **Mixed**：数据字典等文件仍可作为当前规范/语义合同；重放、对账、质量门禁、哈希和阶段验收记录按精确文件、数据身份与 Candidate 判定 |
| [P0 v1 基线验收记录](agent/P0-需求与评测/P0-v1-基线验收记录.md) | 旧需求/评测基线的绑定结果 |
| [P1 页面能力覆盖验收记录](agent/P1-聊天问答/P1-页面能力覆盖-阶段与最终验收记录.md) | 旧页面能力 Candidate 的阶段证据 |
| [P2 W6 离线确定性实现验收说明](agent/P2-组合式调查/实体调查实现工程/W6-离线确定性实现验收说明.md) | 旧离线确定性实现边界，不等于真实 Agent 闭环 |
| [国家中断报告 Agent A5 联合验收记录](国家中断报告与追问AgentA5联合验收记录.md) | 旧报告与追问 Candidate 的联合验收记录 |
| [国家中断通用观测页 S5 最终验收记录](国家中断通用观测页S5最终验收记录.md) | 旧通用观测页 Candidate 证据 |
| [国家中断趋势分析 S6 最终验收报告](国家中断趋势分析S6最终验收报告.md) | 旧趋势分析 Candidate 证据 |
| [RRC25 同期全局状态重放 S6 报告](data/RRC25伊朗同期全局状态重放S6最终验收报告.md) | 特定窗口与重放实现的历史证据 |

判断一份历史 Evidence 是否还能使用，至少核对 Candidate、合同版本、publication、
revision、collector、窗口、数据摘要和执行单元摘要。任一身份变化都必须重新判定，
不能沿用旧绿色状态。

## Template

模板只提供结构，不携带项目事实，也不表示已获写入、合并或发布授权。

| 模板 | 用途 |
|---|---|
| [Codex TASK 示例](../.codex/TASK.example.json) | 创建单 Worktree、单任务的允许/禁止路径与检查合同 |
| [ADR 模板](adr/0000-adr-template.md) | 记录重要架构决策的上下文、选择和后果 |
| [Completion Packet 模板](governance/Completion_Packet.template.yaml) | 向 GitHub 收尾同步器声明单一 Primary Issue、Candidate、Evidence 和精确授权 |
| [Architecture Issue 模板](../.github/ISSUE_TEMPLATE/architecture.md) | 架构工作 Issue |
| [Capability Issue 模板](../.github/ISSUE_TEMPLATE/capability.md) | 能力增量 Issue |
| [Bug Issue 模板](../.github/ISSUE_TEMPLATE/bug.md) | 缺陷 Issue |
| [Pull Request 模板](../.github/PULL_REQUEST_TEMPLATE.md) | PR 边界、验证和状态声明 |
| [Capability Roadmap 空表](roadmap/capability-roadmap.md) | 能力状态草稿；实际状态值以 GitHub 管理规则为准 |
| [Milestone 规划模板](roadmap/milestones.md) | 阶段目标、用户效果、Evidence、非目标和退出条件 |

新增文档时先判断它属于哪一类，并在正文写清适用范围、状态、Candidate/数据身份和被谁
替代。不要再用孤立的“最终”“通过”或“生产”作为跨版本结论。
