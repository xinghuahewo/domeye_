# P2-S1 国家中断组合式调查助手实现工程 Task Spec

文档版本：`country-outage-agent-p2-s1-implementation-target-v1`  
文档状态：实现工程目标基线，待 W0 开始实施  
实现范围：单一国家事件、单一 RRC25 publication、只读调查、P2 v1  
最终产品名称：RRC25 国家中断组合式调查助手  
设计候选：`country-outage-p2-s1-s1d-6-04135cee55b39ce5d574f7e4`

## 一、文档定位

本文定义 P2-S1 实现工程完成后必须实际产生的用户效果、数据效果、运行效果、工程效果与
验收证据。它不是上一阶段的设计合同复述，也不是“创建若干接口和类”即可完成的任务清单。

实现工程只有在可运行代码、受信数据读取、Tool/Operator、组合运行时、接口、页面交互、
离线认证和运行证据共同闭合后，才可声明 `implementation_accepted`。

本文通过不等于生产部署。生产发布、流量切换和线上模型晋级必须另立发布任务。

## 二、冻结设计输入

实现不得重新解释或静默改写以下冻结设计输入：

| 字段 | 冻结值 |
|---|---|
| design candidate | `country-outage-p2-s1-s1d-6-04135cee55b39ce5d574f7e4` |
| candidate 文件 SHA-256 | `dfc764ff34ca2d79f4580f3eb4f9792c4a10ed907c485182f877a9079b31f957` |
| candidate content digest | `d0256d9f1246191df2d48432655ea384acb2e5a6844b15a78f80e4c9f5e55e74` |
| acceptance manifest 文件 SHA-256 | `d5e5a6e31d600f7437c612792396a27d3418d82576dcbd6f871dd87b7c9abdbe` |
| S1D-6 receipt digest | `e9bebac7d23ca78f4a942e4b7d21c9203d3de8420dcfc1db4a87627e91fea827` |
| final receipt digest | `3ef7e71ddd9bcaf2ed0fb762cc0b3d217c6f4b8047f2eacbbaaba7712bfa0619` |
| collector | `rrc25` |
| publication 数量 | 单一 publication/revision |

若实施发现设计合同无法落地，必须停止对应波次，形成设计变更提案并重新进行影响审查；不得在
实现代码中悄悄扩大 Tool 人口、合并 Operator 变换或放宽证据边界。

## 三、一句话实现目标

把已经通过设计验收的 28 题调查能力实现为一个真实可运行、只读、可取消、可局部失败、可重跑、
可预览和导出、证据可追溯的组合调查系统；用户提出一个复杂国家中断调查目标后，系统能够生成
受控 InvestigationPlan，执行原子 Tool 与确定性 Operator，建立 Evidence Graph，并由
Sol→Host Grounding/Validation→DS 流程交付有边界的回答。

## 四、必须达成的最终效果

### 4.1 用户调查效果

实现完成后，用户必须能够：

1. 用一句自然语言创建一个独立调查任务，而不是只触发一段同步长回答；
2. 在执行前看到调查目标、步骤、依赖、当前状态和预计执行范围；
3. 看到时间线、IPv4/IPv6、固定 cohort、新前缀、ASN、前缀和路径证据被组合为一份调查结果；
4. 从峰值或低点下钻到当时的 ASN、前缀和路径证据；
5. 查看不同指标是同槽、先后、部分一致、冲突、缺失还是不可比较；
6. 点击事实或派生事实，查看 publication、时间身份、Tool/Operator 回执和原始 Evidence；
7. 围绕同一 Investigation 继续追问，而不是依赖上一句话的临时槽位；
8. 取消尚未开始或正在执行的可取消步骤，并保留已经提交的有效证据；
9. 重新运行失败或过期的单一步骤，不重复执行仍有效的无关步骤；
10. 对大结果先看稳定预览，再请求完整分页或导出；
11. 查看每一步的状态、耗时、模型用量、费用、限制和失败原因；
12. 明确知道哪些问题只能部分回答、哪些需要外部证据、哪些延期到 P2.1。

### 4.2 BGP 事实效果

系统必须实际做到：

- 所有事实绑定同一 incident、publication、revision、cohort、collector、窗口和 data-through；
- AS 默认按 `peak_invisible_direction_count DESC`、
  `peak_complete_prefix_count DESC`、`asn ASC` 排序；
