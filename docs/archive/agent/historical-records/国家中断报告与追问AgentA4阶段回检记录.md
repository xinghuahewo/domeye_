# 国家中断报告与追问 Agent A4 阶段回检记录

版本：3.0<br>
回检日期：2026-07-30<br>
验收口径：2.2<br>
阶段：A4（`core-v1` 已通过；外部证据能力包不属于核心 A4）

## 一、当前双结论

### 1.1 核心国家中断 Agent

`core-v1` 的 A4 验收结论为：**通过**。

这里的核心能力只包括：

- 用户触发的伊朗国家中断观测报告；
- 与报告相同快照、相同事实集合的追问；
- Markdown 和 PDF 下载；
- DeepSeek 正式认证 profile 下的受限语言槽生成；
- 唯一 `rrc25` 的 Domeye BGP 控制面事实；
- 短期会话、失败关闭、只读工具、安全审计和前端主流程。

该结论证明当前候选源码和本地联合旅程满足 `core-v1` 验收合同，不表示已经部署到
生产，也不表示生产身份、生产 ACL、真实长期日志留存或特定真实读屏器已经验收。

### 1.2 外部证据能力包

`country-outage-external-evidence-pack-v1` 当前结论为：
**`draft`、当前环境 `not_configured`、尚未验收**。

外部能力包不再是核心 A4 的出口条件。当前核心运行显式使用
`ExternalEvidenceProvider=disabled`，没有调用 Evidence Gateway，也没有把
`bgp.he.net`、`radar.cloudflare.com` 或其他公开网页材料带入报告生成、追问或
Domeye 事实集合。

未来只有在独立完成 E0、E1、E2 后，才能把外部能力包声明为可用。即使届时启用，
外部材料也只能作为独立旁证附录，不能改变唯一 RRC25 和 Domeye 页面事实边界。

## 二、2.1 历史与 2.2 迁移

2.1 口径曾把以下四件事绑在同一条 A4/A5 出口：

1. RRC25 核心报告；
2. 外部证据能力；
3. 当前运行环境的公网出口；
4. Agent 主流程验收。

因此，本文件 2.0 版曾据实记录：FE-07、RG-09、SCE-05 缺少成功产品路径证据，
A4 不能进入 A5。该判断在当时的 2.1 合同下成立，不能删除或改写成“当时已经
通过”。

2.2 口径把外部证据迁移为独立 capability pack：

- 核心 A4 只验收 RRC25 报告、追问、下载、模型、安全和运行边界；
- FE-07、RG-09、SCE-05、SCE-06 迁移到外部能力包的 E0—E2；
- 外部能力包未部署或 readiness 为 `not_configured` 时，不阻塞核心报告；
- 一旦声明外部能力包已启用，就必须另行完成真实受控公网读取验收。

先前对 SSRF 拒绝、恶意 URL、授权域名和 `198.18.0.0/15` 合成保留地址的检查，
仍保留为历史安全证据；它们既不是“外部读取已成功”的证据，也不再是核心 A4 的
阻断项。

## 三、核心 A4 效果回检

### 3.1 前端主流程

已通过：

- 默认进入“数据观测”，报告仅由用户点击生成；
- 页面是技术报告研读工作台，不是通用聊天界面；
- 报告、三类同快照追问、正文“就此追问”、证据展开和返回数据观测均可完成；
- Markdown 与 PDF 可下载，刷新后短期报告和问答不恢复，也不会自动重生成；
- 1440×900、1024×720、768×1024、390×844 均无水平溢出；
- 键盘可到达下载和正文操作，语义树可读，减弱动画生效；
- 外部证据入口根据 readiness 显示“当前环境未配置”，不会让用户点击后才发现
  核心报告不可用；
- 外部问题和附录接口在能力未配置时明确拒绝，不影响核心报告、追问和下载。

特定真实读屏器、生产身份和生产 ACL 继续标记为“本轮非阻断、未验收”。

### 3.2 报告生成逻辑

已通过：

