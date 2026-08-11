import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import {
  EventWindowTrendError,
  REGISTERED_TREND_PROFILES,
  analyzeCompactTrendBundle,
  analyzeEventWindowTrend,
  analyzeEventWindowTrendCompact,
  analyzeMultiTrackTrend,
  getRegisteredTrendProfile,
  type EventWindowTrendInput,
  type EventWindowTrendCompactOutput,
  type EventWindowTrendResult,
  type EventWindowTrendSourceIdentity,
  type RegisteredTrendMetric,
} from '../src/chat/event-window-trend.js'

const BASE_EPOCH = Date.parse('2025-01-01T00:00:00Z')
const INTERVAL_MS = 300_000

function timestamp(offsetMs: number): string {
  return new Date(BASE_EPOCH + offsetMs).toISOString()
}

const COMPACT_INTERNAL_LEAK = /(?:metric[_ ]?id|change_threshold|threshold|阈值|审计|转折|毫秒|fact[_ ]?id|phase_sequence|analysis_value|profile[_ ]?id)/i

function assertCompactOutput(
  output: EventWindowTrendCompactOutput,
  metric: RegisteredTrendMetric,
): void {
  const visibleText = [
    output.headline_zh,
    output.body_zh,
    ...output.cards.flatMap((card) => [card.label_zh, card.text_zh]),
    ...output.limitations.map((item) => item.text_zh),
  ].join('\n')
  assert.ok(output.sentence_count >= 1 && output.sentence_count <= 3)
  assert.ok(output.character_count <= 220)
  assert.equal(output.character_count, Array.from(output.body_zh).length)
  assert.doesNotMatch(visibleText, COMPACT_INTERNAL_LEAK)
  assert.doesNotMatch(visibleText, new RegExp(metric, 'i'))
  assert.ok(output.cards.length >= 4)
  assert.ok(output.cards.every((card) =>
    card.fact_ids.length > 0 && card.evidence_refs.length > 0))
  assert.ok(output.limitations.length >= 2)
  assert.ok(output.limitations.every((item) =>
    item.fact_ids.length > 0 && item.evidence_refs.length > 0))
  assert.ok(output.limitations.some((item) => /单位|口径/.test(item.text_zh)))
  assert.ok(output.limitations.some((item) =>
    /恢复/.test(item.text_zh) && /原因/.test(item.text_zh)
      && /用户影响/.test(item.text_zh)))
}

function identity(timestamps: string[]): EventWindowTrendSourceIdentity {
  return {
    source_schema_version: 'synthetic_series_v1',
    event_type: 'country_outage',
    incident_id: 'synthetic-incident',
    publication_id: 'synthetic-publication',
    publication_state: 'published',
    revision: 1,
    collector_id: 'rrc25',
    cohort_id: 'synthetic-cohort',
    window_start_utc: timestamps[0]!,
    window_end_utc: timestamps[timestamps.length - 1]!,
    data_through: timestamps[timestamps.length - 1]!,
    is_final_in_data_range: false,
    lifecycle_state: 'event_end_unknown',
    observation_state: 'synthetic_complete',
    quality_state: 'complete',
    missing_slot_count: 0,
  }
}

function input(
  metric: RegisteredTrendMetric,
  values: Array<number | null>,
  offsetsMs: number[] = values.map((_, index) => index * INTERVAL_MS),
): EventWindowTrendInput {
  const profile = getRegisteredTrendProfile(metric)
  const timestamps = offsetsMs.map(timestamp)
  return {
    source_identity: identity(timestamps),
    metric,
    unit: profile.unit,
    series_semantics: profile.series_semantics,
    timestamps,
    values,
    source_evidence_refs: {
      identity: ['fixture:/resolver#identity', 'fixture:/overview#identity'],
      timestamps: 'fixture:/series#/timestamps',
      values: `fixture:/series#/tracks/${metric}`,
      metric_definition: `fixture:/series#/track_definitions/${metric}`,
      trend_profile: `contract:/trend-profiles#/${metric}`,
    },
    trend_profile: profile,
  }
}

function result(
  values: Array<number | null>,
  metric: RegisteredTrendMetric = 'affected_asn_count',
  offsetsMs?: number[],
): EventWindowTrendResult {
  return analyzeEventWindowTrend(input(metric, values, offsetsMs))
}

function expectError(
  action: () => unknown,
  code: EventWindowTrendError['code'],
): void {
  assert.throws(action, (error: unknown) => {
    assert.ok(error instanceof EventWindowTrendError)
    assert.equal(error.code, code)
    return true
  })
}

function collectFactIds(value: unknown, resultIds: string[] = []): string[] {
  if (Array.isArray(value)) {
    for (const item of value) collectFactIds(item, resultIds)
  } else if (value !== null && typeof value === 'object') {
    for (const [key, item] of Object.entries(value as Record<string, unknown>)) {
      if (key === 'fact_id' && typeof item === 'string') resultIds.push(item)
      collectFactIds(item, resultIds)
    }
  }
  return resultIds
}

