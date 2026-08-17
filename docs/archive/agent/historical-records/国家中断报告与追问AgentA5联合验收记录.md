# 国家中断报告与追问 Agent A5 联合验收记录

版本：1.0<br>
验收日期：2026-07-30<br>
核心验收剖面：`core-v1`<br>
联合旅程证据：
[模型复验影响判定与联合旅程证据](../artifacts/country-outage-agent/a5-core-v1-joint-20260730T190548+0800/模型复验影响判定与联合旅程证据.md)

## 一、验收结论

| 验收对象 | 结论 | 结论边界 |
|---|---|---|
| 核心剖面 `core-v1` | 通过 | 当前源码、当前正式模型 profile、本地联合验收环境、固定伊朗事件 |
| 外部证据能力包 `external-evidence-pack-v1` | `draft`、未配置、未验收 | readiness 为 `not_configured / disabled`；未执行真实公开网络读取 |

核心剖面的“通过”只表示：固定 RRC25 报告、Markdown/PDF 下载、同快照追问、
短期会话和失败关闭在本次候选联合旅程中达到当前冻结合同。它不表示生产部署、
生产身份、生产 ACL、生产容量或特定真实读屏器已经验收。

外部证据能力包不属于核心剖面的依赖项。本次只证明能力未配置时能够明确失败关闭，
且不会触发 Pi 或阻塞核心报告；这不能替代 FE-07、RG-09、SCE-05、SCE-06 的
真实产品路径验收。

## 二、固定事件和唯一数据边界

本次从始至终只有 `rrc25`，没有 collector 选择、多 collector 或聚合。

| 项目 | 固定值 |
|---|---|
| 事件引用 | `country_outage/2026-02-27 09:12:32/IR/1/r` |
| incident | `incident_go_v1_a1de26f854831330c616a72af21597eb` |
| publication | `publication_v1_38bddead083db3f49023c2e1` |
| revision | `1` |
| data through | `2026-02-28T15:00:00Z` |
| finality | `final`（`is_final=true`） |
| collector | `rrc25` |
| cohort | `cohort_go_v1_4ff75dc68f95249de99c11bec48391fb` |
| 观察窗口 | `2026-02-28T10:05:00Z` 至 `2026-02-28T15:00:00Z` |
| 时间网格 | `300` 秒；`60 / 60` 个观测槽 |
| fact set | `facts_44ce6ba951e4774835b0459eabf186e6` |

固定身份可由
[核心验收合同](../config/country-outage-agent-core-acceptance-v3.json)、
[正式冒烟 Markdown](../artifacts/country-outage-agent/a5-core-v1-joint-20260730T190548+0800/downloads/iran-rrc25-core-report.md)
和
[Pi 审计](../artifacts/country-outage-agent/a5-core-v1-joint-20260730T190548+0800/pi-audit/country-outage-pi-run-audit-v1-2026-07-30.jsonl)
交叉核对。

## 三、模型复验影响判定

本阶段没有盲目重跑完整模型认证。以
[A3 v6 当前源码基线清单](../artifacts/country-outage-agent/a3-v6-current-source-20260730T165806+0800/source-end-manifest.json)
为基线，对以下六个模型影响面涉及的 21 个去重文件重新计算 SHA-256：

1. DeepSeek 模型与 API adapter；
2. Pi `0.82.1` 依赖身份；
3. 三个只读工具及 schema；
4. Skill、提示词、报告规范与 validator；
5. RRC25 事实合同与上下文构造；
6. timeout、retry 与 token 上限。

结果为 `21 / 21 UNCHANGED`。因此没有触发完整五场景模型重认证。当前使用的模型
身份仍为：

| 项目 | 值 |
|---|---|
| profile | `deepseek-v4-flash-pi-0.82.1-v1` |
| registry | `deepseek-v4-flash-certified-v1` |
| certification evidence | `evidence:model-certification:b50f247c7b1322df6d05afa45c5c1078b58349329d9f27ec5800bbfa5770a1d4` |
| Skill bundle SHA-256 | `5f108d26f39dea9ff5a2902b00cdb113e3a76a8afd1b6560dffd7e9453d3a88d` |
| responseModel adapter SHA-256 | `5805cc08566c4d9437280f68d996ef0fb452c15e2becb67b94c967b7ace2023b` |

随后发现的两条组合问法漏答只修改了确定性 QA 路径及其测试。修复后再次核对上述
21 个模型路径文件仍全部不变，并精确回归 Sidecar 全量测试；因此该修复同样不触发
模型重认证。

## 四、自动回归

| 门禁 | 结果 | 证据时间边界 |
|---|---:|---|
| Agent Sidecar | `545 / 545` | 确定性 QA 修复后全量结果 |
| 前端 Vitest | `181 / 181` | 本阶段更早结果；QA 修复未改前端 |
| 后端 `pytest -q backend` | `218 / 218` | 本阶段更早结果；QA 修复未改后端 |
| `backend/core.sha256` | `14 / 14` | 核心检测边界保持不变 |

前端和后端结果没有被写成“修复后重新全量运行”。本阶段按影响面只对实际变化的
确定性 QA 与 Sidecar 全量路径重新取证。

## 五、外部证据能力未配置时的隔离

本地正式 profile 联合栈实测：

- readiness 请求返回 HTTP `200`，关键状态为
  `state=not_configured`、`provider=disabled`；
- 外部模式追问请求返回 HTTP `409`；
- 外部附录下载请求返回 HTTP `409`；
- 三项检查前后 Pi audit 行数不变，没有因此调用模型或工具。

