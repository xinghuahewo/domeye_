# 国家中断报告 Agent DeepSeek 模型认证运行说明

状态更新时间：2026-07-30（2.2 编排迁移后）

## 1. 当前结论

`deepseek-v4-flash` 已完成本地 A4 真实模型认证，并已通过机械晋级写入正式模型
注册表。这里的“正式”只表示当前候选工作树的正式 Sidecar 入口可以选择该已认证
profile，不表示已经部署到生产。

这次五报告认证的证明范围是**核心模型组合**：DeepSeek 模型与 adapter、Pi、
三个只读工具、Skill/提示词/报告规范/validator、RRC25 事实上下文，以及固定
timeout/retry/token 上限。它不证明
`country-outage-external-evidence-pack-v1`、Evidence Gateway、来源适配器、
网页读取、外部材料解析或外部附录已经可用。

认证证据：

```text
evidence:model-certification:b50f247c7b1322df6d05afa45c5c1078b58349329d9f27ec5800bbfa5770a1d4
```

认证于 `2026-07-30T09:19:12.493Z` 开始，`09:19:34.010Z` 完成。两份代表性
伊朗 RRC25 报告和三个认证专用边界场景全部通过；每份报告恰好使用两次 provider
请求，retry 为 0，均生成完整 ReportDocument、Markdown 和 PDF，并通过
`country_outage_report_validator_rules_v5`。五份报告实际保守成本合计
`0.08085616 CNY`。

供应方没有提供不可变权重 revision，因此 `deepseek-v4-flash` 仍按可变别名管理。
当前认证有效至 `2026-08-06T09:19:34.010Z`，认证场景集为
`country-outage-rrc25-legal-scenarios-v2`，输入范围为
`legal_country_outage_rrc25_v1`；到期或正式路径变化后必须重新认证。

机械晋级后的注册表 SHA-256 为：

```text
30a5743019a19ace272a70c35f3bfbffb72286d33b3d698f526c6af8739e4ff6
```

Pi `0.82.1` 的 `responseModel` vendor patch 仍按批准摘要
`5805cc08566c4d9437280f68d996ef0fb452c15e2becb67b94c967b7ace2023b`
核验；正式路径继续关闭 PackageManager、ModelResolver、`models.json` 刷新和
外部 glob，只加载固定 Skill 与三个只读工具。

预算没有重置、删改或回算。`2026-07-30T09:40:39.213Z` 的模型候选只读
readiness 为
`ready=true`、`blockers=[]`；账本共 34 条记录，累计保守承诺
`14.23965984 CNY`，剩余 `5.76034016 CNY`，未结预留为 0，历史用量状态为
`resolved`。该检查明确记录 `credentialsRead=false`、
`networkAccessed=false`；它不是外部证据 capability readiness。

## 2. 冻结候选

候选资源：

`agent-sidecar/resources/model-candidates/deepseek-v4-flash-pi-0.82.1-v1.json`

冻结内容如下：

| 项目 | 冻结值 |
|---|---|
| 候选 ID | `deepseek-v4-flash-pi-0.82.1-v1` |
| 状态 | `candidate` |
| provider / model | `deepseek` / `deepseek-v4-flash` |
| Pi | `0.82.1` |
| API 适配器 | `openai-completions` |
| thinking | `off` |
| 模型目录上下文 | 1,000,000 token |
| 模型目录最大输出 | 384,000 token |
| 实际运行最大输入 | 64,000 token |
| 实际运行最大输出 | 16,384 token |
| 每份报告 provider 请求轮次上限 | 2（首轮渲染 + 最多一次整包修复） |
| 进入 adapter 前的 Context 容量/DoS 上限 | 900,000 UTF-8 bytes |
| HTTP 前最终 adapter payload 上限 | 59,904 UTF-8 bytes |
| provider framing 预留 | 4,096 token |
| 结构化输出 | 每轮 DeepSeek 请求均强制 `tool_choice=none` 与 `response_format=json_object` |
| provider retry | 0 |
| 完整报告认证套件 | 5 份：2 份代表事件重复性 + 3 份认证专用边界场景 |
| 付费上限 | 20 CNY |
| 保守折算 | 1 USD = 8 CNY |

目录单价也固定在候选资源中。未来付费运行按真实可达的两轮 provider 请求逐轮
取正式执行上限，不再按模型目录的 1,000,000 token 能力预留：

```text
单轮输入 = 64,000 × 0.14 USD / 1,000,000 × 8 = 0.07168 CNY
单轮输出 = 16,384 × 0.28 USD / 1,000,000 × 8 = 0.03670016 CNY
单轮合计 = 0.10838016 CNY
单份报告 = 单轮 × 2 = 0.21676032 CNY
五份报告 = 单份报告 × 5 = 1.0838016 CNY
```

因此，完整认证套件启动前至少需要 `1.0838016 CNY` 可用余额；每份报告实际开始前
再单独预留 `0.21676032 CNY`。只有 Pi 审计已经接受，且每轮回执均完成
下述 usage 一致性校验时，才可按全会话实际 usage 结算；这也适用于 Pi 已接受、
但随后 PDF 等后处理失败的运行。Pi 审计本身只要拒绝，包括最后一轮 provider
失败、超时、用户取消、回执缺失或统计不一致，就不得用已收到的部分统计释放
预留，必须按整份 `0.21676032 CNY` 结算。缓存输入按 input、cacheRead、
cacheWrite 三者最高单价保守计费，不能靠缓存字段规避预算。

