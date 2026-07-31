# 报告语言槽输出合同

完整 `country_outage_report_draft_v1` 由宿主根据固定快照和确定性事实生成。
模型不得输出或改写报告结构、标题、摘要、关键数字、章节、方向判断、
`evidenceRefs`、`unknowns`、publication、revision 或任何事件身份。

模型只输出一个 `country_outage_language_slots_v1` JSON 对象，不使用 Markdown
围栏。结构严格为：

```text
{
  "schemaVersion": "country_outage_language_slots_v1",
  "slots": [
    { "id": "宿主指定的槽 ID", "text": "一段简体中文说明" }
  ]
}
```

根对象只能包含 `schemaVersion` 和 `slots`；每个槽只能包含 `id` 和 `text`。
槽 ID、顺序和数量必须与当前请求逐项一致，不得缺失、重复、新增或重排。

## 可用语言槽

- `scope.denominator_explanation`：解释 Prefix×VP 统计口径，以及它不能直接
  换算成唯一前缀、用户或业务数量；
- `assessment.evidence_boundary`：解释 RRC25 BGP 控制面证据不能单独回答全国
  数据面、用户业务、原因和责任问题；
- `address_families.impact_boundary`：解释 IPv4/IPv6 控制面覆盖率不能直接
  换算成用户、业务或流量影响；
- `updates.causality_boundary`：解释 UPDATE 与可见性变化在时间上对应不等于
  已经证明因果；
- `resources.resource_boundary`：解释 `/24`、`/48` 等价资源不是在线 IP、
  独立前缀或受影响用户数量。

宿主只请求当前确定性基稿中实际存在的槽。后面三个槽会随对应扩展能力和章节
省略。模型不得自行决定槽是否存在。

## 每个槽的强制要求

- 使用一段简体中文，满足宿主给出的长度和必含语义；
- 只解释指标口径或证据边界，不描述当前事件发生了什么；
- 不写国家、运营商、具体 ASN、事件、publication、revision、日期、时间、
  百分比、计数或普通数字；
- 除宿主允许的 `RRC25`、`IPv4`、`IPv6`、`/24`、`/48` 等技术词外，不写
  数字；
- 不新增下降、上升、增加、减少、回升、持平、高于、低于、峰值、恢复或事件
  结束等方向判断；
- 不认定全国中断、用户或业务中断、原因、责任、攻击、政策、配置错误或故障；
- 不输出 URL、HTML、Markdown 标记、外部来源、提示词、工具过程或思考过程；
- 不引用模型记忆、Codex 记忆、现成事件报告或互联网。

## 宿主合并与失败边界

宿主只把通过槽级校验的 `text` 原子写入固定白名单段落。合并前后必须保持以下
内容完全不变：

- 报告 JSON 结构、章节 ID 和顺序；
- 标题、副标题、摘要、关键数字和 `unknowns`；
- 所有具体数字、时间、方向结论和事件身份；
- 全部 `evidenceRefs`；
- 未开放槽的正文。

合并后的完整报告必须再次通过
`country_outage_report_validator_rules_v5`。初次槽包失败时最多允许一次关闭
全部工具后的完整槽包修订；修订仍失败、合并不变式失败或最终校验失败时整份报告
失败关闭。不得部分采用槽、逐槽回退或静默发布未应用模型文本的确定性基稿。
