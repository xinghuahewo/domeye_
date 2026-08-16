# P2-S0A Tool 与 Operator 生命周期治理 Task Spec（最终验收文档）

版本：`country-outage-agent-p2-s0a-lifecycle-governance-v1`

状态：冻结的建设与最终验收合同

适用范围：国家中断 Agent 的 Capability、只读 Tool 与确定性 Operator 治理控制面

冻结日期：2026-08-11

## 一、文档定位与结论

本 Task Spec 冻结 P2-S0A 的最终产品效果、开发者效果、治理对象、生命周期、机器合同、
入口、出口、权限、证据、回滚和验收门。它不锁定未来 Runtime Registry 使用数据库、
服务框架、部署拓扑、编程语言或控制面形态。

P2-S0A 的核心交付不是“再写一份 Tool 列表”，而是建立一个可离线执行、可审计、
可回放、失败关闭的治理控制面，使一个 Capability 或执行单元从发现、提议、Oracle
准备、认证、激活、弃用、退役直至 Tombstone 的每一步都有不可变身份、合法迁移、
影响分析、证据和回滚点。

本轮建设允许交付离线 Registry、治理命令、机器 Schema、迁移快照、Oracle、Reviewer、
Alignment Hook 和同候选验收证据；禁止修改现有 P1/P1.1/P2 生产运行时、禁止部署、禁止
切换 prod32、禁止修改远程状态或生产配置。Registry 中的 `active` 只表示“在本离线候选
快照中获准进入未来计划准入检查”，不表示已接入、已部署或已在生产执行。

## 二、最终产品与开发者效果

### 2.1 产品效果

最终用户不会直接操作 Registry，但系统必须因此获得以下确定效果：

1. 同一问题在同一 Registry 快照、同一事件/publication 身份和同一输入下，只能选择
   同一组获准 Capability 与执行单元；
2. 非 `active` Capability 或执行单元不得进入 `GroundingPlan` 或
   `InvestigationPlan`，也不得靠模型、Prompt、旧缓存或硬编码绕过；
3. Tool/Operator 被弃用、退役或 Tombstone 后，新计划失败关闭，历史 Evidence 与计划
   仍可解析到原稳定 ID、版本和摘要；
4. 兼容升级、破坏性升级、单单元回滚和整版 Registry 回滚均能解释影响范围，不静默
   改写历史回答；
5. 任何事实仍绑定 RRC25 事件、incident、publication、revision、collector、cohort、
   窗口、`data_through`、finality、合同版本、实现摘要和 Registry 快照；
6. 生命周期治理不会把控制面观测升级为全国断网、真实用户影响、恢复、原因、责任或
   RCA 结论。

### 2.2 开发者效果

开发者必须能够通过一个受控离线入口完成：

- Create：创建新的稳定 ID 或在既有稳定 ID 下创建新 SemVer；
- Read：按种类、稳定 ID、版本、状态或快照读取；
- Update：以新版本提交变更，禁止原地改写既有版本；
- Deprecate：停止新依赖增长，同时允许既有受控计划在期限内继续；
- Retire：禁止新执行，并要求迁移或显式回滚；
- Delete：只删除可清理载荷，不删除 Tombstone、历史身份、摘要和迁移关系；
- Certify/Activate：只有 Oracle、产品语义、安全、费用与性能门全部闭合后才可激活；
- Snapshot/Rollback：生成不可变 Registry 快照，并执行单单元或整版离线回滚演练；
- Impact Analysis：在任何更新、弃用、退役、删除或回滚前得到 Capability、计划、
  Evidence、依赖单元、权限、费用和性能影响清单。

## 三、P2-S0A 入口、出口与边界

### 3.1 入口

P2-S0A 以当前仓库同一基线的以下制品为权威入口：

