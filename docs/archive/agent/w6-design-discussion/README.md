# W6 旧 Agent 设计讨论包归档

> 状态：Legacy-Frozen（历史冻结参考）  
> 来源：2026-08-14 W6 v3 本地工作树讨论包  
> 归档原则：保留原文，明确历史地位；不让旧设计覆盖当前目标架构、产品边界或代码事实。

本目录保存上一轮 Agent 架构讨论材料。它们用于理解旧系统、定位旧 Candidate、迁移已有
Tool / Operator / Registry / ResultSet 资产，以及解释为什么需要当前重构；不再作为新开发
的产品合同、执行合同或验收结论。

当前开发先读：

1. [目标架构 v1.1](../../../architecture/Domeye_Agent_Target_Architecture_v1.1.md)
2. [产品、数据与 Claim 边界 v1.1](../../../architecture/Domeye_Product_Data_Claim_Boundary_v1.1.md)
3. [首个纵向切片锚点合同 v1.0](../../../architecture/Domeye_First_Vertical_Slice_Anchor_v1.0.md)
4. [当前代码基线](../../../architecture/Domeye_Current_Code_Baseline_2026-08-16.md)
5. [总文档导航](../../../README.md)

## 1. 本次归档的文件组成

本次收到的 9 个附件实际是 **8 份编号设计文档 + 1 份旧 README**。旧 README 的阅读顺序
还引用了编号 06，但本次没有提供：

| 编号 | 原文件 | 归档位置 | 当前处理 |
|---|---|---|---|
| 01 | 当前系统功能与能力边界.md | [01-当前系统功能与能力边界.md](w6-discussion-package/01-当前系统功能与能力边界.md) | 保留为 W6 现状快照；当前事实以代码基线、产品边界和锚点合同为准 |
| 02 | 原子Tool设计与目录.md | [02-原子Tool设计与目录.md](w6-discussion-package/02-原子Tool设计与目录.md) | 保留 Tool 设计资产；不表示整套 Tool 已迁入新首片 |
| 03 | 确定性Operator设计与目录.md | [03-确定性Operator设计与目录.md](w6-discussion-package/03-确定性Operator设计与目录.md) | 保留确定性计算经验；首片只迁移合同明确需要的最小能力 |
| 04 | Registry合同与生命周期治理.md | [04-Registry合同与生命周期治理.md](w6-discussion-package/04-Registry合同与生命周期治理.md) | 作为 Trust Kernel / Registry 迁移输入，不作为新治理合同 |
| 05 | LLM与Host职责边界.md | [05-LLM与Host职责边界.md](w6-discussion-package/05-LLM与Host职责边界.md) | 以 Pi Proposal、Trust Kernel 准入和 Finding-first 新边界修订 |
| 06 | 任务规划与DAG执行模型.md | 未提供 | 不制造空文件或虚假正文；旧 README 中对应链接视为缺失引用 |
| 07 | ResultSet、EvidenceGraph、状态与API.md | [07-ResultSet、EvidenceGraph、状态与API.md](w6-discussion-package/07-ResultSet、EvidenceGraph、状态与API.md) | 保留制品与状态经验；当前首片采用 Typed Finding / Artifact / Trace 的最小合同 |
| 08 | 当前断层与架构路线讨论.md | [08-当前断层与架构路线讨论.md](w6-discussion-package/08-当前断层与架构路线讨论.md) | 作为旧双运行时断层诊断；当前路线以目标架构 v1.1 为准 |
| 09 | 术语表与源码索引.md | [09-术语表与源码索引.md](w6-discussion-package/09-术语表与源码索引.md) | 仅作历史索引；术语冲突以当前架构、锚点合同和机器合同为准 |
| — | README.md | [README.md](w6-discussion-package/README.md) | 保留原始讨论包说明；不作为当前仓库导航入口 |

## 2. 旧设计与新架构的关系

