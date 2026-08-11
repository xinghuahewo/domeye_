import { createHash } from 'node:crypto'

import type { P1ConversationBinding } from './contracts.js'
import {
  P1ReadModelError,
  type P1PageCapabilityReadProvider,
} from './general-read-model-provider.js'
import type {
  P1GroundingDecision,
  P1GroundingNode,
  P1SemanticGoalResult,
  P1UserGoal,
} from './runtime-v2-semantic.js'
import type { P1RuntimeV2Evidence } from './runtime-v2-single-turn.js'
import {
  analyzeCompactTrendBundle,
  analyzeEventWindowTrend,
  getRegisteredTrendProfile,
  P1_EVENT_WINDOW_TREND_CAPABILITY,
  P1_EVENT_WINDOW_TREND_EXECUTION_UNIT,
  P1_EVENT_WINDOW_TREND_PROFILE_REGISTRY,
  REGISTERED_TREND_PROFILES,
  type CompactTrendBundleOutput,
  type EventWindowTrendCompactOutput,
  type EventWindowTrendInput,
  type RegisteredTrendMetric,
  type TrendFactLineage,
} from './event-window-trend.js'

type JsonObject = Record<string, any>

export interface P1PageNodeExecutionReceipt {
  node_id: string
  goal_id: string
  execution_unit: string
  capability_ids: string[]
  status: 'passed' | 'reused_preflight' | 'failed'
  input_node_ids: string[]
  output_sha256: string | null
  output_hash_algorithm: 'sha256-json-stringify-v1'
  output: unknown | null
  evidence_refs: string[]
  error_code: string | null
}

export interface P1PageGoalExecution {
  result: P1SemanticGoalResult
  evidence: P1RuntimeV2Evidence[]
  node_receipts: P1PageNodeExecutionReceipt[]
}

function stableSha256(value: unknown): string {
  return createHash('sha256').update(JSON.stringify(value)).digest('hex')
}

function numberText(value: unknown): string {
  return typeof value === 'number' && Number.isFinite(value)
    ? value.toLocaleString('zh-CN')
    : '不可用'
}

function asObject(value: unknown): JsonObject {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as JsonObject
    : {}
}

function asArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : []
}

function pathValue(value: unknown, path: string): unknown {
  let current = value
  for (const segment of path.split('/').filter(Boolean)) {
    if (Array.isArray(current)) {
      const index = Number(segment)
      current = Number.isSafeInteger(index) ? current[index] : undefined
    } else if (current && typeof current === 'object') {
      current = (current as JsonObject)[segment]
    } else {
      return undefined
    }
  }
  return current
}

function evidence(
  source: P1RuntimeV2Evidence['source'],
  fieldPath: string,
  payload: unknown,
  binding: P1ConversationBinding,
  unit: string | null,
  observedAtUtc: string | null = null,
): P1RuntimeV2Evidence {
  const value = pathValue(payload, fieldPath)
  const nonScalar = value !== null
    && typeof value === 'object'
  return {
    evidence_ref: `${source}:${fieldPath}`,
    source,
    field_path: fieldPath,
    value: value === undefined || nonScalar
      ? null
      : value as string | number | boolean | null,
    value_state: nonScalar ? 'non_scalar_hashed' : 'scalar',
    ...(nonScalar ? {
      value_sha256: stableSha256(value),
      value_hash_algorithm: 'sha256-json-stringify-v1' as const,
    } : {}),
    unit,
    observed_at_utc: observedAtUtc,
    incident_id: binding.incident_id,
    publication_id: binding.publication_id,
    revision: binding.revision,
    collector_id: 'rrc25',
  }
}

function derivedEvidence(
  ref: string,
  value: string | number | boolean | null,
  binding: P1ConversationBinding,
  unit: string | null,
  observedAtUtc: string | null = null,
): P1RuntimeV2Evidence {
  return {
    evidence_ref: `derived:${ref}`,
    source: 'derived',
    field_path: ref,
    value,
    unit,
    observed_at_utc: observedAtUtc,
    incident_id: binding.incident_id,
    publication_id: binding.publication_id,
    revision: binding.revision,
    collector_id: 'rrc25',
  }
}

interface ExtremaResult {
  source_identity: ReturnType<typeof operatorSourceIdentity>
  metric: string
  unit: string
  first: number
  first_at_utc: string
  last: number
  last_at_utc: string
  minimum: number
  minimum_at_utc: string
  maximum: number
  maximum_at_utc: string
  difference: number
  net_change: number
  observed_point_count: number
  null_point_count: number
  source_evidence_refs: string[]
}

interface IntegratedTrendMetricResult {
  metric: RegisteredTrendMetric
  unit: string
  compact_chat_output: EventWindowTrendCompactOutput
  published_fact_lineage: TrendFactLineage[]
  audit_result_sha256: string
  audit_counts: {
    phase_count: number
    display_phase_count: number
    turning_point_count: number
    isolated_spike_count: number
    fact_lineage_count: number
  }
}

interface IntegratedTrendOperatorResult {
  schema_version: 'country_outage_p1_event_window_trend_execution_v1'
  operator: {
    execution_unit: typeof P1_EVENT_WINDOW_TREND_EXECUTION_UNIT
    capability_id: typeof P1_EVENT_WINDOW_TREND_CAPABILITY
    operator_id: 'event-window-trend'
    operator_version: '1.2.0'
    deterministic: true
    model_dependency: 'none'
  }
  source_identity: ReturnType<typeof operatorSourceIdentity>
  profile_registry_version: typeof P1_EVENT_WINDOW_TREND_PROFILE_REGISTRY
  metrics: IntegratedTrendMetricResult[]
  compact_bundle: CompactTrendBundleOutput | null
  source_evidence_refs: string[]
}

function operatorSourceIdentity(binding: P1ConversationBinding) {
  return {
    event_type: binding.event_type,
    incident_id: binding.incident_id,
    publication_id: binding.publication_id,
    revision: binding.revision,
    collector_id: binding.collector_id,
    cohort_id: binding.cohort_id,
    window_start_utc: binding.window_start_utc,
    window_end_utc: binding.window_end_utc,
    data_through: binding.data_through,
    is_final_in_data_range: binding.is_final_in_data_range,
    lifecycle_state: binding.lifecycle_state,
  }
}

function eventWindowTrendSourceIdentity(binding: P1ConversationBinding) {
  return {
    source_schema_version: 'country_outage_general_series_v1',
    event_type: 'country_outage' as const,
    incident_id: binding.incident_id,
    publication_id: binding.publication_id,
    publication_state: 'published' as const,
    revision: binding.revision,
    collector_id: 'rrc25' as const,
    cohort_id: binding.cohort_id,
    window_start_utc: binding.window_start_utc,
    window_end_utc: binding.window_end_utc,
    data_through: binding.data_through,
    is_final_in_data_range: binding.is_final_in_data_range,
    lifecycle_state: binding.lifecycle_state,
    observation_state: binding.observation_state,
    quality_state: binding.quality_state,
    missing_slot_count: binding.missing_slot_count,
  }
}

function trendInput(
  series: JsonObject,
  metric: RegisteredTrendMetric,
  binding: P1ConversationBinding,
): EventWindowTrendInput {
  const profile = getRegisteredTrendProfile(metric)
  return {
    source_identity: eventWindowTrendSourceIdentity(binding),
    metric,
    unit: profile.unit,
    series_semantics: profile.series_semantics,
    timestamps: asArray(series.timestamps).map(String),
    values: asArray(asObject(series.tracks)[metric]) as Array<number | null>,
    source_evidence_refs: {
      identity: [
        'resolution:/event_type',
        'resolution:/incident_id',
        'resolution:/publication_id',
        'resolution:/revision',
        'resolution:/collector_id',
        'resolution:/window_start_utc',
        'resolution:/window_end_utc',
        'resolution:/data_through',
      ],
      timestamps: 'series:/timestamps',
      values: `series:/tracks/${metric}`,
      metric_definition: `series:/track_definitions/${metric}`,
      trend_profile:
        `derived:/operators/event_window_trend/profiles/${metric}`,
    },
    trend_profile: profile,
  }
}

