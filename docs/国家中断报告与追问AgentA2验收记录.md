# 国家中断报告与追问 Agent A2 验收记录

版本：1.6  
阶段：A2  
验收范围：RG-05、RG-06、RG-08、RG-12、RG-13 的基础报告部分，以及
SCE-01、SCE-02、SCE-03、SCE-09 的生成侧

> 1.5 以前的 validator、fact set、项目知识 v2/v3/v4/v5 和对应重放制品只保留为
> 历史候选证据。v5 证据目录
> `artifacts/country-outage-agent/a2-v5-project-knowledge-20260730T003500+0800/`
> 已被本次 v6 证据取代，不能再用于证明当前生成协议通过 A2。当前有效证据位于
> `artifacts/country-outage-agent/a2-v6-language-slots-20260730T083322Z-final/`。

## 一、阶段结论

- 代表性合法事件已从 Domeye 只读 API 重新读取并生成面向人的中文报告；
- 报告只使用唯一 RRC25 固定快照，不读取数据库、Codex 对话、Codex 记忆、现成
  报告或互联网；
- 宿主先根据固定证据构造完整确定性基稿，基稿通过
  `country_outage_report_validator_rules_v5` 后，模型才有资格处理少量、事件无关
  的解释性语言槽；
- 模型不再生成 `country_outage_report_draft_v1`，也不能拥有标题、摘要、数字、
  方向、章节、证据引用、不能回答项或事件身份；
- 语言槽必须整体通过严格结构、文本安全和语义校验，再原子合并；不允许部分采用、
  逐槽回退或静默发布未应用模型文本的确定性基稿；
- 合并后必须证明结构、非槽正文、引用和事实数字等不变，并再次通过 validator v5；
- Markdown 与 PDF 来自同一个已校验 `ReportDocument`，共享报告身份并分别计算
  SHA-256；
- 扩展能力缺失只会由宿主省略相应章节和语言槽；最低数据不足、快照冲突、基稿
  失败、槽包失败或最终校验失败都不发布半成品；
- Pi SDK 0.82.1 仍只加载固定版本 Skill、只注册三个绑定事件与快照的只读工具；
- 本阶段只验收基础报告生成逻辑和确定性制品，不表示 DeepSeek 或其他真实模型组合
  已通过认证，也不表示 A3 或 A4 已通过。

## 二、报告生成逻辑

### 2.1 确定性事实、完整基稿与报告制品

`CountryOutageReportCompiler` 只接受 A1 固定批次。宿主装配事实集合和确定性派生
事实后，先构造完整 `country_outage_report_draft_v1` 基稿，并在任何模型会话开始
前执行 validator v5：

1. 校验事件、publication、revision、窗口、cohort 和唯一 RRC25；
2. 校验章节与能力状态一致；
3. 校验所有数字都来自事实集合或带操作数、公式、单位和 `factId` 的确定性派生事实；
4. 校验所有证据引用真实存在且定位规范；
5. 校验标题、摘要、正文和结论的方向一致；
6. 禁止把控制面观测写成全国断网、用户或业务影响、原因、责任、事件结束或完全恢复；
7. 基稿不合法时在模型会话和外部调用之前失败关闭。

最大至最小 IPv4 `/24` 等价资源变化、起点至最低点变化、结束相对起点差值和 ASN
持续时间等数字均在事实装配层确定，模型不现场计算。ASN 持续时间由固定槽数和固定
槽宽计算，并保留具体 ASN 项的证据定位。

完整报告通过最终校验后，宿主一次性形成只读
`country_outage_report_document_v1`，再从同一文档独立构建 Markdown 和 PDF。
任一下载格式构建失败不会伪造另一格式成功，但未通过最终校验的文档不能生成任何
正式制品。

### 2.2 v6 语言槽协议

项目知识 v6 把模型职责压缩为 `country_outage_language_slots_v1`。代表性事件的完整
基稿包含五个白名单解释槽：

