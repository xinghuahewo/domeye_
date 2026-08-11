export type TrendSeriesSemantics = 'stock' | 'cumulative' | 'current_supplement'

export type TrendSemanticRole =
  | 'interruption_stock'
  | 'visibility_stock'
  | 'cumulative_new'
  | 'current_supplement'

export type RegisteredTrendMetric =
  | 'interrupted_prefix_count'
  | 'completely_interrupted_prefix_count'
  | 'invisible_direction_count'
  | 'affected_asn_count'
  | 'route_interrupted_asn_count'
  | 'fixed_visible_ipv4_address_count'
  | 'fixed_visible_ipv6_slash48_count'
  | 'new_cumulative_ipv4_prefix_count'
  | 'new_cumulative_ipv4_address_count'
  | 'new_cumulative_ipv6_prefix_count'
  | 'new_cumulative_ipv6_slash48_count'
  | 'new_visible_ipv4_prefix_count'
  | 'new_visible_ipv4_address_count'
  | 'new_visible_ipv6_prefix_count'
  | 'new_visible_ipv6_slash48_count'

export interface EventWindowTrendSourceIdentity {
  source_schema_version: string
  event_type: 'country_outage'
  incident_id: string
  publication_id: string
  publication_state: 'published'
  revision: number
  collector_id: 'rrc25'
  cohort_id: string
  window_start_utc: string
  window_end_utc: string
  data_through: string | null
  is_final_in_data_range: boolean
  lifecycle_state: string
  observation_state: string
  quality_state: string
  missing_slot_count: number
}

export interface TrendProfileParameters {
  baseline_observed_points: number
  median_filter_radius: number
  change_threshold_absolute: number
  change_threshold_relative_to_baseline: number
  change_threshold_relative_to_observed_range: number
  gap_interval_multiplier: number
  tail_observed_points: number
  minimum_observed_points: number
  cumulative_decrease_tolerance: number
  isolated_spike_threshold_multiplier: number
  isolated_spike_return_tolerance_multiplier: number
  display_max_phases_per_run: number
  display_max_facts: number
}

export interface RegisteredTrendProfile {
  schema_version: 'country_outage_p1_trend_profile_v1'
  profile_id: string
  profile_version: 3
  base_profile_id: 'stock-default-v1' | 'cumulative-default-v1'
  metric: RegisteredTrendMetric
  unit: string
  series_semantics: TrendSeriesSemantics
  semantic_role: TrendSemanticRole
  primary_fact: 'minimum' | 'maximum' | 'last'
  display_label_zh: string
  unit_label_zh: string
  parameters: TrendProfileParameters
}

export interface TrendSourceEvidenceRefs {
  identity: string[]
  timestamps: string
  values: string
  metric_definition: string
  trend_profile: string
}

export interface EventWindowTrendInput {
  source_identity: EventWindowTrendSourceIdentity
  metric: RegisteredTrendMetric
  unit: string
  series_semantics: TrendSeriesSemantics
  timestamps: string[]
  values: Array<number | null>
  source_evidence_refs: TrendSourceEvidenceRefs
  trend_profile: RegisteredTrendProfile
}

export type TrendGlobalShape =
  | 'insufficient_data'
  | 'stable'
  | 'monotonic_increase'
  | 'monotonic_decrease'
  | 'decrease_then_partial_rebound'
  | 'decrease_then_return_to_baseline'
  | 'decrease_then_above_baseline'
  | 'rise_then_partial_decline'
  | 'rise_then_return_to_baseline'
  | 'rise_then_below_baseline'
  | 'multi_phase'
  | 'cumulative_flat'
  | 'cumulative_growth'

export type TrendDirection = 'increase' | 'decrease' | 'stable'

export interface LineagedValue<T> {
  value: T
  fact_id: string
}

export interface TrendPointFact {
  index: number
  at_utc: string
  value: number
  analysis_value: number
  fact_id: string
}

export interface TrendMovementSegment {
  from: TrendPointFact
  to: TrendPointFact
  change: number
  duration_ms: number
  fact_id: string
}

export interface TrendPhase extends TrendMovementSegment {
  phase_id: string
  direction: TrendDirection
  source_run: number
}

export interface TrendTurningPoint extends TrendPointFact {
  turning_point_id: string
  kind: 'local_minimum' | 'local_maximum' | 'level_shift' | 'plateau_start'
}

export interface TrendAdjacentStep extends TrendMovementSegment {
  adjacent: true
}

export interface TrendIsolatedSpike {
  spike_id: string
  direction: 'up' | 'down'
  before: TrendPointFact
  center: TrendPointFact
  after: TrendPointFact
  excursion_from_neighbor_median: number
  neighbor_return_delta: number
  threshold_ratio: number
  fact_id: string
}

export interface TrendDisplayPhase {
  display_phase_id: string
  source_run: number
  from: TrendPointFact
  to: TrendPointFact
  direction: TrendDirection | 'mixed'
  shape: 'stable' | 'gradual' | 'abrupt' | 'volatile' | 'stepwise'
  change: number
  duration_ms: number
  minimum: number
  maximum: number
  audit_phase_ids: string[]
  fact_id: string
}

export interface TrendSignificantFact {
  rank: number
  fact_type:
    | 'data_quality'
    | 'last'
    | 'minimum'
    | 'maximum'
    | 'net_change'
    | 'largest_adjacent_step_down'
    | 'largest_adjacent_step_up'
    | 'maximum_decline_segment'
    | 'maximum_numeric_rise_segment'
    | 'isolated_spike'
    | 'cumulative_plateau_start'
  salience_score: number
  fact_id: string
  point_indexes: number[]
}

export interface TrendGap {
  gap_id: string
  kind: 'null_run' | 'implicit_time_gap'
  from_index: number
  to_index: number
  start_at_utc: string
  end_at_utc: string
  slot_count: number
  duration_ms: number
  fact_id: string
}

export interface TrendFactLineage {
  fact_id: string
  claim_path: string
  source_point_indexes: number[]
  evidence_refs: string[]
  derived_by: {
    operator_id: 'event-window-trend'
    operator_version: '1.2.0'
    profile_id: string
  }
}

export type TrendCompactCardType =
  | 'first'
  | 'last'
  | 'minimum'
  | 'maximum'
  | 'net_change'
  | 'largest_adjacent_step_up'
  | 'isolated_spike'
  | 'tail_null'

export interface TrendCompactCard {
  card_id: string
  fact_type: TrendCompactCardType
  label_zh: string
  text_zh: string
  value: number
  unit: string
  unit_label_zh: string
  at_utc: string | null
  fact_ids: string[]
  evidence_refs: string[]
}

export interface TrendCompactLimitation {
  limitation_id: string
  text_zh: string
  fact_ids: string[]
  evidence_refs: string[]
}

export interface EventWindowTrendCompactOutput {
  schema_version: 'country_outage_p1_event_window_trend_compact_chat_v1'
  operator: {
    operator_id: 'event-window-trend-compact'
    operator_version: '1.2.0'
    deterministic: true
    model_dependency: 'none'
  }
  source_identity: EventWindowTrendSourceIdentity
  display_label_zh: string
  unit: string
  unit_label_zh: string
  series_semantics: TrendSeriesSemantics
  semantic_role: TrendSemanticRole
  headline_zh: string
  body_zh: string
  sentence_count: number
  character_count: number
  cards: TrendCompactCard[]
  limitations: TrendCompactLimitation[]
  fact_ids: string[]
  evidence_refs: string[]
}

export interface CompactTrendBundleOutput {
  schema_version: 'country_outage_p1_compact_trend_bundle_v1'
  bundle_profile_id: 'fixed-ip-address-change-v1'
  operator: {
    operator_id: 'event-window-trend-compact-bundle'
    operator_version: '1.2.0'
    deterministic: true
    model_dependency: 'none'
  }
  source_identity: EventWindowTrendSourceIdentity
  title_zh: 'IP 地址变化情况'
  body_zh: string
  character_count: number
  tracks: [EventWindowTrendCompactOutput, EventWindowTrendCompactOutput]
  limitations: TrendCompactLimitation[]
  unit_separation: {
    ipv4_unit: 'unique_ipv4_address'
    ipv6_unit: 'ipv6_slash48_equivalent'
    cross_unit_aggregation: 'forbidden'
  }
  fact_ids: string[]
  evidence_refs: string[]
}

export interface EventWindowTrendResult {
  schema_version: 'country_outage_p1_event_window_trend_result_v3'
  operator: {
    operator_id: 'event-window-trend'
    operator_version: '1.2.0'
    deterministic: true
    model_dependency: 'none'
  }
  source_identity: EventWindowTrendSourceIdentity
  metric: RegisteredTrendMetric
  unit: string
  series_semantics: TrendSeriesSemantics
  trend_profile: RegisteredTrendProfile
  data_quality: {
    status: LineagedValue<'complete' | 'usable_with_caveats' | 'insufficient'>
    total_point_count: number
    observed_point_count: number
    null_point_count: number
    coverage_ratio: number
    nominal_interval_ms: number | null
    irregular_interval: boolean
    warnings: string[]
    fact_id: string
  }
  summary: {
    first: TrendPointFact
    last: TrendPointFact
    minimum: TrendPointFact
    maximum: TrendPointFact
    net_change: LineagedValue<number>
    baseline: LineagedValue<number>
    baseline_difference_at_tail: LineagedValue<number>
    change_threshold: LineagedValue<number>
    global_shape: LineagedValue<TrendGlobalShape>
  }
  phase_sequence: TrendPhase[]
  turning_points: TrendTurningPoint[]
  display_phase_sequence: TrendDisplayPhase[]
  display_turning_points: TrendTurningPoint[]
  largest_adjacent_step_down: TrendAdjacentStep | null
  largest_adjacent_step_up: TrendAdjacentStep | null
  isolated_spikes: TrendIsolatedSpike[]
  maximum_decline_segment: TrendMovementSegment | null
  maximum_rebound_segment: TrendMovementSegment | null
  significant_facts: TrendSignificantFact[]
  duration: {
    window_duration_ms: number
    observed_span_ms: number
    connected_observation_duration_ms: number
    increase_duration_ms: number
    decrease_duration_ms: number
    stable_duration_ms: number
    below_baseline_duration_ms: number | null
    fact_id: string
  }
  null_and_gaps: {
    null_point_count: number
    trailing_null_point_count: number
    gap_count: number
    longest_gap_duration_ms: number
    gaps: TrendGap[]
    interpolation: 'forbidden'
    null_as_zero: 'forbidden'
    fact_id: string
  }
  tail_state: {
    observation: 'at_data_through' | 'before_data_through' | 'data_through_unknown'
    baseline_relation: 'above' | 'below' | 'near'
    recent_direction: TrendDirection | 'unavailable'
    last_observed_at_utc: string
    trailing_null_point_count: number
    event_state_inference: 'forbidden'
    fact_id: string
  }
  deterministic_description_zh: {
    text: string
    fact_ids: string[]
  }
  compact_chat_output: EventWindowTrendCompactOutput
  fact_lineage: TrendFactLineage[]
}