function executeEventWindowTrend(
  series: JsonObject,
  metrics: RegisteredTrendMetric[],
  binding: P1ConversationBinding,
): IntegratedTrendOperatorResult {
  const inputs = metrics.map((metric) => trendInput(series, metric, binding))
  const fullResults = inputs.map(analyzeEventWindowTrend)
  const metricResults = fullResults.map((item): IntegratedTrendMetricResult => {
    const publishedFactIds = new Set(item.compact_chat_output.fact_ids)
    return {
      metric: item.metric,
      unit: item.unit,
      compact_chat_output: item.compact_chat_output,
      published_fact_lineage: item.fact_lineage.filter((lineage) =>
        publishedFactIds.has(lineage.fact_id)
      ),
      audit_result_sha256: stableSha256(item),
      audit_counts: {
        phase_count: item.phase_sequence.length,
        display_phase_count: item.display_phase_sequence.length,
        turning_point_count: item.turning_points.length,
        isolated_spike_count: item.isolated_spikes.length,
        fact_lineage_count: item.fact_lineage.length,
      },
    }
  })
  const fixedMetrics = new Set(metrics)
  const compactBundle = fixedMetrics.size === 2
    && fixedMetrics.has('fixed_visible_ipv4_address_count')
    && fixedMetrics.has('fixed_visible_ipv6_slash48_count')
    ? analyzeCompactTrendBundle('fixed-ip-address-change-v1', inputs)
    : null
  return {
    schema_version: 'country_outage_p1_event_window_trend_execution_v1',
    operator: {
      execution_unit: P1_EVENT_WINDOW_TREND_EXECUTION_UNIT,
      capability_id: P1_EVENT_WINDOW_TREND_CAPABILITY,
      operator_id: 'event-window-trend',
      operator_version: '1.2.0',
      deterministic: true,
      model_dependency: 'none',
    },
    source_identity: operatorSourceIdentity(binding),
    profile_registry_version: P1_EVENT_WINDOW_TREND_PROFILE_REGISTRY,
    metrics: metricResults,
    compact_bundle: compactBundle,
    source_evidence_refs: [...new Set(metricResults.flatMap((item) =>
      item.compact_chat_output.evidence_refs
    ))],
  }
}

function evidenceForTrendRef(
  ref: string,
  series: JsonObject,
  binding: P1ConversationBinding,
  unit: string,
): P1RuntimeV2Evidence | null {
  if (ref.startsWith('series:')) {
    const path = ref.slice('series:'.length)
    const timestampMatch = /^\/timestamps\/(\d+)$/.exec(path)
    const valueMatch = /^\/tracks\/[^/]+\/(\d+)$/.exec(path)
    const indexText = timestampMatch?.[1] ?? valueMatch?.[1]
    const observedAtUtc = indexText === undefined
      ? null
      : String(asArray(series.timestamps)[Number(indexText)] ?? '') || null
    const evidenceUnit = path.startsWith('/timestamps')
      ? 'utc_timestamp'
      : path.startsWith('/track_definitions')
        ? 'metadata'
        : unit
    return evidence('series', path, series, binding, evidenceUnit, observedAtUtc)
  }
  if (ref.startsWith('derived:/operators/event_window_trend/profiles/')) {
    const metric = ref.slice(
      'derived:/operators/event_window_trend/profiles/'.length,
    ) as RegisteredTrendMetric
    return derivedEvidence(
      `/operators/event_window_trend/profiles/${metric}`,
      stableSha256(getRegisteredTrendProfile(metric)),
      binding,
      'sha256',
    )
  }
  return null
}

function renderIntegratedTrend(
  output: IntegratedTrendOperatorResult,
  series: JsonObject,
  binding: P1ConversationBinding,
): { text: string, evidence: P1RuntimeV2Evidence[] } {
  const text = output.compact_bundle?.body_zh
    ?? output.metrics.map((item) => item.compact_chat_output.body_zh).join('\n')
  const evidenceById = new Map<string, P1RuntimeV2Evidence>()
  for (const item of output.metrics) {
    for (const ref of item.compact_chat_output.evidence_refs) {
      const value = evidenceForTrendRef(ref, series, binding, item.unit)
      if (value) evidenceById.set(value.evidence_ref, value)
    }
    const derived = evidence(
      'derived',
      `/operators/event_window_trend/${item.metric}`,
      { operators: { event_window_trend: { [item.metric]: item } } },
      binding,
      'event_window_trend_result',
    )
    evidenceById.set(derived.evidence_ref, derived)
  }
  return {
    text: `${text}\n以上仅描述当前 publication 的 RRC25 事件窗口数值趋势，不用于判断恢复、原因或真实用户影响。`,
    evidence: [...evidenceById.values()],
  }
}

function metricUnit(metric: string, definition: JsonObject): string {
  if (typeof definition.unit === 'string' && definition.unit) {
    return definition.unit
  }
  if (metric === 'fixed_visible_ipv4_address_count'
    || metric === 'new_cumulative_ipv4_address_count'
    || metric === 'new_visible_ipv4_address_count') {
    return 'unique_ipv4_address'
  }
  if (metric.includes('ipv6') && metric.includes('slash48')) {
    return 'ipv6_slash48_equivalent'
  }
  if (metric.includes('asn')) return 'asn'
  return 'prefix'
}

function extrema(
  series: JsonObject,
  metric: string,
  binding: P1ConversationBinding,
): ExtremaResult {
  const timestamps = asArray(series.timestamps)
  const tracks = asObject(series.tracks)
  const values = asArray(tracks[metric])
  if (timestamps.length === 0 || values.length !== timestamps.length) {
    throw new P1ReadModelError(
      'invalid_series_shape',
      `${metric} 的 timestamps 与轨道长度不一致`,
    )
  }
  const observed = values
    .map((value, index) => ({ value, index }))
    .filter((item): item is { value: number, index: number } =>
      typeof item.value === 'number' && Number.isFinite(item.value)
    )
  if (observed.length === 0) {
    throw new P1ReadModelError(
      'metric_unavailable',
      `${metric} 全部为空，不能按 0 处理`,
    )
  }
  let minimum = observed[0]!
  let maximum = observed[0]!
  for (const item of observed.slice(1)) {
    if (item.value < minimum.value) minimum = item
    if (item.value > maximum.value) maximum = item
  }
  const first = observed[0]!
  const last = observed[observed.length - 1]!
  const definitions = asObject(
    series.track_definitions ?? series.definitions,
  )
  const definition = asObject(definitions[metric])
  const sourceEvidenceRefs = [
    `series:/tracks/${metric}`,
    'series:/timestamps',
  ]
  return {
    source_identity: operatorSourceIdentity(binding),
    metric,
    unit: metricUnit(metric, definition),
    first: first.value,
    first_at_utc: String(timestamps[first.index]),
    last: last.value,
    last_at_utc: String(timestamps[last.index]),
    minimum: minimum.value,
    minimum_at_utc: String(timestamps[minimum.index]),
    maximum: maximum.value,
    maximum_at_utc: String(timestamps[maximum.index]),
    difference: maximum.value - minimum.value,
    net_change: last.value - first.value,
    observed_point_count: observed.length,
    null_point_count: values.length - observed.length,
    source_evidence_refs: [
      ...sourceEvidenceRefs,
      `series:/track_definitions/${metric}/unit`,
      `series:/track_definitions/${metric}/definition`,
    ],
  }
}

