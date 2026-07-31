import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import test from 'node:test'

import type {
  CountryOutageAudit,
  CountryOutageOverview,
  CountryOutageResolution,
  CountryOutageSeries,
  ObservationBatch,
  ResourceSlot,
  VisibilitySlot,
} from '../src/domain/contracts.js'
import {
  DomeyeCountryOutageClient,
  assertBatchIdentity,
} from '../src/domain/domeye-client.js'
import {
  DomeyeApiError,
  ReportDataInsufficientError,
  SnapshotConflictError,
  UnsupportedCollectorError,
} from '../src/domain/errors.js'
import { assembleCountryOutageFacts } from '../src/domain/observation-assembler.js'

const publicationId = 'publication-test-v1'
const incidentId = 'incident-test-v1'
const cohortId = 'cohort-test-v1'

function slot(
  minute: string,
  visible: number,
  _ratio: number,
  delta: number | null,
): VisibilitySlot {
  const ipv6Visible = 950
  const ipv4Visible = visible - ipv6Visible
  return {
    observed_at_utc: `2026-02-28T10:${minute}:00Z`,
    observed_at_local: `2026-02-28T18:${minute}:00+08:00`,
    slot_state: 'observed',
    visible_prefix_vp_count: visible,
    visible_prefix_vp_ratio: visible / 384767,
    ...(delta === null
      ? {}
      : {
          visible_prefix_vp_delta: delta,
          visible_prefix_vp_ratio_delta_pp:
            delta / 384767 * 100,
        }),
    visible_origin_asn_count: 543,
    fully_visible_asn_count: 463,
    partially_visible_asn_count: 80,
    fully_invisible_asn_count: 20,
    ipv4_visible_prefix_vp_count: ipv4Visible,
    ipv4_visible_prefix_vp_ratio: ipv4Visible / 383804,
    ipv6_visible_prefix_vp_count: ipv6Visible,
    ipv6_visible_prefix_vp_ratio: ipv6Visible / 963,
    update_total: 100,
    announce_count: 70,
    withdraw_count: 30,
    withdraw_ratio: 0.3,
  }
}

function resolution(): CountryOutageResolution {
  return {
    schema_version: 'country_outage_resolution_v2',
    incident_id: incidentId,
    publication_id: publicationId,
    legacy_reference: 'country_outage/2026-02-27 09:12:32/IR/1/r',
    event_type: 'country_outage',
    observation_state: 'state_complete',
    latest_revision: 1,
    data_mode: 'replay',
    data_through: '2026-02-28T10:20:00Z',
    is_final: true,
    missing_slot_count: 0,
    capability_contract_version: 'country_outage_capabilities_v1',
    capabilities: {
      fixed_cohort: { state: 'available' },
      normal_band: {
        state: 'unavailable',
        reason: '缺少可信正常参照',
      },
    },
  }
}

function overview(): CountryOutageOverview {
  return {
    schema_version: 'country_outage_overview_v2',
    incident_id: incidentId,
    publication_id: publicationId,
    publication_state: 'published',
    observation_state: 'state_complete',
    revision: 1,
    data_through: '2026-02-28T10:20:00Z',
    is_final: true,
    window_start_utc: '2026-02-28T10:05:00Z',
    window_end_utc: '2026-02-28T10:20:00Z',
    cohort_id: cohortId,
    event_identity: {
      incident_id: incidentId,
      legacy_reference: 'country_outage/2026-02-27 09:12:32/IR/1/r',
      event_type: 'country_outage',
      country_code: 'IR',
      country_name: '伊朗',
      display_name: '伊朗 BGP 路由观测',
    },
    observation_scope: {
      collector_id: 'rrc25',
      collector_ids: ['rrc25'],
      collector_count: 1,
      window_start_utc: '2026-02-28T10:05:00Z',
      window_start_local: '2026-02-28T18:05:00+08:00',
      window_end_utc: '2026-02-28T10:20:00Z',
      window_end_local: '2026-02-28T18:20:00+08:00',
      timezone: 'Asia/Shanghai',
      interval_seconds: 300,
      observation_count: 4,
      expected_observation_count: 4,
      missing_observation_count: 0,
      quality_status: 'pass',
      last_observation_at_utc: '2026-02-28T10:20:00Z',
      last_observation_at_local: '2026-02-28T18:20:00+08:00',
    },
    cohort: {
      cohort_id: cohortId,
      denominator_policy: 'fixed_from_complete_rib',
      origin_asn_count: 563,
      prefix_vp_count: 384767,
      ipv4_prefix_vp_count: 383804,
      ipv6_prefix_vp_count: 963,
    },
    capabilities: {
      fixed_cohort: { state: 'available' },
      address_families: { state: 'available' },
      asn_matrix: { state: 'available' },
      update_activity: { state: 'available' },
      normal_band: {
        state: 'unavailable',
        reason: '缺少可信正常参照',
      },
    },
    capability_contract_version: 'country_outage_capabilities_v1',
    missing_slot_count: 0,
    processing_status: { state: 'final' },
    limitations: ['只能描述 RRC25 的 BGP 控制面观测。'],
  }
}

