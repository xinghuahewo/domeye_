# 国家中断报告与追问 Agent A3 验收记录

版本：1.5  
阶段：A3  
执行日期：2026-07-30  
判定：已修正  
证据目录：
`artifacts/country-outage-agent/a3-v6-current-source-20260730T165806+0800/`

## 一、结论

A3 当前源码出口通过。浏览器首轮发现前端仍只接受项目知识 v5，导致合法的 v6
完成事件被安全拒绝；该偏离已在 A3 范围内修正，并在指定 Google Chrome
150.0.7871.187 上从当前源码重新构建、重新生成、重新追问和重新下载。

本阶段成立的结论：

- FE-01 至 FE-06、FE-08、FE-09 和 RG-07 通过；
- SCE-04、SCE-08、SCE-09、SCE-10 的前端与 Domeye-only 问答部分通过；
- 页面仍是事件内的“技术报告研读工作台”，不是通用聊天或通用 RCA Agent；
- 报告、追问、证据展开和下载固定在同一 publication、revision、cohort 和唯一
  RRC25；
- 报告达到面向人的中文长文效果，Markdown/PDF 可下载；
- 问答默认只使用 Domeye 数据，不自动访问公开互联网；
- 1440×900、1024×720、768×1024、390×844 四个视口无水平溢出；
- 刷新后不恢复旧报告和旧问答，只能由用户基于当前合法快照重新生成；
- 没有修改 `backend/core/`，没有模型调用，没有认证文件读取，没有公开互联网访问。

A3 不代表 DeepSeek 模型认证、外部证据核验、生产身份、生产 ACL、特定真实读屏器
或生产部署通过；这些不能由本记录外推。

## 二、固定身份与运行边界

| 项目 | 固定值 |
|---|---|
| 事件引用 | `country_outage/2026-02-27 09:12:32/IR/1/r` |
| incident | `incident_go_v1_a1de26f854831330c616a72af21597eb` |
| publication | `publication_v1_38bddead083db3f49023c2e1` |
| revision | `1` |
| data through | `2026-02-28T15:00:00Z` |
| finality | `final` |
| collector | `rrc25` |
| cohort | `cohort_go_v1_4ff75dc68f95249de99c11bec48391fb` |
| fact set | `facts_44ce6ba951e4774835b0459eabf186e6` |
| 报告制品 | `report_827dffaa205b24516d3f9de166b63c9a` |
| 报告正文摘要 | `b86feb2e8b4cc93c23ea96c201a8a8fe4f6a6eabf3fa41a2f28dabad627dde49` |
| 项目知识 | `country_outage_report_skill_v6` |
| 校验规则 | `country_outage_report_validator_rules_v5` |

浏览器到 Domeye 的验收链路：

```text
Google Chrome 150
    ↓ 127.0.0.1
当前源码构建的 Vite 预览前端
    ↓ /api/v2/country-outage/*
Python 只读代理（受信任 loopback 验收身份）
    ↓ 内部 Bearer
Node.js Agent Sidecar（确定性验收叙述器）
    ↓ 固定只读 API
Domeye 10.99.8.16:28471
```

确定性验收叙述器只用于隔离前端随机性，不能冒充正式 Pi/DeepSeek 模型。运行配置
明确记录：模型调用 0、认证读取 0、公开互联网调用 0。浏览器请求来源只有本地
验收栈。

## 三、首轮偏离与修正

### 3.1 发现

Sidecar 已生成完整报告并通过 v5 校验，完成事件携带：

```text
country_outage_report_spec_v1
country_outage_report_skill_v6
country_outage_report_validator_rules_v5
```

前端和 OpenAPI 仍把项目知识固定为
`country_outage_report_skill_v5`，因此页面显示
`REPORT_PROTOCOL_IDENTITY_CONFLICT`，没有发布半成品。这证明失败关闭有效，也
暴露了真实前后端版本错位。

### 3.2 修正

修正只涉及协议身份及回归：

