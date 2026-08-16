import { createHash } from 'node:crypto'

export type AddressFamily = 'ipv4' | 'ipv6' | 'both'
export type AnalysisMode = 'change_summary' | 'event_window_trend'
export type Answerability = 'supported' | 'partial' | 'clarify' | 'invalid_data' | 'unsupported'

export const FIXED_IPV4_METRIC = 'fixed_visible_ipv4_address_count' as const
export const FIXED_IPV6_METRIC = 'fixed_visible_ipv6_slash48_count' as const

export const ADDRESS_METRIC_DEFINITIONS = {
  [FIXED_IPV4_METRIC]: {
    definition: '规范化、去重并合重叠后的 IPv4 唯一地址并集。',
    label: '固定前缀可见 IPv4 地址量',
    unit: 'unique_ipv4_address',
    family: 'ipv4',
    population: 'fixed_cohort',
    temporalSemantics: 'state_gauge',
  },
  [FIXED_IPV6_METRIC]: {
    definition: '规范化、去重并合后的 IPv6 /48 等价并集。',
    label: '固定前缀可见 IPv6 /48 等价量',
    unit: 'ipv6_slash48_equivalent',
    family: 'ipv6',
    population: 'fixed_cohort',
    temporalSemantics: 'state_gauge',
  },
  new_cumulative_ipv4_prefix_count: {
    definition: '事件窗口内曾首次出现的新 IPv4 前缀累计数。',
    label: '累计出现新 IPv4 前缀',
    unit: 'prefix',
    family: 'ipv4',
    population: 'new_prefix',
    temporalSemantics: 'cumulative',
  },
  new_cumulative_ipv4_address_count: {
    definition: '累计新 IPv4 前缀的唯一地址并集。',
    label: '累计出现新 IPv4 地址量',
    unit: 'unique_ipv4_address',
    family: 'ipv4',
    population: 'new_prefix',
    temporalSemantics: 'cumulative',
  },
  new_visible_ipv4_prefix_count: {
    definition: 'cohort 冻结后首次出现且当前可见的新 IPv4 前缀。',
    label: '当前可见新 IPv4 前缀',
    unit: 'prefix',
    family: 'ipv4',
    population: 'new_prefix',
    temporalSemantics: 'state_gauge',
  },
  new_visible_ipv4_address_count: {
    definition: '当前可见新 IPv4 前缀的唯一地址并集。',
    label: '当前可见新 IPv4 地址量',
    unit: 'unique_ipv4_address',
    family: 'ipv4',
    population: 'new_prefix',
    temporalSemantics: 'state_gauge',
  },
  new_cumulative_ipv6_prefix_count: {
    definition: '事件窗口内曾首次出现的新 IPv6 前缀累计数。',
    label: '累计出现新 IPv6 前缀',
    unit: 'prefix',
    family: 'ipv6',
    population: 'new_prefix',
    temporalSemantics: 'cumulative',
  },
  new_cumulative_ipv6_slash48_count: {
    definition: '累计新 IPv6 前缀的 /48 等价并集。',
    label: '累计出现新 IPv6 /48 等价量',
    unit: 'ipv6_slash48_equivalent',
    family: 'ipv6',
    population: 'new_prefix',
    temporalSemantics: 'cumulative',
  },
  new_visible_ipv6_prefix_count: {
    definition: 'cohort 冻结后首次出现且当前可见的新 IPv6 前缀。',
    label: '当前可见新 IPv6 前缀',
    unit: 'prefix',
    family: 'ipv6',
    population: 'new_prefix',
    temporalSemantics: 'state_gauge',
  },
  new_visible_ipv6_slash48_count: {
    definition: '当前可见新 IPv6 前缀的 /48 等价并集。',
    label: '当前可见新 IPv6 /48 等价量',
    unit: 'ipv6_slash48_equivalent',
    family: 'ipv6',
    population: 'new_prefix',
    temporalSemantics: 'state_gauge',
  },
} as const

export type AddressMetric = keyof typeof ADDRESS_METRIC_DEFINITIONS
export type AddressCapabilityId =
  | 'CAP-001'
  | 'CAP-002'
  | 'CAP-006'
  | 'CAP-007'
  | 'CAP-008'
  | 'CAP-009'
  | 'CAP-016'
  | 'CAP-017'

const REGISTERED_S1_ADDRESS_CAPABILITIES: AddressCapabilityId[] = [
  'CAP-001', 'CAP-002', 'CAP-006', 'CAP-007', 'CAP-008', 'CAP-009', 'CAP-016', 'CAP-017',
]

export interface EventBinding {
  eventType: 'country_outage'
  incidentId: string
  publicationId: string
  revision: number
  collectorId: 'rrc25'
  countryCode: string
  windowStartUtc: string
  windowEndUtc: string
  dataThrough: string
  lifecycleState: 'event_end_unknown' | 'event_end_known'
  resolutionSchemaVersion: 'country_outage_general_resolution_v1'
  observationState: 'evidence_complete' | 'partial'
  qualityState: 'complete' | 'partial'
  capabilityIds: AddressCapabilityId[]
  identityEvidenceRefs: string[]
  legacyReference: string
  sourceCapabilities: {
    eventSeries: 'available' | 'unavailable'
    overview: 'available' | 'unavailable'
  }
}

export interface EventBindingVerificationReceipt {
  toolId: 'TOOL-P1-EVENT-BINDING-VERIFY'
  verificationMode: 'live_resolver' | 'fixture_contract'
  verified: true
  resolverEndpoint: string
  resolverResponseSha256: string
  overviewEndpoint: string
  overviewResponseSha256: string
  resolverIdentity: {
    eventType: 'country_outage'
    countryCode: string
    incidentId: string
    publicationId: string
    revision: number
    collectorId: 'rrc25'
    windowStartUtc: string
    windowEndUtc: string
    dataThrough: string
    lifecycleState: 'event_end_unknown' | 'event_end_known'
  }
  sourceCapabilities: {
    eventSeries: 'available'
    overview: 'available'
  }
  negotiatedCapabilityIds: AddressCapabilityId[]
  evidenceRefs: string[]
}

export interface EventBindingVerifier {
  readonly toolId: 'TOOL-P1-EVENT-BINDING-VERIFY'
  verify(
    binding: EventBinding,
    requiredCapabilityIds: AddressCapabilityId[],
    signal?: AbortSignal,
  ): Promise<EventBindingVerificationReceipt>
}

export interface SeriesPayload {
  schemaVersion: 'country_outage_general_series_v1'
  binding: EventBinding
  timestamps: string[]
  tracks: Partial<Record<AddressMetric, Array<number | null>>>
  definitions: Partial<Record<AddressMetric, { unit: string; definition: string }>>
  eventCountryIdentitySource: 'verified_event_binding'
  sourceReceipt: SeriesSourceReceipt
}

export interface SeriesSourceReceipt {
  sourceId: string
  endpoint: string
  responseSha256: string
}

export interface ReadSeriesRequest {
  binding: EventBinding
  bindingVerification: EventBindingVerificationReceipt
  metrics: AddressMetric[]
}

export interface ReadSeriesTool {
  readonly toolId: 'TOOL-P1-PAGE-SERIES-READ'
  read(request: ReadSeriesRequest, signal?: AbortSignal): Promise<SeriesPayload>
}

export interface PageSeriesExecutionContext {
  grantedPermissions: string[]
  timeoutMs: number
}

const DEFAULT_PAGE_SERIES_EXECUTION_CONTEXT: PageSeriesExecutionContext = {
  grantedPermissions: [],
  timeoutMs: 10_000,
}

export interface ControlledIpUserGoal {
  goalId: string
  kind: 'ip_address_change' | 'ip_address_trend'
  requestedText: string
  binding: EventBinding | null
  entities?: {
    addressFamily?: AddressFamily
    includeNewPrefixes?: boolean
    population?: 'fixed_cohort' | 'new_prefix_only'
    timeScope?: 'current_publication_window' | 'historical' | 'cross_event'
    trendProduct?: 'event_window' | 'formal_historical'
  }
}

export interface NormalizedIpUserGoal {
  goalId: string
  requestedText: string
  requestedGoal: 'fixed_cohort_address_change' | 'event_window_address_series_trend'
  supplementGoal: 'new_prefix_supplement' | null
  addressFamily: AddressFamily
  primaryPopulation: 'fixed_cohort' | null
  supplementPopulation: 'new_prefix' | null
  analysisMode: 'event_window_change_summary' | 'event_window_series_summary'
  timeScope: 'current_publication_window' | 'historical' | 'cross_event'
  formalHistoricalTrend: boolean
}

