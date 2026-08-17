# Domeye 产品、数据与 Claim（对外陈述）边界 v1.1

> 本文是 Domeye Agent 重构的独立边界合同。本文中的 Claim 只表示“系统对用户作出的事实性陈述”，不是独立的运行时对象、状态机或通用谓词体系。

| 项目 | 内容 |
|---|---|
| 文档版本 | v1.1 |
| 日期 | 2026-08-16 |
| 决策状态 | 已确认的目标边界；当前代码尚未全部实现 |
| 目标产品 | Domeye 国家网络中断调查 Agent |
| 当前首个交付切片 | 基于 RRC25 BGP 控制面数据的滚动调查闭环 |
| 当前事实范围 | RRC25 观察事实和登记 Operator 的确定性派生结果 |
| 明确不建设 | 通用 Claim Schema、Claim 状态机、独立 Claim Validator、普通回答的 Claim Publisher |
| 不代表 | 当前已经具备全国中断判定、原因分析、用户影响评估或生产发布能力 |
| 首片合同 | [Domeye 首个纵向切片锚点合同 v1.0](Domeye_First_Vertical_Slice_Anchor_v1.0.md) |
| 代码事实 | [Domeye 当前代码基线](Domeye_Current_Code_Baseline_2026-08-16.md) |

---

## 1. 为什么必须单独成文

目标架构回答“Agent 怎样理解、执行和继续调查”，本文回答另一个问题：

> Domeye 当前能观察到什么、能计算什么，以及最终回答最多可以说到什么程度？

如果不单独规定，系统很容易出现四类错误：

1. 把目标产品“国家网络中断调查 Agent”缩成一个静态事件页面；
2. 把当前仅接入 RRC25 的阶段能力写成产品永久定义；
3. 把一次查询成功或分页闭合写成全球网络事实完整；
4. 让模型把 BGP 控制面变化扩写成全国断网、用户影响、原因、责任或恢复。

本文约束的是产品承诺、数据语义和回答边界，不规定自然语言问题必须命中哪一个模板，也不建立第二套 Agent 路由。

---

## 2. 权威产品定义、当前能力和事实边界

### 2.1 唯一有效的产品定义

> **Domeye 是面向开放式国家网络中断调查任务的 Agent。它理解用户目标，每次提出一个下一步能力请求，根据真实执行结果继续调查、改变方法、澄清、停止或组织回答。**

这一定义同时规定：

- 产品对象是“国家网络中断调查任务”，不是某一个 collector 的查询任务；
- 产品形态是能够观察后继续规划的 Agent，不是静态事件视图；
- Tool、Operator、数据源和页面都是可演进的能力组成，不反向定义产品；
- 当前证据不足时，Agent 可以返回局部事实、未知、限制和下一步证据需求，但不能因此被改名成单数据源查询器。

面向用户的产品主名称应保持为：

> **Domeye 国家网络中断调查 Agent**

如果需要副标题，应描述 Agent 的调查方式或当前建设阶段，例如：

> **基于可追溯网络证据的滚动调查 Agent**

不得用“单一 RRC25 数据能力”替代产品名称，也不得把 Domeye 降格为一个国家中断事件视图。事件、publication、revision、collector 和时间窗等绑定条件属于当前能力合同，不属于产品定义。

### 2.2 当前第一个能力切片——不是产品定义

当前第一个纵向切片只接入：

- 已绑定事件；
- 已绑定 publication 和 revision；
- 单一 RRC25 采集器；
- 冻结观察窗口；
- 当前已有的 Tool、Operator 和 Artifact 资产。

因此当前可建设、可验证的是：

> **基于 RRC25 BGP 控制面数据的第一个 Agent 调查闭环。**

RRC25 是当前能力范围，不是产品名称、产品副标题或永久定义。界面如需展示当前能力，应与产品标题分层，例如：

```text
Domeye 国家网络中断调查 Agent
当前已接入证据能力：RRC25 BGP 控制面
```

不能把第二行替换第一行，也不能把当前能力包装成一个新的“事件视图”产品。

### 2.3 当前回答事实边界——不是产品边界

当前回答只能使用：

- 数据身份与观察范围；
- RRC25 实际记录的 BGP 控制面事实；
- 对这些事实执行登记 Operator 得到的确定性结果；
- 明确的未知、冲突、部分完成和 limitation。

当前不能仅凭这些数据把“国家网络中断”“真实用户影响”“原因”“责任”“攻击归因”或“实际恢复”作为事实发布。

这里收缩的是当前回答强度，不是 Domeye 的产品目标。产品可以继续围绕国家网络中断调查逐步接入多 collector、主动探测、流量、DNS、HTTP / TLS 和外部事件证据。

