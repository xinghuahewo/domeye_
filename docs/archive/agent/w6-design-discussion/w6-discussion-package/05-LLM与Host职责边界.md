# LLM与Host职责边界

> 文档口径：本文同时说明当前合同、当前fixture实现和目标职责模型；三者不可互相替代。当前外部Sol/DS provider调用数为0。

## 1. 核心结论

LLM应深度参与语义理解、目标拆解、候选调查结构和答案表达，但不拥有执行、权限、事实发布和状态提交权。

最简洁的边界是：

```text
LLM提出：用户想调查什么、可能需要哪些能力、证据如何组织成解释
Host决定：系统允许执行什么、实际执行什么、哪些结果可以成为正式事实
```

## 2. 当前设计中的角色分工

| 角色 | 可以做 | 不可以做 |
|---|---|---|
| Sol Semantic Planner | 理解原始目标、拆子目标、识别实体和歧义、提出Capability意图 | 选择具体Tool版本、执行Tool、写状态、发布事实 |
| Host Grounding | 绑定身份、解析Capability、生成可执行Plan、检查权限和预算 | 偷偷新增业务语义或替Operator计算 |
| Tool | 读取一个冻结事实人口 | 排名、解释、跨人口联接、调用模型 |
| Operator | 对完整类型化输入执行一次确定性变换 | 读数据库、调用Tool、调用模型、写状态 |
| Sol Reference Generator | 基于已验证Evidence给出参考结构、必需事实和限制 | 改Plan、补未引用事件事实、把自己当真值 |
| DS Answer Generator | 把同一Evidence组织成面向用户的答案 | 新增事件事实、调用Tool、修改Evidence |
| Host Answer Validator | 验证身份、Evidence、完整性、边界和禁止结论 | 依赖LLM自我声明作为唯一门禁 |
| Transaction/CAS层 | 原子提交Answer、Turn和Investigation revision | 接受未通过Gate的中间结果 |

权威设计：[Model Role Contract](../country-outage-agent-p2-s1-w6-offline-certification-v3-work/contracts/agent/country-outage-p2-s1-execution-unit-design/model-role-contract.json)

## 3. LLM应该参与到什么程度

### 3.1 应当由LLM承担

- 保留用户原始问题，不被当前Tool集合提前裁剪；
- 抽取目标、实体、时间、ASN、前缀、路径和指代；
- 拆分可独立验证的子目标；
- 标记歧义、缺失条件和可能需要外部证据的目标；
- 在受控Capability视图下提出候选能力依赖；
- 对执行失败提出“需要重新Grounding”的建议；
- 在已提交EvidenceGraph上生成答案草案；
- 解释限制、未知和证据冲突；
- 生成不改变事实的叙事结构。

### 3.2 可以让LLM提出，但必须由Host编译和验证

- 候选DAG；
- 节点之间的逻辑依赖；
- 需要哪个抽象Capability；
- 是否需要澄清；
- 是否需要补充证据；
- 失败后希望重试、改查或缩小目标；
- 答案应包含哪些事实和限制。

这里的关键词是“候选”。模型输出不是执行合同。

### 3.3 必须保留在模型外

- incident/publication/revision/collector/cohort/window绑定；
- 具体unit ID、版本、合同摘要和实现摘要选择；
- Registry snapshot和Admission Receipt；
- 权限、预算、重试次数和截止时间；
- ResultSet完整性和分页闭包；
- 结构投影与population binding receipt；
- EvidenceGraph事实提交；
- Answer Gate；
- CAS、幂等、取消和崩溃恢复；
- 最终状态写入与制品发布。

## 4. 开放SemanticPlan与闭合执行

当前P1.1和P2设计最有价值的共同原则是：

> 语义理解开放，执行能力闭合。

SemanticPlan应允许保留：

- 当前系统不支持的目标；
- 用户尚未消解的歧义；
- 需要外部数据的目标；
- 当前证据不足的目标；
- 尚未找到执行单元的目标。

Host再把每个目标分类为：

- executable；
- needs_clarification；
- unsupported；
- insufficient_evidence；
- deferred；
- boundary_only。

如果先用Tool allowlist裁剪用户语言，系统会错误地把“不能执行”改写成“用户没有问”。

## 5. 是否向LLM提供Tool和Operator元数据

应该提供，但提供的是过滤后的Model Capability View，不是完整内部Registry。

### 5.1 模型适合看到

- capability_id和自然语言语义；
- read、transform、validate等能力类别；
- 语义输入和输出；
- 前置条件；
- 是否要求完整人口；
- empty、unknown、not_computable语义；
- 允许的组合关系；
- 禁止推断；
- 简短示例。

### 5.2 模型不应看到或不能控制

- handler路径和数据库端点；
- Registry管理接口；
- 凭据、密钥和签名机制；
- 具体实现摘要的选择权；
- 生命周期状态变更；
- 内部审批和回滚操作；
- 可绕过Gateway或Dispatcher的调用方法。

