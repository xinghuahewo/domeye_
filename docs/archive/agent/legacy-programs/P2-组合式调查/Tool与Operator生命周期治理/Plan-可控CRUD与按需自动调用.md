# Tool 与 Operator 可控 CRUD 及按需自动调用计划

版本：`country-outage-agent-tool-governance-and-runtime-plan-v1`

状态：路线冻结；当前只新增计划文档，不实施运行时接入，不部署

关联文档：

- `Task-Spec-最终验收文档.md`
- `Plan-分阶段计划.md`

## 一、目标

本计划只服务两个产品目标：

1. 以后新增、查询、更新、弃用、退役或删除 Tool/Operator 时，所有变化都有统一入口、
   校验、审核、回执、不可变快照和回滚路径；
2. Tool/Operator 通过认证并被加入活动快照后，Agent Runtime 能在问题确实需要该能力时
   自动选择并调用，而不是由开发者继续维护散落的硬编码映射，也不是每轮无条件调用工具。

目标闭环为：

```text
治理面：提案 → 校验 → 审核 → 认证 → 激活 → 退役/回滚
                                      ↓
运行面：固定快照 → 解析能力 → 计划准入 → 按需调用 → Evidence
```

## 二、当前状态

### 2.1 已完成

- Capability Registry 与 Execution Unit Registry 已建立；
- 稳定 ID、SemVer、contract/implementation/semantic digest 和 Registry revision 已建立；
- Create、Read、Update、状态迁移、Tombstone、单单元回滚和整版回滚已有离线入口；
- 当前 6 个 Tool、3 个基础 Operator 和 OP-04 已迁移至统一 Registry；
- 不可变 Registry Snapshot 和离线 Plan Admission 已建立；
- Oracle、独立产品语义 Reviewer、Alignment Hook 和篡改测试已建立。

以上只表示离线治理候选完成，不表示 Agent Runtime 已经从 Registry 自动发现或调用单元。

### 2.2 待建设

- Runtime Snapshot Loader；
- 用户目标到 Capability 的解析；
- 从 Capability 到活动 Execution Unit 的确定性解析；
- 真实 GroundingPlan/InvestigationPlan 准入；
- 统一 Tool/Operator Executor 和 Evidence 回执；
- Shadow 验证以及后续独立生产发布验收。

## 三、冻结原则

### 3.1 唯一写入口

Registry 内容不得通过人工直接编辑完成治理写入。所有变更必须通过统一治理入口生成，
并携带：

- `request_id`；
- actor 与治理角色；
- 操作理由；
- `expected_registry_revision`；
- 目标稳定 ID 和版本；
- 兼容性与影响分析；
- before/after revision；
- 治理回执。

Git PR 是变更审批和历史承载入口，Registry 治理命令是业务规则的唯一执行入口。未来即使
增加 Web UI 或服务 API，也只能包装相同治理规则，不得建立第二套状态迁移逻辑。

### 3.2 CRUD 语义

| 用户动作 | 系统语义 | 禁止行为 |
|---|---|---|
| Create | 创建新稳定 ID 或已有 ID 的新版本，初始不得执行 | 创建即生产可用 |
| Read | 查询当前、历史、依赖、影响、回执和 Tombstone | 读取时隐式改变状态 |
| Update | 生成新 SemVer 版本，重新认证 | 覆盖已发布版本 |
| Deprecate | 停止推荐新计划使用，保留迁移窗口 | 立即删除历史 |
| Retire | 禁止进入任何新计划 | 继续被自动选择 |
| Delete | 清理载荷并生成 Tombstone | 物理抹除身份、复用 ID |
| Rollback | 切换到旧认证版本或旧完整快照 | 原地修改历史版本 |

### 3.3 按需自动调用

“自动调用”必须同时满足：

1. 当前问题需要一个已登记 Capability；
2. 本轮固定快照包含该 Capability；
3. 映射的 Execution Unit 为 `active`；
4. 合同、实现、语义摘要与快照一致；
5. 当前用户、事件、publication、revision、collector、预算和数据权限允许；
6. 输入 Schema 可构造且不存在待澄清的关键身份；
7. 调用结果可以形成符合边界的 Evidence。

任一条件失败，运行时不得调用。模型只能提出能力需求和参数候选，不能自行选择 Registry
revision、恢复非 active 单元、绕过权限或直接调用未登记实现。

### 3.4 调用策略

每个 Capability 必须声明一种调用策略：