`country_outage` 可以继续作为领域名称、事件类型、模块名或历史兼容命名。它表示 Agent 正在调查的领域，不表示单一 RRC25 数据已经证明某国发生了网络中断。

正确关系是：

```text
目标产品：Domeye 国家网络中断调查 Agent
  ↓
当前能力：RRC25 BGP 控制面 Tool / Operator
  ↓
当前 Finding：RRC25 观察事实与确定性派生结果
  ↓
当前回答：受观察者、人口、时间和完整性边界限制
```

---

## 3. 当前数据边界

### 3.1 每次执行必须绑定同一组身份

以下信息不能从聊天文本临时猜测：

| 字段 | 含义 |
|---|---|
| incident / event | 当前调查绑定的事件 |
| publication_id | 使用的正式数据发布 |
| publication_revision | 发布版本 |
| collector | 当前通常为 RRC25 |
| observation_window | 本次观察窗口 |
| address_family | IPv4、IPv6 或明确分开展示的双栈 |
| population_strategy | fixed cohort 或 dynamic observed population |
| source_population | Tool 实际枚举的人口 |
| registry_snapshot | 本次允许使用的能力和实现快照 |
| candidate_identity | 代码、配置、模型、Prompt、数据和 Policy 候选 |

这些公共身份优先放在 Artifact Envelope 或 Action / Turn 上，不要求在每一句回答中重复复制。

### 3.2 当前正式来源

当前正式事实来源是绑定 publication 中的 RRC25 BGP 控制面数据及其登记派生结果。

以下内容不能自动并入当前事实范围：

- 其他 RIS 或 RouteViews collector；
- 主动探测；
- 流量、DNS、HTTP、TLS 或用户测量；
- 新闻、社交媒体和模型常识；
- 商业关系库；
- 外部搜索结果；
- 未绑定 publication、版本和来源合同的本地文件。

外部数据接入必须有独立数据合同、身份、权限、范围和测试。模型引用网页不代表数据边界已经扩大。

### 3.3 四种“完整”必须正交

| 维度 | 回答的问题 |
|---|---|
| query enumeration completeness | 声明的查询和分页是否枚举完成 |
| source snapshot completeness | publication 内声明的数据是否齐备 |
| observer scope / coverage | RRC25、VP、peer 和健康状态实际覆盖什么 |
| world completeness | 是否足以代表全球路由、数据平面或用户世界 |

它们可以同时处于不同状态。例如：

> 查询分页已完整枚举，但来源或观察者覆盖有限，不能推出全球完整。

当前 `world completeness` 通常是 false 或 unknown。

### 3.4 必须随结果传播的边界

以下信息至少进入 Artifact Envelope 或具体 Finding：

- collector / VP / peer 范围及健康状态；
- observation window；
- IPv4 / IPv6；
- population strategy 与 population reference；
- 比例结果的 denominator；
- query / source / observer / world completeness；
- 时间采样间隔、缺口和删失状态；
- Operator / Profile / version；
- Evidence / lineage reference；
- limitation、unknown、conflict 和部分失败。

---

## 4. Typed Finding，而不是通用 Claim 子系统

### 4.1 Typed Finding 是什么

Typed Finding 是 Tool 观察事实或 Operator 确定性派生结果的类型化表示。

它不是自然语言段落，也不是由 Pi 任意发明的谓词。它的类型来自具体 Tool / Operator 合同。

例如首个切片中的 extrema Finding 可以包含：

```yaml
finding_type: extrema
metric: fixed_visible_ipv4_address_count
min_value: 123456
unit: unique_ipv4_address
first_observed_slot: 2026-01-01T00:00:00Z
population_ref: fixed_population_artifact
evidence_ref: time_series_artifact
```

事件、publication、collector、window、候选身份和完整性等公共字段由外层 Artifact Envelope 继承。

### 4.2 最小字段按结果类型决定

所有 Finding 不需要共享一个臃肿的十几字段 Schema。只保留共同最小语义：

- finding type；
- subject / metric；
- value 与 unit；
- time scope；
- support / lineage reference；
- 必要 limitation。

不同类型按需增加字段：

- 比例：必须有 denominator reference；
- 排名：必须有 Profile、排序键和并列规则；
- 时间：必须有 observed_at 或 event_time_interval 及删失状态；
- 集合：必须有 population 和 completeness；
- 路径：必须有 AS_PATH canonicalization Profile 和 observer scope。

### 4.3 Claim 在本文中的含义

Claim 只表示最终语言中的事实性陈述，例如：