- 只接受已有合法 `country_outage`，固定当前伊朗事件及其 publication/revision；
- 从始至终只有 `rrc25`，不存在 collector 选择、多 collector 或聚合；
- Skill v6、validator v5、确定性事实和派生计算替代 Codex 记忆；
- DeepSeek 只填写五个受限语言槽，确定性基础报告、合并不变量和最终规则均通过；
- 正文只表达 BGP 控制面可见性，不推断全国断网、用户影响、原因、责任或窗外恢复；
- 正式 Pi 只执行 `country_outage_resolve` 和
  `country_outage_get_observation`；未授权工具尝试为 0；
- PackageManager、ModelResolver、`models.json`、模型目录联网刷新和 provider
  retry 均关闭；
- 报告、Markdown、PDF 和问答绑定同一快照和同一事实集合；
- 模型认证到期时，正式入口在监听、auth 读取和模型调用前明确失败关闭；
- 外部 Provider 不参与核心报告编译，外部能力不可用不会改变核心报告结果。

### 3.3 到期项归属

| 条款 | 2.2 判定 | 说明 |
|---|---|---|
| RG-10 | 通过 | 五份真实 DeepSeek 报告覆盖代表性与三种合法边界场景 |
| RG-11 核心部分 | 通过（本地候选边界） | 身份、权限继承、隔离、幂等、并发、容量、超时、缓存和审计已回归 |
| RG-13 | 通过 | 固定 Skill/validator、事实等价认证、同 profile 真实核心冒烟和可下载制品均有证据 |
| SCE-07 核心部分 | 通过 | 未认证 profile 和认证到期均在启动前明确失败；不切换未认证备用 |
| SCE-10 核心部分 | 通过（本地候选边界） | 四视口、键盘、语义树和减弱动画已回检 |
| FE-07 / RG-09 / SCE-05 / SCE-06 | 不属于核心 A4 | 已迁移到外部能力包 E0—E2；当前状态仍为未配置、未验收 |

## 四、为什么本轮没有重跑完整五报告认证

### 4.1 六组认证输入身份比较

比较基线为：

```text
artifacts/country-outage-agent/
a3-v6-current-source-20260730T165806+0800/source-end-manifest.json
```

本轮按原认证合同只比较以下六组，不自行增加或替换分组：

1. DeepSeek 模型与 API adapter；
2. Pi `0.82.1` 依赖身份；
3. 三个只读工具及 schema；
4. Skill、提示词、报告规范和 validator；
5. RRC25 事实合同和上下文构造；
6. timeout、retry 和 token 上限。

这六组涉及的 21 个相关文件与基线逐文件比较，结果为 `21 / 21` SHA-256
不变。模型 profile、正式注册表和认证 evidence 也保持原身份，但它们是附加
核对项，不能替代上述六组源码身份比较。

本次变化是核心编排与外部能力解耦，没有改变模型认证输入或输出合同。因此采用
影响面证据方案：

- 完成全量核心回归；
- 完成不调用模型的外围能力检查；
- 使用同一已认证 profile 完成一次真实核心成功冒烟；
- 不重新执行五份付费报告认证。

如果上述六组任一项、正式 profile、认证 evidence 或注册表身份发生变化，就不能
沿用这次判断，必须重新评估是否执行完整认证。

### 4.2 全量核心与零模型外围回归

本轮结果：

- Sidecar 全量测试：`545 / 545` 通过；
- 前端全量测试：`181 / 181` 通过；
- 前端 typecheck、生产 build 和 `api:types`：通过；
- 后端全量测试：`218 / 218` 通过，只有 1 条既有 pandas warning；
- `backend/core.sha256`：`14 / 14` 通过；
- 外部 readiness GET：HTTP 200，状态为 `not_configured/disabled`；
- 有效 external question：HTTP 409；
- external appendix GET：HTTP 409；
- 三次零模型外围请求前后，Pi 审计行数保持不变。

这些结果证明外部能力未配置时不会触发 Pi 或阻断核心流程；它们不证明真实公网
读取、Evidence Gateway 或外部能力包已经验收。

### 4.3 Chrome 150 确定性问答补验