function series(): CountryOutageSeries {
  return {
    schema_version: 'country_outage_series_v2',
    incident_id: incidentId,
    publication_id: publicationId,
    publication_state: 'published',
    observation_state: 'state_complete',
    revision: 1,
    data_through: '2026-02-28T10:20:00Z',
    is_final: true,
    window_start_utc: '2026-02-28T10:05:00Z',
    window_end_utc: '2026-02-28T10:20:00Z',
    cohort_id: cohortId,
    interval_seconds: 300,
    missing_slot_count: 0,
    metric_definitions: [],
    series: [
      slot('05', 367215, 0.9544, null),
      slot('10', 350000, 0.9096, -17215),
      slot('15', 316733, 0.8232, -33267),
      slot('20', 333938, 0.8679, 17205),
    ],
    metric_extrema: {},
    resource_series: [],
    resource_metric_extrema: {},
    annotations: [],
  }
}

function audit(): CountryOutageAudit {
  return {
    schema_version: 'country_outage_audit_v2',
    incident_id: incidentId,
    publication_id: publicationId,
    publication_state: 'published',
    observation_state: 'state_complete',
    revision: 1,
    data_through: '2026-02-28T10:20:00Z',
    is_final: true,
    window_start_utc: '2026-02-28T10:05:00Z',
    window_end_utc: '2026-02-28T10:20:00Z',
    cohort_id: cohortId,
    quality_status: 'pass',
    missing_slot_count: 0,
    missing_slots: [],
    source_system: 'country_outage_observation_package',
    source_reference: incidentId,
    evidence_level: 'aggregated_route_state_with_artifact_hashes',
    algorithm_version: 'test/1',
    mapping_version: 'mapping-test',
    verified_hashes: { 'cohort.json': 'abc123' },
  }
}

function batch(): ObservationBatch {
  return {
    resolution: resolution(),
    overview: overview(),
    series: series(),
    audit: audit(),
  }
}

function enableResourceTrack(value: ObservationBatch): ResourceSlot[] {
  const ipv4EquivalentCounts = [1_000, 990, 970, 975]
  const ipv6EquivalentCounts = [2_000, 2_000, 1_980, 1_980]
  const announceCounts = [0, 12, 8, 10]
  const withdrawCounts = [0, 4, 6, 3]
  const resources = value.series.series.map((visibilityPoint, index) => {
    const ipv4Equivalent = ipv4EquivalentCounts[index]!
    const ipv6Equivalent = ipv6EquivalentCounts[index]!
    const announce = announceCounts[index]!
    const withdraw = withdrawCounts[index]!
    const total = announce + withdraw
    return {
      observed_at_utc: visibilityPoint.observed_at_utc,
      observed_at_local: visibilityPoint.observed_at_local,
      ipv4_24_equivalent_count: ipv4Equivalent,
      ipv4_address_count: ipv4Equivalent * 256,
      ipv6_48_equivalent_count: ipv6Equivalent,
      announce_count: announce,
      withdraw_count: withdraw,
      update_total: total,
      withdraw_ratio: total === 0 ? null : withdraw / total,
      ipv4_24_equivalent_delta:
        index === 0
          ? null
          : ipv4Equivalent - ipv4EquivalentCounts[index - 1]!,
      ipv4_address_delta:
        index === 0
          ? null
          : (
              ipv4Equivalent -
              ipv4EquivalentCounts[index - 1]!
            ) * 256,
      ipv6_48_equivalent_delta:
        index === 0
          ? null
          : ipv6Equivalent - ipv6EquivalentCounts[index - 1]!,
      announce_delta:
        index === 0
          ? null
          : announce - announceCounts[index - 1]!,
      withdraw_delta:
        index === 0
          ? null
          : withdraw - withdrawCounts[index - 1]!,
    } satisfies ResourceSlot
  })
  value.overview.capabilities.country_resources = {
    state: 'available',
  }
  value.series.resource_series = resources
  return resources
}

function declareGap(
  value: ObservationBatch,
  index: number,
  slotState: Exclude<VisibilitySlot['slot_state'], 'observed'> =
    'not_observed',
): void {
  const existing = value.series.series[index]!
  const missingReason = '测试声明的缺槽'
  value.series.series[index] = {
    observed_at_utc: existing.observed_at_utc,
    observed_at_local: existing.observed_at_local,
    slot_state: slotState,
    missing_reason: missingReason,
  }
  value.resolution.missing_slot_count = 1
  value.overview.missing_slot_count = 1
  value.series.missing_slot_count = 1
  value.audit.missing_slot_count = 1
  value.overview.observation_scope.observation_count =
    value.series.series.length - 1
  value.overview.observation_scope.expected_observation_count =
    value.series.series.length
  value.overview.observation_scope.missing_observation_count = 1
  value.overview.observation_scope.quality_status =
    'published_with_declared_gaps'
  value.audit.missing_slots = [{
    observed_at: existing.observed_at_utc,
    slot_state: slotState,
    missing_reason: missingReason,
  }]
  const lastObserved = [...value.series.series]
    .reverse()
    .find((item) => item.slot_state === 'observed')
  value.overview.observation_scope.last_observation_at_utc =
    lastObserved?.observed_at_utc ?? null
  value.overview.observation_scope.last_observation_at_local =
    lastObserved?.observed_at_local ?? null
}