- P0 v1.3 Capability Discovery Ledger、unknown ledger 和 Oracle seed；
- P1 Runtime Capability Catalog：17 个 selected Capability；
- P1 Typed Tool Contract：`TOOL-01` 至 `TOOL-06`、`OP-01` 至 `OP-03`；
- P1 完整 Oracle：17 个 Capability，每个 normal、missing、null、wrong identity、
  unavailable、boundary 六类，共 102 个展开案例；
- OP-04 `event-window-trend@1.2.0`、`CAP-TREND-001`、Profile Registry、合成 Oracle
  与 P1 集成合同；
- P1 release manifest、摘要验证、readiness probe、认证门、活动指针和整版回滚脚本；
- 国家中断 Agent 总纲中 P2 只读 Typed Tools、确定性 Operator、Evidence Graph 与
  非 RCA 边界。

### 3.2 出口

P2-S0A 只有同时交付以下结果才可结束：

1. 冻结的本 Task Spec 与中文分阶段计划；
2. Capability Registry 与 Execution Unit Registry 的版本化机器合同和 Schema；
3. 稳定 ID、SemVer、`contract_digest`、`implementation_digest`、
   `registry_revision` 和不可变快照；
4. 可离线执行的 CRUD、影响分析、认证、激活、弃用、退役、Tombstone、快照、计划准入
   与回滚入口；
5. 当前 18 个 Capability、6 个 Tool、3 个基础 Operator 和 OP-04 的无语义改写迁移；
6. 生命周期、Oracle、异常、边界、身份冲突、状态篡改、迁移与回滚测试；
7. 与构建角色分离的产品语义 Reviewer 回执；
8. 每阶段 Alignment Hook 回执、最终同候选 manifest、摘要和 postflight 结果；
9. 明确标注“尚未接入生产运行时”的 P2-S0B 入口回执。

### 3.3 固定边界

- 本轮不实现生产 Runtime Registry 服务，不修改 P1/P1.1/P2 运行时选择逻辑；
- 本轮不部署、不切换任何 production release、不访问或改变远程状态；
- 本轮不修改当前 Tool/Operator 的输入、输出、错误、权限、单位、证据或产品语义；
- 本轮不把模型当事实生成器、权限裁决器、状态迁移器或 Registry 写入者；
- 本轮不引入 RRC25 之外的事实来源；
- 本轮不建设 Evidence Graph、开放组合调查、假设生成、多源证据或 RCA；
- 源码存在、HTTP 200、Hook、Schema 通过、单测或离线 Registry `active` 均不能单独
  证明产品验收、运行时接入、合并、部署或生产验证。

## 四、现状基线：已有、部分具备、待建设

| 治理面 | 现状 | 证据与判断 |
|---|---|---|
| Capability Catalog | 已有 | P1 有 17 个 selected Capability、5 defer、4 reject、8 unknown，并按事件绑定协商。 |
| 基础执行合同 | 已有 | 一个版本化 Typed Tool Contract 覆盖 6 Tool 和 3 Operator，含 I/O、身份、权限、超时、null、错误与禁止用途。 |
| 完整基础 Oracle | 已有 | 17×6 类展开案例，覆盖 normal、missing、null、wrong identity、unavailable、boundary。 |
| OP-04 合同 | 已有但独立 | `event-window-trend@1.2.0` 有独立合同、合成 Oracle、Profile Registry 和 P1 集成门。 |
| 运行时合同读取 | 部分具备 | P1 会读取静态 Capability/Tool/Oracle/Policy，但 Grounding 中仍有硬编码映射，OP-04 由独立扩展接入。 |
| 发布认证与摘要 | 部分具备 | release manifest、认证有效期、文件摘要、readiness 和费用记录门已存在，但不是 Tool/Operator 逐单元生命周期。 |
| 回滚 | 部分具备 | 现有脚本可回滚整版 P1 Sidecar release；没有 Registry revision、单单元激活指针或历史计划重放治理。 |
| 统一 Registry | 待建设 | Capability 与 9 个基础单元、OP-04 尚未处于同一生命周期、版本和快照模型。 |
| CRUD 与状态机 | 待建设 | 没有 discovered 到 tombstoned 的合法迁移、审计命令、幂等与并发冲突合同。 |
| 兼容/影响分析 | 待建设 | 没有按 SemVer、依赖、计划、Evidence、权限、费用和性能计算变更影响的统一入口。 |
| Tombstone 与 ID 永不复用 | 待建设 | 没有删除后历史身份保留、迁移映射、旧 Evidence/计划解析和拒绝复用的机器门。 |
| Runtime 快照准入 | 待建设 | 没有在运行时绑定 `registry_snapshot_id` 并由 `active` 状态机械过滤计划；本轮只冻结合同和离线验收。 |