组合问法展示修复后，使用本机实际 agent-browser executable
Google Chrome `150.0.7871.187`（UA major 150）和
deterministic-acceptance Sidecar 完成浏览器补验：

- 两条组合问法的答案均完整显示；
- browser console 和页面 errors 均为空；
- 没有发出外网请求；
- Pi 审计文件保持 2 行，没有新增模型调用。

证据仍位于本轮联合目录：

```text
artifacts/country-outage-agent/
a5-core-v1-joint-20260730T190548+0800/
├── screenshots/12-chrome150-postqa-report-1440x900.png
├── screenshots/13-chrome150-postqa-two-complete-answers-1440x900.png
└── python-proxy-access.jsonl
```

该补验只证明确定性问答与前端显示修复在 Chrome 150 下成立，**不是模型证据**。
正式已认证 profile 的真实模型成功冒烟仍是下一节记录的第二次真实调用。

## 五、同认证 profile 真实核心冒烟

证据目录：

```text
artifacts/country-outage-agent/
a5-core-v1-joint-20260730T190548+0800/
```

本次真实旅程透明记录了两次独立报告调用：

1. 第一次调用的 Pi 报告生成已被接受，Markdown 已生成；随后 PDF 单项后处理失败。
   失败原因是当时配置的系统 Python 缺少既有 PDF renderer 所需的 `reportlab`，
   不是 DeepSeek、Pi、RRC25 事实、Skill、validator 或报告正文失败。
2. 第二次只把既有环境变量 `DOMEYE_REPORT_PYTHON_EXECUTABLE` 指向当前机器上
   已具备 `reportlab` 的解释器。没有修改代码、模型、Pi、Skill、validator、
   报告规范、事实合同或工具边界。随后完整报告、Markdown、四页 PDF、三类追问和
   四视口旅程成功。

这两次是两次真实报告提交，不是一次 provider retry。两条 Pi 审计均为
`accepted`，每次只执行 `resolve + observation`，未授权工具尝试均为 0：

| 调用 | input | cacheRead | output | provider 请求 | 估算成本 USD |
|---|---:|---:|---:|---:|---:|
| 第一次，PDF 环境失败 | 104 | 13,952 | 338 | 2 | 0.0001482656 |
| 第二次，完整成功 | 104 | 13,952 | 326 | 2 | 0.0001449056 |
| 合计 | 208 | 27,904 | 664 | 4 | 0.0002931712 |

按冻结预算口径 `1 USD = 8 CNY`，本次两次调用合计：

```text
0.0002931712 USD × 8 = 0.0023453696 CNY
```

关键制品：

```text
screenshots/03-attempt1-report-pdf-env-failure-1440x900.png
downloads/attempt1-core-report.md
downloads/iran-rrc25-core-report.md
downloads/iran-rrc25-core-report.pdf
pdf-render/page-1.png ... page-4.png
pi-audit/country-outage-pi-run-audit-v1-2026-07-30.jsonl
```

成功冒烟证明同一认证 profile 在解耦后的核心编排中仍可生成完整报告；第一次
后处理失败也被保留，没有从费用或证据中隐藏。

## 六、2.1 五报告认证历史

认证 evidence：

```text
artifacts/country-outage-agent/a4-model-certification/
evidence:model-certification:b50f247c7b1322df6d05afa45c5c1078b58349329d9f27ec5800bbfa5770a1d4
```

历史认证结果保持不变：

- 两份代表性报告；
- 三份认证专用合法边界报告：
  `capability-degraded-final`、
  `direction-end-above-start-final`、
  `non-final-snapshot`；
- 每份恰好两次 provider 请求，retry 为 0；
- 五份均通过 ReportDocument、validator、Markdown 和 PDF；
- 五份 PDF 共 19 页已逐页渲染检查，无裁切、重叠、乱码、缺字、异常空白页或
  表格溢出；
- 实际认证费用 `0.08085616 CNY`；
- 认证有效至 `2026-08-06T09:19:34.010Z`。

正式注册表：

```text
agent-sidecar/resources/certified-models/country-outage-pi-models-v1.json
```