test('装配固定快照事实并由确定性公式计算关键差值', () => {
  const first = assembleCountryOutageFacts(batch())
  const second = assembleCountryOutageFacts(batch())

  assert.equal(first.factSetId, second.factSetId)
  assert.equal(first.snapshot.collectorId, 'rrc25')
  assert.equal(first.eligibility.eligible, true)
  assert.deepEqual(
    first.keyVisibilityPoints.map((item) => [
      item.kind,
      item.visiblePrefixVpCount,
    ]),
    [
      ['start', 367215],
      ['lowest', 316733],
      ['end', 333938],
      ['largest_drop', 316733],
      ['largest_recovery', 333938],
    ],
  )

  const derived = Object.fromEntries(
    first.derivedFacts.map((item) => [item.metric, item]),
  )
  assert.equal(
    derived.start_to_lowest_visible_prefix_vp_change?.value,
    50482,
  )
  assert.equal(derived.end_gap_from_start?.value, 33277)
  assert.equal(derived.recovered_from_lowest?.value, 17205)
  assert.equal(
    derived.recovery_share_of_prior_loss?.value,
    17205 / 50482,
  )
  assert.equal(
    derived.end_gap_from_start?.formula,
    'start_visible_prefix_vp_count - end_visible_prefix_vp_count',
  )
})

test('事实集合标识不受 JSON 对象键顺序影响', () => {
  const left = batch()
  const right = batch()
  left.audit.verified_hashes = {
    z: 'hash-z',
    A: 'hash-upper',
    a_b: 'hash-underscore',
    'a-b': 'hash-hyphen',
    'ä': 'hash-unicode',
    '\uE000': 'hash-private-use',
    '😀': 'hash-astral',
  }
  right.audit.verified_hashes = {
    '😀': 'hash-astral',
    '\uE000': 'hash-private-use',
    'ä': 'hash-unicode',
    'a-b': 'hash-hyphen',
    a_b: 'hash-underscore',
    A: 'hash-upper',
    z: 'hash-z',
  }
  assert.equal(
    assembleCountryOutageFacts(left).factSetId,
    assembleCountryOutageFacts(right).factSetId,
  )

  const assembled = assembleCountryOutageFacts(left)
  const { factSetId: _factSetId, ...factSetContent } = assembled
  const compareCodePoints = (first: string, second: string): number => {
    const firstCodePoints = Array.from(first, (value) => value.codePointAt(0)!)
    const secondCodePoints = Array.from(second, (value) => value.codePointAt(0)!)
    const length = Math.min(firstCodePoints.length, secondCodePoints.length)
    for (let index = 0; index < length; index += 1) {
      const difference =
        firstCodePoints[index]! - secondCodePoints[index]!
      if (difference !== 0) return difference
    }
    return firstCodePoints.length - secondCodePoints.length
  }
  const canonicalizeByCodePoint = (value: unknown): unknown => {
    if (Array.isArray(value)) return value.map(canonicalizeByCodePoint)
    if (value && typeof value === 'object') {
      return Object.fromEntries(
        Object.entries(value as Record<string, unknown>)
          .sort(([first], [second]) => compareCodePoints(first, second))
          .map(([key, item]) => [key, canonicalizeByCodePoint(item)]),
      )
    }
    return value
  }
  const expectedId = `facts_${createHash('sha256')
    .update(JSON.stringify(canonicalizeByCodePoint(factSetContent)))
    .digest('hex')
    .slice(0, 32)}`
  assert.equal(assembled.factSetId, expectedId)
})

test('事实集合标识覆盖所有可进入报告的事实类别', () => {
  const baseline = assembleCountryOutageFacts(batch()).factSetId
  const mutations: Array<(value: ObservationBatch) => void> = [
    (value) => {
      value.overview.event_identity.display_name = '伊朗 BGP 路由观测（修订）'
    },
    (value) => {
      value.overview.capabilities.normal_band = {
        state: 'available',
      }
    },
    (value) => {
      value.overview.limitations = [
        ...value.overview.limitations,
        '没有用户侧可达性证据。',
      ]
    },
    (value) => {
      const point = value.series.series[1]!
      value.series.metric_extrema = {
        update_total: {
          max: {
            metric: 'update_total',
            value: point.update_total,
            observed_at_utc: point.observed_at_utc,
            observed_at_local: point.observed_at_local,
            slot_index: 1,
          },
        },
      }
    },
    (value) => {
      const resources = enableResourceTrack(value)
      value.series.resource_metric_extrema = {
        ipv6_48_equivalent_count: {
          max: {
            metric: 'ipv6_48_equivalent_count',
            value: resources[0]!.ipv6_48_equivalent_count,
            observed_at_utc: resources[0]!.observed_at_utc,
            observed_at_local: resources[0]!.observed_at_local,
            slot_index: 0,
          },
        },
      }
    },
    (value) => {
      value.series.annotations = [
        {
          kind: 'observation_note',
          observed_at_utc: '2026-02-28T10:15:00Z',
        },
      ]
    },
    (value) => {
      value.audit.algorithm_version = 'test/2'
    },
    (value) => {
      value.audit.verified_hashes['series.json'] = 'def456'
    },
  ]

  for (const mutate of mutations) {
    const changed = batch()
    mutate(changed)
    assert.notEqual(assembleCountryOutageFacts(changed).factSetId, baseline)
  }
})

