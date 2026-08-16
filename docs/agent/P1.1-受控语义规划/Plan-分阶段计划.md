# P1.1 受控 Semantic Planner 分阶段计划

版本：1.2

状态：建设路线与阶段边界，不是实施完成声明

最终效果基线：[Task-Spec-最终验收文档.md](Task-Spec-最终验收文档.md)

冻结 P1-v1 候选：`p1-candidate-61354911c7793d75`

## 一、计划定位

本计划规定阶段目标、入口、出口、可调整空间和不可突破边界。

本计划不把 P1.1 拆成五个相互独立的大工程，也不预先指定每一项代码任务、模型供应商、
Prompt 写法、内部类名或精确工期。S0 至 S4 是同一条受控 Semantic Planner 建设路线上的
五个决策点：每个决策点只在证据足够时继续，否则停留、回退或调整路线。

路线允许根据实测结果调整，但不得跳过硬门禁。允许并行探索，不允许把尚未通过的探索结果
直接接入执行、回答发布或会话状态。

本路线将两个维度明确分开：用户目标和请求含义保持开放，包括 `unsupported`、越界、
尚无映射和需要澄清的目标；capability/operator grounding 及其参数、身份和权限保持封闭。
忠实保留用户目标不等于授权执行。

## 二、始终保持的最终方向

```text
自然语言
  → 开放 requested goals 与实体候选
  → 可选的封闭 capability/operator grounding 提案（不可信）
  → Schema / 身份 / 能力 / 参数 / 边界 / 状态校验
  → 逐目标裁决 bound / unsupported / out_of_scope / unmapped / clarify
  → 只有 bound 目标形成唯一受信执行计划
  → P1 确定性事实算子
  → 事实与证据校验
  → 确定性 answerability 裁决
  → 回答发布
  → 成功后原子提交状态
```

任何阶段都必须同时保持以下方向：

- P1-v1 是冻结基线，不通过修改原候选、原 35 例或原验收记录制造 P1.1 成功；
- P0 v1.3 Capability Discovery Ledger 和 P1 v1.2 `adopt` 工具合同是新版 S0 的上游入口；
  P1.1 不重新探索内部系统，也不把 P0 feasibility 或后端数据存在直接变成可执行能力；
- 模型只提出开放请求语义与可选的封闭 grounding，不拥有事件身份、能力目录、事实、证据、
  回答等级、权限或状态提交权；
- Capability Catalog、Typed Operator Contract、Policy/Validator 和算子 oracle 必须先于语义计划
  真值和可执行规划冻结；
- 真实执行只能来自通过校验的唯一计划，Shadow 不参与投票、拼接、执行或发布；
- `rrc25`、incident、publication、revision、cohort、window 和 `data_through` 由系统绑定；
- 原因、责任、用户影响、全国完全断网、外部证据和 P2 至 P5 能力保持关闭；
- `null`、unknown、unavailable、unsupported、no result 和 invalid data 不互相替代；
- `implemented`、`tested`、`accepted`、`deployed`、`production_verified` 分开记录。

## 三、路线调整原则

### 3.1 可以自由调整的内容

在不改变 Task Spec 最终效果的前提下，可以基于阶段证据调整：

- 模型供应商、模型版本、structured output 或 function calling 机制；
- Prompt、few-shot 样例、计划 Schema 的内部组织和规范化策略；
- Validator、Policy、执行适配和状态机的内部模块边界；
- Shadow 采样方式、审核界面、差异分类、缓存、超时和重试策略；
- S1 至 S3 内部探索顺序，以及可安全并行的验证工作；
- 延迟、成本、稳定性和接管比例阈值，但必须在真实测量后记录理由；
- 最终选择继续 Shadow、有限候选接管、主路径接管或保持 P1-v1。

### 3.2 调整必须留下什么

每次影响合同或候选身份的调整必须留下：

- 调整前观察、问题分类和受影响要求编号；
- 调整决定、替代方案和未选择理由；
- 新的 Prompt、Schema、Catalog、Validator 或候选 revision；
- 受影响测试和阶段出口的重新验证结果；
- 是否影响 P1-v1 基线、后续阶段入口和回退能力。

局部优化不要求重跑无关工程；但变更触达的合同、状态迁移、边界或候选身份必须重验。

### 3.3 不可自由调整的内容

下列事项如需改变，必须先修订 Task Spec 和验收合同，不能由阶段实施自行决定：

