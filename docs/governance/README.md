# Domeye GitHub 治理套件 v1.0

本目录是七份核心交接文档中的“管理方法”入口。它只管理 GitHub 工作状态、候选身份、
证据引用与可恢复同步，不定义产品事实，也不能把文档或卡片状态提升为首片已实现。

## 套件组成

| 制品 | 作用 | 当前状态 |
|---|---|---|
| [GitHub 管理与任务收尾同步规则](Domeye_GitHub_Management_Rules_v1.0.md) | 定义 Issue、Milestone、Project、PR、五个状态轴和同步不变量 | `Designed` |
| [Completion Packet 模板](Completion_Packet.template.yaml) | 为一次受权的收尾同步绑定 Primary Issue、Candidate、Evidence 和精确动作 | 模板已入库 |
| [项目级收尾代理](../../.codex/agents/github-closeout.toml) | 按 Completion Packet 做预检、写入与读回 | 配置已入库；须在新运行中另行验证发现和权限 |
| [根 AGENTS 规则](../../AGENTS.md) | 固定 `main`、任务隔离和收尾调用边界 | 已纳入当前分支历史 |
| [服务器目录治理计划](Domeye_Server_Directory_Governance_Plan_v1.0.md) | 定义旧 Domeye 保护边界、只读盘点、隔离、保留和删除 Gate | `Designed`；只读工具在任务分支实现 |
| [服务器目录治理 S0 只读基线](evidence/server-directory-baseline-2026-08-18.md) | 记录目录、进程、活动指针、磁盘和风险的带时间戳观察 | `Observed`；Gate=`BLOCK_MUTATION` |
| [服务器 checkout S2 预检](evidence/server-checkout-normalization-2026-08-18-preflight.json) | 冻结合并后 `main`、脏 checkout 与活动指针的再审计 | `Observed`；尚不授权服务器写入 |

## 使用边界

- 套件存在不等于已经修改 GitHub Issues、Projects 或 Milestones。
- 配置文件存在不等于当前运行已经发现或启用了收尾代理。
- GitHub 同步采用 `Pending → 写入 → 立即读回 → 全量读回 → Synced`；任一步失败保留
  `Pending / Partial`，不得猜测成功。
- `Done`、PR merged、Project Synced、`Implemented`、`Verified` 和 `Released` 彼此不能
  自动推导。
- 当前首片目标与边界分别以[锚点合同](../architecture/Domeye_First_Vertical_Slice_Anchor_v1.0.md)
  和[产品、数据与 Claim 边界](../architecture/Domeye_Product_Data_Claim_Boundary_v1.1.md)为准。

文档目录整理只调整文档路径和导航，不修改 GitHub Issues、Projects、Milestones 或生产环境；这些对象是否同步仍按本套规则和独立授权处理。