- 排名同时区分 `severity_rank_global` 与 `result_position`，并列严重度不得被 ASN 打破；
- 能从 AS 下钻到固定前缀、状态点、首次/最后出现和连续异常区间；
- 能从时间点下钻到前缀状态、观察方向和真实 RouteState；
- 能查询窗口级完整路径关系人口并保留 typed AS_PATH segment；
- `known_origin` 必须是观测 origin，并与规范路径尾端语义闭合；
- `OP-19` 只能输出 RRC25 observed downstream origin set，不得命名为 customer cone；
- `complete invisible` 只能解释为固定人口与预期方向下的控制面分类；
- 世界知识只允许解释概念、提出验证路线和补充限制，不得成为本事件事实。

### 4.3 组合运行效果

系统必须有独立的 Investigation 状态，而不仅是对话状态：

```text
用户目标
  → GroundingPlan
  → InvestigationPlan admission
  → 原子 Tool 读取
  → 确定性 Operator 派生
  → ResultSet 冻结
  → Evidence Graph 提交
  → Sol TeacherReference
  → Host Evidence/Boundary Validation
  → DS Student 回答
  → 发布与调查状态原子提交
```

每个节点必须具有稳定身份、输入摘要、输出摘要、状态、Evidence、限制、重试策略和费用/时间回执。
局部失败不得破坏已经提交的其他分支；取消不得制造伪成功、伪空集或伪 Evidence。

### 4.4 开发者与原子性效果

实现者必须获得并保持以下确定性边界：

- 一个 Tool 只读取一种冻结事实人口，不排序、不聚合、不推理、不做动态 fan-out；
- 一个 Operator 只执行一种登记的确定性变换，不读外部数据、不调用模型、不发布回答；
- 组合、依赖、并发、条件分支和重试只存在于 InvestigationPlan/Host；
- Tool/Operator 的运行时输入输出必须通过各自冻结 JSON Schema；
- 每个新执行单元必须通过 `atomic_split_test`，并能证明没有隐藏第二种业务变换；
- 同一输入、同一 Profile、同一 Registry snapshot 必须得到字节级稳定的结构化结果摘要；
- 任何接口便利层不得把多个 Tool/Operator 包装成一个新的未登记复合执行单元。

### 4.5 运行治理效果

系统必须能够证明：

- 调查只能使用准入状态为 active 的执行单元及固定版本；
- 运行回执绑定 Registry snapshot、实现摘要、语义摘要、参数 Profile 和输入人口；
- 预览不冒充完整结果，样本不冒充总体，分页可稳定重放并完成总数对账；
- 所有成功和失败的模型尝试都记录 tokens、费用、延迟、结果和重试状态；
- 金额策略为 unlimited 只表示金额不是拒绝门，不表示可以无限调用或不记账；
- 默认必须先执行 Sol，TeacherReference 经 Host 验证后才能作为 DS 的受控参考；
- Teacher 不是事实真值，publication 与确定性 Operator 证据优先；
- DS 首答未通过时最多接受一次结构化反馈和一次成功修订；
- 没有可信 Student artifact、同绑定摘要和 Host 硬门指标时，不得晋级模型；
- implementation acceptance、runtime promotion 与 production deployment 是三个独立状态。

## 五、产品范围

### 5.1 P2 v1 实现人口

- 问题人口：冻结的 28 个问题 ID；
- 新 Tool：`TOOL-07` 至 `TOOL-12`；
- 新 Operator：`OP-05` 至 `OP-33`、`OP-35` 至 `OP-39`；
- Plan capability：`PLAN-CAP-01`；
- 控制与交付：`GATE-01..05`、`BOUNDARY-01`、`RENDERER-01..03`、`DELIVERY-01`；
- 既有依赖：`TOOL-01..06`、`OP-01..04`；
- 单一 RRC25 publication、只读执行。

### 5.2 P2.1 延期人口

以下能力不得进入 P2 v1 实现或以其他名字隐式出现：

- `PLAN-CAP-02 expand_member_scoped_subplan`；
- `TOOL-13 query_route_events`；
- `OP-34 route_change_classifier`；
- Q20 的逐路径位置与批量邻接动态展开；
- Q23 的逐活动路径位置与跨并列峰值自动展开；
- Q26 的逐 origin 再查询和逐 origin 分别统计。

### 5.3 固定非目标

- 不证明 customer cone、customer/provider/peer 商业关系；
- 不证明全国中断、真实用户数量、流量、DNS、HTTP 或服务影响；
- 不判断原因、责任、政府行为、故障传播或恢复；
- 不接入外部数据或第二 collector；
- 不做跨国家、跨事件比较；
- 不在实现任务中直接执行生产部署。

