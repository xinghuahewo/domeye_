# P1 runtime-v2 阶段与最终验收记录

## 一、最终结论

国家中断 Agent P1 本地开发候选 `p1-runtime-v2-395d2d180aa1f3c4` 已完成
S0—S4，并通过独立产品语义真值终审与 S4 Alignment Hook。

本结论只表示：围绕一个已绑定的 `country_outage` 事件，系统已建立“开放
UserGoalPlan → 封闭 GroundingPlan → 确定性 Tool/算子 → 证据回答 → 事务性状态”
的 P1 链路。它不表示已合并、发布、部署或生产验证，也不表示已具备 P2 组合调查、
P3 有限假设、P4 多源证据或 P5 RCA 能力。

## 二、冻结身份

| 身份维度 | 冻结值 |
|---|---|
| Candidate | `p1-runtime-v2-395d2d180aa1f3c4` |
| Task Spec 基线 | `6cb2bd3` |
| P0 入口 | `p0-v1.3-20260809-ir-r1` |
| Collector | `rrc25` |
| 语义模型 | `codex-cli:0.147.0-alpha.6.5:gpt-5.6-sol:blind-v2` |
| Incident | `incident_go_v1_a1de26f854831330c616a72af21597eb` |
| Publication / revision | `country_outage_publication_v1_989f698fb6f6c32579eebe7bb2bc833f` / `1` |
| Cohort | `country_event_cohort_v1_1e04abfc6430776bef20403fac528698` |
| Data through | `2026-03-11T00:00:00Z` |
| Finality | `is_final_in_data_range=false`，事件结束未知 |

候选摘要绑定代码、Prompt、Schema、Capability Catalog、Typed Tool Contract、Oracle、
权限与状态实现、前后端实现、模型身份、P0 manifest 和运行预算。运行证据、截图、
阶段回执与最终文档再由 manifest 逐文件绑定。

## 三、P0 能力入口与 35 案

P0 入口保持 16 个系统表面、26 项能力、17 adopt / 5 defer / 4 reject、8 项
unknown、10 个 Oracle seed 和 35 个案例。P1 未把 defer/reject 能力通过 Prompt
或模型输出变成可执行 Tool。

| 类别 | 结果 | 主要验收语义 |
|---|---:|---|
| 当前页面直接问题 | 20/20 | 事件身份、事实、时点、单位、RRC25 边界 |
| 多轮追问 | 5/5 | 继承、覆盖、主题隔离、事件切换、状态事务 |
| 越界问题 | 5/5 | 局部回答可观测事实，拒绝原因、全国和用户外推 |
| 缺失/冲突/异常 | 5/5 | null、unknown、unavailable 与 0 不混淆；身份冲突失败关闭 |

33 个真实模型/API 案例全部首轮完成。X04/X05 使用同候选受控故障注入，保存
原问题、故障输入、预检顺序、零发布、错误码和完整状态前后快照：UserGoalPlan、
GroundingPlan 与 Tool 分别为 `not_reached`、`not_reached`、`not_started`，Binding、
EvidenceState、DialogState 与 active generation 均完整回滚。

B05 将责任主体、真实用户/全国影响和经济影响保留为三个独立目标，分别说明
RRC25 不能认定责任、不能判断或量化真实用户影响、也不包含经济损失/金额/业务损失
证据；三个目标均为 `unsupported`、零节点、零证据且不提交状态。

M01 三轮保持同一 publication：`event_summary → prefix_peak → metric_followup`；
“它什么时候最严重”返回中断前缀峰值 3,855 和北京时间 2026-02-28 07:15，
未扩写成完整时间线。D11 为 `remaining_vs_peak / partial`，比较 3,855 与 1,024，
明确两个状态点不能证明期间持续异常。

## 四、语义灵活性与封闭执行

- 当前 conversation Prompt 的 24 个样本包含 30 个参考子目标。
- UserGoalPlan 为 29/30，目标保真率 96.67%，无额外目标，达到不低于 95% 的门槛。
- 唯一保守失分为 SEM-004：“数据看到哪儿了”归一为 `event_identity`，仍能安全返回
  data-through；不通过修改参考答案掩盖该标签差异。