## 五、双 Registry 领域模型

### 5.1 Capability Registry

Capability Registry 表达用户可获得的产品结果，不等同于函数、端点或执行单元。每个版本
至少包含：

- `capability_id`：永久稳定 ID；
- `version`：SemVer；
- 中文 `user_outcome`、目标种类、回答模式和边界；
- 依赖的执行单元稳定 ID 与版本约束；
- 事件类型、collector、权限、身份、证据、null/unknown/unavailable 和单位要求；
- 生命周期状态、状态变更回执和替代/迁移目标；
- `contract_digest`、`semantic_digest`、来源合同与 Oracle 摘要；
- 创建、认证、弃用、退役和 Tombstone 元数据。

Capability 的稳定 ID 表示同一用户结果语义。若用户结果、证据边界、人口、单位或允许断言
发生本质变化，必须创建新 Capability ID；不能仅通过 Major 版本把旧 ID 变成另一个结果。

### 5.2 Execution Unit Registry

Execution Unit Registry 表达受控可执行接口，包括 `read_tool` 与
`deterministic_operator`。每个版本至少包含：

- `unit_id`：永久稳定 ID；
- `kind`、名称、用途、SemVer 和生命周期状态；
- 输入、输出、身份、时间、单位、分页、null、错误、超时和权限合同摘要；
- 依赖单元、允许 Capability、禁止用途和状态提交语义；
- `contract_digest` 与 `implementation_digest`；
- Oracle、产品语义审核、安全、费用、性能和确定性证据摘要；
- 兼容性、替代单元、弃用期限、退役条件与 Tombstone 记录。

### 5.3 双 Registry 一致性

- Capability 依赖必须解析到同一快照中的 Execution Unit；
- Execution Unit 声明的 Capability 反向映射必须与 Capability Registry 一致；
- 两侧版本约束、状态、权限、collector 和边界冲突时，候选失败关闭；
- 单元 `active` 但 Capability 非 `active`，或反之，均不得进入新计划；
- 同一稳定 ID+SemVer 的合同与实现摘要一旦进入 `oracle_ready`，不得原地改变。

## 六、身份、版本和摘要合同

### 6.1 稳定 ID

- 迁移保留 `CAP-001` 至 `CAP-018` 中当前 selected 的 17 个 ID、
  `CAP-TREND-001`、`TOOL-01` 至 `TOOL-06`、`OP-01` 至 `OP-04`；
- ID 区分 Capability 与 Execution Unit 命名空间；
- ID 一经创建永不复用，即使记录已经 Tombstone；
- 重命名只改变显示名称；产品语义变化遵守新 ID 规则；
- 旧别名只能通过有摘要的 migration map 解析，不能成为第二个可执行身份。

### 6.2 SemVer

- Patch：文档澄清、等价实现修复、性能改善或新增不改变既有结果的可选证据；既有合法
  输入、输出、错误、单位、身份和边界均保持兼容；
- Minor：向后兼容地新增可选输入、可选输出、错误细分、Capability 映射或已登记指标；
  旧调用者和旧 Oracle 仍通过；
