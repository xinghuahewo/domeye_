# 国家中断报告与追问 Agent A0 基线

版本：1.3
阶段：A0  
状态：2.2 核心基线已冻结；A0 核心剖面 Hook 机检通过
冻结时间：`2026-07-30T11:39:45Z`

> 1.0 基线已被 1.1 取代；1.1 与其四个摘要继续保留为 2.1 合同历史证据，但不得
> 用来证明 2.2 核心剖面。1.2 把核心 Agent 与外部证据能力包解耦：核心配置只冻结
> RRC25 报告、追问、下载和公共网络访问为 `none`；外部 URL、Gateway、Provider、
> 来源适配器与独立附录进入能力包配置。1.3 在合同、Hook 和核心配置稳定后填写四个
> 2.2 摘要；外部能力包仍为独立 `draft`，不进入核心四摘要或 A0 至 A5 结论。

## 一、冻结结论

A0 只冻结前端和报告生成逻辑的验收起点，不实现 Agent。

- 最终效果合同：
  [国家中断报告与追问 Agent 最终验收文档](./国家中断报告与追问Agent最终验收文档.md)
  2.2；
- 阶段合同：
  [国家中断报告与追问 Agent 分阶段计划](./国家中断报告与追问Agent分阶段计划.md)
  2.2；
- 核心量化配置：
  `config/country-outage-agent-core-acceptance-v3.json`；
- 外部证据能力包配置：
  `config/country-outage-external-evidence-pack-v1.json`，不属于核心 A0 四摘要，
  将由 E0 独立冻结；
- Evidence Envelope：
  `contracts/agent/country-outage-external-evidence-envelope-v1.schema.json`，由 E0
  独立冻结；
- 唯一事件类型：已有合法 `country_outage`；
- 唯一观测源：RRC25；
- 触发方式：用户主动触发；
- 数据权限：已发布国家中断观测的只读读取；
- 默认证据模式：仅使用 Domeye 数据；
- 核心公共网络访问：`none`；
- 外部证据能力包：不作为核心验收阻断项；未配置或未验收不得写成通过；
- 会话：短期有效，不建设永久历史；
- 冻结核心：不修改 `backend/core/`。

## 二、代表性事件基线

本阶段只把以下事件作为可复核基线，不把其中数字复制到其他事件：

| 字段 | 冻结值 |
|---|---|
| 事件引用 | `country_outage/2026-02-27 09:12:32/IR/1/r` |
| 事件类型 | `country_outage` |
| 国家 | 伊朗（IR） |
| incident | `incident_go_v1_a1de26f854831330c616a72af21597eb` |
| publication | `publication_v1_38bddead083db3f49023c2e1` |
| revision | `1` |
| 数据截止 | `2026-02-28T15:00:00Z` |
| 最终性 | `true` |
| collector | `rrc25`，且 `collector_count=1` |
| 观测窗口 | 2026-02-28 18:05 至 23:00，Asia/Shanghai |
| 时间槽 | 60 个预期、60 个可用、0 个缺失，5 分钟间隔 |
| 固定人口 | 563 个 origin ASN，384,767 个 Prefix×VP |
| 质量状态 | `pass` |

2026-07-28 的只读运行态核对结果：

- `/api/v2/events/resolve` 返回
  `country_outage_resolution_v2`；
- `overview`、`series`、`asns`、`audit` 均返回 HTTP 200；
- 四个接口返回相同 publication、revision 和 `data_through`；
- `series` 返回 60 个时间槽，ASN 接口声明总数 563；
- `fixed_cohort`、`country_resources`、`update_activity`、
  `address_families`、`asn_matrix` 和 `audit` 可用；
- `normal_band` 因“缺少可信正常参照”不可用；
- 以上只证明当前基线可读取，不证明 Agent、前端、报告、追问或生产发布已经完成。

## 三、回开时的代码能力基线

### 3.1 已形成当前候选

- Python 只读国家中断 API：
  `resolve`、`overview`、`series`、`asns`、`audit`；
- publication、revision、data through、最终性、能力状态和 ETag；
- ASN 服务端分页及地址族、状态、查询和排序参数；
- 事件页中的国家中断数据观测工作台；
- 缺槽、质量、能力、revision 历史和审计的只读展示；
- 前端 Vue 3、TypeScript、Vitest 和 Vite 构建链；
- Node.js Sidecar、固定快照事实装配、报告与追问窄接口；
- ReportDocument、Markdown、PDF、短期会话、SSE、取消和重连候选；
- 只加载固定 Skill、只注册三个只读工具的 Pi 候选路径；
- 外部 URL 只读旁证、安全地址边界和独立附录存在旧 Sidecar 直连候选；该候选
  不能作为 2.2 正式核心装配，也不能作为 `managed-egress-v1` 能力包通过证据。

### 3.2 既有证据与 2.2 闭合结果

2.1 候选已经留下五份 `deepseek-v4-flash` 真实模型报告的认证证据，覆盖两份代表性
报告和三类认证专用边界场景，并完成当时正式 profile 的机械晋级。不得再把这项历史
事实写成“真实报告、模型认证与晋级尚未证明”。该证据证明的是 2.1 当时固定模型、
Pi、Skill、校验规则和源码组合，不证明生产部署，也不能自动证明合同迁移后的
2.2 `core-v1` 当前源码。

2.2 重新冻结后已经完成以下闭合：

- 当前源码的正式核心装配已经移除 Provider、Gateway、DNS、公开 HTTP 和站点解析
  依赖；
- 核心 A4 已按用户确认的六组身份完成 `21 / 21` 文件摘要比较、全量核心回归、
  readiness/Provider/附录零模型检查，以及同一认证 profile 的真实成功冒烟；六组
  未变，因此没有重跑完整五报告认证；