export type EventWindowTrendErrorCode =
  | 'invalid_identity'
  | 'unknown_metric'
  | 'unit_mismatch'
  | 'series_semantics_mismatch'
  | 'unregistered_trend_profile'
  | 'invalid_series_shape'
  | 'invalid_timestamp'
  | 'invalid_metric_value'
  | 'empty_observed_set'
  | 'cumulative_series_decreased'
  | 'missing_evidence_ref'
  | 'cross_track_identity_conflict'

export interface MultiTrackTrendEvidencePoint {
  metric: RegisteredTrendMetric
  unit: string
  semantic_role: TrendSemanticRole
  index: number
  at_utc: string
  before_value: number
  after_value: number
  delta: number
  normalized_magnitude: number
  source_fact_ids: string[]
  evidence_refs: string[]
}

export interface MultiTrackTrendFact {
  fact_id: string
  kind:
    | 'same_interval_change'
    | 'synchronized_isolated_spike'
    | 'cumulative_current_divergence'
  relation:
    | 'same_numeric_direction'
    | 'opposing_numeric_direction'
    | 'not_applicable'
  from_index: number
  to_index: number
  from_at_utc: string
  to_at_utc: string
  salience_score: number
  metrics: RegisteredTrendMetric[]
  evidence_points: MultiTrackTrendEvidencePoint[]
  claim_zh: string
}

export interface MultiTrackTrendResult {
  schema_version: 'country_outage_p1_multi_track_trend_result_v1'
  operator: {
    operator_id: 'event-window-trend'
    operator_version: '1.2.0'
    deterministic: true
    model_dependency: 'none'
  }
  source_identity: EventWindowTrendSourceIdentity
  track_count: number
  audit_facts: MultiTrackTrendFact[]
  display_facts: MultiTrackTrendFact[]
  display_limit: 12
  comparison_rules: {
    same_identity_and_timeline_required: true
    cross_unit_aggregation: 'forbidden'
    normalized_magnitude_use: 'ranking_only'
    causal_inference: 'forbidden'
  }
}

export class EventWindowTrendError extends Error {
  constructor(
    readonly code: EventWindowTrendErrorCode,
    message: string,
  ) {
    super(message)
    this.name = 'EventWindowTrendError'
  }
}

const STOCK_DEFAULT: TrendProfileParameters = {
  baseline_observed_points: 3,
  median_filter_radius: 1,
  change_threshold_absolute: 1,
  change_threshold_relative_to_baseline: 0.001,
  change_threshold_relative_to_observed_range: 0.03,
  gap_interval_multiplier: 3,
  tail_observed_points: 3,
  minimum_observed_points: 3,
  cumulative_decrease_tolerance: 0,
  isolated_spike_threshold_multiplier: 1,
  isolated_spike_return_tolerance_multiplier: 1,
  display_max_phases_per_run: 6,
  display_max_facts: 8,
}

const CUMULATIVE_DEFAULT: TrendProfileParameters = {
  baseline_observed_points: 1,
  median_filter_radius: 0,
  change_threshold_absolute: 1,
  change_threshold_relative_to_baseline: 0,
  change_threshold_relative_to_observed_range: 0,
  gap_interval_multiplier: 3,
  tail_observed_points: 3,
  minimum_observed_points: 2,
  cumulative_decrease_tolerance: 0,
  isolated_spike_threshold_multiplier: 1,
  isolated_spike_return_tolerance_multiplier: 1,
  display_max_phases_per_run: 6,
  display_max_facts: 8,
}

export const TREND_PROFILE_DEFAULTS = Object.freeze({
  'stock-default-v1': Object.freeze({ ...STOCK_DEFAULT }),
  'cumulative-default-v1': Object.freeze({ ...CUMULATIVE_DEFAULT }),
})

const METRIC_PRESENTATION: Readonly<Record<RegisteredTrendMetric, {
  display_label_zh: string
  unit_label_zh: string
}>> = Object.freeze({
  interrupted_prefix_count: {
    display_label_zh: '前缀路由中断', unit_label_zh: '个前缀',
  },
  completely_interrupted_prefix_count: {
    display_label_zh: '完全中断前缀', unit_label_zh: '个前缀',
  },
  invisible_direction_count: {
    display_label_zh: '不可见独立观察方向', unit_label_zh: '个 peer-ASN 观察方向',
  },
  affected_asn_count: {
    display_label_zh: '受影响 AS', unit_label_zh: '个 AS',
  },
  route_interrupted_asn_count: {
    display_label_zh: 'AS 路由中断', unit_label_zh: '个 AS',
  },
  fixed_visible_ipv4_address_count: {
    display_label_zh: '固定前缀可见 IPv4 地址量', unit_label_zh: '个唯一 IPv4 地址',
  },
  fixed_visible_ipv6_slash48_count: {
    display_label_zh: '固定前缀可见 IPv6 /48 等价量', unit_label_zh: '个 IPv6 /48 等价量',
  },
  new_cumulative_ipv4_prefix_count: {
    display_label_zh: '累计出现新 IPv4 前缀', unit_label_zh: '个前缀',
  },
  new_cumulative_ipv4_address_count: {
    display_label_zh: '累计出现新 IPv4 地址量', unit_label_zh: '个唯一 IPv4 地址',
  },
  new_cumulative_ipv6_prefix_count: {
    display_label_zh: '累计出现新 IPv6 前缀', unit_label_zh: '个前缀',
  },
  new_cumulative_ipv6_slash48_count: {
    display_label_zh: '累计出现新 IPv6 /48 等价量', unit_label_zh: '个 IPv6 /48 等价量',
  },
  new_visible_ipv4_prefix_count: {
    display_label_zh: '当前可见新 IPv4 前缀', unit_label_zh: '个前缀',
  },
  new_visible_ipv4_address_count: {
    display_label_zh: '当前可见新 IPv4 地址量', unit_label_zh: '个唯一 IPv4 地址',
  },
  new_visible_ipv6_prefix_count: {
    display_label_zh: '当前可见新 IPv6 前缀', unit_label_zh: '个前缀',
  },
  new_visible_ipv6_slash48_count: {
    display_label_zh: '当前可见新 IPv6 /48 等价量', unit_label_zh: '个 IPv6 /48 等价量',
  },
})

function profile(
  metric: RegisteredTrendMetric,
  unit: string,
  seriesSemantics: TrendSeriesSemantics,
  semanticRole: TrendSemanticRole,
  primaryFact: RegisteredTrendProfile['primary_fact'],
  overrides: Partial<TrendProfileParameters> = {},
): RegisteredTrendProfile {
  const base = seriesSemantics === 'cumulative' ? CUMULATIVE_DEFAULT : STOCK_DEFAULT
  const presentation = METRIC_PRESENTATION[metric]
  return {
    schema_version: 'country_outage_p1_trend_profile_v1',
    profile_id: `country-outage-p1-trend-profile-v1:${metric}`,
    profile_version: 3,
    base_profile_id: seriesSemantics === 'cumulative'
      ? 'cumulative-default-v1' : 'stock-default-v1',
    metric,
    unit,
    series_semantics: seriesSemantics,
    semantic_role: semanticRole,
    primary_fact: primaryFact,
    display_label_zh: presentation.display_label_zh,
    unit_label_zh: presentation.unit_label_zh,
    parameters: { ...base, ...overrides },
  }
}

const PROFILE_LIST: RegisteredTrendProfile[] = [
  profile('interrupted_prefix_count', 'prefix', 'stock', 'interruption_stock', 'maximum', {
    change_threshold_absolute: 5,
    change_threshold_relative_to_baseline: 0.01,
  }),
  profile('completely_interrupted_prefix_count', 'prefix', 'stock', 'interruption_stock', 'maximum', {
    change_threshold_absolute: 5,
    change_threshold_relative_to_baseline: 0.01,
  }),
  profile('invisible_direction_count', 'peer_asn_direction', 'stock', 'interruption_stock', 'maximum', {
    change_threshold_absolute: 20,
    change_threshold_relative_to_baseline: 0.005,
  }),
  profile('affected_asn_count', 'asn', 'stock', 'interruption_stock', 'maximum', {
    change_threshold_absolute: 1,
    change_threshold_relative_to_baseline: 0.02,
  }),
  profile('route_interrupted_asn_count', 'asn', 'stock', 'interruption_stock', 'maximum', {
    change_threshold_absolute: 1,
    change_threshold_relative_to_baseline: 0.02,
  }),
  profile('fixed_visible_ipv4_address_count', 'unique_ipv4_address', 'stock', 'visibility_stock', 'minimum'),
  profile('fixed_visible_ipv6_slash48_count', 'ipv6_slash48_equivalent', 'stock', 'visibility_stock', 'minimum', {
    change_threshold_relative_to_baseline: 0.000003,
  }),
  profile('new_cumulative_ipv4_prefix_count', 'prefix', 'cumulative', 'cumulative_new', 'last'),
  profile('new_cumulative_ipv4_address_count', 'unique_ipv4_address', 'cumulative', 'cumulative_new', 'last'),
  profile('new_cumulative_ipv6_prefix_count', 'prefix', 'cumulative', 'cumulative_new', 'last'),
  profile('new_cumulative_ipv6_slash48_count', 'ipv6_slash48_equivalent', 'cumulative', 'cumulative_new', 'last'),
  profile('new_visible_ipv4_prefix_count', 'prefix', 'current_supplement', 'current_supplement', 'maximum'),
  profile('new_visible_ipv4_address_count', 'unique_ipv4_address', 'current_supplement', 'current_supplement', 'maximum'),
  profile('new_visible_ipv6_prefix_count', 'prefix', 'current_supplement', 'current_supplement', 'maximum'),
  profile('new_visible_ipv6_slash48_count', 'ipv6_slash48_equivalent', 'current_supplement', 'current_supplement', 'maximum'),
]

export const REGISTERED_TREND_PROFILES: Readonly<
  Record<RegisteredTrendMetric, RegisteredTrendProfile>
> = Object.freeze(Object.fromEntries(
  PROFILE_LIST.map((item) => [item.metric, Object.freeze({
    ...item,
    parameters: Object.freeze({ ...item.parameters }),
  })]),
) as Record<RegisteredTrendMetric, RegisteredTrendProfile>)

export function getRegisteredTrendProfile(
  metric: RegisteredTrendMetric,
): RegisteredTrendProfile {
  const value = REGISTERED_TREND_PROFILES[metric]
  if (value === undefined) {
    throw new EventWindowTrendError('unknown_metric', `未登记指标 ${metric}`)
  }
  return structuredClone(value)
}

interface AnalysisPoint {
  index: number
  atUtc: string
  epochMs: number
  rawValue: number
  analysisValue: number
}

interface Run {
  id: number
  points: AnalysisPoint[]
}

