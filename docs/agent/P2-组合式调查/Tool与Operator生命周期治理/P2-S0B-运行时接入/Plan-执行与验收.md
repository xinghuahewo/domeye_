# P2-S0B Tool/Operator 治理快照运行时接入执行计划

版本：`country-outage-agent-p2-s0b-execution-plan-v1`

状态：执行中；所有阶段仅面向本地候选；禁止部署

## 一、执行约束

- 每阶段先满足入口，再实施工作，最后运行该阶段 Alignment Hook；
- Hook 检查结构、身份、摘要和边界，不能替代产品语义 Reviewer 或端到端测试；
- 每阶段不得改变 `backend/core`、P1/OP-04 权威合同、P2-S0A 合同或生产配置；
- 失败只重跑受影响路径；候选源码变化后必须重建 candidate/snapshot，并重跑后续阶段证据；
- 所有回执绑定同一 S0B candidate；混合候选证据不得合并为验收结论。

## 二、阶段计划

### S0B-0：合同与任务身份冻结

入口：P2-S0A 离线治理候选有效，S0B 独立分支与 TASK 身份完成 preflight。

工作：冻结最终效果、Snapshot Loader、Resolver、Admission、Executor、错误码、Oracle、非目标
和生产入口。

出口：Task Spec、runtime contract、admission schema、shadow oracle 可机器读取；明确“active 仍需
Host Handler”。

到期验收：文档标记、14 类 Oracle、四接口、required/conditional/forbidden 与非部署边界齐全。

### S0B-1：只读快照与内容寻址

入口：S0B-0 Hook 通过。

工作：实现普通文件安全加载、大小限制、结构校验、跨语言 canonical digest、深冻结和一轮一次
读取。

出口：快照缺失、null、篡改、symlink 和摘要冲突均失败关闭；磁盘更新只影响下一轮。

到期验收：同轮 snapshot drift=0；内容寻址往返一致；Runtime Registry 写入口数量=0。

### S0B-2：Capability/Unit 解析与真实计划准入

入口：S0B-1 Hook 通过。

工作：在现有 GroundingPlan 后解析唯一 active 版本；校验双向映射、三类摘要、依赖、权限、
RRC25 身份和 Host Handler；强制事实计划含 TOOL-01。

出口：每个可执行节点获得 Registry Binding；拒绝发生在 Executor 前。

到期验收：非 active、缺 Handler、依赖缺失、摘要冲突、边界错误和缺 required call 的业务执行
次数均为 0。

### S0B-3：Executor 与 Evidence 绑定

入口：S0B-2 Hook 通过。

工作：Executor 拒绝无 Registry Binding 的节点；在成功与失败回执写入 snapshot、revision、版本
和摘要；回答级 runtime identity 与节点回执闭合。

出口：Tool/Operator 的输入输出逻辑不变，新增治理身份可完整审计。

到期验收：Evidence Registry 身份完整率 100%；同一回答所有节点 snapshot ID 唯一。

### S0B-4：Shadow、负例与独立产品语义审核

入口：S0B-3 Hook 通过。

工作：运行应调用、不应调用、missing/null/identity/inactive/tamper/dependency/drift/handler/rollback
用例；运行既有 P1 语义与会话回归；由不导入候选构建器的独立 Reviewer 从 P1/OP-04 真值反推。

出口：量化门全部满足，Reviewer blocking=0；只形成本地 Shadow 结论。

到期验收：S0B 专项 100%，P1 定向回归 100%，越界误执行 0，Reviewer 职责分离。

### S0B-5：同候选验收与 postflight

入口：S0B-4 Hook 通过，候选源码与合同停止变化。

工作：最后一次重建 candidate/snapshot；重跑 Python、TypeScript 与 P1 回归；生成同候选 Reviewer
回执和 acceptance manifest；运行 final Hook、core 摘要、diff check 与 codex-postflight。

出口：本地候选可复现、可审核、未部署；交付总结区分已实现、本地已验证、待生产建设。

到期验收：所有证据 candidate ID 一致，核心摘要通过，工作区差异只在任务允许路径，生产写入=0。

### S0B-6：后续生产灰度（本轮不执行）

入口：新的生产授权、独立 TASK、生产活动指针设计、安全/费用/性能评审和可回滚发布候选。

工作：真实运行进程只读挂载、灰度流量、费用与 P95/P99 性能、监控告警、活动指针原子切换、
单版本和整版回滚演练、浏览器/API/Evidence 同候选验收。

出口：满足生产发布合同后才可声明“生产自动调用”。

本阶段未获授权，不得以 S0B-0..5 的成功替代。

## 三、日常开发者体验

S0B-0..5 完成后，已有 Handler 的兼容版本更新流程为：

```text
一个治理命令提交新版本
→ 自动影响分析和 SemVer 检查
→ Oracle + Reviewer + 认证
→ 生成新快照
→ 新一轮请求自动采用
```

新增全新 Tool/Operator 比兼容更新多一步：实现并登记 Host Handler。退役则通过状态迁移和新
快照完成，无需从多个 Planner 手工删除散落映射。日常流程有门，但操作入口应保持一个；复杂性
被放在自动校验和证据生成中，而不是交给每个开发者记忆。

## 四、验收解释边界

- 类型检查证明接口可编译；
- 单元测试证明固定用例；
- Hook 证明合同标记、身份、摘要和阶段依赖；
- Reviewer 证明候选没有偏离既有产品语义；
- 同候选本地验收证明本分支可进入后续发布候选评审；
- 以上任何一项或其组合都不证明生产已部署、真实流量已切换或用户已使用。
