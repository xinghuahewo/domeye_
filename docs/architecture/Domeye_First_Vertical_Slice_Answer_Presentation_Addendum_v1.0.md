# Domeye 首个纵向切片回答呈现附加合同 v1.0

> 本合同只改变首个纵向切片的 Answer Context、Renderer Draft、用户正文与 Response Guard 呈现边界。它不修改旧锚点合同、数据身份、Tool、Operator、Typed Finding、数值 oracle、J1–J5 或非完成语义。

| 项目 | 固定值 |
|---|---|
| 合同版本 | `domeye.first-vertical-slice.answer-presentation/v1.0` |
| 稳定路径 | `docs/architecture/Domeye_First_Vertical_Slice_Answer_Presentation_Addendum_v1.0.md` |
| 被附加合同 | `domeye.first-vertical-slice/v1.0` |
| 状态 | 合同已形成；实现、真实评测、独立验收与发布仍须由新 Candidate 证明 |
| 适用出口 | 第一轮只适用于当前 Interactive Agent |

## 1. 适用顺序

[首个纵向切片锚点合同 v1.0](Domeye_First_Vertical_Slice_Anchor_v1.0.md) 保持原文件、版本和摘要不变。

两份合同按以下顺序解释：

1. 固定问题、RRC25 数据身份、TOOL-03、OP-01、Typed Finding、数值 oracle、J1–J5、DG1 和禁止结论继续以锚点 v1.0 为准；
2. Answer Context、Renderer Draft、用户可见正文、Response Guard 风格门和确定性回退呈现以本附加合同为准；
3. 本合同不把 Tool 或 Operator 合同提升为 v1.1，也不把旧 Candidate 自动升级为新回答风格；
4. 旧合同要求默认展示完整身份、全部 limitation 和 Evidence 的部分，只在用户正文呈现范围内由本合同替代；这些材料仍须完整保存在内部记录。

## 2. 用户最终看到的效果

成功正文按固定职责顺序组成：

```text
lead：直接给最低值和首次观测时间
fact_blocks：补齐首值、末值、最大值和极差
boundary：用一句自然中文合并必要边界
next_step：本首片固定为空
```

正文必须覆盖且只覆盖以下六个事实键，每个恰好一次：

- `minimum`
- `minimum_at_utc`
- `first`
- `last`
- `maximum`
- `difference`

同一个数值即使相等，也不能把两个不同事实键合并成一个。例如本切片的首值与最大值相等，仍须分别表达“首值”和“最大值”。

单位只显示一次，采用人能直接理解的“个唯一 IPv4 地址”。时间采用 Host 在 Answer Context 中给出的 UTC 中文显示值；Renderer 不自行换算时区、百分比、倍数、约数或其他派生量。

## 3. Answer Context v2

`domeye_agent_answer_context_v2` 是 Renderer 唯一可读的事实投影，只包含：

- 固定用户问题；
- 人类可读的指标和单位；
- 六个允许陈述的事实值及其固定中文显示值；
- 三项必须覆盖的用户边界；
- 禁止结论；
- 紧凑模式的机器篇幅约束。

它不得包含：

- Candidate、Finding、Context、Receipt 或 Artifact ID；
- commit、SHA、digest 或文件路径；
- publication、revision、内部数据身份对象；
- Evidence 引用、Trace、模型调用和费用账本；
- 原始数据库、内部 endpoint、凭据或旧对话事实。

完整 Typed Finding、数据身份和合同绑定仍由 Host 留在同一轮内部运行记录中，不复制给 Renderer。

## 4. Renderer Draft v2

`domeye_agent_renderer_draft_v2` 只允许以下结构：

```json
{
  "schema_version": "domeye_agent_renderer_draft_v2",
  "lead": { "fact_keys": [], "text": "" },
  "fact_blocks": [
    { "fact_keys": [], "text": "" }
  ],
  "boundary": { "boundary_codes": [], "text": "" },
  "next_step": null
}
```

约束如下：

- `lead.fact_keys` 恰为 `minimum` 与 `minimum_at_utc`；
- 其余四个事实键进入一至三个 `fact_blocks`；
- 六个事实键在所有表达块中各出现一次；
- 每个事实块正文必须按 `fact_keys` 顺序，将每个事实键的人类可读标签与该键自己的固定显示值直接配对；只同时出现标签和值集合不算合格；
- `boundary` 恰为一个块，并覆盖全部必要边界代码；
- 本首片 `next_step` 固定为 `null`；
- Draft 没有独立自由 `text` 字段，也没有内部身份字段。

表达块不是任意自由文本。Guard 使用确定性有限中文 grammar，只接受由合同标签、对应固定显示值、唯一单位、必要连接词和句末标点组成的事实短句；边界块只接受三项必要含义对应的合同句式。Renderer 可以在该 grammar 内选择连接词、标签允许变体、分块数量和事实块组合，因此不是逐字固定整段答案；任何未被 grammar 消费的评价、趋势、状态、结论或一般未知句子都必须阻断。

Renderer 仍是一次、零工具的模型调用。模型失败、非法 JSON 或 Schema 不合格时不重试。

## 5. Host 组装与最终正文