test('固定能力词表缺失项显式记录为 unknown 且不解释为可用', () => {
  const value = batch()
  value.overview.capabilities = {
    fixed_cohort: { state: 'available' },
    asn_matrix: { state: 'available' },
  }
  const facts = assembleCountryOutageFacts(value)
  assert.equal(facts.capabilities.fixed_cohort?.state, 'available')
  assert.equal(facts.capabilities.asn_matrix?.state, 'available')
  for (const capability of [
    'legacy_summary',
    'country_resources',
    'update_activity',
    'address_families',
    'audit',
    'normal_band',
  ]) {
    assert.equal(facts.capabilities[capability]?.state, 'unknown')
    assert.match(
      facts.capabilities[capability]?.reason ?? '',
      /未声明/,
    )
    assert.equal(
      facts.eligibility.degradedCapabilities[capability]?.state,
      'unknown',
    )
  }
})

test('非 RRC25 快照被失败关闭', () => {
  const value = batch()
  value.overview.observation_scope.collector_id = 'rrc00'
  value.overview.observation_scope.collector_ids = ['rrc00']
  assert.throws(
    () => assembleCountryOutageFacts(value),
    UnsupportedCollectorError,
  )
})

test('缺少最低可见性序列时不发布正式事实集合', () => {
  const value = batch()
  for (const item of value.series.series.slice(1)) {
    delete item.visible_prefix_vp_count
    delete item.visible_prefix_vp_ratio
    delete item.visible_prefix_vp_delta
    delete item.visible_prefix_vp_ratio_delta_pp
  }
  assert.throws(
    () => assembleCountryOutageFacts(value),
    ReportDataInsufficientError,
  )
})

test('数据质量不是 pass 时不发布正式事实集合', () => {
  for (const target of ['overview', 'audit'] as const) {
    const value = batch()
    if (target === 'overview') {
      value.overview.observation_scope.quality_status = 'failed'
    } else {
      value.audit.quality_status = 'failed'
    }
    assert.throws(
      () => assembleCountryOutageFacts(value),
      (error: unknown) =>
        error instanceof ReportDataInsufficientError &&
        error.message.includes('quality_status=pass'),
    )
  }
})

test('数据截止为空时不发布正式事实集合', () => {
  for (const dataThrough of [null, ''] as const) {
    const value = batch()
    value.resolution.data_through = dataThrough
    value.overview.data_through = dataThrough
    value.series.data_through = dataThrough
    value.audit.data_through = dataThrough
    assert.throws(
      () => assembleCountryOutageFacts(value),
      (error: unknown) =>
        error instanceof ReportDataInsufficientError &&
        error.message.includes('data_through'),
    )
  }
})

test('显式非观测槽即使携带数值也不计入最低可见性序列', () => {
  const value = batch()
  const nonObservedStates: VisibilitySlot['slot_state'][] = [
    'source_unavailable',
    'processing_gap',
    'parse_failed',
  ]
  value.series.series.slice(1).forEach((item, index) => {
    item.slot_state = nonObservedStates[index]!
    item.missing_reason = '测试声明的缺槽'
  })
  assert.throws(
    () => assembleCountryOutageFacts(value),
    SnapshotConflictError,
  )
})

test('固定窗口序列缺首、缺尾或静默截断时失败关闭', () => {
  for (const mutate of [
    (value: ObservationBatch) => {
      value.series.series.shift()
      value.overview.observation_scope.observation_count -= 1
      value.overview.observation_scope.expected_observation_count! -= 1
    },
    (value: ObservationBatch) => {
      value.series.series.pop()
      value.overview.observation_scope.observation_count -= 1
      value.overview.observation_scope.expected_observation_count! -= 1
    },
  ]) {
    const value = batch()
    mutate(value)
    assert.throws(
      () => assembleCountryOutageFacts(value),
      SnapshotConflictError,
    )
  }
})

test('固定窗口首尾显式缺槽时也不能改用相邻观测槽发布报告', () => {
  for (const index of [0, 3]) {
    const value = batch()
    declareGap(value, index)
    assert.throws(
      () => assembleCountryOutageFacts(value),
      (error: unknown) =>
        error instanceof ReportDataInsufficientError &&
        error.message.includes(
          index === 0
            ? 'series.window_start_observed'
            : 'series.window_end_observed',
        ),
    )
  }
})

test('完整网格、观测数、缺槽数和 audit 清单必须逐项对账', () => {
  const mutations: Array<(value: ObservationBatch) => void> = [
    (value) => {
      value.overview.observation_scope.observation_count -= 1
    },
    (value) => {
      value.overview.observation_scope.expected_observation_count! -= 1
    },
    (value) => {
      value.overview.observation_scope.missing_observation_count = 1
    },
    (value) => {
      value.audit.missing_slots = [{
        observed_at: value.series.series[1]!.observed_at_utc,
        slot_state: 'not_observed',
        missing_reason: '伪造缺槽',
      }]
      value.audit.missing_slot_count = 1
    },
  ]
  for (const mutate of mutations) {
    const value = batch()
    mutate(value)
    assert.throws(
      () => assembleCountryOutageFacts(value),
      SnapshotConflictError,
    )
  }
})

test('事件嵌套身份、请求引用和国家代码必须绑定同一快照', () => {
  const mutations: Array<(value: ObservationBatch) => void> = [
    (value) => {
      value.overview.event_identity.incident_id = 'incident-other'
    },
    (value) => {
      value.overview.event_identity.legacy_reference =
        'country_outage/2026-02-27 09:12:32/US/1/r'
    },
    (value) => {
      value.overview.event_identity.country_code = 'US'
    },
  ]
  for (const mutate of mutations) {
    const value = batch()
    mutate(value)
    assert.throws(
      () => assembleCountryOutageFacts(value),
      SnapshotConflictError,
    )
  }
})

