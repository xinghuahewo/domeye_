# 国家中断 Agent 正式 Pi 审计日志运维说明

## 1. 结论与适用范围

国家中断 Agent 的正式 Pi 入口必须把运行安全审计写入 Sidecar 专用目录，不再以 stdout 作为正式留存介质。审计层只记录已经通过白名单清洗的运行元数据，不记录提示词、模型回答正文、工具参数或结果、认证文件内容、API Key、Cookie、令牌和风险例外批准正文。

本能力只覆盖国家中断报告正式 Pi 运行审计，不改变 Domeye 数据、不修改 `backend/core`，也不扩大 Agent 的工具、路径或网络能力。

在 2.2 核心验收口径中，Pi 审计固定属于 `core-v1`，外部证据能力为
`disabled/not_configured`。核心 Agent 不直接联网，外部 Provider、Evidence
Gateway、来源 URL、网页摘要和外部附录都不得进入核心 Pi 会话或核心 Pi 审计。
外部能力包未来启用时必须使用独立审计合同和独立验收证据，不能复用本日志宣称
外部读取已经通过。

## 2. 启动配置

正式环境必须设置：

```text
COUNTRY_OUTAGE_PI_AUDIT_DIRECTORY=/var/log/domeye/country-outage-pi-audit
```

该目录必须由运维预先创建，并同时满足：

- 使用无 `..`、无尾随分隔符的绝对规范路径；
- 目录自身及祖先路径均不经过符号链接或路径别名；
- 是普通目录，由 Sidecar 当前运行用户所有；
- 权限严格为 `0700`；
- 只供本组件使用，不能把仓库目录、用户主目录、系统日志根目录或共享目录作为目标。

示例中的实际服务账号应由部署环境确定：

```bash
install -d -m 0700 -o domeye-agent -g domeye-agent /var/log/domeye/country-outage-pi-audit
```

配置缺失、目录不存在、权限过宽、所有者不匹配、路径为符号链接或真实路径发生漂移时，正式 Sidecar 会在模型预检和 HTTP 监听之前失败关闭。Sidecar 不会自行创建、接管或放宽目录权限。

## 3. 文件与写入合同

日志按 UTC 自然日写入：

```text
country-outage-pi-run-audit-v1-YYYY-MM-DD.jsonl
```

每个文件必须是当前 Sidecar 用户所有、权限严格为 `0600` 的普通文件，且只能有一个硬链接。写入使用 `O_NOFOLLOW | O_APPEND`，每条清洗后的 JSON 记录以一次追加写入并执行同步落盘。同一进程内的并发记录串行进入追加队列，避免产生交错行。

如果当日精确命名文件已经是符号链接、目录、宽权限文件或多硬链接文件，Sidecar 会拒绝写入。每条记录必须恰好占一行、以 LF 结束且不超过 64 KiB；原始换行注入、CR 或超限内容会被拒绝。

正式审计记录允许的内容仅包括：

- 记录 schema `country_outage_pi_run_audit_v3`；
- 记录时间、结果、拒绝码；
- 正式或候选运行身份；正式运行绑定注册表版本和认证 evidence；
- 已认证模型注册表、profile、provider、model 和 Pi 版本身份；
- 期望与实际返回的 provider、model、`responseModel` 和停止原因；
- 事件引用哈希、事件和快照身份、唯一 `rrc25` collector；
- 固定 Skill、验证器和安全运行配置身份；
- 确定性基础报告、语言槽合同、请求/接受槽数、合并不变量和最终规则校验结果；
- 模型语言槽是否实际进入最终报告，不记录槽正文；
- 结构化输出是否适用、固定机制标识和成功构造 payload 的次数；
- 模型尝试上限、实际尝试次数、provider 请求数和 provider retry 数；
- 已执行工具名称和次数，不含工具参数或结果；
- token、消息数和估算费用等数值；
- 风险例外 ID、截止时间和状态，不含 advisory、依赖细节或批准正文。

核心审计中的“external disabled”是能力口径，不是把外部字段塞进 Pi 审计正文：

- `country_outage_pi_run_audit_v3` 只记录实际进入 Pi 的核心运行；
- 当前 v3 schema 不记录外部 URL、网页标题、摘要、正文、来源 hash 或附录；
- 编排 readiness 必须另行记录
  `capability=external_evidence`、`state=not_configured` 和 disabled Provider
  身份，并与同次核心旅程证据一并保存；
- v3 记录没有 external 字段，不能被解释成“外部能力已启用”，也不能被解释成
  “外部能力已验收”；
- 任何外部材料都不是 Domeye 事实，不得进入 `input.factSetId` 所指事实集合、
  RRC25 快照、语言槽事实输入或核心报告校验结果。

## 4. 固定 30 天留存

留存口径固定为“当前 UTC 日及前 29 个 UTC 日”，共 30 个日期文件。以 `2026-07-29` 为例：

- `2026-06-30` 是边界日，继续保留；
- `2026-06-29` 及更早文件属于过期文件，可被清理。

Sidecar 在启动预检时执行一次清理；长驻进程进入新的 UTC 日后，在下一次审计写入前再次清理。留存天数不是环境变量，运维不能在未修改验收合同的情况下延长或缩短。

清理只处理符合固定组件文件名、且通过普通文件、所有者、`0600`、单硬链接检查的过期文件：

- 普通非组件文件永不删除；
- 使用组件前缀但不符合固定格式的近似名字会导致失败关闭；
- 组件精确名字若指向符号链接或其他危险对象，会导致失败关闭而不是跟随或删除目标；
- 无效日期、路径漂移和对象身份漂移均会失败关闭。