function median(values: number[]): number {
  const ordered = [...values].sort((left, right) => left - right)
  const middle = Math.floor(ordered.length / 2)
  return ordered.length % 2 === 1
    ? ordered[middle]!
    : (ordered[middle - 1]! + ordered[middle]!) / 2
}

function cloneProfile(value: RegisteredTrendProfile): RegisteredTrendProfile {
  return structuredClone(value)
}

function sameJson(left: unknown, right: unknown): boolean {
  const canonicalize = (value: unknown): unknown => {
    if (Array.isArray(value)) return value.map(canonicalize)
    if (value !== null && typeof value === 'object') {
      return Object.fromEntries(Object.entries(value as Record<string, unknown>)
        .sort(([leftKey], [rightKey]) => leftKey < rightKey
          ? -1 : leftKey > rightKey ? 1 : 0)
        .map(([key, item]) => [key, canonicalize(item)]))
    }
    return value
  }
  return JSON.stringify(canonicalize(left)) === JSON.stringify(canonicalize(right))
}

function assertNonEmptyString(value: unknown, label: string): asserts value is string {
  if (typeof value !== 'string' || value.length === 0) {
    throw new EventWindowTrendError('invalid_identity', `${label} 缺失`)
  }
}

function parseTimestamp(value: string, code: EventWindowTrendErrorCode): number {
  const epoch = Date.parse(value)
  if (!Number.isFinite(epoch)
    || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,3})?Z$/.test(value)) {
    throw new EventWindowTrendError(code, `时间戳不是规范 UTC date-time：${value}`)
  }
  const fraction = value.match(/\.(\d{1,3})Z$/)?.[1]
  const normalized = fraction === undefined
    ? value.replace(/Z$/, '.000Z')
    : value.replace(/\.\d{1,3}Z$/, `.${fraction.padEnd(3, '0')}Z`)
  if (new Date(epoch).toISOString() !== normalized) {
    throw new EventWindowTrendError(code, `时间戳不是有效 UTC date-time：${value}`)
  }
  return epoch
}

function validateIdentity(identity: EventWindowTrendSourceIdentity): void {
  if (identity.event_type !== 'country_outage' || identity.collector_id !== 'rrc25') {
    throw new EventWindowTrendError('invalid_identity', '只接受 country_outage / rrc25 身份')
  }
  for (const [label, value] of [
    ['source_schema_version', identity.source_schema_version],
    ['incident_id', identity.incident_id],
    ['publication_id', identity.publication_id],
    ['cohort_id', identity.cohort_id],
    ['lifecycle_state', identity.lifecycle_state],
    ['observation_state', identity.observation_state],
    ['quality_state', identity.quality_state],
  ] as const) assertNonEmptyString(value, label)
  if (identity.publication_state !== 'published'
    || !Number.isSafeInteger(identity.revision) || identity.revision < 1
    || !Number.isSafeInteger(identity.missing_slot_count)
    || identity.missing_slot_count < 0) {
    throw new EventWindowTrendError('invalid_identity', 'publication/revision/质量身份非法')
  }
  const start = parseTimestamp(identity.window_start_utc, 'invalid_identity')
  const end = parseTimestamp(identity.window_end_utc, 'invalid_identity')
  if (end < start) {
    throw new EventWindowTrendError('invalid_identity', '事件窗口结束早于开始')
  }
  if (identity.data_through !== null) {
    const through = parseTimestamp(identity.data_through, 'invalid_identity')
    if (through < start || through > end) {
      throw new EventWindowTrendError('invalid_identity', 'data_through 不在事件窗口内')
    }
  }
}

function validateEvidence(refs: TrendSourceEvidenceRefs): void {
  const values = [
    ...refs.identity,
    refs.timestamps,
    refs.values,
    refs.metric_definition,
    refs.trend_profile,
  ]
  if (refs.identity.length === 0
    || values.some((value) => typeof value !== 'string' || value.length === 0)
    || new Set(values).size !== values.length) {
    throw new EventWindowTrendError(
      'missing_evidence_ref',
      '身份、时间、数值、定义和 Profile 必须具有非空且互异的 evidence ref',
    )
  }
}

function validateAndParse(input: EventWindowTrendInput): number[] {
  validateIdentity(input.source_identity)
  const registered = REGISTERED_TREND_PROFILES[input.metric]
  if (registered === undefined) {
    throw new EventWindowTrendError('unknown_metric', `未登记指标 ${input.metric}`)
  }
  if (input.unit !== registered.unit) {
    throw new EventWindowTrendError(
      'unit_mismatch', `${input.metric} 只允许单位 ${registered.unit}`,
    )
  }
  if (input.series_semantics !== registered.series_semantics) {
    throw new EventWindowTrendError(
      'series_semantics_mismatch',
      `${input.metric} 只允许 ${registered.series_semantics} 语义`,
    )
  }
  if (!sameJson(input.trend_profile, registered)) {
    throw new EventWindowTrendError(
      'unregistered_trend_profile', '输入 Profile 与宿主登记值不一致',
    )
  }
  validateEvidence(input.source_evidence_refs)
  if (input.timestamps.length === 0
    || input.timestamps.length !== input.values.length) {
    throw new EventWindowTrendError(
      'invalid_series_shape', 'timestamps 与 values 必须同长且非空',
    )
  }
  const start = Date.parse(input.source_identity.window_start_utc)
  const end = Date.parse(input.source_identity.window_end_utc)
  const epochs: number[] = []
  input.timestamps.forEach((timestamp, index) => {
    const epoch = parseTimestamp(timestamp, 'invalid_timestamp')
    if (epoch < start || epoch > end || (index > 0 && epoch <= epochs[index - 1]!)) {
      throw new EventWindowTrendError(
        'invalid_timestamp', 'timestamps 必须严格递增且位于事件窗口内',
      )
    }
    epochs.push(epoch)
  })
  let observedCount = 0
  let previousCumulative: number | null = null
  for (const value of input.values) {
    if (value === null) continue
    if (!Number.isFinite(value) || !Number.isSafeInteger(value) || value < 0) {
      throw new EventWindowTrendError(
        'invalid_metric_value', '轨道只接受非负有限安全整数或 null',
      )
    }
    observedCount += 1
    if (input.series_semantics === 'cumulative') {
      if (previousCumulative !== null
        && value + registered.parameters.cumulative_decrease_tolerance
          < previousCumulative) {
        throw new EventWindowTrendError(
          'cumulative_series_decreased', '累计轨道出现下降，不能套用 stock 语义',
        )
      }
      previousCumulative = value
    }
  }
  if (observedCount === 0) {
    throw new EventWindowTrendError('empty_observed_set', '轨道没有非 null 观测')
  }
  return epochs
}

function medianInterval(epochs: number[]): number | null {
  if (epochs.length < 2) return null
  return median(epochs.slice(1).map((value, index) => value - epochs[index]!))
}

function buildRuns(
  input: EventWindowTrendInput,
  epochs: number[],
  nominalInterval: number | null,
): Run[] {
  const runs: Run[] = []
  let current: AnalysisPoint[] = []
  const gapLimit = nominalInterval === null
    ? Number.POSITIVE_INFINITY
    : nominalInterval * input.trend_profile.parameters.gap_interval_multiplier
  for (let index = 0; index < input.values.length; index += 1) {
    const value = input.values[index]!
    if (value === null) {
      if (current.length > 0) {
        runs.push({ id: runs.length, points: current })
        current = []
      }
      continue
    }
    if (current.length > 0
      && epochs[index]! - current[current.length - 1]!.epochMs > gapLimit) {
      runs.push({ id: runs.length, points: current })
      current = []
    }
    current.push({
      index,
      atUtc: input.timestamps[index]!,
      epochMs: epochs[index]!,
      rawValue: value,
      analysisValue: value,
    })
  }
  if (current.length > 0) runs.push({ id: runs.length, points: current })

  const radius = input.trend_profile.parameters.median_filter_radius
  for (const run of runs) {
    const smoothed = run.points.map((point, index) => {
      if (radius === 0 || index < radius || index + radius >= run.points.length) {
        return point.rawValue
      }
      return median(run.points
        .slice(index - radius, index + radius + 1)
        .map((item) => item.rawValue))
    })
    run.points.forEach((point, index) => {
      point.analysisValue = smoothed[index]!
    })
  }
  return runs
}

function pointEvidence(input: EventWindowTrendInput, indexes: number[]): string[] {
  const atIndex = (reference: string, index: number) =>
    `${reference}${reference.includes('#') ? '/' : '#/'}${index}`
  return indexes.flatMap((index) => [
    atIndex(input.source_evidence_refs.timestamps, index),
    atIndex(input.source_evidence_refs.values, index),
  ])
}

function direction(change: number, threshold: number): TrendDirection {
  if (change >= threshold) return 'increase'
  if (change <= -threshold) return 'decrease'
  return 'stable'
}

function compactDirections(phases: TrendPhase[]): TrendDirection[] {
  const result: TrendDirection[] = []
  for (const phase of phases) {
    if (phase.direction === 'stable') continue
    if (result[result.length - 1] !== phase.direction) result.push(phase.direction)
  }
  return result
}

function phasePivots(points: AnalysisPoint[], threshold: number): AnalysisPoint[] {
  if (points.length <= 1) return [...points]
  const pivots: AnalysisPoint[] = [points[0]!]
  let anchor = points[0]!
  let extreme = anchor
  let trend: -1 | 0 | 1 = 0
  for (const point of points.slice(1)) {
    if (trend === 0) {
      if (point.analysisValue - anchor.analysisValue >= threshold) {
        trend = 1
        extreme = point
      } else if (anchor.analysisValue - point.analysisValue >= threshold) {
        trend = -1
        extreme = point
      }
      continue
    }
    if (trend === 1) {
      if (point.analysisValue > extreme.analysisValue) {
        extreme = point
      } else if (extreme.analysisValue - point.analysisValue >= threshold) {
        if (pivots[pivots.length - 1]!.index !== extreme.index) pivots.push(extreme)
        anchor = extreme
        extreme = point
        trend = -1
      }
    } else if (point.analysisValue < extreme.analysisValue) {
      extreme = point
    } else if (point.analysisValue - extreme.analysisValue >= threshold) {
      if (pivots[pivots.length - 1]!.index !== extreme.index) pivots.push(extreme)
      anchor = extreme
      extreme = point
      trend = 1
    }
  }
  if (trend !== 0 && pivots[pivots.length - 1]!.index !== extreme.index) {
    pivots.push(extreme)
  }
  const last = points[points.length - 1]!
  if (pivots[pivots.length - 1]!.index !== last.index) pivots.push(last)
  return pivots
}

