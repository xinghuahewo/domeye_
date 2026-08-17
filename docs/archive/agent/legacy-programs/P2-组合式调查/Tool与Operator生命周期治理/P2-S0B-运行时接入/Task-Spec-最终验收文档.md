# P2-S0B Tool/Operator 治理快照运行时接入 Task Spec

版本：`country-outage-agent-p2-s0b-governed-auto-invocation-v1`

状态：合同冻结；本轮实现与本地同候选验收；禁止生产部署

上游真值：

- `../Task-Spec-最终验收文档.md`：生命周期治理总合同；
- `../Plan-可控CRUD与按需自动调用.md`：从 CRUD 到按需调用的产品路线；
- `contracts/agent/country-outage-p2-s0a-lifecycle/registry-snapshot.json`：已验收离线治理基线；
- P1 Capability Catalog、Typed Tool/Operator Contract 与 OP-04 合同：既有产品语义真值。

## 一、最终效果

对开发者，日常动作收敛为：

```text
通过治理命令 Create/Update/Deprecate/Retire
→ 自动完成版本、摘要、依赖、兼容性和 Oracle 校验
→ Reviewer 与认证通过
→ 生成一个新的不可变活动快照
→ 后续新请求自动读取新快照
```

开发者不需要在多个 Planner、Executor 和配置文件中重复登记同一个版本，也不能通过直接改
JSON、覆盖旧版本或修改运行中请求来绕过治理。

对最终用户：

- 问题需要当前已登记事实时，Agent 自动调用对应 Tool/Operator；
- 问题不需要某能力时不调用；
- 原因、责任、真实用户影响、恢复和跨事件 RCA 等越界问题不得因 Registry 存在而变成可执行；
- 每次答案可追溯到同一个 candidate、Registry Snapshot、revision、单元版本和三类摘要；
- 更新只影响下一轮；同一轮不混用新旧版本；回滚后新轮使用回滚快照。

“激活后自动调用”不是“任意新源码自动执行”。一个新单元还必须有经过审查的 Host Handler。
Registry 决定允许哪个版本进入计划；Host Handler 决定运行时有哪些受控实现可被调用。缺少任一侧都
必须在执行前拒绝。

## 二、入口、出口与边界

### 2.1 入口

本轮接收：

1. 已验证的 `UserGoalPlan`；
2. 由现有确定性 Grounder 生成的 `GroundingPlan`；
3. 已绑定的 RRC25 `country_outage` incident、publication 与 revision；
4. 一个 P2-S0B 内容寻址不可变 Registry Snapshot；
5. 当前只读权限与既有执行预算。

### 2.2 出口

成功出口必须同时产生：

- 目标到 Capability/Execution Unit 的解析结果；
- 执行前 Registry Admission 回执；
- 每个节点的版本化 Registry Binding；
- Tool/Operator 执行回执和 Evidence；
- 回答级 runtime identity。

拒绝出口必须有稳定错误码，且被拒绝节点的业务 Handler 调用次数为零。

### 2.3 本轮非目标

- 不部署，不切换 prod32，不修改生产配置、远程状态、进程、端口或发布指针；
- 不建设在线 Registry 数据库、写服务、管理 UI、HA 控制面或生产热更新；
- Runtime 不写 Registry，也不改变 P2-S0A 历史快照；
- 不重写开放式意图理解，不建设 P2 InvestigationPlan 或 RCA Runtime；
- 不改变 6 个 Tool、3 个基础 Operator 与 OP-04 的数据读取和产品语义；
- 不把本地 Shadow、HTTP 200、源码存在、Hook 或单测单独称为生产能力。

## 三、当前能力分层