export interface GroundingNode {
  nodeId: string
  nodeKind: 'identity_gate' | 'series_analysis' | 'composition' | 'definition_gate'
  capabilityIds: AddressCapabilityId[]
  toolId: 'TOOL-P1-EVENT-BINDING-VERIFY' | 'TOOL-P1-PAGE-SERIES-READ' | null
  operatorId:
    | 'OP-P1-IDENTITY-GATE'
    | 'OP-P1-SERIES-EXTREMA'
    | 'OP-P1-CURRENT-VALUE'
    | 'OP-P1-ADDRESS-FAMILY-COMPARE'
    | 'OP-P1-METRIC-DEFINITION-GATE'
  metrics: AddressMetric[]
  dependsOn: string[]
}

export interface ControlledIpGroundingPlan {
  goalId: string
  normalizedUserGoal: NormalizedIpUserGoal
  analysisMode: AnalysisMode
  addressFamily: AddressFamily
  includeFixedCohort: boolean
  includeNewPrefixes: boolean
  formalHistoricalTrend: boolean
  answerability: Answerability
  reasonCode: string
  nodes: GroundingNode[]
}

export interface SeriesSummary {
  metric: AddressMetric
  unit: string
  definition: string
  population: 'fixed_cohort' | 'new_prefix'
  temporalSemantics: 'state_gauge' | 'cumulative'
  first: number
  firstAtUtc: string
  last: number
  lastAtUtc: string
  minimum: number
  minimumAtUtc: string
  maximum: number
  maximumAtUtc: string
  netChange: number
  observedPointCount: number
  nullPointCount: number
  direction: 'increased' | 'decreased' | 'unchanged'
  evidenceRefs: string[]
}

export interface CurrentMetricValue {
  metric: AddressMetric
  unit: string
  definition: string
  population: 'fixed_cohort' | 'new_prefix'
  temporalSemantics: 'state_gauge' | 'cumulative'
  value: number
  observedAtUtc: string
  evidenceRefs: string[]
}

export interface AddressFamilyComparison {
  operatorId: 'OP-P1-ADDRESS-FAMILY-COMPARE'
  operatorVersion: 'v1'
  ipv4Metric: typeof FIXED_IPV4_METRIC
  ipv6Metric: typeof FIXED_IPV6_METRIC
  unitPolicy: 'separate_units_only'
  combinedAbsoluteTotal: 'forbidden'
  evidenceRefs: string[]
}

export interface PageQaDialogState {
  topic: 'address_visibility' | null
  addressFamily: AddressFamily | null
  primaryPopulation: 'fixed_cohort' | null
  includeNewPrefixes: boolean | null
  analysisMode: AnalysisMode | null
  formalHistoricalTrend: false | null
}

export interface PageQaEvidenceState {
  bindingIdentity: string | null
  verifiedGoalIds: string[]
  verifiedMetrics: AddressMetric[]
  metricEvidenceRefs: Partial<Record<AddressMetric, string[]>>
}

export interface PageQaState {
  dialog: PageQaDialogState
  evidence: PageQaEvidenceState
}

export interface PageQaStateReceipt {
  before: PageQaState
  proposal: PageQaState | null
  after: PageQaState
  commit: 'committed' | 'none'
  committedVerifiedFamilies: Array<'ipv4' | 'ipv6'>
  rejectedOrMissingMetrics: AddressMetric[]
}

export interface IpQuestionResult {
  goalId: string
  answerability: Answerability
  reasonCode: string
  answerText: string
  groundingPlan: ControlledIpGroundingPlan
  summaries: SeriesSummary[]
  currentValues: CurrentMetricValue[]
  comparison: AddressFamilyComparison | null
  bindingVerification: EventBindingVerificationReceipt | null
  evidenceRefs: string[]
  evidenceBindings: EvidenceBinding[]
  operatorReceipts: OperatorExecutionReceipt[]
  stateCommit: 'committed' | 'none'
  stateReceipt: PageQaStateReceipt
  limitations: string[]
}

export interface EvidenceBinding {
  evidenceId: string
  sourceId: string
  sourceEndpoint: string
  responseSha256: string
  jsonPointers: string[]
}

export interface OperatorExecutionReceipt {
  receiptId: string
  nodeId: string
  operatorId: GroundingNode['operatorId']
  operatorVersion: 'v1'
  inputEvidenceIds: string[]
  inputOperatorReceiptIds: string[]
  output: unknown
}

export class PageSeriesContractError extends Error {
  constructor(readonly code: string, message: string) {
    super(message)
    this.name = 'PageSeriesContractError'
  }
}

const FIXED_METRICS_BY_FAMILY: Record<'ipv4' | 'ipv6', AddressMetric> = {
  ipv4: FIXED_IPV4_METRIC,
  ipv6: FIXED_IPV6_METRIC,
}

const NEW_METRICS_BY_FAMILY: Record<'ipv4' | 'ipv6', AddressMetric[]> = {
  ipv4: [
    'new_cumulative_ipv4_prefix_count',
    'new_cumulative_ipv4_address_count',
    'new_visible_ipv4_prefix_count',
    'new_visible_ipv4_address_count',
  ],
  ipv6: [
    'new_cumulative_ipv6_prefix_count',
    'new_cumulative_ipv6_slash48_count',
    'new_visible_ipv6_prefix_count',
    'new_visible_ipv6_slash48_count',
  ],
}

function selectedFamilies(family: AddressFamily): Array<'ipv4' | 'ipv6'> {
  return family === 'both' ? ['ipv4', 'ipv6'] : [family]
}

function unique<T>(values: T[]): T[] {
  return [...new Set(values)]
}

function requestAborted(signal?: AbortSignal): boolean {
  return signal?.aborted === true
}

async function runWithDeadline<T>(
  operation: (signal: AbortSignal) => Promise<T>,
  externalSignal: AbortSignal | undefined,
  deadlineEpochMs: number,
): Promise<T> {
  if (requestAborted(externalSignal)) {
    throw new PageSeriesContractError('request_aborted', '请求已取消')
  }
  const remainingMs = deadlineEpochMs - Date.now()
  if (remainingMs <= 0) {
    throw new PageSeriesContractError('tool_timeout', '执行预算已耗尽')
  }
  const controller = new AbortController()
  let timer: ReturnType<typeof setTimeout> | undefined
  let abortListener: (() => void) | undefined
  const stop = new Promise<never>((_resolve, reject) => {
    timer = setTimeout(() => {
      reject(new PageSeriesContractError('tool_timeout', '执行超过受控超时预算'))
      controller.abort()
    }, remainingMs)
    if (externalSignal !== undefined) {
      abortListener = () => {
        reject(new PageSeriesContractError('request_aborted', '请求已取消'))
        controller.abort()
      }
      externalSignal.addEventListener('abort', abortListener, { once: true })
    }
  })
  try {
    return await Promise.race([operation(controller.signal), stop])
  } finally {
    if (timer !== undefined) clearTimeout(timer)
    if (externalSignal !== undefined && abortListener !== undefined) {
      externalSignal.removeEventListener('abort', abortListener)
    }
  }
}

export function emptyPageQaState(): PageQaState {
  return {
    dialog: {
      topic: null,
      addressFamily: null,
      primaryPopulation: null,
      includeNewPrefixes: null,
      analysisMode: null,
      formalHistoricalTrend: null,
    },
    evidence: {
      bindingIdentity: null,
      verifiedGoalIds: [],
      verifiedMetrics: [],
      metricEvidenceRefs: {},
    },
  }
}

export function normalizeControlledIpGoal(goal: ControlledIpUserGoal): NormalizedIpUserGoal {
  const addressFamily = goal.entities?.addressFamily ?? 'both'
  const includeFixedCohort = goal.entities?.population !== 'new_prefix_only'
  const includeNewPrefixes = goal.entities?.population === 'new_prefix_only'
    ? true
    : goal.entities?.includeNewPrefixes ?? true
  return {
    goalId: goal.goalId,
    requestedText: goal.requestedText,
    requestedGoal: goal.kind === 'ip_address_trend'
      ? 'event_window_address_series_trend'
      : 'fixed_cohort_address_change',
    supplementGoal: includeNewPrefixes ? 'new_prefix_supplement' : null,
    addressFamily,
    primaryPopulation: includeFixedCohort ? 'fixed_cohort' : null,
    supplementPopulation: includeNewPrefixes ? 'new_prefix' : null,
    analysisMode: goal.kind === 'ip_address_trend'
      ? 'event_window_series_summary'
      : 'event_window_change_summary',
    timeScope: goal.entities?.trendProduct === 'formal_historical'
      ? 'historical'
      : goal.entities?.timeScope ?? 'current_publication_window',
    formalHistoricalTrend: goal.entities?.trendProduct === 'formal_historical',
  }
}

