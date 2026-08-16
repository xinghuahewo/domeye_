# P1 runtime-v2 P2 入口回执

## 一、回执结论

P1 本地候选 `p1-runtime-v2-34dcc7de840e33d8` 已完成 S0—S4，并通过独立产品
语义真值终审、Alignment Hook 与同候选联合验收。

本回执只授权启动 P2 的需求澄清、评测合同和能力发现，不授权直接实现 P2，
不授权接入外部数据，也不表示已具备原因分析或 RCA。

## 二、P2 可继承的 P1 基础

- 开放 UserGoalPlan 保留用户原意，封闭 GroundingPlan 只包含登记且获权的执行节点；
- P0 v1.3 的 17 adopt、5 defer、4 reject、Capability Catalog、Typed Tool Contract 与 Oracle；
- incident、publication、revision、RRC25、cohort、data-through 和 finality 的共享身份门；
- 逐子目标 `supported`、`partial`、`clarify`、`unsupported`、`invalid_data` 裁决；
- 事实只来自确定性 Tool/算子，模型不创造事实、证据或能力；
- EvidenceState 与 DialogState 分离，通过计划、权限、执行、证据和身份复验后才提交；
- 事件/revision 切换、TTL、取消、幂等代次和共享身份冲突的回滚语义；
- 35 案、29/30 目标保真、24/24 Grounding、CAP-018 与 remaining unknown。

## 三、不得继承的虚假结论

- P1 多意图回答不等于 P2 组合调查计划。
- P1 事实时间线不是因果链，时间先后、相关或路径相邻均不是原因。
- RRC25 控制面观测不等于真实用户断网、全国中断、流量下降、责任或恢复。
- defer/reject 能力不会因进入 P2 自动成为 Tool。
- 本地浏览器、API、测试和 Hook 不等于生产完成。

## 四、P2 立项时必须重新定义

1. 哪些“时间线 + ASN + 地址族 + 路径”问题属于组合调查，而不是 P1 并列回答？
2. 调查计划如何表达依赖、顺序、中间事实、终止条件和资源预算？
3. Evidence Graph 如何区分原始证据、确定性派生事实、未知、冲突和限制？
4. P2 是否继续 RRC25-only；多源不得在 P2 静默接入，需按既定 P4 或新合同审批。
5. 如何建立 P2 黄金调查集，并分别计分计划保真、事实正确、证据完整和边界合规？

## 五、启动硬门

P2 必须另行形成 Task Spec、Plan、Alignment Hook 和独立产品语义审核；P1 的 unknown、
本地候选状态、未合并/未部署/未生产验证与 RRC25 边界必须显式继承。

## 六、边界声明

本回执不修改 P1/P0 真值，不实施 P2，不创建原因、责任、政府行为、用户影响、
全国中断、经济损失或 RCA 能力，不接入 RRC25 之外的数据源。