test('核心可见率和相邻变化必须与固定人口及相邻槽闭合', () => {
  const mutations: Array<(value: ObservationBatch) => void> = [
    (value) => {
      value.series.series[1]!.visible_prefix_vp_ratio = 0.5
    },
    (value) => {
      value.series.series[1]!.visible_prefix_vp_count =
        value.overview.cohort!.prefix_vp_count + 1
    },
    (value) => {
      value.series.series[1]!.visible_prefix_vp_delta = -1
    },
    (value) => {
      value.series.series[1]!.visible_prefix_vp_ratio_delta_pp = -1
    },
  ]
  for (const mutate of mutations) {
    const value = batch()
    mutate(value)
    assert.throws(
      () => assembleCountryOutageFacts(value),
      (error: unknown) =>
        error instanceof ReportDataInsufficientError &&
        /series\.(visible_prefix_vp|adjacent_delta)_semantics/.test(
          error.message,
        ),
    )
  }
})

test('进入报告的极值必须回指同指标、同值和同一 UTC/local 槽', () => {
  const mutations: Array<(value: ObservationBatch) => void> = [
    (value) => {
      const point = value.series.series[0]!
      value.series.metric_extrema = {
        visible_prefix_vp_count: {
          max: {
            metric: 'other_metric',
            value: point.visible_prefix_vp_count,
            observed_at_utc: point.observed_at_utc,
            observed_at_local: point.observed_at_local,
          },
        },
      }
    },
    (value) => {
      const extreme = value.series.series[0]!
      const wrongTime = value.series.series[1]!
      value.series.metric_extrema = {
        visible_prefix_vp_count: {
          max: {
            metric: 'visible_prefix_vp_count',
            value: extreme.visible_prefix_vp_count,
            observed_at_utc: wrongTime.observed_at_utc,
            observed_at_local: wrongTime.observed_at_local,
          },
        },
      }
    },
    (value) => {
      const extreme = value.series.series[0]!
      value.series.metric_extrema = {
        visible_prefix_vp_count: {
          max: {
            metric: 'visible_prefix_vp_count',
            value: extreme.visible_prefix_vp_count! - 1,
            observed_at_utc: extreme.observed_at_utc,
            observed_at_local: extreme.observed_at_local,
          },
        },
      }
    },
    (value) => {
      const extreme = value.series.series[0]!
      value.series.metric_extrema = {
        visible_prefix_vp_count: {
          max: {
            metric: 'visible_prefix_vp_count',
            value: extreme.visible_prefix_vp_count,
            observed_at_utc: extreme.observed_at_utc,
            observed_at_local: '2026-02-28T18:06:00+08:00',
          },
        },
      }
    },
  ]
  for (const mutate of mutations) {
    const value = batch()
    mutate(value)
    assert.throws(
      () => assembleCountryOutageFacts(value),
      (error: unknown) =>
        error instanceof ReportDataInsufficientError &&
        error.message.includes('series.metric_extrema_semantics'),
    )
  }
})

test('国家资源极值必须回指 resource_series 的真实最值', () => {
  const value = batch()
  const resources = enableResourceTrack(value)
  value.series.resource_metric_extrema = {
    ipv4_24_equivalent_count: {
      min: {
        metric: 'ipv4_24_equivalent_count',
        value: resources[0]!.ipv4_24_equivalent_count,
        observed_at_utc: resources[0]!.observed_at_utc,
        observed_at_local: resources[0]!.observed_at_local,
      },
    },
  }
  assert.throws(
    () => assembleCountryOutageFacts(value),
    (error: unknown) =>
      error instanceof ReportDataInsufficientError &&
      error.message.includes(
        'series.resource_metric_extrema_semantics',
      ),
  )
})

test('ASN 三态、可见 origin 与固定 origin 总量必须闭合', () => {
  for (const mutate of [
    (value: ObservationBatch) => {
      value.series.series[1]!.visible_origin_asn_count! -= 1
    },
    (value: ObservationBatch) => {
      value.series.series[1]!.fully_invisible_asn_count! -= 1
    },
  ]) {
    const value = batch()
    mutate(value)
    assert.throws(
      () => assembleCountryOutageFacts(value),
      (error: unknown) =>
        error instanceof ReportDataInsufficientError &&
        error.message.includes('series.asn_partition_semantics'),
    )
  }
})

test('IPv4/IPv6 固定分母、数量合计和比例必须闭合', () => {
  const mutations: Array<(value: ObservationBatch) => void> = [
    (value) => {
      value.overview.cohort!.ipv4_prefix_vp_count! += 1
    },
    (value) => {
      value.series.series[1]!.ipv6_visible_prefix_vp_count! -= 1
    },
    (value) => {
      value.series.series[1]!.ipv4_visible_prefix_vp_ratio = 0.5
    },
  ]
  for (const mutate of mutations) {
    const value = batch()
    mutate(value)
    assert.throws(
      () => assembleCountryOutageFacts(value),
      (error: unknown) =>
        error instanceof ReportDataInsufficientError &&
        /(?:cohort|series)\.address_family_/.test(error.message),
    )
  }
})