- 前端 API 类型升级到 `country_outage_report_skill_v6`；
- 前端完成事件结构校验升级到 v6；
- OpenAPI 常量及生成类型升级到 v6；
- 后端 OpenAPI 合同测试同步；
- 新增旧版项目知识必须返回
  `report_protocol_identity_conflict` 的前端测试。

首次失败证据保留在
`artifacts/country-outage-agent/a3-v6-current-source-20260730T165056+0800/`，
并明确标为不构成通过。系统 Python 缺少 Flask 的一次启动失败保留在
`artifacts/country-outage-agent/a3-v6-current-source-20260730T165722+0800/`；
它没有进入浏览器、报告或模型路径。最终运行使用
`backend/.venv/bin/python`。

## 四、前端效果验收

### 4.1 FE-01：事件内入口

- 默认进入“数据观测”；
- “报告与追问”只存在于当前合法 `country_outage` 事件内；
- 页面没有国家、时间、collector、URL 或任意事件输入；
- 生成必须由用户点击触发；
- “返回数据观测核对”往返后，事件、publication、revision 和 RRC25 不变。

### 4.2 FE-02、FE-03：报告主体与研读记录

1440×900 为报告正文和研读记录双栏。报告页头持续显示：

- AI 生成、未经人工审核、BGP 控制面观测；
- RRC25、窗口、publication、revision、data through、生成时间；
- 短期会话剩余时间；
- Markdown 和 PDF 下载入口。

正文包含标题、副标题、摘要、关键数字、十个阅读章节、证据返回入口和“不能回答
的问题”。没有头像、聊天气泡、打字机输出、token 流、工具日志或模型思考过程。

1024、768 和 390 三档切换为“报告 / 追问”单栏，切换后报告、回答和证据展开状态
保持稳定。

### 4.3 FE-04、RG-07：Domeye-only 追问

指定 Chrome 150 实际提交：

> 窗口结束时的回升能否称为完全恢复？

得到：

> 当前报告只能说明观测窗口结束时的状态，不能判断其后是否完全恢复或事件是否
> 结束。本快照窗口结束于 2026-02-28T23:00:00+08:00，数据截止为
> 2026-02-28T15:00:00Z；要回答后续状态，需要窗口之后、同口径且具有明确
> publication/revision 的新观测快照。

回答证据模式为 `domeye-only`，没有请求外部来源。展开项固定引用：

- 报告的“窗口之后是否完全恢复”未知项；
- `overview:/observation_scope/window_end_utc`；
- `overview:/observation_scope/last_observation_at_utc`；
- 原报告的 RRC25、cohort、publication 和 revision。

普通问答不修改基础报告制品，不把上一轮自然语言假设积累为下一轮事实。

### 4.4 FE-05、FE-06：状态、失败和短期会话

前端只显示受控阶段，不展示工具细节。首轮 v5/v6 身份冲突时页面没有发布草稿，
并给出可重试的协议身份错误。

取消、重复报告、重复问题、SSE 事件号重放、短期重连、到期、制品单项失败和新
revision 保留旧内容由 Sidecar/前端机器测试闭合。浏览器完成刷新验证：

- 刷新后回到“数据观测”；
- 旧报告和旧问答不恢复；
- 再次进入“报告与追问”只显示生成前核对；
- 必须由用户重新触发，页面不声称正在恢复永久历史会话。

### 4.5 FE-08：下载

| 格式 | HTTP | Content-Type | 字节 | SHA-256 |
|---|---:|---|---:|---|
| Markdown | 200 | `text/markdown; charset=utf-8` | 8,248 | `2e9f98df14b7472ffee7f687de39677f28a8c78b8eeb81356eaf5e48d707e20f` |
| PDF | 200 | `application/pdf` | 151,966 | `bfe50d5c3085c940784cdd7792526112c1d24ab30fabce8ff6ada1125083555f` |

两种响应均为 `private, no-store`，`X-Artifact-Id` 均为
`report_827dffaa205b24516d3f9de166b63c9a`。文件名包含国家、观察窗口、revision 和
生成时间。

