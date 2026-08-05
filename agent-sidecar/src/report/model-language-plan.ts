import type { EventIdentity } from '../domain/contracts.js'
import type {
  CountryOutageReportDraft,
  ReportSection,
} from './contracts.js'

export const COUNTRY_OUTAGE_LANGUAGE_SLOT_CONTRACT_VERSION =
  'country_outage_language_slots_v1' as const

export const COUNTRY_OUTAGE_LANGUAGE_SLOT_IDS = Object.freeze([
  'scope.denominator_explanation',
  'assessment.evidence_boundary',
  'address_families.impact_boundary',
  'updates.causality_boundary',
  'resources.resource_boundary',
] as const)

export type CountryOutageLanguageSlotId =
  (typeof COUNTRY_OUTAGE_LANGUAGE_SLOT_IDS)[number]

export type CountryOutageLanguageSemanticId =
  | 'prefix_vp_observation_relation'
  | 'prefix_vp_not_unique_prefix'
  | 'prefix_vp_not_user_business_count'
  | 'bgp_control_plane_boundary'
  | 'nationwide_data_plane_boundary'
  | 'user_business_impact_boundary'
  | 'cause_responsibility_boundary'
  | 'address_family_control_plane'
  | 'address_family_not_user_traffic'
  | 'update_temporal_relation_only'
  | 'update_no_causality'
  | 'resource_normalized_equivalence'
  | 'resource_not_online_ip'
  | 'resource_not_user_business_count'

export interface CountryOutageModelLanguagePlanItem {
  readonly id: CountryOutageLanguageSlotId
  readonly sectionId: ReportSection['id']
  readonly paragraphIndex: number
  readonly seedText: string
  readonly requiredSemanticIds:
    readonly CountryOutageLanguageSemanticId[]
  readonly minLength: number
  readonly maxLength: number
}

export interface CountryOutageLanguageSlot {
  readonly id: CountryOutageLanguageSlotId
  readonly text: string
}

export interface CountryOutageLanguageSlotBundle {
  readonly schemaVersion:
    typeof COUNTRY_OUTAGE_LANGUAGE_SLOT_CONTRACT_VERSION
  readonly slots: readonly CountryOutageLanguageSlot[]
}

export const COUNTRY_OUTAGE_MODEL_LANGUAGE_ERROR_CODES =
  Object.freeze([
    'language_plan_invalid',
    'language_bundle_schema_invalid',
    'language_bundle_slot_mismatch',
    'language_slot_text_invalid',
    'language_slot_semantic_invalid',
    'language_slot_merge_invariant_failed',
  ] as const)

export type CountryOutageModelLanguageErrorCode =
  (typeof COUNTRY_OUTAGE_MODEL_LANGUAGE_ERROR_CODES)[number]

const ERROR_MESSAGES: Readonly<
  Record<CountryOutageModelLanguageErrorCode, string>
> = Object.freeze({
  language_plan_invalid: '国家中断报告语言槽计划无效',
  language_bundle_schema_invalid: '模型语言槽响应结构无效',
  language_bundle_slot_mismatch: '模型语言槽集合或顺序不一致',
  language_slot_text_invalid: '模型语言槽正文不符合安全文本合同',
  language_slot_semantic_invalid: '模型语言槽正文缺少固定语义边界',
  language_slot_merge_invariant_failed:
    '模型语言槽合并改变了确定性报告合同',
})

export class CountryOutageModelLanguageError extends Error {
  constructor(
    readonly code: CountryOutageModelLanguageErrorCode,
    readonly slotId?: CountryOutageLanguageSlotId,
  ) {
    super(
      slotId === undefined
        ? ERROR_MESSAGES[code]
        : `${ERROR_MESSAGES[code]}：${slotId}`,
    )
    this.name = 'CountryOutageModelLanguageError'
  }
}

interface SlotDefinition extends CountryOutageModelLanguagePlanItem {
  readonly required: boolean
  readonly paragraphLocator?: 'last'
}