## 六、实现架构边界

### 6.1 数据与事实读取层

实现必须从冻结、可审计的数据视图读取，必要的数据视图、索引、checkpoint 和物化回执必须先在
W0 准备并验证。Tool 不得在查询时临时回放整个事件或把 LLM 过滤作为数据读取。

### 6.2 确定性变换层

Operator 运行时必须直接消费冻结 Tool/ResultSet 或上游 Operator 的类型化输出。Host 只做身份、
Schema、摘要、引用和字段投影闭包，不重算路径、时间、集合或一致性业务语义。

### 6.3 调查编排层

InvestigationPlan 负责组合原子执行单元。Plan admission 必须在执行前完成 Registry、权限、预算、
依赖、输入、输出 Schema 和 Evidence 闭包检查。P2 v1 禁止动态成员 fan-out。

### 6.4 交付层

页面和 API 只能消费已提交的 Investigation、ResultSet、Evidence Graph 和回答制品。Renderer 只做
表现投影，不补算业务事实。Delivery 只能导出冻结人口，不能通过导出过程改变人口或排序。

## 七、阶段实现目标

### W0：Source 与治理基础

达成效果：后续 Tool 能从受信、冻结、可重放的数据人口读取；未准入单元无法执行。

必须实现数据 Schema/View、物化索引、Registry admission、可信回执存储、身份与摘要解析、权限和
审计基础。W0 不交付用户调查回答。

### W1：ASN、前缀与状态时间

达成效果：用户可以回答“哪些 AS/前缀发生了什么、何时首次/最后发生、持续多久、指定时点成员
是什么”，并获得完整或明确受限的 ResultSet。

### W2：窗口路径与集合

达成效果：用户可以查询包含某 AS 的窗口级完整路径人口、路径结构、observed downstream origin、
关联前缀和方向集合，并获得独立计数、交集和覆盖关系。

### W3：区间与固定 cohort 集合闭合

达成效果：用户可以确定两个 AS 异常区间是否真实重叠，并在同一 population contract 下比较固定
cohort 前缀集合。

### W4：Path-at-time 与 VP 关系

达成效果：用户从峰值/低点下钻时，能够查看该精确时点实际活动的 RouteState、路径包含、观察方向
和 VP 一致性，而不是使用窗口样本冒充时点路径。

### W5：组合调查产品

达成效果：复杂目标能够转成可见的 InvestigationPlan，执行 W1–W4 能力，提交 ResultSet 与
Evidence Graph，支持局部失败、取消、重跑、预览、导出和多轮追问，并走完 Sol→Host→DS 回答链。

### W6：离线认证与实现验收

达成效果：冻结 28 题在同一 GroundingPlan、Evidence、Registry snapshot 和 publication 上完成
回放；Host 硬门、边界、费用、延迟、取消、重跑、完整导出和攻击测试均产生可信证据。

## 八、接口与页面应达到的效果

实现至少需要提供以下产品入口，具体路由名称可在 W5 冻结，但语义不得改变：

- 创建 Investigation；
- 读取计划、节点状态和依赖；
- 取消节点或调查；
- 重跑指定节点并形成新 revision；
- 读取时间线和关键发现；
- 查询/分页 ResultSet；
- 读取 Evidence Graph 节点与边；
- 请求完整导出并读取导出状态；
- 提交同一调查上下文中的追问；
- 查看 Tool、Operator、模型、费用和延迟回执。

页面必须区分 `完整结果`、`稳定预览`、`有限样本`、`来源不完整`、`数据缺失`、`不可比较`、
`P2.1 延期` 和 `需要外部证据`。

## 九、最终验收旅程

### I01 调查创建与身份闭合

一句复杂调查目标创建独立 Investigation；Plan、所有运行节点和最终回答绑定同一事件身份与
Registry snapshot。

### I02 计划可见与受控执行

用户在执行前看到计划步骤；未准入、Schema 不匹配或越权单元在执行前被拒绝。

### I03 AS 严重性排序

使用冻结三键输出完整排名；并列严重度共享 competition rank，`result_position` 连续且确定。

### I04 AS→前缀下钻

指定 AS 返回固定前缀成员、状态轨、首次/最后异常和连续区间；数量与完整人口对账。

### I05 时间点前缀下钻

指定极值时间点返回 partial/complete 前缀成员，而不是只返回聚合数字。

### I06 新前缀