test('UPDATE announce、withdraw、total 和 withdraw_ratio 必须闭合', () => {
  for (const mutate of [
    (value: ObservationBatch) => {
      value.series.series[1]!.update_total! += 1
    },
    (value: ObservationBatch) => {
      value.series.series[1]!.withdraw_ratio = 0.5
    },
  ]) {
    const value = batch()
    mutate(value)
    assert.throws(
      () => assembleCountryOutageFacts(value),
      (error: unknown) =>
        error instanceof ReportDataInsufficientError &&
        error.message.includes('series.update_activity_semantics'),
    )
  }
})

test('零 UPDATE 槽只接受 null withdraw_ratio，两条轨道使用同一失败关闭规则', () => {
  const valid = batch()
  valid.series.series[0]!.announce_count = 0
  valid.series.series[0]!.withdraw_count = 0
  valid.series.series[0]!.update_total = 0
  valid.series.series[0]!.withdraw_ratio = null
  enableResourceTrack(valid)
  assert.doesNotThrow(() => assembleCountryOutageFacts(valid))

  for (const target of ['visibility', 'resource'] as const) {
    const value = batch()
    value.series.series[0]!.announce_count = 0
    value.series.series[0]!.withdraw_count = 0
    value.series.series[0]!.update_total = 0
    value.series.series[0]!.withdraw_ratio = null
    const resources = enableResourceTrack(value)
    if (target === 'visibility') {
      value.series.series[0]!.withdraw_ratio = 0
    } else {
      resources[0]!.withdraw_ratio = 0
    }
    assert.throws(
      () => assembleCountryOutageFacts(value),
      (error: unknown) =>
        error instanceof ReportDataInsufficientError &&
        error.message.includes(
          target === 'visibility'
            ? 'series.update_activity_semantics'
            : 'series.resource_update_activity_semantics',
        ),
    )
  }
})

test('国家资源 UPDATE 轨道独立闭合，不能借用 replay 可见性轨道数值', () => {
  const valid = batch()
  const resources = enableResourceTrack(valid)
  const resourcePeak = resources[1]!
  valid.series.resource_metric_extrema = {
    update_total: {
      max: {
        metric: 'update_total',
        value: resourcePeak.update_total,
        observed_at_utc: resourcePeak.observed_at_utc,
        observed_at_local: resourcePeak.observed_at_local,
        slot_index: 1,
      },
    },
  }
  assert.doesNotThrow(() => assembleCountryOutageFacts(valid))

  const borrowed = batch()
  const borrowedResources = enableResourceTrack(borrowed)
  const visibilityPoint = borrowed.series.series[1]!
  borrowed.series.resource_metric_extrema = {
    update_total: {
      max: {
        metric: 'update_total',
        value: visibilityPoint.update_total,
        observed_at_utc: borrowedResources[1]!.observed_at_utc,
        observed_at_local: borrowedResources[1]!.observed_at_local,
        slot_index: 1,
      },
    },
  }
  assert.throws(
    () => assembleCountryOutageFacts(borrowed),
    (error: unknown) =>
      error instanceof ReportDataInsufficientError &&
      error.message.includes(
        'series.resource_metric_extrema_semantics',
      ),
  )
})

test('国家资源序列必须覆盖同一完整网格，资源数、地址量和相邻变化逐槽闭合', () => {
  const mutations: Array<(value: ObservationBatch) => void> = [
    (value) => {
      enableResourceTrack(value).pop()
    },
    (value) => {
      const resources = enableResourceTrack(value)
      resources[1]!.observed_at_utc = resources[2]!.observed_at_utc
      resources[1]!.observed_at_local = resources[2]!.observed_at_local
    },
    (value) => {
      enableResourceTrack(value)[1]!.ipv4_address_count! += 1
    },
    (value) => {
      enableResourceTrack(value)[1]!.ipv4_24_equivalent_delta! += 1
    },
    (value) => {
      enableResourceTrack(value)[1]!.announce_delta! += 1
    },
    (value) => {
      enableResourceTrack(value)[0]!.withdraw_delta = 0
    },
    (value) => {
      enableResourceTrack(value)[1]!.update_total! += 1
    },
    (value) => {
      enableResourceTrack(value)[1]!.withdraw_ratio = 0.5
    },
  ]
  for (const mutate of mutations) {
    const value = batch()
    mutate(value)
    assert.throws(
      () => assembleCountryOutageFacts(value),
      (error: unknown) =>
        error instanceof ReportDataInsufficientError &&
        /series\.resource_(?:complete_time_grid|count_semantics|first_slot_delta=null|adjacent_delta_semantics|update_activity_semantics)/.test(
          error.message,
        ),
    )
  }
})

test('跨接口 publication_state 和 observation_state 漂移被拒绝', () => {
  for (const mutate of [
    (value: ObservationBatch) => {
      value.series.publication_state = 'building'
    },
    (value: ObservationBatch) => {
      value.audit.observation_state = 'state_building'
    },
  ]) {
    const value = batch()
    mutate(value)
    assert.throws(
      () => assertBatchIdentity(value),
      SnapshotConflictError,
    )
  }
})

test('不完整 observation_state 和空国家展示身份不能发布正式报告', () => {
  for (const mutate of [
    (value: ObservationBatch) => {
      value.resolution.observation_state = 'state_partial'
      value.overview.observation_state = 'state_partial'
      value.series.observation_state = 'state_partial'
      value.audit.observation_state = 'state_partial'
    },
    (value: ObservationBatch) => {
      value.overview.event_identity.country_name = ''
    },
    (value: ObservationBatch) => {
      value.overview.event_identity.display_name = ''
    },
  ]) {
    const value = batch()
    mutate(value)
    assert.throws(
      () => assembleCountryOutageFacts(value),
      ReportDataInsufficientError,
    )
  }
})