这组证据支持“外部能力未配置时不阻塞核心 Agent，并且失败关闭”。它不支持
“外部证据能力包已经通过”，也没有访问 `bgp.he.net`、
`radar.cloudflare.com` 或其他公开网络。

## 六、正式 profile 浏览器联合旅程

### 6.1 第一次运行：透明保留 PDF 环境失败

第一次真实调用使用认证 profile，Pi 审计结果为 `accepted`。模型生成及 Markdown
完成，但 Sidecar 默认调用的系统 Python 缺少 `reportlab`，PDF 单项失败。

该次运行没有被改写成成功旅程，也没有从记录中删除。其证据包括：

- [首次失败截图](../artifacts/country-outage-agent/a5-core-v1-joint-20260730T190548+0800/screenshots/03-attempt1-report-pdf-env-failure-1440x900.png)；
- [首次 Markdown](../artifacts/country-outage-agent/a5-core-v1-joint-20260730T190548+0800/downloads/attempt1-core-report.md)；
- Pi audit 第一行。

失败原因属于运行环境依赖选择，不是模型、Skill、RRC25 事实、报告 validator 或
PDF 版式代码失败。

### 6.2 第二次运行：正式冒烟成功

第二次只调整运行环境变量 `DOMEYE_REPORT_PYTHON_EXECUTABLE`，由系统 Python
改为 Codex bundled Python；源码、认证 profile、事件、publication、revision、
finality 和 RRC25 均未改变。

第二次正式冒烟完成：

- 一份报告；
- Markdown 与 PDF 下载；
- 三次同快照 Domeye-only 追问；
- 四个视口检查；
- `prefers-reduced-motion: reduce` 检查；
- 控制台错误 `0`；
- 浏览器公开外网请求 `0`。

Markdown 与 PDF 绑定同一制品：
`report_2528f47fdbb99b6bc2aae5d038578b06`。

| 格式 | 字节 | SHA-256 |
|---|---:|---|
| Markdown | `8,916` | `0375005feb6fe1c32157fcc7d6267ee3c77bc4e73d48be03af51c670a4b076a8` |
| PDF | `152,960` | `c50d104efddb471416004612c6d574467559ed6c427cab29d5ad2ea14034b03c` |

PDF 为 A4、4 页。四页逐页渲染目检没有发现裁切、重叠、缺字、乱码、异常空白页
或表格溢出。1440×900、1024×720、768×1024、390×844 四个视口均无横向
溢出。

## 七、真实模型审计和费用

两次真实模型调用都为 `accepted`，都只执行：

- `country_outage_resolve`；
- `country_outage_get_observation`。

每次工具执行数为 `2`，未授权工具尝试均为 `0`；没有执行第三个 ASN 工具。两次
审计均记录 provider/model/responseModel 为
`deepseek / deepseek-v4-flash / deepseek-v4-flash`。

| 调用 | 审计估算 USD |
|---|---:|
| 第一次 | `0.0001482656` |
| 第二次 | `0.0001449056` |
| 合计 | `0.0002931712` |
| 按冻结汇率 8 折算 CNY | `0.0023453696` |

这些数值是 Pi audit 的估算，不是供应商账单结算证明。

## 八、组合问法偏离与精确修复

浏览器三次追问完成后，复核发现两条组合问法会漏掉问题中的第二个要求：

1. `窗口最低点发生在什么时候，较起点变化了多少？`
2. `Prefix×VP 是什么意思，能否直接换算受影响用户数？`

只修改确定性问答路由和答案拼装，并增加精确断言：

- 第一问同时回答 `22:35`、`316,733`、`82.32%`、减少 `50,482` 条和
  `13.75%`；
- 第二问同时解释前缀、固定 BGP 观测点、固定路由观测关系、同一前缀可对应多个
  观测点，以及不能换算受影响用户数。

修复后 Sidecar 全量 `545 / 545` 通过。此前三次正式 profile 追问及
`06-three-questions-1440x900.png` 发生在修复前，不能证明修复效果。

随后冻结 `/Applications/Google Chrome.app` 版本 `150.0.7871.187`，
`agent-browser` 使用该 executable，页面实际 UA major 为 `150`。为避免额外付费
模型调用，补验启动 `deterministic-acceptance` Sidecar，而不是正式认证模型
profile。真实页面完成报告、Markdown/PDF 和两条修复后问答：

- 最低点组合问法完整显示
  `22:35 / 316,733 / 82.32% / 50,482 / 13.75%`；
- Prefix×VP 组合问法完整显示固定观测关系、同一前缀可对应多个 VP，以及不能
  换算用户影响。

补验的 console errors 和 page errors 均为空，
`bgp.he.net`、`radar.cloudflare.com` 请求均为 `0`；Pi audit 仍为 `2` 行，
证明补验没有调用模型。截图：

- [修复后报告](../artifacts/country-outage-agent/a5-core-v1-joint-20260730T190548+0800/screenshots/12-chrome150-postqa-report-1440x900.png)
- [修复后两条完整回答](../artifacts/country-outage-agent/a5-core-v1-joint-20260730T190548+0800/screenshots/13-chrome150-postqa-two-complete-answers-1440x900.png)

因此，修复后已经完成冻结 Chrome 150 的非模型浏览器补验；正式认证 profile 的
付费浏览器冒烟仍只有第六节记录的第二次成功运行，二者不得合并为同一类证据。

## 九、不在本次结论内

本记录没有验收，也不得外推为：

- 外部证据能力包或 Evidence Gateway 已部署、已配置或已读取真实公开来源；
- 生产身份与生产 ACL；
- 生产发布、金丝雀、回滚或容量；
- 特定真实读屏器；
- 修复后再次使用正式认证 profile 的付费浏览器冒烟；
- 完整模型重认证。