> “在绑定 publication 的 RRC25 观察中，IPv4 可见地址指标的最低值为 X。”

它不是独立数据库实体，不要求：

- Claim ID；
- Candidate / Rejected / Verified 状态机；
- 全局 predicate 词表；
- 每句话重复 candidate digest 和 policy version；
- 独立 Publisher 为普通聊天逐句签发；
- 通用 Validator 证明任意自然语言蕴含。

系统通过“Finding 先存在、Renderer 只能消费 Answer Context、Guard 检查表达漂移”控制回答边界。

---

## 5. 当前允许表达的事实层级

| 层级 | 内容 | 当前状态 |
|---|---|---|
| F0 — 身份与来源 | 事件、publication、revision、collector、窗口和候选身份 | 允许 |
| F1 — 观察事实 | RRC25 在指定人口和时间窗记录的值、状态或路径表示 | 允许，必须带观察范围 |
| F2 — 确定性派生 | 差值、极值、排序、集合比较和时间区间等登记 Operator 输出 | 允许，必须绑定输入和算法版本 |
| F3 — 多信号印证 | 多 collector、主动探测、流量、DNS、HTTP / TLS 等相互印证 | 当前没有正式合同 |
| F4 — 中断、影响与原因 | 全国中断、用户影响、业务损失、原因、责任、恢复和攻击归因 | 当前禁止作为事实 |

产品名不能让 F1 / F2 自动升级成 F4。

当现有数据不足时，正确结果可以是：

- 请求澄清；
- 返回局部结果；
- 明确未知或证据冲突；
- 说明当前数据不能回答；
- 建议接入有正式合同的外部证据能力。

---

## 6. 当前禁止的语义放大

以下表达不得从单一 RRC25 BGP 控制面结果推出：

| 禁止说法 | 原因 | 允许的替代表达 |
|---|---|---|
| “全国网络已经中断” | 单一观察者控制面数据不能代表全国网络和用户世界 | “RRC25 在声明范围内观察到……变化” |
| “有 N 个用户受影响” | 没有用户、流量或可达性证据 | “当前数据不支持估算用户影响” |
| “事件由某方造成” | 相关性和时间邻近不能证明原因或责任 | “现有证据不能判断原因和责任” |
| “这是一次攻击” | 没有归因合同和独立信号 | “当前证据不足以进行攻击归因” |
| “事件已经恢复” | 指标回升或窗口结束不等于实际恢复 | “截至窗口末，该观察指标回升至……” |
| “两 AS 物理直连” | AS_PATH 序列不等于物理拓扑 | “RRC25 观测路径表示中出现相邻序列项” |
| “A 是 B 的客户或上游” | AS_PATH 顺序不证明商业关系 | “现有路径证据不支持商业关系判断” |
| “没有观测到，所以不存在” | 观察缺失不等于世界不存在 | “在当前观察范围内未观测到” |

这些规则必须进入 Finding 类型、Answer Context 和 Response Guard 的测试，不能只写进 Prompt 或页面免责声明。

---

## 7. 五条 BGP 领域硬规则

### 7.1 AS_PATH 只表示观测序列

路径相关 Finding 使用 `observed_sequence_adjacency` 或同等清晰术语。

不得自动解释为：

- 物理直连；
- 商业 customer / provider；
- 实际流量路径；
- 真实传播方向；
- customer cone。

### 7.2 时间只能精确到证据支持的程度

必须区分：

- `observed_at`：某个观察槽实际记录到状态；
- `event_time_interval`：状态变化可能发生的区间；
- left-censored；
- right-censored；
- sampling gap；
- observer unhealthy。

“首次观测到”不能渲染成“准确发生于”。

### 7.3 IPv4 与 IPv6 分开

IPv4 与 IPv6 的人口、地址空间、指标和恢复程度分别计算。需要总览时也必须保留分项，不得静默合成一个结果。

### 7.4 数量不等于影响程度

前缀数、ASN 数、IPv4 / IPv6 地址覆盖、fixed cohort 比例、VP agreement、baseline deviation 和 observer coverage 是不同维度。

若排序，必须说明：

- 排序指标；
- Profile 和权重；
- tie 规则；
- 观察范围；
- 不能代表用户影响或全国严重程度。

### 7.5 Fixed 与 Dynamic Population 不能混用

正式保留：

- Fixed Cohort View；
- Dynamic Observed Population View。

每个比例必须能回答“分母来自哪套人口”。两类人口的比较由确定性 Operator 完成，不能由 Renderer 临时换分母。

---

## 8. 回答组织与边界检查

当前正式路径是：

```text
Tool / Operator
  → Artifact / Typed Finding
  → Answer Context
  → Renderer / DLP
  → Response Guard
  → 用户回答
```