test('v2 未显式标注 observed 的槽即使数值有效也不能补足最低门槛', () => {
  const value = batch()
  for (const item of value.series.series) {
    delete (item as unknown as Record<string, unknown>).slot_state
  }
  assert.throws(
    () => assembleCountryOutageFacts(value),
    SnapshotConflictError,
  )
})

test('固定 cohort 人口必须是正安全整数且身份字段完整', () => {
  const mutations: Array<(value: ObservationBatch) => void> = [
    (value) => { value.overview.cohort!.prefix_vp_count = 0 },
    (value) => { value.overview.cohort!.prefix_vp_count = -1 },
    (value) => { value.overview.cohort!.origin_asn_count = 0 },
    (value) => { value.overview.cohort!.origin_asn_count = 1.5 },
    (value) => { value.overview.cohort!.denominator_policy = '' },
    (value) => {
      value.overview.observation_scope.interval_seconds = 0
      value.series.interval_seconds = 0
    },
  ]
  for (const mutate of mutations) {
    const value = batch()
    mutate(value)
    assert.throws(
      () => assembleCountryOutageFacts(value),
      ReportDataInsufficientError,
    )
  }
})

test('嵌套窗口、cohort、interval 和 slot 时间冲突被失败关闭', () => {
  const mutations: Array<(value: ObservationBatch) => void> = [
    (value) => {
      value.overview.observation_scope.window_end_utc =
        '2026-02-28T14:55:00Z'
    },
    (value) => {
      value.overview.observation_scope.window_start_local =
        '2026-02-28T18:10:00+08:00'
    },
    (value) => {
      value.overview.observation_scope.window_start_local =
        '2026-02-28T10:05:00Z'
    },
    (value) => {
      value.overview.cohort!.cohort_id = 'cohort-conflict'
    },
    (value) => {
      value.series.interval_seconds = 600
    },
    (value) => {
      value.series.series[1]!.observed_at_utc =
        value.series.series[0]!.observed_at_utc
      value.series.series[1]!.observed_at_local =
        value.series.series[0]!.observed_at_local
    },
    (value) => {
      value.series.series[1]!.observed_at_utc =
        '2026-02-28T10:11:00Z'
      value.series.series[1]!.observed_at_local =
        '2026-02-28T18:11:00+08:00'
    },
    (value) => {
      value.series.series[1]!.observed_at_local =
        '2026-02-28T18:12:00+08:00'
    },
    (value) => {
      value.series.series[1]!.observed_at_local =
        '2026-02-28T10:10:00Z'
    },
    (value) => {
      value.series.series[0]!.observed_at_utc =
        '2026-02-28T10:00:00Z'
      value.series.series[0]!.observed_at_local =
        '2026-02-28T18:00:00+08:00'
    },
  ]

  for (const mutate of mutations) {
    const value = batch()
    mutate(value)
    assert.throws(
      () => assembleCountryOutageFacts(value),
      SnapshotConflictError,
    )
  }
})

test('跨接口 publication 或 revision 冲突被拒绝', () => {
  const value = batch()
  value.series.revision = 2
  assert.throws(() => assertBatchIdentity(value), SnapshotConflictError)
})

test('客户端遇到嵌套观测身份冲突时同样丢弃整批并重新 resolve', async () => {
  let resolveCount = 0
  let overviewCount = 0
  const fetchImplementation: typeof fetch = async (input) => {
    const url = new URL(
      typeof input === 'string'
        ? input
        : input instanceof URL
          ? input
          : input.url,
    )
    let payload: unknown
    if (url.pathname.endsWith('/events/resolve')) {
      resolveCount += 1
      payload = resolution()
    } else if (url.pathname.endsWith('/overview')) {
      overviewCount += 1
      payload = overview()
      if (overviewCount === 1) {
        ;(payload as CountryOutageOverview)
          .observation_scope.window_end_utc =
          '2026-02-28T14:55:00Z'
      }
    } else if (url.pathname.endsWith('/series')) {
      payload = series()
    } else if (url.pathname.endsWith('/audit')) {
      payload = audit()
    } else {
      return new Response('not found', { status: 404 })
    }
    return new Response(JSON.stringify(payload), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    })
  }

  const client = new DomeyeCountryOutageClient({
    baseUrl: 'http://domeye.test/api/v2/',
    maximumSnapshotBatchRetries: 2,
    fetchImplementation,
  })
  const value = await client.getObservationBatch(
    'country_outage/2026-02-27 09:12:32/IR/1/r',
  )
  assert.equal(resolveCount, 2)
  assert.equal(overviewCount, 2)
  assert.equal(
    value.overview.observation_scope.window_end_utc,
    value.overview.window_end_utc,
  )
})