test('登记合同的默认/Profile 与 TypeScript 注册表无漂移', () => {
  const registry = JSON.parse(readFileSync(
    new URL(
      '../../../contracts/agent/country-outage-p1-trend-operator/v1/trend-profiles.json',
      import.meta.url,
    ),
    'utf8',
  )) as {
    defaults: Record<string, Record<string, number>>
    profiles: Record<string, {
      unit: string
      series_semantics: 'stock' | 'cumulative' | 'current_supplement'
      semantic_role: string
      primary_fact: string
      profile_version: number
      display_label_zh: string
      unit_label_zh: string
      base_profile_id: 'stock-default-v1' | 'cumulative-default-v1'
      overrides: Record<string, number>
    }>
  }
  assert.deepEqual(Object.keys(registry.profiles).sort(),
    Object.keys(REGISTERED_TREND_PROFILES).sort())
  for (const [metric, contractProfile] of Object.entries(registry.profiles)) {
    const implemented = getRegisteredTrendProfile(metric as RegisteredTrendMetric)
    assert.equal(implemented.unit, contractProfile.unit)
    assert.equal(implemented.series_semantics, contractProfile.series_semantics)
    assert.equal(implemented.semantic_role, contractProfile.semantic_role)
    assert.equal(implemented.primary_fact, contractProfile.primary_fact)
    assert.equal(implemented.profile_version, contractProfile.profile_version)
    assert.equal(implemented.display_label_zh, contractProfile.display_label_zh)
    assert.equal(implemented.unit_label_zh, contractProfile.unit_label_zh)
    assert.equal(implemented.base_profile_id, contractProfile.base_profile_id)
    assert.deepEqual(implemented.parameters, {
      ...registry.defaults[contractProfile.base_profile_id],
      ...contractProfile.overrides,
    })
  }
})