const SLOT_DEFINITIONS: readonly SlotDefinition[] = Object.freeze([
  Object.freeze({
    id: 'scope.denominator_explanation',
    sectionId: 'scope',
    paragraphIndex: 1,
    seedText:
      'Prefix×VP 描述前缀与固定 BGP 观测点之间的可见关系。它不是唯一前缀，也不能直接换算成用户或业务数量。',
    requiredSemanticIds: Object.freeze([
      'prefix_vp_observation_relation',
      'prefix_vp_not_unique_prefix',
      'prefix_vp_not_user_business_count',
    ]),
    minLength: 32,
    maxLength: 160,
    required: true,
  }),
  Object.freeze({
    id: 'assessment.evidence_boundary',
    sectionId: 'assessment',
    paragraphIndex: 1,
    paragraphLocator: 'last',
    seedText:
      '这份报告只支持 BGP 控制面可见性描述，不能据此判断全国数据面状态、用户或业务影响，也不能认定事件原因和责任主体。',
    requiredSemanticIds: Object.freeze([
      'bgp_control_plane_boundary',
      'nationwide_data_plane_boundary',
      'user_business_impact_boundary',
      'cause_responsibility_boundary',
    ]),
    minLength: 40,
    maxLength: 180,
    required: true,
  }),
  Object.freeze({
    id: 'address_families.impact_boundary',
    sectionId: 'address_families',
    paragraphIndex: 1,
    seedText:
      '地址族指标属于路由控制面观测，不能直接换算为用户、业务或实际流量影响。',
    requiredSemanticIds: Object.freeze([
      'address_family_control_plane',
      'address_family_not_user_traffic',
    ]),
    minLength: 24,
    maxLength: 120,
    required: false,
  }),
  Object.freeze({
    id: 'updates.causality_boundary',
    sectionId: 'updates',
    paragraphIndex: 1,
    seedText:
      '相关 UPDATE 活动与可见性变化仅表现为时间对应；这种对应值得关注，但现有证据不足以证明因果关系。',
    requiredSemanticIds: Object.freeze([
      'update_temporal_relation_only',
      'update_no_causality',
    ]),
    minLength: 28,
    maxLength: 140,
    required: false,
  }),
  Object.freeze({
    id: 'resources.resource_boundary',
    sectionId: 'resources',
    paragraphIndex: 1,
    seedText:
      '等价资源表示规范化、去重后的路由资源覆盖，并非实际在线 IP 地址，也不能换算成用户或业务数量。',
    requiredSemanticIds: Object.freeze([
      'resource_normalized_equivalence',
      'resource_not_online_ip',
      'resource_not_user_business_count',
    ]),
    minLength: 28,
    maxLength: 140,
    required: false,
  }),
]) as readonly SlotDefinition[]