- 将模型输出当作事实、证据、授权或最终状态；
- 允许模型创造算子、直接联网、直接查询任意数据源或改写事件身份；
- 取消确定性 Policy/Validator、事实校验或成功后原子提交；
- 把 Shadow 输出用于用户回答或业务状态；
- 把 P1.1 扩张为 P2、Evidence Graph、多源调查、因果分析或 RCA；
- 修改 P1-v1 冻结候选来消除 P1.1 的差异。

## 四、阶段目标

### S0：能力与算子前置合同、语义边界与基线

#### 入口

- P1-v1 候选 `p1-candidate-61354911c7793d75` 可复验，身份、35 例和不可变制品明确；
- P0 v1.3 能力 revision、Capability Discovery Ledger、分层证据、feasibility/Oracle seed
  和 `adopt`/`defer`/`reject` 候选处置可校验；
- P1 v1.2 已为 `adopt` 项形成 Capability Catalog、Typed Operator Contract、完整 oracle
  和稳定只读端口；旧 P0/P1 基线不能单独证明本版入口成立；
- 旧 S0 候选 `p1-1-s0-contract-7e534af51d92` 只能作为旧合同迁移输入，不得沿用其身份、
  manifest 或阶段回执证明本 revision 的 S0 出口；
- 当前 P1 规则规划、确定性算子、会话状态和边界能够被阅读、观测和作为对照；
- P1 `adopt` 集合、证据身份、缺失语义和算子实际行为足以建立最小纵向切片 oracle；
- P1.1 Task Spec、任务路径授权和阶段 Hook 已形成。

#### 本阶段目标效果

- 先冻结 Capability Catalog 的能力身份、支持状态、证据包络和边界；
- 再冻结 Typed Operator Contract 的类型化输入输出、前置条件、缺失/失败语义、证据身份和
  副作用边界；
- 冻结 Policy/Validator 的 grounding disposition、字段所有权和失败关闭规则，并为最小纵向切片
  建立算子 oracle；
- 在上述前置制品具有同一 revision 后，才冻结受控计划的最低表达、开放 requested goal、
  对应原问题片段或等价审计锚点、独立语义描述、封闭 grounding、状态提案语义与语义
  计划真值；
- 建立至少 15 例最小语义对照集的结构和审核标准，覆盖“IP地址变换情况”、真实事件切换、
  待澄清污染、复合表达、`unsupported` 和越界目标；
- 明确 baseline plan、shadow plan、reviewed plan 与 executed plan 的身份和比较口径；
- 建立模型、Prompt、Schema、Catalog、Typed Operator Contract、Policy/Validator、算子 oracle、
  基线规则和候选 revision 的追溯口径。

本阶段到期映射：P1.1-EFF-02、P1.1-EFF-04、P1.1-EFF-05、P1.1-EFF-06、P1.1-EFF-07、P1.1-EFF-13、P1.1-EFF-16；
P1.1-GATE-01、P1.1-GATE-02、P1.1-GATE-03、P1.1-GATE-08、P1.1-GATE-09、P1.1-GATE-14；
P1.1-SCE-01、P1.1-SCE-02。

#### 可调整空间

- Schema 可采用单计划多 goal 或显式子计划，只要完整保留多目标并能逐项裁决；
- 对照集可先人工编写，再吸收真实问题；数量可增加，不得低于最终门禁；
- Catalog 的文件形式、生成方式和版本策略可在可审计前提下调整；
- 不要求先实现全部工具；未实现或越界目标必须在 Catalog/Policy 中表达为非 `bound`，
  不得通过语义层模拟实现；
- S0 可以做无 Provider 的 fixture 验证，但不能把 fixture 通过写成模型能力通过。

#### 出口

- 每个受控字段均有类型、来源、所有者、是否可由模型提出及失败语义；
- Capability Catalog、Typed Operator Contract、Policy/Validator 和算子 oracle 先行闭合，且能力、算子、
  参数、边界和状态转换都有可版本化合同；
- 开放的 requested goal 与封闭的 capability/operator grounding 在 Schema、Policy 和对照集中可分别校验；
- 最小对照集的真值在前置制品后冻结，并拥有审核后的 requested goals、entities、grounding
  disposition、可选 capability/operator、policy 和 state effect 标准；
- P1-v1 冻结候选复验通过，P1.1 变更未重写原证据；
- P0 能力 revision 与 P1 `adopt` 工具合同引用闭合，`defer`、`reject`、unknown 或只有
  feasibility 的条目均未进入可执行目录；
- 新 S0 合同候选具有区别于旧候选的 revision、manifest 和阶段回执；旧回执未被复用；
- S1 可在最小纵向切片合同已闭合、但不要求全部工具已实现的前提下，记录完全不执行的
  Shadow 可比较输出。

#### 边界