本次认证启动前按五报告包络一次性检查预算是否足够，再按顺序逐份预留和结算；
以下真实费用与认证结果属于旧五轮工具合同的历史记录，不是当前零工具候选的成本
证明。历史认证实际合计结算为 `0.08085616 CNY`。认证后剩余批准预算为
`5.76034016 CNY`。历史账本没有因为 64K 公式修正而重置；上界收紧只来自发送前
实际可达的运行时硬门。

认证和晋级后又完成一次正式 profile 浏览器报告旅程，实际估算费用为
`0.0011592448 CNY`。该费用不回写 candidate ledger，只在 20 CNY 项目总预算
口径中单独计入；因此该 2.1 时点累计保守承诺为 `14.2408190848 CNY`，剩余
`5.7591809152 CNY`。这是 2.1 时点的历史预算快照。

2.2 编排迁移后又透明执行了两次真实核心报告调用：第一次 Pi 报告成功，但 PDF
后处理因所配置的系统 Python 缺少 `reportlab` 而失败；第二次只调整既有
`DOMEYE_REPORT_PYTHON_EXECUTABLE`，完整报告、Markdown 和 PDF 成功。两次
Pi 审计都为 `accepted`，合计 `0.0002931712 USD`，按冻结折算
`1 USD = 8 CNY` 计为 `0.0023453696 CNY`。因此当前项目累计保守承诺为
`14.2431644544 CNY`，剩余 `5.7568355456 CNY`。两次调用均计入，不把第一次
PDF 环境失败隐藏为“未消费”。

`59,904 UTF-8 bytes + 4,096 framing reserve` 是在尚未固定 DeepSeek 官方
tokenizer 和服务端 chat framing 时采用的保守工程假设，不是精确 tokenizer
证明。900,000 字节门继续只承担进入 adapter 前的容量/DoS 防护，不能作为计费
上界。最终 payload 门位于既有 payload hook 和 `response_format` 注入之后，因而
首轮和受控整份修订轮都无法绕过发送前检查。

上述本地冻结单价只用于候选边界和离线预算计算，不能单独证明下一次调用时供应商
仍采用该价格。下一次任何真实 provider 调用前，受信操作者还必须提供仍在有效期
内的供应商价格证据，至少绑定 provider、模型、币种、计价单位、input/output/
cacheRead/cacheWrite 单价、取得时间、失效时间和证据 SHA-256；实际预留使用的
单价不得低于该证据。若供应商支持项目或密钥级消费限额，还应先设置不高于本次
批准余额的供应商侧硬限额。当前代码中的候选 JSON 和本文计算不替代该外部证据，
在价格证据缺失或过期时不得再次发起真实调用。证据单价高于候选冻结值时，必须
先更新候选资源、预算公式和回归证据，不能继续使用现有 `1.0838016 CNY` 上界。
当前零工具改造改变了候选资源 SHA-256；此前绑定旧候选摘要的价格证明即使尚未
到期也不得复用。下一次真实调用前必须重新取得官方价格并生成绑定新候选摘要的
价格证明。

## 3. 认证文件

候选与正式路径复用同一套专用认证文件读取和冻结 `CredentialStore`：

- 路径必须由宿主显式提供绝对路径；
- 必须是当前用户拥有的普通文件；
- 禁止符号链接；
- 权限不得宽于 `0600`；
- 只接受所选 provider 的 `{ "type": "api_key", "key": "..." }`；
- 禁止命令型、环境变量型和 OAuth 凭据；
- 运行时禁止修改或删除凭据；
- 审核记录不写认证路径和密钥。

认证文件由操作者在终端中安全创建。本仓库不保存密钥，也不接受把密钥写入命令行、
文档、测试夹具或日志。曾经出现在聊天记录中的密钥应先轮换，再写入专用文件。

无安全认证路径时，候选入口不会创建模型运行时、不会访问网络，也不会修改正式
注册表。

## 4. 无监听预检与完整报告 runner

### 4.1 responseModel vendor patch 固定身份

补丁 ID 为 `pi-ai-openai-completions-response-model-v1`，只针对
`@earendil-works/pi-coding-agent@0.82.1` 内
`@earendil-works/pi-ai@0.82.1` 的固定相对路径：

```text
node_modules/@earendil-works/pi-ai/dist/api/openai-completions.js
```

固定摘要如下：

| 身份 | SHA-256 |
|---|---|
| 补丁前上游源码 | `0d50250fe2931e66e2078279a397814202e1ecddee58faf4b8bc04c278da177a` |
| 补丁后适配器源码 | `5805cc08566c4d9437280f68d996ef0fb452c15e2becb67b94c967b7ace2023b` |
| 补丁制品 | `c62983d07f150ddbef0e412feb596406648f1e151430f633f406ca018e2412cd` |
| 补丁 manifest | `886b0faf7ccbd0dec19ba74aaa3d92e5b6a218177bf36f1f50a5ece553f8bfba` |

应用模式为 `postinstall_exact_hash_replacement_v1`。安装时只允许从固定上游摘要
执行精确替换，或核验已经匹配补丁后摘要的源码。目标包版本、固定路径、普通文件
身份、源码长度、摘要、补丁制品、manifest 或替换片段任一漂移均失败关闭。
`prebuild`、`pretypecheck`、候选预检和风险例外 v2 还会分别核验补丁身份。
补丁不增加网络请求、不增加工具、不改变资源解析或正式能力边界。