function isObject(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

function hasExactKeys(
  value: Record<string, unknown>,
  expected: readonly string[],
): boolean {
  const actual = Object.keys(value).sort()
  const normalizedExpected = [...expected].sort()
  return (
    actual.length === normalizedExpected.length &&
    actual.every((key, index) => key === normalizedExpected[index])
  )
}

function definitionFor(
  id: CountryOutageLanguageSlotId,
): SlotDefinition {
  const definition = SLOT_DEFINITIONS.find((item) => item.id === id)
  if (!definition) {
    throw new CountryOutageModelLanguageError(
      'language_plan_invalid',
      id,
    )
  }
  return definition
}

function planItemMatchesDefinition(
  value: CountryOutageModelLanguagePlanItem,
  definition: SlotDefinition,
): boolean {
  const paragraphIndexMatches = definition.paragraphLocator === 'last'
    ? Number.isSafeInteger(value.paragraphIndex) &&
      value.paragraphIndex >= 0
    : value.paragraphIndex === definition.paragraphIndex
  return (
    hasExactKeys(value as unknown as Record<string, unknown>, [
      'id',
      'sectionId',
      'paragraphIndex',
      'seedText',
      'requiredSemanticIds',
      'minLength',
      'maxLength',
    ]) &&
    value.id === definition.id &&
    value.sectionId === definition.sectionId &&
    paragraphIndexMatches &&
    value.seedText === definition.seedText &&
    JSON.stringify(value.requiredSemanticIds) ===
      JSON.stringify(definition.requiredSemanticIds) &&
    value.minLength === definition.minLength &&
    value.maxLength === definition.maxLength
  )
}

function assertPlanShape(
  plan: readonly CountryOutageModelLanguagePlanItem[],
): void {
  if (!Array.isArray(plan)) {
    throw new CountryOutageModelLanguageError('language_plan_invalid')
  }
  const ids = plan.map((item) => item?.id)
  const expectedOrder = SLOT_DEFINITIONS.filter((definition) =>
    ids.includes(definition.id),
  ).map((definition) => definition.id)
  if (
    ids.length !== new Set(ids).size ||
    JSON.stringify(ids) !== JSON.stringify(expectedOrder) ||
    SLOT_DEFINITIONS.filter((item) => item.required).some(
      (item) => !ids.includes(item.id),
    )
  ) {
    throw new CountryOutageModelLanguageError('language_plan_invalid')
  }
  for (const item of plan) {
    const rawItem = item as unknown
    if (
      !isObject(rawItem) ||
      typeof rawItem.id !== 'string' ||
      !COUNTRY_OUTAGE_LANGUAGE_SLOT_IDS.includes(
        rawItem.id as CountryOutageLanguageSlotId,
      )
    ) {
      throw new CountryOutageModelLanguageError('language_plan_invalid')
    }
    const typedItem =
      rawItem as unknown as CountryOutageModelLanguagePlanItem
    const definition = definitionFor(typedItem.id)
    if (!planItemMatchesDefinition(typedItem, definition)) {
      throw new CountryOutageModelLanguageError(
        'language_plan_invalid',
        typedItem.id,
      )
    }
  }
}

export function buildCountryOutageModelLanguagePlan(
  draft: CountryOutageReportDraft,
): readonly CountryOutageModelLanguagePlanItem[] {
  const plan: CountryOutageModelLanguagePlanItem[] = []
  for (const definition of SLOT_DEFINITIONS) {
    const matchingSections = draft.sections.filter(
      (section) => section.id === definition.sectionId,
    )
    if (matchingSections.length === 0 && !definition.required) continue
    if (matchingSections.length !== 1) {
      throw new CountryOutageModelLanguageError(
        'language_plan_invalid',
        definition.id,
      )
    }
    const paragraphIndex = definition.paragraphLocator === 'last'
      ? matchingSections[0]!.paragraphs.length - 1
      : definition.paragraphIndex
    if (matchingSections[0]!.paragraphs[paragraphIndex] === undefined) {
      throw new CountryOutageModelLanguageError(
        'language_plan_invalid',
        definition.id,
      )
    }
    plan.push(
      Object.freeze({
        id: definition.id,
        sectionId: definition.sectionId,
        paragraphIndex,
        seedText: definition.seedText,
        requiredSemanticIds: Object.freeze([
          ...definition.requiredSemanticIds,
        ]),
        minLength: definition.minLength,
        maxLength: definition.maxLength,
      }),
    )
  }
  assertPlanShape(plan)
  return Object.freeze(plan)
}

const explicitNegation =
  /不能|不可|无法|不足以|不得|不应|并非|不是|不代表|不等于|不能据此|无法据此/

const SEMANTIC_CHECKS: Readonly<
  Record<CountryOutageLanguageSemanticId, (text: string) => boolean>
> = Object.freeze({
  prefix_vp_observation_relation: (text) =>
    /Prefix\s*[×x*]\s*VP/iu.test(text) &&
    /前缀/u.test(text) &&
    /观测点|可见关系|观测关系/u.test(text),
  prefix_vp_not_unique_prefix: (text) =>
    explicitNegation.test(text) && /唯一前缀/u.test(text),
  prefix_vp_not_user_business_count: (text) =>
    explicitNegation.test(text) &&
    /用户|业务/u.test(text) &&
    /数量|规模|换算/u.test(text),
  bgp_control_plane_boundary: (text) =>
    /BGP/iu.test(text) && /控制面/u.test(text),
  nationwide_data_plane_boundary: (text) =>
    explicitNegation.test(text) && /全国|数据面/u.test(text),
  user_business_impact_boundary: (text) =>
    explicitNegation.test(text) &&
    /用户|业务/u.test(text) &&
    /影响|状态|换算/u.test(text),
  cause_responsibility_boundary: (text) =>
    explicitNegation.test(text) &&
    /原因|因果/u.test(text) &&
    /责任/u.test(text),
  address_family_control_plane: (text) =>
    /地址族/u.test(text) && /控制面|路由/u.test(text),
  address_family_not_user_traffic: (text) =>
    explicitNegation.test(text) &&
    /用户|业务/u.test(text) &&
    /流量|影响|换算/u.test(text),
  update_temporal_relation_only: (text) =>
    /UPDATE/iu.test(text) &&
    /时间/u.test(text) &&
    /相邻|对应|同一阶段/u.test(text),
  update_no_causality: (text) =>
    explicitNegation.test(text) && /因果|导致|造成|引发/u.test(text),
  resource_normalized_equivalence: (text) =>
    /等价资源/u.test(text) && /规范化|去重/u.test(text),
  resource_not_online_ip: (text) =>
    explicitNegation.test(text) &&
    /在线/u.test(text) &&
    /IP|地址/iu.test(text),
  resource_not_user_business_count: (text) =>
    explicitNegation.test(text) &&
    /用户|业务/u.test(text) &&
    /数量|规模|换算/u.test(text),
})

const DIRECTION_WORDS =
  /下降|下滑|降低|减少|上升|升高|增加|回升|反弹|回落|低于|高于|持平|恢复|结束|起点|最低点/

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

function containsEventIdentity(
  text: string,
  event: EventIdentity,
): boolean {
  const directValues = [
    event.country_name,
    event.display_name,
    event.incident_id,
    event.legacy_reference,
  ].filter((value) => value.trim().length > 0)
  if (directValues.some((value) => text.includes(value))) return true
  if (/country_outage/iu.test(text)) return true
  const countryCode = event.country_code.trim()
  return (
    countryCode.length > 0 &&
    new RegExp(
      `(^|[^A-Za-z0-9])${escapeRegExp(countryCode)}([^A-Za-z0-9]|$)`,
      'iu',
    ).test(text)
  )
}

function containsOrdinaryNumberOrTime(text: string): boolean {
  const withoutApprovedTechnicalNumbers = text
    .normalize('NFKC')
    .replace(/\bRRC25\b/giu, 'RRC')
    .replace(/\bIPv[46]\b/giu, 'IPv')
    .replace(/\/(?:24|48)(?!\d)/gu, '/prefix')
  if (/[0-9]/u.test(withoutApprovedTechnicalNumbers)) return true
  return /[零〇一二两三四五六七八九十百千万]+\s*(?:年|月|日|号|时|点|分|秒|小时|分钟)/u.test(
    withoutApprovedTechnicalNumbers,
  )
}

function isChineseNarrative(text: string): boolean {
  if (!/\p{Script=Han}/u.test(text)) return false
  const withoutApprovedTechnicalTerms = text.replace(
    /\b(?:BGP|RRC25|Prefix|VP|IPv4|IPv6|UPDATE|ANNOUNCE|WITHDRAW|IP)\b/giu,
    '',
  )
  return !/[A-Za-z]{2,}/u.test(withoutApprovedTechnicalTerms)
}

function assertSafeSlotText(
  text: string,
  planItem: CountryOutageModelLanguagePlanItem,
  event: EventIdentity,
): void {
  const characterLength = [...text].length
  if (
    text.trim() !== text ||
    characterLength < planItem.minLength ||
    characterLength > planItem.maxLength ||
    /[\r\n\u2028\u2029]/u.test(text) ||
    !isChineseNarrative(text) ||
    containsOrdinaryNumberOrTime(text) ||
    /(?:https?:\/\/|www\.|mailto:|data:|javascript:|(?:[A-Za-z0-9-]+\.)+(?:com|net|org|cn)\b)/iu.test(
      text,
    ) ||
    /<\/?[A-Za-z][^>]*>|&(?:lt|gt|amp|quot|#\d+);/iu.test(text) ||
    /```|`|\*\*|__|~~|!\[[^\]]*\]\(|\[[^\]]+\]\([^)]+\)|(^|\s)#{1,6}\s|(^|\s)[>*+-]\s/u.test(
      text,
    ) ||
    /\bAS\s*\d+\b/iu.test(text) ||
    DIRECTION_WORDS.test(text) ||
    containsEventIdentity(text, event)
  ) {
    throw new CountryOutageModelLanguageError(
      'language_slot_text_invalid',
      planItem.id,
    )
  }
}