function classifyShape(
  semantics: TrendSeriesSemantics,
  observedCount: number,
  minimumObserved: number,
  phases: TrendPhase[],
  baseline: number,
  lastValue: number,
  threshold: number,
): TrendGlobalShape {
  if (observedCount < minimumObserved) return 'insufficient_data'
  if (semantics === 'cumulative') {
    return lastValue - baseline >= threshold ? 'cumulative_growth' : 'cumulative_flat'
  }
  const directions = compactDirections(phases)
  if (directions.length === 0) return 'stable'
  if (directions.length === 1) {
    return directions[0] === 'increase'
      ? 'monotonic_increase' : 'monotonic_decrease'
  }
  if (directions.length === 2 && directions[0] === 'decrease') {
    if (Math.abs(lastValue - baseline) < threshold) {
      return 'decrease_then_return_to_baseline'
    }
    return lastValue < baseline
      ? 'decrease_then_partial_rebound' : 'decrease_then_above_baseline'
  }
  if (directions.length === 2 && directions[0] === 'increase') {
    if (Math.abs(lastValue - baseline) < threshold) {
      return 'rise_then_return_to_baseline'
    }
    return lastValue > baseline
      ? 'rise_then_partial_decline' : 'rise_then_below_baseline'
  }
  return 'multi_phase'
}

function describeShape(shape: TrendGlobalShape): string {
  const labels: Record<TrendGlobalShape, string> = {
    insufficient_data: '有效点不足，只报告可核对的点值，不判定全局形态',
    stable: '在登记阈值内持平',
    monotonic_increase: '整体单调上升',
    monotonic_decrease: '整体单调下降',
    decrease_then_partial_rebound: '下降后数值部分回升，但末值仍低于基线',
    decrease_then_return_to_baseline: '下降后末值回到基线阈值带',
    decrease_then_above_baseline: '下降后数值上升至基线以上',
    rise_then_partial_decline: '上升后数值部分回落，但末值仍高于基线',
    rise_then_return_to_baseline: '上升后末值回到基线阈值带',
    rise_then_below_baseline: '上升后数值下降至基线以下',
    multi_phase: '存在多阶段上升与下降',
    cumulative_flat: '累计值在登记阈值内未增加',
    cumulative_growth: '累计值非递减增长',
  }
  return labels[shape]
}

function numericText(value: number): string {
  return Number.isInteger(value) ? String(value) : String(Number(value.toFixed(6)))
}

function compactNumber(value: number): string {
  const sign = value < 0 ? '-' : ''
  const absolute = Math.abs(value)
  if (!Number.isInteger(absolute)) return `${sign}${numericText(absolute)}`
  return `${sign}${String(absolute).replace(/\B(?=(\d{3})+(?!\d))/g, ',')}`
}

function compactChange(value: number, unitLabel: string): string {
  if (value > 0) return `较首值增加 ${compactNumber(value)}${unitLabel}`
  if (value < 0) return `较首值减少 ${compactNumber(Math.abs(value))}${unitLabel}`
  return '与首值相同'
}

