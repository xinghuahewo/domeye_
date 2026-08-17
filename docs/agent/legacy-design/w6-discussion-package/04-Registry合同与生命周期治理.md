# Registry合同与生命周期治理

> 文档口径：基于2026-08-14本地W6 v3工作树。Registry snapshot、离线active或本地执行准入不应越级解释为当前生产激活状态。

## 1. 为什么需要两个Registry视角

Capability和Execution Unit不是同一概念。

| 对象 | 回答的问题 | 示例 |
|---|---|---|
| Capability Registry | 系统具备什么受控能力 | 读取固定cohort成员、计算时间关系 |
| Execution Unit Registry | 哪个具体版本实现了能力 | TOOL-07@某版本、OP-29@某版本 |

一个Capability可以由一个或多个执行单元实现；一个执行单元也可能服务多个受控Capability。模型更适合看到Capability视图，Host执行时必须绑定Execution Unit视图。

## 2. Registry条目的核心字段

统一Registry条目至少需要：

- capability ID；
- unit ID和kind；
- 版本；
- 输入输出Schema；
- 语义摘要、合同摘要、实现摘要；
- 权限；
- 身份、人口和数据依赖；
- null、unknown、error、completeness语义；
- 禁止用途与禁止结论；
- Oracle和测试证据；
- 生命周期状态；
- replacement、migration和tombstone信息；
- 当前snapshot与调用时Admission Receipt。

## 3. 生命周期目标

设计上的生命周期是：

```text
discovered
  → proposed
  → oracle_ready
  → certified
  → active
  → deprecated
  → retired
  → tombstoned
```

重要规则：

1. 非active执行单元不得进入正式Plan；
2. 不兼容语义变更发布新major版本，不能覆盖旧语义；
3. deprecated和retired必须保留历史调查可重放身份；
4. tombstone禁止ID复用；
5. Registry更新本身不自动授权运行时调用；
6. 每次调查绑定一个不可变snapshot，不在执行中漂移。

## 4. 当前P1 Registry状态

P2-S0A/S0B建立的Registry实际登记的是P1执行单元：

- TOOL-01～06；
- OP-01～04；
- 对应CAP-001～014、CAP-016～018和CAP-TREND-001。

这些条目具有版本、active状态、合同/语义/实现摘要和生命周期历史。P1运行时通过 `P2RegistryAdmissionReceipt` 在GroundingPlan执行前检查绑定。

关键代码：[P1 Registry Runtime](../country-outage-agent-p2-s1-w6-offline-certification-v3-work/agent-sidecar/src/chat/p2-registry-runtime.ts)

这解释了为什么文件名包含P2，但执行单元仍是P1：P2-S0A/S0B是面向未来P2的治理建设阶段，不是P2产品执行器。

## 5. 当前P2 Registry状态

P2-S1先建立proposal和逐Wave不可调用绑定，随后W5签发本地隔离执行准入。

W5 revision 8本地执行准入包含：

- 6个P2 Tool：TOOL-07～12；
- 34个P2 Operator：OP-05～33、OP-35～39；
- 11个Host控制、渲染和交付单元。

明确拒绝：

- TOOL-13；
- OP-34；
- PLAN-CAP-02。

该准入状态是 `admitted_local_isolated_execution`，并明确 `production_deployed=false`。

权威证据：[W5 Execution Admission](../country-outage-agent-p2-s1-w6-offline-certification-v3-work/contracts/agent/country-outage-p2-s1-implementation/wave-evidence/W5.json)

## 6. “Registry统一”目前统一到了哪里

已经统一或接近统一的是：

- unit ID和kind；
- 版本化合同；
- 语义、合同和实现摘要；
- snapshot与Admission Receipt；
- deferred单元拒绝；
- 静态handler绑定；
- Oracle、攻击和测试证据思想。

尚未统一的是：

- P1和P2进入同一个Dispatcher；
- P1/P2共享一个Plan IR；
- P1输出和P2 ResultSet共享一个Typed Artifact协议；
- P1 Evidence Bundle和P2 EvidenceGraph的正式桥；
- 全部Capability到DAG模板的编译目录；
- 同一调查内跨P1/P2执行单元的结构绑定。

