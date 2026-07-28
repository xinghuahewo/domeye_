# 国家中断报告与追问 Agent A0 基线

版本：1.0  
阶段：A0  
状态：已冻结，尚不代表 Agent 功能已经实现  
冻结时间：2026-07-28T13:26:19Z

## 一、冻结结论

A0 只冻结前端和报告生成逻辑的验收起点，不实现 Agent。

- 最终效果合同：
  [国家中断报告与追问 Agent 最终验收文档](./国家中断报告与追问Agent最终验收文档.md)
  2.1；
- 阶段合同：
  [国家中断报告与追问 Agent 分阶段计划](./国家中断报告与追问Agent分阶段计划.md)
  2.1；
- 量化配置：
  `config/country-outage-agent-acceptance-v1.json`；
- 唯一事件类型：已有合法 `country_outage`；
- 唯一观测源：RRC25；
- 触发方式：用户主动触发；
- 数据权限：已发布国家中断观测的只读读取；
- 默认证据模式：仅使用 Domeye 数据；
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

## 三、现有代码能力基线

### 3.1 已存在

- Python 只读国家中断 API：
  `resolve`、`overview`、`series`、`asns`、`audit`；
- publication、revision、data through、最终性、能力状态和 ETag；
- ASN 服务端分页及地址族、状态、查询和排序参数；
- 事件页中的国家中断数据观测工作台；
- 缺槽、质量、能力、revision 历史和审计的只读展示；
- 前端 Vue 3、TypeScript、Vitest 和 Vite 构建链。

### 3.2 尚不存在或尚未证明

- 国家中断 Agent Sidecar；
- 固定快照事实装配和报告事实合同；
- 面向人的正式报告生成及机器校验；
- 报告与追问前端工作面；
- 短期报告会话、SSE 进度、取消和重连；
- PDF/Markdown 报告制品；
- 模型认证、备用模型和干净环境重放；
- 用户授权的外部证据补充；
- FE-01 至 FE-09、RG-01 至 RG-13、SCE-01 至 SCE-10 的最终通过证据。

## 四、量化验收基线

`config/country-outage-agent-acceptance-v1.json` 冻结以下类别：

- 30 分钟会话，提前 5 分钟提醒；
- 报告最长 120 秒、追问最长 60 秒、外部补充最长 90 秒；
- 单用户同时 1 个报告运行，全局 8 个，队列最多 32 个；
- 每会话最多 30 个问题，每分钟最多 6 个问题；
- 单次最多 2,000 条事实，64,000 token 上下文，回答最多 4,000 字符；
- 外部证据最多 5 个公开页面、单页 2 MiB、最多 3 次重定向；
- PDF 最多 40 页、10 MiB，Markdown 最多 2 MiB；
- 1440×900、1024×720、768×1024、390×844 四个验收视口；
- 临时报告和下载最长 1 小时，问答和外部证据最长 30 分钟；
- 运行日志保留 30 天，但不记录提示词和回答正文；
- 所有生成、追问、外部证据和下载继承事件读取权限。

这些数值是验收上限或目标，不代表当前代码已经达到。后续若必须修改，需形成新配置
版本并重新执行受影响阶段，不能在验收时无痕调整。

## 五、版本与工作树说明

- 基线 Git HEAD：`410be2e424c8fa658e74861ae3427a3fe07846ad`；
- 分支：`codex/rrc25-iran-research-loop`；
- 冻结时工作树包含大量既有修改和未跟踪文件；
- 本 Goal 只修改明确属于国家中断 Agent 的外围文件，不覆盖或清理其他工作；
- 验收文档 SHA-256：
  `6858244b52e9f0f675230aef8e778592e27685f6658ddd422a0ecf4f03836e4f`；
- 分阶段计划 SHA-256：
  `a6d211d5b68d69f431a39cbf862003d9bcaeb24b9061cfdfaba0850199c9b661`；
- 防偏离 Hook SHA-256：
  `cb3afe22287e18736a96c771dc2b25b43b37b4e65b908092bc3c1160c2784e51`。

## 六、A0 出口判定

- 两条验收主线已经冻结；
- 代表性事件和当前已有能力已经区分；
- 量化配置已经冻结；
- 未实现项没有被写成通过；
- `backend/core` 必须继续通过 `core.sha256` 校验；
- A0 Hook 回检通过后，才能进入 A1。
