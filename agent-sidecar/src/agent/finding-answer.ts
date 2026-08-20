import { canonicalJsonSha256 } from '../shared/deterministic-json.js'

import type {
  DomeyeAnswerBoundaryCode,
  DomeyeAnswerContext,
  DomeyeAnswerFactKey,
  DomeyeAnswerStyleAssessment,
  DomeyeActionReceipt,
  DomeyeArtifactEnvelope,
  DomeyeRendererDraft,
  DomeyeResponseGuardDecision,
  DomeyeTypedFinding,
} from './contracts.js'

const FINDING_SCHEMA_VERSION = 'domeye_agent_typed_finding_v1' as const
const ANSWER_CONTEXT_SCHEMA_VERSION = 'domeye_agent_answer_context_v2' as const
const RESPONSE_GUARD_SCHEMA_VERSION =
  'domeye_agent_response_guard_v2' as const

export const COUNTRY_OUTAGE_ANSWER_STYLE_POLICY_ID =
  'domeye.answer-style.compact-first-slice/v1.0' as const

const METRIC = 'fixed_visible_ipv4_address_count' as const
const UNIT = 'unique_ipv4_address' as const
const FIRST_SLICE_QUESTION =
  '在这次冻结 publication 的观测窗口内，RRC25 看到的固定前缀可见 IPv4 地址量最低是多少，首次在什么观测时刻出现？首值、末值、最大值和极差分别是多少？' as const

export const COUNTRY_OUTAGE_MANDATORY_LIMITATIONS = Object.freeze([
  Object.freeze({
    code: 'fixed_population_semantics',
    text: 'unique_ipv4_address 是固定前缀规范化、去重并合并重叠后的 IPv4 唯一地址并集，不是用户数、设备数或流量。',
  }),
  Object.freeze({
    code: 'rrc25_observer_scope_only',
    text: 'RRC25 是单一观察点，不能代表全国或全球互联网。',
  }),
  Object.freeze({
    code: 'no_cause_responsibility_or_recovery',
    text: '极值、下降或末值回升不能单独证明事件原因、责任、全国中断或真实恢复。',
  }),
  Object.freeze({
    code: 'window_not_event_closure',
    text: '窗口冻结仅表示评测输入不静默变化，不表示真实事件已经结束。',
  }),
])

export const COUNTRY_OUTAGE_FORBIDDEN_CONCLUSIONS = Object.freeze([
  'national_outage',
  'real_user_impact',
  'cause',
  'responsibility',
  'real_recovery',
] as const)

export const COUNTRY_OUTAGE_REQUIRED_ANSWER_BOUNDARIES = Object.freeze([
  Object.freeze({
    code: 'fixed_prefix_population_not_users' as const,
    meaning_zh: '地址量是固定前缀 IPv4 唯一地址并集，不是用户数。',
  }),
  Object.freeze({
    code: 'rrc25_control_plane_observation_only' as const,
    meaning_zh: '结果只表示 RRC25 的 BGP 控制面观测。',
  }),
  Object.freeze({
    code: 'no_national_or_user_impact_cause_responsibility_recovery' as const,
    meaning_zh: '不能据此判断全国状态、用户影响、原因、责任或恢复。',
  }),
])

const REQUIRED_FACT_KEYS = Object.freeze([
  'minimum',
  'minimum_at_utc',
  'first',
  'last',
  'maximum',
  'difference',
] as const satisfies readonly DomeyeAnswerFactKey[])

const STYLE_POLICY_BODY = Object.freeze({
  policy_id: COUNTRY_OUTAGE_ANSWER_STYLE_POLICY_ID,
  normalization_algorithm_id:
    'unicode-nfc-collapse-whitespace-intl-segmenter-zh-v1',
  required_fact_keys: REQUIRED_FACT_KEYS,
  required_boundary_codes: COUNTRY_OUTAGE_REQUIRED_ANSWER_BOUNDARIES.map(
    (item) => item.code,
  ),
  lead_fact_keys: ['minimum', 'minimum_at_utc'],
  fact_value_policy: 'context_display_zh_exact_once_per_fact_key',
  expression_grammar_id: 'fact-label-display-pairing-zh-v1',
  boundary_grammar_id: 'required-boundary-clauses-zh-v1',
  unit_policy: 'context_unit_zh_exactly_once',
  forbidden_conclusions: COUNTRY_OUTAGE_FORBIDDEN_CONCLUSIONS,
  leak_policy: [
    'internal_audit_object',
    'digest_or_commit',
    'path_or_code_location',
    'endpoint_or_port',
    'credential',
    'runtime_accounting_or_identifier',
    'audit_heading',
  ],
  outside_context_policy: [
    'unknown_numeric_or_time_literal',
    'unapproved_approximation_or_conversion',
    'unknown_or_internal_unit',
    'unsupported_external_assertion',
    'unapproved_uncertainty_or_negation',
    'next_step_not_allowed',
  ],
  max_lead_graphemes: 90,
  max_fact_blocks: 3,
  required_boundary_blocks: 1,
  max_total_graphemes: 360,
  max_sentences: 6,
  next_step: 'not_applicable_for_fixed_first_slice',
})

export type DomeyeFindingAnswerErrorCode =
  | 'artifact_not_qualified'
  | 'receipt_not_qualified'
  | 'artifact_receipt_conflict'
  | 'identity_conflict'
  | 'invalid_extrema_payload'