返回冻结 cohort 后首次被 RRC25 观察到的前缀及状态，同时拒绝“新分配/全球首次宣告”结论。

### I07 路径完整人口

查询包含某 AS 的窗口路径，返回稳定预览、总数和完整导出；样本不得冒充总体。

### I08 路径结构

对具体路径确定包含、位置和直接邻接，保留 AS_SET/confederation/unknown 等不可线性化状态。

### I09 Observed downstream

输出完整 observed downstream origin set 及贡献证据，明确拒绝 customer cone/customer 解释。

### I10 Path-at-time

在指定状态点返回真实活动 RouteState；窗口 concurrent 或静态样本不能冒充时点活动路径。

### I11 指标一致性

前缀、ASN、IPv4、IPv6、方向和路径事实输出同槽、先后、部分一致、冲突、缺失或不可比较，
并携带可比性 Profile。

### I12 Evidence Graph

每个发布事实可点击追溯到 Tool/Operator 回执、输入人口、publication 和限制；关系边由登记
Operator 输出绑定，Host 不重算业务语义。

### I13 局部失败

一个可选分支失败时，未依赖该分支的节点继续；最终结果明确标记缺失证据，不伪造空集。

### I14 取消

取消操作停止未提交工作，保留已提交 Evidence；取消状态、费用和不可取消边界可审计。

### I15 单步重跑

重跑只生成受影响节点和下游的新 revision；未受影响制品按摘要复用并有复用回执。

### I16 预览与完整导出

预览排序稳定且为完整人口子集；导出字节可解析并与 ResultSet 成员、顺序、数量和摘要逐项闭合。

### I17 多轮追问

“展开那个时间点”“涉及哪些 ASN”等追问解析到当前 Investigation 节点，不依赖模型猜测上句话。

### I18 Sol→DS 回答

Sol 先运行，Host grounding/validation 后 DS 再运行；Teacher 不是事实真值，硬门不能被文本相似度
覆盖，DS 最多一次成功修订。

### I19 边界攻击

customer cone、因果、恢复、全国、用户影响、P2.1 fan-out、外部数据和跨 publication 注入均被
确定性拒绝或降为明确限制/未知。

### I20 同证据离线认证

28 题在同一绑定上完成回放；每题的目标覆盖、事实精度、Evidence、边界、时间/集合/路径语义和
回答结构均有 Host 计算的硬门回执。

## 十、测试与证据要求

每个波次必须提供：

- 单元测试；
- Schema 正反例；
- atomic split test；
- 来源人口完整性与摘要测试；
- 同输入确定性重放；
- 缺失、unknown、empty、not-computable 和跨身份攻击；
- 权限、取消、重试和幂等攻击；
- 对应 28 题 Oracle 子集回放；
- 原始运行 trace、运行回执和文件摘要；
- 实测延迟、内存、结果规模、模型 tokens 与费用。

测试通过只证明其覆盖层。最终实现验收必须绑定同一实现候选、同一 Registry snapshot 和同一冻结
数据，不得拼接不同候选的测试结果。

## 十一、实现完成定义

只有同时满足以下条件，才能声明 P2-S1 `implementation_accepted`：

1. W0–W6 全部通过各自 Hook 和独立验收；
2. P2 v1 全部执行单元有真实实现摘要、测试和 Registry admission receipt；
3. 28 题可执行部分完成同候选回放，延期和外部证据边界准确；
4. InvestigationPlan、ResultSet、Evidence Graph、取消、重跑和导出有端到端证据；
5. Sol→Host→DS 有可信运行制品和 Host 硬门指标；
6. Tool、Operator、模型和端到端性能均已测量并通过冻结门槛；
7. 安全、权限、费用、幂等、崩溃恢复和攻击测试通过；
8. 独立产品语义和 BGP 审查针对同一实现候选通过；
9. `runtime_implemented=true` 可由代码和回执证明；
10. `production_deployed` 仍保持 false，直到独立发布任务完成。

## 十二、禁止的完成声明

在对应证据未闭合前，禁止使用：

- “P2 已实现”；
- “所有 28 题都能完整回答”；
- “运行时已接入”；
- “模型已经对齐”；
- “性能已经通过”；
- “Registry 已激活”；
- “已经上线或生产可用”；
- “可以分析原因、责任、恢复或用户影响”。

实现工程最终允许的最强结论是：

> 冻结的 P2-S1 实现候选已通过离线实现验收，可进入独立的发布准备与生产准入流程。
