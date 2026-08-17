# P2-S1 国家中断组合式调查助手实现工程分阶段计划

文档版本：`country-outage-agent-p2-s1-implementation-phase-plan-v1`  
文档状态：实现规划基线，尚未开始 W0 运行时代码  
绑定设计候选：`country-outage-p2-s1-s1d-6-04135cee55b39ce5d574f7e4`  
实施波次：W0 → W1/W2 → W3/W4 → W5 → W6

## 一、计划目的

本计划把冻结设计合同转换为可执行的实现工程。每个波次不仅列出代码任务，还必须回答：

- 本波次为最终用户效果增加了什么；
- 实现了哪些原子 Tool 或 Operator；
- 消费了哪一种受信事实人口；
- 产生了哪些运行制品与 Evidence；
- 如何证明没有隐藏复合变换；
- 如何失败、取消、重试和回滚；
- 达到什么证据后才允许进入下一波次。

本计划不授权生产部署。W6 通过后只能进入独立发布准备。

## 二、统一实施规则

### 2.1 冻结输入规则

所有实现波次必须绑定：

- design candidate ID；
- candidate content digest；
- acceptance manifest；
- 当前实现候选 ID；
- Registry snapshot ID 与 digest；
- RRC25 publication/revision/cohort/window；
- Tool/Operator 实现摘要与语义摘要；
- 参数 Profile digest；
- 上一波次验收回执。

设计合同需要变更时必须停止，不得在实现中兼容性“猜测”设计意图。

### 2.2 Tool 功能原子性

一个 Tool 只读取一种冻结事实人口，并且只承担一种主查询语义。

每个 Tool 必须满足：

1. 只读取一个冻结事实人口；
2. 一个调用只有一种主查询语义；
3. 过滤条件必须是该事实人口的原生索引或物化谓词；
4. 不在 Tool 内执行排序、极值、集合投影、路径位置、时间关系或一致性分类；
5. 不调用其他 Tool、Operator 或模型；
6. complete、partial、sample、source-incomplete 必须有机器状态和可信回执；
7. 分页使用冻结稳定排序、去重键、总数和 continuation token；
8. 每个 Tool 都必须通过 `atomic_split_test` 和人口替换攻击测试。

### 2.3 Operator 功能原子性

一个 Operator 只执行一种登记的确定性业务变换。

每个 Operator 必须满足：

1. 只执行一个登记的确定性业务变换；
2. 输入必须是冻结 Tool/ResultSet 或上游 Operator 的类型化输出；
3. 不读取数据库、网络、文件系统外部事实或世界知识；
4. 不调用模型，不发布自然语言结论；
5. 不把路径定位、邻接、集合计数、时间关系等多个变换揉成一个单元；
6. 输出 Schema、结果状态、Profile、复杂度和 Evidence lineage 全部冻结；
7. 同输入必须得到同摘要；
8. 每个 Operator 单独通过正例、边界、攻击和 `atomic_split_test`。

### 2.4 组合边界

组合只能发生于 InvestigationPlan/Host。Renderer、API handler、页面、导出器和 LLM 不得通过循环、
批处理或隐藏查询替代 `PLAN-CAP-02`，也不得生成未登记派生事实。

### 2.5 阶段候选与回执

每个波次生成一个不可变实现候选和波次回执。回执至少包含：

- 波次、实现候选、父候选；
- 输入设计摘要与 Registry snapshot；
- 改动文件及 SHA-256；
- 已实现执行单元及实现摘要；
- 测试、Schema、atomicity、Oracle 与攻击回执；
- 性能与费用测量；
- known limitations；
- runtime、promotion、deployment 状态；
- 下一波次准入结论。

同一阶段不得拼接不同候选的测试或运行证据。

### 2.6 Hook 调用规则

阶段结束必须调用：

```bash
python3 .codex/hooks/country_outage_agent_p2_s1_implementation_alignment.py \
  --repo-root . \
  --stage <S1I-P0|W0|W1|W2|W3|W4|W5|W6> \
  --output <receipt-path>
```