export function analyzeEventWindowTrend(
  input: EventWindowTrendInput,
): EventWindowTrendResult {
  const epochs = validateAndParse(input)
  const observed = input.values
    .map((value, index) => value === null ? null : ({
      index,
      atUtc: input.timestamps[index]!,
      epochMs: epochs[index]!,
      rawValue: value,
      analysisValue: value,
    }))
    .filter((point): point is AnalysisPoint => point !== null)
  const intervals = epochs.slice(1).map((value, index) => value - epochs[index]!)
  const nominalInterval = medianInterval(epochs)
  const runs = buildRuns(input, epochs, nominalInterval)
  const baselinePoints = observed.slice(
    0, input.trend_profile.parameters.baseline_observed_points,
  )
  const baseline = median(baselinePoints.map((point) => point.rawValue))
  let observedMinimum = observed[0]!.rawValue
  let observedMaximum = observed[0]!.rawValue
  for (const point of observed.slice(1)) {
    observedMinimum = Math.min(observedMinimum, point.rawValue)
    observedMaximum = Math.max(observedMaximum, point.rawValue)
  }
  const threshold = Number(Math.max(
    input.trend_profile.parameters.change_threshold_absolute,
    Math.abs(baseline)
      * input.trend_profile.parameters.change_threshold_relative_to_baseline,
    (observedMaximum - observedMinimum)
      * input.trend_profile.parameters.change_threshold_relative_to_observed_range,
  ).toPrecision(12))

  const lineages: TrendFactLineage[] = []
  let factOrdinal = 0
  const addFact = (
    claimPath: string,
    indexes: number[] = [],
    extraRefs: string[] = [],
  ): string => {
    factOrdinal += 1
    const factId = `trend-fact:${input.metric}:${String(factOrdinal).padStart(4, '0')}`
    lineages.push({
      fact_id: factId,
      claim_path: claimPath,
      source_point_indexes: [...indexes],
      evidence_refs: [...new Set([
        ...input.source_evidence_refs.identity,
        input.source_evidence_refs.metric_definition,
        input.source_evidence_refs.trend_profile,
        ...pointEvidence(input, indexes),
        ...extraRefs,
      ])],
      derived_by: {
        operator_id: 'event-window-trend',
        operator_version: '1.2.0',
        profile_id: input.trend_profile.profile_id,
      },
    })
    return factId
  }
  const pointFact = (point: AnalysisPoint, path: string): TrendPointFact => ({
    index: point.index,
    at_utc: point.atUtc,
    value: point.rawValue,
    analysis_value: point.analysisValue,
    fact_id: addFact(path, [point.index]),
  })

  const firstPoint = observed[0]!
  const lastPoint = observed[observed.length - 1]!
  let minimumPoint = firstPoint
  let maximumPoint = firstPoint
  for (const point of observed.slice(1)) {
    if (point.rawValue < minimumPoint.rawValue) minimumPoint = point
    if (point.rawValue > maximumPoint.rawValue) maximumPoint = point
  }

  const phaseSequence: TrendPhase[] = []
  const turningPoints: TrendTurningPoint[] = []
  for (const run of runs) {
    const pivots = phasePivots(run.points, threshold)
    for (let index = 1; index < pivots.length; index += 1) {
      const from = pivots[index - 1]!
      const to = pivots[index]!
      const change = to.analysisValue - from.analysisValue
      const phaseId = `phase-${String(phaseSequence.length + 1).padStart(3, '0')}`
      const factId = addFact(
        `/phase_sequence/${phaseSequence.length}`,
        [from.index, to.index],
      )
      phaseSequence.push({
        phase_id: phaseId,
        direction: direction(change, threshold),
        source_run: run.id,
        from: pointFact(from, `/phase_sequence/${phaseSequence.length}/from`),
        to: pointFact(to, `/phase_sequence/${phaseSequence.length}/to`),
        change,
        duration_ms: to.epochMs - from.epochMs,
        fact_id: factId,
      })
    }
    for (let index = 1; index + 1 < pivots.length; index += 1) {
      const previous = pivots[index - 1]!
      const current = pivots[index]!
      const next = pivots[index + 1]!
      const left = current.analysisValue - previous.analysisValue
      const right = next.analysisValue - current.analysisValue
      if (!((left >= threshold && right <= -threshold)
        || (left <= -threshold && right >= threshold))) continue
      const turningPointId = `turning-point-${String(turningPoints.length + 1).padStart(3, '0')}`
      turningPoints.push({
        ...pointFact(current, `/turning_points/${turningPoints.length}`),
        turning_point_id: turningPointId,
        kind: left > 0 ? 'local_maximum' : 'local_minimum',
      })
    }
  }

  const adjacentStep = (
    kind: 'down' | 'up',
  ): TrendAdjacentStep | null => {
    let best: { from: AnalysisPoint, to: AnalysisPoint, delta: number } | null = null
    for (const run of runs) {
      for (let index = 1; index < run.points.length; index += 1) {
        const from = run.points[index - 1]!
        const to = run.points[index]!
        const delta = to.rawValue - from.rawValue
        if ((kind === 'down' && delta >= 0) || (kind === 'up' && delta <= 0)) continue
        if (best === null
          || (kind === 'down' ? delta < best.delta : delta > best.delta)) {
          best = { from, to, delta }
        }
      }
    }
    if (best === null) return null
    const path = kind === 'down'
      ? '/largest_adjacent_step_down' : '/largest_adjacent_step_up'
    const factId = addFact(path, [best.from.index, best.to.index])
    return {
      adjacent: true,
      from: pointFact(best.from, `${path}/from`),
      to: pointFact(best.to, `${path}/to`),
      change: best.delta,
      duration_ms: best.to.epochMs - best.from.epochMs,
      fact_id: factId,
    }
  }
  const largestAdjacentStepDown = adjacentStep('down')
  const largestAdjacentStepUp = adjacentStep('up')

  const isolatedSpikes: TrendIsolatedSpike[] = []
  if (input.series_semantics !== 'cumulative') {
    const spikeThreshold = threshold
      * input.trend_profile.parameters.isolated_spike_threshold_multiplier
    const returnTolerance = threshold
      * input.trend_profile.parameters.isolated_spike_return_tolerance_multiplier
    for (const run of runs) {
      for (let index = 1; index + 1 < run.points.length; index += 1) {
        const before = run.points[index - 1]!
        const center = run.points[index]!
        const after = run.points[index + 1]!
        const neighborMedian = (before.rawValue + after.rawValue) / 2
        const excursion = center.rawValue - neighborMedian
        const returnDelta = after.rawValue - before.rawValue
        if (Math.abs(excursion) < spikeThreshold
          || Math.abs(returnDelta) > returnTolerance) continue
        const spikeIndex = isolatedSpikes.length
        const factId = addFact(
          `/isolated_spikes/${spikeIndex}`,
          [before.index, center.index, after.index],
        )
        isolatedSpikes.push({
          spike_id: `isolated-spike-${String(spikeIndex + 1).padStart(3, '0')}`,
          direction: excursion > 0 ? 'up' : 'down',
          before: pointFact(before, `/isolated_spikes/${spikeIndex}/before`),
          center: pointFact(center, `/isolated_spikes/${spikeIndex}/center`),
          after: pointFact(after, `/isolated_spikes/${spikeIndex}/after`),
          excursion_from_neighbor_median: excursion,
          neighbor_return_delta: returnDelta,
          threshold_ratio: Number((Math.abs(excursion) / threshold).toFixed(6)),
          fact_id: factId,
        })
      }
    }
  }

  const displayPhaseSequence: TrendDisplayPhase[] = []
  const displayTurningPoints: TrendTurningPoint[] = []
  for (const run of runs) {
    const auditPhases = phaseSequence.filter((phase) => phase.source_run === run.id)
    if (run.points.length === 1) {
      const only = run.points[0]!
      displayPhaseSequence.push({
        display_phase_id: `display-phase-${String(displayPhaseSequence.length + 1).padStart(3, '0')}`,
        source_run: run.id,
        from: pointFact(only, `/display_phase_sequence/${displayPhaseSequence.length}/from`),
        to: pointFact(only, `/display_phase_sequence/${displayPhaseSequence.length}/to`),
        direction: 'stable',
        shape: 'stable',
        change: 0,
        duration_ms: 0,
        minimum: only.rawValue,
        maximum: only.rawValue,
        audit_phase_ids: [],
        fact_id: addFact(`/display_phase_sequence/${displayPhaseSequence.length}`, [only.index]),
      })
      continue
    }
    const candidates = auditPhases.slice(0, -1).map((phase, index) => {
      const next = auditPhases[index + 1]!
      const magnitude = Math.min(Math.abs(phase.change), Math.abs(next.change))
      const durationScale = nominalInterval === null ? 1 : Math.max(
        1,
        Math.min(phase.duration_ms, next.duration_ms) / nominalInterval,
      )
      return {
        pointIndex: phase.to.index,
        score: (magnitude / threshold) * Math.log2(2 + durationScale),
      }
    })
    const selected = candidates
      .sort((left, right) => right.score - left.score
        || left.pointIndex - right.pointIndex)
      .slice(0, Math.max(
        0,
        input.trend_profile.parameters.display_max_phases_per_run - 1,
      ))
    const selectedIndexes = new Set(selected.map((item) => item.pointIndex))
    const boundaries = [
      run.points[0]!,
      ...run.points.filter((point) => selectedIndexes.has(point.index)),
      run.points[run.points.length - 1]!,
    ].filter((point, index, all) => index === 0
      || point.index !== all[index - 1]!.index)
    for (let index = 1; index < boundaries.length; index += 1) {
      const from = boundaries[index - 1]!
      const to = boundaries[index]!
      const points = run.points.filter((point) =>
        point.index >= from.index && point.index <= to.index)
      const change = to.analysisValue - from.analysisValue
      const minimum = points.reduce(
        (value, point) => Math.min(value, point.rawValue), points[0]!.rawValue,
      )
      const maximum = points.reduce(
        (value, point) => Math.max(value, point.rawValue), points[0]!.rawValue,
      )
      const simpleDirection = direction(change, threshold)
      const displayDirection = simpleDirection === 'stable'
        && maximum - minimum >= threshold * 2 ? 'mixed' : simpleDirection
      const sourceAuditPhases = auditPhases.filter((phase) =>
        phase.to.index > from.index && phase.from.index < to.index)
      const shape: TrendDisplayPhase['shape'] = displayDirection === 'stable'
        ? 'stable'
        : displayDirection === 'mixed'
          ? 'volatile'
          : to.epochMs - from.epochMs <= (nominalInterval ?? 0)
            ? 'abrupt'
            : sourceAuditPhases.length > 1 ? 'stepwise' : 'gradual'
      const displayIndex = displayPhaseSequence.length
      displayPhaseSequence.push({
        display_phase_id: `display-phase-${String(displayIndex + 1).padStart(3, '0')}`,
        source_run: run.id,
        from: pointFact(from, `/display_phase_sequence/${displayIndex}/from`),
        to: pointFact(to, `/display_phase_sequence/${displayIndex}/to`),
        direction: displayDirection,
        shape,
        change,
        duration_ms: to.epochMs - from.epochMs,
        minimum,
        maximum,
        audit_phase_ids: sourceAuditPhases.map((phase) => phase.phase_id),
        fact_id: addFact(
          `/display_phase_sequence/${displayIndex}`,
          [from.index, to.index],
        ),
      })
    }
    for (const selectedBoundary of selected.sort((left, right) =>
      left.pointIndex - right.pointIndex)) {
      const existing = turningPoints.find((point) =>
        point.index === selectedBoundary.pointIndex)
      if (existing !== undefined) {
        displayTurningPoints.push(structuredClone(existing))
        continue
      }
      const point = run.points.find((item) =>
        item.index === selectedBoundary.pointIndex)!
      const before = auditPhases.find((phase) => phase.to.index === point.index)
      const after = auditPhases.find((phase) => phase.from.index === point.index)
      const kind: TrendTurningPoint['kind'] = input.series_semantics === 'cumulative'
        && after?.direction === 'stable'
        ? 'plateau_start' : 'level_shift'
      displayTurningPoints.push({
        ...pointFact(point, `/display_turning_points/${displayTurningPoints.length}`),
        turning_point_id: `display-turning-point-${String(displayTurningPoints.length + 1).padStart(3, '0')}`,
        kind: before?.direction === 'increase' && after?.direction === 'decrease'
          ? 'local_maximum'
          : before?.direction === 'decrease' && after?.direction === 'increase'
            ? 'local_minimum' : kind,
      })
    }
  }

  const movement = (kind: 'decline' | 'rebound'): TrendMovementSegment | null => {
    if (input.series_semantics === 'cumulative') return null
    const candidates = phaseSequence.filter((phase) => phase.direction
      === (kind === 'decline' ? 'decrease' : 'increase'))
    let best: TrendPhase | null = null
    for (const candidate of candidates) {
      if (best === null
        || (kind === 'decline'
          ? candidate.change < best.change
          : candidate.change > best.change)) best = candidate
    }
    if (best === null) return null
    const factId = addFact(
      kind === 'decline' ? '/maximum_decline_segment' : '/maximum_rebound_segment',
      [best.from.index, best.to.index],
    )
    return {
      from: structuredClone(best.from),
      to: structuredClone(best.to),
      change: best.change,
      duration_ms: best.duration_ms,
      fact_id: factId,
    }
  }

  const nullGaps: TrendGap[] = []
  for (let index = 0; index < input.values.length;) {
    if (input.values[index] !== null) {
      index += 1
      continue
    }
    const start = index
    while (index + 1 < input.values.length && input.values[index + 1] === null) index += 1
    const end = index
    nullGaps.push({
      gap_id: `gap-${String(nullGaps.length + 1).padStart(3, '0')}`,
      kind: 'null_run',
      from_index: start,
      to_index: end,
      start_at_utc: input.timestamps[start]!,
      end_at_utc: input.timestamps[end]!,
      slot_count: end - start + 1,
      duration_ms: epochs[end]! - epochs[start]!
        + (nominalInterval ?? 0),
      fact_id: addFact(`/null_and_gaps/gaps/${nullGaps.length}`, [start, end]),
    })
    index += 1
  }
  const implicitGaps: TrendGap[] = []
  if (nominalInterval !== null) {
    const limit = nominalInterval
      * input.trend_profile.parameters.gap_interval_multiplier
    intervals.forEach((interval, index) => {
      if (interval <= limit) return
      implicitGaps.push({
        gap_id: `gap-${String(nullGaps.length + implicitGaps.length + 1).padStart(3, '0')}`,
        kind: 'implicit_time_gap',
        from_index: index,
        to_index: index + 1,
        start_at_utc: input.timestamps[index]!,
        end_at_utc: input.timestamps[index + 1]!,
        slot_count: Math.max(1, Math.round(interval / nominalInterval) - 1),
        duration_ms: interval,
        fact_id: addFact(
          `/null_and_gaps/gaps/${nullGaps.length + implicitGaps.length}`,
          [index, index + 1],
        ),
      })
    })
  }
  const gaps = [...nullGaps, ...implicitGaps]
    .sort((left, right) => left.from_index - right.from_index
      || (left.kind < right.kind ? -1 : left.kind > right.kind ? 1 : 0))
  let trailingNullPointCount = 0
  for (let index = input.values.length - 1;
    index >= 0 && input.values[index] === null; index -= 1) {
    trailingNullPointCount += 1
  }
  const irregularInterval = intervals.some((interval) => interval !== nominalInterval)
  const warnings = [
    ...(input.values.length !== observed.length ? ['contains_nulls'] : []),
    ...(gaps.length > 0 ? ['contains_gaps'] : []),
    ...(trailingNullPointCount > 0 ? ['tail_unobserved'] : []),
    ...(irregularInterval ? ['irregular_interval'] : []),
    ...(observed.length < input.trend_profile.parameters.minimum_observed_points
      ? ['short_series'] : []),
  ]
  const qualityStatus = observed.length
    < input.trend_profile.parameters.minimum_observed_points
    ? 'insufficient'
    : warnings.length > 0 ? 'usable_with_caveats' : 'complete'
  const qualityFactId = addFact(
    '/data_quality',
    [],
    [input.source_evidence_refs.timestamps, input.source_evidence_refs.values],
  )

  const globalShape = classifyShape(
    input.series_semantics,
    observed.length,
    input.trend_profile.parameters.minimum_observed_points,
    phaseSequence,
    baseline,
    lastPoint.rawValue,
    threshold,
  )
  const tailRun = runs[runs.length - 1]!
  const tailPoints = tailRun.points.slice(
    -input.trend_profile.parameters.tail_observed_points,
  )
  const recentDirection = tailPoints.length < 2
    ? 'unavailable'
    : direction(
      tailPoints[tailPoints.length - 1]!.analysisValue
        - tailPoints[0]!.analysisValue,
      threshold,
    )
  const baselineDifference = lastPoint.rawValue - baseline
  const baselineRelation = baselineDifference >= threshold
    ? 'above' : baselineDifference <= -threshold ? 'below' : 'near'
  const dataThroughEpoch = input.source_identity.data_through === null
    ? null : Date.parse(input.source_identity.data_through)
  const tailObservation = dataThroughEpoch === null
    ? 'data_through_unknown'
    : lastPoint.epochMs === dataThroughEpoch && trailingNullPointCount === 0
      ? 'at_data_through' : 'before_data_through'

  const phaseDuration = (wanted: TrendDirection): number => phaseSequence
    .filter((phase) => phase.direction === wanted)
    .reduce((sum, phase) => sum + phase.duration_ms, 0)
  const connectedObservationDuration = runs.reduce((sum, run) => {
    if (run.points.length < 2) return sum
    return sum + run.points[run.points.length - 1]!.epochMs - run.points[0]!.epochMs
  }, 0)
  let belowBaselineDuration = 0
  if (input.series_semantics !== 'cumulative') {
    for (const run of runs) {
      for (let index = 1; index < run.points.length; index += 1) {
        if (run.points[index - 1]!.analysisValue <= baseline - threshold
          && run.points[index]!.analysisValue <= baseline - threshold) {
          belowBaselineDuration += run.points[index]!.epochMs
            - run.points[index - 1]!.epochMs
        }
      }
    }
  }
  const durationFactId = addFact(
    '/duration',
    [firstPoint.index, lastPoint.index],
    [input.source_evidence_refs.timestamps],
  )
  const nullGapFactId = addFact(
    '/null_and_gaps',
    input.values.map((value, index) => value === null ? index : -1).filter((index) => index >= 0),
    [input.source_evidence_refs.timestamps, input.source_evidence_refs.values],
  )
  const tailFactId = addFact('/tail_state', [lastPoint.index])
  const first = pointFact(firstPoint, '/summary/first')
  const last = pointFact(lastPoint, '/summary/last')
  const minimum = pointFact(minimumPoint, '/summary/minimum')
  const maximum = pointFact(maximumPoint, '/summary/maximum')
  const netFact = addFact('/summary/net_change', [firstPoint.index, lastPoint.index])
  const baselineFact = addFact(
    '/summary/baseline', baselinePoints.map((point) => point.index),
  )
  const baselineDifferenceFact = addFact(
    '/summary/baseline_difference_at_tail',
    [...baselinePoints.map((point) => point.index), lastPoint.index],
  )
  const thresholdFact = addFact(
    '/summary/change_threshold',
    baselinePoints.map((point) => point.index),
    [input.source_evidence_refs.trend_profile],
  )
  const shapeFact = addFact(
    '/summary/global_shape',
    [],
    [input.source_evidence_refs.timestamps, input.source_evidence_refs.values],
  )
  const maximumDecline = movement('decline')
  const maximumRebound = movement('rebound')

  const significantCandidates: Array<Omit<TrendSignificantFact, 'rank'>> = []
  const candidate = (
    factType: TrendSignificantFact['fact_type'],
    salienceScore: number,
    factId: string,
    pointIndexes: number[],
  ) => significantCandidates.push({
    fact_type: factType,
    salience_score: Number(salienceScore.toFixed(6)),
    fact_id: factId,
    point_indexes: pointIndexes,
  })
  if (warnings.length > 0) candidate('data_quality', 110, qualityFactId, [])
  candidate('last', 100, last.fact_id, [last.index])
  const primaryPoint = input.trend_profile.primary_fact === 'minimum'
    ? minimum : input.trend_profile.primary_fact === 'maximum' ? maximum : last
  candidate(
    input.trend_profile.primary_fact,
    95,
    primaryPoint.fact_id,
    [primaryPoint.index],
  )
  candidate('net_change', 90, netFact, [first.index, last.index])
  if (largestAdjacentStepDown !== null) candidate(
    'largest_adjacent_step_down',
    85 + Math.min(9, Math.abs(largestAdjacentStepDown.change) / threshold),
    largestAdjacentStepDown.fact_id,
    [largestAdjacentStepDown.from.index, largestAdjacentStepDown.to.index],
  )
  if (largestAdjacentStepUp !== null) candidate(
    'largest_adjacent_step_up',
    85 + Math.min(9, Math.abs(largestAdjacentStepUp.change) / threshold),
    largestAdjacentStepUp.fact_id,
    [largestAdjacentStepUp.from.index, largestAdjacentStepUp.to.index],
  )
  if (maximumDecline !== null) candidate(
    'maximum_decline_segment',
    80 + Math.min(9, Math.abs(maximumDecline.change) / threshold),
    maximumDecline.fact_id,
    [maximumDecline.from.index, maximumDecline.to.index],
  )
  if (maximumRebound !== null) candidate(
    'maximum_numeric_rise_segment',
    80 + Math.min(9, Math.abs(maximumRebound.change) / threshold),
    maximumRebound.fact_id,
    [maximumRebound.from.index, maximumRebound.to.index],
  )
  for (const spike of isolatedSpikes) candidate(
    'isolated_spike',
    88 + Math.min(9, spike.threshold_ratio),
    spike.fact_id,
    [spike.before.index, spike.center.index, spike.after.index],
  )
  if (input.series_semantics === 'cumulative' && maximum.index < last.index) {
    candidate('cumulative_plateau_start', 88, maximum.fact_id, [maximum.index])
  }
  const uniqueSignificant = new Map<string, Omit<TrendSignificantFact, 'rank'>>()
  for (const item of significantCandidates) {
    const existing = uniqueSignificant.get(item.fact_id)
    if (existing === undefined || item.salience_score > existing.salience_score) {
      uniqueSignificant.set(item.fact_id, item)
    }
  }
  const significantFacts = [...uniqueSignificant.values()]
    .sort((left, right) => right.salience_score - left.salience_score
      || (left.point_indexes[0] ?? -1) - (right.point_indexes[0] ?? -1)
      || (left.fact_type < right.fact_type ? -1 : left.fact_type > right.fact_type ? 1 : 0))
    .slice(0, input.trend_profile.parameters.display_max_facts)
    .map((item, index) => ({ ...item, rank: index + 1 }))

  const compactEvidence = (factIds: string[]): string[] => [...new Set(
    lineages.filter((item) => factIds.includes(item.fact_id))
      .flatMap((item) => item.evidence_refs),
  )]
  const compactCards: TrendCompactCard[] = []
  const addCompactCard = (
    factType: TrendCompactCardType,
    labelZh: string,
    textZh: string,
    value: number,
    atUtc: string | null,
    factIds: string[],
    unit = input.unit,
    unitLabelZh = input.trend_profile.unit_label_zh,
  ): void => {
    compactCards.push({
      card_id: `compact-card-${String(compactCards.length + 1).padStart(2, '0')}`,
      fact_type: factType,
      label_zh: labelZh,
      text_zh: textZh,
      value,
      unit,
      unit_label_zh: unitLabelZh,
      at_utc: atUtc,
      fact_ids: [...new Set(factIds)],
      evidence_refs: compactEvidence(factIds),
    })
  }
  const unitLabel = input.trend_profile.unit_label_zh
  const valueText = (value: number): string => `${compactNumber(value)}${unitLabel}`
  addCompactCard('first', '首个有效值', valueText(first.value), first.value,
    first.at_utc, [first.fact_id])
  if (input.trend_profile.primary_fact === 'minimum') {
    addCompactCard('minimum', '窗口最低', valueText(minimum.value), minimum.value,
      minimum.at_utc, [minimum.fact_id])
  } else if (input.trend_profile.primary_fact === 'maximum') {
    addCompactCard('maximum', '窗口峰值', valueText(maximum.value), maximum.value,
      maximum.at_utc, [maximum.fact_id])
  }
  addCompactCard('last', trailingNullPointCount > 0 ? '最后有效观测' : '截止值',
    valueText(last.value), last.value, last.at_utc, [last.fact_id, tailFactId])
  addCompactCard('net_change', '首尾净变化', compactChange(
    last.value - first.value, unitLabel,
  ), last.value - first.value, last.at_utc, [netFact, first.fact_id, last.fact_id])

  const primarySpike = isolatedSpikes.find((spike) =>
    spike.center.index === primaryPoint.index)
  if (primarySpike !== undefined) {
    addCompactCard('isolated_spike', '单槽尖峰',
      `${valueText(primarySpike.center.value)}，前后槽回到接近水平`,
      primarySpike.center.value, primarySpike.center.at_utc,
      [primarySpike.fact_id, primarySpike.center.fact_id])
  }
  if (input.series_semantics === 'cumulative'
    && largestAdjacentStepUp !== null) {
    addCompactCard('largest_adjacent_step_up', '最大单槽增加',
      `增加 ${valueText(largestAdjacentStepUp.change)}`,
      largestAdjacentStepUp.change, largestAdjacentStepUp.to.at_utc,
      [largestAdjacentStepUp.fact_id])
  }
  if (trailingNullPointCount > 0) {
    addCompactCard('tail_null', '尾部空值',
      `末尾 ${compactNumber(trailingNullPointCount)} 个槽位无有效值`,
      trailingNullPointCount, null, [nullGapFactId, tailFactId],
      'slot', '个槽位')
  }

  const tailNoun = trailingNullPointCount > 0 ? '最后有效观测' : '截止值'
  const netText = compactChange(last.value - first.value, '')
  const bodyValue = (value: number): string => compactNumber(value)
  let compactBody: string
  if (input.trend_profile.semantic_role === 'visibility_stock') {
    let afterMinimum: string
    if (last.index === minimum.index || last.value === minimum.value) {
      afterMinimum = `${tailNoun}仍为该低点 ${bodyValue(last.value)}`
    } else if (last.value > minimum.value && last.value < first.value) {
      afterMinimum = `随后数值部分回升，${tailNoun}为 ${bodyValue(last.value)}`
    } else if (last.value === first.value) {
      afterMinimum = `随后回到首值，${tailNoun}为 ${bodyValue(last.value)}`
    } else if (last.value > first.value) {
      afterMinimum = `随后升至高于首值，${tailNoun}为 ${bodyValue(last.value)}`
    } else {
      afterMinimum = `${tailNoun}为 ${bodyValue(last.value)}`
    }
    compactBody = `${input.trend_profile.display_label_zh}：从 ${bodyValue(first.value)} 降至窗口最低 ${bodyValue(minimum.value)}，${afterMinimum}；${netText}。`
  } else if (input.trend_profile.semantic_role === 'interruption_stock') {
    const spikeText = primarySpike === undefined ? '' : '，该峰值为单槽尖峰'
    compactBody = `${input.trend_profile.display_label_zh}：窗口峰值为 ${bodyValue(maximum.value)}${spikeText}；${tailNoun}为 ${bodyValue(last.value)}，${netText}。`
  } else if (input.trend_profile.semantic_role === 'cumulative_new') {
    const stepText = largestAdjacentStepUp === null
      ? '窗口内没有数值增加'
      : `最大单槽增加 ${bodyValue(largestAdjacentStepUp.change)}`
    const plateauText = maximum.index < last.index
      ? '，达到当前累计值后保持至截止' : ''
    compactBody = `${input.trend_profile.display_label_zh}：${tailNoun}为 ${bodyValue(last.value)}，${netText}；${stepText}${plateauText}。`
  } else {
    const spikeText = primarySpike === undefined ? '' : '，且为单槽尖峰'
    compactBody = `${input.trend_profile.display_label_zh}：属于当前可见补充量，窗口峰值 ${bodyValue(maximum.value)}${spikeText}；${tailNoun}为 ${bodyValue(last.value)}，${netText}。`
  }
  if (qualityStatus === 'insufficient') {
    compactBody = `${input.trend_profile.display_label_zh}有效观测较少：首个有效值 ${bodyValue(first.value)}，${tailNoun}为 ${bodyValue(last.value)}；暂只报告可核对数值。`
  }

  const compactLimitations: TrendCompactLimitation[] = [
    {
      limitation_id: 'unit-and-scope',
      text_zh: `单位为${unitLabel}；采用当前 publication 的 RRC25 控制面口径。`,
      fact_ids: [first.fact_id, last.fact_id],
      evidence_refs: compactEvidence([first.fact_id, last.fact_id]),
    },
    {
      limitation_id: 'inference-boundary',
      text_zh: '仅描述观测数值，不用于判断恢复、原因或真实用户影响。',
      fact_ids: [tailFactId],
      evidence_refs: compactEvidence([tailFactId]),
    },
  ]
  if (trailingNullPointCount > 0) {
    compactLimitations.push({
      limitation_id: 'tail-unobserved',
      text_zh: '尾部存在空值，最后有效观测不能向前填充为当前值。',
      fact_ids: [nullGapFactId, tailFactId],
      evidence_refs: compactEvidence([nullGapFactId, tailFactId]),
    })
  }
  const compactFactIds = [...new Set([
    ...compactCards.flatMap((card) => card.fact_ids),
    ...compactLimitations.flatMap((item) => item.fact_ids),
  ])]
  const compactChatOutput: EventWindowTrendCompactOutput = {
    schema_version: 'country_outage_p1_event_window_trend_compact_chat_v1',
    operator: {
      operator_id: 'event-window-trend-compact',
      operator_version: '1.2.0',
      deterministic: true,
      model_dependency: 'none',
    },
    source_identity: structuredClone(input.source_identity),
    display_label_zh: input.trend_profile.display_label_zh,
    unit: input.unit,
    unit_label_zh: unitLabel,
    series_semantics: input.series_semantics,
    semantic_role: input.trend_profile.semantic_role,
    headline_zh: `${input.trend_profile.display_label_zh}（单位：${unitLabel.replace(/^个/, '')}）`,
    body_zh: compactBody,
    sentence_count: compactBody.split(/[。！？]/).filter((item) => item.length > 0).length,
    character_count: Array.from(compactBody).length,
    cards: compactCards,
    limitations: compactLimitations,
    fact_ids: compactFactIds,
    evidence_refs: compactEvidence(compactFactIds),
  }

  const descriptionFacts = [
    first.fact_id,
    last.fact_id,
    netFact,
    baselineFact,
    baselineDifferenceFact,
    shapeFact,
    qualityFactId,
    durationFactId,
    nullGapFactId,
    tailFactId,
    ...significantFacts.map((item) => item.fact_id),
    ...(maximumDecline === null ? [] : [maximumDecline.fact_id]),
    ...(maximumRebound === null ? [] : [maximumRebound.fact_id]),
  ]
  const movementDescription = input.series_semantics === 'cumulative'
    ? '累计语义不生成最大下降段或“下降后回升”判断。'
    : [
      maximumDecline === null
        ? '未识别到达到登记阈值的连续数值下降段。'
        : `最大连续数值下降段为 ${numericText(maximumDecline.change)}（${maximumDecline.from.at_utc} 至 ${maximumDecline.to.at_utc}，持续 ${numericText(maximumDecline.duration_ms)} 毫秒）。`,
      maximumRebound === null
        ? '未识别到达到登记阈值的连续数值上升段。'
        : `最大连续数值上升段（maximum_rebound_segment）为 +${numericText(maximumRebound.change)}（${maximumRebound.from.at_utc} 至 ${maximumRebound.to.at_utc}，持续 ${numericText(maximumRebound.duration_ms)} 毫秒）；“上升”只是数值方向，不表示恢复。`,
    ].join('')
  const description = [
    `${input.metric}（单位 ${input.unit}，${input.series_semantics === 'stock' ? '存量' : input.series_semantics === 'cumulative' ? '累计' : '当前可见补充'}语义）`,
    `首个非空值 ${numericText(first.value)}（${first.at_utc}），末个非空值 ${numericText(last.value)}（${last.at_utc}），净变化 ${numericText(last.value - first.value)}。`,
    `基线 ${numericText(baseline)}，末值相对基线差 ${numericText(baselineDifference)}；全局形态：${describeShape(globalShape)}。`,
    `按登记阈值 ${numericText(threshold)} 得到 ${phaseSequence.length} 个审计阶段、${turningPoints.length} 个审计转折；展示层压缩为 ${displayPhaseSequence.length} 个阶段、${displayTurningPoints.length} 个转折。识别 ${isolatedSpikes.length} 个单槽尖峰，尖峰与持续阶段分开。${movementDescription}`,
    `有效观测 ${observed.length}/${input.values.length}，null ${input.values.length - observed.length}，缺口 ${gaps.length}；null 未插值、未按 0 处理。`,
    tailObservation === 'at_data_through'
      ? '末值位于 data-through；这里只描述观测窗口内数值状态，不据此判断事件结束或恢复。'
      : '末个非空值早于 data-through 或 data-through 未知；不得向前填充为当前值，也不判断事件结束或恢复。',
  ].join('')

  return {
    schema_version: 'country_outage_p1_event_window_trend_result_v3',
    operator: {
      operator_id: 'event-window-trend',
      operator_version: '1.2.0',
      deterministic: true,
      model_dependency: 'none',
    },
    source_identity: structuredClone(input.source_identity),
    metric: input.metric,
    unit: input.unit,
    series_semantics: input.series_semantics,
    trend_profile: cloneProfile(input.trend_profile),
    data_quality: {
      status: { value: qualityStatus, fact_id: qualityFactId },
      total_point_count: input.values.length,
      observed_point_count: observed.length,
      null_point_count: input.values.length - observed.length,
      coverage_ratio: observed.length / input.values.length,
      nominal_interval_ms: nominalInterval,
      irregular_interval: irregularInterval,
      warnings,
      fact_id: qualityFactId,
    },
    summary: {
      first,
      last,
      minimum,
      maximum,
      net_change: { value: last.value - first.value, fact_id: netFact },
      baseline: { value: baseline, fact_id: baselineFact },
      baseline_difference_at_tail: {
        value: baselineDifference,
        fact_id: baselineDifferenceFact,
      },
      change_threshold: { value: threshold, fact_id: thresholdFact },
      global_shape: { value: globalShape, fact_id: shapeFact },
    },
    phase_sequence: phaseSequence,
    turning_points: turningPoints,
    display_phase_sequence: displayPhaseSequence,
    display_turning_points: displayTurningPoints,
    largest_adjacent_step_down: largestAdjacentStepDown,
    largest_adjacent_step_up: largestAdjacentStepUp,
    isolated_spikes: isolatedSpikes,
    maximum_decline_segment: maximumDecline,
    maximum_rebound_segment: maximumRebound,
    significant_facts: significantFacts,
    duration: {
      window_duration_ms: Date.parse(input.source_identity.window_end_utc)
        - Date.parse(input.source_identity.window_start_utc),
      observed_span_ms: lastPoint.epochMs - firstPoint.epochMs,
      connected_observation_duration_ms: connectedObservationDuration,
      increase_duration_ms: phaseDuration('increase'),
      decrease_duration_ms: phaseDuration('decrease'),
      stable_duration_ms: phaseDuration('stable'),
      below_baseline_duration_ms: input.series_semantics !== 'cumulative'
        ? belowBaselineDuration : null,
      fact_id: durationFactId,
    },
    null_and_gaps: {
      null_point_count: input.values.length - observed.length,
      trailing_null_point_count: trailingNullPointCount,
      gap_count: gaps.length,
      longest_gap_duration_ms: gaps.reduce(
        (best, gap) => Math.max(best, gap.duration_ms), 0,
      ),
      gaps,
      interpolation: 'forbidden',
      null_as_zero: 'forbidden',
      fact_id: nullGapFactId,
    },
    tail_state: {
      observation: tailObservation,
      baseline_relation: baselineRelation,
      recent_direction: recentDirection,
      last_observed_at_utc: last.at_utc,
      trailing_null_point_count: trailingNullPointCount,
      event_state_inference: 'forbidden',
      fact_id: tailFactId,
    },
    deterministic_description_zh: {
      text: description,
      fact_ids: descriptionFacts,
    },
    compact_chat_output: compactChatOutput,
    fact_lineage: lineages,
  }
}