- `scope.denominator_explanation`：解释 Prefix×VP 口径；
- `assessment.evidence_boundary`：解释 BGP 控制面证据边界；
- `address_families.impact_boundary`：解释地址族指标不能换算成用户或流量影响；
- `updates.causality_boundary`：解释时间对应不等于因果；
- `resources.resource_boundary`：解释等价资源不是在线 IP 或用户数量。

前两个槽是基础报告必需槽；后三个只在对应扩展能力和章节存在时由宿主加入计划。
因此，代表性事件要求五个槽，能力降级事件可能要求更少。槽 ID、数量和顺序始终由
宿主决定，模型不能自行省略、新增、重复或重排。

模型响应采用 exact schema：

```text
{
  "schemaVersion": "country_outage_language_slots_v1",
  "slots": [
    { "id": "宿主指定的槽 ID", "text": "一段简体中文说明" }
  ]
}
```

根对象只能有 `schemaVersion` 和 `slots`，每个槽只能有 `id` 和 `text`。每段文字
必须满足宿主规定的长度和语义锚点，只能解释指标口径或证据边界，并且：

- 不得包含事件数字、计数、比例、日期、时间或其他普通数字；`RRC25`、`IPv4`、
  `IPv6`、`/24`、`/48` 仅作为宿主明确允许的技术标识；
- 不得包含上升、下降、增加、减少、回升、持平、恢复、结束等方向或状态判断；
- 不得包含国家、事件、publication、revision、具体 ASN 或其他事件身份；
- 不得包含 URL、外部来源、HTML、Markdown、提示词、工具过程或思考过程；
- 不得认定全国中断、用户或业务影响、原因、责任、攻击、政策、配置错误或故障。

不满足 exact schema、槽集合、文本安全或必含语义中的任一条件，整包拒绝。

### 2.3 原子合并与失败关闭

宿主只把已通过槽级校验的 `text` 写入语言计划指定的白名单段落。合并是整包原子
操作，合并前后必须保持以下内容不变：

- 报告 JSON 结构、章节 ID、顺序和全部证据引用；
- 标题、副标题、摘要、关键数字和 `unknowns`；
- 全部事件身份、具体数字、时间和方向结论；
- 所有未开放槽正文。

宿主比较非槽投影和报告中的事实数字令牌，任何不一致都以
`language_slot_merge_invariant_failed` 失败关闭。原子合并完成后，完整草稿还必须
再次通过 validator v5，随后才可以形成 `ReportDocument`。

初次槽包失败时，Pi 路径最多允许一次关闭全部工具后的整包修订；修订仍失败、合并
不变式失败或最终 validator v5 失败时，整份报告拒绝。协议明确禁止：

- 只采用部分有效槽；
- 按槽逐个修补或回退；
- 把失败槽替换为种子文本后继续发布；
- 静默跳过模型并把确定性基稿冒充为模型生成结果。

### 2.4 项目知识替代隐藏记忆

版本化 Skill 位于：

- `agent-sidecar/resources/skills/country-outage-report/SKILL.md`；
- `agent-sidecar/resources/skills/country-outage-report/references/metrics-and-boundaries.md`；
- `agent-sidecar/resources/skills/country-outage-report/references/report-output-contract.md`。

Skill 只定义指标语义、语言槽合同、表达边界和失败规则，不包含伊朗事件的冻结数字、
ASN、现成结论或完整报告范文。事实、数字、方向、章节和证据引用全部由宿主根据
当前固定快照确定；模型 API、模型记忆、Codex 记忆和互联网都不能改变正式事实。

Pi 叙述器继续使用：

- 精确版本 `@earendil-works/pi-coding-agent@0.82.1`；
- `SessionManager.inMemory` 和 `SettingsManager.inMemory`；
- 唯一受信任的 `country-outage-report` Skill；
- `country_outage_resolve`、`country_outage_get_observation` 和
  `country_outage_get_asns` 三个绑定当前事件与快照的只读工具；