- 不接入真实执行主路径，不让模型输出触发算子或状态写入；
- 仅有封闭 `requested_goal` 枚举、通用 `unsupported` 回退、简化 operator 映射或旧阶段
  回执时，必须判定存在待处理偏离，不得进入 S1；
- P0 能力账本或 P1 v1.2 工具合同未形成时，必须判定存在待处理偏离，不得进入 S1；
- 不得在 Catalog、Typed Operator Contract、Policy/Validator 或算子 oracle 未闭合时先冻结语义计划真值；
- 不提前声称语义准确率、延迟、成本或 Provider 可用性；
- 合同未闭合时不得用 Prompt 约定代替 Validator 或 Policy；
- 未达到当前阶段出口时不得进入下一阶段。

### S1：Shadow 规划与差异观测

#### 入口

- S0 出口有回执，Catalog、Typed Operator Contract、Policy/Validator、算子 oracle、Schema、
  比较口径和最小语义对照集已按依赖顺序冻结 revision；
- 最小纵向切片合同足以校验 Shadow 的 grounding 提案，但 Shadow 无任何算子执行权；
- Provider 调用边界、凭据隔离、超时、取消和日志脱敏方案已审核；
- Shadow 通道在设计上没有执行、发布和状态提交权限。

#### 本阶段目标效果

- 对同义、口语、输入误差、复合目标、实体、指代和省略生成结构化 Shadow 计划，并忠实保留
  `unsupported`、越界和无映射的 requested goals；
- 同轮记录 baseline、shadow 和 reviewed plan，将 requested goal 完整性与 capability/operator grounding
  正确性分开，再按实体、政策和状态效果分类差异；
- 对最小语义对照集进行至少 3 次重复规划，观察规范结果稳定性而非原始文本一致性；
- 记录模型/Provider、Prompt、Schema、Catalog、输入指纹、延迟、用量、成本估计和失败分类；
- 保持 P1-v1 为唯一执行与回答路径。

本阶段到期映射：P1.1-EFF-01、P1.1-EFF-02、P1.1-EFF-03、P1.1-EFF-12、P1.1-EFF-13；
P1.1-GATE-02、P1.1-GATE-09、P1.1-GATE-10、P1.1-GATE-11、P1.1-GATE-13；
P1.1-SCE-01、P1.1-SCE-02、P1.1-SCE-03、P1.1-SCE-11。

#### 可调整空间

- 可比较一个或多个模型，但不得因此扩大用户数据、联网或执行权限；
- 可调整 Prompt、示例、temperature、重试和规范化，只要每次调整形成新的可追溯 revision；
- 差异审核可使用离线脚本或内部界面，呈现形式不作为阶段硬约束；
- 如果实测显示模型无增益，可停止在 Shadow，不强制进入接管路线。

#### 出口

- Shadow 的算子调用、用户回答发布和业务状态写入均为 0；
- 最小语义对照集有逐例、重复、差异与审核结果，能解释收益和失败分布；
- “IP地址变换情况”不再因单字“换”被 Shadow 规划为事件切换；
- 复合表达不丢目标，`unsupported`、越界或无映射目标不被改写为已有 capability，无法唯一
  解析的表达给出最少必要澄清提案；
- 已形成是否进入 Validator 接管候选的证据化决定，而非按计划默认前进。

#### 边界

- Shadow 不与基线拼接、投票，不修改本轮回答和下一轮状态；
- 模型评分、解析理由和 Intent 标签都不能视为 answerability 或执行授权；
- 模型日志不得包含凭据、内部 Prompt 或不必要的原始个人对话；
- S1 指标不足或差异不可解释时，继续 Shadow 或调整，不得越过门禁。

### S2：计划校验、策略与受控执行

#### 入口

- S1 对照结果证明模型计划存在可验证增益，且进入有限候选的理由已记录；
- Schema、Catalog、Typed Operator Contract、Validator、Policy、算子 oracle 和回退 revision 均可独立识别；
- 进入受控执行的最小纵向切片算子已实现，并通过 Typed Operator Contract 和 oracle 校验；
- 受控执行可以限制在测试/候选流量，并能一键回到 P1-v1。

#### 本阶段目标效果

- 模型计划先经过 Schema、身份、能力、参数、边界和状态转换校验，再形成唯一受信计划；
- 确定性策略先逐 requested goal 裁决 `bound`、`unsupported`、`out_of_scope`、`unmapped` 或
  `clarify`；只有 `bound` 目标可进入执行；