- Major：删除/收紧输入、删除/改名输出、改变单位/人口/null/错误/身份、增加强制权限、
  改变确定性规则、证据要求、禁止用途或结果语义；必须并行迁移，不能原地替换历史版本；
- 即使标为兼容，只要影响分析发现既有计划或 Oracle 变化，也按不兼容处理；
- Major 仍不得把同一稳定 Capability ID 变成不同用户结果。

### 6.3 摘要与 Registry revision

- `contract_digest`：规范化机器合同的 SHA-256；
- `implementation_digest`：实现文件清单及逐文件 SHA-256 的规范化摘要；
- `semantic_digest`：用户结果、单位、身份、null、错误、证据、边界和禁止断言的规范化摘要；
- `registry_revision`：双 Registry 每次事务提交后单调递增，禁止回退或复用；
- `registry_snapshot_id`：规范化完整快照的内容寻址身份；
- 摘要不匹配、revision 非单调、快照内容漂移或同 ID+版本摘要改变均为一票否决。

## 七、生命周期状态机

合法主路径固定为：

`discovered → proposed → oracle_ready → certified → active → deprecated → retired → tombstoned`

### 7.1 状态含义

| 状态 | 含义 | 可进入新计划 |
|---|---|---|
| `discovered` | 已发现候选及来源，语义和合同尚未冻结 | 否 |
| `proposed` | 已有稳定 ID、版本、合同草案、owner 和影响分析 | 否 |
| `oracle_ready` | 正常、缺失、null、身份冲突、不可用、边界 Oracle 已闭合 | 否 |
| `certified` | Oracle、产品语义、安全、权限、费用、性能、确定性均通过 | 否 |
| `active` | 被某不可变离线快照选中，可供未来运行时准入；不等于生产部署 | 是，仅限绑定该快照 |
| `deprecated` | 仍可为已绑定快照或迁移窗口服务，禁止新增依赖 | 否，新计划；既有计划按快照政策 |
| `retired` | 禁止执行，只允许历史解析、迁移和回滚评估 | 否 |
| `tombstoned` | 可执行载荷已删除，永久保留 ID、版本、摘要、原因、替代项和历史引用 | 否 |

### 7.2 状态迁移规则

- 每次迁移必须携带 actor、reason、前后 revision、前后摘要、证据清单和时间；
- 迁移必须使用 compare-and-swap 的期望 revision；并发冲突不产生部分写入；
- 不允许跳过 `oracle_ready` 或 `certified` 激活；
- `active` 回到 `certified` 只通过回滚新快照实现，不能改写旧快照；
- `deprecated` 可在未发生不兼容变更且重新认证后以新 revision 恢复为 `active`；旧状态
  记录不删除；
- `retired` 不得恢复执行；需要恢复时创建新版本并从 `proposed` 重新认证；
- `tombstoned` 是终态；稳定 ID 和版本永久占用。

## 八、CRUD 与生命周期 SOP

### 8.1 Create SOP

1. 证明稳定 ID 未使用、未 Tombstone；
2. 提交 owner、用户结果/执行用途、版本、来源、权限、边界和初始影响分析；
3. 计算合同、语义和实现摘要；
4. 以 `discovered` 或 `proposed` 写入新 revision；
5. 生成不可变操作回执；
6. 不得同时激活，不得进入计划。

### 8.2 Read SOP

1. 指定 Registry 快照或 revision；
2. 按稳定 ID+SemVer 读取，禁止默认“最新”覆盖历史；
3. 返回状态、摘要、权限、依赖、替代项和历史解析能力；
4. 读取 Tombstone 时返回身份和迁移信息，不返回可执行载荷。

### 8.3 Update SOP

1. 输入旧版本和新候选，不允许原地修改；
2. 机械分类 Patch/Minor/Major，并允许影响分析把声明兼容升级为不兼容；
3. 列出受影响 Capability、执行单元、Oracle、计划、Evidence、权限、费用和性能；
4. 创建新 SemVer，旧版本保持不变；
5. 从 `proposed` 重新经过 Oracle、认证和激活；
6. 未关闭影响或迁移计划缺失时拒绝提交。