- `noTools=builtin`，并显式排除 read、bash、edit、write、grep、find 和 ls；
- 空 extensions、prompts、AGENTS/context 和持久会话；
- `AbortSignal` 到 Pi 会话中止的失败关闭。

语言槽生成本身只需要事件解析和观测事实，不为润色措辞重复读取 ASN。不同模型/API
的差异由同一事实合同、同一项目知识、同一 exact schema、同一合并不变式、同一
发布前校验和逐模型认证吸收。

## 三、代表性事件生成结果

事件：`country_outage/2026-02-27 09:12:32/IR/1/r`

| 项目 | 结果 |
|---|---|
| incident | `incident_go_v1_a1de26f854831330c616a72af21597eb` |
| publication | `publication_v1_38bddead083db3f49023c2e1` |
| revision | `1` |
| collector | `rrc25` |
| fact set | `facts_44ce6ba951e4774835b0459eabf186e6` |
| report artifact | `report_827dffaa205b24516d3f9de166b63c9a` |
| report content SHA-256 | `b86feb2e8b4cc93c23ea96c201a8a8fe4f6a6eabf3fa41a2f28dabad627dde49` |
| validator | `country_outage_report_validator_rules_v5` |
| 项目知识 | `country_outage_report_skill_v6` |
| Skill 包摘要 | `5f108d26f39dea9ff5a2902b00cdb113e3a76a8afd1b6560dffd7e9453d3a88d` |
| 机器校验 | 通过，0 个错误、0 个警告 |
| Markdown | 8,248 bytes，SHA-256 `0ff82e9f61bf1f5399d883cc5e42ea29e2e68f783e0d5a7783925a4390991588` |
| PDF | A4、4 页、151,965 bytes，SHA-256 `821a354f22762c2c64a97ed457408be2598300fc6786c6d1b943da40e9f63d3e` |
| audit manifest | 21,675 bytes，SHA-256 `56a89fe2ea92644a14a6afb18775b1839e9c5a645eabee6525c80eff4860a18c` |
| report document | SHA-256 `4a1fb7fe0451fc4b65863bfce20305190200fbc2f8ef970c9c307b9250219caa` |
| artifact manifest | SHA-256 `5036a6ef86625625a71af25980921193031d63aeaa34184b76fcdb51269433a2` |

报告正文仍保留此前人工认可的阅读顺序：

- 标题、副标题和窗口结论；
- RRC25、固定人口、Prefix×VP 解释和控制面边界；
- 起点、最低点、结束点、最大下降、ASN 峰值和 UPDATE 峰值；
- ASN 连续全不可见时间、IPv4/IPv6、UPDATE 时间对应、结束状态和等价资源；
- 综合判断与明确不能回答的问题。

报告未把 UPDATE 与下降写成因果，未认定全国断网、用户影响、责任主体、事件结束
或完全恢复。

## 四、下载与视觉验收

当前有效制品位于
`artifacts/country-outage-agent/a2-v6-language-slots-20260730T083322Z-final/`：

- `replay-1/` 和 `replay-2/`：两个独立主进程生成的 Markdown、PDF、
  `report-document.json`、`manifest.json` 和 `audit-manifest.json`；
- `clean-replay/`：在隔离目录、无 Codex 记忆和无现成报告条件下生成的相同五个文件；
- `rendered-pages/`：四页 PDF 的逐页 PNG。

三次重放的五个文件均逐字节一致；各文件 SHA-256 与第三节表格一致。PDF 使用 A4
页面，共 4 页，不包含 JavaScript。逐页视觉检查已通过：

- 中文、英文、数字、Prefix×VP 和证据定位均可读；
- 标题层级和关键数字表完整；
- 无截断、越界、重叠、空白页或缺字；
- 页眉、页脚、页码和制品身份完整。

PDF 生成仍保留 20 秒默认超时、10 MiB 输入/输出上限和 40 页上限，并使用受信任
Python 解释器和明确配置的中文字体。Markdown 与 PDF 的生成结果独立记录；任一
格式失败不会覆盖另一成功格式。