export function analyzeEventWindowTrendCompact(
  input: EventWindowTrendInput,
): EventWindowTrendCompactOutput {
  return analyzeEventWindowTrend(input).compact_chat_output
}

export function analyzeCompactTrendBundle(
  bundleProfileId: 'fixed-ip-address-change-v1',
  inputs: EventWindowTrendInput[],
): CompactTrendBundleOutput {
  if (bundleProfileId !== 'fixed-ip-address-change-v1' || inputs.length !== 2) {
    throw new EventWindowTrendError(
      'invalid_series_shape', 'fixed IP compact bundle 必须且只能提交 IPv4/IPv6 两条登记轨道',
    )
  }
  const byMetric = new Map(inputs.map((input) => [input.metric, input]))
  const ipv4Input = byMetric.get('fixed_visible_ipv4_address_count')
  const ipv6Input = byMetric.get('fixed_visible_ipv6_slash48_count')
  if (byMetric.size !== 2 || ipv4Input === undefined || ipv6Input === undefined) {
    throw new EventWindowTrendError(
      'invalid_series_shape', 'fixed IP compact bundle 只接受登记的 fixed IPv4 与 fixed IPv6 轨道',
    )
  }
  if (!sameJson(ipv4Input.source_identity, ipv6Input.source_identity)
    || !sameJson(ipv4Input.timestamps, ipv6Input.timestamps)) {
    throw new EventWindowTrendError(
      'cross_track_identity_conflict', 'compact bundle 要求完整 source identity 与时间轴一致',
    )
  }
  const ipv4 = analyzeEventWindowTrendCompact(ipv4Input)
  const ipv6 = analyzeEventWindowTrendCompact(ipv6Input)
  const card = (
    output: EventWindowTrendCompactOutput,
    factType: TrendCompactCardType,
  ): TrendCompactCard => {
    const value = output.cards.find((item) => item.fact_type === factType)
    if (value === undefined) {
      throw new EventWindowTrendError(
        'invalid_series_shape', `compact bundle 缺少 ${factType} 卡片`,
      )
    }
    return value
  }
  const trackText = (
    family: 'IPv4 唯一地址' | 'IPv6 /48 等价量',
    output: EventWindowTrendCompactOutput,
  ): string => {
    const first = card(output, 'first')
    const minimum = card(output, 'minimum')
    const last = card(output, 'last')
    const net = card(output, 'net_change')
    const netText = net.value > 0
      ? `较首值增加 ${compactNumber(net.value)}`
      : net.value < 0
        ? `较首值减少 ${compactNumber(Math.abs(net.value))}`
        : '与首值相同'
    return `${family}：${compactNumber(first.value)}→最低 ${compactNumber(minimum.value)}→${last.label_zh} ${compactNumber(last.value)}，${netText}`
  }
  const body = `${trackText('IPv4 唯一地址', ipv4)}；${trackText('IPv6 /48 等价量', ipv6)}。两者分轨计量，不合并。`
  const factIds = [...new Set([...ipv4.fact_ids, ...ipv6.fact_ids])]
  const evidenceRefs = [...new Set([...ipv4.evidence_refs, ...ipv6.evidence_refs])]
  const limitations: TrendCompactLimitation[] = [
    {
      limitation_id: 'address-family-unit-separation',
      text_zh: 'IPv4 使用唯一地址量，IPv6 使用 /48 等价量；两种单位不可相加。',
      fact_ids: factIds,
      evidence_refs: evidenceRefs,
    },
    {
      limitation_id: 'inference-boundary',
      text_zh: '仅描述 RRC25 控制面数值，不用于判断恢复、原因或真实用户影响。',
      fact_ids: [
        ...ipv4.limitations.find((item) =>
          item.limitation_id === 'inference-boundary')!.fact_ids,
        ...ipv6.limitations.find((item) =>
          item.limitation_id === 'inference-boundary')!.fact_ids,
      ],
      evidence_refs: [
        ...ipv4.limitations.find((item) =>
          item.limitation_id === 'inference-boundary')!.evidence_refs,
        ...ipv6.limitations.find((item) =>
          item.limitation_id === 'inference-boundary')!.evidence_refs,
      ],
    },
  ]
  return {
    schema_version: 'country_outage_p1_compact_trend_bundle_v1',
    bundle_profile_id: 'fixed-ip-address-change-v1',
    operator: {
      operator_id: 'event-window-trend-compact-bundle',
      operator_version: '1.2.0',
      deterministic: true,
      model_dependency: 'none',
    },
    source_identity: structuredClone(ipv4.source_identity),
    title_zh: 'IP 地址变化情况',
    body_zh: body,
    character_count: Array.from(body).length,
    tracks: [ipv4, ipv6],
    limitations,
    unit_separation: {
      ipv4_unit: 'unique_ipv4_address',
      ipv6_unit: 'ipv6_slash48_equivalent',
      cross_unit_aggregation: 'forbidden',
    },
    fact_ids: factIds,
    evidence_refs: evidenceRefs,
  }
}