test('合成 Oracle 可逐案执行并命中冻结的产品语义', () => {
  const oracle = JSON.parse(readFileSync(
    new URL(
      '../../../contracts/agent/country-outage-p1-trend-operator/v1/synthetic-oracle.json',
      import.meta.url,
    ),
    'utf8',
  )) as {
    default_interval_ms: number
    cases: Array<{
      case_id: string
      metric: RegisteredTrendMetric
      values: Array<number | null>
      offsets_ms?: number[]
      expected: Record<string, unknown>
    }>
    multi_track_cases: Array<{
      case_id: string
      tracks: Partial<Record<RegisteredTrendMetric, number[]>>
      expected: {
        kind: string
        relation?: string
        from_index: number
        to_index: number
      }
    }>
    compact_cases: Array<{
      case_id: string
      metric: RegisteredTrendMetric
      values: Array<number | null>
      expected: {
        semantic_role: string
        required_cards: string[]
        required_limitations?: string[]
        body_contains: string[]
        body_excludes?: string[]
      }
    }>
    compact_bundle_cases: Array<{
      case_id: string
      bundle_profile_id: 'fixed-ip-address-change-v1'
      tracks: Partial<Record<RegisteredTrendMetric, number[]>>
      expected: {
        body_contains: string[]
        ipv4_unit: string
        ipv6_unit: string
        cross_unit_aggregation: string
      }
    }>
  }
  assert.ok(oracle.cases.length >= 16)
  for (const item of oracle.cases) {
    const offsets = item.offsets_ms
      ?? item.values.map((_, index) => index * oracle.default_interval_ms)
    const actual = result(item.values, item.metric, offsets)
    const expected = item.expected
    if (typeof expected.global_shape === 'string') {
      assert.equal(actual.summary.global_shape.value, expected.global_shape, item.case_id)
    }
    if (typeof expected.net_change === 'number') {
      assert.equal(actual.summary.net_change.value, expected.net_change, item.case_id)
    }
    if (typeof expected.baseline === 'number') {
      assert.equal(actual.summary.baseline.value, expected.baseline, item.case_id)
    }
    if (expected.maximum_decline === null) {
      assert.equal(actual.maximum_decline_segment, null, item.case_id)
    }
    if (typeof expected.maximum_decline_change === 'number') {
      assert.equal(actual.maximum_decline_segment?.change,
        expected.maximum_decline_change, item.case_id)
    }
    if (expected.maximum_rebound === null) {
      assert.equal(actual.maximum_rebound_segment, null, item.case_id)
    }
    if (typeof expected.maximum_rebound_change === 'number') {
      assert.equal(actual.maximum_rebound_segment?.change,
        expected.maximum_rebound_change, item.case_id)
    }
    if (typeof expected.turning_point_count === 'number') {
      assert.equal(actual.turning_points.length,
        expected.turning_point_count, item.case_id)
    }
    if (typeof expected.minimum_turning_point_count === 'number') {
      assert.ok(actual.turning_points.length >= expected.minimum_turning_point_count,
        item.case_id)
    }
    if (typeof expected.null_point_count === 'number') {
      assert.equal(actual.null_and_gaps.null_point_count,
        expected.null_point_count, item.case_id)
    }
    if (typeof expected.gap_count === 'number') {
      assert.equal(actual.null_and_gaps.gap_count, expected.gap_count, item.case_id)
    }
    if (typeof expected.trailing_null_point_count === 'number') {
      assert.equal(actual.tail_state.trailing_null_point_count,
        expected.trailing_null_point_count, item.case_id)
    }
    if (typeof expected.tail_observation === 'string') {
      assert.equal(actual.tail_state.observation, expected.tail_observation, item.case_id)
    }
    if (typeof expected.tail_baseline_relation === 'string') {
      assert.equal(actual.tail_state.baseline_relation,
        expected.tail_baseline_relation, item.case_id)
    }
    if (typeof expected.irregular_interval === 'boolean') {
      assert.equal(actual.data_quality.irregular_interval,
        expected.irregular_interval, item.case_id)
    }
    if (typeof expected.observed_span_ms === 'number') {
      assert.equal(actual.duration.observed_span_ms,
        expected.observed_span_ms, item.case_id)
    }
    if (typeof expected.quality_status === 'string') {
      assert.equal(actual.data_quality.status.value, expected.quality_status, item.case_id)
    }
    if (typeof expected.isolated_spike_count === 'number') {
      assert.equal(actual.isolated_spikes.length,
        expected.isolated_spike_count, item.case_id)
    }
    if (typeof expected.largest_adjacent_step_up_change === 'number') {
      assert.equal(actual.largest_adjacent_step_up?.change,
        expected.largest_adjacent_step_up_change, item.case_id)
    }
    if (typeof expected.largest_adjacent_step_down_change === 'number') {
      assert.equal(actual.largest_adjacent_step_down?.change,
        expected.largest_adjacent_step_down_change, item.case_id)
    }
    if (expected.largest_adjacent_step_down === null) {
      assert.equal(actual.largest_adjacent_step_down, null, item.case_id)
    }
    if (expected.largest_adjacent_step_up === null) {
      assert.equal(actual.largest_adjacent_step_up, null, item.case_id)
    }
    if (typeof expected.minimum_audit_phase_count === 'number') {
      assert.ok(actual.phase_sequence.length >= expected.minimum_audit_phase_count,
        item.case_id)
    }
    if (typeof expected.maximum_display_phase_count === 'number') {
      assert.ok(actual.display_phase_sequence.length
        <= expected.maximum_display_phase_count, item.case_id)
    }
    if (typeof expected.series_semantics === 'string') {
      assert.equal(actual.series_semantics, expected.series_semantics, item.case_id)
    }
  }
  assert.equal(oracle.compact_cases.length, 5)
  for (const item of oracle.compact_cases) {
    const actual = analyzeEventWindowTrendCompact(input(item.metric, item.values))
    assertCompactOutput(actual, item.metric)
    assert.equal(actual.semantic_role, item.expected.semantic_role, item.case_id)
    const cardTypes = new Set<string>(actual.cards.map((card) => card.fact_type))
    for (const factType of item.expected.required_cards) {
      assert.ok(cardTypes.has(factType), `${item.case_id}:${factType}`)
    }
    for (const fragment of item.expected.body_contains) {
      assert.match(actual.body_zh, new RegExp(fragment), item.case_id)
    }
    for (const fragment of item.expected.body_excludes ?? []) {
      assert.doesNotMatch(actual.body_zh, new RegExp(fragment), item.case_id)
    }
    const limitationIds = new Set(actual.limitations.map((item) => item.limitation_id))
    for (const limitationId of item.expected.required_limitations ?? []) {
      assert.ok(limitationIds.has(limitationId), `${item.case_id}:${limitationId}`)
    }
  }
  assert.equal(oracle.compact_bundle_cases.length, 1)
  for (const item of oracle.compact_bundle_cases) {
    const trackInputs = Object.entries(item.tracks).map(([metric, values]) =>
      input(metric as RegisteredTrendMetric, values))
    const actual = analyzeCompactTrendBundle(item.bundle_profile_id, trackInputs)
    for (const fragment of item.expected.body_contains) {
      assert.match(actual.body_zh, new RegExp(fragment), item.case_id)
    }
    assert.equal(actual.unit_separation.ipv4_unit, item.expected.ipv4_unit)
    assert.equal(actual.unit_separation.ipv6_unit, item.expected.ipv6_unit)
    assert.equal(actual.unit_separation.cross_unit_aggregation,
      item.expected.cross_unit_aggregation)
  }
  assert.equal(oracle.multi_track_cases.length, 3)
  for (const item of oracle.multi_track_cases) {
    const trackInputs = Object.entries(item.tracks).map(([metric, values]) =>
      input(metric as RegisteredTrendMetric, values))
    const actual = analyzeMultiTrackTrend(trackInputs)
    assert.ok(actual.audit_facts.some((fact) =>
      fact.kind === item.expected.kind
        && (item.expected.relation === undefined
          || fact.relation === item.expected.relation)
        && fact.from_index === item.expected.from_index
        && fact.to_index === item.expected.to_index), item.case_id)
  }
})

test('单调上升：形态、净变化、最大回升和持续时间具有确定语义', () => {
  const value = result([10, 12, 15, 18, 21])
  assert.equal(value.summary.global_shape.value, 'monotonic_increase')
  assert.equal(value.summary.net_change.value, 11)
  assert.equal(value.maximum_decline_segment, null)
  assert.equal(value.maximum_rebound_segment?.change, 11)
  assert.equal(value.maximum_rebound_segment?.duration_ms, 4 * INTERVAL_MS)
  assert.equal(value.duration.increase_duration_ms, 4 * INTERVAL_MS)
  assert.equal(value.tail_state.recent_direction, 'increase')
  assert.equal(value.largest_adjacent_step_down, null)
  assert.equal(value.largest_adjacent_step_up?.change, 3)
})