function currentAtDataThrough(
  series: JsonObject,
  metric: string,
  binding: P1ConversationBinding,
): { value: number, observed_at_utc: string, unit: string } | null {
  const timestamps = asArray(series.timestamps)
  const index = timestamps.findIndex((item) => item === binding.data_through)
  if (index < 0) return null
  const tracks = asObject(series.tracks)
  const values = asArray(tracks[metric])
  const value = values[index]
  if (typeof value !== 'number' || !Number.isFinite(value)) return null
  const definitions = asObject(
    series.track_definitions ?? series.definitions,
  )
  return {
    value,
    observed_at_utc: String(timestamps[index]),
    unit: metricUnit(metric, asObject(definitions[metric])),
  }
}

function seriesPointEvidence(
  series: JsonObject,
  metric: string,
  atUtc: string,
  binding: P1ConversationBinding,
  unit: string,
): P1RuntimeV2Evidence[] {
  const timestamps = asArray(series.timestamps)
  const index = timestamps.findIndex((item) => item === atUtc)
  if (index < 0) return []
  const value = asArray(asObject(series.tracks)[metric])[index]
  if (typeof value !== 'number' || !Number.isFinite(value)) return []
  return [
    evidence(
      'series',
      `/tracks/${metric}/${index}`,
      series,
      binding,
      unit,
      atUtc,
    ),
    evidence(
      'series',
      `/timestamps/${index}`,
      series,
      binding,
      'utc_timestamp',
      atUtc,
    ),
  ]
}

function identityEvidence(
  binding: P1ConversationBinding,
): P1RuntimeV2Evidence[] {
  const payload = {
    event_type: binding.event_type,
    incident_id: binding.incident_id,
    publication_id: binding.publication_id,
    revision: binding.revision,
    collector_id: binding.collector_id,
    country_code: binding.country_code,
    window_start_utc: binding.window_start_utc,
    window_end_utc: binding.window_end_utc,
    data_through: binding.data_through,
    lifecycle_state: binding.lifecycle_state,
    is_final_in_data_range: binding.is_final_in_data_range,
    quality_state: binding.quality_state,
    missing_slot_count: binding.missing_slot_count,
  }
  return [
    evidence('resolution', '/event_type', payload, binding, null),
    evidence('resolution', '/incident_id', payload, binding, null),
    evidence('resolution', '/publication_id', payload, binding, null),
    evidence('resolution', '/revision', payload, binding, null),
    evidence('resolution', '/collector_id', payload, binding, null),
    evidence('resolution', '/country_code', payload, binding, null),
    evidence('resolution', '/window_start_utc', payload, binding, 'utc_timestamp'),
    evidence('resolution', '/window_end_utc', payload, binding, 'utc_timestamp'),
    evidence('resolution', '/data_through', payload, binding, 'utc_timestamp'),
    evidence('resolution', '/lifecycle_state', payload, binding, null),
    evidence('resolution', '/is_final_in_data_range', payload, binding, null),
    evidence('resolution', '/quality_state', payload, binding, null),
    evidence('resolution', '/missing_slot_count', payload, binding, 'state_point'),
  ]
}

function result(
  goal: P1UserGoal,
  answerability: P1SemanticGoalResult['answerability'],
  text: string,
  evidenceValues: P1RuntimeV2Evidence[],
  limitations: string[] = [],
): P1SemanticGoalResult {
  return {
    goal_id: goal.goal_id,
    requested_goal: goal.requested_goal,
    normalized_kind: goal.normalized_kind,
    answerability,
    text,
    evidence_refs: evidenceValues.map((item) => item.evidence_ref),
    limitations,
  }
}