test('客户端遇到冲突时丢弃整批并重新 resolve', async () => {
  let resolveCount = 0
  let seriesCount = 0
  const fetchImplementation: typeof fetch = async (input) => {
    const url = new URL(
      typeof input === 'string'
        ? input
        : input instanceof URL
          ? input
          : input.url,
    )
    let payload: unknown
    if (url.pathname.endsWith('/events/resolve')) {
      resolveCount += 1
      payload = resolution()
    } else if (url.pathname.endsWith('/overview')) {
      payload = overview()
    } else if (url.pathname.endsWith('/series')) {
      seriesCount += 1
      payload = series()
      if (seriesCount === 1) {
        ;(payload as CountryOutageSeries).revision = 2
      }
    } else if (url.pathname.endsWith('/audit')) {
      payload = audit()
    } else {
      return new Response('not found', { status: 404 })
    }
    return new Response(JSON.stringify(payload), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    })
  }

  const client = new DomeyeCountryOutageClient({
    baseUrl: 'http://domeye.test/api/v2/',
    maximumSnapshotBatchRetries: 2,
    fetchImplementation,
  })
  const value = await client.getObservationBatch(
    'country_outage/2026-02-27 09:12:32/IR/1/r',
  )
  assert.equal(resolveCount, 2)
  assert.equal(seriesCount, 2)
  assert.equal(value.series.revision, 1)
})

test('空格与加号形式的同一引用先规范化再绑定 resolve', async () => {
  const requestedReferences: string[] = []
  const fetchImplementation: typeof fetch = async (input) => {
    const url = new URL(
      typeof input === 'string'
        ? input
        : input instanceof URL
          ? input
          : input.url,
    )
    if (url.pathname.endsWith('/events/resolve')) {
      requestedReferences.push(url.searchParams.get('ref') ?? '')
      return Response.json(resolution())
    }
    if (url.pathname.endsWith('/overview')) return Response.json(overview())
    if (url.pathname.endsWith('/series')) return Response.json(series())
    if (url.pathname.endsWith('/audit')) return Response.json(audit())
    return new Response('not found', { status: 404 })
  }
  const client = new DomeyeCountryOutageClient({
    baseUrl: 'http://domeye.test/api/v2/',
    fetchImplementation,
  })
  await client.getObservationBatch(
    'country_outage/2026-02-27+09:12:32/IR/1/r',
  )
  assert.deepEqual(requestedReferences, [
    'country_outage/2026-02-27 09:12:32/IR/1/r',
  ])
})

test('宿主取消贯通 resolve、观测批次与 ASN 读取，且不触发后续重试', async () => {
  const paths: string[] = []
  const fetchImplementation: typeof fetch = async (input, init) => {
    const url = new URL(
      typeof input === 'string'
        ? input
        : input instanceof URL
          ? input
          : input.url,
    )
    paths.push(url.pathname)
    const requestSignal = init?.signal
    assert.ok(requestSignal)
    requestSignal.throwIfAborted()
    return await new Promise<Response>((_resolve, reject) => {
      requestSignal.addEventListener(
        'abort',
        () => reject(requestSignal.reason),
        { once: true },
      )
    })
  }
  const client = new DomeyeCountryOutageClient({
    baseUrl: 'http://domeye.test/api/v2/',
    maximumSnapshotBatchRetries: 2,
    fetchImplementation,
  })
  const reference =
    'country_outage/2026-02-27 09:12:32/IR/1/r'
  const reportSnapshot = assembleCountryOutageFacts(batch()).snapshot
  const operations: Array<{
    expectedPath: string
    run(signal: AbortSignal): Promise<unknown>
  }> = [
    {
      expectedPath: '/api/v2/events/resolve',
      run: async (signal) => await client.resolve(reference, signal),
    },
    {
      expectedPath: '/api/v2/events/resolve',
      run: async (signal) =>
        await client.getObservationBatch(reference, signal),
    },
    {
      expectedPath: `/api/v2/country-outages/${incidentId}/asns`,
      run: async (signal) =>
        await client.getAsns(reportSnapshot, {}, signal),
    },
  ]

  for (const operation of operations) {
    paths.length = 0
    const controller = new AbortController()
    const pending = operation.run(controller.signal)
    assert.deepEqual(paths, [operation.expectedPath])
    controller.abort()
    await assert.rejects(
      pending,
      (error: unknown) =>
        error instanceof DOMException && error.name === 'AbortError',
    )
    await new Promise<void>((resolve) => setImmediate(resolve))
    assert.deepEqual(paths, [operation.expectedPath])
  }
})

test('宿主取消信号与 Domeye 固定超时组合，内部超时仍失败关闭为可重试 API 错误', async () => {
  const fetchImplementation: typeof fetch = async (_input, init) => {
    const requestSignal = init?.signal
    assert.ok(requestSignal)
    requestSignal.throwIfAborted()
    return await new Promise<Response>((_resolve, reject) => {
      requestSignal.addEventListener(
        'abort',
        () => reject(requestSignal.reason),
        { once: true },
      )
    })
  }
  const client = new DomeyeCountryOutageClient({
    baseUrl: 'http://domeye.test/api/v2/',
    timeoutMs: 10,
    fetchImplementation,
  })
  // AbortSignal.timeout 使用不保持事件循环存活的计时器。测试桩本身没有真实
  // socket 句柄，因此显式保活到超时断言结束，避免不同主机调度下被父测试取消。
  const keepAlive = setTimeout(() => {}, 100)
  try {
    await assert.rejects(
      client.resolve(
        'country_outage/2026-02-27 09:12:32/IR/1/r',
        new AbortController().signal,
      ),
      (error: unknown) =>
        error instanceof DomeyeApiError &&
        error.status === 503 &&
        error.retryable &&
        /超时/.test(error.message),
    )
  } finally {
    clearTimeout(keepAlive)
  }
})