Host 在 Guard 之前按以下顺序确定性拼接表达块：

```text
lead.text
→ fact_blocks[*].text
→ boundary.text
→ 非空时的 next_step
```

块之间只加入换行，不删除、替换或重写 Renderer 文字。拼接后的同一正文同时用于：

- Response Guard 检查；
- Guard `pass` 后的最终 Answer；
- 内部记录中的最终文本摘要；
- 后续 Evaluator 与发布验证器的重算。

Guard 通过后，Backend、前端、正则或第二个模型均不得再次压缩、改写或重建正文。

## 6. 紧凑模式机器门

固定风格策略为 `domeye.answer-style.compact-first-slice/v1.0`。

字符数使用 `unicode-nfc-collapse-whitespace-intl-segmenter-zh-v1` 计算：先按 Unicode NFC 规范化，连续空白仅在计数时合并，再用 `Intl.Segmenter` 的中文 grapheme 边界计数。该算法身份、策略摘要和最终文本摘要必须写入内部评估记录。

硬门如下：

- `lead` 不超过 90 个 grapheme；
- 必要事实块不超过 3 个；
- 必要边界块恰为 1 个；
- 全文不超过 360 个 grapheme、6 句；
- 六个必要事实键覆盖率 100%，重复数为 0；
- 每个事实标签必须与自己的固定显示值直接配对，不能在同一块内交换；
- 三个必要边界代码覆盖率 100%，重复边界为 0；
- 单位缺失或重复为 0；
- Answer Context 外数字、时间、单位、换算、约数和外部断言为 0；
- 内部 ID、摘要、路径、endpoint、端口、凭据、Evidence 和审计栏目泄露为 0。
- 事实块和边界块在合同有限 grammar 外的剩余语义为 0。

这组机器门不判断文学风格。自然度和第一遍可读性仍由阶段 D/E 预注册的独立人工量表判断，不能覆盖任何机器失败。

## 7. 必要边界

一个 `boundary` 块必须同时覆盖：

| 代码 | 必须保留的含义 |
|---|---|
| `fixed_prefix_population_not_users` | 地址量是固定前缀 IPv4 唯一地址并集，不是用户数 |
| `rrc25_control_plane_observation_only` | 结果只是 RRC25 的 BGP 控制面观测 |
| `no_national_or_user_impact_cause_responsibility_recovery` | 不能据此判断全国状态、用户影响、原因、责任或恢复 |

Renderer 可以自然改写，但不能删掉任一含义，也不能在其他块重复边界。

## 8. Response Guard v2

`domeye_agent_response_guard_v2` 对 Host 已拼接的最终正文做确定性检查，并保留：

- `guarded_text` 与其摘要；
- 风格策略 ID、摘要和规范化算法 ID；
- grapheme、句数、事实块和边界块计数；
- 已覆盖、缺失和重复的事实键与边界代码；
- 内部泄露和 Answer Context 外内容分类；
- `pass/block` 与机器原因码。

只有风格评估真实执行且全部硬门通过时，Guard 才能返回 `pass`，并记录 `assessment_status=evaluated`。Renderer 未执行或输出非法时，记录 `assessment_status=not_evaluated` 且 `style_assessment` 明确为空，不能伪称已经评估或通过。

Guard 不生成事实、不改写正文、不再次调用模型，也不决定 DG1。

## 9. 失败与确定性回退

Renderer 失败或 Guard `block` 后：

- 原草稿不得发布；
- 不得再次调用 Renderer；
- 只形成内部失败闭包和简短安全失败文本；
- `answer_success=false`、`workflow_completed=false`；
- 不能计入 Pilot、正式 30 次或生产成功分子。

确定性回退不是用户问题的成功答案。完整 Finding、Context、草稿、Guard、Trace、Receipt 和 usage 留在受信内部记录，普通用户接口不返回它们。

## 10. Candidate 绑定

新 Candidate 使用 `domeye_first_slice_candidate_manifest_v2`，目标路径为：

```text
contracts/agent/domeye-first-vertical-slice/v1.1/candidate.json
```

Manifest 必须分别绑定：

- `contract.version/digest`：旧锚点 `domeye.first-vertical-slice/v1.0` 及其原路径摘要；
- `answer_presentation_contract.version/digest`：本附加合同及其固定路径摘要；
- Answer Context、Renderer、Host 拼接、Guard、公开回答和内部记录涉及的完整源码与 build 闭包。

旧 `v1/candidate.json` 保持历史不可变，不能被加载为新呈现合同的 Candidate。本阶段只准备 Schema 与生成器，不生成 Candidate；冻结、真实 3/3、正式 30/30、独立验收和生产晋级分别在后续阶段完成。

## 11. 明确不引入

本合同不引入：

- 新 Question Template 或同义问句路由；
- 固定表达 DAG、Common Plan IR 或通用 Publisher；
- 第二个回答判定模型；
- Guard 后改写层；
- 新证据平台或 Durable Workflow；
- 旧 P1/P2、`/rebind` 或旧 Sidecar 路由。

合同与代码存在只表示阶段 B/C 实现可进入离线验证，不表示新 Candidate 已冻结、真实评测已通过或生产已经部署。