风险例外 ID 已更新为
`country-outage-pi-ghsa-mh99-v99m-4gvg-20260812-v2`，到期时间仍为
`2026-08-12T16:00:00Z`。补丁漂移、补丁不再需要、Pi 修复版本可用、正式路径
或能力范围变化时必须立即重新评估。

### 4.2 操作者入口与阶段顺序

构建后可运行无监听预检：

```bash
cd /Users/botongwu/Documents/domeye/core-work/agent-sidecar
npm run build
# COUNTRY_OUTAGE_PI_CANDIDATE_AUTH_PATH 已由受信操作者在当前进程环境中安全注入。
node dist/src/cli/preflight-model-candidate.js
```

预检不会创建 HTTP Server。输出只含候选 ID、候选资源摘要、模型目录边界、预算
上界和安全状态，不含认证路径、凭据或底层响应正文。预检只接受补丁后固定源码
摘要；未打补丁、未知补丁或源码漂移均以适配器不支持错误失败关闭。

操作者使用的无监听完整报告入口已经实现，且现有 20 CNY 授权覆盖五报告套件最坏
包络。A3 当前源码出口和 Hook 已闭合；正式入口仍会在调用时重新核验价格证明、
账本、anchor、风险例外与认证文件元数据。不得把命令存在、构建通过或 readiness
通过解释为模型认证已经通过。

```bash
cd /Users/botongwu/Documents/domeye/core-work/agent-sidecar
# COUNTRY_OUTAGE_PI_CANDIDATE_AUTH_PATH 已由受信操作者在当前进程环境中安全注入。
DOMEYE_API_BASE_URL=http://受信任的Domeye地址/api/v2/ \
DOMEYE_REPORT_PYTHON_EXECUTABLE=/绝对路径/python \
DOMEYE_REPORT_FONT_PATH=/绝对路径/中文字体.ttf \
  npm run certify:model:a4
```

正式认证还要求候选活动账本和独立 tail anchor 已经存在且完全一致。账本目录固定为
`0700`，两个文件固定为当前用户所有的普通 `0600` 文件：

```text
var/country-outage-agent/a4-model-certification-activity/
├── deepseek-v4-flash-pi-0.82.1-v1-activity-v1.jsonl
└── deepseek-v4-flash-pi-0.82.1-v1-activity-anchor-v1.json
```

anchor 固定记录 schema、账本行数、最后一行 SHA-256 和累计承诺成本。每次追加先
写账本并 `fsync`，再通过同目录独占临时文件、`fsync`、原子 rename 和目录
`fsync` 更新 anchor。正式认证只打开和核验已有状态，绝不调用初始化逻辑；账本或
anchor 任一缺失、空账本、尾部截断、旧前缀、anchor 回退或两者不一致，都会在
`ModelRuntime`、Domeye client、Pi session 和 PDF renderer 创建前失败关闭。
若崩溃留下“账本领先于 anchor”，也不会自动猜测或补写。

全新且确定没有任何历史 provider 活动的环境，只能使用零成本初始化入口：

```bash
cd /Users/botongwu/Documents/domeye/core-work/agent-sidecar
npm run initialize:model:a4-activity
```

该命令只允许 ledger 与 anchor 同时不存在，创建一条
`clean_environment_no_prior_provider_activity` genesis，累计成本为零且历史
状态为 `resolved`。任一文件已存在、命令重复执行，或试图对 legacy 状态使用该
入口，都会拒绝。正式认证路径自身永远不会初始化。

已知存在首个历史失败调用时，迁移入口是：

```bash
cd /Users/botongwu/Documents/domeye/core-work/agent-sidecar
npm run reconcile:model:a4-failure
```

该命令不创建 `ModelRuntime`，不访问 provider，不生成报告，也不属于付费认证
路径。它与 clean initializer 不可互换，仅允许两种状态：

1. 账本和 anchor 都不存在，且操作者明确确认存在该历史失败：创建新账本，
   第一行固定写 `pre_ledger_reconciliation`，保留原历史补记值
   `0.10838016 CNY`，标记为 `historical_usage_unresolved`，再创建 anchor；
2. 只有 anchor 缺失，且既有账本严格等于唯一一条合法
   `pre_ledger_reconciliation`：只为既有尾状态创建 anchor，不追加记录、不重复
   收费。

两文件都存在、只有 anchor 存在、既有账本为空或不是上述唯一合法记录时，命令
拒绝执行。完成一次初始化或迁移后再次执行也会拒绝，不能用于普通恢复。

`pre_ledger_reconciliation` 不能自动改按未来两轮上界收费，也不能直接视为
已结清。历史结清有两种互斥依据：供应商实际用量，或供应商最终实扣金额。

若受信操作者取得可核验的历史实际用量和对应证据 SHA-256，可在当前进程环境中
分别注入请求次数、input、output、cacheRead 和 cacheWrite，并运行：

```bash
cd /Users/botongwu/Documents/domeye/core-work/agent-sidecar
# 由受信操作者在当前进程环境中注入以下六项，不写入仓库或命令历史：
# COUNTRY_OUTAGE_PI_PRE_LEDGER_EVIDENCE_SHA256
# COUNTRY_OUTAGE_PI_PRE_LEDGER_PROVIDER_REQUEST_COUNT
# COUNTRY_OUTAGE_PI_PRE_LEDGER_INPUT_TOKENS
# COUNTRY_OUTAGE_PI_PRE_LEDGER_OUTPUT_TOKENS
# COUNTRY_OUTAGE_PI_PRE_LEDGER_CACHE_READ_TOKENS
# COUNTRY_OUTAGE_PI_PRE_LEDGER_CACHE_WRITE_TOKENS
npm run reconcile:model:a4-historical-usage
```