test('单调下降：形态、最大下降、基线关系和下降时长正确', () => {
  const value = result([21, 18, 15, 12, 10])
  assert.equal(value.summary.global_shape.value, 'monotonic_decrease')
  assert.equal(value.summary.net_change.value, -11)
  assert.equal(value.maximum_decline_segment?.change, -11)
  assert.equal(value.maximum_rebound_segment, null)
  assert.equal(value.tail_state.baseline_relation, 'below')
  assert.equal(value.duration.decrease_duration_ms, 4 * INTERVAL_MS)
  assert.equal(value.largest_adjacent_step_down?.change, -3)
  assert.equal(value.largest_adjacent_step_up, null)
})

test('持平：不制造阶段转折或显著变化段', () => {
  const value = result([10, 10, 10, 10, 10])
  assert.equal(value.summary.global_shape.value, 'stable')
  assert.equal(value.summary.net_change.value, 0)
  assert.equal(value.turning_points.length, 0)
  assert.equal(value.maximum_decline_segment, null)
  assert.equal(value.maximum_rebound_segment, null)
  assert.ok(value.phase_sequence.every((phase) => phase.direction === 'stable'))
  assert.equal(value.largest_adjacent_step_down, null)
  assert.equal(value.largest_adjacent_step_up, null)
})

test('下降后部分回升：末值仍低于基线，不能写成恢复', () => {
  const value = result([100, 100, 80, 50, 50, 70, 70])
  assert.equal(value.summary.baseline.value, 100)
  assert.equal(value.summary.global_shape.value, 'decrease_then_partial_rebound')
  assert.equal(value.tail_state.baseline_relation, 'below')
  assert.equal(value.maximum_decline_segment?.change, -50)
  assert.equal(value.maximum_rebound_segment?.change, 20)
  assert.match(value.deterministic_description_zh.text, /不据此判断事件结束或恢复/)
})

test('下降后回到基线：只判定回到阈值带，不判断恢复', () => {
  const value = result([100, 100, 80, 50, 50, 100, 100])
  assert.equal(value.summary.global_shape.value, 'decrease_then_return_to_baseline')
  assert.equal(value.tail_state.baseline_relation, 'near')
  assert.equal(value.maximum_rebound_segment?.change, 50)
  assert.equal(value.tail_state.event_state_inference, 'forbidden')
})

test('多阶段波动：保留按时间排序的阶段与交替转折', () => {
  const value = result([100, 100, 70, 70, 90, 90, 50, 50, 75, 75, 55, 55])
  assert.equal(value.summary.global_shape.value, 'multi_phase')
  assert.ok(value.turning_points.length >= 3)
  assert.deepEqual(
    value.phase_sequence.filter((phase) => phase.direction !== 'stable')
      .map((phase) => phase.direction),
    ['decrease', 'increase', 'decrease', 'increase', 'decrease'],
  )
  assert.ok(value.turning_points.every((point, index, all) =>
    index === 0 || point.index > all[index - 1]!.index))
})

test('孤立尖峰：连续段三点中位数抑制尖峰，不污染全局形态', () => {
  const value = result([100, 100, 180, 100, 100])
  assert.equal(value.summary.maximum.value, 180)
  assert.equal(value.summary.global_shape.value, 'stable')
  assert.equal(value.turning_points.length, 0)
  assert.equal(value.maximum_decline_segment, null)
  assert.equal(value.maximum_rebound_segment, null)
  assert.equal(value.isolated_spikes.length, 1)
  assert.equal(value.isolated_spikes[0]?.center.index, 2)
  assert.equal(value.largest_adjacent_step_up?.change, 80)
  assert.equal(value.largest_adjacent_step_down?.change, -80)
})

test('单槽尖峰与持续台阶分离，展示层不泄漏审计层复杂度', () => {
  const spike = result([100, 100, 180, 100, 100])
  const persistent = result([100, 100, 180, 180, 180])
  const volatile = result([
    100, 100, 70, 95, 60, 90, 50, 85, 40, 80, 30, 75, 20, 70, 10, 65,
  ])
  assert.equal(spike.isolated_spikes.length, 1)
  assert.equal(persistent.isolated_spikes.length, 0)
  assert.equal(persistent.summary.global_shape.value, 'monotonic_increase')
  assert.ok(volatile.phase_sequence.length > volatile.display_phase_sequence.length)
  assert.ok(volatile.display_phase_sequence.length <= 6)
})

test('显著事实排序尊重语义主事实且完全确定', () => {
  const interruption = result([10, 10, 40, 10, 10], 'affected_asn_count')
  const visibility = result(
    [1000, 1000, 700, 700, 900], 'fixed_visible_ipv4_address_count',
  )
  assert.equal(interruption.significant_facts[0]?.fact_type, 'last')
  assert.ok(interruption.significant_facts.some((fact) => fact.fact_type === 'maximum'))
  assert.ok(visibility.significant_facts.some((fact) => fact.fact_type === 'minimum'))
  assert.deepEqual(
    analyzeEventWindowTrend(input('affected_asn_count', [10, 10, 40, 10, 10]))
      .significant_facts,
    interruption.significant_facts,
  )
})