function assertSlotSemantics(
  text: string,
  planItem: CountryOutageModelLanguagePlanItem,
): void {
  if (
    planItem.requiredSemanticIds.some(
      (semanticId) => !SEMANTIC_CHECKS[semanticId](text),
    )
  ) {
    throw new CountryOutageModelLanguageError(
      'language_slot_semantic_invalid',
      planItem.id,
    )
  }
}

function parseBundleStructure(
  value: unknown,
  plan: readonly CountryOutageModelLanguagePlanItem[],
): Array<{ id: CountryOutageLanguageSlotId; text: string }> {
  if (
    !isObject(value) ||
    !hasExactKeys(value, ['schemaVersion', 'slots']) ||
    value.schemaVersion !==
      COUNTRY_OUTAGE_LANGUAGE_SLOT_CONTRACT_VERSION ||
    !Array.isArray(value.slots)
  ) {
    throw new CountryOutageModelLanguageError(
      'language_bundle_schema_invalid',
    )
  }
  const slots: Array<{
    id: CountryOutageLanguageSlotId
    text: string
  }> = []
  for (const item of value.slots) {
    if (
      !isObject(item) ||
      !hasExactKeys(item, ['id', 'text']) ||
      typeof item.id !== 'string' ||
      !COUNTRY_OUTAGE_LANGUAGE_SLOT_IDS.includes(
        item.id as CountryOutageLanguageSlotId,
      ) ||
      typeof item.text !== 'string'
    ) {
      throw new CountryOutageModelLanguageError(
        'language_bundle_schema_invalid',
      )
    }
    slots.push({
      id: item.id as CountryOutageLanguageSlotId,
      text: item.text,
    })
  }
  if (
    JSON.stringify(slots.map((item) => item.id)) !==
    JSON.stringify(plan.map((item) => item.id))
  ) {
    throw new CountryOutageModelLanguageError(
      'language_bundle_slot_mismatch',
    )
  }
  return slots
}