export function groundControlledIpGoal(goal: ControlledIpUserGoal): ControlledIpGroundingPlan {
  const normalizedUserGoal = normalizeControlledIpGoal(goal)
  const addressFamily = normalizedUserGoal.addressFamily
  const timeScope = normalizedUserGoal.timeScope
  const includeFixedCohort = normalizedUserGoal.primaryPopulation === 'fixed_cohort'
  const includeNewPrefixes = normalizedUserGoal.supplementPopulation === 'new_prefix'
  const analysisMode: AnalysisMode = goal.kind === 'ip_address_trend'
    ? 'event_window_trend'
    : 'change_summary'

  if (goal.binding === null) {
    return {
      goalId: goal.goalId,
      normalizedUserGoal,
      analysisMode,
      addressFamily,
      includeFixedCohort,
      includeNewPrefixes,
      formalHistoricalTrend: normalizedUserGoal.formalHistoricalTrend,
      answerability: 'clarify',
      reasonCode: 'event_binding_required',
      nodes: [],
    }
  }
  if (timeScope !== 'current_publication_window') {
    return {
      goalId: goal.goalId,
      normalizedUserGoal,
      analysisMode,
      addressFamily,
      includeFixedCohort,
      includeNewPrefixes,
      formalHistoricalTrend: normalizedUserGoal.formalHistoricalTrend,
      answerability: 'unsupported',
      reasonCode: 'formal_or_cross_event_trend_not_in_s1',
      nodes: [],
    }
  }

  const families = selectedFamilies(addressFamily)
  const fixedMetrics = includeFixedCohort
    ? families.map((family) => FIXED_METRICS_BY_FAMILY[family])
    : []
  const newMetrics = includeNewPrefixes
    ? families.flatMap((family) => NEW_METRICS_BY_FAMILY[family])
    : []
  const identityNodeId = `${goal.goalId}:identity`
  const nodes: GroundingNode[] = [{
    nodeId: identityNodeId,
    nodeKind: 'identity_gate',
    capabilityIds: ['CAP-001', 'CAP-002'],
    toolId: 'TOOL-P1-EVENT-BINDING-VERIFY',
    operatorId: 'OP-P1-IDENTITY-GATE',
    metrics: [],
    dependsOn: [],
  }]
  const fixedNodeIds: string[] = []
  for (const family of families) {
    if (!includeFixedCohort) continue
    const metric = FIXED_METRICS_BY_FAMILY[family]
    const nodeId = `${goal.goalId}:fixed:${family}`
    fixedNodeIds.push(nodeId)
    nodes.push({
      nodeId,
      nodeKind: 'series_analysis',
      capabilityIds: [family === 'ipv4' ? 'CAP-006' : 'CAP-007', 'CAP-016'],
      toolId: 'TOOL-P1-PAGE-SERIES-READ',
      operatorId: 'OP-P1-SERIES-EXTREMA',
      metrics: [metric],
      dependsOn: [identityNodeId],
    })
  }
  if (fixedMetrics.length === 2) {
    nodes.push({
      nodeId: `${goal.goalId}:compare`,
      nodeKind: 'composition',
      capabilityIds: ['CAP-017'],
      toolId: null,
      operatorId: 'OP-P1-ADDRESS-FAMILY-COMPARE',
      metrics: fixedMetrics,
      dependsOn: fixedNodeIds,
    })
  }
  if (newMetrics.length > 0) {
    nodes.push({
      nodeId: `${goal.goalId}:new`,
      nodeKind: 'series_analysis',
      capabilityIds: ['CAP-008'],
      toolId: 'TOOL-P1-PAGE-SERIES-READ',
      operatorId: 'OP-P1-CURRENT-VALUE',
      metrics: newMetrics,
      dependsOn: [identityNodeId],
    })
  }
  const allMetrics = unique([...fixedMetrics, ...newMetrics])
  if (allMetrics.length > 0) {
    nodes.push({
      nodeId: `${goal.goalId}:definitions`,
      nodeKind: 'definition_gate',
      capabilityIds: ['CAP-009'],
      toolId: 'TOOL-P1-PAGE-SERIES-READ',
      operatorId: 'OP-P1-METRIC-DEFINITION-GATE',
      metrics: allMetrics,
      dependsOn: [identityNodeId],
    })
  }
  return {
    goalId: goal.goalId,
    normalizedUserGoal,
    analysisMode,
    addressFamily,
    includeFixedCohort,
    includeNewPrefixes,
    formalHistoricalTrend: normalizedUserGoal.formalHistoricalTrend,
    answerability: 'supported',
    reasonCode: goal.entities?.addressFamily === undefined
      ? 'generic_ip_defaults_to_ipv4_and_ipv6'
      : 'explicit_address_family',
    nodes,
  }
}

function assertIsoTimestamp(value: string, field: string): void {
  if (!Number.isFinite(Date.parse(value))) {
    throw new PageSeriesContractError('invalid_timestamp', `${field} 不是合法时间`)
  }
}

export function verifyEventBinding(
  binding: EventBinding,
  plan: ControlledIpGroundingPlan,
): void {
  if (binding.eventType !== 'country_outage') {
    throw new PageSeriesContractError('event_type_not_allowed', '仅允许 country_outage 事件')
  }
  if (binding.collectorId !== 'rrc25') {
    throw new PageSeriesContractError('collector_not_allowed', 'S1 仅允许 RRC25')
  }
  if (binding.resolutionSchemaVersion !== 'country_outage_general_resolution_v1') {
    throw new PageSeriesContractError('binding_not_verified', '事件绑定缺少受控 resolver 回执')
  }
  if (!/^[A-Z]{2}$/.test(binding.countryCode)) {
    throw new PageSeriesContractError('country_identity_invalid', '国家代码身份不合法')
  }
  if (binding.identityEvidenceRefs.length === 0) {
    throw new PageSeriesContractError('binding_evidence_missing', '事件绑定缺少身份 evidence ref')
  }
  if (binding.legacyReference.length === 0) {
    throw new PageSeriesContractError('binding_reference_missing', '事件绑定缺少 legacy reference')
  }
  if (binding.sourceCapabilities.eventSeries !== 'available'
    || binding.sourceCapabilities.overview !== 'available') {
    throw new PageSeriesContractError(
      'source_capability_unavailable',
      'resolver 未声明 event_series 与 overview 可用',
    )
  }
  assertIsoTimestamp(binding.windowStartUtc, 'windowStartUtc')
  assertIsoTimestamp(binding.windowEndUtc, 'windowEndUtc')
  assertIsoTimestamp(binding.dataThrough, 'dataThrough')
  if (
    Date.parse(binding.windowStartUtc) >= Date.parse(binding.windowEndUtc)
    || Date.parse(binding.dataThrough) !== Date.parse(binding.windowEndUtc)
  ) {
    throw new PageSeriesContractError('binding_time_invalid', '事件窗口或 data through 不合法')
  }
}

function requiredCapabilityIds(plan: ControlledIpGroundingPlan): AddressCapabilityId[] {
  return unique(plan.nodes.flatMap((node) => node.capabilityIds))
}

function assertResolverIdentity(
  binding: EventBinding,
  identity: EventBindingVerificationReceipt['resolverIdentity'],
): void {
  const checks: Array<[string, string | number, string | number]> = [
    ['event_type', binding.eventType, identity.eventType],
    ['country_code', binding.countryCode, identity.countryCode],
    ['incident_id', binding.incidentId, identity.incidentId],
    ['publication_id', binding.publicationId, identity.publicationId],
    ['revision', binding.revision, identity.revision],
    ['collector_id', binding.collectorId, identity.collectorId],
    ['window_start_utc', binding.windowStartUtc, identity.windowStartUtc],
    ['window_end_utc', binding.windowEndUtc, identity.windowEndUtc],
    ['data_through', binding.dataThrough, identity.dataThrough],
    ['lifecycle_state', binding.lifecycleState, identity.lifecycleState],
  ]
  for (const [field, expected, actual] of checks) {
    if (actual !== expected) {
      throw new PageSeriesContractError('binding_resolver_conflict', `resolver identity mismatch: ${field}`)
    }
  }
}

