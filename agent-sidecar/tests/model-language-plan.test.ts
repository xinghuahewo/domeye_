import assert from 'node:assert/strict'
import test from 'node:test'

import { assembleCountryOutageFacts } from '../src/domain/observation-assembler.js'
import {
  buildDeterministicCountryOutageDraft,
  DeterministicAcceptanceNarrator,
} from '../src/report/deterministic-narrator.js'
import { validateReportDraft } from '../src/report/draft-validator.js'
import {
  buildCountryOutageModelLanguagePlan,
  COUNTRY_OUTAGE_LANGUAGE_SLOT_CONTRACT_VERSION,
  CountryOutageModelLanguageError,
  mergeCountryOutageLanguageSlots,
  parseCountryOutageLanguageSlotBundle,
  type CountryOutageLanguageSlotBundle,
  type CountryOutageLanguageSlotId,
  type CountryOutageModelLanguageErrorCode,
  type CountryOutageModelLanguagePlanItem,
} from '../src/report/model-language-plan.js'
import type {
  CountryOutageReportDraft,
  ReportEvidenceBundle,
} from '../src/report/contracts.js'
import {
  A4_REFERENCE,
  a4AsnPage,
  a4ObservationBatch,
} from './helpers/a4-country-outage-fixture.js'

function evidence(): ReportEvidenceBundle {
  return {
    facts: assembleCountryOutageFacts(a4ObservationBatch()),
    asnPages: [a4AsnPage()],
  }
}

const VALID_SLOT_TEXT: Readonly<
  Record<CountryOutageLanguageSlotId, string>
> = Object.freeze({
  'scope.denominator_explanation':
    'Prefix×VP 描述前缀与固定观测点之间的可见关系；它并非唯一前缀，也不能换算为用户或业务数量。',
  'assessment.evidence_boundary':
    '本报告只支持 BGP 控制面可见性描述，不能据此判断全国数据面状态，也无法认定用户或业务影响、事件原因和责任主体。',
  'address_families.impact_boundary':
    '地址族指标属于路由控制面观测，不能直接换算为用户、业务或实际流量影响。',
  'updates.causality_boundary':
    '相关 UPDATE 活动与可见性变化只构成时间对应；现有证据不足以据此证明因果关系。',
  'resources.resource_boundary':
    '等价资源表示规范化、去重后的路由资源覆盖，并非实际在线 IP 地址，也不能换算成用户或业务数量。',
})

function rawBundle(
  plan: readonly CountryOutageModelLanguagePlanItem[],
  replacements: Partial<Record<CountryOutageLanguageSlotId, string>> = {},
): unknown {
  return {
    schemaVersion: COUNTRY_OUTAGE_LANGUAGE_SLOT_CONTRACT_VERSION,
    slots: plan.map((item) => ({
      id: item.id,
      text: replacements[item.id] ?? VALID_SLOT_TEXT[item.id],
    })),
  }
}

function errorCode(code: CountryOutageModelLanguageErrorCode) {
  return (error: unknown): boolean =>
    error instanceof CountryOutageModelLanguageError &&
    error.code === code
}

function paragraphText(
  draft: CountryOutageReportDraft,
  sectionId: string,
  paragraphIndex: number,
): string {
  const section = draft.sections.find((item) => item.id === sectionId)
  assert.ok(section)
  return section.paragraphs[paragraphIndex]!.text
}

function evidenceRefs(draft: CountryOutageReportDraft): string[][] {
  return [
    [...draft.summary.evidenceRefs],
    ...draft.highlights.map((item) => [...item.evidenceRefs]),
    ...draft.sections.flatMap((section) =>
      section.paragraphs.map((paragraph) => [
        ...paragraph.evidenceRefs,
      ]),
    ),
  ]
}

test('确定性基稿纯函数与验收叙述器生成完全相同的 v5 草稿', async () => {
  const input = evidence()
  const direct = buildDeterministicCountryOutageDraft(input)
  const narrated = await new DeterministicAcceptanceNarrator().generate({
    reference: A4_REFERENCE,
    evidence: input,
  })

  assert.deepEqual(direct, narrated)
  const validation = validateReportDraft(direct, input)
  assert.equal(validation.passed, true, validation.errors.join('\n'))
})