该入口不接受证据文件路径，不读取任意文件，不访问 provider，且终端只输出固定
中文结果，不回显证据摘要、路径、token 或费用。它只允许在唯一一条 unresolved
legacy 记录之后追加一次实际用量结清记录；实际保守成本低于既有
`0.10838016 CNY`、字段非法、证据摘要非法、已存在后续记录或重复执行时均拒绝。

若供应商控制台不提供完整 token 分类，但提供能够绑定本次调用的最终实扣金额，
应使用独立金额结清入口。账单证据必须明确覆盖并已最终结算以下固定调用：

```text
provider / model：deepseek / deepseek-v4-flash
UTC：2026-07-29T03:18:48.543Z 至 2026-07-29T03:19:24.681Z
Asia/Shanghai：2026-07-29 11:18:48.543 至 11:19:24.681
```

只有金额和币种、但无法证明其覆盖该调用窗口的截图、当前账户余额或 pending
账单，不能解除 `historical_usage_unresolved`。允许的账单范围只有“单次调用最终
实扣”或“包含该调用的账户窗口完整实扣上界”；后一种会把整个窗口金额计入预算，
允许多计，不能少计。

```bash
cd /Users/botongwu/Documents/domeye/core-work/agent-sidecar
# 由受信操作者在当前进程环境中注入以下九项，不写入仓库或命令历史：
# COUNTRY_OUTAGE_PI_PRE_LEDGER_BILLING_EVIDENCE_SHA256
# COUNTRY_OUTAGE_PI_PRE_LEDGER_BILLED_AMOUNT_DECIMAL
# COUNTRY_OUTAGE_PI_PRE_LEDGER_BILLED_CURRENCY
# COUNTRY_OUTAGE_PI_PRE_LEDGER_BILLING_SCOPE
# COUNTRY_OUTAGE_PI_PRE_LEDGER_BILLING_FINALITY
# COUNTRY_OUTAGE_PI_PRE_LEDGER_BILLING_EVIDENCE_TIMEZONE
# COUNTRY_OUTAGE_PI_PRE_LEDGER_BILLING_EVIDENCE_WINDOW_START
# COUNTRY_OUTAGE_PI_PRE_LEDGER_BILLING_EVIDENCE_WINDOW_END
# COUNTRY_OUTAGE_PI_PRE_LEDGER_BILLING_EVIDENCE_ACQUIRED_AT
npm run reconcile:model:a4-historical-billing
```

金额只接受规范非负十进制，禁止指数、逗号、符号和货币符号；`1.0`、`1.00`
统一规范成 `1`。币种只接受 `CNY` 或 `USD`。CNY 按 `1:1`；USD 固定按本地预算
政策 `8 CNY/USD`，该值不是实时汇率，也不是供应商结算汇率。换算使用 BigInt
定点整数并向上取整到 `10^-8 CNY`，同时写入并校验换算金额与预算记账金额的
CNY E8 整数。

历史金额预算记账值固定为：

```text
max(0.10838016 CNY, 向上取整后的实扣换算金额)
```

因此供应商实扣低于 `0.10838016 CNY` 时仍可完成结清，但不释放原有保守 floor。
实扣金额较高时也必须如实写账并标记历史状态为 `resolved`；随后仍由 20 CNY
preflight 决定能否继续。五份未来报告的总包络为 `1.0838016 CNY`，所以历史记账值
只有不高于 `18.9161984 CNY` 时才能继续。相同证据和规范化金额重复执行为不追加
记录的幂等读取；证据或金额不同则拒绝。

金额入口不接受证据路径、认证路径、provider、model、汇率或换算后金额，不读取
认证文件，不访问网络，不创建 `ModelRuntime`、Domeye client、Pi session 或 PDF
renderer。证据原件必须在仓库或主机之外按 SHA-256 留存，摘要本身不替代真实性
与范围审核。历史金额结清也不证明下一次调用的供应商价格，价格证明门禁仍需独立
通过。

运维边界必须明确：若 ledger 和 anchor 被同时删除，本机剩余状态无法区分
“全新环境”和“历史被删除”。操作者必须依赖仓库或主机之外的审批与留痕判断应使用
clean initializer 还是历史失败迁移；两者都不得作为自动恢复动作。tail anchor
能发现单文件删除、清空、截断、回退和普通漂移，
但不抵御能够修改程序代码并同时重算替换账本与 anchor 的同 UID 恶意操作者；该
威胁需要仓库或主机之外的不可变可信锚。现阶段也不自动清理 stale lock，锁残留
继续按安全阻断处理。

该命令固定执行
`country_outage/2026-02-27 09:12:32/IR/1/r`，并逐项核验固定的
incident、publication、revision `1`、`dataThrough`、finality、国家 `IR`
和唯一 collector `rrc25`。它不接受其他事件、collector、Skill、模型或输出目录。
当前项目知识身份固定为 `country_outage_report_skill_v6`，确定性基稿与正式 Pi
静态 ResourceLoader 均以同一三文件内容算法计算
`skillBundleSha256=5f108d26f39dea9ff5a2902b00cdb113e3a76a8afd1b6560dffd7e9453d3a88d`。
任一 Skill 文件变化都必须形成新身份，不能沿用旧模型认证或回放结论。