export function parseCountryOutageLanguageSlotBundle(
  value: unknown,
  plan: readonly CountryOutageModelLanguagePlanItem[],
  event: EventIdentity,
): CountryOutageLanguageSlotBundle {
  assertPlanShape(plan)
  const slots = parseBundleStructure(value, plan)
  const parsed = slots.map((slot, index) => {
    const planItem = plan[index]!
    assertSafeSlotText(slot.text, planItem, event)
    assertSlotSemantics(slot.text, planItem)
    return Object.freeze({ id: slot.id, text: slot.text })
  })
  return Object.freeze({
    schemaVersion: COUNTRY_OUTAGE_LANGUAGE_SLOT_CONTRACT_VERSION,
    slots: Object.freeze(parsed),
  })
}

function normalizedPlan(
  plan: readonly CountryOutageModelLanguagePlanItem[],
): string {
  return JSON.stringify(plan)
}

function invariantProjection(
  draft: CountryOutageReportDraft,
  plan: readonly CountryOutageModelLanguagePlanItem[],
): unknown {
  const slotLocations = new Set(
    plan.map((item) => `${item.sectionId}:${item.paragraphIndex}`),
  )
  return {
    schemaVersion: draft.schemaVersion,
    title: draft.title,
    subtitle: draft.subtitle,
    summary: draft.summary,
    highlights: draft.highlights,
    sections: draft.sections.map((section) => ({
      id: section.id,
      title: section.title,
      paragraphs: section.paragraphs.map((paragraph, index) => ({
        text: slotLocations.has(`${section.id}:${index}`)
          ? '<country_outage_language_slot>'
          : paragraph.text,
        evidenceRefs: paragraph.evidenceRefs,
      })),
    })),
    unknowns: draft.unknowns,
  }
}