function renderAddress(
  goal: P1UserGoal,
  binding: P1ConversationBinding,
  outputs: Map<string, unknown>,
): { text: string, evidence: P1RuntimeV2Evidence[], answerability: 'supported' | 'partial' } {
  const seriesNode = [...outputs.entries()].find(([key]) =>
    key.endsWith(':TOOL-03')
  )
  const series = asObject(seriesNode?.[1])
  const extremaValues = [...outputs.entries()]
    .filter(([key]) => key.endsWith(':OP-01'))
    .map(([, value]) => value as ExtremaResult)
  const metricNode = [...outputs.keys()].find((key) => key.endsWith(':TOOL-03'))
  const metrics = metricNode
    ? asArray(asObject(outputs.get(`${metricNode}:input`)).metrics)
      .filter((item): item is string => typeof item === 'string')
    : []
  const evidenceValues: P1RuntimeV2Evidence[] = []
  const lines: string[] = []
  let unavailable = false
  const analysisMode = String(goal.entities.analysis_mode ?? 'change_summary')
  if (analysisMode === 'event_window_trend') {
    const trendOutput = [...outputs.entries()].find(([key]) =>
      key.endsWith(`:${P1_EVENT_WINDOW_TREND_EXECUTION_UNIT}`)
    )?.[1] as IntegratedTrendOperatorResult | undefined
    if (trendOutput === undefined) {
      throw new P1ReadModelError(
        'trend_operator_result_missing',
        '事件窗口趋势目标缺少 OP-04 确定性结果',
      )
    }
    const rendered = renderIntegratedTrend(trendOutput, series, binding)
    return {
      text: rendered.text,
      evidence: rendered.evidence,
      answerability: 'supported',
    }
  }
  if (analysisMode === 'current_value') {
    for (const metric of metrics.filter((item) =>
      item === 'fixed_visible_ipv4_address_count'
      || item === 'fixed_visible_ipv6_slash48_count'
    )) {
      const current = currentAtDataThrough(series, metric, binding)
      const label = metric.includes('ipv4') ? 'IPv4' : 'IPv6'
      if (current === null) {
        unavailable = true
        lines.push(`${label} 固定 cohort 在 data-through 点为 null，当前值 unavailable，不向前回填，也不按 0 处理。`)
        continue
      }
      const index = asArray(series.timestamps).findIndex((item) =>
        item === current.observed_at_utc
      )
      const unit = current.unit === 'unique_ipv4_address'
        ? '个唯一 IPv4 地址' : '个 IPv6 /48 等价块'
      lines.push(
        `${label} 固定 cohort 在 data-through ${current.observed_at_utc} 的当前可见规模为 ${numberText(current.value)} ${unit}。`,
      )
      evidenceValues.push(
        evidence(
          'series', `/tracks/${metric}/${index}`, series, binding,
          current.unit, current.observed_at_utc,
        ),
        evidence(
          'series', `/track_definitions/${metric}/unit`,
          series, binding, 'metadata',
        ),
        evidence(
          'series', `/track_definitions/${metric}/definition`,
          series, binding, 'metadata',
        ),
        evidence(
          'series', `/timestamps/${index}`,
          series, binding, 'utc_timestamp', current.observed_at_utc,
        ),
      )
    }
  }
  for (const item of extremaValues) {
    const label = item.metric.includes('ipv4') ? 'IPv4' : 'IPv6'
    const unit = item.unit === 'unique_ipv4_address'
      ? '个唯一 IPv4 地址' : '个 IPv6 /48 等价块'
    const minimumToLast = item.last - item.minimum
    if (analysisMode === 'minimum_to_current') {
      if (binding.data_through !== null && item.last_at_utc !== binding.data_through) {
        unavailable = true
        lines.push(
          `${label} 固定 cohort 的窗口最小值为 ${numberText(item.minimum)} ${unit}（${item.minimum_at_utc}）；data-through 点为 null，不能用最近非空值补成当前值。`,
        )
      } else {
        lines.push(
          `${label} 固定 cohort 从窗口最小值 ${numberText(item.minimum)} ${unit}（${item.minimum_at_utc}）到 data-through ${numberText(item.last)} ${unit}（${item.last_at_utc}），增加 ${numberText(item.last - item.minimum)}。`,
        )
      }
    } else if (binding.data_through !== null && item.last_at_utc !== binding.data_through) {
      unavailable = true
      lines.push(
        `${label} 固定 cohort：起点为 ${numberText(item.first)} ${unit}（${item.first_at_utc}）；窗口最小值 ${numberText(item.minimum)}（${item.minimum_at_utc}），最大值 ${numberText(item.maximum)}（${item.maximum_at_utc}）。data-through 点为 null，当前值 unavailable；最近非空值 ${numberText(item.last)}（${item.last_at_utc}）只作历史观测，不向前回填为当前值。`,
      )
    } else {
      lines.push(
        `${label} 固定 cohort：从 ${numberText(item.first)} ${unit}（${item.first_at_utc}）变为 ${numberText(item.last)} ${unit}（${item.last_at_utc}），净变化 ${numberText(item.net_change)}；窗口最小值 ${numberText(item.minimum)}（${item.minimum_at_utc}），最大值 ${numberText(item.maximum)}（${item.maximum_at_utc}）；最低点到 data-through ${minimumToLast >= 0 ? '增加' : '减少'} ${numberText(Math.abs(minimumToLast))}。`,
      )
      if (goal.normalized_kind === 'address_family_compare') {
        lines.push(
          `${label} 窗口最大值到最小值的下降幅度为 ${numberText(item.difference)} ${unit}；这与首末净变化是两个不同量。`,
        )
      }
    }
    lines.push(
      `${label} 轨道共 ${numberText(item.observed_point_count + item.null_point_count)} 个时间槽：有效观测 ${numberText(item.observed_point_count)} 个，null ${numberText(item.null_point_count)} 个；null 未按 0 处理。`,
    )
    const points = [
      seriesPointEvidence(
        series, item.metric, item.first_at_utc, binding, item.unit,
      ),
      seriesPointEvidence(
        series, item.metric, item.last_at_utc, binding, item.unit,
      ),
      seriesPointEvidence(
        series, item.metric, item.minimum_at_utc, binding, item.unit,
      ),
      seriesPointEvidence(
        series, item.metric, item.maximum_at_utc, binding, item.unit,
      ),
    ].flat()
    evidenceValues.push(
      ...points,
      evidence(
        'series', `/tracks/${item.metric}`,
        series, binding, 'full_track_hashed',
      ),
      evidence(
        'series', '/timestamps',
        series, binding, 'full_timestamp_track_hashed',
      ),
      evidence(
        'series', `/track_definitions/${item.metric}/unit`,
        series, binding, 'metadata',
      ),
      evidence(
        'series', `/track_definitions/${item.metric}/definition`,
        series, binding, 'metadata',
      ),
      derivedEvidence(
        `/operators/series_extrema/${item.metric}`,
        JSON.stringify(item), binding, item.unit,
      ),
    )
  }
  const newMetrics = metrics.filter((metric) => metric.startsWith('new_'))
  const current = new Map<string, ReturnType<typeof currentAtDataThrough>>()
  for (const metric of newMetrics) {
    const value = currentAtDataThrough(series, metric, binding)
    current.set(metric, value)
    if (value === null) unavailable = true
    else {
      evidenceValues.push(
        evidence(
          'series',
          `/tracks/${metric}/${asArray(series.timestamps)
            .findIndex((item) => item === binding.data_through)}`,
          series,
          binding,
          value.unit,
          value.observed_at_utc,
        ),
        evidence(
          'series', `/track_definitions/${metric}/unit`,
          series, binding, 'metadata',
        ),
        evidence(
          'series', `/track_definitions/${metric}/definition`,
          series, binding, 'metadata',
        ),
        evidence(
          'series', `/timestamps/${asArray(series.timestamps)
            .findIndex((item) => item === binding.data_through)}`,
          series, binding, 'utc_timestamp', value.observed_at_utc,
        ),
      )
    }
  }
  for (const family of ['ipv4', 'ipv6'] as const) {
    const cumulative = current.get(`new_cumulative_${family}_prefix_count`)
    const visible = current.get(`new_visible_${family}_prefix_count`)
    const scale = current.get(family === 'ipv4'
      ? 'new_visible_ipv4_address_count'
      : 'new_visible_ipv6_slash48_count')
    if (cumulative === undefined && visible === undefined && scale === undefined) {
      continue
    }
    if (
      cumulative === null || visible === null || scale === null
      || cumulative === undefined || visible === undefined
      || scale === undefined
    ) {
      lines.push(
        `${family.toUpperCase()} 新前缀补充在 data-through 点存在 null，记为 unavailable，不向前回填，也不按 0 处理。`,
      )
      continue
    }
    lines.push(
      `${family.toUpperCase()} 新前缀补充（与固定 cohort 分开）：窗口累计出现 ${numberText(cumulative.value)} 条，data-through 时当前可见 ${numberText(visible.value)} 条，对应 ${numberText(scale.value)} ${family === 'ipv4' ? '个唯一 IPv4 地址' : '个 IPv6 /48 等价块'}。`,
    )
  }
  if (extremaValues.length === 2) {
    lines.push('IPv4 地址数与 IPv6 /48 等价块单位不同，只分轨比较各自变化，不生成合并总量或绝对严重度排序。')
    const comparison = [...outputs.entries()]
      .find(([key]) => key.endsWith(':OP-02'))?.[1]
    if (comparison !== undefined) {
      evidenceValues.push(derivedEvidence(
        '/operators/address_family_comparison',
        JSON.stringify(comparison), binding, 'unit_preserving_pair',
      ))
    }
  }
  lines.push(
    analysisMode === 'event_window_trend'
      ? '以上是当前 publication 观测窗口内的确定性时序趋势概括，不是正式历史趋势制品。'
      : analysisMode === 'current_value'
        ? '以上只回答 data-through 当前值，不扩写为首末、极值或历史趋势。'
        : analysisMode === 'minimum_to_current'
          ? '以上只比较窗口最小值与 data-through 状态；两点改善不等于恢复。'
      : '以上是当前 publication 观测窗口内的确定性变化概括。',
  )
  lines.push(
    `观测窗口 ${binding.window_start_utc} 至 ${binding.window_end_utc}，数据截至 ${binding.data_through ?? '未知'}；最低点后的改善不等于恢复。`,
  )
  if (
    goal.entities.address_family === 'ipv6'
    && goal.entities.include_new_prefixes === false
  ) {
    lines.push('本回答只使用 IPv6 fixed cohort，已显式排除 IPv4 和新出现前缀。')
  }
  return {
    text: lines.join('\n'),
    evidence: evidenceValues,
    answerability: unavailable ? 'partial' : 'supported',
  }
}