export class DomeyeFindingAnswerError extends Error {
  constructor(
    readonly code: DomeyeFindingAnswerErrorCode,
    message: string,
  ) {
    super(message)
    this.name = 'DomeyeFindingAnswerError'
  }
}

export interface DomeyeAnswerRenderer {
  render(
    context: DomeyeAnswerContext,
  ): Promise<DomeyeRendererDraft>
}

type DomeyeCompletedRenderAttempt = Readonly<{
  status: 'completed'
  draft: DomeyeRendererDraft
  failure_code: null
}>

type DomeyeFailedRenderAttempt = Readonly<{
  status: 'failed'
  draft: null
  failure_code: 'renderer_failed_or_invalid'
}>

export type DomeyeAcceptedAnswer = Readonly<{
  answer: string
  answer_digest: string
  source: 'renderer'
  guard_result: Extract<
    DomeyeResponseGuardDecision,
    { readonly decision: 'pass' }
  >
  render_attempt: DomeyeCompletedRenderAttempt
}>

export type DomeyeFallbackAnswer = Readonly<{
  answer: string
  answer_digest: string
  source: 'deterministic_fallback'
  guard_result: Extract<
    DomeyeResponseGuardDecision,
    { readonly decision: 'block' }
  >
  render_attempt: DomeyeCompletedRenderAttempt | DomeyeFailedRenderAttempt
}>

export type DomeyeComposedAnswer =
  | DomeyeAcceptedAnswer
  | DomeyeFallbackAnswer

function digest(value: unknown): string {
  return `sha256:${canonicalJsonSha256(value)}`
}

function deepFreeze<T>(value: T): Readonly<T> {
  if (value !== null && typeof value === 'object' && !Object.isFrozen(value)) {
    for (const child of Object.values(value as Record<string, unknown>)) {
      deepFreeze(child)
    }
    Object.freeze(value)
  }
  return value
}

function sameValue(left: unknown, right: unknown): boolean {
  return canonicalJsonSha256(left) === canonicalJsonSha256(right)
}

function uniqueSorted(values: readonly string[]): string[] {
  return [...new Set(values)].sort()
}

function isNonEmptyString(value: unknown): value is string {
  return typeof value === 'string' && value.trim().length > 0
}

function isSafeMetricValue(value: unknown): value is number {
  return Number.isSafeInteger(value) && Number(value) >= 0
}