test('current_supplement 与 cumulative 使用不同产品语义', () => {
  const current = result(
    [0, 10, 10, 5, 5], 'new_visible_ipv4_prefix_count',
  )
  const cumulative = result(
    [0, 10, 10, 10, 10], 'new_cumulative_ipv4_prefix_count',
  )
  assert.equal(current.series_semantics, 'current_supplement')
  assert.equal(current.summary.global_shape.value, 'rise_then_below_baseline')
  assert.equal(current.maximum_decline_segment?.change, -5)
  assert.equal(cumulative.series_semantics, 'cumulative')
  assert.equal(cumulative.maximum_decline_segment, null)
})

test('compact visibility stock：首值、最低、部分回升和截止值可直接展示', () => {
  const metric: RegisteredTrendMetric = 'fixed_visible_ipv4_address_count'
  const compact = analyzeEventWindowTrendCompact(
    input(metric, [1000, 1000, 700, 700, 900]),
  )
  assertCompactOutput(compact, metric)
  assert.equal(compact.display_label_zh, '固定前缀可见 IPv4 地址量')
  assert.match(compact.body_zh, /窗口最低/)
  assert.match(compact.body_zh, /部分回升/)
  assert.deepEqual(compact.cards.map((card) => card.fact_type).slice(0, 4),
    ['first', 'minimum', 'last', 'net_change'])
})

test('compact interruption stock：峰值和单槽尖峰优先于内部阶段', () => {
  const metric: RegisteredTrendMetric = 'affected_asn_count'
  const compact = analyzeEventWindowTrendCompact(
    input(metric, [10, 10, 40, 10, 10]),
  )
  assertCompactOutput(compact, metric)
  assert.match(compact.body_zh, /窗口峰值/)
  assert.match(compact.body_zh, /单槽尖峰/)
  assert.ok(compact.cards.some((card) => card.fact_type === 'maximum'))
  assert.ok(compact.cards.some((card) => card.fact_type === 'isolated_spike'))
})

test('compact cumulative：报告累计末值、关键台阶和平台，不写下降恢复', () => {
  const metric: RegisteredTrendMetric = 'new_cumulative_ipv4_prefix_count'
  const compact = analyzeEventWindowTrendCompact(
    input(metric, [0, 1, 1, 4, 7, 7]),
  )
  assertCompactOutput(compact, metric)
  assert.match(compact.body_zh, /累计/)
  assert.match(compact.body_zh, /最大单槽增加/)
  assert.match(compact.body_zh, /保持至截止/)
  assert.doesNotMatch(compact.body_zh, /下降|恢复/)
  assert.ok(compact.cards.some((card) =>
    card.fact_type === 'largest_adjacent_step_up'))
})

test('compact current_supplement：明确当前可见补充量而非累计', () => {
  const metric: RegisteredTrendMetric = 'new_visible_ipv4_prefix_count'
  const compact = analyzeEventWindowTrendCompact(
    input(metric, [0, 10, 10, 5, 5]),
  )
  assertCompactOutput(compact, metric)
  assert.match(compact.body_zh, /当前可见补充量/)
  assert.doesNotMatch(compact.body_zh, /累计值|累计增加/)
  assert.ok(compact.cards.some((card) => card.fact_type === 'maximum'))
})

test('compact 尾部 null：使用最后有效观测并单列尾部限制', () => {
  const metric: RegisteredTrendMetric = 'affected_asn_count'
  const compact = analyzeEventWindowTrendCompact(
    input(metric, [100, 90, 80, null, null]),
  )
  assertCompactOutput(compact, metric)
  assert.match(compact.body_zh, /最后有效观测/)
  assert.ok(compact.cards.some((card) => card.fact_type === 'tail_null'))
  assert.ok(compact.limitations.some((item) =>
    item.limitation_id === 'tail-unobserved' && /不能向前填充/.test(item.text_zh)))
})

test('compact 轻量入口与完整结果中的 compact 层逐字段一致', () => {
  const request = input('affected_asn_count', [10, 20, 15, 25, 20])
  const full = analyzeEventWindowTrend(request)
  const compact = analyzeEventWindowTrendCompact(structuredClone(request))
  assert.deepEqual(compact, full.compact_chat_output)
  assert.ok(JSON.stringify(compact).length < JSON.stringify(full).length)
})

test('compact IPv4/IPv6 bundle：分轨单位、禁止合并且无内部词', () => {
  const bundle = analyzeCompactTrendBundle('fixed-ip-address-change-v1', [
    input('fixed_visible_ipv4_address_count', [1000, 1000, 700, 900]),
    input('fixed_visible_ipv6_slash48_count', [20, 20, 18, 19]),
  ])
  assert.equal(bundle.title_zh, 'IP 地址变化情况')
  assert.match(bundle.body_zh, /IPv4 唯一地址/)
  assert.match(bundle.body_zh, /IPv6 \/48 等价量/)
  assert.match(bundle.body_zh, /不合并/)
  assert.doesNotMatch(bundle.body_zh, COMPACT_INTERNAL_LEAK)
  assert.equal(bundle.unit_separation.ipv4_unit, 'unique_ipv4_address')
  assert.equal(bundle.unit_separation.ipv6_unit, 'ipv6_slash48_equivalent')
  assert.equal(bundle.unit_separation.cross_unit_aggregation, 'forbidden')
  assert.notEqual(bundle.tracks[0].unit, bundle.tracks[1].unit)
  expectError(() => analyzeCompactTrendBundle('fixed-ip-address-change-v1', [
    input('affected_asn_count', [1, 2, 3]),
    input('fixed_visible_ipv6_slash48_count', [3, 2, 1]),
  ]), 'invalid_series_shape')
})