- registry：`deepseek-v4-flash-certified-v1`
- profile：`deepseek-v4-flash-pi-0.82.1-v1`
- SHA-256：
  `30a5743019a19ace272a70c35f3bfbffb72286d33b3d698f526c6af8739e4ff6`

供应方没有不可变权重 revision，因此该模型继续按可变别名管理。认证到期、六组
认证身份变化或正式路径变化时必须重新评估。

该五报告认证只证明核心模型组合，不证明外部 Provider、Evidence Gateway、来源
适配器、网页内容解析或外部附录可用。

### 6.1 2.1 正式 profile 旅程

2.1 期间还完成过一次正式 profile 浏览器旅程：

```text
artifacts/country-outage-agent/
a4-formal-profile-journey-20260730T174914+0800/
正式模型浏览器旅程验收说明.md
```

该旅程实际费用为 `0.0011592448 CNY`，三类追问不调用模型，Markdown 和四页
PDF 均完成；它仍是本地候选旅程，不是生产部署证据。

### 6.2 认证到期失败关闭

证据：

```text
artifacts/country-outage-agent/
a4-sce07-certification-expired-20260730T180408+0800/
SCE-07认证失效失败关闭验收说明.md
```

已过期注册表副本和未注册 profile 均在 ready、监听、auth 读取和模型调用前明确
失败；隔离旅程无审计、无网络、无模型费用。当前没有第二个已认证 profile，正确
行为是失败并要求重新认证，而不是切换未认证备用。

## 七、预算账本

20 CNY 总上限、历史账本和 tail anchor 均未重置、删除、回退或回算。当前项目
总预算口径为：

| 项目 | CNY |
|---|---:|
| 总上限 | 20.0000000000 |
| A4 安全账本累计保守承诺 | 14.2396598400 |
| 2.1 正式 profile 单报告旅程 | 0.0011592448 |
| 2.2 编排迁移两次真实调用 | 0.0023453696 |
| 当前累计保守承诺 | 14.2431644544 |
| 当前剩余 | 5.7568355456 |

未来预留继续按运行时真正强制的单请求 64K 上限计算。每份报告最多五次 provider
请求时，最坏预留为 `0.5419008 CNY`。这次不重跑完整认证是基于六组身份不变，
不是为了绕开预算。

## 八、外部能力包的当前边界

当前外部能力包配置仍为草案，尚未冻结、尚未部署、尚未验收。核心正式启动显式
注入 disabled Provider；readiness 为 `not_configured`，页面显示“当前环境未配置
公开来源旁证”。

用户曾授权的公开来源范围继续保留为未来能力包策略输入：

- `https://bgp.he.net/` 及其子域名；
- `https://radar.cloudflare.com/` 及其子域名。

本轮没有使用这些 URL 生成或修改报告。未来 Evidence Gateway 即使成功返回材料，
也必须以独立 Evidence Envelope 和附录呈现，并满足：

- 外部材料不是 Domeye 事实；
- 不改变 RRC25 报告正文、指标、方向或结论；
- “证据不足”是合法结果；
- DNS、TLS、重定向、SSRF、响应大小、HTML 清洗和来源适配由受管网关负责；
- 外部能力包单独审计、单独验收，不写入核心 Pi 模型输入。

不得用放宽 `198.18.x.x`、Sidecar 临时代理、浏览器登录态或测试夹具冒充外部能力
已通过。

## 九、最终判定与历史保留

最终双结论：

```text
core-v1 A4：通过
country-outage-external-evidence-pack-v1：draft / not_configured / 未验收
```

本次未修改 A0 的任何 SHA-256 占位；阶段 Hook 对 A0 冻结摘要的判定由主流程在
摘要真实填写后执行，不能用本文结论代替。

2026-07-29 的 590 行过程记录仍原样归档：

```text
artifacts/country-outage-agent/a4-history/
国家中断报告与追问AgentA4阶段回检记录-20260729历史快照.md
```

该快照和本文件 2.0 版共同保留 1.x/2.1 的验证细节、早期真实失败和旧预算值。
当前 2.2 双结论以本 3.0 文档及其列出的证据为准。