- GroundingPlan 24/24 合法，合法性 100%；非法、越界、不可用或需澄清目标不执行。
- 模型只解释用户表达，不创造事实、证据、权限、能力或状态提交结果。
- 宿主不以关键词改写开放标签；它只校验登记能力、参数、权限、事件身份和 Oracle。

## 五、CAP-018 与浏览器/API 联合旅程

最终浏览器、API 和 Joint Artifact 绑定同一会话：
`p1v2_ea84b80b39b143aa8451211b7224cf8c`。

1. “按时间线列出这次事件的已知事实”：`supported`，4 个执行节点、18 条证据。
   OP-03 引用六个有序事实节点和 `terminal_unknown`；包含 3,855、1,553、350、
   1,024 及正确时点/单位，并声明事实时间线不是因果链。
2. “伊朗人现在还有互联网吗，是不是全国都断了”：`partial`，2 个执行节点、
   5 条证据。回答 data-through 时点 1,024 个中断前缀，拒绝全国中断、真实用户
   连通性和受影响用户数推断。
3. “这次观测覆盖多大范围”：`supported`，2 个执行节点、9 条证据。区分累计
   525/151 个 AS 与逐槽峰值 350/94 及其时点，且不解释为全国或用户影响。

三轮 `model_generated_fact_count=0`、Grounding 合法，状态只在计划、权限、执行、
证据和绑定复验全部通过后提交。桌面 `1440×1100` 与窄屏 `375×900` 均完成真实页面验收。

## 六、工程回归与防伪硬门

- Sidecar 全量：639/639。
- P1 runtime-v2 与 S4 Python 校验器：38/38，其中 S4 篡改负例 13/13。
- 后端同源代理：9/9。
- 前端定向：17/17；`vue-tsc` 与 Vite build 通过。
- P0 v1.3：16/26/8/10/35 闭合。
- S4 validator 解析 35 个 proof 指针、X04/X05 失败回执、现行 Prompt、
  Browser/API/Joint 候选与会话身份、CAP-018 OP-03、运行预算和 manifest。
- 独立专家审核产品语义真值，不以代码规范、测试数量、HTTP 200、截图或 Hook 代替。
- S4 Alignment Hook 退出 0，结论为“已修正”。

## 七、仍然未知与固定边界

- 只有 RRC25 控制面证据，不能证明真实用户连通性、全国中断、原因、责任、恢复、
  业务损失或数据面一致性。
- Update/Withdraw、DNS、HTTP、流量、IODA、OONI、新闻和其他 collector 未接入。
- SEM-004 继续作为真实语言样本采集项。
- 当前候选为本地、内存态运行；未验证持久化、多副本、发布回滚、生产吞吐或供应商 SLO。
- 未合并、未发布、未部署、未生产验证，也未实施 P2-P5。

## 八、证据索引

- `evaluation/country-outage/p1-runtime-v2/candidate-identity.json`
- `evaluation/country-outage/p1-runtime-v2/s4-p0-live-evidence.json`
- `evaluation/country-outage/p1-runtime-v2/s4-p0-v1-3-results.json`
- `evaluation/country-outage/p1-runtime-v2/s4-semantic-current-prompt-candidate.json`
- `evaluation/country-outage/p1-runtime-v2/s4-semantic-current-prompt-evaluation.json`
- `evaluation/country-outage/p1-runtime-v2/s4-browser-api-conversation.json`
- `evaluation/country-outage/p1-runtime-v2/s4-joint-acceptance.json`
- `evaluation/country-outage/p1-runtime-v2/s4-browser-desktop.png`
- `evaluation/country-outage/p1-runtime-v2/s4-browser-narrow.png`
- `evaluation/country-outage/p1-runtime-v2/stage-receipts/S4.json`
- `evaluation/country-outage/p1-runtime-v2/manifest.json`