function publishableTexts(draft: CountryOutageReportDraft): string[] {
  return [
    draft.title,
    draft.subtitle,
    draft.summary.text,
    ...draft.highlights.flatMap((item) => [item.label, item.value]),
    ...draft.sections.flatMap((section) => [
      section.title,
      ...section.paragraphs.map((paragraph) => paragraph.text),
    ]),
    ...draft.unknowns,
  ]
}

function factNumericTokens(draft: CountryOutageReportDraft): string[] {
  const text = publishableTexts(draft)
    .join('\n')
    .normalize('NFKC')
    .replace(/\bRRC25\b/giu, 'RRC')
    .replace(/\bIPv[46]\b/giu, 'IPv')
    .replace(/\/(?:24|48)(?!\d)/gu, '/prefix')
    .replace(/(^|[^\d])5\s*分钟(?!\d)/gu, '$1分钟')
  return (
    text.match(
      /\d{1,3}(?:,\d{3})+(?:\.\d+)?%?|\d+(?:\.\d+)?%?/gu,
    ) ?? []
  ).sort()
}

export function mergeCountryOutageLanguageSlots(
  baseDraft: CountryOutageReportDraft,
  plan: readonly CountryOutageModelLanguagePlanItem[],
  bundle: CountryOutageLanguageSlotBundle,
): CountryOutageReportDraft {
  assertPlanShape(plan)
  const expectedPlan = buildCountryOutageModelLanguagePlan(baseDraft)
  if (normalizedPlan(plan) !== normalizedPlan(expectedPlan)) {
    throw new CountryOutageModelLanguageError('language_plan_invalid')
  }
  const slots = parseBundleStructure(bundle, plan)
  const merged = structuredClone(baseDraft)
  for (let index = 0; index < plan.length; index += 1) {
    const planItem = plan[index]!
    const matchingSections = merged.sections.filter(
      (section) => section.id === planItem.sectionId,
    )
    const paragraph =
      matchingSections.length === 1
        ? matchingSections[0]!.paragraphs[planItem.paragraphIndex]
        : undefined
    if (!paragraph) {
      throw new CountryOutageModelLanguageError(
        'language_plan_invalid',
        planItem.id,
      )
    }
    paragraph.text = slots[index]!.text
  }
  if (
    JSON.stringify(invariantProjection(baseDraft, plan)) !==
      JSON.stringify(invariantProjection(merged, plan)) ||
    JSON.stringify(factNumericTokens(baseDraft)) !==
      JSON.stringify(factNumericTokens(merged))
  ) {
    throw new CountryOutageModelLanguageError(
      'language_slot_merge_invariant_failed',
    )
  }
  return merged
}