function assertVerificationReceipt(
  binding: EventBinding,
  receipt: EventBindingVerificationReceipt,
  required: AddressCapabilityId[],
): void {
  if (!receipt.verified || receipt.toolId !== 'TOOL-P1-EVENT-BINDING-VERIFY') {
    throw new PageSeriesContractError('binding_not_verified', '事件绑定没有通过 resolver 验证')
  }
  assertResolverIdentity(binding, receipt.resolverIdentity)
  if (receipt.sourceCapabilities.eventSeries !== 'available'
    || receipt.sourceCapabilities.overview !== 'available') {
    throw new PageSeriesContractError('source_capability_unavailable', 'resolver 能力不可用')
  }
  const negotiated = new Set(receipt.negotiatedCapabilityIds)
  const missing = required.filter((capabilityId) => !negotiated.has(capabilityId))
  if (missing.length > 0) {
    throw new PageSeriesContractError(
      'capability_not_negotiated',
      `resolver 未协商能力：${missing.join(',')}`,
    )
  }
  if (receipt.resolverResponseSha256.length !== 64
    || receipt.overviewResponseSha256.length !== 64
    || receipt.evidenceRefs.length === 0) {
    throw new PageSeriesContractError('binding_evidence_missing', 'resolver 原始回执证据不完整')
  }
}

export class LocalEventBindingVerifier implements EventBindingVerifier {
  readonly toolId = 'TOOL-P1-EVENT-BINDING-VERIFY' as const

  async verify(
    binding: EventBinding,
    required: AddressCapabilityId[],
    signal?: AbortSignal,
  ): Promise<EventBindingVerificationReceipt> {
    if (requestAborted(signal)) {
      throw new PageSeriesContractError('request_aborted', '请求已取消')
    }
    const fixturePlan = {
      nodes: [{ capabilityIds: required }],
    } as ControlledIpGroundingPlan
    verifyEventBinding(binding, fixturePlan)
    const resolverResponseSha256 = createHash('sha256')
      .update(JSON.stringify(binding))
      .digest('hex')
    const receipt: EventBindingVerificationReceipt = {
      toolId: this.toolId,
      verificationMode: 'fixture_contract',
      verified: true,
      resolverEndpoint: `fixture:${binding.legacyReference}`,
      resolverResponseSha256,
      overviewEndpoint: `fixture:/api/v2/country-outages/${binding.incidentId}/overview`,
      overviewResponseSha256: resolverResponseSha256,
      resolverIdentity: {
        eventType: binding.eventType,
        countryCode: binding.countryCode,
        incidentId: binding.incidentId,
        publicationId: binding.publicationId,
        revision: binding.revision,
        collectorId: binding.collectorId,
        windowStartUtc: binding.windowStartUtc,
        windowEndUtc: binding.windowEndUtc,
        dataThrough: binding.dataThrough,
        lifecycleState: binding.lifecycleState,
      },
      sourceCapabilities: { eventSeries: 'available', overview: 'available' },
      negotiatedCapabilityIds: [...binding.capabilityIds],
      evidenceRefs: [
        ...binding.identityEvidenceRefs,
        `sha256:${resolverResponseSha256}`,
      ],
    }
    assertVerificationReceipt(binding, receipt, required)
    return receipt
  }
}

class RejectingEventBindingVerifier implements EventBindingVerifier {
  readonly toolId = 'TOOL-P1-EVENT-BINDING-VERIFY' as const

  async verify(): Promise<EventBindingVerificationReceipt> {
    throw new PageSeriesContractError(
      'live_binding_verifier_required',
      '宿主必须显式注入真实 resolver+overview 验证器',
    )
  }
}

function bindingStateIdentity(binding: EventBinding): string {
  return [
    binding.incidentId,
    binding.publicationId,
    binding.revision,
    binding.collectorId,
    binding.dataThrough,
  ].join(':')
}

function assertBinding(expected: EventBinding, actual: EventBinding): void {
  const keys: Array<keyof EventBinding> = [
    'eventType', 'incidentId', 'publicationId', 'revision', 'collectorId',
    'countryCode', 'windowStartUtc', 'windowEndUtc', 'dataThrough', 'lifecycleState',
  ]
  for (const key of keys) {
    if (expected[key] !== actual[key]) {
      throw new PageSeriesContractError('identity_conflict', `series identity mismatch: ${key}`)
    }
  }
  if (actual.collectorId !== 'rrc25') {
    throw new PageSeriesContractError('collector_not_allowed', 'S1 仅允许 RRC25')
  }
}

export function validateSeriesPayload(payload: SeriesPayload, request: ReadSeriesRequest): void {
  if (payload.schemaVersion !== 'country_outage_general_series_v1') {
    throw new PageSeriesContractError('schema_mismatch', 'series schema 不匹配')
  }
  if (payload.eventCountryIdentitySource !== 'verified_event_binding') {
    throw new PageSeriesContractError(
      'identity_provenance_missing',
      'series 缺少 event/country 的已验证绑定来源',
    )
  }
  assertVerificationReceipt(request.binding, request.bindingVerification, [])
  if (payload.sourceReceipt.sourceId.length === 0
    || payload.sourceReceipt.endpoint.length === 0
    || payload.sourceReceipt.responseSha256.length !== 64) {
    throw new PageSeriesContractError('series_source_receipt_invalid', 'series 原始响应身份不完整')
  }
  assertBinding(request.binding, payload.binding)
  if (payload.timestamps.length === 0) {
    throw new PageSeriesContractError('empty_timestamps', 'series 时间轴为空')
  }
  let previous = -Infinity
  payload.timestamps.forEach((timestamp, index) => {
    assertIsoTimestamp(timestamp, `timestamps[${index}]`)
    const current = Date.parse(timestamp)
    if (current <= previous) {
      throw new PageSeriesContractError('unordered_timestamps', 'series 时间轴必须严格递增')
    }
    previous = current
  })
  for (const metric of request.metrics) {
    const track = payload.tracks[metric]
    if (track === undefined) continue
    if (track.length !== payload.timestamps.length) {
      throw new PageSeriesContractError('track_length_mismatch', `${metric} 轨道长度与时间轴不一致`)
    }
    const definition = payload.definitions[metric]
    const expectedDefinition = ADDRESS_METRIC_DEFINITIONS[metric]
    if (definition === undefined || definition.unit !== expectedDefinition.unit) {
      throw new PageSeriesContractError('unit_mismatch', `${metric} 单位不符合登记合同`)
    }
    if (definition.definition !== expectedDefinition.definition) {
      throw new PageSeriesContractError(
        'metric_definition_mismatch',
        `${metric} 指标定义不符合登记合同`,
      )
    }
    for (const value of track) {
      if (value !== null && (!Number.isFinite(value) || value < 0)) {
        throw new PageSeriesContractError('invalid_metric_value', `${metric} 包含非法值`)
      }
    }
  }
}

export function summarizeSeries(
  metric: AddressMetric,
  timestamps: string[],
  values: Array<number | null>,
  sourceReceipt: SeriesSourceReceipt,
): SeriesSummary | null {
  const observed = values
    .map((value, index) => ({ value, index }))
    .filter((point): point is { value: number; index: number } => point.value !== null)
  if (observed.length === 0) return null
  let minimum = observed[0]!
  let maximum = observed[0]!
  for (const point of observed.slice(1)) {
    if (point.value < minimum.value) minimum = point
    if (point.value > maximum.value) maximum = point
  }
  const first = observed[0]!
  const last = observed[observed.length - 1]!
  const netChange = last.value - first.value
  const definition = ADDRESS_METRIC_DEFINITIONS[metric]
  return {
    metric,
    unit: definition.unit,
    definition: definition.definition,
    population: definition.population,
    temporalSemantics: definition.temporalSemantics,
    first: first.value,
    firstAtUtc: timestamps[first.index]!,
    last: last.value,
    lastAtUtc: timestamps[last.index]!,
    minimum: minimum.value,
    minimumAtUtc: timestamps[minimum.index]!,
    maximum: maximum.value,
    maximumAtUtc: timestamps[maximum.index]!,
    netChange,
    observedPointCount: observed.length,
    nullPointCount: values.length - observed.length,
    direction: netChange > 0 ? 'increased' : netChange < 0 ? 'decreased' : 'unchanged',
    evidenceRefs: [
      sourceEvidenceRef(sourceReceipt, `/tracks/${metric}`),
      sourceEvidenceRef(sourceReceipt, `/track_definitions/${metric}`),
      sourceEvidenceRef(sourceReceipt, '/timestamps'),
      `operator:OP-P1-SERIES-EXTREMA:${metric}`,
    ],
  }
}