候选预检先于 Domeye 读取、Pi 会话、PDF 渲染和证据目录创建。适配器、风险例外、
认证材料或候选资源任一门禁失败时会在这些动作之前失败关闭。预检通过后，runner
依次执行两份代表性报告和三个认证专用边界场景；每份都新建
`PiReportNarrator`、Pi 内存会话、报告编译器和 PDF renderer，并通过同一固定
Domeye 只读合同生成 `ReportDocument`、Markdown 和 PDF。三个边界场景使用
明确标记为 `certificationOnly=true`、`synthetic=true` 的固定输入，不冒充
Domeye 真实事件事实。

provider、model、独立 `responseModel`、正常请求轮次、retry 和 token usage
只从 `PiReportNarrator` 内部已经验证的审计记录取得。正常请求轮次使用受验证的
provider 转发计数，并要求
`forwardedProviderRequestCount == assistant 消息数 == SessionStats.assistantMessages`；
不得由外层 runner 自报。每条 assistant 消息都必须带完整的安全整数
`input/output/cacheRead/cacheWrite/totalTokens` 和完整非负 cost 回执；
`input + cacheRead + cacheWrite` 与 `output` 都必须大于零，`totalTokens` 必须
等于各项之和，所有逐轮 token 合计还必须逐项精确等于 SessionStats。字段缺失、
适配器默认零、总数不一致或转发数不一致均以 `session_stats_invalid` 拒绝。
retry 固定取自关闭 provider retry 的冻结 Settings 对应审计值，必须为零。

请求轮次的执行时硬门直接包装每个新会话公开的
`session.agent.streamFunction`。正式叙述层不注册工具；首轮完成后只允许一次整包
修复，因此第二轮转发完成后，第三轮必须在调用原始 provider stream 之前以
`provider_request_limit_exceeded` 截断。每轮转发前还会
序列化完整 Context；UTF-8 JSON 超过 `900,000 bytes` 时以
`provider_context_limit_exceeded` 零上游拒绝。该 900,000 字节门只是进入
adapter 前的容量/DoS 上限，不参与费用计算。

DeepSeek 固定候选只接收宿主已经冻结的事实合同和语言槽计划。正式 Pi 会话使用
`noTools=all`、`tools=[]`、`customTools=[]`，并在每一轮发送前组合受控
`onPayload`，最终强制：

```json
{
  "tool_choice": "none",
  "response_format": {
    "type": "json_object"
  }
}
```

组合顺序固定为：先执行既有 payload hook，再核验返回值是保留 `model`、
`messages` 和 `stream=true` 的普通对象，最后覆盖 `tool_choice` 和
`response_format`。任何数组、Date、类实例、
字段缺失或 hook 异常都失败关闭；再由最外层发送前门序列化最终 payload，超过
`59,904 UTF-8 bytes` 时以
`provider_payload_limit_exceeded` 在 HTTP 前拒绝。59,904 字节与 4,096 token
framing 预留共同服从 64,000 单请求输入预算；由于 DeepSeek 官方 tokenizer 与
服务端 framing 尚未固定，这是保守工程假设，不是精确 token 换算证明。只有新
payload 已成功构造才增加结构化输出审计计数。任何轮次都不得绕过零工具和 JSON
对象约束。

真实安装在 Pi `0.82.1` 依赖树中的 `openai-completions` adapter 已用假 API Key
和 loopback `baseUrl` 完成 HTTP 序列化测试：本地服务实际捕获的
`/v1/chat/completions` POST JSON 包含上述字段。该测试不访问 DeepSeek，不读取
正式认证文件，只证明当前安装适配器会使用 `onPayload` 返回值。

安全审计 schema 为 `country_outage_pi_run_audit_v3`。对 DeepSeek 固定候选，
`runtimeSecurity.structuredOutput` 必须记录：

```json
{
  "applicability": "required",
  "mechanism": "deepseek-json-object-no-tools-v2",
  "payloadPreparedCount": 1
}
```

示例计数只表示下界，不是所有运行的固定值。实际候选通过要求计数至少为 1、不得
超过 provider 转发数；非适用模型必须明确记录 `not_applicable`，不能混写成
“未触发”。候选 run manifest 同时保存机制和实际计数，并将其纳入
`runEvidenceId` 和最终 `evidenceId`。

宿主先从确定性事实生成完整 v5 基稿，再建立固定语言槽计划。模型只能返回
`country_outage_language_slots_v1`，逐项改写白名单解释段落；槽 ID、顺序、
数量和字段必须精确，槽正文不能携带事件数字、时间、ASN、方向词、URL、Markdown
结构或越界结论，并必须覆盖对应语义锚点。宿主原子合并全部语言槽后核验未授权块
字节不变、事实与证据引用不变，再对整份报告重新运行 v5 校验。槽缺失、额外字段、
顺序漂移、语义锚点缺失、合并不变量失败或终检失败时均不发布部分结果。

`country_outage_report_validator_rules_v5` 拒绝空白阅读内容、空章节、未声明
字段、无证据数字、国家身份漂移、英文叙事、方向矛盾以及全国性中断、用户或业务
影响、原因、责任、完全恢复和事件结束等越界结论。若首轮槽包结构失败且仍有请求
额度，只允许在同一零工具会话进行一次整包修订；不重新读取数据、不局部
修补，也不追加第二轮修订。