function renderGoal(
  goal: P1UserGoal,
  decision: P1GroundingDecision,
  binding: P1ConversationBinding,
  outputs: Map<string, unknown>,
): { result: P1SemanticGoalResult, evidence: P1RuntimeV2Evidence[] } {
  const overview = asObject(
    [...outputs.entries()].find(([key]) => key.endsWith(':TOOL-02'))?.[1],
  )
  const series = asObject(
    [...outputs.entries()].find(([key]) => key.endsWith(':TOOL-03'))?.[1],
  )
  const asns = asObject(
    [...outputs.entries()].find(([key]) => key.endsWith(':TOOL-04'))?.[1],
  )
  const paths = asObject(
    [...outputs.entries()].find(([key]) => key.endsWith(':TOOL-05'))?.[1],
  )
  const audit = asObject(
    [...outputs.entries()].find(([key]) => key.endsWith(':TOOL-06'))?.[1],
  )
  const baseLimit = 'RRC25 控制面观测不能单独证明全国中断、真实用户影响、原因、责任或恢复。'
  let values: P1RuntimeV2Evidence[] = []
  let text = ''
  let answerability = decision.answerability

  switch (goal.normalized_kind) {
    case 'event_summary':
      values = [
        evidence('overview', '/cohort/fixed_prefix_count', overview, binding, 'prefix'),
        evidence('overview', '/affected_as_count', overview, binding, 'asn'),
        evidence('overview', '/peaks/interrupted_prefix_count/value', overview, binding, 'prefix'),
        evidence('overview', '/current/interrupted_prefix_count', overview, binding, 'prefix', binding.data_through),
        evidence('overview', '/event/event_end_at_utc', overview, binding, 'utc_timestamp'),
      ]
      text = `RRC25 固定 cohort 有 ${numberText(pathValue(overview, '/cohort/fixed_prefix_count'))} 个前缀，窗口内累计涉及 ${numberText(pathValue(overview, '/affected_as_count'))} 个不同 AS；中断前缀峰值为 ${numberText(pathValue(overview, '/peaks/interrupted_prefix_count/value'))}，数据截止时为 ${numberText(pathValue(overview, '/current/interrupted_prefix_count'))}。事件结束时间未知。`
      break
    case 'event_identity':
      values = identityEvidence(binding)
      text = `当前回答绑定 event_type=${binding.event_type}、国家 ${binding.country_code}、incident ${binding.incident_id}、publication ${binding.publication_id}、revision ${binding.revision}、collector RRC25。`
      break
    case 'observation_window':
      values = identityEvidence(binding).filter((item) =>
        [
          '/window_start_utc', '/window_end_utc', '/data_through',
          '/lifecycle_state', '/is_final_in_data_range', '/quality_state',
          '/missing_slot_count',
        ]
          .includes(item.field_path)
      )
      text = `观测窗口为 ${binding.window_start_utc} 至 ${binding.window_end_utc}，数据截至 ${binding.data_through ?? '未知'}；is_final_in_data_range=${binding.is_final_in_data_range}，quality_state=${binding.quality_state}，missing_slot_count=${binding.missing_slot_count}。窗口和槽位完整不等于事件结束。`
      break
    case 'event_end_state':
      values = [
        evidence('overview', '/event/event_end_at_utc', overview, binding, 'utc_timestamp'),
        ...identityEvidence(binding).filter((item) =>
          ['/data_through', '/lifecycle_state'].includes(item.field_path)
        ),
      ]
      text = 'event_end_at_utc 为 null，表示当前证据不能确认事件结束时点；它不是 0，也不能用 data-through 或窗口末端代替。'
      break
    case 'detection_time':
    case 'true_outage_onset':
      values = [
        evidence('overview', '/event/detected_at_utc', overview, binding, 'utc_timestamp'),
        ...identityEvidence(binding).filter((item) =>
          item.field_path === '/window_start_utc'
        ),
      ]
      text = `页面记录的检测时间为 ${String(pathValue(overview, '/event/detected_at_utc'))}；观测窗口起点为 ${binding.window_start_utc}。两者不是同一时间，也都不能证明真实用户中断起点。`
      break
    case 'current_scope':
      values = [
        evidence('overview', '/cohort/fixed_prefix_count', overview, binding, 'prefix'),
        evidence('overview', '/affected_as_count', overview, binding, 'asn'),
        evidence('overview', '/route_interrupted_as_count', overview, binding, 'asn'),
        evidence('overview', '/current/invisible_direction_count', overview, binding, 'observation_direction', binding.data_through),
      ]
      text = `固定人口为 ${numberText(pathValue(overview, '/cohort/fixed_prefix_count'))} 个前缀；窗口内累计涉及 ${numberText(pathValue(overview, '/affected_as_count'))} 个不同 AS，其中 ${numberText(pathValue(overview, '/route_interrupted_as_count'))} 个曾出现整 AS 固定前缀同时中断；data-through 时不可见观察方向为 ${numberText(pathValue(overview, '/current/invisible_direction_count'))}。这些人口不能互换。`
      break
    case 'cumulative_affected_asn_count':
    case 'affected_asn_count':
      values = [
        evidence('overview', '/affected_as_count', overview, binding, 'asn'),
        evidence('overview', '/route_interrupted_as_count', overview, binding, 'asn'),
      ]
      text = `当前 publication 的观测窗口内，累计涉及 ${numberText(pathValue(overview, '/affected_as_count'))} 个不同受影响 AS，其中 ${numberText(pathValue(overview, '/route_interrupted_as_count'))} 个曾出现整 AS 固定前缀同时中断。这是窗口去重人口，不是某一时间槽的同时峰值。`
      break
    case 'current_prefix_state':
    case 'metric_followup':
      values = [
        evidence('overview', '/current/interrupted_prefix_count', overview, binding, 'prefix', binding.data_through),
        evidence('overview', '/current/completely_interrupted_prefix_count', overview, binding, 'prefix', binding.data_through),
        ...identityEvidence(binding).filter((item) => item.field_path === '/data_through'),
      ]
      text = `截至 data-through ${binding.data_through ?? '未知'}，固定 cohort 人口中中断前缀为 ${numberText(pathValue(overview, '/current/interrupted_prefix_count'))} 个，其中完全不可见 ${numberText(pathValue(overview, '/current/completely_interrupted_prefix_count'))} 个；这是数据截止状态，不是提问时实时网络，也不包含新出现前缀人口。`
      break
    case 'prefix_peak':
      values = [
        evidence('overview', '/peaks/interrupted_prefix_count/value', overview, binding, 'prefix'),
        evidence('overview', '/peaks/interrupted_prefix_count/state_point_utc', overview, binding, 'utc_timestamp'),
      ]
      text = `中断前缀峰值为 ${numberText(pathValue(overview, '/peaks/interrupted_prefix_count/value'))}，首次出现在 ${String(pathValue(overview, '/peaks/interrupted_prefix_count/state_point_utc'))}。`
      break
    case 'asn_peak':
      values = [
        evidence('overview', '/peaks/affected_asn_count/value', overview, binding, 'asn'),
        evidence('overview', '/peaks/affected_asn_count/state_point_utc', overview, binding, 'utc_timestamp'),
        evidence('overview', '/peaks/route_interrupted_asn_count/value', overview, binding, 'asn'),
        evidence('overview', '/peaks/route_interrupted_asn_count/state_point_utc', overview, binding, 'utc_timestamp'),
      ]
      text = `逐槽受影响 AS 峰值为 ${numberText(pathValue(overview, '/peaks/affected_asn_count/value'))}（${String(pathValue(overview, '/peaks/affected_asn_count/state_point_utc'))}）；逐槽整 AS 中断峰值为 ${numberText(pathValue(overview, '/peaks/route_interrupted_asn_count/value'))}（${String(pathValue(overview, '/peaks/route_interrupted_asn_count/state_point_utc'))}）。它们不等于窗口累计 AS 人口。`
      break
    case 'remaining_vs_peak':
    case 'recovery_status':
      values = [
        evidence('overview', '/peaks/interrupted_prefix_count/value', overview, binding, 'prefix'),
        evidence('overview', '/peaks/interrupted_prefix_count/state_point_utc', overview, binding, 'utc_timestamp'),
        evidence('overview', '/current/interrupted_prefix_count', overview, binding, 'prefix', binding.data_through),
      ]
      text = `中断前缀峰值为 ${numberText(pathValue(overview, '/peaks/interrupted_prefix_count/value'))}，data-through 时为 ${numberText(pathValue(overview, '/current/interrupted_prefix_count'))}。只能比较两个已观测状态点，不能证明中间连续性、事件结束或用户网络恢复。`
      break
    case 'fact_timeline': {
      const timeline = [...outputs.entries()]
        .find(([key]) => key.endsWith(':OP-03'))?.[1] as JsonObject | undefined
      const facts = asArray(timeline?.ordered_fact_nodes)
      values = facts.flatMap((item) => {
        const row = asObject(item)
        const refs = [...new Set([
          String(row.evidence_ref ?? ''),
          String(row.time_evidence_ref ?? ''),
        ])]
        return refs.flatMap((sourceRef) => {
          const [source, fieldPath] = sourceRef.split(':', 2)
          if (
            (source !== 'resolution' && source !== 'overview')
            || typeof fieldPath !== 'string'
            || !fieldPath
          ) return []
          const sourceValue = source === 'resolution' ? {
            window_start_utc: binding.window_start_utc,
            data_through: binding.data_through,
          } : overview
          return [evidence(
            source,
            fieldPath,
            sourceValue,
            binding,
            sourceRef === row.time_evidence_ref
              ? 'utc_timestamp'
              : String(row.unit ?? 'fact'),
            typeof row.at_utc === 'string' ? row.at_utc : null,
          )]
        })
      })
      values.push(derivedEvidence(
        '/operators/fact_timeline',
        JSON.stringify(timeline), binding, 'ordered_fact_timeline',
      ))
      text = facts.map((item) => {
        const row = asObject(item)
        return `${row.at_utc ?? '未知时点'}：${row.label ?? row.kind} ${row.value ?? ''}`
      }).join('\n')
      text += '\n以上是按时点排序的观测事实，不是因果链；事件结束仍未知。'
      break
    }
    case 'address_family_change':
    case 'address_family_compare':
    case 'new_prefix_resources':
    case 'new_prefix_state': {
      const rendered = renderAddress(goal, binding, outputs)
      values = rendered.evidence
      text = rendered.text
      answerability = rendered.answerability
      break
    }
    case 'metric_semantics': {
      const definitions = asObject(series.track_definitions ?? series.definitions)
      const metrics = scalarMetricList(goal)
      for (const metric of metrics) {
        values.push(
          evidence(
            'series', `/track_definitions/${metric}/unit`,
            series, binding, 'metadata',
          ),
          evidence(
            'series', `/track_definitions/${metric}/definition`,
            series, binding, 'metadata',
          ),
        )
      }
      text = metrics.map((metric) => {
        const definition = asObject(definitions[metric])
        const population = metric.startsWith('fixed_visible_')
          ? 'fixed cohort' : '该指标登记人口'
        return `${metric}：${String(definition.definition ?? '定义不可用')}；统计人口 ${population}；单位 ${String(definition.unit ?? '不可用')}。`
      }).join('\n')
      break
    }
    case 'missing_value_semantics': {
      const metrics = scalarMetricList(goal)
      for (const metric of metrics) {
        values.push(
          evidence(
            'series', `/tracks/${metric}`,
            series, binding, 'full_track_hashed',
          ),
          evidence(
            'series', `/track_definitions/${metric}/definition`,
            series, binding, 'metadata',
          ),
        )
      }
      text = metrics.map((metric) =>
        `${metric}：null 表示该状态点未观测或未知，不是 0；只有原始轨道明确给出数值 0 才是 0；整条轨道全为 null 或轨道缺失时必须标为 unavailable，不能补 0。`
      ).join('\n')
      break
    }
    case 'affected_asn_list':
    case 'top_affected_asns':
    case 'asn_detail': {
      const items = asArray(asns.items).map(asObject)
      values = [
        evidence('asns', '/total', asns, binding, 'asn'),
        evidence('asns', '/page', asns, binding, 'page'),
        evidence('asns', '/page_size', asns, binding, 'row'),
        evidence('asns', '/query', asns, binding, 'query'),
        evidence('asns', '/classification', asns, binding, 'classification'),
        evidence('asns', '/sort', asns, binding, 'sort'),
        ...items.slice(0, 10).map((_, index) =>
          evidence('asns', `/items/${index}/asn`, asns, binding, 'asn')
        ),
      ]
      if (goal.normalized_kind === 'asn_detail') {
        const requested = numericEntity(goal.entities.asn)
        const item = items.find((row) => row.asn === requested)
        if (!item) {
          answerability = 'invalid_data'
          text = `当前 publication 的查询结果中没有 AS${requested ?? '未知'}；无结果不等于其前缀中断数为 0。`
        } else {
          const index = items.indexOf(item)
          for (const [field, unit] of [
            ['as_name', 'metadata'],
            ['event_classification', 'classification'],
            ['fixed_prefix_count', 'prefix'],
            ['peak_complete_prefix_count', 'prefix'],
            ['path_downstream_asn_count', 'asn'],
            ['concurrent_downstream_asn_count', 'asn'],
          ] as const) {
            values.push(evidence(
              'asns', `/items/${index}/${field}`, asns, binding, unit,
            ))
          }
          text = `AS${item.asn}（${item.as_name ?? '名称未知'}）在当前 publication 中分类为 ${item.event_classification}，固定前缀 ${numberText(item.fixed_prefix_count)} 个，峰值完全中断前缀 ${numberText(item.peak_complete_prefix_count)} 个，观测到的路径下游 AS 数为 ${numberText(item.path_downstream_asn_count)}，并发状态路径下游 AS 数为 ${numberText(item.concurrent_downstream_asn_count)}。这不表示原因或责任。`
        }
      } else {
        text = `查询条件 query=${JSON.stringify(asns.query ?? '')}、classification=${String(asns.classification)}、sort=${String(asns.sort)}、page=${numberText(asns.page)}、page_size=${numberText(asns.page_size)}；共 ${numberText(asns.total)} 个结果。样例：${items.slice(0, 5).map((item) => `AS${item.asn}(${item.as_name ?? '名称未知'})`).join('、') || '本页为空'}。本页为空不能推出总体没有受影响 AS。`
      }
      break
    }
    case 'path_association':
    case 'path_sample': {
      const items = asArray(paths.items).map(asObject)
      values = [
        evidence('paths', '/total', paths, binding, 'observed_relation'),
        evidence('paths', '/relationship_semantics', paths, binding, null),
        evidence('paths', '/affected_asn', paths, binding, 'asn'),
        evidence('paths', '/scope', paths, binding, 'scope'),
        evidence('paths', '/query', paths, binding, 'query'),
        evidence('paths', '/page', paths, binding, 'page'),
        evidence('paths', '/page_size', paths, binding, 'row'),
      ]
      if (items.length === 0) {
        answerability = 'partial'
        text = '当前查询没有可核对的路径关联或样本，记为 unavailable；不能据此推出“没有关系”。'
      } else {
        const item = items[0]!
        const samples = asArray(item.path_samples).map(asObject)
        values.push(
          evidence('paths', '/items/0/affected_asn', paths, binding, 'asn'),
          evidence('paths', '/items/0/downstream_asn', paths, binding, 'asn'),
        )
        if (goal.normalized_kind === 'path_sample' && samples.length > 0) {
          values.push(
            evidence('paths', '/items/0/path_samples/0/prefix', paths, binding, 'prefix'),
            evidence('paths', '/items/0/path_samples/0/as_path_canonical', paths, binding, 'as_path'),
            evidence('paths', '/items/0/path_samples/0/independent_peer_asns/0', paths, binding, 'peer_asn'),
          )
          text = `观测关联：AS${item.affected_asn} 与下游 AS${item.downstream_asn} 在 RRC25 路径中有序共同出现；样本前缀 ${samples[0]!.prefix}，AS_PATH ${samples[0]!.as_path_canonical}，独立观测 peer ASN 为 ${JSON.stringify(samples[0]!.independent_peer_asns ?? [])}。每行最多返回 3 条样本，有限样本不是完整网络拓扑；peer 只标识独立观测来源，样本只能证明有序共同出现，不能证明依赖、传播方向、原因或责任。`
        } else {
          text = `当前查询共 ${numberText(paths.total)} 条观测关联；样例为 AS${item.affected_asn} 与下游 AS${item.downstream_asn}，观测路径数 ${numberText(item.observed_path_count)}。这不是依赖或因果关系。`
        }
      }
      break
    }
    case 'data_source':
      values = [
        evidence('audit', '/run_id', audit, binding, null),
        evidence('audit', '/dataset_id', audit, binding, null),
      ]
      text = `数据来自当前 publication 绑定的 RRC25 country-outage general read model；collector=${binding.collector_id}，publication=${binding.publication_id}，revision=${binding.revision}，dataset=${String(audit.dataset_id)}，run=${String(audit.run_id)}。`
      break
    case 'evidence_identity':
      values = [
        evidence('audit', '/run_id', audit, binding, null),
        evidence('audit', '/dataset_id', audit, binding, null),
        evidence('audit', '/implementation_id', audit, binding, null),
        evidence('audit', '/manifest_sha256', audit, binding, 'sha256'),
        evidence('audit', '/event_content_sha256', audit, binding, 'sha256'),
      ]
      text = `审计身份：dataset=${String(audit.dataset_id)}，run=${String(audit.run_id)}，数据 implementation=${String(audit.implementation_id)}，manifest=${String(audit.manifest_sha256)}，event_content=${String(audit.event_content_sha256)}。这些是 RRC25 read-model 数据制品身份，不是当前 Web 服务发布 commit，也不表示服务器文件已被独立验签。`
      break
    case 'publication_identity':
      values = identityEvidence(binding).filter((item) =>
        ['/event_type', '/incident_id', '/publication_id', '/revision', '/collector_id']
          .includes(item.field_path)
      )
      text = `publication 身份为 ${binding.publication_id}，revision=${binding.revision}，collector=${binding.collector_id}，incident=${binding.incident_id}；本轮不会跨 publication 或来源拼接。`
      break
    case 'event_switch':
      values = identityEvidence(binding).filter((item) =>
        ['/event_type', '/incident_id', '/publication_id', '/revision', '/collector_id']
          .includes(item.field_path)
      )
      text = `已验证并原子切换到 publication ${binding.publication_id}（revision ${binding.revision}、collector ${binding.collector_id}）；旧事件回答保留原身份，地址族、ASN、指标和待澄清上下文已清除。`
      break
    case 'data_completeness':
      values = [
        evidence('overview', '/quality_state', overview, binding, null),
        evidence('overview', '/missing_slot_count', overview, binding, 'state_point'),
        evidence('overview', '/is_final_in_data_range', overview, binding, null),
        evidence('audit', '/manifest_sha256', audit, binding, 'sha256'),
      ]
      text = `当前数据质量状态为 ${String(overview.quality_state)}，缺槽 ${numberText(overview.missing_slot_count)}；is_final_in_data_range=${String(overview.is_final_in_data_range)}。数据范围完整不等于事件结束或恢复。`
      break
    case 'rrc25_proof_boundary':
      values = [
        evidence('overview', '/semantic_boundary', overview, binding, null),
        evidence('audit', '/causal_boundary', audit, binding, null),
        ...identityEvidence(binding).filter((item) =>
          item.field_path === '/collector_id'
        ),
      ]
      text = '该页能证明同一 publication 下 RRC25 观察到的 BGP 控制面可见性、AS 和路径关联事实；不能单独证明全国用户中断、真实可达性、原因、责任或恢复。'
      break
    default:
      text = '已执行登记能力，但回答装配器未登记该用户结果。'
      answerability = 'invalid_data'
  }
  return {
    result: result(
      goal,
      answerability,
      text,
      values,
      [baseLimit],
    ),
    evidence: values,
  }
}