### 8.1 各方职责

| 组件 | 可以做 | 不可以做 |
|---|---|---|
| Pi Agent | 理解目标、提出下一步、观察结果、判断何时信息足够 | 批准自己的 Action；把猜测变成 Finding |
| Tool | 读取声明事实人口并返回类型化结果 | 解释原因、影响或直接写最终回答 |
| Operator | 对合格输入做版本化确定性计算并产生 Finding | 隐藏读取、调用模型或任意扩张语义 |
| Artifact / Finding | 保存结果、身份、范围、人口、完整性、lineage 和 limitation | 承担自然语言规划 |
| Answer Context Builder | 选择当前问题需要的 Finding 和限制 | 新计算、删除必要限制或创造新事实 |
| Renderer | 将 Answer Context 组织为自然语言、表格或导出并执行 DLP | 引入 Context 外的业务事实 |
| Response Guard | 确定性检查可验证的表达对齐和禁用语义，只返回 `pass/block` | 宣称证明世界真相、改写草稿或在 `block` 后再次调用模型 |

### 8.2 Response Guard 的最小范围

第一版只检查可明确实现和测试的内容：

1. 回答中的关键数字、单位、ASN、前缀和时间能在 Answer Context 中找到；
2. IPv4 / IPv6 没有静默混合；
3. 比例使用了 Finding 中的 denominator；
4. “首次 / 末次观测”没有被写成精确事件时刻；
5. RRC25 观察没有被扩大成 global、nationwide 或用户世界事实；
6. AS_PATH 相邻没有被写成物理、商业或流量关系；
7. 必须随附的 limitation、unknown、conflict 和部分失败没有被删除；
8. 输出满足 DLP 和渠道策略。

Guard 返回 `block` 后不得输出原草稿，也不得再次调用模型。系统只能使用同一 Answer
Context 生成固定格式的确定性安全答案；若同一 Context 仍不足以安全表达，则返回明确
限制。回退不得增加事实、改变 Candidate 或更换数据身份。

### 8.3 Guard 不是什么

Response Guard 不是：

- 通用真理验证器；
- 任意 Claim 的逻辑证明器；
- 第二个规划器；
- 第二个 LLM Reviewer；
- 给每句话签发证书的 Publisher。

NLI、反向事实抽取或第二模型若未来经评测采用，只能作为 Guard 决定前的附加告警；它们
不能在 `block` 后触发第二次生成，也不能覆盖确定性 `pass/block` 决定。

### 8.4 何时才需要额外发布审批

普通对话不建设独立 Publisher。以下场景如果未来真实出现，可通过 ADR 和独立决策门增加渠道级审批：

- 面向外部机构的正式报告；
- 敏感或跨租户数据导出；
- 自动触发外部行动；
- 法律、监管或审计交付；
- 多源因果、攻击或责任归因。

渠道级审批不应倒灌为普通回答逐句 Claim 状态机。

---

## 9. 对 Agent 路由的约束

产品和数据边界不等于问题分类器。

禁止：

```text
自然语言问题
  → Claim 类型
  → Question Template
  → 固定 DAG
```

正确运行方式是：

```text
Semantic Goal
  → 一个 Capability Proposal
  → Admission
  → Action
  → Observation / Finding
  → Replan / Clarify / Finish
```

Capability Family 只组织合同、文档和评测覆盖。一个 Action 获准读取某类数据，不等于下一项 Action、外部数据源或更强结论已经获准。

---

## 10. 面向用户的表达规则

推荐使用：

- “在 RRC25 的当前观察范围内……”；
- “首次观测到该状态的槽位是……”；
- “根据 fixed cohort，比例为……，分母为……”；
- “查询已完成，但来源或观察者覆盖有限……”；
- “当前 BGP 控制面证据不能判断原因或用户影响……”。

不得为了语言流畅删除：

- 观察者范围；
- 人口与分母；
- 时间精度；
- 地址族；
- 必要 limitation；
- 未知、冲突或部分完成状态。

---

## 11. 何时可以扩大事实边界

接入一个新 API、增加一份数据文件或允许模型搜索互联网，不会自动扩大边界。

若未来要支持 F3 或 F4，至少需要：

1. 独立来源合同和版本身份；
2. 时间、空间、对象和人口对齐规则；
3. 明确哪些信号组合支持哪一级结论；
4. 冲突、缺失和观察者异常处理；
5. 新的领域 Operator 或 Finding 类型；
6. 针对表达边界的 Response Guard 用例；
7. 真实任务评测、红队和回滚条件；
8. 必要时通过 ADR 决定是否引入渠道级发布审批。