Pi 审计的 `narration` 对象必须同时记录
`mode=deterministic-base-with-language-slots-v1`、槽合同版本、请求/接受槽数、
`baseV5`、`mergeInvariant`、`finalV5` 和 `modelOutputApplied=true`。这些字段进入
持久化清单复核，不能只靠文件摘要证明模型参与和终检通过。

五份都通过后，证据以临时目录、独占文件、同步落盘和原子 rename 写入：

```text
artifacts/country-outage-agent/a4-model-certification/<evidenceId>/
├── manifest.json
├── run-1/
│   ├── report-document.json
│   ├── audit-manifest.json
│   ├── pi-run-audit.json
│   ├── report.md
│   └── report.pdf
├── run-2/
    ├── report-document.json
    ├── audit-manifest.json
    ├── pi-run-audit.json
    ├── report.md
    └── report.pdf
├── scenario-capability-degraded-final/
├── scenario-direction-end-above-start-final/
└── scenario-non-final-snapshot/
```

三个场景目录各自包含 `CERTIFICATION-ONLY.txt`、报告、报告审计、Pi 审计、
Markdown 和 PDF。

最终目录已存在、父路径发生符号链接或 realpath 漂移、任一摘要不一致时均拒绝
覆盖。每轮文件写完后会从磁盘回读并逐项核对 ReportDocument、报告审计、Pi
运行审计、Markdown 和 PDF 摘要，再发布最终目录。同一 evidence lock 已由其他
进程持有时，失败进程不会删除并发方的锁。任一报告运行失败时不会创建证据目录。

`audit-manifest.json` 是
`country_outage_report_audit_manifest_v1`，记录报告实际引用的安全证据投影、
能力边界、校验结果和报告身份。`pi-run-audit.json` 使用
`country_outage_pi_run_audit_v3` 并逐字段复制白名单，只记录候选
模型身份、固定输入身份、工具名称与计数、运行安全开关、模型尝试次数、响应模型、
语言槽终检结果及 token/request 统计；不记录提示词、回答正文、工具参数、工具
结果、session ID、认证路径或密钥。两个文件的实际字节 SHA-256 都写入每轮认证
证据。

命令只输出证据 ID、相对目录、候选 provenance 和保守成本，不输出认证路径、
密钥、提示词、模型原始回答、会话消息、工具参数或工具结果；它不会监听端口，
也不会自动晋级正式注册表。

底层通用 `runPiModelCandidateCertification()` 只用于候选框架机械测试，其清单
固定标记为 `candidate-framework-test-runner-v1`、`promotable: false`。带任何
测试依赖注入的完整 runner 也固定标记为
`country-outage-full-report-integration-test-v1`、`promotable: false`。
只有上述不带注入、实际连接 Domeye、Pi 和 PDF 的固定 CLI 路径才能产生
`country-outage-full-report-runner-v1` provenance；仍须全部认证门通过。

## 5. 五报告认证套件的通过条件

每次运行必须同时满足：

- 审核身份明确为 `candidate`；
- 实际 provider 和 model 与候选一致；
- 独立 `responseModel` 存在且等于 `deepseek-v4-flash`；
- 完整报告生成完成；
- 报告 validator 通过；
- `report-document.json` 生成成功且有按实际落盘字节计算的 SHA-256；
- `audit-manifest.json` 与白名单 `pi-run-audit.json` 均生成成功并有 SHA-256；
- Markdown 制品生成成功且有 SHA-256；
- PDF 制品生成成功且有 SHA-256；
- 每个 assistant 请求的 `input + cacheRead + cacheWrite` 不超过 64,000 token，
  `output` 不超过 16,384 token；全会话聚合上限按实际 provider 请求数分别乘以
  这两个单请求上限；
- 每份报告允许 1 至 2 个正常 provider 请求轮次；第二轮只能用于首轮槽包未通过
  本地机器校验后的整包修复，不属于 retry；
- provider 实际转发数、assistant 消息数和 SessionStats assistant 数必须相等；
  每轮 input-like 与 output 均大于零，逐轮 token 回执完整且合计与
  SessionStats 精确相等；
- 第三轮必须在上游调用前被硬门拒绝；单轮 Context UTF-8 JSON 不得超过
  900,000 bytes，最终 adapter payload 不得超过 59,904 UTF-8 bytes；
- transport/provider retry 必须为 0；
- 每个 provider 请求都必须完成 `tool_choice=none` 与 JSON object payload 构造，
  机制和次数必须进入 Pi 审计与候选 manifest，且次数等于 provider 转发数；
- 报告必须通过 `country_outage_report_validator_rules_v5`，包括空白内容、
  未声明字段、五类关键数字分别覆盖、所有可发布文本块的数字证据，以及指标、
  时间、地址族、单位、冻结国家身份、中文、变化方向和控制面语义边界约束；
- 成本在 20 CNY 总预算内。

input-like 上限同时在 Pi 会话逐请求审计、全会话聚合校验和候选认证清单校验中
执行，不能通过把输入转移到 cacheRead 或 cacheWrite 绕过。