当前任务只生成并通过 `S1I-P0` 实现规划基线。W0–W6 Hook 已定义 evidence contract，但在真实
实现证据出现前必须 fail closed。

## 三、依赖总览

```text
S1I-P0 实现规划基线
  ↓
W0 Source / Schema / Registry / Trusted Receipt Store
  ├── W1 ASN / Prefix / State Time
  └── W2 Window Path / Projection / Set
        ├── W3 Interval / Cohort Set（同时依赖 W1）
        └── W4 Path-at-time / VP
              ↓
W5 Investigation Runtime / Result / Graph / API / UI / Sol→DS
  ↓
W6 Same-candidate Offline Certification
  ↓
独立发布准备任务（不属于本计划）
```

## 四、S1I-P0：实现规划与防跑偏基线

### 4.1 要达成的效果

实现团队拥有一份无歧义的效果目标、波次依赖、原子性边界、验收旅程和机器 Hook；任何人都不能
把“完成几个接口”误写成“P2 实现完成”。

### 4.2 工作

- 绑定冻结 design candidate 与 final receipt；
- 编写实现工程 Task Spec；
- 编写 W0–W6 分阶段计划；
- 创建实现 baseline JSON；
- 创建实现对齐 Hook 与攻击测试；
- 明确 P2.1 deferred 与生产部署边界。

### 4.3 输出

- `Task-Spec-P2-S1实现工程目标与最终验收.md`；
- `Plan-P2-S1实现工程分阶段计划.md`；
- `implementation-baseline.json`；
- Implementation Alignment Hook；
- Hook 定向测试；
- `S1I-P0.json` 回执。

### 4.4 退出门

- 目标文档必须先写用户与系统要达成的效果，再写技术工作；
- W0–W6 的入口、输出、原子性门、测试和退出效果完整；
- Hook 能拒绝设计摘要漂移、效果章节缺失、P2.1 混入、运行时/部署冒充和跳阶段；
- 不修改 backend、frontend、tools、data 或 deploy；
- `runtime_implemented=false`、`production_deployed=false`。

## 五、W0：Source、Schema、Registry 与可信回执基础

### 5.1 入口

- S1I-P0 回执通过；
- 冻结设计候选可解析；
- source owner、Registry owner、runtime owner 明确；
- 数据生成与读取边界完成影响分析。

### 5.2 要达成的效果

后续每一个 Tool 调用都能证明“读了哪个冻结人口、为什么完整、使用了哪个索引和实现”，并且
未准入、延期或摘要漂移的执行单元无法运行。

### 5.3 实施工作

1. 为 TOOL-07..12 准备冻结 source view/schema；
2. 建立 prefix state、ASN state、new prefix、RouteState-at-time、window path 的物化人口；
3. 建立 path-ASN membership 与 eligible anchor population 索引；
4. 建立 checkpoint、manifest、materialization 和 query receipt；
5. 实现受信 Registry snapshot resolver；
6. 实现执行单元 admission、版本、实现摘要和语义摘要校验；
7. 实现可信回执存储、内容寻址和重放；
8. 冻结权限、只读和审计上下文；
9. 为未来 ResultSet/Graph/事务提交准备统一身份信封。

### 5.4 原子性门

- 每个 source view 只表达一个事实人口；
- 预物化索引不能预先做业务聚合或生成调查结论；
- Tool 查询时不得临时回放事件或解析整条路径来伪装原生索引；
- Registry resolver 不执行 Tool 或 Operator；
- receipt store 不解释业务语义。

### 5.5 测试

- Schema 正反例和 FormatChecker；
- source manifest 缺页、重复、顺序、摘要、跨 publication 攻击；
- eligible anchor 集合伪造与 known origin 非尾端攻击；
- Registry snapshot 替换、实现摘要换签、inactive/deferred 单元攻击；
- checkpoint 恢复与 no-future-read；
- 可信回执 ghost、篡改和重放攻击。

### 5.6 输出

- source schema/view/index 实现；
- materialization 与 trusted receipt store；
- Registry admission 实现及 W0 admission snapshot；
- W0 测试、性能基线和回执。