test('null 间断：不插值、不补零且不跨缺口计算下降', () => {
  const value = result([100, 100, null, null, 50, 50])
  assert.equal(value.data_quality.null_point_count, 2)
  assert.equal(value.data_quality.status.value, 'usable_with_caveats')
  assert.equal(value.null_and_gaps.gap_count, 1)
  assert.equal(value.null_and_gaps.gaps[0]?.kind, 'null_run')
  assert.equal(value.null_and_gaps.gaps[0]?.slot_count, 2)
  assert.equal(value.null_and_gaps.interpolation, 'forbidden')
  assert.equal(value.null_and_gaps.null_as_zero, 'forbidden')
  assert.equal(value.maximum_decline_segment, null)
})

test('尾部 null：最后非空值不得冒充 data-through 当前值', () => {
  const value = result([100, 90, 80, null, null])
  assert.equal(value.null_and_gaps.trailing_null_point_count, 2)
  assert.equal(value.tail_state.observation, 'before_data_through')
  assert.equal(value.tail_state.last_observed_at_utc, timestamp(2 * INTERVAL_MS))
  assert.match(value.deterministic_description_zh.text, /不得向前填充为当前值/)
})

test('累计指标：只允许非递减增长，不产生下降后回升语义', () => {
  const value = result(
    [0, 1, 1, 4, 7],
    'new_cumulative_ipv4_prefix_count',
  )
  assert.equal(value.series_semantics, 'cumulative')
  assert.equal(value.summary.global_shape.value, 'cumulative_growth')
  assert.equal(value.summary.net_change.value, 7)
  assert.equal(value.maximum_decline_segment, null)
  assert.equal(value.maximum_rebound_segment, null)
  assert.equal(value.duration.below_baseline_duration_ms, null)
  assert.equal(value.largest_adjacent_step_down, null)
  assert.equal(value.largest_adjacent_step_up?.change, 3)
  const invalid = input('new_cumulative_ipv4_prefix_count', [0, 2, 1])
  expectError(() => analyzeEventWindowTrend(invalid), 'cumulative_series_decreased')
})

test('相邻方向步只接受严格正负 delta，并各自选择最大方向幅度', () => {
  const value = result([10, 14, 13, 20, 15, 15])
  assert.equal(value.largest_adjacent_step_up?.change, 7)
  assert.equal(value.largest_adjacent_step_up?.from.index, 2)
  assert.equal(value.largest_adjacent_step_up?.to.index, 3)
  assert.equal(value.largest_adjacent_step_down?.change, -5)
  assert.equal(value.largest_adjacent_step_down?.from.index, 3)
  assert.equal(value.largest_adjacent_step_down?.to.index, 4)
})

test('不规则时间：时长来自 UTC 差值，并显式报告隐式大缺口', () => {
  const offsets = [0, 60_000, 180_000, 600_000]
  const value = result([10, 20, 30, 40], 'affected_asn_count', offsets)
  assert.equal(value.data_quality.irregular_interval, true)
  assert.equal(value.duration.observed_span_ms, 600_000)
  assert.equal(value.duration.connected_observation_duration_ms, 180_000)
  assert.equal(value.null_and_gaps.gap_count, 1)
  assert.equal(value.null_and_gaps.gaps[0]?.kind, 'implicit_time_gap')
  assert.equal(value.maximum_rebound_segment?.duration_ms, 180_000)
})

test('短序列：保留首末事实但不越级判定全局形态', () => {
  const value = result([10, 20])
  assert.equal(value.data_quality.status.value, 'insufficient')
  assert.equal(value.summary.global_shape.value, 'insufficient_data')
  assert.equal(value.summary.first.value, 10)
  assert.equal(value.summary.last.value, 20)
  assert.ok(value.data_quality.warnings.includes('short_series'))
})

test('错误身份、单位、语义、形状与 Profile 均失败关闭', async (context) => {
  await context.test('错误 collector 身份', () => {
    const value = input('affected_asn_count', [1, 2, 3]) as unknown as {
      source_identity: Record<string, unknown>
    }
    value.source_identity.collector_id = 'rrc00'
    expectError(
      () => analyzeEventWindowTrend(value as unknown as EventWindowTrendInput),
      'invalid_identity',
    )
  })
  await context.test('跨单位', () => {
    const value = input('affected_asn_count', [1, 2, 3])
    value.unit = 'prefix'
    expectError(() => analyzeEventWindowTrend(value), 'unit_mismatch')
  })
  await context.test('错误 series semantics', () => {
    const value = input('affected_asn_count', [1, 2, 3])
    value.series_semantics = 'cumulative'
    expectError(() => analyzeEventWindowTrend(value), 'series_semantics_mismatch')
  })
  await context.test('自由修改 Profile', () => {
    const value = input('affected_asn_count', [1, 2, 3])
    value.trend_profile.parameters.change_threshold_absolute = 999
    expectError(() => analyzeEventWindowTrend(value), 'unregistered_trend_profile')
  })
  await context.test('数组不同长', () => {
    const value = input('affected_asn_count', [1, 2, 3])
    value.values.pop()
    expectError(() => analyzeEventWindowTrend(value), 'invalid_series_shape')
  })
  await context.test('时间不递增', () => {
    const value = input('affected_asn_count', [1, 2, 3])
    value.timestamps[2] = value.timestamps[1]!
    expectError(() => analyzeEventWindowTrend(value), 'invalid_timestamp')
  })
})