test('完整与能力降级草稿生成固定顺序、精确字段的语言槽计划', () => {
  const full = buildDeterministicCountryOutageDraft(evidence())
  const plan = buildCountryOutageModelLanguagePlan(full)

  assert.deepEqual(
    plan.map((item) => item.id),
    [
      'scope.denominator_explanation',
      'assessment.evidence_boundary',
      'address_families.impact_boundary',
      'updates.causality_boundary',
      'resources.resource_boundary',
    ],
  )
  for (const item of plan) {
    assert.deepEqual(Object.keys(item).sort(), [
      'id',
      'maxLength',
      'minLength',
      'paragraphIndex',
      'requiredSemanticIds',
      'sectionId',
      'seedText',
    ])
    assert.ok(item.requiredSemanticIds.length > 0)
    assert.ok(item.minLength > 0)
    assert.ok(item.maxLength > item.minLength)
  }

  const degraded = structuredClone(full)
  degraded.sections = degraded.sections.filter(
    (section) =>
      ![
        'address_families',
        'updates',
        'resources',
      ].includes(section.id),
  )
  assert.deepEqual(
    buildCountryOutageModelLanguagePlan(degraded).map(
      (item) => item.id,
    ),
    [
      'scope.denominator_explanation',
      'assessment.evidence_boundary',
    ],
  )
})

test('合法语言槽包原子替换白名单正文且最终仍通过 v5', () => {
  const input = evidence()
  const base = buildDeterministicCountryOutageDraft(input)
  const baseBefore = structuredClone(base)
  const plan = buildCountryOutageModelLanguagePlan(base)
  const parsed = parseCountryOutageLanguageSlotBundle(
    rawBundle(plan),
    plan,
    input.facts.event,
  )
  const merged = mergeCountryOutageLanguageSlots(base, plan, parsed)

  assert.deepEqual(base, baseBefore)
  assert.equal(
    parsed.schemaVersion,
    COUNTRY_OUTAGE_LANGUAGE_SLOT_CONTRACT_VERSION,
  )
  assert.deepEqual(
    parsed.slots.map((slot) => slot.id),
    plan.map((item) => item.id),
  )
  for (const item of plan) {
    assert.equal(
      paragraphText(merged, item.sectionId, item.paragraphIndex),
      VALID_SLOT_TEXT[item.id],
    )
  }
  assert.equal(merged.title, base.title)
  assert.equal(merged.subtitle, base.subtitle)
  assert.deepEqual(merged.summary, base.summary)
  assert.deepEqual(merged.highlights, base.highlights)
  assert.deepEqual(merged.unknowns, base.unknowns)
  assert.deepEqual(evidenceRefs(merged), evidenceRefs(base))
  const validation = validateReportDraft(merged, input)
  assert.equal(validation.passed, true, validation.errors.join('\n'))
})

test('语言槽包根、条目、ID 集合与顺序必须精确', async (context) => {
  const input = evidence()
  const draft = buildDeterministicCountryOutageDraft(input)
  const plan = buildCountryOutageModelLanguagePlan(draft)
  const valid = rawBundle(plan) as {
    schemaVersion: string
    slots: Array<{ id: string; text: string }>
  }
  const cases: Array<{
    name: string
    value: unknown
    code: CountryOutageModelLanguageErrorCode
  }> = [
    {
      name: '根对象额外字段',
      value: { ...valid, report: {} },
      code: 'language_bundle_schema_invalid',
    },
    {
      name: '条目额外字段',
      value: {
        ...valid,
        slots: valid.slots.map((item, index) =>
          index === 0 ? { ...item, evidenceRefs: [] } : item,
        ),
      },
      code: 'language_bundle_schema_invalid',
    },
    {
      name: '未知槽 ID',
      value: {
        ...valid,
        slots: valid.slots.map((item, index) =>
          index === 0 ? { ...item, id: 'scope.other' } : item,
        ),
      },
      code: 'language_bundle_schema_invalid',
    },
    {
      name: '缺少槽',
      value: { ...valid, slots: valid.slots.slice(0, -1) },
      code: 'language_bundle_slot_mismatch',
    },
    {
      name: '槽顺序变化',
      value: {
        ...valid,
        slots: [valid.slots[1], valid.slots[0], ...valid.slots.slice(2)],
      },
      code: 'language_bundle_slot_mismatch',
    },
  ]

  for (const item of cases) {
    await context.test(item.name, () => {
      assert.throws(
        () =>
          parseCountryOutageLanguageSlotBundle(
            item.value,
            plan,
            input.facts.event,
          ),
        errorCode(item.code),
      )
    })
  }
})