### 5.7 退出门

- 所有 W1–W4 Tool 所需人口已可受信解析；
- 未准入单元 fail closed；
- 真实 source completeness 可由回执重放；
- 尚不声称用户调查可用。

## 六、W1：ASN、前缀与状态时间

### 6.1 入口

- W0 通过；
- prefix/asn/new-prefix 人口和回执可用；
- TOOL-07/08/09/10 的 Registry contract 已准入候选。

### 6.2 要达成的效果

用户能够得到完整、可分页、可追溯的 ASN→前缀、指定时点状态、新前缀、首次/最后出现和连续
异常区间结果，替代当前只能看到窗口峰值汇总的限制。

### 6.3 实施单元

Tool：

- `TOOL-07` fixed cohort member；
- `TOOL-08` prefix state；
- `TOOL-09` ASN state；
- `TOOL-10` new prefix state。

Operator：

- `OP-05..14`；
- `OP-35`；
- `OP-36`。

具体名称、输入输出和状态以冻结 catalog/schema 为准，不在实现中重新命名或合并。

### 6.4 原子性门

- AS 排序只由 OP-05 执行；
- 首次、最后、区间、比例、状态投影各由独立 Operator 执行；
- Tool 不返回模型生成的 AS 名称或世界知识；
- 空集、unknown、missing 和 left-censored 分开；
- ASN→prefix 成员与 path relation prefix 人口严格分离。

### 6.5 测试与 Oracle

- 覆盖 Q03、Q04、Q05、Q06、Q07、Q08、Q09、Q10、Q13、Q14、Q16、Q17、Q18；
- 排序三键、competition rank、result position；
- MOAS 重复、IPv4/IPv6 key、非法 CIDR；
- 首槽已异常、缺槽、最后异常和持续区间；
- 完整分页、稳定排序和导出人口对账；
- 同输入确定性重放。

### 6.6 退出门

- W1 单元逐个通过 Schema、atomicity 和 Oracle；
- ASN/前缀调查效果在离线 API harness 中可运行；
- 不依赖 W2 路径能力也能独立闭合对应问题；
- 页面尚可不接入，但结构化 ResultSet 必须真实可消费。

## 七、W2：窗口路径、投影、集合与计数

### 7.1 入口

- W0 通过；
- complete window path population、anchor population 和 native filter receipt 可用；
- `TOOL-12` contract 已准入候选。

### 7.2 要达成的效果

用户能够查询经过某 AS 的窗口级完整观测路径人口，查看路径结构、observed downstream origin、
关联前缀和方向集合，并获得互相独立、可对账的集合与计数。

### 7.3 实施单元

- `TOOL-12`；
- `OP-15..28`。

### 7.4 原子性门

- contains/order 是 Tool 原生过滤合同，不在 OP-19 内重新判断；
- 路径包含、位置、邻接是独立变换；
- path/prefix/direction 三种集合投影独立；
- 三种基数计算独立；
- observed downstream 投影不执行 customer cone 推断；
- 本波次不实现逐成员动态 fan-out。

### 7.5 测试与 Oracle

- 覆盖 Q19、Q20、Q21、Q22、Q26、Q27；
- typed segment、AS_SET、confederation、prepend、unknown；
- known origin 与观测 origin/path tail；
- eligible/noneligible anchor；
- 完整/空/来源不完整人口；
- OP-19 全成员一一投影、贡献回执和 evidence 对账；
- path/prefix/direction 集合不混淆；
- customer、provider、peer、cone 越界攻击。

### 7.6 退出门

- complete ResultSet 可分页和导出；
- 预览明确为总体子集；
- OP-19 输出只能使用 observed downstream 命名；
- Q20/Q26 只交付 P2 v1 支持子集，延期子目标明确。

## 八、W3：异常区间交集与固定 cohort 前缀集合

### 8.1 入口

- W1 与 W2 均通过；
- state interval set 和 fixed cohort member ResultSet 已冻结。

### 8.2 要达成的效果

用户可以准确判断两个 AS 的异常窗口是否重叠，并比较它们的固定 cohort 前缀集合；端点相接、
空集、缺槽和人口不完整不会被误判为重叠或共享。