### 8.4 Deprecate SOP

1. 先创建可用替代项或记录 `no_replacement` 的产品理由；
2. 冻结弃用期限、迁移人口、调用者、历史快照和回滚点；
3. 禁止新计划依赖，允许旧快照在明确期限内继续；
4. 发布迁移与费用/性能差异；
5. 到期未迁移的依赖阻断 Retire。

### 8.5 Retire SOP

1. 证明活动快照不再选择该版本；
2. 证明无未完成计划、无待重放事务、替代/回滚已演练；
3. 保留合同、摘要、Oracle、Reviewer、迁移和历史 Evidence 解析材料；
4. 状态转为 `retired`，任何执行请求失败关闭；
5. 仍有活动依赖或历史解析缺失时拒绝退役。

### 8.6 Delete SOP

Delete 是受控载荷清理，不是身份删除：

1. 只有 `retired` 版本可申请；
2. 证明法定/审计保留期、Evidence 重放、迁移和替代项已满足；
3. 删除实现定位、临时制品或允许清理的载荷；
4. 写入 `tombstoned`，永久保留 ID、SemVer、合同/实现/语义摘要、退役与删除原因、
   替代项、历史快照引用；
5. 任何再次 Create 同稳定 ID+版本的请求必须被拒绝。

## 九、兼容性与影响分析

### 9.1 兼容性硬门

以下任一变化自动为不兼容：

- 输入从可选变必选、合法值收窄、分页上限或默认人口改变；
- 输出删除、改名、类型/单位/人口/时间语义改变；
- `null`、unknown、unavailable、not_configured、empty 或 0 的语义互换；
- event/publication/revision/collector/cohort/窗口/finality 身份门改变；
- evidence refs、权限、超时、重试、错误或状态提交语义收紧；
- 确定性算法、并列策略、Profile、禁止用途或 RCA 边界改变；
- Tool/Operator 依赖图或执行顺序使既有计划结果改变。

### 9.2 影响分析输出

每次变更至少输出：

- 直接和传递依赖的 Capability、Tool、Operator；
- 受影响的 GroundingPlan/InvestigationPlan 节点种类与快照；
- 需要重跑的 Oracle 类别和具体案例，不无差别重跑全部；
- 历史 Evidence/计划是否仍可重放；
- 权限、数据身份、费用、P50/P95/P99 延迟、超时和资源上限变化；
- 是否需要 Major、新稳定 ID、迁移、Shadow、灰度、回滚或人工批准；
- 未关闭阻断和可接受风险。

## 十、Oracle、异常与边界真值

每个 `oracle_ready` 及以上版本必须至少覆盖：

1. normal：合法输入得到确定结果与完整 Evidence；
2. missing：必要字段或证据缺失，不猜测、不以 0 补齐；
3. null：合法 null 与非法 null 分开处理；
4. wrong identity：incident/publication/revision/collector/cohort/窗口/快照冲突整轮失败；
5. unavailable：不可用、未配置、超时和找不到保持不同错误；
6. boundary：RRC25、权限、写操作、因果、恢复、用户影响和 RCA 越界失败关闭；
7. migration：旧版本、替代项、Tombstone 和历史快照解析正确；
8. tamper：状态、revision、摘要、Oracle、Reviewer 或快照被篡改时拒绝；
9. rollback：单单元与整版回滚后身份、依赖和历史记录闭合；
10. plan admission：非 active、版本不匹配或快照未绑定的节点不能进入计划。

Oracle 必须包含可执行输入、预期结果/错误、状态提交、Evidence、边界断言和 fixture 摘要；
只有 case 名称、自然语言说明或 PASS 字样不构成 Oracle。

## 十一、问题探针与独立产品语义 Reviewer