这项行为不能替代主机备份、集中日志或合规归档。如果业务要求超过 30 天的合规留存，必须在本目录之外由受控日志系统接收已清洗记录，并另行冻结访问、保留和删除合同。

## 5. 上线检查

上线前至少确认：

1. 以真实 Sidecar 服务用户检查目录真实路径、uid/gid 和 `0700`；
2. 以正式启动命令验证缺失配置、宽权限、符号链接和危险当日文件均在监听前失败；
3. 完成一次受控模型运行，确认当日 JSONL 为 `0600`，每行均可独立解析；
4. 检索测试提示词、回答、工具参数和凭据哨兵值，确认均未落盘；
5. 用受控日期或隔离副本验证第 29 天边界保留、第 30 天之前文件清理；
6. 验证进程重启、UTC 日切、磁盘满、只读文件系统和写入失败时均不会把“审计失败”的运行结果误报为成功；
7. 冻结真实服务身份、目录挂载、磁盘容量、告警、备份边界和回滚证据。

## 6. 证据边界

自动化测试和本地临时目录验证只证明代码候选满足文件安全与 30 天清理合同，**不等于生产日志已经持续留存 30 天**。生产验收仍需真实主机、真实服务用户、真实挂载路径、至少一次正式运行和跨日/留存观测证据。

在这些证据齐备前，只能表述为“正式 Pi 审计日志留存代码候选已实现并通过本地测试”，不能表述为“生产 30 天留存已经验收通过”。

## 7. 当前本地正式 profile 旅程证据

### 7.1 2.1 单报告旅程

2026-07-30 的 2.1 已认证正式 profile 浏览器旅程生成了一条
`country_outage_pi_run_audit_v3` 终态记录：

```text
artifacts/country-outage-agent/a4-formal-profile-journey-20260730T174914+0800/
pi-audit/country-outage-pi-run-audit-v1-2026-07-30.jsonl
```

该记录为 `runtimeIdentity=formal`、`outcome=accepted`，绑定
`deepseek-v4-flash-certified-v1` 和认证 evidence；五个语言槽全部接受，
基础报告、合并不变量和最终规则 v5 均通过，且 `modelOutputApplied=true`。
运行只执行 `country_outage_resolve` 和
`country_outage_get_observation`，未授权工具尝试为 0。

这条本地正式旅程证明当前代码可产生合规 v3 审计记录，但仍不等于生产主机已经
持续留存 30 天。完整旅程说明见：

```text
artifacts/country-outage-agent/a4-formal-profile-journey-20260730T174914+0800/
正式模型浏览器旅程验收说明.md
```

### 7.2 2.2 核心编排迁移旅程

2.2 核心编排迁移后的证据目录为：

```text
artifacts/country-outage-agent/
a5-core-v1-joint-20260730T190548+0800/
```

其中的审计文件包含两条 `accepted` 记录：

```text
pi-audit/country-outage-pi-run-audit-v1-2026-07-30.jsonl
```

两条记录都绑定同一正式认证身份：

- registry：`deepseek-v4-flash-certified-v1`；
- profile：`deepseek-v4-flash-pi-0.82.1-v1`；
- certification evidence：
  `evidence:model-certification:b50f247c7b1322df6d05afa45c5c1078b58349329d9f27ec5800bbfa5770a1d4`；
- 唯一 collector：`rrc25`；
- Skill v6、validator v5、报告规范 v1；
- 只执行 `country_outage_resolve` 和
  `country_outage_get_observation`；
- 未授权工具尝试为 0，provider retry 为 0。

第一次 Pi 报告生成被接受后，PDF 单项因所配置的系统 Python 缺少 `reportlab`
失败；第二次只调整既有 `DOMEYE_REPORT_PYTHON_EXECUTABLE` 后完整成功。审计
保留了两次实际调用，没有因第一次后处理失败而删除记录或隐藏费用。两条记录合计
估算 `0.0002931712 USD`。

本次核心编排显式使用 disabled Provider；readiness 为
`external_evidence=not_configured`。三条外围检查——readiness GET、有效 external
question 和 external appendix GET——前后 Pi 审计行数不变，说明未配置外部能力
不会偷偷触发模型。

需要特别说明：现行 Pi v3 记录本身没有 external 字段。上述 disabled 状态来自
同一旅程的编排/readiness 证据，不应伪装成 Pi schema 内字段。两类证据共同证明
“核心运行时外部能力关闭”，但仍不证明生产部署或外部能力包通过。

## 8. 未来外部能力包的独立审计

如果以后部署 `country-outage-external-evidence-pack-v1`，必须另行冻结外部能力
审计合同。它至少应记录：

- Provider 和 Evidence Gateway 身份；
- 策略版本及摘要；
- 授权请求 ID、来源 URL 与最终 URL 的安全投影；
- `fetched`、`insufficient`、`rejected`、`failed` 等明确结果；
- 取得时间、内容摘要、内容 SHA-256、重定向和限制命中状态；
- 是否生成独立附录及其制品摘要；
- 不记录 Cookie、登录态、凭据、完整网页正文或不必要的个人信息。

外部能力审计必须与核心 Pi 审计分文件、分 schema、分留存和分验收。外部材料只能
是独立旁证，不能被记为 Domeye 观测事实，不能改变 RRC25 指标、方向、窗口、
publication/revision、报告正文结论或同快照追问答案。

外部能力包仍为 `draft/not_configured/未验收` 时，不得创建“成功外部读取”审计，
也不得因为它未部署而把核心 Pi 运行记为失败。