function currentMetricValue(
  metric: AddressMetric,
  timestamps: string[],
  values: Array<number | null>,
  dataThrough: string,
  sourceReceipt: SeriesSourceReceipt,
): CurrentMetricValue | null {
  const index = timestamps.findIndex((timestamp) => timestamp === dataThrough)
  if (index < 0) return null
  const value = values[index]
  if (value === null || value === undefined) return null
  return {
    metric,
    unit: ADDRESS_METRIC_DEFINITIONS[metric].unit,
    definition: ADDRESS_METRIC_DEFINITIONS[metric].definition,
    population: ADDRESS_METRIC_DEFINITIONS[metric].population,
    temporalSemantics: ADDRESS_METRIC_DEFINITIONS[metric].temporalSemantics,
    value,
    observedAtUtc: timestamps[index]!,
    evidenceRefs: [
      sourceEvidenceRef(sourceReceipt, `/tracks/${metric}/${index}`),
      sourceEvidenceRef(sourceReceipt, `/track_definitions/${metric}`),
      sourceEvidenceRef(sourceReceipt, `/timestamps/${index}`),
      `operator:OP-P1-CURRENT-VALUE:${metric}`,
    ],
  }
}

function sourceEvidenceRef(source: SeriesSourceReceipt, pointer: string): string {
  return `source:${source.sourceId}:sha256:${source.responseSha256}#${pointer}`
}

function numberText(value: number): string {
  return new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 4 }).format(value)
}

function fixedSummaryText(summary: SeriesSummary, analysisMode: AnalysisMode): string {
  const family = summary.metric === FIXED_IPV4_METRIC ? 'IPv4' : 'IPv6'
  const unit = summary.unit === 'unique_ipv4_address' ? '个唯一 IPv4 地址' : '个 IPv6 /48 等价块'
  const movement = summary.direction === 'decreased' ? '减少' : summary.direction === 'increased' ? '增加' : '不变'
  const base = `${family} 固定 cohort（${summary.definition}）：从 ${numberText(summary.first)} ${unit}（${summary.firstAtUtc}）变为 ${numberText(summary.last)} ${unit}（${summary.lastAtUtc}），净${movement} ${numberText(Math.abs(summary.netChange))}；窗口最小值 ${numberText(summary.minimum)}（${summary.minimumAtUtc}），最大值 ${numberText(summary.maximum)}（${summary.maximumAtUtc}）；有效观测 ${summary.observedPointCount} 点，null ${summary.nullPointCount} 点。`
  if (analysisMode === 'event_window_trend') {
    return `${base} 这是当前 publication 观测窗口内的确定性时序概括。`
  }
  return base
}

function newPrefixText(values: CurrentMetricValue[], family: 'ipv4' | 'ipv6'): string | null {
  const prefixMetric: AddressMetric = family === 'ipv4'
    ? 'new_cumulative_ipv4_prefix_count'
    : 'new_cumulative_ipv6_prefix_count'
  const visiblePrefixMetric: AddressMetric = family === 'ipv4'
    ? 'new_visible_ipv4_prefix_count'
    : 'new_visible_ipv6_prefix_count'
  const scaleMetric: AddressMetric = family === 'ipv4'
    ? 'new_visible_ipv4_address_count'
    : 'new_visible_ipv6_slash48_count'
  const cumulative = values.find((value) => value.metric === prefixMetric)
  const visible = values.find((value) => value.metric === visiblePrefixMetric)
  const scale = values.find((value) => value.metric === scaleMetric)
  if (cumulative === undefined || visible === undefined || scale === undefined) return null
  const scaleUnit = family === 'ipv4' ? '个唯一 IPv4 地址' : '个 IPv6 /48 等价块'
  return `${family.toUpperCase()} 新前缀补充：在 data-through 点 ${cumulative.observedAtUtc}，窗口累计出现 ${numberText(cumulative.value)} 条；“当前可见”轨道为 ${numberText(visible.value)} 条，对应 ${numberText(scale.value)} ${scaleUnit}。任一 data-through 点为 null 时整项标记 unavailable，不向前回填。`
}

function failureResult(
  goal: ControlledIpUserGoal,
  plan: ControlledIpGroundingPlan,
  answerability: Answerability,
  reasonCode: string,
  answerText: string,
  stateBefore: PageQaState,
  bindingVerification: EventBindingVerificationReceipt | null = null,
): IpQuestionResult {
  const before = structuredClone(stateBefore)
  return {
    goalId: goal.goalId,
    answerability,
    reasonCode,
    answerText,
    groundingPlan: plan,
    summaries: [],
    currentValues: [],
    comparison: null,
    bindingVerification,
    evidenceRefs: [],
    evidenceBindings: [],
    operatorReceipts: [],
    stateCommit: 'none',
    stateReceipt: {
      before,
      proposal: null,
      after: structuredClone(before),
      commit: 'none',
      committedVerifiedFamilies: [],
      rejectedOrMissingMetrics: [],
    },
    limitations: ['未发布任何未经验证的事实。'],
  }
}