PDF 为 A4、4 页、无 JavaScript、未加密。四页已逐页渲染目检，无空白页、截断、
缺字或表格越界。普通问答没有进入下载文件。

### 4.6 FE-09：响应式、键盘与语义

| 视口 | 形态 | 水平溢出 | 报告/追问 | 状态保留 |
|---|---|---:|---:|---:|
| 1440×900 | 双栏 | 无 | 同屏 | 通过 |
| 1024×720 | 单栏 | 无 | 可切换 | 通过 |
| 768×1024 | 单栏 | 无 | 可切换 | 通过 |
| 390×844 | 移动单栏 | 无 | 可切换 | 通过 |

指定 Chrome 150 验证：

- 外层页签可用方向键和 Home/End 切换并保持焦点；
- 移动端“报告 / 追问”可用 Enter 切换；
- Tab 顺序从当前报告页签进入 Markdown，再进入 PDF；
- 可访问性树识别页签、区域、标题、报告、输入和复选框标签；
- 文档语言为 `zh-CN`，状态同时使用文字，不只依赖颜色；
- `prefers-reduced-motion: reduce` 生效；
- 控制台错误 0，页面错误 0。

特定真实读屏器环境按冻结合同为非阻断项，本轮没有测试，不能外推为真实读屏通过。

## 五、场景结论

| 场景 | 结果 | 证据 |
|---|---|---|
| SCE-04 | 通过 | 实际追问固定原快照，边界回答和证据可展开；数字、原因、用户影响等问答由机器测试覆盖 |
| SCE-08 | 通过 | 浏览器验证刷新不恢复历史；取消、幂等、SSE 重放、到期和新 revision 由机器测试覆盖 |
| SCE-09 | 通过 | 两种格式真实下载，同制品身份、独立摘要；单项失败和注入载荷由机器测试覆盖 |
| SCE-10 | 通过 | 四视口、键盘、语义树和本地代码级跨用户隔离通过；不外推为生产 ACL 或真实读屏验收 |

## 六、自动回归与来源身份

| 门禁 | 结果 |
|---|---:|
| Agent Sidecar | 547 / 547 |
| 前端 Vitest | 165 / 165，14 个文件 |
| 前端生产构建 | 通过 |
| 后端 `web/tests` | 220 / 220；1 条既存 pandas FutureWarning |
| `backend/core.sha256` | 14 / 14 |

源码组合摘要：
`983fb7034a9f20abd1ad8f557eb101cef7b3e836002f510008289ce0741e3c45`。
构建前、构建后和浏览器旅程清理后完全一致。所有本地端口已关闭，没有强制杀进程。

主要证据：

- `browser-assertions.json`：结构化浏览器、问答、下载与回归断言；
- `evidence.md`：本次发现、修正、主旅程和边界；
- `01-preflight-1440x900.png` 至
  `13-reload-no-session-history-390x844.png`：指定 Chrome 150 截图；
- `report.md`、`report.pdf` 和 `pdf-pages/`：真实下载及逐页渲染；
- `hashes.sha256`：证据文件摘要；
- 三份源码清单、构建来源和清理记录。

## 七、阶段边界

A3 只闭合前端、Domeye-only 追问和下载。以下仍属于 A4/A5：

- Pi 0.82.1 与 DeepSeek `deepseek-v4-flash` 的真实模型认证；
- 用户显式授权后的公开 URL 旁证；
- 模型实际 token、费用、responseModel 和审计证明；
- 正式模型失败/修复的候选晋级；
- 生产身份、生产 ACL、生产容量和生产部署；
- 前端与正式模型报告生成逻辑的最终联合验收。

没有加入第二 collector、任意国家/时间入口、通用搜索、通用 RCA、归因、处置、
Shell、文件编辑、SQL、数据库写入或永久会话历史。

国家中断报告 Agent 最终验收回检：A3 已修正