### 8.3 实施单元

- `OP-38 intersect_state_interval_sets`；
- `OP-39 project_fixed_cohort_prefix_set`。

### 8.4 原子性门

- OP-38 只做两个完整半开区间集的交集；
- OP-39 只把完整 fixed cohort member 行投影为唯一 prefix key 集；
- 不复用点对点时间比较或路径人口投影来兼任；
- 不在任一 Operator 内计算 Jaccard 或生成因果结论。

### 8.5 测试

- 端点相接、左右空、双方空、乱序、内部重叠；
- window/grid/identity 不一致；
- MOAS 重复、v4/v6 key、非法前缀；
- incomplete/missing/unknown fail closed；
- O(n+m) 与 O(n log n) 复杂度测量。

### 8.6 退出门

- Q09 所需时间重叠和前缀集合人口闭合；
- 输出可直接被集合关系 Operator 消费；
- 无隐式 fan-out 或人口切换。

## 九、W4：Path-at-time、观察方向与 VP 一致性

### 9.1 入口

- W0 与 W2 通过；
- RouteState checkpoint、exact-time materialization、path membership index 可用。

### 9.2 要达成的效果

用户从极值时间点下钻时，看到的是该时点实际活动路径和观察方向，而不是窗口样本或状态并发关系；
系统能够描述 VP 方向一致、部分变化、缺失或不可比较。

### 9.3 实施单元

- `TOOL-11`；
- `OP-29..33`；
- `OP-37`。

### 9.4 原子性门

- Tool 只读取 exact-time RouteState 人口；
- path membership 使用 W0 冻结索引，不在查询时解析整库；
- 时间关系、VP 分类、受控 join、一致性分类各自独立；
- Host 只校验 Operator 结构化输出，不重算路径或时间业务语义；
- 不引入 RouteEvent 或 OP-34。

### 9.5 测试与 Oracle

- 覆盖 Q14、Q18、Q23、Q31、Q32；
- no-future-read、checkpoint 漂移、指定槽不存在；
- visible/unknown/not-applicable 路径状态；
- 返回路径确实包含目标 ASN；
- VP missing/unknown/mixed 优先级；
- OP-29 not-comparable 不得投影为先后；
- OP-37 冲突只能来自同时间域互斥断言。

### 9.6 退出门

- exact-time 路径结果可重放；
- window association 与 path-at-time 在 API 和文案上明确分离；
- Q23 动态逐路径位置仍标 P2.1 延期。

## 十、W5：组合调查运行时、接口和页面

### 10.1 入口

- W1、W2、W3、W4 全部通过；
- 原子执行单元已准入同一 Registry snapshot；
- 运行时事务、权限、费用和模型执行 owner 明确。

### 10.2 要达成的效果

用户可以真正创建、观察、取消、重跑和追问一个组合调查；页面交付关键发现、时间线、指标关系、
Evidence Graph、限制和导出，而不是把多个 API 结果拼成一次性长回答。

### 10.3 实施范围

- `PLAN-CAP-01` 与 InvestigationPlan admission/execution；
- ResultSet freeze、preview、page、export；
- Evidence Graph commit；
- 调查/节点 revision 与事务一致性；
- `GATE-01..05`、`BOUNDARY-01`；
- `RENDERER-01..03`、`DELIVERY-01`；
- Sol planning/reference、Host grounding/validation、DS first/revision；
- Investigation API、查询/取消/重跑/导出 API；
- 国家中断页调查入口和调查详情交互。

### 10.4 原子性门

- Plan 组合原子单元但不执行隐藏业务变换；
- Renderer 不计算事实；
- Delivery 不改变人口；
- API handler 不循环替代动态 fan-out；
- LLM 不调用未准入单元、不直接写 Evidence Graph；
- Host 不把 Teacher 文本当事实，也不重算 Operator 业务语义。

### 10.5 状态与失败

必须实现：