function numericEntity(value: unknown): number | null {
  if (typeof value === 'number' && Number.isSafeInteger(value)) return value
  if (typeof value === 'string' && /^[1-9][0-9]*$/.test(value)) {
    const parsed = Number(value)
    return Number.isSafeInteger(parsed) ? parsed : null
  }
  return null
}

function scalarMetricList(goal: P1UserGoal): string[] {
  const value = goal.entities.metric ?? goal.entities.metrics
  return typeof value === 'string'
    ? value.split(',').map((item) => item.trim()).filter(Boolean)
    : []
}

function factTimeline(
  overview: JsonObject,
  binding: P1ConversationBinding,
): JsonObject {
  const sourceIdentity = operatorSourceIdentity(binding)
  const facts: JsonObject[] = [
    {
      fact_id: 'fact-window-start',
      kind: 'window_start',
      at_utc: binding.window_start_utc,
      label: '观测窗口开始',
      value: binding.window_start_utc,
      unit: 'UTC',
      source_identity: sourceIdentity,
      evidence_ref: 'resolution:/window_start_utc',
      time_evidence_ref: 'resolution:/window_start_utc',
    },
    {
      fact_id: 'fact-detected',
      kind: 'detected',
      at_utc: pathValue(overview, '/event/detected_at_utc'),
      label: '页面检测时间',
      value: pathValue(overview, '/event/detected_at_utc'),
      unit: 'UTC',
      source_identity: sourceIdentity,
      evidence_ref: 'overview:/event/detected_at_utc',
      time_evidence_ref: 'overview:/event/detected_at_utc',
    },
  ]
  for (const [metric, kind, label, unit] of [
    ['interrupted_prefix_count', 'interrupted_prefix_peak', '中断前缀峰值', 'prefix'],
    ['completely_interrupted_prefix_count', 'completely_interrupted_prefix_peak', '完全中断前缀峰值', 'prefix'],
    ['affected_asn_count', 'affected_asn_peak', '逐槽受影响 AS 峰值', 'asn'],
    ['route_interrupted_asn_count', 'route_interrupted_asn_peak', '逐槽整 AS 中断峰值', 'asn'],
  ] as const) {
    facts.push({
      fact_id: `fact-${kind}`,
      kind,
      at_utc: pathValue(overview, `/peaks/${metric}/state_point_utc`),
      label,
      value: pathValue(overview, `/peaks/${metric}/value`),
      unit,
      source_identity: sourceIdentity,
      evidence_ref: `overview:/peaks/${metric}/value`,
      time_evidence_ref: `overview:/peaks/${metric}/state_point_utc`,
    })
  }
  facts.push({
    fact_id: 'fact-data-through',
    kind: 'data_through',
    at_utc: binding.data_through,
    label: '数据截止',
    value: pathValue(overview, '/current/interrupted_prefix_count'),
    unit: 'prefix',
    source_identity: sourceIdentity,
    evidence_ref: 'overview:/current/interrupted_prefix_count',
    time_evidence_ref: 'resolution:/data_through',
  })
  facts.sort((left, right) =>
    String(left.at_utc ?? '').localeCompare(String(right.at_utc ?? ''))
  )
  return {
    source_identity: sourceIdentity,
    ordered_fact_nodes: facts,
    terminal_unknown: {
      reason: 'event_end_unknown',
      event_end_at_utc: null,
    },
    causal_edges: 'forbidden',
  }
}