| 能力 | 当前结论 | 本轮动作 |
|---|---|---|
| P2-S0A 双 Registry、CRUD、状态机、认证、退役、Tombstone、回滚 | 已有 | 只读复用，禁止修改其合同与快照 |
| P1 目标理解、Grounding、6 Tool 与 4 Operator Handler | 已有 | 保持语义和底层行为 |
| 真实 Runtime 绑定 Registry Snapshot | 待建设 | 本轮实现 |
| active/版本/摘要/依赖/Handler 执行前准入 | 离线部分具备 | 本轮接入真实 GroundingPlan 出口 |
| 回执绑定 candidate/snapshot/version/digest | 部分具备 | 本轮补齐 |
| 生产活动快照指针、灰度、监控与发布回滚 | 待建设 | 后续独立授权 |

## 四、冻结运行时合同

### 4.1 Snapshot Loader

- 每个语义轮只加载一次；
- 只接受普通文件，拒绝符号链接、超限文件、无效 JSON 和关键身份 null；
- 校验 `schema_version`、candidate、revision、scope、非部署标志和内容寻址摘要；
- 摘要采用 `p2-s0b-canonical-json-v1`：对象键按 Unicode 码点排序，JSON 数字统一为规范
  科学计数法，再计算 SHA-256；
- 加载失败时失败关闭，不回退到未受 Registry 约束的硬编码全量能力；
- 轮中使用深冻结副本；磁盘更新只影响下一轮。

### 4.2 Capability Resolver

现有开放式模型只产生用户目标；现有确定性 Grounder 产生受允许列表约束的节点。P2-S0B 在
Grounder 之后独立解析每个节点的：

```text
capability_id
→ 唯一 active Capability version
→ 双向映射的唯一 active Execution Unit version
→ contract / implementation / semantic digest
→ 已登记 Host Handler
```

模型不得指定 candidate、快照、版本或摘要。

### 4.3 调用策略

- `required`：只要本轮存在 supported/partial 事实目标，整个计划必须包含 `TOOL-01`
  身份预检；缺失返回 `required_call_missing`；
- `conditional`：业务 Tool/Operator 只有在目标生成相应节点时才调用；
- `forbidden`：unsupported、pending clarification 和明确越界目标不生成业务节点。

这里的“按需”指确定性计划需要，而不是模型觉得可能有用。

### 4.4 Plan Admission

节点进入 Executor 前必须通过：

1. Capability 与 Execution Unit 都存在且各有唯一 `active` 版本；
2. Capability→Unit 引用和 Unit→Capability 反向关系一致；
3. 两侧 contract、implementation 与 semantic digest 完全一致；
4. 单元属于本 Runtime 已登记 Handler 集；
5. OP 依赖节点存在，版本和关系满足合同；
6. event_type=`country_outage`、collector=`rrc25`、权限为只读允许值；
7. Grounding decision 的可执行性与节点数量一致；
8. 所有关键 Registry/事件身份非空且快照完整。

任何一项失败，整轮事实执行失败关闭，不发布半答案，不提交半状态。

### 4.5 Executor 与 Evidence

Executor 只接受带 `admission_status=admitted` 的节点。每个节点回执至少记录：

- candidate ID、Registry Snapshot ID 与 revision；
- Capability ID/version/contract digest；
- Execution Unit ID/version；
- unit contract/implementation/semantic digest；
- 输入、输出或失败、上游 Evidence 与派生 Evidence；
- 事件、publication、revision、collector 绑定。

Executor 不根据 Registry 内容动态加载任意模块，不执行 Registry 中的路径、命令或代码字符串。

## 五、错误与失败语义

以下错误均发生在业务 Handler 前：