- draft、admitted、running、partially_completed、completed、cancelled、failed；
- 节点 pending、running、passed、reused、failed、cancelled、skipped_dependency_failed；
- revision、parent、CAS、幂等、崩溃恢复；
- 局部失败继续、可选节点缺失、hard dependency 阻断；
- 单步重跑的影响闭包；
- 模型不可用、Teacher 验证失败和用户授权 degraded 分支。

### 10.6 测试

- Plan DAG、权限、Registry、输入 provenance；
- ResultSet 完整/分页/预览/导出字节闭包；
- Evidence Graph 节点、边、Operator output 和端点绑定；
- 五类事务提交、CAS、幂等和崩溃恢复；
- 取消竞态、局部失败、重跑影响闭包；
- Sol/DS 顺序、可信 stores、一次成功修订；
- typed claim、Oracle boundary、世界知识与事件事实分离；
- API 和浏览器交互证据；
- 隐式 fan-out、复合 Tool、Host 重算和 Renderer 补算攻击。

### 10.7 退出门

- 三项核心能力真实可用：事件全景、时间点下钻、证据一致性；
- 调查状态、ResultSet、Evidence Graph 与回答同事务闭合；
- 页面和 API 在本地/隔离环境完成端到端验证；
- 不声称模型或性能已晋级；
- 不授权生产部署。

## 十一、W6：同候选离线认证与实现验收

### 11.1 入口

- W5 通过；
- 冻结 implementation candidate、Registry snapshot、测试数据和模型合同；
- 产品语义与 BGP 独立审查者可用。

### 11.2 要达成的效果

证明 P2-S1 不只是“本地能跑”，而是在冻结候选上稳定回答目标问题、守住 Evidence 与边界、支持
运行治理，并且具有可接受的性能、费用和恢复行为。

### 11.3 认证工作

1. 28 题同 GroundingPlan/Evidence/Registry/publication 回放；
2. Host 计算目标覆盖、事实精度、Evidence、边界、时间/集合/路径语义和结构硬门；
3. DS 首答与至多一次修订；
4. Tool/Operator 单元性能；
5. 组合调查端到端性能；
6. 大人口分页和完整导出；
7. 取消、局部失败、重跑、崩溃恢复；
8. 权限、注入、跨身份、P2.1 偷渡和关系越界攻击；
9. 独立产品语义审查；
10. 独立 BGP 审查；
11. 形成单一 implementation candidate 与 acceptance manifest。

### 11.4 退出门

- 28 题可执行部分通过同候选回放；
- P2.1、外部证据和不可执行题准确标界；
- model alignment、performance acceptance 只有在真实证据闭合后才能为 true；
- runtime implementation 可由代码、Registry 和运行回执证明；
- `production_deployed=false`；
- 最终结论只能是 `implementation_accepted_for_release_preparation`。

## 十二、P2.1 隔离

`PLAN-CAP-02`、`TOOL-13`、`OP-34` 必须另立 Task、设计候选、Registry admission 和运行验收。
任何波次发现为了完成 P2 v1 必须动态逐成员展开时，应把相应子目标标记为 deferred，而不是在 Host、
Tool、Operator、Renderer、API handler 或模型中写隐式循环。

## 十三、阶段停止条件

出现以下任一情况立即停止当前波次：

- 冻结设计摘要或语义需要改变；
- Tool 需要读取两个事实人口；
- Operator 需要执行两个业务变换；
- 需要外部数据或第二 collector；
- 需要 P2.1 动态 fan-out；
- 数据人口无法证明完整性或 no-future-read；
- Host 必须重算业务语义才能验证 Operator；
- 测试证据来自不同实现候选；
- 运行时、模型、性能或部署状态被提前写为通过；
- 无法提供取消、重跑、费用或 Evidence 回执。

## 十四、发布边界

W6 通过后仍需独立发布任务完成：

- 不可变发布候选；
- 环境配置与秘密检查；
- 数据兼容和迁移演练；
- staging smoke；
- 生产准入审查；
- 发布、监控、回滚与发布后证据；
- 旧能力兼容和认证责任分拆。

因此本计划的终点是“实现候选可进入发布准备”，不是“已经上线”。