两次代表事件运行还必须使用等价模型输入：`factSetId`、快照 SHA-256，以及整个
`ReportEvidenceBundle` 的 canonical SHA-256 完全一致。最后一项覆盖完整事实
结构和所有已分页 ASN 证据；即使 snapshot 与 `factSetId` 相同，只要 ASN 页内容
发生漂移也会拒绝认证且不写证据目录。两份代表报告文字可以不同，但不能用不同事实
或 ASN 明细拼接成认证结果。

随后三个认证专用场景必须依次覆盖能力降级、窗口结束高于起点和非最终快照。
这些场景制品均标识 `certificationOnly=true`、`synthetic=true`，只能用于模型
认证，不能作为用户报告或 Domeye 事件事实。任一报告失败时立即停止后续报告，
不生成可晋级清单或证据目录，正式注册表保持原样。

## 6. 认证清单与机械晋级

五份完整报告都通过后，框架生成
`country_outage_pi_model_certification_manifest_v1`。清单只记录白名单字段：

- 候选资源 SHA-256；
- 当前适配器源码 SHA-256；
- 两次代表运行与三个边界场景的安全证据 ID；
- provider、model 和独立 `responseModel`；
- JSON object 结构化输出机制与每轮成功构造 payload 的次数；
- ReportDocument、报告审计、Pi 运行审计、Markdown、PDF、事实快照和完整模型
  证据输入摘要；
- token 与保守成本；
- 晋级前正式注册表版本和 SHA-256。

清单不包含密钥、认证路径、提示词、回答正文、工具参数或工具结果。

runner CLI 只生成候选证据，不自动调用晋级。面向操作者的机械晋级入口为：

```bash
cd /Users/botongwu/Documents/domeye/core-work/agent-sidecar
npm run promote:model:a4 -- \
  --evidence-id 'evidence:model-certification:<64 位摘要>' \
  --registry-version 'deepseek-v4-flash-certified-v1'
```

该入口不接受任意路径，只接受固定格式的 `evidenceId`，并从仓库内固定
`artifacts/country-outage-agent/a4-model-certification/<evidenceId>/` 回读
`manifest.json`、两轮代表事件 ReportDocument，以及三个固定场景目录中的报告
审计、Pi 审计、Markdown 和 PDF。
证据目录、子目录和文件必须是当前用户所有、不可由组或其他用户写入的普通路径；
证据文件必须为 `0600`，不允许符号链接、额外文件、缺失文件或非规范清单字节。
全部摘要重新核对后，才调用底层 `promotePiModelCandidate()`。以下条件必须同时
成立：

1. 清单 provenance 必须是固定真实完整报告
   `country-outage-full-report-runner-v1`，固定 A4 fixture 且
   `promotable: true`；generic fake runner 和集成 mock 清单明确拒绝；
2. 固定落盘目录、清单结构、两轮代表事件和三个场景的全部文件摘要没有被删改；
3. 恰好存在五份完整通过证据，场景集合和输入范围与冻结值一致；
4. 五份 `responseModel` 均为冻结值；
5. 五份结构化输出机制与次数均存在于 manifest，并与落盘 Pi 审计逐字段一致；
6. validator、ReportDocument、PDF、Markdown 和事实等价检查均通过；
7. 当前适配器能够保留同名 `responseModel`，且源码摘要与认证清单一致；
8. 正式注册表仍与运行前版本和 SHA-256 相同；
9. 首次晋级时注册表不存在同名 profile；重新认证续期时只允许恰好一个同名已认证
   profile，并要求 provider、model、modelVersion、responseModel、thinkingLevel、
   Pi 版本、可变别名类型、场景集和输入范围全部不变；新证据 ID 必须不同，
   `certifiedAt` 与 `certificationValidUntil` 必须严格向后推进，注册表版本也必须
   更新。任一条件不满足均以冲突失败关闭且注册表零写入。

原子更新先在同目录写入临时文件、同步落盘、再次核验旧注册表，再执行 rename。
任一检查失败都不会部分写入。首次晋级追加固定 profile；重新认证续期只在原位置
原子替换该 profile，不保留两个同名条目，也不允许借续期改变模型身份或缩短认证
有效期。晋级后，正式 profile 的证据 ID 指向完整认证清单，候选资源本身仍保持
`candidate`。

该 CLI 已通过成功晋级、摘要篡改、清单符号链接、额外文件、路径逃逸，以及
“重算全部摘要和证据身份但 Pi 审计语义与 manifest 不一致”的离线测试。持久化
读取在摘要核验后独立解析五份 `pi-run-audit.json`；摘要正确不能替代语义一致。

本次已使用固定真实 evidence 执行机械晋级，注册表中恰好有一个 profile：

```text
deepseek-v4-flash-pi-0.82.1-v1
```

该 profile 指向上述 `b50f...` 认证 evidence，状态为 `certified`。晋级没有调用
provider，也没有改变候选资源、报告事实或认证制品；注册表变化只属于本地候选
工作树，不得写成生产部署。

## 7. 当前真实运行、账本和执行边界

### 7.1 认证结果

| 项目 | 当前结果 |
|---|---|
| 认证状态 | `passed` |
| 可晋级 | `true` |
| 代表性报告 | 2 / 2 通过 |
| 认证专用边界场景 | 3 / 3 通过 |
| provider 请求 | 每份 2 次，共 10 次 |
| provider retry | 0 |
| 实际保守成本 | `0.08085616 CNY` |
| 报告规范 | `country_outage_report_spec_v1` |
| 项目知识 | `country_outage_report_skill_v6` |
| 校验规则 | `country_outage_report_validator_rules_v5` |
| Pi 审计 | `country_outage_pi_run_audit_v3` |