const CUMULATIVE_CURRENT_PAIRS: ReadonlyArray<readonly [
  RegisteredTrendMetric,
  RegisteredTrendMetric,
]> = Object.freeze([
  ['new_cumulative_ipv4_prefix_count', 'new_visible_ipv4_prefix_count'],
  ['new_cumulative_ipv4_address_count', 'new_visible_ipv4_address_count'],
  ['new_cumulative_ipv6_prefix_count', 'new_visible_ipv6_prefix_count'],
  ['new_cumulative_ipv6_slash48_count', 'new_visible_ipv6_slash48_count'],
])

function sourcePointRefs(
  input: EventWindowTrendInput,
  indexes: number[],
): string[] {
  return [...new Set([
    ...input.source_evidence_refs.identity,
    input.source_evidence_refs.metric_definition,
    input.source_evidence_refs.trend_profile,
    ...pointEvidence(input, indexes),
  ])]
}

/**
 * 对同一 publication、同一时间轴的轨道生成跨轨审计事实。数值只在各自单位内
 * 计算；normalized_magnitude 仅供事实排序，不构造跨单位总量。
 */
export function analyzeMultiTrackTrend(
  inputs: EventWindowTrendInput[],
): MultiTrackTrendResult {
  if (inputs.length < 2) {
    throw new EventWindowTrendError(
      'invalid_series_shape', '多轨分析至少需要两条登记轨道',
    )
  }
  const firstInput = inputs[0]!
  const metricSet = new Set<RegisteredTrendMetric>()
  for (const input of inputs) {
    if (metricSet.has(input.metric)) {
      throw new EventWindowTrendError(
        'invalid_series_shape', `多轨输入重复指标 ${input.metric}`,
      )
    }
    metricSet.add(input.metric)
    if (!sameJson(input.source_identity, firstInput.source_identity)
      || !sameJson(input.timestamps, firstInput.timestamps)) {
      throw new EventWindowTrendError(
        'cross_track_identity_conflict', '多轨分析要求完整 source identity 与时间轴一致',
      )
    }
  }
  const orderedInputs = [...inputs].sort((left, right) =>
    left.metric < right.metric ? -1 : left.metric > right.metric ? 1 : 0)
  const results = new Map(orderedInputs.map((input) => [
    input.metric,
    analyzeEventWindowTrend(input),
  ]))
  const inputByMetric = new Map(orderedInputs.map((input) => [input.metric, input]))
  const facts: MultiTrackTrendFact[] = []
  let ordinal = 0
  const addFact = (
    partial: Omit<MultiTrackTrendFact, 'fact_id'>,
  ): void => {
    ordinal += 1
    facts.push({
      fact_id: `multi-track-fact:${String(ordinal).padStart(5, '0')}`,
      ...partial,
    })
  }
  const evidencePoint = (
    input: EventWindowTrendInput,
    beforeIndex: number,
    afterIndex: number,
    delta: number,
    sourceFactIds: string[],
  ): MultiTrackTrendEvidencePoint => {
    const threshold = results.get(input.metric)!.summary.change_threshold.value
    return {
      metric: input.metric,
      unit: input.unit,
      semantic_role: input.trend_profile.semantic_role,
      index: afterIndex,
      at_utc: input.timestamps[afterIndex]!,
      before_value: input.values[beforeIndex]!,
      after_value: input.values[afterIndex]!,
      delta,
      normalized_magnitude: Number((Math.abs(delta) / threshold).toFixed(6)),
      source_fact_ids: sourceFactIds,
      evidence_refs: sourcePointRefs(input, [beforeIndex, afterIndex]),
    }
  }

  for (let index = 1; index < firstInput.timestamps.length; index += 1) {
    const changes: MultiTrackTrendEvidencePoint[] = []
    for (const input of orderedInputs) {
      const before = input.values[index - 1]
      const after = input.values[index]
      if (before === null || before === undefined
        || after === null || after === undefined) continue
      const delta = after - before
      const result = results.get(input.metric)!
      if (Math.abs(delta) < result.summary.change_threshold.value) continue
      const stepFacts = [
        ...(result.largest_adjacent_step_down?.from.index === index - 1
          && result.largest_adjacent_step_down.to.index === index
          ? [result.largest_adjacent_step_down.fact_id] : []),
        ...(result.largest_adjacent_step_up?.from.index === index - 1
          && result.largest_adjacent_step_up.to.index === index
          ? [result.largest_adjacent_step_up.fact_id] : []),
      ]
      changes.push(evidencePoint(input, index - 1, index, delta, stepFacts))
    }
    if (changes.length < 2) continue
    const signs = new Set(changes.map((item) => Math.sign(item.delta)))
    const relation = signs.size === 1
      ? 'same_numeric_direction' : 'opposing_numeric_direction'
    const salience = changes.reduce(
      (sum, item) => sum + Math.log2(1 + item.normalized_magnitude), 0,
    ) * Math.log2(1 + changes.length)
    addFact({
      kind: 'same_interval_change',
      relation,
      from_index: index - 1,
      to_index: index,
      from_at_utc: firstInput.timestamps[index - 1]!,
      to_at_utc: firstInput.timestamps[index]!,
      salience_score: Number(salience.toFixed(6)),
      metrics: changes.map((item) => item.metric),
      evidence_points: changes,
      claim_zh: `同一相邻时间槽有 ${changes.length} 条轨道达到各自登记阈值，数值方向${relation === 'same_numeric_direction' ? '一致' : '存在反向'}；各单位分别报告，不求和。`,
    })
  }

  const spikesByCenter = new Map<number, Array<{
    input: EventWindowTrendInput
    result: EventWindowTrendResult
    spike: TrendIsolatedSpike
  }>>()
  for (const input of orderedInputs) {
    const result = results.get(input.metric)!
    for (const spike of result.isolated_spikes) {
      const group = spikesByCenter.get(spike.center.index) ?? []
      group.push({ input, result, spike })
      spikesByCenter.set(spike.center.index, group)
    }
  }
  for (const [centerIndex, group] of [...spikesByCenter.entries()]
    .sort(([left], [right]) => left - right)) {
    if (group.length < 2) continue
    const points = group.map(({ input, spike }) => evidencePoint(
      input,
      spike.before.index,
      spike.center.index,
      spike.center.value - spike.before.value,
      [spike.fact_id],
    ))
    const signs = new Set(group.map(({ spike }) => spike.direction))
    addFact({
      kind: 'synchronized_isolated_spike',
      relation: signs.size === 1
        ? 'same_numeric_direction' : 'opposing_numeric_direction',
      from_index: centerIndex - 1,
      to_index: centerIndex + 1,
      from_at_utc: firstInput.timestamps[centerIndex - 1]!,
      to_at_utc: firstInput.timestamps[centerIndex + 1]!,
      salience_score: Number((points.reduce(
        (sum, item) => sum + Math.log2(1 + item.normalized_magnitude), 0,
      ) * 2).toFixed(6)),
      metrics: points.map((item) => item.metric),
      evidence_points: points,
      claim_zh: `${points.length} 条轨道在同一中心时间槽出现孤立尖峰，前后槽回到登记容差内；该事实不并入持续阶段。`,
    })
  }

  for (const [cumulativeMetric, currentMetric] of CUMULATIVE_CURRENT_PAIRS) {
    const cumulative = inputByMetric.get(cumulativeMetric)
    const current = inputByMetric.get(currentMetric)
    if (cumulative === undefined || current === undefined) continue
    const currentResult = results.get(currentMetric)!
    for (let index = 1; index < current.timestamps.length; index += 1) {
      const cumulativeBefore = cumulative.values[index - 1]
      const cumulativeAfter = cumulative.values[index]
      const currentBefore = current.values[index - 1]
      const currentAfter = current.values[index]
      if (cumulativeBefore === null || cumulativeBefore === undefined
        || cumulativeAfter === null || cumulativeAfter === undefined
        || currentBefore === null || currentBefore === undefined
        || currentAfter === null || currentAfter === undefined
        || cumulativeAfter !== cumulativeBefore) continue
      const currentDelta = currentAfter - currentBefore
      if (Math.abs(currentDelta) < currentResult.summary.change_threshold.value) continue
      const points = [
        evidencePoint(cumulative, index - 1, index, 0, []),
        evidencePoint(current, index - 1, index, currentDelta, []),
      ]
      addFact({
        kind: 'cumulative_current_divergence',
        relation: 'not_applicable',
        from_index: index - 1,
        to_index: index,
        from_at_utc: current.timestamps[index - 1]!,
        to_at_utc: current.timestamps[index]!,
        salience_score: Number((12 + Math.log2(
          1 + points[1]!.normalized_magnitude,
        )).toFixed(6)),
        metrics: [cumulativeMetric, currentMetric],
        evidence_points: points,
        claim_zh: '累计轨道在该槽保持不变，而对应当前可见补充轨道达到登记变化阈值；两种语义不可互换。',
      })
    }
  }

  const auditFacts = [...facts].sort((left, right) =>
    left.from_index - right.from_index
    || left.to_index - right.to_index
    || (left.kind < right.kind ? -1 : left.kind > right.kind ? 1 : 0)
    || (left.metrics.join(',') < right.metrics.join(',') ? -1 : 1))
  const displayFacts = [...auditFacts]
    .sort((left, right) => right.salience_score - left.salience_score
      || left.from_index - right.from_index
      || (left.fact_id < right.fact_id ? -1 : 1))
    .slice(0, 12)
  return {
    schema_version: 'country_outage_p1_multi_track_trend_result_v1',
    operator: {
      operator_id: 'event-window-trend',
      operator_version: '1.2.0',
      deterministic: true,
      model_dependency: 'none',
    },
    source_identity: structuredClone(firstInput.source_identity),
    track_count: orderedInputs.length,
    audit_facts: auditFacts,
    display_facts: displayFacts,
    display_limit: 12,
    comparison_rules: {
      same_identity_and_timeline_required: true,
      cross_unit_aggregation: 'forbidden',
      normalized_magnitude_use: 'ranking_only',
      causal_inference: 'forbidden',
    },
  }
}