| 旧讨论重点 | 当前依据 |
|---|---|
| “一次性生成完整 Plan / DAG” | 开放任务改为 Pi 每次提出一个 Proposal，Trust Kernel 逐项准入；M0/M1 不建设通用 Plan IR 或预生成整图 |
| “LLM + 大 Host” | Pi 负责认知循环和动作提议；Domeye Trust Kernel 负责身份、权限、Registry、预算、准入和正式提交 |
| Tool / Operator / Registry 目录 | 保留为迁移输入；只将首个真实纵向切片需要的能力接入新 Capability Gateway |
| ResultSet / EvidenceGraph | 保留可冻结人口、可追溯执行和恢复经验；首片先闭合 Artifact、Typed Finding、Answer Context、Trace 和 Response Guard |
| 通用 Claim Validator / Publisher / Verified Claim Set | 当前明确不建设；Claim 只表示回答中的事实性陈述，事实先以 Typed Finding 存在 |
| 28 题 / 固定 Question Template 路由 | 不作为新生产路由；Capability Family 只用于合同、文档和评测覆盖，不把问题映射到固定 DAG |
| P1/P2 两套链路 | 旧代码和旧 Candidate 继续作为迁移/回归资产；新目标以 Agent Action、必要时 Durable Job 的边界收敛 |

## 3. docs 之外的历史或过渡资产

这些路径不能简单删除或移动，因为当前测试、脚本、历史回放和候选复现可能仍引用它们。
本次通过本索引明确其地位，后续迁移任务再按实际引用逐项处理：

| 路径 | 分类 | 当前处理 |
|---|---|---|
| `contracts/agent/country-outage-p2-s0a-lifecycle/` | 旧 Registry 生命周期机器合同 | 冻结保留；不作为新 M0/M1 合同权威 |
| `contracts/agent/country-outage-p2-s0b-runtime/` | 旧 P2 运行时机器合同 | 冻结保留；仅用于旧 Candidate / 迁移回归 |
| `contracts/agent/country-outage-p2-s1-execution-unit-design/` | 旧 Tool / Operator / DAG 设计合同 | 冻结保留；新能力须重新绑定当前锚点 |
| `contracts/agent/country-outage-p2-s1-implementation/` | W0–W6 实现与验收合同 | 作为历史候选证据；不等于新 Agent 已实现 |
| `evaluation/country-outage/p2-*/` | 旧 P2 Candidate 的案例、回执和阶段证据 | 仅证明各自绑定的 Candidate、publication 和 revision |
| `agent-sidecar/src/chat/p2-*` | 旧 P2 TypeScript 编排与适配代码 | 保留作迁移和回归资产，不扩大为新主路径 |
| `backend/services/country_outage_p2_s1_*` | 旧 P2 Python Tool / Operator / 调查运行时 | 保留作迁移和回归资产，不代表当前首片已接入 |
| `backend/web/api/v2/country_outage_investigations.py` 与对应前端调查页 | 旧调查 API / 页面壳 | 可继续用于现有基线观察；不能作为新 Agent 闭环证明 |
| `.codex/hooks/country_outage_agent_p2_*` | 旧 P2 阶段 Hook | 只约束旧路径；新任务以当前 TASK、锚点和新 Gate 为准 |
| `openspec/changes/` | 历史提案、设计和规格 | 提案不自动表示实现；恢复任何旧方案前重新评审 |

本次不移动这些机器资产的原因是保持旧 Candidate 可重放、避免无证据破坏代码路径；“保留”
不等于“继续扩建”。

## 4. 使用规则

- 看到本目录的文档，默认按历史冻结资料阅读。
- 需要决定产品名称、当前能力、Claim 边界时，只看当前架构目录的三份边界文档。
- 需要决定首片要做什么时，只看锚点合同和绑定的任务合同。
- 需要判断代码是否完成时，只看当前 Candidate 的代码、测试、机器合同和独立 Evidence。
- 需要恢复旧 Tool / Operator / Registry 资产时，先创建明确的迁移任务，绑定新合同、允许路径和验收证据。
- 不以旧文档中的 “P2”“最终验收”“active Registry” 或 “API 存在”推导新架构已实现。

本目录只做归档和导航，不修改原始文档正文。