五份报告的 `responseModel` 都精确为 `deepseek-v4-flash`；Pi 审计只记录白名单
元数据，未记录密钥、认证路径、提示词、报告正文、工具参数或工具结果。代表性两份
报告使用相同 `factSetId`、快照摘要和证据输入摘要；三个边界场景分别验证能力
降级、结束高于起点和非最终快照。

五份 PDF 共 19 页已按 150 DPI 全量渲染并逐页检查，未发现裁切、重叠、乱码、
缺字、异常空白页或表格溢出；Markdown、报告文档和 PDF 的标题、方向、能力降级、
非最终状态及控制面限制一致。QA 结果位于该认证 evidence 下的：

```text
pdf-visual-qa/PDF视觉与可读性QA结果.md
```

PDF 的 `Tagged` 仍为 `no`，该结果不替代真实读屏器验收。

### 7.2 当前账本与价格证明

价格证明 ID：

```text
price-attestation:ca39b52507afea65d4ee1573dfc770b7e38f310e472d379425c4a419582c71f8
```

证明记录缓存输入 `0.0028`、普通输入 `0.14`、输出 `0.28 USD / 百万 token`，
有效至 `2026-07-31T07:03:04.000Z`。认证开始、五份报告完成和最终 manifest
完成时间均落在有效期内。

最新只读 readiness：

```json
{
  "checkedAt": "2026-07-30T09:40:39.213Z",
  "ready": true,
  "blockers": [],
  "activity": {
    "committedCostCny": 14.239659840000002,
    "remainingBudgetCny": 5.760340159999998,
    "openReservations": 0,
    "recordCount": 34,
    "historicalUsageStatus": "resolved"
  },
  "safety": {
    "readOnly": true,
    "credentialsRead": false,
    "networkAccessed": false
  }
}
```

20 CNY 总上限、历史记录和 tail anchor 均保留。以后任何真实调用仍须重新检查
当时的价格证明、认证有效期、风险例外、账本和发送前 64K 硬门；本次认证不构成
长期无限额调用授权。

### 7.3 本地晋级与剩余边界

本地机械晋级已经完成，注册表只包含一个已认证 profile。2.1 认证完成时的源码
串行全量回归为 `547 / 547` 通过；这是历史认证时点证据，不冒充 2.2 编排迁移后
的当前测试计数。

仍须遵守：

1. 不得把本地注册表变化写成生产部署；
2. 不得删除、重置、回退或重算 ledger/anchor；
3. 不得把可变模型别名写成不可变权重版本；
4. 认证到期、Pi/补丁/Skill/校验规则/正式路径变化时重新评估；
5. 五报告认证和机械晋级只证明核心模型组合，不覆盖外部证据能力包；
6. 生产身份、生产 ACL 和特定真实读屏器仍按本轮约定记为非阻断、未验收。

### 7.4 2.2 编排迁移后的认证复用判定

编排迁移没有直接以“代码改动看起来不涉及模型”为由复用认证，而是与以下基线做
逐文件身份比较：

```text
artifacts/country-outage-agent/
a3-v6-current-source-20260730T165806+0800/source-end-manifest.json
```

比较严格沿用用户确认的六组：

1. DeepSeek 模型与 API adapter；
2. Pi `0.82.1` 依赖身份；
3. 三个只读工具及 schema；
4. Skill、提示词、报告规范和 validator；
5. RRC25 事实合同和上下文构造；
6. timeout、retry 和 token 上限。

六组所涉及的 21 个文件与基线比较结果为 `21 / 21` SHA-256 不变。已认证
profile、正式注册表和认证 evidence 也保持原身份；后面三项只是附加核对，不能
替代六组源码身份。

因此，本轮没有重新付费执行完整五报告套件，而是按影响面完成：

- Sidecar 全量测试 `545 / 545`；
- 前端全量测试 `181 / 181`，并通过 typecheck、生产 build 和 `api:types`；
- 后端全量测试 `218 / 218`，只有 1 条既有 pandas warning；
- `backend/core.sha256` 为 `14 / 14`；
- readiness、external question 和 external appendix 三条外围路径在
  `not_configured/disabled` 下完成零模型检查，前后 Pi 审计行数不变；
- 使用同一 `deepseek-v4-flash-pi-0.82.1-v1` profile 完成一次真实核心成功
  冒烟。

真实冒烟证据位于：

```text
artifacts/country-outage-agent/
a5-core-v1-joint-20260730T190548+0800/
```

该目录的 Pi 审计透明保存两条 accepted 记录。第一次报告生成成功后，PDF 单项
因系统 Python 缺少 `reportlab` 失败；第二次只把既有
`DOMEYE_REPORT_PYTHON_EXECUTABLE` 指向已具备依赖的解释器后完整成功。两次均
只执行 `country_outage_resolve` 和 `country_outage_get_observation`，
未授权工具尝试为 0，provider retry 为 0。

这次复用判定只适用于核心编排迁移。外部能力包当前为
`draft/not_configured/未验收`，没有参与模型输入、五报告认证、上述真实调用或
核心结论。未来实现 Evidence Gateway 或来源适配器时，应按外部能力包的 E0—E2
独立认证；不得把外部材料写入 Domeye 事实、核心 Pi 提示词或既有模型认证清单。