test('确定性重放：完整机器结果逐字段相同且逐事实均有 lineage', () => {
  const request = input('affected_asn_count', [100, 90, null, 70, 80, 60, 75])
  const first = analyzeEventWindowTrend(request)
  const second = analyzeEventWindowTrend(structuredClone(request))
  assert.deepEqual(second, first)
  const lineageIds = new Set(first.fact_lineage.map((item) => item.fact_id))
  const referencedFactIds = new Set(collectFactIds({ ...first, fact_lineage: [] }))
  assert.ok(referencedFactIds.size > 10)
  for (const factId of referencedFactIds) assert.ok(lineageIds.has(factId), factId)
  assert.ok(first.fact_lineage.every((item) => item.evidence_refs.length >= 4))
})

test('多轨 Oracle：同步反向、同步尖峰与累计/当前分歧均可审计', () => {
  const opposing = analyzeMultiTrackTrend([
    input('interrupted_prefix_count', [10, 10, 30, 30]),
    input('fixed_visible_ipv4_address_count', [1000, 1000, 700, 700]),
  ])
  assert.ok(opposing.audit_facts.some((fact) =>
    fact.kind === 'same_interval_change'
      && fact.relation === 'opposing_numeric_direction'
      && fact.from_index === 1 && fact.to_index === 2))
  assert.equal(opposing.comparison_rules.cross_unit_aggregation, 'forbidden')

  const spikes = analyzeMultiTrackTrend([
    input('affected_asn_count', [10, 10, 40, 10, 10]),
    input('interrupted_prefix_count', [100, 100, 400, 100, 100]),
  ])
  assert.ok(spikes.audit_facts.some((fact) =>
    fact.kind === 'synchronized_isolated_spike'
      && fact.from_index === 1 && fact.to_index === 3))

  const divergence = analyzeMultiTrackTrend([
    input('new_cumulative_ipv6_prefix_count', [0, 1, 1, 1]),
    input('new_visible_ipv6_prefix_count', [0, 1, 0, 0]),
  ])
  assert.ok(divergence.audit_facts.some((fact) =>
    fact.kind === 'cumulative_current_divergence'
      && fact.from_index === 1 && fact.to_index === 2))
  assert.deepEqual(
    analyzeMultiTrackTrend([
      input('new_visible_ipv6_prefix_count', [0, 1, 0, 0]),
      input('new_cumulative_ipv6_prefix_count', [0, 1, 1, 1]),
    ]),
    divergence,
  )
})

test('多轨身份或时间轴不一致时失败关闭', () => {
  const left = input('affected_asn_count', [1, 2, 3])
  const right = input('interrupted_prefix_count', [1, 2, 3])
  right.source_identity.publication_id = 'different-publication'
  expectError(() => analyzeMultiTrackTrend([left, right]),
    'cross_track_identity_conflict')
})

interface RawSeries {
  schema_version: string
  publication_id: string
  publication_state: 'published'
  revision: number
  collector_id: 'rrc25'
  incident_id: string
  cohort_id: string
  window_start_utc: string
  window_end_utc: string
  data_through: string | null
  is_final_in_data_range: boolean
  lifecycle_state: string
  observation_state: string
  quality_state: string
  missing_slot_count: number
  timestamps: string[]
  tracks: Record<string, Array<number | null>>
  track_definitions: Record<string, { unit: string }>
}

function realInput(series: RawSeries, metric: RegisteredTrendMetric): EventWindowTrendInput {
  const profile = getRegisteredTrendProfile(metric)
  return {
    source_identity: {
      source_schema_version: series.schema_version,
      event_type: 'country_outage',
      incident_id: series.incident_id,
      publication_id: series.publication_id,
      publication_state: series.publication_state,
      revision: series.revision,
      collector_id: series.collector_id,
      cohort_id: series.cohort_id,
      window_start_utc: series.window_start_utc,
      window_end_utc: series.window_end_utc,
      data_through: series.data_through,
      is_final_in_data_range: series.is_final_in_data_range,
      lifecycle_state: series.lifecycle_state,
      observation_state: series.observation_state,
      quality_state: series.quality_state,
      missing_slot_count: series.missing_slot_count,
    },
    metric,
    unit: series.track_definitions[metric]!.unit,
    series_semantics: profile.series_semantics,
    timestamps: series.timestamps,
    values: series.tracks[metric]!,
    source_evidence_refs: {
      identity: ['frozen:/resolver-v6#identity', 'frozen:/series-v6#identity'],
      timestamps: 'frozen:/series-v6#/timestamps',
      values: `frozen:/series-v6#/tracks/${metric}`,
      metric_definition: `frozen:/series-v6#/track_definitions/${metric}`,
      trend_profile: `contract:/trend-profiles#/${metric}`,
    },
    trend_profile: profile,
  }
}