function isUtcTimestamp(value: unknown): value is string {
  return isNonEmptyString(value) && Number.isFinite(Date.parse(value))
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

function redactKnownText(text: string, values: readonly string[]): string {
  return [...values]
    .filter((value) => value.length > 0)
    .sort((left, right) => right.length - left.length)
    .reduce((current, value) => current.replace(
      new RegExp(escapeRegExp(value), 'giu'),
      ' ',
    ), text)
}

function includesSameMembers(
  actual: readonly string[],
  expected: readonly string[],
): boolean {
  return sameValue(uniqueSorted(actual), uniqueSorted(expected))
}

type MetricSeriesArtifact = Extract<
  DomeyeArtifactEnvelope,
  { readonly artifact_kind: 'metric_series' }
>

type SeriesExtremaArtifact = Extract<
  DomeyeArtifactEnvelope,
  { readonly artifact_kind: 'series_extrema' }
>

function isMetricSeriesArtifact(
  artifact: DomeyeArtifactEnvelope,
): artifact is MetricSeriesArtifact {
  return artifact.artifact_kind === 'metric_series'
}

function isSeriesExtremaArtifact(
  artifact: DomeyeArtifactEnvelope,
): artifact is SeriesExtremaArtifact {
  return artifact.artifact_kind === 'series_extrema'
}

function assertQualifiedPair(
  artifact: DomeyeArtifactEnvelope,
  receipt: DomeyeActionReceipt,
  capabilityId: 'CAP-006' | 'CAP-016',
  executionUnitId: 'TOOL-03' | 'OP-01',
  executionUnitName: 'read_metric_series' | 'series_extrema',
): void {
  if (
    artifact.schema_version !== 'domeye_agent_artifact_envelope_v1'
    || !artifact.immutable
    || artifact.content_digest !== digest(artifact.payload)
  ) {
    throw new DomeyeFindingAnswerError(
      'artifact_not_qualified',
      'Artifact 不是内容摘要一致的冻结制品',
    )
  }
  if (
    receipt.schema_version !== 'domeye_agent_action_receipt_v1'
    || receipt.status !== 'succeeded'
    || receipt.failure_code !== null
    || receipt.capability_id !== capabilityId
    || receipt.execution_binding.execution_unit_id !== executionUnitId
    || receipt.execution_binding.execution_unit_name !== executionUnitName
  ) {
    throw new DomeyeFindingAnswerError(
      'receipt_not_qualified',
      'Action Receipt 不是预期执行单元的成功回执',
    )
  }
  if (
    receipt.action_id !== artifact.producer_action_id
    || receipt.candidate_id !== artifact.candidate_id
    || receipt.tenant_id !== artifact.tenant_id
    || !sameValue(receipt.data_identity, artifact.data_identity)
    || !sameValue(receipt.execution_binding, artifact.execution_binding)
    || !includesSameMembers(receipt.artifact_ids, [artifact.artifact_id])
  ) {
    throw new DomeyeFindingAnswerError(
      'artifact_receipt_conflict',
      'Artifact 与 Action Receipt 的动作、身份或执行绑定不一致',
    )
  }
}

function assertKnownExtrema(
  payload: SeriesExtremaArtifact['payload'],
): void {
  if (payload.result_state !== 'known') return
  const values = [
    payload.first,
    payload.last,
    payload.minimum,
    payload.maximum,
    payload.difference,
  ]
  const times = [
    payload.first_at_utc,
    payload.last_at_utc,
    payload.minimum_at_utc,
    payload.maximum_at_utc,
  ]
  if (
    !values.every(isSafeMetricValue)
    || !Number.isSafeInteger(payload.net_change)
    || !times.every(isUtcTimestamp)
    || payload.observed_point_count < 1
    || payload.observed_point_count + payload.null_point_count
      !== payload.time_slot_count
    || payload.minimum > payload.maximum
    || payload.first < payload.minimum
    || payload.first > payload.maximum
    || payload.last < payload.minimum
    || payload.last > payload.maximum
    || payload.difference !== payload.maximum - payload.minimum
    || payload.net_change !== payload.last - payload.first
  ) {
    throw new DomeyeFindingAnswerError(
      'invalid_extrema_payload',
      'OP-01 的已知 extrema 结果不满足计数、极值或差值不变量',
    )
  }
}

function assertEmptyExtrema(
  payload: SeriesExtremaArtifact['payload'],
): void {
  if (payload.result_state !== 'empty_observed_set') return
  const values = [
    payload.first,
    payload.first_at_utc,
    payload.last,
    payload.last_at_utc,
    payload.minimum,
    payload.minimum_at_utc,
    payload.maximum,
    payload.maximum_at_utc,
    payload.difference,
    payload.net_change,
  ]
  if (
    payload.observed_point_count !== 0
    || payload.null_point_count !== payload.time_slot_count
    || !values.every((value) => value === null)
  ) {
    throw new DomeyeFindingAnswerError(
      'invalid_extrema_payload',
      'empty_observed_set 必须保持全空，不能补成 0',
    )
  }
}

export function buildCountryOutageSeriesExtremaFinding(input: {
  readonly series_artifact: DomeyeArtifactEnvelope
  readonly series_receipt: DomeyeActionReceipt
  readonly extrema_artifact: DomeyeArtifactEnvelope
  readonly extrema_receipt: DomeyeActionReceipt
}): DomeyeTypedFinding {
  if (!isMetricSeriesArtifact(input.series_artifact)) {
    throw new DomeyeFindingAnswerError(
      'artifact_not_qualified',
      'CAP-006 必须提供 metric_series Artifact',
    )
  }
  if (!isSeriesExtremaArtifact(input.extrema_artifact)) {
    throw new DomeyeFindingAnswerError(
      'artifact_not_qualified',
      'CAP-016 必须提供 series_extrema Artifact',
    )
  }
  const seriesArtifact = input.series_artifact
  const extremaArtifact = input.extrema_artifact
  assertQualifiedPair(
    seriesArtifact,
    input.series_receipt,
    'CAP-006',
    'TOOL-03',
    'read_metric_series',
  )
  assertQualifiedPair(
    extremaArtifact,
    input.extrema_receipt,
    'CAP-016',
    'OP-01',
    'series_extrema',
  )
  if (
    seriesArtifact.candidate_id !== extremaArtifact.candidate_id
    || seriesArtifact.tenant_id !== extremaArtifact.tenant_id
    || !sameValue(seriesArtifact.data_identity, extremaArtifact.data_identity)
    || extremaArtifact.payload.source_artifact_id !== seriesArtifact.artifact_id
  ) {
    throw new DomeyeFindingAnswerError(
      'identity_conflict',
      'TOOL-03 与 OP-01 未绑定同一 Candidate、数据身份或源 Artifact',
    )
  }
  if (
    seriesArtifact.payload.metric !== METRIC
    || extremaArtifact.payload.metric !== METRIC
    || seriesArtifact.payload.unit !== UNIT
    || extremaArtifact.payload.unit !== UNIT
    || extremaArtifact.payload.time_slot_count
      !== seriesArtifact.payload.time_slot_count
    || extremaArtifact.payload.observed_point_count
      !== seriesArtifact.payload.observed_point_count
    || extremaArtifact.payload.null_point_count
      !== seriesArtifact.payload.null_point_count
  ) {
    throw new DomeyeFindingAnswerError(
      'invalid_extrema_payload',
      'OP-01 结果与 TOOL-03 的指标、单位或点数不一致',
    )
  }
  if (
    seriesArtifact.payload.completeness.state !== 'complete'
    || seriesArtifact.payload.completeness.missing_slot_count !== 0
  ) {
    throw new DomeyeFindingAnswerError(
      'artifact_not_qualified',
      '不完整 TOOL-03 Artifact 不能产生可回答 Finding',
    )
  }
  assertKnownExtrema(extremaArtifact.payload)
  assertEmptyExtrema(extremaArtifact.payload)

  const extrema = extremaArtifact.payload
  const findingContent = {
    schema_version: FINDING_SCHEMA_VERSION,
    finding_type: 'fixed_visible_ipv4_series_extrema' as const,
    value_state: extrema.result_state === 'known'
      ? 'known' as const
      : 'empty' as const,
    candidate_id: extremaArtifact.candidate_id,
    tenant_id: extremaArtifact.tenant_id,
    data_identity: extremaArtifact.data_identity,
    metric: METRIC,
    unit: UNIT,
    population_definition: seriesArtifact.payload.population_definition,
    values: {
      first: extrema.first,
      first_at_utc: extrema.first_at_utc,
      last: extrema.last,
      last_at_utc: extrema.last_at_utc,
      minimum: extrema.minimum,
      minimum_at_utc: extrema.minimum_at_utc,
      maximum: extrema.maximum,
      maximum_at_utc: extrema.maximum_at_utc,
      difference: extrema.difference,
      net_change: extrema.net_change,
    },
    time_slot_count: extrema.time_slot_count,
    observed_point_count: extrema.observed_point_count,
    null_point_count: extrema.null_point_count,
    completeness_state: seriesArtifact.payload.completeness.state,
    limitation_codes: COUNTRY_OUTAGE_MANDATORY_LIMITATIONS.map(
      (limitation) => limitation.code,
    ),
    tool_version: input.series_receipt.execution_binding.execution_unit_version,
    operator_version:
      input.extrema_receipt.execution_binding.execution_unit_version,
    artifact_refs: [seriesArtifact.artifact_id, extremaArtifact.artifact_id],
    receipt_refs: [
      input.series_receipt.receipt_id,
      input.extrema_receipt.receipt_id,
    ],
    evidence_refs: uniqueSorted([
      ...seriesArtifact.payload.evidence_refs,
      ...extremaArtifact.payload.evidence_refs,
    ]),
  }
  const resultDigest = digest(findingContent)
  return deepFreeze({
    ...findingContent,
    finding_id: `finding-${resultDigest}`,
    result_digest: resultDigest,
  }) as DomeyeTypedFinding
}

export function buildCountryOutageAnswerContext(
  finding: DomeyeTypedFinding,
): DomeyeAnswerContext {
  const values = finding.values
  if (
    finding.value_state !== 'known'
    || values.minimum === null
    || values.minimum_at_utc === null
    || values.first === null
    || values.last === null
    || values.maximum === null
    || values.difference === null
  ) {
    throw new DomeyeFindingAnswerError(
      'invalid_extrema_payload',
      '只有数值完整的 known Finding 才能形成回答上下文',
    )
  }
  return deepFreeze({
    schema_version: ANSWER_CONTEXT_SCHEMA_VERSION,
    question_zh: FIRST_SLICE_QUESTION,
    metric_zh: '固定前缀可见 IPv4 地址量' as const,
    unit_zh: '个唯一 IPv4 地址' as const,
    facts: {
      minimum: {
        value: values.minimum,
        display_zh: formatIntegerZh(values.minimum),
      },
      minimum_at_utc: {
        value: values.minimum_at_utc,
        display_zh: formatUtcZh(values.minimum_at_utc),
      },
      first: {
        value: values.first,
        display_zh: formatIntegerZh(values.first),
      },
      last: {
        value: values.last,
        display_zh: formatIntegerZh(values.last),
      },
      maximum: {
        value: values.maximum,
        display_zh: formatIntegerZh(values.maximum),
      },
      difference: {
        value: values.difference,
        display_zh: formatIntegerZh(values.difference),
      },
    },
    required_boundaries: COUNTRY_OUTAGE_REQUIRED_ANSWER_BOUNDARIES.map(
      (item) => ({ ...item }),
    ),
    forbidden_conclusions: [...COUNTRY_OUTAGE_FORBIDDEN_CONCLUSIONS],
    style_constraints: {
      max_lead_graphemes: 90 as const,
      max_fact_blocks: 3 as const,
      required_boundary_blocks: 1 as const,
      max_total_graphemes: 360 as const,
      max_sentences: 6 as const,
    },
  }) as DomeyeAnswerContext
}

function formatIntegerZh(value: number): string {
  return String(value).replace(/\B(?=(\d{3})+(?!\d))/g, ',')
}

function formatUtcZh(value: string): string {
  const instant = new Date(value)
  return `${instant.getUTCFullYear()} 年 ${instant.getUTCMonth() + 1} 月 ${instant.getUTCDate()} 日 ${String(instant.getUTCHours()).padStart(2, '0')}:${String(instant.getUTCMinutes()).padStart(2, '0')} UTC`
}

function countOccurrences(text: string, fragment: string): number {
  if (fragment.length === 0) return 0
  return text.split(fragment).length - 1
}

function normalizedForMeasurement(text: string): string {
  return text.normalize('NFC').replace(/\s+/gu, ' ').trim()
}

const GRAPHEME_SEGMENTER = new Intl.Segmenter('zh-CN', {
  granularity: 'grapheme',
})

function graphemeCount(text: string): number {
  return [...GRAPHEME_SEGMENTER.segment(
    normalizedForMeasurement(text),
  )].length
}

function sentenceCount(text: string): number {
  const normalized = normalizedForMeasurement(text)
  if (!normalized) return 0
  const endings = normalized.match(/[。！？!?]+/gu)?.length ?? 0
  return endings + (/[。！？!?]$/u.test(normalized) ? 0 : 1)
}

function answerBlocks(draft: DomeyeRendererDraft): string[] {
  return [
    draft.lead.text,
    ...draft.fact_blocks.map((block) => block.text),
    draft.boundary.text,
    ...(draft.next_step === null ? [] : [draft.next_step]),
  ]
}

export function composeCountryOutageRendererDraftText(
  draft: DomeyeRendererDraft,
): string {
  return answerBlocks(draft).join('\n')
}

function factDisplay(
  context: DomeyeAnswerContext,
  key: DomeyeAnswerFactKey,
): string {
  return context.facts[key].display_zh
}

function hasSensitiveEndpoint(text: string): boolean {
  return /(?:https?|wss?):\/\/[^\s]*(?:localhost|127\.0\.0\.1|0\.0\.0\.0|10\.\d{1,3}\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}|\.internal\b|\.local\b)|(?:^|\s)\/api\/|\b(?:localhost|127\.0\.0\.1):\d+\b/iu.test(
    text,
  )
}