在这些条件成立前，产品目标可以继续面向国家网络中断调查，但当前回答必须诚实停留在已有证据范围。

---

## 12. 验收用例

### 12.1 必须成功

1. 返回绑定事件、publication、revision、RRC25 和窗口；
2. 返回有单位、有地址族、有来源的 RRC25 观察值；
3. 返回登记 Operator 计算的差值或极值，并追到输入 Artifact；
4. 返回首次观测槽位和可能事件区间；
5. 返回 fixed population 比例及 denominator；
6. 表达“查询完整但来源或观察者不完整”；
7. 表达“当前观察范围内未观测到”，而不是“不存在”；
8. Renderer 漂移时 Guard 返回 `block`，原草稿不对用户可见，并从同一 Answer Context
   生成确定性安全答案或明确限制。

### 12.2 必须阻断并进入确定性回退

1. 单 RRC25 推出全国断网；
2. 前缀或 ASN 数量推出用户影响；
3. 指标回升推出实际恢复；
4. AS_PATH 相邻推出物理直连、商业关系或流量路径；
5. 查询完成推出全球完整；
6. fixed 和 dynamic 人口静默混合；
7. 没有 denominator 的比例；
8. 没有结束证据的“已经恢复”；
9. Renderer 添加 Answer Context 之外的数字或业务事实；
10. limitation、unknown、conflict 或部分失败被删除。

### 12.3 每条验收轨迹至少保存

- Goal 与 Proposal；
- Admission Decision；
- policy、Registry 和 candidate 身份；
- Artifact / Typed Finding；
- completeness、population 和 limitation；
- Answer Context；
- Renderer 输出；
- Response Guard 结果；
- 确定性回退或明确阻断结果。

不要求为每一句话生成 Claim ID。

---

## 13. 与 22 项整改的对应关系

| Review ID | 本文承担的整改 |
|---|---|
| R02 | 分开目标产品、当前切片和当前事实边界 |
| R14 | 用 Typed Finding、Answer Context 和 Response Guard 防止自然语言放大；不建设通用 Claim 平台 |
| R15 | 默认单 Pi 或确定性骨架表达；Teacher 只有 A/B 证明净收益后才考虑 |
| R18 | 正交表达 query、source、observer 和 world completeness |
| R19 | AS_PATH 只表达观察序列，不推出物理、商业或流量关系 |
| R20 | 区分观察槽和事件时间区间，显式表达删失与缺口 |
| R21 | 使用多指标控制面变化向量，不冒充用户影响或全国严重度 |
| R22 | 固定人口与动态人口并存，比例绑定 denominator |

R14 的整改目标是防止“有引用就任意扩写”，不是要求给所有事实句建立通用谓词、状态机和 Publisher。

---

## 14. 当前 `main` 的真实状态

当前基线为：

```text
main@6a4bbd41aa712c12080a0126e5f8b1ec1440a9ca
```

当前代码已有：

- Tool、Operator 和部分类型化结果资产；
- P1 Evidence Bundle；
- P2 ResultSet、EvidenceGraph、Receipt 和 CAS 原型；
- 多类 BGP 边界规则；
- 旧回答校验和 fixture 经验。

当前尚未证明：

- 真实 Pi 使用 Tool 的滚动 Agent 闭环；
- 统一 Typed Finding / Artifact Envelope；
- Answer Context Builder；
- 本文规定的轻量 Response Guard；
- 同一候选上的成功、拒绝、执行失败后重选和回答回退轨迹；
- 生产部署和真实用户流量。

因此本文当前状态是 **Designed**，不能标为 Implemented、Verified 或 Released。

---

## 15. 版本和变更规则

以下变化必须显式更新本文或通过 ADR：

- 扩大数据源或 observer scope；
- 改变人口、分母、时间或完整性语义；
- 允许新的 Finding 类型或更强事实层级；
- 允许全国中断、用户影响、原因、责任、恢复或归因结论；
- 改变 Renderer 可以添加的内容；
- 改变 Response Guard 的硬阻断项或安全回退；
- 为正式报告、敏感导出或自动行动引入渠道级审批。

普通措辞调整、UI 排版和不改变语义的 Renderer 优化不需要把每句话注册成 Claim。

---

## 16. 一句话边界

> **Domeye 的目标产品是开放式国家网络中断调查 Agent；当前首个切片只接入 RRC25 BGP 控制面能力，因此 Tool / Operator 先产生有范围的 Typed Finding，模型只能据此组织回答，Response Guard 阻断可测试的语义放大，而不是由一套通用 Claim 平台决定系统能说什么。**
