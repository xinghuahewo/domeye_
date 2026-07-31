# 国家中断报告 Agent DeepSeek 价格证明门禁

## 1. 目的

A4 真实模型认证属于付费操作。候选资源中冻结的价格用于预算上限，但冻结值不能证明供应商在认证发生时仍按该价格计费。

本门禁增加一份短期、可追溯的供应商价格证明。真实 A4 runner 只有在以下条件同时成立时才可继续：

- 价格证明存在于固定路径；
- 文件是当前用户所有的普通文件，权限严格为 `0600`，不是 symlink 或硬链接；
- 证明绑定当前候选 ID、候选资源 SHA、供应商 `deepseek` 和模型 `deepseek-v4-flash`；
- 四类 token 单价、币种、计价单位、观测时间、到期时间和证据 SHA 完整；
- 观测时间不在未来，证明尚未过期；
- 任一价格不高于候选资源中的冻结值；
- 认证开始时间和完成时间均落在证明有效期内。

该门禁只控制 A4 DeepSeek 付费认证，不扩大 Agent 工具、网络、文件或事件范围。

## 2. 固定资源

价格证明只允许写入：

```text
var/country-outage-agent/a4-provider-price-attestation/
└── deepseek-v4-flash-pi-0.82.1-v1-price-attestation-v1.json
```

相对路径固定为：

```text
var/country-outage-agent/a4-provider-price-attestation/deepseek-v4-flash-pi-0.82.1-v1-price-attestation-v1.json
```

安全约束：

- 仓库根目录必须是绝对路径、当前用户所有、非 symlink，且不可被组或其他用户写入；
- 专用目录权限固定为 `0700`；
- 证明文件权限固定为 `0600`，必须是当前用户所有、单链接普通文件；
- 读取使用 `O_NOFOLLOW`；
- 写入使用独占 lock、`O_EXCL` 临时文件、文件 `fsync`、原子 `rename` 和目录 `fsync`；
- CLI 不接受证明文件路径、证据文件路径或输出路径。

## 3. 冻结结构

schema 为：

```text
country_outage_provider_price_attestation_v1
```

资源至少包含：

- `attestationId`
- `candidateId`
- `candidateResourceSha256`
- `provider`
- `model`
- `currency`
- `billingUnit`
- `priceUsdPerMillionTokens.input`
- `priceUsdPerMillionTokens.output`
- `priceUsdPerMillionTokens.cacheRead`
- `priceUsdPerMillionTokens.cacheWrite`
- `observedAt`
- `expiresAt`
- `validitySeconds`
- `evidence.type`
- `evidence.sha256`

固定语义：

- 币种固定为 `USD`；
- 计价单位固定为 `per_1_million_tokens`；
- 证据类型固定为 `provider_pricing_snapshot`；
- 有效期固定为 24 小时；
- `expiresAt` 由程序按 `observedAt + 24 小时`生成，运维人员不能自行指定；
- `attestationId` 是完整证明正文的规范化摘要；
- `resourceSha256` 是固定 pretty JSON 加结尾换行的完整文件字节摘要。

manifest parser 会重建完整证明资源并重新计算 `resourceSha256`，不能用任意 64 位十六进制字符串冒充资源摘要。

## 4. 运维写入入口

显式入口：

```bash
cd /Users/botongwu/Documents/domeye/core-work/agent-sidecar
npm run attest:model:a4-price
```

入口只读取以下环境标量：

```text
COUNTRY_OUTAGE_PI_PRICE_OBSERVED_AT
COUNTRY_OUTAGE_PI_PRICE_EVIDENCE_SHA256
COUNTRY_OUTAGE_PI_PRICE_INPUT_USD_PER_MILLION
COUNTRY_OUTAGE_PI_PRICE_OUTPUT_USD_PER_MILLION
COUNTRY_OUTAGE_PI_PRICE_CACHE_READ_USD_PER_MILLION
COUNTRY_OUTAGE_PI_PRICE_CACHE_WRITE_USD_PER_MILLION
```

其中：

- `OBSERVED_AT` 必须能转换为 UTC 时间，且不得晚于执行时刻；
- `EVIDENCE_SHA256` 是供应商价格页面截图、导出或其他留存证据的 SHA-256；
- 四项价格必须是非负十进制字符串；程序会去除无意义的末尾零，并保留最多 64 位小数、总长不超过 128 字符；
- 任何一项高于候选冻结价格时，写入失败并要求重新预算。