- 未登记 capability、operator、参数、模型伪造身份和 Prompt Injection 在数据读取前失败关闭；
- 事实、证据、时间、单位、ASN、前缀和 answerability 由确定性层生成和裁决；
- 复合问题逐 goal 形成可回答、缺能力、证据不足或需澄清结果，允许局部成功；
- 执行与回答始终绑定同一 incident、publication、revision、window 和 `rrc25`。

本阶段到期映射：P1.1-EFF-04、P1.1-EFF-05、P1.1-EFF-06、P1.1-EFF-07、P1.1-EFF-08、P1.1-EFF-12；
P1.1-GATE-02、P1.1-GATE-03、P1.1-GATE-04、P1.1-GATE-05、P1.1-GATE-07、P1.1-GATE-11；
P1.1-SCE-06、P1.1-SCE-07、P1.1-SCE-08、P1.1-SCE-12。

#### 可调整空间

- Validator 可按多层管线或统一策略引擎实现，但拒绝位置和 reason code 必须可观测；
- 候选流量可以从离线回放、内部测试或极小比例开始，比例由风险证据决定；
- 对模型计划可选择严格拒绝或安全澄清，前提是不执行越界部分且不伪造成功；
- 自然语言呈现可以调整，但事实与证据不得经过模型新增或改写。

#### 出口

- 非白名单算子、参数和冲突身份的实际执行次数为 0；
- 模型新增事实、证据或越级原因/责任结论的发布次数为 0；
- 所有实际执行可追溯到唯一 validated/executed plan 和确定性事实结果；
- 多意图局部成功、发明算子、错标边界和身份伪造场景均有实际结果；
- 回退到 P1-v1 后语义和会话行为保持可解释。

#### 边界

- 不允许模型直接调用工具、构造 SQL、访问任意 URL 或决定证据引用；
- 不允许把“模型高置信”替代 Schema、Policy、事实或证据校验；
- 原因、责任、用户影响、全国完全断网和外部证据仍不可回答；
- S2 不证明多轮状态安全，未通过 S3 前不得扩大接管范围。

### S3：多轮状态、澄清与失败降级

#### 入口

- S2 的有限受控执行门禁成立，所有执行均绑定唯一有效计划；
- 会话状态拥有 revision、turn、并发/取消和原子提交边界；
- Provider 失败与 P1-v1 回退通道可以被故障注入测试。

#### 本阶段目标效果

- `inherit`、`set`、`clear` 只是计划提案，整轮成功后才原子提交；
- `pending_clarification` 绑定缺失字段、源 turn 和源 goal，并与新问题联合解析；
- 后补实体完成旧澄清，明确改问新主题则清除旧澄清，避免旧状态劫持新问题；
- 超时、限流、非法 JSON、Schema 无效、Provider 5xx、取消、乱序和 revision 漂移均失败关闭；
- 普通用户仍看到 P1 风格的结论、事实、证据、限制、未知和上下文，而非内部计划倾倒。

本阶段到期映射：P1.1-EFF-03、P1.1-EFF-09、P1.1-EFF-10、P1.1-EFF-11、P1.1-EFF-14、P1.1-EFF-15；
P1.1-GATE-04、P1.1-GATE-06、P1.1-GATE-07、P1.1-GATE-10、P1.1-GATE-12；
P1.1-SCE-04、P1.1-SCE-05、P1.1-SCE-09、P1.1-SCE-10。

#### 可调整空间

- 可根据错误分布选择基线回退、安全澄清或明确失败，不要求所有错误使用同一降级方式；
- 可调整会话到期、超时和重试阈值，但必须证明不会重复发布或写入幽灵状态；
- 调试视图可选择展示规范计划、差异和 reason code，不要求进入普通用户界面；
- 状态实现形式可调整，前提是原子性、幂等、取消、乱序和 revision 约束可验证。

#### 出口

- 无效计划、失败算子、校验失败、取消、超时、乱序和 Shadow 的业务状态提交数均为 0；
- 待澄清补齐、待澄清后切换主题、状态清除、并发和失败场景逐例通过；
- 模型不可用时 P1-v1 仍可用，或系统明确失败关闭且不伪造模型成功；
- 前端不获得 Provider 凭据、内部 Prompt、原始错误或未经校验计划；
- 具备在同一候选上进行全量 P1.1 验收的身份和故障证据。

#### 边界

- 聊天历史不是事实证据，旧状态不能覆盖当前事件的确定性身份；
- 回退不允许变成自由文本回答，也不允许静默改变回答等级；
- 前端体验改善不能替代状态机、取消、乱序和重连证据；
- 未完成失败注入和状态污染验证时不得进入接管或发布决策。

### S4：同候选全量验收与晋级决策

#### 入口