| 错误码 | 含义 |
|---|---|
| `registry_snapshot_missing/unsafe/invalid` | 快照缺失、不安全或结构无效 |
| `registry_snapshot_digest_mismatch` | 内容与内容寻址身份不一致 |
| `capability_not_active` | 没有唯一 active Capability 版本 |
| `execution_unit_not_active` | 没有唯一 active Unit 版本 |
| `execution_handler_missing` | Registry active 但 Host 未登记 Handler |
| `registry_permission_denied` | 单元权限不允许当前只读调用 |
| `registry_boundary_violation` | 事件或 collector 越界 |
| `capability_unit_digest_mismatch` | 双向映射或摘要冲突 |
| `execution_dependency_missing` | Operator 上游节点缺失或版本不符 |
| `required_call_missing` | 可执行事实计划缺少身份预检 |
| `registry_plan_invalid` | decision、goal 与节点结构不闭合 |
| `registry_admission_missing` | Executor 收到未准入节点 |

错误码稳定；人类信息可扩充但不能改变错误分类。

## 六、安全、身份与 RCA 边界

- 只有 Host 可选择快照文件；用户和模型输入不得提供路径；
- Runtime 只读，不提供 Create/Update/Transition/Delete 权限；
- Registry 中的实现路径只用于摘要证明，不用于动态执行；
- RRC25 publication 身份先由现有 resolver 验证，P2-S0B 不创造或修复身份；
- `data_through`、末值改善或趋势标签不证明恢复；
- AS_PATH 相邻不证明依赖、传播因果、责任或技术原因；
- IPv4 unique address 与 IPv6 /48 等价量不得相加为全国绝对总量；
- Control-plane Evidence 不得升级为真实用户影响、全国影响或 RCA。

## 七、迁移验收

本轮候选必须保持：

- 18 个 Capability；
- 6 个 Tool：`TOOL-01..06`；
- 3 个基础 Operator：`OP-01..03`；
- 独立 OP-04：`event-window-trend@1.2.0`，依赖 `TOOL-03@1.0.0`；
- 所有原有 capability version、unit version、contract digest 与 semantic digest 不变；
- implementation digest 只按当前同候选实现文件重新计算；
- 所有产品语义差异为零，生产行为未切换。

## 八、Oracle 与量化门

Oracle 至少覆盖：required、conditional、forbidden、missing、null、wrong identity、unavailable、
inactive、tamper、dependency、snapshot drift、handler missing、Evidence binding、rollback。

最终本地候选门：

- 应调用用例覆盖率 = 100%；
- 不应调用业务单元的误调用率 = 0；
- 非 active 单元到达 Handler 次数 = 0；
- 同轮快照漂移次数 = 0；
- 执行回执 Registry 身份完整率 = 100%；
- 现有 P1 定向回归通过率 = 100%；
- 产品语义 Reviewer blocking count = 0；
- 生产部署次数、prod32 切换次数、远程写次数均 = 0。

一票否决：任何被拒绝单元实际执行、摘要篡改仍可调用、同轮混版、越界问题产生事实节点、
Reviewer 非独立、P1 产品语义变化、生产状态被修改。

## 九、回滚与后续生产入口

本轮代码未部署，因此代码回滚只需恢复本分支；P2-S0A 基线不受影响。运行时快照回滚语义为：

- 新轮读取上一个完整认证快照；
- 已开始轮继续固定原快照或由上层原子取消；
- 禁止把不同快照的 Capability/Unit 拼成混合版；
- 历史计划、Evidence、回执和 Tombstone 不修改。

生产接入必须另立任务并重新完成权限、受控流量、资源与调用量、性能、可观测性、活动指针原子切换、真实
进程身份、浏览器/API/Evidence 同候选验收和生产回滚演练。本 Task Spec 通过不等于生产可用。

## 十、交付清单

- 本 Task Spec 与执行 Plan；
- Runtime 合同、准入 Schema、Shadow Oracle；
- 只读 Snapshot Loader、Resolver/Admission、Executor 回执绑定；
- 内容寻址 S0B candidate 与不可变快照；
- 正常、边界、篡改、迁移、漂移和回滚测试；
- 独立产品语义 Reviewer；
- 分阶段 Alignment Hook 与负例测试；
- 同候选本地验收清单、postflight 结果和非部署声明。