CLI 和资源层不会把价格转换为 JavaScript `Number`。比较使用 `BigInt` 定点十进制，因此类似 `0.1400000000000000001` 的微小上调也不能被浮点舍入为 `0.14` 后通过。

入口不接受 `argv`，不读取证据文件，不读取认证文件，不输出密钥、认证路径或证据内容。成功输出只包含证明 ID、证明资源 SHA、观测时间和到期时间。

## 5. 真实 A4 runner 顺序

真实认证要求价格证明至少还剩 15 分钟有效期。入口在打开活动账本之前执行第一次检查，并在读取 auth、创建 `ModelRuntime` 之前重新取时执行第二次检查。顺序固定为：

```text
加载冻结候选资源
  → 读取并验证价格证明
  → 打开并验证活动 ledger/anchor
  → 验证 auth
  → 创建 ModelRuntime
  → 读取 Domeye 固定 A4 样本
  → 发起 DeepSeek 请求
```

因此，价格证明缺失、过期、在未来、候选漂移、文件不安全或价格上调时：

- 不读取 auth 文件；
- 不创建 ModelRuntime；
- 不创建 Domeye client；
- 不创建 Pi session；
- 不发起 DeepSeek 或其他网络请求；
- 不修改 ledger、anchor、正式注册表或认证制品。

剩余有效期不足 15 分钟也按失败关闭处理，避免证明在本地预检期间到期后才读取 auth 或创建模型运行时。模型每轮及最终完成时仍会核对真实完成时间；若认证异常拖延到证明过期，结果不会形成可晋级清单。

价格证明通过后，其身份、资源 SHA、证据 SHA、观测时间、到期时间和四类价格会进入：

- `CandidatePiModelPreflight.priceAttestation`
- `PiModelCertificationManifest.policy.priceAttestation`
- 最终 manifest 的 `evidenceId` 计算输入

manifest 同时记录 `certificationStartedAt`。解析及晋级时重新验证：

```text
observedAt <= certificationStartedAt
certificationStartedAt <= 每次报告完成时间
每次报告完成时间 < expiresAt
最终 completedAt < expiresAt
```

通用测试 runner 的 `priceAttestation` 固定为 `null`，其 provenance 本来就不可晋级。任何伪装为真实 A4 provenance、但缺少价格证明的清单都会被 parser 拒绝。

## 6. 预算语义

价格证明是“供应商当前价格没有高于冻结值”的门禁，不是单独下调预算的依据。

即使证明中的价格低于候选资源，以下计算仍使用候选资源的冻结价格：

- 单份报告最坏成本；
- 五报告认证套件总包络；
- 实际 usage 的保守结算；
- 20 元总预算预检。

这样可以避免短期低价或错误证明降低已批准的安全预算。

预算上界仍可因正式运行的可达请求边界收紧而降低，但必须先有发送前硬门、账本
兼容、精确公式和回归证据，不能只修改文档或预算常量。当前正式路径把每次请求的
最终 adapter payload 限制为 59,904 UTF-8 字节，并另留 4,096 token 的 provider
framing 预留；每份报告最多 5 次 provider 请求，输出仍以每次 16,384 token 计。
因此当前未来预留使用 64,000 输入、16,384 输出的单请求上界，而不是模型目录的
1,000,000 token 上下文能力。900,000 字节 Context 门继续只承担进入 adapter
前的容量/DoS 防护。

DeepSeek 官方 tokenizer 和服务端 chat framing 尚未随候选资源固定，所以上述
59,904 字节加 4,096 framing 预留是保守工程假设，不是精确 tokenizer 证明。
价格证明中的单价没有因此降低，20 CNY 总上限和既有历史账本也没有重置或改写。

## 7. 纯只读 readiness

只读状态入口：

```bash
cd /Users/botongwu/Documents/domeye/core-work/agent-sidecar
npm run status:model:a4-readiness
```

业务状态检查本身：

- 不读取 auth；
- 不创建 ModelRuntime；
- 不访问 Domeye、DeepSeek 或其他网络；
- 不创建 ledger lock；
- 不写价格证明、ledger、anchor、注册表或认证制品。

`npm run status:model:a4-readiness` 会先执行 TypeScript 构建，因此会更新本地 `dist`。这里的“只读”限定为不改变价格证明、活动账本、anchor、认证注册表和报告制品，不宣称整个文件系统零写入。