function hasSensitiveCredential(text: string): boolean {
  return /\bBearer\s+[A-Za-z0-9._~+/=-]+|\b(?:api[_-]?key|access[_-]?token|password|passwd|secret)\s*[:=]\s*\S+|\bsk-[A-Za-z0-9_-]{12,}/iu.test(
    text,
  )
}

const FACT_LABELS: Readonly<Record<DomeyeAnswerFactKey, RegExp>> = {
  minimum: /(?:最低(?:值|点)?|最小值)/u,
  minimum_at_utc:
    /(?:首次(?:被)?观测(?:的)?(?:时刻|时间)?|首次(?:出现|出现在))/u,
  first: /首值/u,
  last: /末值/u,
  maximum: /最大(?:值|点)?/u,
  difference: /极差/u,
}

const FACT_CLAUSE_LABEL_GRAMMAR: Readonly<
  Record<DomeyeAnswerFactKey, string>
> = Object.freeze({
  minimum: '(?:最低(?:值|点)?|最小值)',
  minimum_at_utc:
    '(?:首次(?:被)?观测(?:的)?(?:时刻|时间)?|首次(?:出现|出现在))',
  first: '首值',
  last: '末值',
  maximum: '最大(?:值|点)?',
  difference: '极差',
})

function factClauseContractPattern(
  context: DomeyeAnswerContext,
  key: DomeyeAnswerFactKey,
): string {
  const label = FACT_CLAUSE_LABEL_GRAMMAR[key]
  const display = escapeRegExp(factDisplay(context, key))
  if (key === 'minimum_at_utc') {
    return `${label}\\s*(?:为|是|于|在|：|:)?\\s*${display}`
  }
  return `${label}\\s*(?:为|是|：|:)?\\s*${display}(?:\\s*${escapeRegExp(context.unit_zh)})?`
}

