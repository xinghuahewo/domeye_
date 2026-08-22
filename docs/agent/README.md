# Agent 当前开发入口

这里是当前 Agent 重构的导航页，不再混放旧 P0/P1/P2 计划、W6 设计包或历史认证记录。

## 当前依据

Agent 当前开发只按以下顺序进入：

1. [目标架构 v1.1](../architecture/Domeye_Agent_Target_Architecture_v1.1.md)
2. [产品、数据与 Claim 边界 v1.1](../architecture/Domeye_Product_Data_Claim_Boundary_v1.1.md)
3. [首个纵向切片锚点合同 v1.0](../architecture/Domeye_First_Vertical_Slice_Anchor_v1.0.md)
4. [当前代码基线](../architecture/Domeye_Current_Code_Baseline_2026-08-16.md)
5. [22 项整改审计对应表](../requirements/Domeye_Adversarial_Review_22_Items_Traceability_v1.1.md)
6. [22 项开发优先级与阶段计划](../roadmap/Domeye_22_Items_Development_Priority_and_Stages_v1.1.md)
7. [用户回答与内部证据边界 v1.0](../architecture/Domeye_Agent_User_Answer_and_Internal_Evidence_Boundary_v1.0.md)

回答呈现边界见[首个纵向切片回答呈现附加合同 v1.0](../architecture/Domeye_First_Vertical_Slice_Answer_Presentation_Addendum_v1.0.md)。实现、评测与发布结论必须绑定同一 Candidate，不能从设计文档自动推导。

当前产品主名称是 **Domeye 国家网络中断调查 Agent**；当前首片只提供已绑定事件、publication、revision、单一 RRC25 和冻结时间窗内的 **RRC25 BGP 控制面证据调查**。产品名不能扩大当前事实边界。

旧 P0/P1/P2、W6 设计包和被取代的阶段记录不再保留在当前树。确需追溯时从对应 Git 提交或发布 tag 读取，且不得把历史结论当作当前任务路由、架构或 Candidate 证明。