### 11.1 问题探针

离线问题探针从用户结果、生命周期风险和产品边界生成高风险问题，至少覆盖：

- 非 active 单元是否会被错误计划；
- 旧版本、弃用、退役和 Tombstone 的用户可见语义；
- publication/revision 与 Registry snapshot 同时漂移；
- IPv4/IPv6 单位、逐槽/累计人口和路径相邻关系；
- “恢复、全国断网、用户影响、原因、责任、RCA”等越界表达；
- 组合问题中局部可执行、局部不可执行；
- Update/Withdraw、外部证据、正式历史趋势等当前未具备能力。

探针不能修改真值、实现、Registry 或 PASS 状态，也不能自问自判。

### 11.2 独立产品语义 Reviewer

Reviewer 必须：

- 使用 Product Semantic Charter、旧合同和探针输入独立推导先验真值；
- 不把候选 Registry 自称的状态、构建者结论或 Alignment Hook 当真值；
- 只审核用户结果、身份、单位、null/错误、权限、证据、边界、兼容/迁移和回答政策；
- 不以代码格式、类型检查、Schema 语法或测试数量代替产品语义审核；
- 输出 reviewer/build 角色身份、被审摘要、逐项 Semantic Diff、PASS 或阻断项；
- 任何把 Registry `active` 写成生产部署、把控制面事实写成原因/恢复/用户影响，或让
  非 active 单元进入计划，均为阻断。

## 十二、Shadow、认证、激活、灰度、费用与性能审计

### 12.1 Shadow

- 候选版本在隔离输入上与当前版本并行计算，不执行写操作、不发布用户事实、不提交状态；
- 比较事实、错误、Evidence、权限、费用与延迟，不比较隐藏思维链；
- 差异必须分类为兼容改善、精确事实错误、边界越级、预期 Major 或未知；
- Shadow 通过不自动激活。

### 12.2 认证

认证必须同时绑定：合同/实现/语义摘要、完整 Oracle、独立 Reviewer、安全与权限、费用、
性能、确定性、迁移和回滚演练。任一证据缺失、过期或摘要漂移，状态不得为 `certified`。

### 12.3 激活与灰度

- 激活是创建新的不可变 Registry 快照和活动指针，不修改旧快照；
- 单单元灰度只允许在相同 Capability 语义、相同权限和可比较 Evidence 下进行；
- 灰度路由必须宿主决定并记录 cohort，不由模型选择；
- 错误、边界、费用或性能越门自动停止并回滚；
- 本轮只实现离线激活与灰度合同/演练，不接入生产流量。

### 12.4 费用与性能

每版本记录：模型依赖、外部请求数、每次调用费用或 `zero_external_cost`、P50/P95/P99、
最大延迟、超时、重试、内存/结果大小上限和样本身份。OP-04 保持 `model_dependency=none`；
Tool 的数据 API 延迟与 Operator 的本地计算延迟分开。无法核算时必须是 unknown，不能填 0。

## 十三、回滚、Tombstone、迁移与历史重放

### 13.1 单单元回滚

单单元回滚创建新 Registry revision，把某稳定 ID 的活动版本约束恢复到已认证旧版本；
同时验证所有依赖 Capability、上下游单元、Schema、权限、Oracle 和计划兼容。旧快照不变，
回滚回执包含 before/after snapshot、原因、影响和演练结果。

### 13.2 整版回滚

整版回滚把离线活动指针切回一个完整、已认证、摘要有效的旧快照。不得从多个快照拼接
“半版回滚”。回滚后必须复核双 Registry 交叉引用、活动状态、计划准入和历史解析。

### 13.3 历史 Evidence 与计划重放

每个 Evidence、GroundingPlan、InvestigationPlan 和执行回执未来必须记录：