export async function answerControlledIpQuestion(
  goal: ControlledIpUserGoal,
  tool: ReadSeriesTool,
  signal?: AbortSignal,
  initialState: PageQaState = emptyPageQaState(),
  bindingVerifier: EventBindingVerifier = new RejectingEventBindingVerifier(),
  executionContext: PageSeriesExecutionContext = DEFAULT_PAGE_SERIES_EXECUTION_CONTEXT,
): Promise<IpQuestionResult> {
  const plan = groundControlledIpGoal(goal)
  if (plan.answerability === 'clarify') {
    return failureResult(goal, plan, 'clarify', plan.reasonCode, '请先绑定一个 country_outage 事件。', initialState)
  }
  if (plan.answerability === 'unsupported') {
    return failureResult(goal, plan, 'unsupported', plan.reasonCode, 'S1 只回答当前 publication 窗口内走势，不提供历史或跨事件趋势。', initialState)
  }
  if (requestAborted(signal)) {
    return failureResult(goal, plan, 'invalid_data', 'request_aborted', '请求已取消，未执行数据读取。', initialState)
  }
  if (!executionContext.grantedPermissions.includes('country_outage_event_read')) {
    return failureResult(
      goal,
      plan,
      'invalid_data',
      'permission_denied',
      '当前宿主未授予 country_outage_event_read，未执行数据读取。',
      initialState,
    )
  }
  if (!Number.isInteger(executionContext.timeoutMs) || executionContext.timeoutMs <= 0) {
    return failureResult(
      goal,
      plan,
      'invalid_data',
      'invalid_timeout_budget',
      '执行超时预算不合法，未执行数据读取。',
      initialState,
    )
  }
  const binding = goal.binding
  if (binding === null) {
    return failureResult(goal, plan, 'clarify', 'event_binding_required', '请先绑定事件。', initialState)
  }
  const activeBindingIdentity = bindingStateIdentity(binding)
  if (initialState.evidence.bindingIdentity !== null
    && initialState.evidence.bindingIdentity !== activeBindingIdentity) {
    return failureResult(
      goal,
      plan,
      'invalid_data',
      'evidence_state_binding_conflict',
      '既有 EvidenceState 与当前事件 publication 身份冲突，未执行读取或状态合并。',
      initialState,
    )
  }
  const metrics = unique(plan.nodes.flatMap((node) => node.metrics))
  let payload: SeriesPayload
  let bindingVerification: EventBindingVerificationReceipt | null = null
  const deadlineEpochMs = Date.now() + executionContext.timeoutMs
  try {
    verifyEventBinding(binding, plan)
    const required = requiredCapabilityIds(plan)
    bindingVerification = await runWithDeadline(
      (controlledSignal) => bindingVerifier.verify(binding, required, controlledSignal),
      signal,
      deadlineEpochMs,
    )
    assertVerificationReceipt(binding, bindingVerification, required)
    if (requestAborted(signal)) {
      return failureResult(
        goal,
        plan,
        'invalid_data',
        'request_aborted',
        '请求已取消，未执行时序数据读取。',
        initialState,
        bindingVerification,
      )
    }
    payload = await runWithDeadline(
      (controlledSignal) => tool.read(
        { binding, bindingVerification: bindingVerification!, metrics },
        controlledSignal,
      ),
      signal,
      deadlineEpochMs,
    )
    if (requestAborted(signal)) {
      return failureResult(
        goal,
        plan,
        'invalid_data',
        'request_aborted',
        '请求已取消，未发布答案。',
        initialState,
        bindingVerification,
      )
    }
    validateSeriesPayload(payload, { binding, bindingVerification, metrics })
  } catch (error) {
    const reasonCode = error instanceof PageSeriesContractError ? error.code : 'series_tool_failed'
    return failureResult(
      goal,
      plan,
      'invalid_data',
      reasonCode,
      '时序数据未通过身份、能力、形状或单位校验，未发布事实。',
      initialState,
      bindingVerification,
    )
  }

  const fixedMetrics = plan.nodes
    .filter((node) => node.operatorId === 'OP-P1-SERIES-EXTREMA')
    .flatMap((node) => node.metrics)
  const newMetrics = plan.nodes
    .filter((node) => node.operatorId === 'OP-P1-CURRENT-VALUE')
    .flatMap((node) => node.metrics)
  const summaries: SeriesSummary[] = []
  const missingFixed: AddressMetric[] = []
  for (const metric of fixedMetrics) {
    const values = payload.tracks[metric]
    const summary = values === undefined
      ? null
      : summarizeSeries(metric, payload.timestamps, values, payload.sourceReceipt)
    if (summary === null) missingFixed.push(metric)
    else summaries.push(summary)
  }
  const currentValues: CurrentMetricValue[] = []
  const missingNew: AddressMetric[] = []
  for (const metric of newMetrics) {
    const values = payload.tracks[metric]
    const current = values === undefined
      ? null
      : currentMetricValue(
          metric,
          payload.timestamps,
          values,
          binding.dataThrough,
          payload.sourceReceipt,
        )
    if (current !== null) currentValues.push(current)
    else missingNew.push(metric)
  }

  if (fixedMetrics.length > 0 && summaries.length === 0) {
    return failureResult(goal, plan, 'invalid_data', 'all_fixed_tracks_unavailable', 'IPv4 与 IPv6 固定 cohort 轨道均不可用；不可将缺失解释为 0。', initialState, bindingVerification)
  }
  if (fixedMetrics.length === 0 && newMetrics.length > 0 && currentValues.length === 0) {
    return failureResult(goal, plan, 'invalid_data', 'all_new_tracks_unavailable', '新前缀轨道不可用；不可将缺失解释为 0。', initialState, bindingVerification)
  }

  const answerability: Answerability = missingFixed.length > 0
    || currentValues.length < newMetrics.length
    ? 'partial'
    : 'supported'
  const ipv4Summary = summaries.find((summary) => summary.metric === FIXED_IPV4_METRIC)
  const ipv6Summary = summaries.find((summary) => summary.metric === FIXED_IPV6_METRIC)
  const comparison: AddressFamilyComparison | null = ipv4Summary !== undefined
    && ipv6Summary !== undefined
    ? {
        operatorId: 'OP-P1-ADDRESS-FAMILY-COMPARE',
        operatorVersion: 'v1',
        ipv4Metric: FIXED_IPV4_METRIC,
        ipv6Metric: FIXED_IPV6_METRIC,
        unitPolicy: 'separate_units_only',
        combinedAbsoluteTotal: 'forbidden',
        evidenceRefs: [
          ...ipv4Summary.evidenceRefs,
          ...ipv6Summary.evidenceRefs,
          'operator:OP-P1-ADDRESS-FAMILY-COMPARE:v1',
        ],
      }
    : null
  const parts = summaries.map((summary) => fixedSummaryText(summary, plan.analysisMode))
  if (plan.includeNewPrefixes) {
    for (const family of selectedFamilies(plan.addressFamily)) {
      const text = newPrefixText(currentValues, family)
      if (text !== null) parts.push(text)
      else parts.push(`${family.toUpperCase()} 新前缀补充轨道不完整，记为 unavailable，不按 0 处理。`)
    }
  }
  if (missingFixed.length > 0) {
    parts.push(`以下固定 cohort 轨道不可用：${missingFixed.join('、')}；回答已降级为 partial。`)
  }
  parts.push(`证据身份：country_outage / ${binding.countryCode} / ${binding.incidentId} / ${binding.publicationId} / revision ${binding.revision} / RRC25；观测窗口 ${binding.windowStartUtc} 至 ${binding.windowEndUtc}，数据截止 ${binding.dataThrough}，事件结束仍未知。`)
  parts.push('限制：这些是 RRC25 控制面可见性事实，不代表用户数、流量、全国中断、原因、责任或恢复。')
  const verifiedMetrics = unique([
    ...summaries.map((summary) => summary.metric),
    ...currentValues.map((value) => value.metric),
  ])
  const evidenceRefs = unique([
    ...summaries.flatMap((summary) => summary.evidenceRefs),
    ...currentValues.flatMap((value) => value.evidenceRefs),
    ...(comparison?.evidenceRefs ?? []),
    ...binding.identityEvidenceRefs,
    ...bindingVerification.evidenceRefs,
  ])
  const evidenceBindings: EvidenceBinding[] = [
    {
      evidenceId: 'resolver:event-binding',
      sourceId: 'country_outage_general_resolution_v1',
      sourceEndpoint: bindingVerification.resolverEndpoint,
      responseSha256: bindingVerification.resolverResponseSha256,
      jsonPointers: [
        '/event_type', '/country_code', '/incident_id', '/publication_id', '/revision',
        '/collector_id', '/window_start_utc', '/window_end_utc', '/data_through',
        '/lifecycle_state', '/capabilities/event_series', '/capabilities/overview',
      ],
    },
    {
      evidenceId: 'overview:event-binding',
      sourceId: 'country_outage_general_overview_v1',
      sourceEndpoint: bindingVerification.overviewEndpoint,
      responseSha256: bindingVerification.overviewResponseSha256,
      jsonPointers: [
        '/incident_id', '/publication_id', '/revision', '/collector_id',
        '/window_start_utc', '/window_end_utc', '/data_through', '/lifecycle_state',
      ],
    },
    ...verifiedMetricsForEvidence(summaries, currentValues).map(({ metric, pointers }) => ({
      evidenceId: `series:${metric}`,
      sourceId: payload.sourceReceipt.sourceId,
      sourceEndpoint: payload.sourceReceipt.endpoint,
      responseSha256: payload.sourceReceipt.responseSha256,
      jsonPointers: pointers,
    })),
  ]
  const operatorReceipts: OperatorExecutionReceipt[] = []
  for (const summary of summaries) {
    operatorReceipts.push({
      receiptId: `operator:extrema:${summary.metric}`,
      nodeId: plan.nodes.find((node) => node.metrics.includes(summary.metric)
        && node.operatorId === 'OP-P1-SERIES-EXTREMA')?.nodeId ?? 'unresolved',
      operatorId: 'OP-P1-SERIES-EXTREMA',
      operatorVersion: 'v1',
      inputEvidenceIds: [`series:${summary.metric}`],
      inputOperatorReceiptIds: [],
      output: structuredClone(summary),
    })
  }
  for (const current of currentValues) {
    operatorReceipts.push({
      receiptId: `operator:current:${current.metric}`,
      nodeId: plan.nodes.find((node) => node.metrics.includes(current.metric)
        && node.operatorId === 'OP-P1-CURRENT-VALUE')?.nodeId ?? 'unresolved',
      operatorId: 'OP-P1-CURRENT-VALUE',
      operatorVersion: 'v1',
      inputEvidenceIds: [`series:${current.metric}`],
      inputOperatorReceiptIds: [],
      output: structuredClone(current),
    })
  }
  if (comparison !== null) {
    operatorReceipts.push({
      receiptId: 'operator:address-family-compare:v1',
      nodeId: plan.nodes.find((node) => node.operatorId === 'OP-P1-ADDRESS-FAMILY-COMPARE')?.nodeId
        ?? 'unresolved',
      operatorId: 'OP-P1-ADDRESS-FAMILY-COMPARE',
      operatorVersion: 'v1',
      inputEvidenceIds: [],
      inputOperatorReceiptIds: [
        `operator:extrema:${FIXED_IPV4_METRIC}`,
        `operator:extrema:${FIXED_IPV6_METRIC}`,
      ],
      output: structuredClone(comparison),
    })
  }
  operatorReceipts.push({
    receiptId: 'operator:metric-definition-gate:v1',
    nodeId: plan.nodes.find((node) => node.operatorId === 'OP-P1-METRIC-DEFINITION-GATE')?.nodeId
      ?? 'unresolved',
    operatorId: 'OP-P1-METRIC-DEFINITION-GATE',
    operatorVersion: 'v1',
    inputEvidenceIds: verifiedMetrics.map((metric) => `series:${metric}`),
    inputOperatorReceiptIds: [],
    output: Object.fromEntries(verifiedMetrics.map((metric) => [
      metric,
      ADDRESS_METRIC_DEFINITIONS[metric],
    ])),
  })
  const metricEvidenceRefs: Partial<Record<AddressMetric, string[]>> = structuredClone(
    initialState.evidence.metricEvidenceRefs,
  )
  for (const summary of summaries) metricEvidenceRefs[summary.metric] = summary.evidenceRefs
  for (const current of currentValues) metricEvidenceRefs[current.metric] = current.evidenceRefs
  const committedVerifiedFamilies = unique(summaries.map((summary) => (
    summary.metric === FIXED_IPV4_METRIC ? 'ipv4' as const : 'ipv6' as const
  )))
  const proposal: PageQaState = {
    dialog: {
      topic: 'address_visibility',
      addressFamily: plan.addressFamily,
      primaryPopulation: plan.includeFixedCohort ? 'fixed_cohort' : null,
      includeNewPrefixes: plan.includeNewPrefixes,
      analysisMode: plan.analysisMode,
      formalHistoricalTrend: false,
    },
    evidence: {
      bindingIdentity: activeBindingIdentity,
      verifiedGoalIds: answerability === 'supported'
        ? unique([...initialState.evidence.verifiedGoalIds, goal.goalId])
        : [...initialState.evidence.verifiedGoalIds],
      verifiedMetrics: unique([...initialState.evidence.verifiedMetrics, ...verifiedMetrics]),
      metricEvidenceRefs,
    },
  }
  return {
    goalId: goal.goalId,
    answerability,
    reasonCode: answerability === 'partial' ? 'one_or_more_tracks_unavailable' : 'verified_series_answer',
    answerText: parts.join('\n'),
    groundingPlan: plan,
    summaries,
    currentValues,
    comparison,
    bindingVerification,
    evidenceRefs,
    evidenceBindings,
    operatorReceipts,
    stateCommit: 'committed',
    stateReceipt: {
      before: structuredClone(initialState),
      proposal: structuredClone(proposal),
      after: structuredClone(proposal),
      commit: 'committed',
      committedVerifiedFamilies,
      rejectedOrMissingMetrics: [...missingFixed, ...missingNew],
    },
    limitations: [
      'IPv4 与 IPv6 /48 等价量保持不同单位，不生成合并总量或严重度。',
      '数据截止和最低点后的变化不等于恢复。',
      '新前缀与固定 cohort 是不同统计人口。',
    ],
  }
}