function expressionBlockMatchesContractGrammar(
  context: DomeyeAnswerContext,
  block: Readonly<{
    fact_keys: readonly DomeyeAnswerFactKey[]
    text: string
  }>,
): boolean {
  if (block.fact_keys.length === 0) return false
  const clauses = block.fact_keys.map(
    (key) => factClauseContractPattern(context, key),
  )
  const connector = '\\s*(?:，|,|；|;|。|、|和|与|以及)\\s*'
  return new RegExp(`^${clauses.join(connector)}。$`, 'u').test(
    block.text.normalize('NFC'),
  )
}

const BOUNDARY_CLAUSE_GRAMMAR: Readonly<
  Record<DomeyeAnswerBoundaryCode, string>
> = Object.freeze({
  fixed_prefix_population_not_users:
    '(?:地址量|该地址量|这些地址量)\\s*(?:是|仅是|表示的是)\\s*固定前缀(?:的)?\\s*IPv4\\s*唯一地址并集\\s*(?:，|,)\\s*(?:而)?(?:不是|不等于|并非)\\s*(?:真实)?用户数',
  rrc25_control_plane_observation_only:
    '(?:结果|这些结果|以上结果|上述结果)\\s*(?:只|仅)(?:表示|反映|是)\\s*RRC25\\s*(?:的)?\\s*(?:BGP\\s*)?控制面观测',
  no_national_or_user_impact_cause_responsibility_recovery:
    '(?:也)?(?:不能|无法|不足以)\\s*(?:据此)?\\s*(?:判断|说明|证明)\\s*全国(?:互联网)?状态\\s*(?:、|，|,)\\s*(?:真实)?用户影响\\s*(?:、|，|,)\\s*原因\\s*(?:、|，|,)\\s*责任\\s*(?:或|与|、)\\s*(?:真实)?恢复',
})

function boundaryMatchesContractGrammar(
  boundary: DomeyeRendererDraft['boundary'],
): boolean {
  if (boundary.boundary_codes.length === 0) return false
  const clauses = boundary.boundary_codes.map(
    (code) => BOUNDARY_CLAUSE_GRAMMAR[code],
  )
  const connector = '\\s*(?:；|;|，|,)\\s*'
  return new RegExp(`^${clauses.join(connector)}。$`, 'u').test(
    boundary.text.normalize('NFC'),
  )
}

function boundaryMeaningPresent(
  code: DomeyeAnswerBoundaryCode,
  text: string,
): boolean {
  if (code === 'fixed_prefix_population_not_users') {
    return /(?:地址量|IPv4).{0,28}(?:不是|不等于|并非).{0,18}(?:用户数|用户)/u
      .test(text)
  }
  if (code === 'rrc25_control_plane_observation_only') {
    return /RRC25/u.test(text)
      && /(?:BGP\s*)?控制面(?:的)?观测/u.test(text)
  }
  return /(?:不能|无法|不足以|不代表|不得)/u.test(text)
    && /全国/u.test(text)
    && /用户影响|用户受影响/u.test(text)
    && /原因/u.test(text)
    && /责任/u.test(text)
    && /恢复/u.test(text)
}