- `registry_snapshot_id` 与 `registry_revision`；
- Capability ID+SemVer+contract/semantic digest；
- Execution Unit ID+SemVer+contract/implementation digest；
- 事件/publication/revision/collector/cohort/窗口身份；
- 输入/输出/Evidence 摘要与执行状态。

重放默认使用原快照；若原载荷已 Tombstone，只允许身份/迁移解析或明确的替代版本重放，
不得静默使用“最新版”。结果不相同必须产生新的 Evidence 身份。

## 十四、运行时快照绑定与计划准入

未来运行时在一轮请求开始前必须原子读取并固定一个 `registry_snapshot_id`，整轮内禁止
自动漂移。每个 GroundingPlan/InvestigationPlan 节点必须同时满足：

1. Capability 与 Execution Unit 均存在于该快照；
2. 两者状态均为 `active`；
3. 稳定 ID、SemVer、依赖约束和双向映射一致；
4. 当前事件协商能力、publication、revision、collector 和权限允许；
5. Oracle/认证未过期，合同与实现摘要一致；
6. 输入 Schema、单位、null、Evidence 和边界合法。

任一失败，节点不得进入执行器。模型不能选择 Registry revision、恢复非 active 单元、
改变版本约束或绕过授权。本轮以离线 `check-plan` 和篡改测试冻结该准入合同，实际运行时
接入必须作为 P2-S0B 独立任务重新验收。

## 十五、权限、安全与 RRC25/RCA 边界

- Registry 读取与治理写入分权：计划执行者只能读已签快照，治理者按动作拥有
  propose/certify/activate/deprecate/retire/tombstone/rollback 权限；
- 双人职责分离：构建者不得同时成为产品语义 Reviewer 和最终激活批准者；
- 所有写入使用期望 revision、原子替换、规范路径、普通文件、摘要和审计回执；
- 禁止符号链接逃逸、路径穿越、未签合同、未知实现、凭据入库和敏感值写入日志；
- Tool 保持只读；Operator 继承来源读取权限，不获得额外数据权限；
- 所有事实仍为 RRC25 已发布 BGP 控制面观测；publication/revision 不匹配失败关闭；
- 路径相邻不等于依赖/传播/原因，时序共同变化不等于因果，窗口末值改善不等于恢复；
- 全国中断、真实用户/流量/业务影响、DNS/HTTP、原因、责任、政府行为和 RCA 不在本轮
  可发布范围。

## 十六、现有单元迁移验收

### 16.1 迁移集合

- 6 个 Tool：`TOOL-01` 至 `TOOL-06`；
- 3 个基础 Operator：`OP-01` 至 `OP-03`；
- 独立 Operator：`OP-04` / `event-window-trend@1.2.0`；
- 18 个 Capability：P1 selected 的 17 个加 `CAP-TREND-001`。

### 16.2 迁移规则

- 稳定 ID 不变；基础单元首次治理版本记为 `1.0.0`，并保留旧 contract revision；
- OP-04 保留 `1.2.0`，不降级或伪装成 `1.0.0`；
- 从旧合同规范化计算每项 `contract_digest` 和 `semantic_digest`；
- 从明确实现文件清单计算 `implementation_digest`，禁止用 Git 提交号代替文件摘要；
- 迁移只建立离线 Registry 镜像，不改变旧 P1 Runtime Catalog、硬编码 Grounding 或
  release 行为；
- 每项提供 source locator、legacy revision、迁移模式、相等性断言和 Oracle 引用；
- OP-04 与基础九单元进入同一快照，但保留其独立 Profile、合成 Oracle 和 TOOL-03 依赖；
- 迁移前后用户结果、单位、null、错误、Evidence、权限、超时和禁止断言必须等价。

### 16.3 迁移一票否决

- 漏迁任一单元或 Capability；
- ID、OP-04 版本、Capability 映射或 TOOL-03 依赖改变；
- 把旧 defer/reject 能力静默激活；
- 把离线 `active` 写成生产已激活；
- 源合同、实现文件或摘要无法解析；
- 迁移后旧 102 Oracle 或 OP-04 合成 Oracle 的产品语义被改写。