function verifiedMetricsForEvidence(
  summaries: SeriesSummary[],
  currentValues: CurrentMetricValue[],
): Array<{ metric: AddressMetric; pointers: string[] }> {
  return [
    ...summaries.map((summary) => ({
      metric: summary.metric,
      pointers: [
        `/tracks/${summary.metric}`,
        `/track_definitions/${summary.metric}`,
        '/timestamps',
      ],
    })),
    ...currentValues.map((current) => ({
      metric: current.metric,
      pointers: [
        `/tracks/${current.metric}`,
        `/track_definitions/${current.metric}`,
        '/timestamps',
      ],
    })),
  ]
}

export class InMemoryReadSeriesTool implements ReadSeriesTool {
  readonly toolId = 'TOOL-P1-PAGE-SERIES-READ' as const

  constructor(private readonly payload: SeriesPayload) {}

  async read(_request: ReadSeriesRequest, signal?: AbortSignal): Promise<SeriesPayload> {
    if (signal?.aborted === true) {
      throw new PageSeriesContractError('request_aborted', '请求已取消')
    }
    return structuredClone(this.payload)
  }
}

interface FetchLikeResponse {
  ok: boolean
  status: number
  json(): Promise<unknown>
  text?(): Promise<string>
}

export type PageSeriesFetch = (
  url: string,
  init: { signal?: AbortSignal },
) => Promise<FetchLikeResponse>

function objectValue(value: unknown, field: string): Record<string, unknown> {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) {
    throw new PageSeriesContractError('invalid_api_shape', `${field} 必须是对象`)
  }
  return value as Record<string, unknown>
}

function stringValue(value: unknown, field: string): string {
  if (typeof value !== 'string' || value.length === 0) {
    throw new PageSeriesContractError('invalid_api_shape', `${field} 必须是非空字符串`)
  }
  return value
}

function integerValue(value: unknown, field: string): number {
  if (!Number.isInteger(value) || (value as number) < 0) {
    throw new PageSeriesContractError('invalid_api_shape', `${field} 必须是非负整数`)
  }
  return value as number
}

export class HttpEventBindingVerifier implements EventBindingVerifier {
  readonly toolId = 'TOOL-P1-EVENT-BINDING-VERIFY' as const

  constructor(
    private readonly apiBaseUrl: string,
    private readonly fetcher: PageSeriesFetch = (url, init) => fetch(url, init),
  ) {}