因此当前状态可以概括为：

> Registry统一了“有什么和能否准入”的大部分治理概念，但尚未统一“如何在一个运行内核中执行和传递制品”。

### 6.1 Gate编号存在语义漂移

交叉核对还发现，设计Capability目录与W5控制运行时对同一Gate编号的含义并不完全一致：

| Gate | 设计Capability目录 | W5控制运行时handler |
|---|---|---|
| GATE-01 | 验证调查身份 | validate_plan_admission |
| GATE-02 | 验证Evidence引用 | validate_identity_gate |
| GATE-03 | 验证结果完整性 | validate_registry_gate |
| GATE-04 | 验证控制面边界 | validate_evidence_references |
| GATE-05 | 验证禁止结论 | validate_authorization_gate |

此外，Planning Grounding端口仍把GATE-01～03映射为identity、evidence refs和result completeness。说明当前同一个ID在设计、规划和W5运行时三个上下文中承担了不同语义。

这不应只当作命名问题：Gate ID会进入Plan、Registry、receipt和Answer validation。如果同一ID不能唯一指向一个版本化合同，跨层回执就可能“编号相同但证明的事情不同”。因此它属于统一Registry和Common Plan IR必须显式处理的合同收敛问题。

## 7. Model Capability View

不应把完整Registry直接塞给LLM。模型适合看到一个只读、按问题和Profile过滤的Capability视图。

建议模型可见字段：

- capability_id；
- 用户语义说明；
- kind：read、transform、validate等；
- 语义输入与输出；
- 前置条件；
- completeness、empty、unknown语义；
- 组合限制；
- 禁止推断；
- 少量正确和错误示例。

Host保留字段：

- 具体unit版本选择；
- implementation和endpoint；
- 内部权限细节；
- Registry snapshot内部结构；
- receipt密钥和签发逻辑；
- 生命周期变更操作；
- handler地址和运行时拓扑。

模型看到Capability不等于模型获得执行权限。模型可以提出能力意图，Host再完成unit解析、版本绑定、参数绑定和准入。

## 8. Registry、Dispatcher和Host的关系

```text
Registry：定义哪些执行单元在某snapshot中存在、版本是什么、合同是什么
Admission：判断某个Plan在当前身份、权限和Profile下能否使用这些单元
Dispatcher：只调用Admission绑定的静态handler
Host：编译Plan、组织输入、冻结制品、提交Evidence和状态
```

Registry不是Dispatcher，Dispatcher不是Planner，Host也不能因为自己是可信层就绕过Registry。

## 9. 当前讨论需要区分的三个状态

| 状态 | 正确含义 |
|---|---|
| 设计Catalog中的runtime_ready=false | 当时的设计合同没有宣称运行时已实现 |
| W1～W4 handler/test evidence | 后续实现了对应原子单元并形成离线证据 |
| W5 local execution admission | 对同候选静态handler签发本地隔离执行准入，不等于生产active |

如果不区分这些时间和层次，会同时出现“Catalog说没实现”和“Python里明明有代码”的表面矛盾。

## 10. 权威入口

- [Capability Registry](../country-outage-agent-p2-s1-w6-offline-certification-v3-work/contracts/agent/country-outage-p2-s0a-lifecycle/capability-registry.json)
- [Execution Unit Registry](../country-outage-agent-p2-s1-w6-offline-certification-v3-work/contracts/agent/country-outage-p2-s0a-lifecycle/execution-unit-registry.json)
- [Question Capability Map](../country-outage-agent-p2-s1-w6-offline-certification-v3-work/contracts/agent/country-outage-p2-s1-execution-unit-design/question-capability-map.json)
- [W5本地执行准入](../country-outage-agent-p2-s1-w6-offline-certification-v3-work/contracts/agent/country-outage-p2-s1-implementation/wave-evidence/W5.json)
- [W5控制运行时Schema](../country-outage-agent-p2-s1-w6-offline-certification-v3-work/contracts/agent/country-outage-p2-s1-implementation/w5-control-runtime.schema.json)