- S0 至 S3 的阶段出口、偏离和修正均有回执；
- 一个候选完整绑定代码、模型/Provider、Prompt、Schema、Catalog、Validator、基线、评测集、环境和制品摘要；
- 所有验收可以在同一候选身份、同一数据身份和同一测试口径下执行。

#### 本阶段目标效果

- 在同一候选上复验 P1-v1 35 例、P1.1 最小语义对照集、重复规划、Shadow 隔离、边界、
  失败、状态污染、身份、安全和回退场景；
- 汇总真实延迟、用量、成本估计、错误分布和未关闭偏离，形成阈值与风险理由；
- 依据证据选择继续 Shadow、有限流量候选、接管规划主路径或回退 P1-v1；
- 明确 P1.1 的实现、测试、验收、部署和生产验证状态，不跨层宣告。

本阶段到期映射：P1.1-EFF-01 至 P1.1-EFF-16；
P1.1-GATE-01 至 P1.1-GATE-14；
P1.1-SCE-01 至 P1.1-SCE-12。

#### 可调整空间

- 不预设必须接管主路径；保持 Shadow 或回到 P1-v1 都可以是合格的证据化决定；
- 可根据风险增加对照集、重复次数、故障类型和观察期，不得减少最终硬门禁；
- 部署和生产验证可以作为后续独立授权工作，不要求为了完成 P1.1 验收而自动发布；
- 若候选变更触达身份或合同，允许回退到相应阶段重新验证。

#### 出口

- 所有 P1.1-EFF、P1.1-GATE 和 P1.1-SCE 均有同候选证据、明确结论和可定位结果；
- P1-v1 35/35 复验通过，冻结候选和原验收记录未被重写；
- 未关闭偏离被列明且不会被“Hook 通过”“模型演示”或局部测试掩盖；
- 最终决定、回退路径、适用边界和下一授权动作明确；
- P1.1 只被称为 RRC25 事件绑定聊天问答的语义规划增强。

#### 边界

- 验收不自动授权部署、生产切换、数据源扩张或 P2 建设；
- P1.1 通过不表示 P2、Evidence Graph 或 RCA 已实现；
- 不使用不同候选的局部绿色结果拼接最终验收；
- S4 Hook 给出最终“一致”或“已修正”结论，仍需同候选效果证据才能宣告验收。

## 五、阶段封口与 Alignment Hook

每个阶段结束必须调用国家中断 Agent 计划防偏离 Hook：

```bash
python3 .codex/hooks/country_outage_agent_program_review.py \
  --project P1.1 \
  --stage S0
```

将 `--stage S0` 替换为实际阶段 `S1`、`S2`、`S3` 或 `S4`。

启用 Codex Stop Hook 时，同时声明：

```bash
export DOMEYE_COUNTRY_OUTAGE_AGENT_PROJECT=P1.1
export DOMEYE_COUNTRY_OUTAGE_AGENT_STAGE=S0
```

Hook 会检查 Task Spec 和 Plan 的结构、要求编号与阶段映射、当前 `.codex/TASK.json` 路径边界，
并提示人工回答每项防偏离问题。

Hook 结构检查通过不等于 Semantic Planner、模型、评测、页面、部署或生产效果通过。阶段只能在
实际出口证据成立后判定为“一致”或“已修正”；若证据不足，必须判定为“存在待处理偏离”。

## 六、变更与回退规则

- 若只改变 Prompt 或模型但不改变 Schema/Catalog/Policy，重验语义对照集、稳定性、失败和成本；
- 若改变 Catalog、Typed Operator Contract、Policy/Validator 或算子 oracle，其派生的 Schema、语义计划真值和
  可执行规划 revision 立即失效，回到 S0 重新冻结并只重验受影响切片；
- 若只改变 Schema 内部组织或状态转换且未改变 Catalog/Operator/Policy 语义，回到 S0/S2/S3 的
  受影响出口重验；
- 若改变执行、回答或证据身份，必须重验 P1-v1 35 例和所有受影响联合场景；
- 若候选身份、数据身份或制品摘要不一致，不得沿用旧阶段回执；
- 若模型增益不足、成本不可接受或失败不可关闭，保持 Shadow 或回退 P1-v1，不视为工程失败造假；
- 若最终目标或边界发生变化，先修订 Task Spec、Plan、Hook 配置和评测合同，再继续实现。

## 七、计划完成的判定

本文完成只表示 Task Spec 已被映射为一条可调整、可回退、可阶段复盘的建设路线。

它不表示 S0 至 S4 已执行，不表示模型已接入，不表示 P1.1 已实现、已验收、已部署或已生产验证。