test('语言槽正文拒绝非中文、多段、数字时间、外部结构、身份、AS 号和方向词', async (context) => {
  const input = evidence()
  const draft = buildDeterministicCountryOutageDraft(input)
  const plan = buildCountryOutageModelLanguagePlan(draft)
  const id = 'scope.denominator_explanation' as const
  const safe = VALID_SLOT_TEXT[id]
  const cases: Array<{ name: string; text: string }> = [
    { name: '前后空白', text: ` ${safe}` },
    { name: '多段', text: `${safe}\n补充一段说明。` },
    {
      name: '英文叙事',
      text: `${safe} This is an English narrative.`,
    },
    { name: '普通数字', text: `${safe} 共包含 12 项。` },
    { name: '日期时间', text: `${safe} 发生在二十时。` },
    { name: 'URL', text: `${safe} 详见 https://example.com。` },
    { name: 'HTML', text: `${safe}<strong>说明</strong>` },
    { name: 'Markdown', text: `${safe} **重点说明**。` },
    { name: '事件国家身份', text: `${safe} 这里指伊朗。` },
    { name: '事件引用身份', text: `${safe} ${A4_REFERENCE}` },
    { name: 'AS 号', text: `${safe} 例如 AS123。` },
    { name: '方向词', text: `${safe} 随后出现下降。` },
    { name: '长度不足', text: '这是简短说明。' },
    { name: '长度超限', text: `${safe}${'补充说明'.repeat(50)}` },
  ]

  for (const item of cases) {
    await context.test(item.name, () => {
      assert.throws(
        () =>
          parseCountryOutageLanguageSlotBundle(
            rawBundle(plan, { [id]: item.text }),
            plan,
            input.facts.event,
          ),
        errorCode('language_slot_text_invalid'),
      )
    })
  }
})

test('每个语言槽必须覆盖自身的固定语义锚点', async (context) => {
  const input = evidence()
  const draft = buildDeterministicCountryOutageDraft(input)
  const plan = buildCountryOutageModelLanguagePlan(draft)
  const cases: Array<{
    id: CountryOutageLanguageSlotId
    text: string
  }> = [
    {
      id: 'scope.denominator_explanation',
      text:
        'Prefix×VP 描述前缀与固定观测点之间的可见关系，这是一项用于帮助阅读报告的控制面口径说明。',
    },
    {
      id: 'assessment.evidence_boundary',
      text:
        '本报告描述 BGP 控制面可见性，并说明证据适用范围以及阅读口径；相关判断仍应严格依据固定证据。',
    },
    {
      id: 'address_families.impact_boundary',
      text:
        '地址族指标属于路由控制面观测，相关说明用于帮助理解报告中的指标口径。',
    },
    {
      id: 'updates.causality_boundary',
      text:
        '相关 UPDATE 活动与可见性变化形成时间对应，这一描述用于说明观察顺序和阅读口径。',
    },
    {
      id: 'resources.resource_boundary',
      text:
        '等价资源表示规范化、去重后的路由资源覆盖，这是一项用于解释指标口径的说明。',
    },
  ]

  for (const item of cases) {
    await context.test(item.id, () => {
      assert.throws(
        () =>
          parseCountryOutageLanguageSlotBundle(
            rawBundle(plan, { [item.id]: item.text }),
            plan,
            input.facts.event,
          ),
        errorCode('language_slot_semantic_invalid'),
      )
    })
  }
})

test('合并拒绝被篡改的计划和绕过解析器注入的事实数字', () => {
  const input = evidence()
  const base = buildDeterministicCountryOutageDraft(input)
  const plan = buildCountryOutageModelLanguagePlan(base)
  const parsed = parseCountryOutageLanguageSlotBundle(
    rawBundle(plan),
    plan,
    input.facts.event,
  )
  const tamperedPlan = plan.map((item) => ({
    ...item,
    requiredSemanticIds: [...item.requiredSemanticIds],
  }))
  tamperedPlan[0] = {
    ...tamperedPlan[0]!,
    paragraphIndex: 0,
  }
  assert.throws(
    () => mergeCountryOutageLanguageSlots(base, tamperedPlan, parsed),
    errorCode('language_plan_invalid'),
  )

  const bypassed = structuredClone(
    parsed,
  ) as unknown as CountryOutageLanguageSlotBundle & {
    slots: Array<{ id: CountryOutageLanguageSlotId; text: string }>
  }
  bypassed.slots[0]!.text = `${VALID_SLOT_TEXT[
    'scope.denominator_explanation'
  ]} 另有 999 项。`
  assert.throws(
    () => mergeCountryOutageLanguageSlots(base, plan, bypassed),
    errorCode('language_slot_merge_invariant_failed'),
  )
})