输出同时报告价格证明和活动账本状态，能够区分：

```text
price_attestation_missing
price_attestation_expired
price_attestation_future_observation
price_attestation_insufficient_runway
price_attestation_candidate_drift
price_attestation_rebudget_required
price_attestation_invalid
historical_usage_unresolved
activity_budget_preflight_failed
activity_ledger_invalid
```

存在任一 blocker 时进程以非零状态退出。该输出只是只读 readiness 诊断，不替代正式 runner 中带锁的预算门禁。

## 8. 验证结果

### 8.1 历史离线验证

离线测试覆盖价格证明缺失、过期、未来时间、四类价格上调、格式错误、权限错误、
symlink、候选漂移、精确十进制比较、15 分钟最小剩余有效期、auth 前二次取时、
资源摘要篡改和 readiness 不创建 ledger lock。

### 8.2 本次真实认证门禁结果

`2026-07-30T07:03:04.000Z` 取得的官方价格证明记录：

| 项目 | USD / 百万 token |
|---|---:|
| 普通输入 | `0.14` |
| 缓存输入 | `0.0028` |
| 输出 | `0.28` |
| cache write | `0` |

证明 ID 为：

```text
price-attestation:ca39b52507afea65d4ee1573dfc770b7e38f310e472d379425c4a419582c71f8
```

资源 SHA-256 为
`4e3c7991b333d82daf70efa4239e863bc296f6770dccd64900066eda686e3026`，
有效至 `2026-07-31T07:03:04.000Z`。真实 A4 五报告认证从
`2026-07-30T09:19:12.493Z` 运行至 `09:19:34.010Z`，全部运行与最终 manifest
均在证明有效期内。

认证最坏包络仍为 `2.709504 CNY`，实际保守结算为
`0.08085616 CNY`。认证后 `2026-07-30T09:40:39.213Z` 的只读 readiness
结果为 `ready=true`、`blockers=[]`；累计保守承诺 `14.23965984 CNY`，剩余
`5.76034016 CNY`，未结预留为 0。20 CNY 总上限和历史账本没有重置或改写。

随后在同一有效价格证明窗口内完成了一次已晋级正式 profile 浏览器报告旅程。
该次正式运行的 Pi 审计估算费用为 `0.0001449056 USD`，按冻结汇率折合
`0.0011592448 CNY`。它没有回写或伪装成 A4 candidate 活动账本记录；只在项目
20 CNY 总预算口径中单独计入。因此当前累计保守承诺为
`14.2408190848 CNY`，剩余 `5.7591809152 CNY`。正式旅程证据位于：

```text
artifacts/country-outage-agent/
a4-formal-profile-journey-20260730T174914+0800/
正式模型浏览器旅程验收说明.md
```

当前 Sidecar 串行全量回归为 `547 / 547` 通过。该结果与真实认证共同证明本次价格
门禁按设计工作；仍不表示以后调用可以复用过期价格证明，也不表示 A4 或 A5 整体
已经通过。

## 9. 剩余运维边界

代码只保存证据 SHA，不判断截图或导出内容是否真实。实际写入前仍需人工确认：

- 证据来自 DeepSeek 官方价格页面或官方账单说明；
- 证据时间与 `observedAt` 一致；
- 四项单价、币种和计价单位抄录无误；
- 证据原件按项目证据留存规则保存。

价格证明超过 24 小时后必须基于新的官方证据显式更新。Pi 依赖风险例外、历史首轮用量结清和生产身份门禁仍是独立门禁；价格证明通过不代表 A4 或 A5 已通过。

价格写入口使用 `O_EXCL` lock。若进程遭遇 `SIGKILL` 或主机掉电，lock 可能残留；当前实现选择持续 fail-closed，不自动判断或清理 stale lock。受信操作者必须先核验没有仍在运行的写进程、没有待完成的临时写入，再按运维流程处理残留 lock，禁止为了恢复可用性直接加入不安全的自动过期清理。

本门禁证明的是“A4 五报告付费认证套件运行期间价格有效”。模型晋级本身不调用供应商，因此允许在证明到期后基于当时有效且 parser 可复核的历史 manifest 晋级。正式生产模型的每次后续调用是否需要新的逐次价格门禁，属于生产计费治理，不在本次 20 元 A4 付费验收范围内；本实现不把当前 A4 证明夸大为长期生产价格授权。