function internalLeakCodes(text: string): string[] {
  const codes: string[] = []
  if (/\b(?:Candidate|Receipt|Finding|Artifact|Trace|Evidence)\b|(?:^|\n)\s*证据\s*[：:]/iu.test(text)) {
    codes.push('internal_audit_object')
  }
  if (/\bsha256(?::|\b)|\b[a-f0-9]{40}\b|\b[a-f0-9]{64}\b/iu.test(text)) {
    codes.push('digest_or_commit')
  }
  if (/(?:^|[\s（(])(?:\/Users\/|\/home\/|agent-sidecar\/|docs\/|contracts\/)|\b[\w./-]+\.(?:ts|js|py|json|md)(?::\d+)?\b/iu.test(text)) {
    codes.push('path_or_code_location')
  }
  if (hasSensitiveEndpoint(text) || /\b(?:端口|port)\s*[:：]?\s*\d+/iu.test(text)) {
    codes.push('endpoint_or_port')
  }
  if (hasSensitiveCredential(text)) codes.push('credential')
  if (/\b(?:model_api_attempts|provider_usage|context_id|finding_id|candidate_id|receipt_refs|artifact_refs)\b|模型调用次数|调用账本|费用账本/iu.test(text)) {
    codes.push('runtime_accounting_or_identifier')
  }
  if (/(?:^|\n)\s*(?:结论|证据|Evidence|执行证明|测试结果)\s*[：:]/iu.test(text)) {
    codes.push('audit_heading')
  }
  return uniqueSorted(codes)
}

function forbiddenClaimCodes(text: string): string[] {
  const codes: string[] = []
  if (/全国(?:互联网|网络|范围)?(?:已经|已|发生|出现|遭遇|处于|全面)?(?:中断|断网|瘫痪)|全网(?:中断|断网)|nationwide\s+(?:internet\s+)?outage|global\s+outage/iu.test(text)) {
    codes.push('forbidden_national_outage_claim')
  }
  if (/(?:用户|设备|人口).{0,16}(?:受影响|断网|中断|无法上网)|(?:影响|波及).{0,12}(?:用户|人)|users?\s+(?:were\s+)?affected/iu.test(text)) {
    codes.push('forbidden_user_impact_claim')
  }
  if (/(?:原因(?:是|为)|由于.{0,40}(?:导致|造成)|由.{0,40}(?:导致|造成)|归因于|caused\s+by)/iu.test(text)) {
    codes.push('forbidden_cause_claim')
  }
  if (/(?:责任(?:在于|属于|由)|应当?负责|承担责任|responsible\s+for)/iu.test(text)) {
    codes.push('forbidden_responsibility_claim')
  }
  if (/(?:已经|已|完全|实际|全面)(?:恢复|恢复正常)|恢复(?:完成|了)|fully\s+recovered/iu.test(text)) {
    codes.push('forbidden_recovery_claim')
  }
  if (/(?:实际发生于|事件(?:的)?发生时间(?:是|为)|occurred\s+at)/iu.test(text)) {
    codes.push('observed_time_overstated')
  }
  return codes
}

function outsideContextCodes(
  context: DomeyeAnswerContext,
  draft: DomeyeRendererDraft,
  finalText: string,
): string[] {
  const codes: string[] = []
  const knownDisplays = REQUIRED_FACT_KEYS.map(
    (key) => factDisplay(context, key),
  )
  const withoutKnownNumbers = redactKnownText(finalText, [
    ...knownDisplays,
    context.unit_zh,
    context.metric_zh,
    'RRC25',
    'IPv4',
  ])
  if (/\d/u.test(withoutKnownNumbers)) {
    codes.push('unknown_numeric_or_time_literal')
  }
  const withoutAuthorizedChineseNumeralWords = redactKnownText(finalText, [
    '唯一',
  ])
  if (/[零〇一二两三四五六七八九十百千万亿兆]+/u.test(
    withoutAuthorizedChineseNumeralWords,
  ) || /(?:约|大约|近|超过|不足|至少|至多)\s*\d|\d+(?:倍|成)|百分之|数(?:十|百|千|万|百万|千万|亿)/u.test(
    finalText,
  )) codes.push('unapproved_approximation_or_conversion')

  const nonBoundaryText = [
    draft.lead.text,
    ...draft.fact_blocks.map((block) => block.text),
  ].join('\n')
  if (/\b(?:unique_ipv4_address|fixed_visible_ipv4_address_count)\b|\b(?:Mbps|Gbps|bytes?|users?|devices?)\b/iu.test(finalText)) {
    codes.push('unknown_or_internal_unit')
  }
  if (/(?:数据中心|机房|运营商|海缆|电力|攻击).{0,18}(?:发生|故障|中断|火灾|导致|造成)|(?:发生|遭遇).{0,12}(?:火灾|攻击)/u.test(finalText)) {
    codes.push('unsupported_external_assertion')
  }
  if (/(?:也许|可能|大概|估计|疑似)|(?:最低|首值|末值|最大|极差|首次观测).{0,18}(?:不是|并非|不等于)/u.test(
    nonBoundaryText,
  )) codes.push('unapproved_uncertainty_or_negation')
  if (draft.next_step !== null) codes.push('next_step_not_allowed')
  return uniqueSorted(codes)
}

export function assessCountryOutageAnswerStyle(
  context: DomeyeAnswerContext,
  draft: DomeyeRendererDraft,
): DomeyeAnswerStyleAssessment {
  const reasons: string[] = []
  const finalText = composeCountryOutageRendererDraftText(draft)
  const expressionBlocks = [draft.lead, ...draft.fact_blocks]
  const assignedFactKeys = expressionBlocks.flatMap(
    (block) => block.fact_keys,
  )
  const factCounts = new Map<DomeyeAnswerFactKey, number>()
  for (const key of assignedFactKeys) {
    factCounts.set(key, (factCounts.get(key) ?? 0) + 1)
  }
  const realizedFactKeys = REQUIRED_FACT_KEYS.filter(
    (key) => (factCounts.get(key) ?? 0) > 0,
  )
  const missingFactKeys = REQUIRED_FACT_KEYS.filter(
    (key) => (factCounts.get(key) ?? 0) === 0,
  )
  const duplicateFactKeys = REQUIRED_FACT_KEYS.filter(
    (key) => (factCounts.get(key) ?? 0) > 1,
  )
  if (missingFactKeys.length > 0) reasons.push('required_fact_missing')
  if (duplicateFactKeys.length > 0) reasons.push('duplicate_fact')
  if (!sameValue(
    draft.lead.fact_keys,
    ['minimum', 'minimum_at_utc'],
  )) reasons.push('lead_not_direct')

  for (const block of expressionBlocks) {
    const displayCounts = new Map<string, number>()
    for (const key of block.fact_keys) {
      const display = factDisplay(context, key)
      displayCounts.set(display, (displayCounts.get(display) ?? 0) + 1)
      if (!FACT_LABELS[key].test(block.text)) {
        reasons.push('fact_label_missing')
      }
    }
    for (const [display, expectedCount] of displayCounts) {
      if (countOccurrences(block.text, display) < expectedCount) {
        reasons.push('visible_fact_missing')
      }
    }
    if (!expressionBlockMatchesContractGrammar(context, block)) {
      reasons.push('expression_outside_contract_grammar')
    }
  }

  const expectedDisplayCounts = new Map<string, number>()
  for (const key of REQUIRED_FACT_KEYS) {
    const display = factDisplay(context, key)
    expectedDisplayCounts.set(
      display,
      (expectedDisplayCounts.get(display) ?? 0) + 1,
    )
  }
  for (const [display, expectedCount] of expectedDisplayCounts) {
    const actualCount = countOccurrences(finalText, display)
    if (actualCount < expectedCount) reasons.push('visible_fact_missing')
    if (actualCount > expectedCount) reasons.push('duplicate_fact_text')
  }
  if (countOccurrences(finalText, context.unit_zh) !== 1) {
    reasons.push('unit_missing_or_duplicate')
  }

  const assignedBoundaryCodes = draft.boundary.boundary_codes
  const boundaryCounts = new Map<DomeyeAnswerBoundaryCode, number>()
  for (const code of assignedBoundaryCodes) {
    boundaryCounts.set(code, (boundaryCounts.get(code) ?? 0) + 1)
  }
  const requiredBoundaryCodes = context.required_boundaries.map(
    (item) => item.code,
  )
  const realizedBoundaryCodes = requiredBoundaryCodes.filter(
    (code) => (boundaryCounts.get(code) ?? 0) > 0,
  )
  const missingBoundaryCodes = requiredBoundaryCodes.filter(
    (code) => (boundaryCounts.get(code) ?? 0) === 0,
  )
  const duplicateBoundaryCodes = requiredBoundaryCodes.filter(
    (code) => (boundaryCounts.get(code) ?? 0) > 1,
  )
  if (missingBoundaryCodes.length > 0) {
    reasons.push('required_boundary_missing')
  }
  if (duplicateBoundaryCodes.length > 0) {
    reasons.push('duplicate_boundary')
  }
  if (!requiredBoundaryCodes.every(
    (code) => boundaryMeaningPresent(code, draft.boundary.text),
  )) reasons.push('required_boundary_meaning_missing')
  if (!boundaryMatchesContractGrammar(draft.boundary)) {
    reasons.push('boundary_outside_contract_grammar')
  }
  if (sentenceCount(draft.boundary.text) !== 1) {
    reasons.push('boundary_must_be_single_sentence')
  }
  if (/(?:但|然而|可是|其实|实际上)/u.test(draft.boundary.text)) {
    reasons.push('boundary_contains_contrast_claim')
  }
  const nonBoundaryText = [
    draft.lead.text,
    ...draft.fact_blocks.map((block) => block.text),
  ].join('\n')
  if (/(?:不是|不等于|并非).{0,18}用户|控制面(?:的)?观测|全国状态|用户影响|原因|责任|恢复/u.test(
    nonBoundaryText,
  )) reasons.push('duplicate_boundary_text')

  const blocks = answerBlocks(draft)
  if (blocks.some((text) =>
    text.trim() !== text || /[\r\n\t\u0000-\u001f\u007f]/u.test(text)
  )) reasons.push('expression_block_invalid')
  const counts = {
    lead_graphemes: graphemeCount(draft.lead.text),
    total_graphemes: graphemeCount(finalText),
    sentence_count: blocks.reduce(
      (total, text) => total + sentenceCount(text),
      0,
    ),
    fact_block_count: draft.fact_blocks.length,
    boundary_block_count: 1,
  }
  if (counts.lead_graphemes > context.style_constraints.max_lead_graphemes) {
    reasons.push('lead_too_long')
  }
  if (counts.fact_block_count > context.style_constraints.max_fact_blocks) {
    reasons.push('too_many_fact_blocks')
  }
  if (
    counts.boundary_block_count
    !== context.style_constraints.required_boundary_blocks
  ) reasons.push('boundary_block_count_mismatch')
  if (counts.total_graphemes > context.style_constraints.max_total_graphemes) {
    reasons.push('answer_too_long')
  }
  if (counts.sentence_count > context.style_constraints.max_sentences) {
    reasons.push('too_many_sentences')
  }

  const leakCodes = internalLeakCodes(finalText)
  if (leakCodes.length > 0) reasons.push('internal_information_leak')
  const outsideCodes = outsideContextCodes(context, draft, finalText)
  if (outsideCodes.length > 0) reasons.push('content_outside_answer_context')
  reasons.push(...forbiddenClaimCodes(nonBoundaryText))
  const boundaryForbiddenCodes = forbiddenClaimCodes(draft.boundary.text)
    .filter((code) =>
      code !== 'forbidden_national_outage_claim'
      || /全国(?:互联网|网络|范围)?.{0,12}(?:已经|已|发生|出现|遭遇|处于|全面).{0,8}(?:中断|断网|瘫痪)/u
        .test(draft.boundary.text)
    )
  reasons.push(...boundaryForbiddenCodes)

  const reasonCodes = uniqueSorted(reasons)
  return deepFreeze({
    schema_version: 'domeye_agent_answer_style_assessment_v1',
    policy_id: COUNTRY_OUTAGE_ANSWER_STYLE_POLICY_ID,
    policy_digest: digest(STYLE_POLICY_BODY),
    normalization_algorithm_id:
      'unicode-nfc-collapse-whitespace-intl-segmenter-zh-v1',
    final_text_digest: digest(finalText),
    counts,
    realized_fact_keys: [...realizedFactKeys],
    missing_fact_keys: [...missingFactKeys],
    duplicate_fact_keys: [...duplicateFactKeys],
    realized_boundary_codes: [...realizedBoundaryCodes],
    missing_boundary_codes: [...missingBoundaryCodes],
    duplicate_boundary_codes: [...duplicateBoundaryCodes],
    leak_codes: leakCodes,
    outside_context_codes: outsideCodes,
    passed: reasonCodes.length === 0,
    reason_codes: reasonCodes,
  }) as DomeyeAnswerStyleAssessment
}

export function guardCountryOutageResponse(
  context: DomeyeAnswerContext,
  draft: DomeyeRendererDraft,
): DomeyeResponseGuardDecision {
  const styleAssessment = assessCountryOutageAnswerStyle(context, draft)
  const guardedText = composeCountryOutageRendererDraftText(draft)
  if (styleAssessment.passed) {
    return deepFreeze({
      schema_version: RESPONSE_GUARD_SCHEMA_VERSION,
      decision: 'pass',
      reason_codes: [],
      guarded_text: guardedText,
      guarded_text_digest: digest(guardedText),
      assessment_status: 'evaluated',
      style_assessment: styleAssessment,
    })
  }
  return deepFreeze({
    schema_version: RESPONSE_GUARD_SCHEMA_VERSION,
    decision: 'block',
    reason_codes: [...styleAssessment.reason_codes],
    guarded_text: guardedText,
    guarded_text_digest: digest(guardedText),
    assessment_status: 'evaluated',
    style_assessment: styleAssessment,
  })
}

export function renderCountryOutageDeterministicFallback(
  _context: DomeyeAnswerContext,
): string {
  return '当前回答未通过安全检查，未形成可发布答案。'
}

export async function composeCountryOutageAnswer(
  context: DomeyeAnswerContext,
  renderer: DomeyeAnswerRenderer,
): Promise<DomeyeComposedAnswer> {
  let draft: DomeyeRendererDraft
  try {
    draft = await renderer.render(context)
  } catch {
    const fallback = renderCountryOutageDeterministicFallback(context)
    return deepFreeze({
      answer: fallback,
      answer_digest: digest(fallback),
      source: 'deterministic_fallback',
      guard_result: {
        schema_version: RESPONSE_GUARD_SCHEMA_VERSION,
        decision: 'block',
        reason_codes: ['renderer_failed_or_invalid'],
        guarded_text: fallback,
        guarded_text_digest: digest(fallback),
        assessment_status: 'not_evaluated',
        style_assessment: null,
      },
      render_attempt: {
        status: 'failed',
        draft: null,
        failure_code: 'renderer_failed_or_invalid' as const,
      },
    })
  }
  const guardResult = guardCountryOutageResponse(context, draft)
  if (guardResult.decision === 'pass') {
    return deepFreeze({
      answer: guardResult.guarded_text,
      answer_digest: guardResult.guarded_text_digest,
      source: 'renderer',
      guard_result: guardResult,
      render_attempt: {
        status: 'completed',
        draft,
        failure_code: null,
      },
    })
  }
  const fallback = renderCountryOutageDeterministicFallback(context)
  return deepFreeze({
    answer: fallback,
    answer_digest: digest(fallback),
    source: 'deterministic_fallback',
    guard_result: guardResult,
    render_attempt: {
      status: 'completed',
      draft,
      failure_code: null,
    },
  })
}
