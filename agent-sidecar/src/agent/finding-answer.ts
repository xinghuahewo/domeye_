import { canonicalJsonSha256 } from '../shared/deterministic-json.js'

import type {
  DomeyeAnswerContext,
  DomeyeActionReceipt,
  DomeyeArtifactEnvelope,
  DomeyeRendererDraft,
  DomeyeResponseGuardDecision,
  DomeyeTypedFinding,
} from './contracts.js'

const FINDING_SCHEMA_VERSION = 'domeye_agent_typed_finding_v1' as const
const ANSWER_CONTEXT_SCHEMA_VERSION = 'domeye_agent_answer_context_v1' as const
const RESPONSE_GUARD_SCHEMA_VERSION =
  'domeye_agent_response_guard_v1' as const

const METRIC = 'fixed_visible_ipv4_address_count' as const
const UNIT = 'unique_ipv4_address' as const
const OBSERVER_SCOPE = 'RRC25 单一观察点的 BGP 控制面观测' as const

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

export type DomeyeFindingAnswerErrorCode =
  | 'artifact_not_qualified'
  | 'receipt_not_qualified'
  | 'artifact_receipt_conflict'
  | 'identity_conflict'
  | 'payload_digest_conflict'
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
  source: 'renderer'
  guard_result: Extract<
    DomeyeResponseGuardDecision,
    { readonly decision: 'pass' }
  >
  render_attempt: DomeyeCompletedRenderAttempt
}>