| 策略 | 含义 | 示例 |
|---|---|---|
| `required` | 满足意图后必须先调用，否则不能回答事实 | 事件身份解析 |
| `conditional` | 只有问题需要对应事实或派生时调用 | 趋势、峰值、ASN、路径 |
| `forbidden` | 当前边界没有合格能力，不得调用或猜测 | 原因、责任、真实用户影响 |

系统目标不是提高调用次数，而是让“应调用时调用、不应调用时不调用”可验证。

## 四、日常 CRUD SOP

### 4.1 Create

```text
开发者提交定义
→ 分配/校验稳定 ID 和初始 SemVer
→ 生成 proposed 版本
→ Schema、依赖、权限、边界和摘要检查
→ Oracle Ready
→ 独立 Reviewer
→ Certified
→ 明确激活后进入新快照
```

出口：新版本拥有完整回执；激活前不能进入任何执行计划。

### 4.2 Read

必须支持按以下条件查询：

- 稳定 ID、版本和状态；
- Capability 到 Execution Unit 的映射；
- 依赖者与被依赖者；
- 当前活动快照和历史快照；
- 生命周期历史、Reviewer、Oracle、回滚和 Tombstone；
- 某历史计划绑定的精确版本与摘要。

Read 永远不改变 Registry revision。

### 4.3 Update

```text
读取当前版本
→ 影响分析
→ 判断 Patch/Minor/Major
→ 创建新版本
→ 重跑受影响 Oracle、Reviewer 和安全检查
→ 生成新快照
→ 后续请求使用新版本，进行中请求继续旧快照
```

禁止只改实现文件而不更新 implementation digest；禁止在相同 SemVer 下改变合同或实现。

### 4.4 Deprecate、Retire 与 Delete

```text
active
→ deprecated：声明替代版本和迁移期限
→ retired：拒绝所有新计划，验证无活动引用
→ tombstoned：清理载荷，永久保留身份和历史摘要
```

若仍有活动计划、历史重放缺少快照、依赖未迁移或Evidence引用无法定位，不得进入下一状态。

### 4.5 Rollback

- 单单元回滚：影响分析证明依赖图和合同兼容时，生成新的活动快照；
- 整版回滚：任一跨单元兼容关系不确定时，切回完整历史快照；
- 回滚不得修改历史快照、历史Evidence或原版本内容；
- 回滚后新请求使用回滚快照，进行中的请求保持原快照并完成或受控终止。

## 五、P2-S0B 分阶段建设计划

### S0B-0：运行时接入合同冻结

入口：P2-S0A离线Registry候选及最终验收有效。

工作：

- 冻结 `SnapshotLoader`、`CapabilityResolver`、`PlanAdmission`、`Executor` 四个接口；
- 冻结一轮请求的快照固定时点和生命周期；
- 定义 `required/conditional/forbidden` 调用策略；
- 定义加载失败、摘要冲突、版本不存在、权限拒绝和预算不足语义；
- 建立运行时黄金问题与越界问题集。

出口：接口、输入输出、错误语义和边界可独立评审；不修改生产运行时。

到期验收：任何人能够仅凭合同判断一次计划为什么允许或拒绝某个调用。

### S0B-1：只读 Snapshot Loader

入口：S0B-0合同冻结。

工作：

- Runtime 在每轮请求开始时读取一个认证快照；
- 校验快照 ID、revision 和制品摘要；
- 将快照存入本轮不可变执行上下文；
- 快照加载失败时失败关闭，不回退到硬编码全量单元；
- Registry 更新只影响下一轮请求。

出口：同一请求的所有计划节点和Evidence绑定相同 `registry_snapshot_id`。

到期验收：并发更新、摘要篡改、缺失快照和旧快照重放用例全部通过，轮内快照漂移为0。

### S0B-2：Capability Resolver

入口：Snapshot Loader稳定。

工作：

- 保留开放的用户目标理解；
- 将可执行部分确定性映射到快照内Capability；
- 对需要澄清、能力不支持和证据不足分别输出明确 disposition；
- 从Capability映射到允许版本的Execution Unit；
- 删除对应路径中的散落硬编码选择，但保留受控回退开关。

出口：Resolver只产生能力需求和候选节点，不直接执行Tool。

到期验收：黄金问题Capability映射正确率100%；越界问题误映射为可执行的数量为0。

### S0B-3：真实 Plan Admission

入口：Resolver输出稳定。

工作：