### 5.3 两阶段暴露更符合当前目标

```text
第一阶段：不给Tool菜单，完整保留用户语义
第二阶段：提供当前事件、权限和Profile过滤后的Capability视图，生成候选组合
最终阶段：Host编译具体执行单元和DAG
```

这避免两种极端：完全不给能力信息导致模型无法规划；把整个Registry交给模型导致它围绕实现细节思考并尝试越权。

## 6. Host是什么

Host不是另一个模型，也不应只是一个巨大类名。它是逻辑上的可信控制平面，可以由多个服务或模块组成。

合理的逻辑拆分是：

| Host组件 | 职责 |
|---|---|
| Identity Binder | 锁定事件、publication、revision、RRC25、cohort、window |
| Capability Resolver | 把语义Capability映射到当前snapshot中的active执行单元 |
| Plan Compiler | 把SemanticPlan和模板编译成Common Plan IR |
| Admission/Policy Engine | 校验身份、权限、预算、合同、生命周期和边界 |
| Dispatcher/Scheduler | 静态调用handler，管理依赖、并发、取消和重试 |
| ResultSet Manager | 分页、去重、完整性校验和内容寻址冻结 |
| Structural Adapter Runtime | 做零业务变换的类型投影和人口绑定 |
| Evidence Committer | 构造和提交EvidenceGraph |
| Answer Gate | 检查答案与事实、Evidence和边界等价 |
| Transaction Manager | 管理revision、digest、幂等和CAS |

## 7. Host设计合理，但不能成为God Host

Host拥有程序性权力，但不拥有任意创造事实的权力。

合理：

- Host选择已经认证的执行单元；
- Host验证和传递结构；
- Host拒绝越权或不完整输入；
- Host提交经过验证的事实；
- Host控制原子状态变化。

不合理：

- Host Adapter里写业务排名或时间推断；
- Host看到字段后直接拼出未登记结论；
- Host自动把缺失值补成0；
- Host用通用文本过滤代替题目特定Evidence Gate；
- Host因为是可信进程就绕过Registry和receipts；
- 所有职责落在一个不可测试、不可替换的超大模块中。

## 8. 当前实现与目标角色模型的差距

当前合同中的角色边界比较清晰，但实际模型路径仍是fixture replay：

- SemanticPlan是脚本化输出；
- Grounding命中冻结fixture recipe；
- Sol Reference和DS Answer是本地回放；
- 外部provider没有参与；
- 正式Planner只覆盖少量执行单元；
- 模型尚未在28题上证明稳定的开放规划和受控回答能力。

另一个当前差距是Gate身份尚未完全收敛。设计合同中的GATE-01～05用于身份、Evidence、完整性、控制面边界和禁止结论；W5控制运行时却把相同编号用于Plan准入、身份、Registry、Evidence闭包和owner授权。模型回答合同、Planning端口与实际控制handler必须引用同一版本化Gate语义，否则“全部Gate通过”仍不能唯一说明通过了哪些检查。

因此，当前可以说“模型权限边界合同已经设计并被fixture验证”，不能说“真实模型已经完成该角色”。

## 9. 与工业平台的对应关系

工业平台普遍存在相同职责，只是名称不同：

- OpenAI：Agent与Runner，Runner执行Tool、管理循环、审批和状态；
- Anthropic：Brain、Hands、Session分离；
- Google：Workflow Graph控制路径，LLM位于认知节点；
- Microsoft：Agent、Harness、Workflow三层；
- AWS：Harness、Runtime、Gateway、Identity、Policy拆分。

它们一般解决循环、Tool传输、沙箱、权限、Session和Trace，不会替业务系统自动提供publication绑定、ResultSet完整性和BGP EvidenceGraph。这部分仍属于领域Host。

参考：

- [OpenAI Agents SDK Runner](https://openai.github.io/openai-agents-python/running_agents/)
- [Anthropic Managed Agents](https://www.anthropic.com/engineering/managed-agents)
- [Google ADK 2.0 Workflow](https://developers.googleblog.com/en/why-we-built-adk-20/)
- [Microsoft Agent Framework](https://learn.microsoft.com/en-us/agent-framework/overview/)
- [Amazon Bedrock AgentCore](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/)

## 10. 讨论时应坚持的判断句

1. 模型会规划，不等于模型拥有执行权限；
2. 模型能生成合法JSON，不等于它能签发Plan；
3. Tool调用成功，不等于事实可以发布；
4. Teacher回答质量高，不等于Teacher是真值Oracle；
5. LLM Reviewer不能成为唯一Answer Gate；
6. Host可信，不等于Host可以隐藏业务逻辑；
7. Trace记录调用过程，不等于EvidenceGraph证明事实关系。