export type DomeyeFallbackAnswer = Readonly<{
  answer: string
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

function hasAllText(text: string, values: readonly string[]): boolean {
  return values.every((value) => text.includes(value))
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
  contractDigest: string,
): DomeyeAnswerContext {
  if (!/^sha256:[a-f0-9]{64}$/.test(contractDigest)) {
    throw new DomeyeFindingAnswerError(
      'payload_digest_conflict',
      '首片合同摘要必须是 sha256 内容身份',
    )
  }
  const contextContent = {
    schema_version: ANSWER_CONTEXT_SCHEMA_VERSION,
    candidate_id: finding.candidate_id,
    contract_version: 'domeye.first-vertical-slice/v1.0' as const,
    contract_digest: contractDigest,
    data_identity: finding.data_identity,
    finding,
    observer_scope_zh: OBSERVER_SCOPE,
    mandatory_limitations_zh: COUNTRY_OUTAGE_MANDATORY_LIMITATIONS.map(
      (limitation) => limitation.text,
    ),
    forbidden_conclusions: [...COUNTRY_OUTAGE_FORBIDDEN_CONCLUSIONS],
    evidence_refs: [...finding.evidence_refs],
  }
  const contextDigest = digest(contextContent)
  return deepFreeze({
    ...contextContent,
    context_id: `answer-context-${contextDigest}`,
    context_digest: contextDigest,
  }) as DomeyeAnswerContext
}

function expectedTextNumbers(context: DomeyeAnswerContext): string[] {
  return Object.values(context.finding.values)
    .filter((value): value is number => typeof value === 'number')
    .map(String)
}

function expectedTextTimes(context: DomeyeAnswerContext): string[] {
  const values = context.finding.values
  return [
    values.first_at_utc,
    values.last_at_utc,
    values.minimum_at_utc,
    values.maximum_at_utc,
  ].filter((value): value is string => typeof value === 'string')
}

function hasUnknownNumber(
  text: string,
  context: DomeyeAnswerContext,
): boolean {
  const identityStrings = Object.values(context.data_identity)
    .filter((value): value is string => typeof value === 'string')
  const knownText = [
    ...context.mandatory_limitations_zh,
    ...context.evidence_refs,
    ...identityStrings,
    ...expectedTextTimes(context),
    context.context_id,
    context.finding.finding_id,
    context.candidate_id,
    context.contract_version,
    context.contract_digest,
    context.observer_scope_zh,
    context.finding.metric,
    context.finding.unit,
    'RRC25',
    'IPv4',
  ]
  const allowedNumbers = new Set([
    ...expectedTextNumbers(context),
    String(context.data_identity.revision),
  ])
  const remaining = redactKnownText(text, knownText)
  const found = remaining.matchAll(
    /(?<![\p{L}\p{N}_])-?\d+(?:\.\d+)?(?![\p{L}\p{N}_])/gu,
  )
  return [...found].some((match) => !allowedNumbers.has(match[0]))
}

function semanticText(
  text: string,
  context: DomeyeAnswerContext,
): string {
  return redactKnownText(text, context.mandatory_limitations_zh)
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

export function guardCountryOutageResponse(
  context: DomeyeAnswerContext,
  draft: DomeyeRendererDraft,
): DomeyeResponseGuardDecision {
  const reasons: string[] = []
  const finding = context.finding
  const identity = context.data_identity
  if (draft.schema_version !== 'domeye_agent_renderer_draft_v1') {
    reasons.push('renderer_schema_mismatch')
  }
  if (draft.context_id !== context.context_id) {
    reasons.push('answer_context_identity_mismatch')
  }
  if (draft.finding_id !== finding.finding_id) {
    reasons.push('finding_reference_mismatch')
  }
  if (draft.candidate_id !== context.candidate_id) {
    reasons.push('candidate_identity_mismatch')
  }
  if (
    draft.publication_id !== identity.publication_id
    || draft.revision !== identity.revision
    || draft.collector_id !== identity.collector_id
  ) {
    reasons.push('data_identity_mismatch')
  }
  if (
    draft.window_start_utc !== identity.window_start_utc
    || draft.window_end_utc !== identity.window_end_utc
  ) {
    reasons.push('window_identity_mismatch')
  }
  if (draft.metric !== finding.metric) {
    reasons.push('metric_mismatch')
  }
  const draftNumbers = {
    first: draft.values.first,
    last: draft.values.last,
    minimum: draft.values.minimum,
    maximum: draft.values.maximum,
    difference: draft.values.difference,
    net_change: draft.values.net_change,
  }
  const findingNumbers = {
    first: finding.values.first,
    last: finding.values.last,
    minimum: finding.values.minimum,
    maximum: finding.values.maximum,
    difference: finding.values.difference,
    net_change: finding.values.net_change,
  }
  if (!sameValue(draftNumbers, findingNumbers)) {
    reasons.push('number_mismatch')
  }
  const draftTimes = {
    first_at_utc: draft.values.first_at_utc,
    last_at_utc: draft.values.last_at_utc,
    minimum_at_utc: draft.values.minimum_at_utc,
    maximum_at_utc: draft.values.maximum_at_utc,
  }
  const findingTimes = {
    first_at_utc: finding.values.first_at_utc,
    last_at_utc: finding.values.last_at_utc,
    minimum_at_utc: finding.values.minimum_at_utc,
    maximum_at_utc: finding.values.maximum_at_utc,
  }
  if (!sameValue(draftTimes, findingTimes)) {
    reasons.push('observed_time_mismatch')
  }
  if (draft.unit !== finding.unit || !draft.text.includes(finding.unit)) {
    reasons.push('unit_mismatch')
  }
  if (
    draft.observer_scope_zh !== context.observer_scope_zh
    || !draft.text.includes(context.observer_scope_zh)
  ) {
    reasons.push('observer_scope_mismatch')
  }
  if (
    !includesSameMembers(
      draft.limitations_zh,
      context.mandatory_limitations_zh,
    )
    || !hasAllText(draft.text, context.mandatory_limitations_zh)
  ) {
    reasons.push('mandatory_limitation_missing')
  }
  if (!includesSameMembers(draft.evidence_refs, context.evidence_refs)) {
    reasons.push('evidence_reference_mismatch')
  }
  const requiredIdentityText = [
    identity.publication_id,
    `revision ${identity.revision}`,
    identity.collector_id.toUpperCase(),
    identity.window_start_utc,
    identity.window_end_utc,
  ]
  if (!hasAllText(draft.text, requiredIdentityText)) {
    reasons.push('visible_identity_missing')
  }
  if (!hasAllText(draft.text, expectedTextNumbers(context))) {
    reasons.push('visible_number_missing')
  }
  if (!hasAllText(draft.text, expectedTextTimes(context))) {
    reasons.push('visible_time_missing')
  }
  if (hasUnknownNumber(draft.text, context)) {
    reasons.push('content_outside_answer_context')
  }
  if (draft.text !== renderCountryOutageDeterministicFallback(context)) {
    reasons.push('content_outside_answer_context')
  }

  const semantics = semanticText(draft.text, context)
  if (/全国(?:互联网|网络|范围)?(?:已经|已|发生|出现|遭遇|处于|全面)?(?:中断|断网|瘫痪)|全网(?:中断|断网)|nationwide\s+(?:internet\s+)?outage|global\s+outage/iu.test(semantics)) {
    reasons.push('forbidden_national_outage_claim')
  }
  if (/(?:用户|设备|人口).{0,16}(?:受影响|断网|中断|无法上网)|(?:影响|波及).{0,12}(?:用户|人)|users?\s+(?:were\s+)?affected/iu.test(semantics)) {
    reasons.push('forbidden_user_impact_claim')
  }
  if (/(?:原因(?:是|为)|由于.{0,40}(?:导致|造成)|由.{0,40}(?:导致|造成)|归因于|caused\s+by)/iu.test(semantics)) {
    reasons.push('forbidden_cause_claim')
  }
  if (/(?:责任(?:在于|属于|由)|应当?负责|承担责任|responsible\s+for)/iu.test(semantics)) {
    reasons.push('forbidden_responsibility_claim')
  }
  if (/(?:已经|已|完全|实际|全面)(?:恢复|恢复正常)|恢复(?:完成|了)|fully\s+recovered/iu.test(semantics)) {
    reasons.push('forbidden_recovery_claim')
  }
  if (/(?:实际发生于|事件(?:的)?发生时间(?:是|为)|occurred\s+at)/iu.test(semantics)) {
    reasons.push('observed_time_overstated')
  }
  if (hasSensitiveEndpoint(draft.text)) {
    reasons.push('sensitive_endpoint_detected')
  }
  if (hasSensitiveCredential(draft.text)) {
    reasons.push('sensitive_credential_detected')
  }

  const reasonCodes = uniqueSorted(reasons)
  if (reasonCodes.length === 0) {
    return deepFreeze({
      schema_version: RESPONSE_GUARD_SCHEMA_VERSION,
      decision: 'pass',
      reason_codes: [],
    })
  }
  return deepFreeze({
    schema_version: RESPONSE_GUARD_SCHEMA_VERSION,
    decision: 'block',
    reason_codes: reasonCodes,
  })
}

export function renderCountryOutageDeterministicFallback(
  context: DomeyeAnswerContext,
): string {
  const identity = context.data_identity
  const finding = context.finding
  const lines = [
    `在 publication ${identity.publication_id}、revision ${identity.revision} 的固定窗口 ${identity.window_start_utc} 至 ${identity.window_end_utc} 内，${context.observer_scope_zh}结果如下。`,
    `指标：${finding.metric}；单位：${finding.unit}。`,
  ]
  if (finding.value_state === 'known') {
    const values = finding.values
    lines.push(
      `首值 ${values.first}，首次观测时刻 ${values.first_at_utc}。`,
      `末值 ${values.last}，末次观测时刻 ${values.last_at_utc}。`,
      `最低值 ${values.minimum}，首次最低观测时刻 ${values.minimum_at_utc}。`,
      `最大值 ${values.maximum}，首次最大观测时刻 ${values.maximum_at_utc}。`,
      `极差 ${values.difference}；首末净变化 ${values.net_change}。`,
    )
  } else {
    lines.push('当前冻结轨道没有有效观测点，不能按 0 生成正常极值事实。')
  }
  lines.push(
    ...context.mandatory_limitations_zh,
    `Evidence：${context.evidence_refs.join('；')}。`,
  )
  return lines.join('\n')
}

export async function composeCountryOutageAnswer(
  context: DomeyeAnswerContext,
  renderer: DomeyeAnswerRenderer,
): Promise<DomeyeComposedAnswer> {
  let draft: DomeyeRendererDraft
  try {
    draft = await renderer.render(context)
  } catch {
    return deepFreeze({
      answer: renderCountryOutageDeterministicFallback(context),
      source: 'deterministic_fallback',
      guard_result: {
        schema_version: RESPONSE_GUARD_SCHEMA_VERSION,
        decision: 'block',
        reason_codes: ['renderer_failed_or_invalid'],
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
      answer: draft.text,
      source: 'renderer',
      guard_result: guardResult,
      render_attempt: {
        status: 'completed',
        draft,
        failure_code: null,
      },
    })
  }
  return deepFreeze({
    answer: renderCountryOutageDeterministicFallback(context),
    source: 'deterministic_fallback',
    guard_result: guardResult,
    render_attempt: {
      status: 'completed',
      draft,
      failure_code: null,
    },
  })
}