## 十七、量化门与一票否决

最终同候选必须满足：

1. 18/18 Capability、10/10 Execution Unit 迁移并双向闭合；
2. 稳定 ID、SemVer、合同/实现/语义摘要覆盖率 100%；
3. Registry Schema、交叉引用、revision 单调性和快照摘要通过率 100%；
4. 合法状态迁移覆盖率 100%，非法跳转/跳级/终态复用拒绝率 100%；
5. normal、missing、null、wrong identity、unavailable、boundary、migration、tamper、
   rollback、plan admission 十类治理 Oracle 全部通过；
6. 非 active 单元进入新计划的拒绝率 100%；
7. 原地修改既有 ID+SemVer、摘要漂移和 Tombstone ID 复用的拒绝率 100%；
8. 单单元和整版回滚演练各至少 1 次，前后快照和影响闭合；
9. 独立产品语义 Reviewer 阻断项为 0；
10. Alignment Hook 正常场景通过，Task Spec/快照/状态/摘要/Reviewer 篡改负例全部拒绝；
11. 迁移差异中的用户结果、单位、null、错误、权限、证据和边界变化为 0；
12. 所有测试、Reviewer、Hook 和 manifest 绑定同一 candidate ID 与 artifact digest；
13. `backend/core.sha256` 通过，允许路径外改动为 0，生产/远程状态改动为 0。

以下任一项一票否决：

- 非 active、未认证、摘要漂移或快照未绑定的单元到达执行器；
- 未授权治理写入、状态跳级、ID/版本复用、Tombstone 丢失或历史 Evidence 无法解析；
- publication/revision/collector/Registry snapshot 身份冲突仍发布事实或提交状态；
- 任何数值幻觉、权限绕过、写 Tool、跨 collector 拼接或 RCA 越界断言；
- 用源码存在、HTTP 200、Hook、Schema、单测、静态快照或离线 `active` 单独宣称产品、
  合并、部署或生产通过；
- 修改生产运行时、prod32、远程状态或生产配置。

## 十八、交付清单

最终证据包至少包含：

- 本 Task Spec、Plan 和阶段回检回执；
- Capability/Execution Unit Registry、Schema、Policy、迁移映射和不可变快照；
- CRUD、状态机、兼容/影响分析、认证/激活/灰度/退役/Tombstone/回滚合同与入口；
- 计划准入合同、历史 Evidence/计划重放合同；
- 18 Capability、10 Execution Unit 迁移结果和摘要；
- 治理 Oracle、正常与异常回执、状态/摘要篡改负例；
- 单单元和整版回滚证据；
- 问题探针、Product Semantic Charter、独立 Reviewer 回执；
- Alignment Hook、负例测试、逐阶段回执；
- 同候选 manifest、文件 SHA-256、候选/代码/合同/评测身份；
- 费用与性能审计，unknown 和未关闭风险；
- postflight 结果和 P2-S0B 运行时接入建议。

## 十九、非目标与后续入口

P2-S0A 不交付：

- 生产 Runtime Registry 服务或数据库；
- P1/P1.1/P2 Runtime 的 Registry 读取、热更新、流量灰度或生产指针切换；
- 新业务 Tool/Operator、Evidence Graph 或 InvestigationPlan 执行器；
- 多源证据、假设、原因分析、反事实或 RCA；
- 生产部署、prod32 切换、远程配置或状态修改。

P2-S0B 的入口必须是本轮同候选证据包和明确的运行时接入任务合同。S0B 至少需要实现
请求级快照固定、active 准入、旧快照并发排空、运行回执绑定、生产灰度与回滚；必须在
同一运行候选上重新做 API、计划、Tool/Operator、Evidence、状态、费用、性能和浏览器
端到端验收。P2-S0A 的通过不能代替这些结果。