export class P1PageCapabilityExecutor {
  constructor(private readonly provider: P1PageCapabilityReadProvider) {}

  async execute(
    binding: P1ConversationBinding,
    goal: P1UserGoal,
    decision: P1GroundingDecision,
    nodes: P1GroundingNode[],
    signal?: AbortSignal,
  ): Promise<P1PageGoalExecution> {
    const outputs = new Map<string, unknown>()
    const receipts: P1PageNodeExecutionReceipt[] = []
    const overviewCache = new Map<string, JsonObject>()
    try {
      for (const node of nodes) {
        signal?.throwIfAborted()
        let output: unknown
        let status: P1PageNodeExecutionReceipt['status'] = 'passed'
        if (node.execution_unit === 'TOOL-01') {
          output = binding
          status = 'reused_preflight'
        } else if (node.execution_unit === 'TOOL-02') {
          const key = stableSha256(node.inputs)
          output = overviewCache.get(key)
          if (output === undefined) {
            output = await this.provider.readOverview(binding, signal)
            overviewCache.set(key, output as JsonObject)
          } else {
            status = 'reused_preflight'
          }
        } else if (node.execution_unit === 'TOOL-03') {
          output = await this.provider.readSeries(
            binding,
            asArray(node.inputs.metrics)
              .filter((item): item is string => typeof item === 'string'),
            signal,
          )
        } else if (node.execution_unit === 'TOOL-04') {
          output = await this.provider.readAsns(
            binding,
            {
              asn: numericEntity(node.inputs.asn),
              query: String(node.inputs.query),
              classification: node.inputs.classification as
                'all' | 'affected' | 'route_interrupted',
              sort: node.inputs.sort as 'default' | 'asn_asc',
              page: Number(node.inputs.page),
              pageSize: Number(node.inputs.page_size),
            },
            signal,
          )
        } else if (node.execution_unit === 'TOOL-05') {
          output = await this.provider.readPaths(
            binding,
            {
              affectedAsn: numericEntity(node.inputs.affected_asn),
              scope: node.inputs.scope as 'all' | 'concurrent',
              query: String(node.inputs.query),
              page: Number(node.inputs.page),
              pageSize: Number(node.inputs.page_size),
            },
            signal,
          )
        } else if (node.execution_unit === 'TOOL-06') {
          output = await this.provider.readAudit(binding, signal)
        } else if (node.execution_unit === 'OP-01') {
          const source = asObject(outputs.get(String(node.inputs.source_node_id)))
          output = extrema(source, String(node.inputs.metric), binding)
        } else if (node.execution_unit === 'OP-02') {
          const ipv4 = outputs.get(
            String(node.inputs.ipv4_extrema_node_id),
          ) as ExtremaResult
          const ipv6 = outputs.get(
            String(node.inputs.ipv6_extrema_node_id),
          ) as ExtremaResult
          output = {
            source_identity: operatorSourceIdentity(binding),
            ipv4,
            ipv6,
            comparison: 'separate_units_only',
            combined_absolute_total: 'forbidden',
            source_evidence_refs: [
              `derived:/operators/series_extrema/${ipv4.metric}`,
              `derived:/operators/series_extrema/${ipv6.metric}`,
            ],
          }
        } else if (node.execution_unit === 'OP-03') {
          const sourceIds = asArray(node.inputs.source_node_ids)
            .filter((item): item is string => typeof item === 'string')
          const overview = asObject(outputs.get(sourceIds[0]!))
          output = factTimeline(overview, binding)
        } else if (node.execution_unit === P1_EVENT_WINDOW_TREND_EXECUTION_UNIT) {
          const source = asObject(outputs.get(String(node.inputs.source_node_id)))
          const metrics = asArray(node.inputs.metrics)
            .filter((item): item is RegisteredTrendMetric =>
              typeof item === 'string'
              && Object.hasOwn(REGISTERED_TREND_PROFILES, item)
            )
          if (metrics.length !== asArray(node.inputs.metrics).length) {
            throw new P1ReadModelError(
              'trend_metric_not_registered',
              'OP-04 包含未登记趋势指标',
            )
          }
          output = executeEventWindowTrend(source, metrics, binding)
        } else {
          throw new P1ReadModelError(
            'execution_unit_not_registered',
            `未登记执行单元 ${node.execution_unit}`,
          )
        }
        outputs.set(node.node_id, output)
        outputs.set(`${node.node_id}:${node.execution_unit}`, output)
        outputs.set(`${node.node_id}:${node.execution_unit}:input`, node.inputs)
        receipts.push({
          node_id: node.node_id,
          goal_id: goal.goal_id,
          execution_unit: node.execution_unit,
          capability_ids: [...node.capability_ids],
          status,
          input_node_ids: [...node.depends_on],
          output_sha256: stableSha256(output),
          output_hash_algorithm: 'sha256-json-stringify-v1',
          output,
          evidence_refs: [],
          error_code: null,
        })
      }
      const rendered = renderGoal(goal, decision, binding, outputs)
      const bindingEvidence = identityEvidence(binding)
      const existingEvidence = new Set(
        rendered.evidence.map((item) => item.evidence_ref),
      )
      for (const item of bindingEvidence) {
        if (!existingEvidence.has(item.evidence_ref)) {
          rendered.evidence.push(item)
          rendered.result.evidence_refs.push(item.evidence_ref)
        }
      }
      for (const receipt of receipts) {
        const source = receipt.execution_unit === 'TOOL-01' ? 'resolution'
          : receipt.execution_unit === 'TOOL-02' ? 'overview'
            : receipt.execution_unit === 'TOOL-03' ? 'series'
              : receipt.execution_unit === 'TOOL-04' ? 'asns'
                : receipt.execution_unit === 'TOOL-05' ? 'paths'
                  : receipt.execution_unit === 'TOOL-06' ? 'audit'
                    : 'derived'
        if (receipt.execution_unit === 'OP-01') {
          const output = asObject(outputs.get(receipt.node_id))
          receipt.evidence_refs = [
            ...asArray(output.source_evidence_refs)
              .filter((item): item is string => typeof item === 'string'),
            `derived:/operators/series_extrema/${String(output.metric)}`,
          ]
        } else if (receipt.execution_unit === 'OP-02') {
          const output = asObject(outputs.get(receipt.node_id))
          receipt.evidence_refs = [
            ...asArray(output.source_evidence_refs)
              .filter((item): item is string => typeof item === 'string'),
            'derived:/operators/address_family_comparison',
          ]
        } else if (receipt.execution_unit === 'OP-03') {
          const output = asObject(outputs.get(receipt.node_id))
          receipt.evidence_refs = [
            ...asArray(output.ordered_fact_nodes)
              .map(asObject)
              .flatMap((item) => [
                item.evidence_ref,
                item.time_evidence_ref,
              ])
              .filter((item): item is string => typeof item === 'string'),
            'derived:/operators/fact_timeline',
          ].filter((item, index, all) => all.indexOf(item) === index)
        } else if (
          receipt.execution_unit === P1_EVENT_WINDOW_TREND_EXECUTION_UNIT
        ) {
          const output = asObject(outputs.get(receipt.node_id))
          receipt.evidence_refs = [
            ...asArray(output.source_evidence_refs)
              .filter((item): item is string => typeof item === 'string'),
            ...asArray(output.metrics).map(asObject).map((item) =>
              `derived:/operators/event_window_trend/${String(item.metric)}`
            ),
          ].filter((item, index, all) => all.indexOf(item) === index)
        } else {
          receipt.evidence_refs = rendered.evidence
            .filter((item) => item.source === source)
            .map((item) => item.evidence_ref)
        }
      }
      return {
        result: rendered.result,
        evidence: rendered.evidence,
        node_receipts: receipts,
      }
    } catch (error) {
      const code = error instanceof P1ReadModelError
        ? error.code
        : error instanceof Error ? error.name : 'execution_failed'
      const failedNode = nodes[receipts.length]
      if (failedNode) {
        receipts.push({
          node_id: failedNode.node_id,
          goal_id: goal.goal_id,
          execution_unit: failedNode.execution_unit,
          capability_ids: [...failedNode.capability_ids],
          status: 'failed',
          input_node_ids: [...failedNode.depends_on],
          output_sha256: null,
          output_hash_algorithm: 'sha256-json-stringify-v1',
          output: null,
          evidence_refs: [],
          error_code: code,
        })
      }
      return {
        result: result(
          goal,
          'invalid_data',
          `该子目标执行失败（${code}），未发布未经验证的事实。`,
          [],
          ['失败子目标不写入 EvidenceState 或对话槽位。'],
        ),
        evidence: [],
        node_receipts: receipts,
      }
    }
  }
}