- 将现有离线 `check-plan` 规则接入真实GroundingPlan/InvestigationPlan出口；
- 校验Capability、Execution Unit、版本、四类摘要、依赖和调用策略；
- 校验RRC25事件、publication、revision、collector、权限和预算；
- 在执行器之前拒绝非active、身份冲突、null关键身份和未知版本；
- 生成允许或拒绝的准入回执。

出口：只有准入成功节点可以到达Executor。

到期验收：非active或摘要篡改单元到达Executor的次数为0；所有拒绝都有稳定错误码。

### S0B-4：统一 Executor 与 Evidence

入口：Plan Admission失败关闭。

工作：

- 通过统一入口调用Tool和Operator；
- 实施超时、重试上限、费用预算、输出Schema和权限检查；
- 每次调用记录快照、单元版本、输入摘要、输出摘要、耗时和错误；
- 将合法结果写入Evidence Bundle；
- 对missing、null、unavailable和identity conflict保持不同语义；
- 禁止模型把调用失败改写成成功事实。

出口：事实回答可以追溯到精确调用和Registry快照。

到期验收：正常、缺失、null、不可用、身份冲突、超时和越界链路均有端到端证据。

### S0B-5：Shadow 与按需调用验收

入口：统一Executor可在隔离环境运行。

工作：

- 新旧规划路径并行，Shadow路径不影响用户回答和状态；
- 比较能力选择、调用次数、参数、Evidence和答案边界；
- 审计延迟、费用、失败率、无关调用和漏调用；
- 独立产品语义Reviewer复核高风险差异；
- 演练单单元和整版快照回滚。

出口：形成候选级Shadow、性能、费用、语义和回滚证据。

到期验收：

- 应调用黄金问题的调用覆盖率100%；
- 不应调用问题的工具调用率0%；
- 非active单元调用次数0；
- 快照身份和Evidence绑定完整率100%；
- 任何RRC25/RCA边界越界为一票否决。

### S0B-6：生产发布入口

入口：S0B-5同候选证据通过，且获得新的明确部署授权。

工作只在新的生产任务中定义，包括灰度比例、监控门、自动回滚、运行身份和发布窗口。

本计划不部署、不切换prod32、不修改生产配置或远程状态。未获得新授权时，S0B-6
始终保持未开始。

## 六、职责划分

| 角色 | 职责 | 不得执行 |
|---|---|---|
| Builder | Create/Update提案、实现和测试 | 自行认证并生产激活 |
| Product Semantic Reviewer | 独立推导语义真值并审核 | 修改候选实现 |
| Registry Governor | 状态迁移、影响和回执审核 | 绕过Oracle |
| Runtime Host | 读固定快照、准入、执行和记录 | 治理写入Registry |
| Release Operator | 激活、退役和回滚 | 修改已发布版本 |

小团队可以由同一自然人承担多个角色，但每次操作仍须使用不同的角色动作和回执；生产
激活不得由构建动作隐式完成。

## 七、最终验收门

只有同时满足下列条件，才能宣称“可控CRUD并可按需自动调用”：

1. 所有Registry写入100%通过统一治理入口；
2. 所有Update产生新版本，历史版本摘要漂移为0；
3. 所有Delete生成Tombstone，稳定ID复用次数为0；
4. 所有运行请求绑定唯一不可变快照，轮内漂移为0；
5. 非active或未认证单元到达Executor的次数为0；
6. 应调用黄金问题调用覆盖率100%，不应调用问题误调用率0%；
7. 所有调用均可定位到计划节点、版本、摘要、权限决策和Evidence；
8. 单单元与整版回滚均在同候选下成功演练；
9. 独立产品语义Reviewer无阻断；
10. 没有把Hook、单测、源码存在或HTTP 200单独写成产品验收。

## 八、非目标

- 不要求每一轮对话都调用Tool；
- 不允许模型自由搜索或调用Registry外实现；
- 不在本计划中锁定数据库、服务框架、消息系统或部署拓扑；
- 不把Registry建设扩大成通用企业软件目录；
- 不改变现有Tool/Operator产品语义；
- 不扩大RRC25控制平面证据为真实用户影响、原因、责任、恢复或RCA；
- 不在当前任务中接入生产运行时或执行部署。

## 九、执行优先级

```text
第一优先级：固定快照和真实Plan Admission
第二优先级：Capability Resolver和统一Executor
第三优先级：Evidence、Shadow、费用及性能审计
第四优先级：获得独立授权后的生产灰度
```

在前三项完成之前，不建设复杂在线Registry服务。当前规模优先使用Git真值、不可变JSON
制品和只读Runtime Adapter；未来容量和并发需求出现后，再替换存储或控制面实现。