test('伊朗冻结 RRC25 IPv4/IPv6 轨道：保持真实单位、首末值与边界', () => {
  const series = JSON.parse(readFileSync(
    new URL(
      '../../../evaluation/country-outage/p1-page-coverage/s1/raw/live-series-response-v6.json',
      import.meta.url,
    ),
    'utf8',
  )) as RawSeries
  const ipv4 = analyzeEventWindowTrend(
    realInput(series, 'fixed_visible_ipv4_address_count'),
  )
  const ipv6 = analyzeEventWindowTrend(
    realInput(series, 'fixed_visible_ipv6_slash48_count'),
  )
  assert.equal(ipv4.unit, 'unique_ipv4_address')
  assert.equal(ipv6.unit, 'ipv6_slash48_equivalent')
  assert.notEqual(ipv4.unit, ipv6.unit)
  assert.equal(ipv4.summary.net_change.value, -87_040)
  assert.equal(ipv6.summary.net_change.value, -4)
  assert.equal(ipv4.summary.minimum.value, 9_577_728)
  assert.equal(ipv6.summary.minimum.value, 267_288)
  assert.equal(ipv4.tail_state.observation, 'at_data_through')
  assert.equal(ipv6.tail_state.observation, 'at_data_through')
  assert.equal(ipv4.tail_state.event_state_inference, 'forbidden')
  assert.equal(ipv6.tail_state.event_state_inference, 'forbidden')
  assert.equal(ipv4.data_quality.observed_point_count, series.timestamps.length)
  assert.equal(ipv6.data_quality.observed_point_count, series.timestamps.length)
  assert.ok(ipv4.maximum_decline_segment !== null)
  assert.ok(ipv4.maximum_rebound_segment !== null)
  assert.ok(ipv6.maximum_decline_segment !== null)
  assert.match(ipv4.deterministic_description_zh.text, /不据此判断事件结束或恢复/)
})

test('伊朗冻结 RRC25 全部 15 轨：逐轨回放精确事实并生成跨轨审计', () => {
  const series = JSON.parse(readFileSync(
    new URL(
      '../../../evaluation/country-outage/p1-page-coverage/s1/raw/live-series-response-v6.json',
      import.meta.url,
    ),
    'utf8',
  )) as RawSeries
  const metrics = Object.keys(REGISTERED_TREND_PROFILES)
    .sort() as RegisteredTrendMetric[]
  assert.equal(metrics.length, 15)
  const inputs = metrics.map((metric) => realInput(series, metric))
  for (const trackInput of inputs) {
    const actual = analyzeEventWindowTrend(trackInput)
    const observed = trackInput.values.flatMap((value, index) =>
      value === null ? [] : [{ value, index }])
    assert.equal(actual.data_quality.total_point_count, series.timestamps.length)
    assert.equal(actual.data_quality.observed_point_count, observed.length)
    assert.equal(actual.summary.first.value, observed[0]!.value)
    assert.equal(actual.summary.last.value, observed[observed.length - 1]!.value)
    assert.equal(actual.summary.minimum.value,
      Math.min(...observed.map((item) => item.value)))
    assert.equal(actual.summary.maximum.value,
      Math.max(...observed.map((item) => item.value)))
    assert.ok(actual.display_phase_sequence.every((phase) =>
      phase.audit_phase_ids.length <= actual.phase_sequence.length))
    assert.ok(actual.significant_facts.length <= 8)
  }
  const multi = analyzeMultiTrackTrend(inputs)
  assert.equal(multi.track_count, 15)
  assert.ok(multi.audit_facts.length > 0)
  assert.ok(multi.display_facts.length <= 12)
  assert.ok(multi.audit_facts.some((fact) =>
    fact.kind === 'synchronized_isolated_spike'))
  assert.ok(multi.audit_facts.some((fact) =>
    fact.kind === 'cumulative_current_divergence'))
})

test('冻结 RRC25 全部 15 轨 compact：必答卡片、长度和零内部泄漏', () => {
  const series = JSON.parse(readFileSync(
    new URL(
      '../../../evaluation/country-outage/p1-page-coverage/s1/raw/live-series-response-v6.json',
      import.meta.url,
    ),
    'utf8',
  )) as RawSeries
  const metrics = Object.keys(REGISTERED_TREND_PROFILES)
    .sort() as RegisteredTrendMetric[]
  const outputs = metrics.map((metric) => ({
    metric,
    output: analyzeEventWindowTrendCompact(realInput(series, metric)),
  }))
  assert.equal(outputs.length, 15)
  for (const { metric, output } of outputs) {
    assertCompactOutput(output, metric)
    const primary = getRegisteredTrendProfile(metric).primary_fact
    assert.ok(output.cards.some((card) => card.fact_type === primary), metric)
  }
  const bundle = analyzeCompactTrendBundle('fixed-ip-address-change-v1', [
    realInput(series, 'fixed_visible_ipv4_address_count'),
    realInput(series, 'fixed_visible_ipv6_slash48_count'),
  ])
  assert.equal(bundle.unit_separation.cross_unit_aggregation, 'forbidden')
  assert.doesNotMatch(bundle.body_zh, COMPACT_INTERNAL_LEAK)
  assert.ok(bundle.fact_ids.length > 0 && bundle.evidence_refs.length > 0)
})