## 五、测试与干净重放

当前工作区全量测试结果：

```text
547/547 tests passed
0 failed
```

覆盖范围包括：

- 事实公式、快照冲突、能力降级和稳定身份；
- 数字幻觉、无效证据引用、越界结论和方向不一致；
- 确定性基稿必须在模型会话前通过 validator v5；
- 语言槽动态计划、exact schema、槽集合和顺序；
- 普通数字、方向、事件身份、ASN、URL、HTML、Markdown 和外部来源注入拒绝；
- 槽语义锚点、原子合并、非槽投影和事实数字不变式；
- 不允许部分采用或静默确定性 fallback；
- Pi 工具白名单、资源隔离、中止和失败关闭；
- 两种制品独立成败、PDF 字体/文本、超时、大小和页数上限；
- 旧项目知识或跨文件身份漂移不得晋级。

隔离重放路径为：

`/tmp/domeye-a2-v6-current-clean-replay.OgxrUW/core-work`

该重放不复制 Codex 内容、现成报告或历史 A2 制品，只使用当前源码、锁文件、版本化
Skill 和验收配置。隔离环境完成依赖安装、547/547 全量测试和代表性事件重放，得到
与两个主进程完全一致的：

- fact set：`facts_44ce6ba951e4774835b0459eabf186e6`；
- report artifact：`report_827dffaa205b24516d3f9de166b63c9a`；
- report content SHA-256：
  `b86feb2e8b4cc93c23ea96c201a8a8fe4f6a6eabf3fa41a2f28dabad627dde49`；
- 五个输出文件及其 SHA-256。

三次重放使用同一固定生成时间，因此 Markdown、PDF、报告文档和两类清单保持逐
字节一致。

## 六、分阶段边界与后续项

A2 不验收完整问答、外部证据、用户权限隔离、队列运行、生产部署或正式模型组合。
本记录中的语言槽协议通过，表示宿主边界和本地失败关闭逻辑具备可验收证据，不表示
`deepseek-v4-flash` 已产生合格槽包，也不表示真实付费模型认证通过。

Pi 0.82.1 的 GHSA-mh99-v99m-4gvg 风险例外已由用户批准至 2026-08-12。正式路径
继续关闭 PackageManager、ModelResolver 和外部 glob，只使用固定 Skill 与三个
只读工具；Pi 发布可用修复版本或正式路径变化时必须立即复评。该限期例外不等于
生产安全、生产部署或正式模型认证通过。

## 七、阶段出口判定

- RG-05：面向人的稳定中文报告已由代表性事件生成并逐页视觉核对；
- RG-06：控制面、因果、影响、责任和恢复边界已通过正反测试；
- RG-08：项目知识 v6 已版本化，干净重放不依赖 Codex 记忆、互联网或现成正文；
- RG-12：确定性基稿、语言槽、原子合并、数字、引用、章节、能力和最终语义校验
  共同决定是否发布；两种下载来自同一已校验文档；
- RG-13 基础部分：身份、项目知识、报告规范、事实集合、语言槽合同、校验结果和
  制品摘要可审计；
- SCE-01：合法事件正常生成；
- SCE-02：最低数据不足拒绝，扩展能力缺失只降级对应章节和槽；
- SCE-03：接口身份冲突整批重读或失败，不混合快照；
- SCE-09 生成侧：单项失败不覆盖另一成功格式；
- 正式模型、完整问答、外部证据、运行安全和生产部署仍明确留在后续阶段，本文未
  提前宣告通过。

v6 A2 证据满足本阶段功能出口。2026-07-30 已从 `core-work` 运行 A2 防偏离
Hook，并据本记录逐项复核 RG-05、RG-06、RG-08、RG-12、RG-13 与 SCE-01、
SCE-02、SCE-03、SCE-09。不得把本次 Hook 回检解释为 A3、A4、真实模型或生产
验收通过。

国家中断报告 Agent 最终验收回检：A2 已修正