- 当前源码已完成 2.2 `core-v1` A5 联合验收，证据见
  [A5 联合验收记录](./国家中断报告与追问AgentA5联合验收记录.md)；
- 外部证据已迁出核心，`country-outage-external-evidence-pack-v1` 当前仍为 draft；
  E0 至 E2 的应用编排、Gateway 和真实公网认证不计入核心 A4/A5，也不得写成能力包
  已通过；
- 生产身份、生产 ACL 与特定真实读屏器环境不属于本轮阻塞，也不得写成已通过。

## 四、量化验收基线

`config/country-outage-agent-core-acceptance-v3.json` 冻结以下核心类别：

- 30 分钟会话，提前 5 分钟提醒；
- 报告最长 120 秒、追问最长 60 秒；
- 单用户同时 1 个报告运行，全局 8 个，队列最多 32 个；报告生成不设置按用户每小时
  次数上限，供应商欠费或额度耗尽时按既有模型失败路径确定性收口；
- 每份报告最多 5 次 provider 请求；进入 adapter 前的 Context 容量上限为
  900,000 UTF-8 字节，这只是容量/DoS 门，不是计费 token 门；
- 每次 HTTP 发送前都在最终 adapter payload 上执行 59,904 UTF-8 字节硬门，并
  另留 4,096 token 的 provider framing 预留；两者共同服从 64,000 的单请求
  输入预算。该门是未固定 DeepSeek 官方 tokenizer 时的保守工程假设，不宣称
  字节数等于精确计费 token；
- 每会话最多 30 个问题，每分钟最多 6 个问题；
- 单次最多 2,000 条事实，64,000 token 上下文，回答最多 4,000 字符；
- PDF 最多 40 页、10 MiB，Markdown 最多 2 MiB；
- 候选浏览器固定为 macOS Google Chrome `150.0.7871.187`，使用
  1440×900、1024×720、768×1024、390×844 四个验收视口；
- 键盘、语义树和减少动态效果必须验证；特定真实读屏器不作为本轮阻塞；
- 临时报告和下载最长 1 小时，问答最长 30 分钟；
- 运行日志保留 30 天，但不记录提示词和回答正文；
- 所有生成、追问和下载继承事件读取权限，并冻结本地跨用户、跨授权范围和逐入口
  事件能力检查矩阵；
- 核心公共网络访问为 `none`，外部证据能力包不作为核心 A4/A5 阻断项；
- 当前发布规则固定为 `country_outage_report_validator_rules_v5`；
- 代表性验收实例固定为本文件第二节的事件、快照、五分钟间隔和 60 个预期槽。

`config/country-outage-external-evidence-pack-v1.json` 独立保留 2.1 的全部外部量化
细节：外部补充最长 90 秒、临时证据 30 分钟、用户五分钟内确认 1 至 5 个 URL、
只允许 `bgp.he.net` 与 `radar.cloudflare.com` 点边界子域、最多 5 个页面、单页
2 MiB、最多 3 次重定向、不允许私网、登录页和上传，以及 requesting-user-only
权限范围。能力包另外冻结 Domeye 应用编排三接口、Gateway 三层职责、readiness、
Evidence Envelope 和真实产品路径认证条件；这些值不进入核心运行配置。

这些数值是冻结的验收上限。当前 `core-v1` 的达标结论来自 A1 至 A5 的实现、回归
和联合旅程证据，不由本配置文件单独证明。后续若必须修改，需形成新配置版本并重新
执行受影响阶段，不能在验收时无痕调整。

## 五、版本与工作树说明

- 基线 Git HEAD：`a5c1ed5cce5d5c81eb1e0df4d975bd5af446e283`；
- 分支：`codex/rrc25-iran-research-loop`；
- 冻结时工作树包含大量既有修改和未跟踪文件；
- 本 Goal 只修改明确属于国家中断 Agent 的外围文件，不覆盖或清理其他工作；
- 验收文档 SHA-256：`0a6da910eb9b97c495e9fa863cd8cd7a4cea59c2eb78ba2d132dfb4c141a522b`；
- 分阶段计划 SHA-256：`7c61d52d89bac070efb16444d2837fbe71a88e9bbbe36d51bb5de4833a881369`；
- 防偏离 Hook SHA-256：`3222c39907bf2b46413f9fcdb18a667cf45284ef650552ecdba01c435020625c`；
- 核心量化验收配置 SHA-256：`52e77c5e8cc145e820468a5ceaa698fe93004ca4f878c113165c474a33171f81`。

2.1 历史四摘要仍为
`8259f96d3557455ab6eef412c7ad45ffda3f28f196a29f4044de044f51d48a68`、
`e82a258d4d89c090d3f96501d3ef06183c0b704fd87292278e82b70df13dddff`、
`e17304804aff2298b2163e1d1d62b30115fcb990c56821325d4837965ffde477` 和
`40d30cad5f9940fd4fb70d94e57434d107ce49fd997418068aca46313b61ac31`；仅供追溯，
不作为 2.2 A0 证明。

## 六、A0 出口判定

- 两条验收主线已经冻结；
- 代表性事件和当前已有能力已经区分；
- 量化配置已经冻结；
- 未实现项没有被写成通过；
- `backend/core` 必须继续通过 `core.sha256` 校验；
- 四个 2.2 摘要已经填写，且 A0 核心剖面 Hook 已逐项复算通过；该结果只证明
  合同、摘要与静态边界一致，不替代功能、模型或联合旅程验收；
- A0 通过后，A1 至 A5 仍分别以各阶段功能、模型和联合旅程证据为准，不能由 A0
  摘要替代；
- 不得用 1.1 的历史通过结论越过本次 2.2 合同迁移，也不得把核心通过外推为外部
  能力包通过。