  async verify(
    binding: EventBinding,
    required: AddressCapabilityId[],
    signal?: AbortSignal,
  ): Promise<EventBindingVerificationReceipt> {
    if (requestAborted(signal)) {
      throw new PageSeriesContractError('request_aborted', '请求已取消')
    }
    const url = new URL('/api/v2/events/resolve', this.apiBaseUrl)
    url.searchParams.set('ref', binding.legacyReference)
    const init = signal === undefined ? {} : { signal }
    const response = await this.fetcher(url.toString(), init)
    if (!response.ok) {
      throw new PageSeriesContractError(
        'resolver_http_error',
        `resolver API 返回 HTTP ${response.status}`,
      )
    }
    if (response.text === undefined) {
      throw new PageSeriesContractError('resolver_raw_receipt_missing', 'resolver 未返回可哈希原始回执')
    }
    const rawText = await response.text()
    const resolverResponseSha256 = createHash('sha256').update(rawText).digest('hex')
    let parsed: unknown
    try {
      parsed = JSON.parse(rawText)
    } catch {
      throw new PageSeriesContractError('invalid_api_shape', 'resolver 响应不是合法 JSON')
    }
    const value = objectValue(parsed, 'resolver response')
    if (stringValue(value.schema_version, 'schema_version')
      !== 'country_outage_general_resolution_v1') {
      throw new PageSeriesContractError('schema_mismatch', 'resolver schema 不匹配')
    }
    if (stringValue(value.event_type, 'event_type') !== 'country_outage') {
      throw new PageSeriesContractError('event_type_not_allowed', 'resolver 事件类型不是 country_outage')
    }
    const collectorId = stringValue(value.collector_id, 'collector_id')
    if (collectorId !== 'rrc25') {
      throw new PageSeriesContractError('collector_not_allowed', 'resolver collector 不是 RRC25')
    }
    const lifecycleState = stringValue(value.lifecycle_state, 'lifecycle_state')
    if (lifecycleState !== 'event_end_unknown' && lifecycleState !== 'event_end_known') {
      throw new PageSeriesContractError('invalid_api_shape', 'resolver lifecycle_state 不合法')
    }
    const capabilities = objectValue(value.capabilities, 'capabilities')
    if (capabilities.event_series !== 'available' || capabilities.overview !== 'available') {
      throw new PageSeriesContractError(
        'source_capability_unavailable',
        'resolver 未声明 event_series 与 overview 可用',
      )
    }
    const resolverIdentity: EventBindingVerificationReceipt['resolverIdentity'] = {
      eventType: 'country_outage',
      countryCode: stringValue(value.country_code, 'country_code'),
      incidentId: stringValue(value.incident_id, 'incident_id'),
      publicationId: stringValue(value.publication_id, 'publication_id'),
      revision: integerValue(value.revision, 'revision'),
      collectorId: 'rrc25',
      windowStartUtc: stringValue(value.window_start_utc, 'window_start_utc'),
      windowEndUtc: stringValue(value.window_end_utc, 'window_end_utc'),
      dataThrough: stringValue(value.data_through, 'data_through'),
      lifecycleState,
    }
    assertResolverIdentity(binding, resolverIdentity)
    const overviewUrl = new URL(
      `/api/v2/country-outages/${encodeURIComponent(binding.incidentId)}/overview`,
      this.apiBaseUrl,
    )
    overviewUrl.searchParams.set('publication_id', binding.publicationId)
    overviewUrl.searchParams.set('revision', String(binding.revision))
    const overviewResponse = await this.fetcher(overviewUrl.toString(), init)
    if (!overviewResponse.ok) {
      throw new PageSeriesContractError(
        'overview_http_error',
        `overview API 返回 HTTP ${overviewResponse.status}`,
      )
    }
    if (overviewResponse.text === undefined) {
      throw new PageSeriesContractError('overview_raw_receipt_missing', 'overview 未返回可哈希原始回执')
    }
    const overviewRawText = await overviewResponse.text()
    const overviewResponseSha256 = createHash('sha256').update(overviewRawText).digest('hex')
    let overviewParsed: unknown
    try {
      overviewParsed = JSON.parse(overviewRawText)
    } catch {
      throw new PageSeriesContractError('invalid_api_shape', 'overview 响应不是合法 JSON')
    }
    const overview = objectValue(overviewParsed, 'overview response')
    if (stringValue(overview.schema_version, 'overview.schema_version')
      !== 'country_outage_general_overview_v1') {
      throw new PageSeriesContractError('schema_mismatch', 'overview schema 不匹配')
    }
    const overviewChecks: Array<[string, string | number]> = [
      ['incident_id', binding.incidentId],
      ['publication_id', binding.publicationId],
      ['revision', binding.revision],
      ['collector_id', binding.collectorId],
      ['window_start_utc', binding.windowStartUtc],
      ['window_end_utc', binding.windowEndUtc],
      ['data_through', binding.dataThrough],
      ['lifecycle_state', binding.lifecycleState],
    ]
    for (const [field, expected] of overviewChecks) {
      if (overview[field] !== expected) {
        throw new PageSeriesContractError(
          'binding_overview_conflict',
          `overview identity mismatch: ${field}`,
        )
      }
    }
    const requiredSet = new Set(required)
    const unregistered = [...requiredSet]
      .filter((capabilityId) => !REGISTERED_S1_ADDRESS_CAPABILITIES.includes(capabilityId))
    if (unregistered.length > 0) {
      throw new PageSeriesContractError(
        'capability_not_negotiated',
        `本地合同未登记能力：${unregistered.join(',')}`,
      )
    }
    const receipt: EventBindingVerificationReceipt = {
      toolId: this.toolId,
      verificationMode: 'live_resolver',
      verified: true,
      resolverEndpoint: url.toString(),
      resolverResponseSha256,
      overviewEndpoint: overviewUrl.toString(),
      overviewResponseSha256,
      resolverIdentity,
      sourceCapabilities: { eventSeries: 'available', overview: 'available' },
      negotiatedCapabilityIds: REGISTERED_S1_ADDRESS_CAPABILITIES.filter((capabilityId) => (
        requiredSet.has(capabilityId)
      )),
      evidenceRefs: [
        `resolver:${url.pathname}?ref=${encodeURIComponent(binding.legacyReference)}`,
        `sha256:${resolverResponseSha256}`,
        `overview:${overviewUrl.pathname}?publication_id=${encodeURIComponent(binding.publicationId)}&revision=${binding.revision}`,
        `sha256:${overviewResponseSha256}`,
        'resolver:/capabilities/event_series=available',
        'resolver:/capabilities/overview=available',
      ],
    }
    assertVerificationReceipt(binding, receipt, required)
    return receipt
  }
}

export function mapGeneralSeriesApiResponse(
  raw: unknown,
  request: ReadSeriesRequest,
  sourceReceipt: SeriesSourceReceipt,
): SeriesPayload {
  assertVerificationReceipt(request.binding, request.bindingVerification, [])
  const value = objectValue(raw, 'series response')
  const identityChecks: Array<[string, string | number]> = [
    ['incident_id', request.binding.incidentId],
    ['publication_id', request.binding.publicationId],
    ['revision', request.binding.revision],
    ['collector_id', request.binding.collectorId],
    ['window_start_utc', request.binding.windowStartUtc],
    ['window_end_utc', request.binding.windowEndUtc],
    ['data_through', request.binding.dataThrough],
    ['lifecycle_state', request.binding.lifecycleState],
  ]
  for (const [field, expected] of identityChecks) {
    if (value[field] !== expected) {
      throw new PageSeriesContractError('identity_conflict', `API response identity mismatch: ${field}`)
    }
  }
  if (value.event_type !== undefined && value.event_type !== request.binding.eventType) {
    throw new PageSeriesContractError('identity_conflict', 'API response identity mismatch: event_type')
  }
  if (value.country_code !== undefined && value.country_code !== request.binding.countryCode) {
    throw new PageSeriesContractError('identity_conflict', 'API response identity mismatch: country_code')
  }
  if (stringValue(value.schema_version, 'schema_version') !== 'country_outage_general_series_v1') {
    throw new PageSeriesContractError('schema_mismatch', 'API series schema 不匹配')
  }
  if (!Array.isArray(value.timestamps)) {
    throw new PageSeriesContractError('invalid_api_shape', 'timestamps 必须是数组')
  }
  const timestamps = value.timestamps.map((timestamp, index) => stringValue(timestamp, `timestamps[${index}]`))
  const rawTracks = objectValue(value.tracks, 'tracks')
  const rawDefinitions = objectValue(value.track_definitions, 'track_definitions')
  const tracks: SeriesPayload['tracks'] = {}
  const definitions: SeriesPayload['definitions'] = {}
  for (const metric of request.metrics) {
    const rawTrack = rawTracks[metric]
    if (rawTrack !== undefined) {
      if (!Array.isArray(rawTrack)) {
        throw new PageSeriesContractError('invalid_api_shape', `${metric} 轨道必须是数组`)
      }
      tracks[metric] = rawTrack.map((point, index) => {
        if (point === null) return null
        if (typeof point !== 'number') {
          throw new PageSeriesContractError('invalid_api_shape', `${metric}[${index}] 必须是数值或 null`)
        }
        return point
      })
    }
    const rawDefinition = rawDefinitions[metric]
    if (rawDefinition !== undefined) {
      const definition = objectValue(rawDefinition, `track_definitions.${metric}`)
      definitions[metric] = {
        unit: stringValue(definition.unit, `track_definitions.${metric}.unit`),
        definition: stringValue(definition.definition, `track_definitions.${metric}.definition`),
      }
    }
  }
  const payload: SeriesPayload = {
    schemaVersion: 'country_outage_general_series_v1',
    binding: structuredClone(request.binding),
    timestamps,
    tracks,
    definitions,
    eventCountryIdentitySource: 'verified_event_binding',
    sourceReceipt,
  }
  validateSeriesPayload(payload, request)
  return payload
}

export class HttpReadSeriesTool implements ReadSeriesTool {
  readonly toolId = 'TOOL-P1-PAGE-SERIES-READ' as const

  constructor(
    private readonly apiBaseUrl: string,
    private readonly fetcher: PageSeriesFetch = (url, init) => fetch(url, init),
  ) {}

  async read(request: ReadSeriesRequest, signal?: AbortSignal): Promise<SeriesPayload> {
    if (requestAborted(signal)) {
      throw new PageSeriesContractError('request_aborted', '请求已取消')
    }
    const url = new URL(
      `/api/v2/country-outages/${encodeURIComponent(request.binding.incidentId)}/series`,
      this.apiBaseUrl,
    )
    url.searchParams.set('publication_id', request.binding.publicationId)
    url.searchParams.set('revision', String(request.binding.revision))
    const init = signal === undefined ? {} : { signal }
    const response = await this.fetcher(url.toString(), init)
    if (!response.ok) {
      throw new PageSeriesContractError('series_http_error', `series API 返回 HTTP ${response.status}`)
    }
    if (response.text === undefined) {
      throw new PageSeriesContractError('series_raw_receipt_missing', 'series 未返回可哈希原始回执')
    }
    const rawText = await response.text()
    let raw: unknown
    try {
      raw = JSON.parse(rawText)
    } catch {
      throw new PageSeriesContractError('invalid_api_shape', 'series 响应不是合法 JSON')
    }
    return mapGeneralSeriesApiResponse(raw, request, {
      sourceId: 'country_outage_general_series_v1',
      endpoint: url.toString(),
      responseSha256: createHash('sha256').update(rawText).digest('hex'),
    })
  }
}
