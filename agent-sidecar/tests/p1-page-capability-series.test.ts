import assert from 'node:assert/strict'
import test from 'node:test'

import {
  ADDRESS_METRIC_DEFINITIONS,
  HttpEventBindingVerifier,
  HttpReadSeriesTool,
  InMemoryReadSeriesTool,
  LocalEventBindingVerifier,
  answerControlledIpQuestion as executeControlledIpQuestion,
  emptyPageQaState,
  groundControlledIpGoal,
  mapGeneralSeriesApiResponse,
  summarizeSeries,
  type AddressMetric,
  type ControlledIpUserGoal,
  type EventBinding,
  type EventBindingVerifier,
  type PageQaState,
  type PageSeriesExecutionContext,
  type ReadSeriesTool,
  type SeriesPayload,
} from '../src/chat/page-capability-series.js'

function answerControlledIpQuestion(
  userGoal: ControlledIpUserGoal,
  tool: ReadSeriesTool,
  signal?: AbortSignal,
  initialState?: PageQaState,
  verifier?: EventBindingVerifier,
  executionContext: PageSeriesExecutionContext = {
    grantedPermissions: ['country_outage_event_read'],
    timeoutMs: 10_000,
  },
) {
  return executeControlledIpQuestion(
    userGoal,
    tool,
    signal,
    initialState,
    verifier ?? new LocalEventBindingVerifier(),
    executionContext,
  )
}

const binding: EventBinding = {
  eventType: 'country_outage',
  incidentId: 'incident_go_v1_a1de26f854831330c616a72af21597eb',
  publicationId: 'country_outage_publication_v1_989f698fb6f6c32579eebe7bb2bc833f',
  revision: 1,
  collectorId: 'rrc25',
  countryCode: 'IR',
  windowStartUtc: '2026-02-27T00:10:00Z',
  windowEndUtc: '2026-03-11T00:00:00Z',
  dataThrough: '2026-03-11T00:00:00Z',
  lifecycleState: 'event_end_unknown',
  resolutionSchemaVersion: 'country_outage_general_resolution_v1',
  observationState: 'evidence_complete',
  qualityState: 'complete',
  capabilityIds: [
    'CAP-001', 'CAP-002', 'CAP-006', 'CAP-007', 'CAP-008', 'CAP-009',
    'CAP-016', 'CAP-017',
  ],
  identityEvidenceRefs: [
    'resolver:/api/v2/events/resolve',
    'capabilities:/api/v2/country-outages/{incident_id}/overview',
  ],
  legacyReference: 'country_outage/2026-02-27 09:12:32/IR/1/r',
  sourceCapabilities: {
    eventSeries: 'available',
    overview: 'available',
  },
}

const timestamps = [
  '2026-02-27T00:10:00Z',
  '2026-02-28T14:35:00Z',
  '2026-02-28T14:40:00Z',
  '2026-03-04T08:55:00Z',
  '2026-03-11T00:00:00Z',
]

function payload(): SeriesPayload {
  const tracks: SeriesPayload['tracks'] = {
    fixed_visible_ipv4_address_count: [10156800, 9577728, 9577728, 10000000, 10069760],
    fixed_visible_ipv6_slash48_count: [267292, 267292, 267292, 267288, 267288],
    new_cumulative_ipv4_prefix_count: [0, 200, 250, 600, 700],
    new_cumulative_ipv4_address_count: [0, 70000, 80000, 200000, 244291],
    new_visible_ipv4_prefix_count: [0, 80, 90, 100, 111],
    new_visible_ipv4_address_count: [0, 12000, 14000, 18000, 19523],
    new_cumulative_ipv6_prefix_count: [0, 0, 0, 1, 1],
    new_cumulative_ipv6_slash48_count: [0, 0, 0, 524288, 524288],
    new_visible_ipv6_prefix_count: [0, 0, 0, 1, 1],
    new_visible_ipv6_slash48_count: [0, 0, 0, 524288, 524288],
  }
  const definitions: SeriesPayload['definitions'] = {}
  for (const metric of Object.keys(tracks) as AddressMetric[]) {
    definitions[metric] = {
      unit: ADDRESS_METRIC_DEFINITIONS[metric].unit,
      definition: ADDRESS_METRIC_DEFINITIONS[metric].definition,
    }
  }
  return {
    schemaVersion: 'country_outage_general_series_v1',
    binding: structuredClone(binding),
    timestamps: [...timestamps],
    tracks,
    definitions,
    eventCountryIdentitySource: 'verified_event_binding',
    sourceReceipt: {
      sourceId: 'fixture:country_outage_general_series_v1',
      endpoint: 'fixture:/api/v2/country-outages/{incident_id}/series',
      responseSha256: 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
    },
  }
}

function goal(
  kind: ControlledIpUserGoal['kind'],
  entities?: ControlledIpUserGoal['entities'],
): ControlledIpUserGoal {
  const result: ControlledIpUserGoal = {
    goalId: `goal-${kind}`,
    kind,
    requestedText: kind === 'ip_address_change' ? 'IP地址变化情况' : 'ip地址变化趋势',
    binding,
  }
  if (entities !== undefined) result.entities = entities
  return result
}

function rawApiResponse(): Record<string, unknown> {
  const fixture = payload()
  return {
    schema_version: fixture.schemaVersion,
    incident_id: fixture.binding.incidentId,
    publication_id: fixture.binding.publicationId,
    revision: fixture.binding.revision,
    collector_id: fixture.binding.collectorId,
    window_start_utc: fixture.binding.windowStartUtc,
    window_end_utc: fixture.binding.windowEndUtc,
    data_through: fixture.binding.dataThrough,
    lifecycle_state: fixture.binding.lifecycleState,
    timestamps: fixture.timestamps,
    tracks: fixture.tracks,
    track_definitions: fixture.definitions,
  }
}

function resolverApiResponse(): Record<string, unknown> {
  return {
    schema_version: binding.resolutionSchemaVersion,
    event_type: binding.eventType,
    country_code: binding.countryCode,
    incident_id: binding.incidentId,
    publication_id: binding.publicationId,
    revision: binding.revision,
    collector_id: binding.collectorId,
    window_start_utc: binding.windowStartUtc,
    window_end_utc: binding.windowEndUtc,
    data_through: binding.dataThrough,
    lifecycle_state: binding.lifecycleState,
    observation_state: binding.observationState,
    quality_state: binding.qualityState,
    capabilities: {
      event_series: 'available',
      overview: 'available',
    },
  }
}

function overviewApiResponse(): Record<string, unknown> {
  return {
    schema_version: 'country_outage_general_overview_v1',
    incident_id: binding.incidentId,
    publication_id: binding.publicationId,
    revision: binding.revision,
    collector_id: binding.collectorId,
    window_start_utc: binding.windowStartUtc,
    window_end_utc: binding.windowEndUtc,
    data_through: binding.dataThrough,
    lifecycle_state: binding.lifecycleState,
  }
}

function identityFetcher(
  resolverValue: Record<string, unknown>,
  overviewValue: Record<string, unknown> = overviewApiResponse(),
) {
  return async (url: string) => {
    const value = url.includes('/api/v2/events/resolve') ? resolverValue : overviewValue
    const raw = JSON.stringify(value)
    return {
      ok: true,
      status: 200,
      async json(): Promise<unknown> { return JSON.parse(raw) },
      async text(): Promise<string> { return raw },
    }
  }
}

test('泛指 IP 明确 Grounding 为 both，不存在非 IPv6 即 IPv4 的回退', () => {
  const plan = groundControlledIpGoal(goal('ip_address_change'))
  assert.equal(plan.addressFamily, 'both')
  assert.equal(plan.reasonCode, 'generic_ip_defaults_to_ipv4_and_ipv6')
  assert.equal(plan.normalizedUserGoal.requestedGoal, 'fixed_cohort_address_change')
  assert.equal(plan.normalizedUserGoal.addressFamily, 'both')
  assert.deepEqual(plan.nodes
    .filter((node) => node.operatorId === 'OP-P1-SERIES-EXTREMA')
    .flatMap((node) => node.metrics), [
    'fixed_visible_ipv4_address_count',
    'fixed_visible_ipv6_slash48_count',
  ])
  assert.ok(plan.nodes.some((node) => node.operatorId === 'OP-P1-IDENTITY-GATE'))
  assert.ok(plan.nodes.some((node) => node.operatorId === 'OP-P1-METRIC-DEFINITION-GATE'))
  assert.ok(plan.nodes.some((node) => node.operatorId === 'OP-P1-ADDRESS-FAMILY-COMPARE'))
  assert.equal(plan.includeNewPrefixes, true)
})

test('显式 IPv4、IPv6 和 both 都有独立 Grounding', () => {
  const ipv4 = groundControlledIpGoal(goal('ip_address_change', { addressFamily: 'ipv4' }))
  const ipv6 = groundControlledIpGoal(goal('ip_address_change', { addressFamily: 'ipv6' }))
  const both = groundControlledIpGoal(goal('ip_address_change', { addressFamily: 'both' }))
  const extremaMetrics = (plan: ReturnType<typeof groundControlledIpGoal>) => plan.nodes
    .filter((node) => node.operatorId === 'OP-P1-SERIES-EXTREMA')
    .flatMap((node) => node.metrics)
  assert.deepEqual(extremaMetrics(ipv4), ['fixed_visible_ipv4_address_count'])
  assert.deepEqual(extremaMetrics(ipv6), ['fixed_visible_ipv6_slash48_count'])
  assert.equal(extremaMetrics(both).length, 2)
})

test('显式不看新增只执行固定 cohort，显式只看新前缀不执行固定 cohort', () => {
  const fixedOnly = groundControlledIpGoal(goal('ip_address_change', {
    addressFamily: 'both',
    includeNewPrefixes: false,
  }))
  assert.equal(fixedOnly.nodes.some((node) => node.operatorId === 'OP-P1-CURRENT-VALUE'), false)
  assert.equal(fixedOnly.nodes.filter((node) => node.operatorId === 'OP-P1-SERIES-EXTREMA').length, 2)

  const newOnly = groundControlledIpGoal(goal('ip_address_change', {
    addressFamily: 'ipv4',
    population: 'new_prefix_only',
  }))
  assert.equal(newOnly.includeFixedCohort, false)
  assert.equal(newOnly.nodes.some((node) => node.operatorId === 'OP-P1-SERIES-EXTREMA'), false)
  assert.equal(newOnly.nodes.some((node) => node.operatorId === 'OP-P1-CURRENT-VALUE'), true)
})

test('series 算子排除 null，并使用 first-observed 极值并列策略', () => {
  const summary = summarizeSeries(
    'fixed_visible_ipv4_address_count',
    timestamps,
    [10156800, 9577728, 9577728, null, 10069760],
    payload().sourceReceipt,
  )
  assert.ok(summary)
  assert.equal(summary.minimum, 9577728)
  assert.equal(summary.minimumAtUtc, '2026-02-28T14:35:00Z')
  assert.equal(summary.maximumAtUtc, '2026-02-27T00:10:00Z')
  assert.equal(summary.netChange, -87040)
  assert.equal(summary.observedPointCount, 4)
  assert.equal(summary.nullPointCount, 1)
})

test('IP 地址变化情况同时回答 IPv4、IPv6 和新前缀补充', async () => {
  const result = await answerControlledIpQuestion(
    goal('ip_address_change'),
    new InMemoryReadSeriesTool(payload()),
  )
  assert.equal(result.answerability, 'supported')
  assert.equal(result.stateCommit, 'committed')
  assert.equal(result.summaries.length, 2)
  assert.equal(result.currentValues.length, 8)
  assert.equal(result.comparison?.combinedAbsoluteTotal, 'forbidden')
  assert.equal(result.comparison?.operatorVersion, 'v1')
  assert.equal(result.stateReceipt.commit, 'committed')
  assert.deepEqual(result.stateReceipt.committedVerifiedFamilies, ['ipv4', 'ipv6'])
  assert.equal(result.stateReceipt.after.dialog.addressFamily, 'both')
  assert.equal(result.stateReceipt.after.evidence.verifiedMetrics.length, 10)
  assert.ok(result.evidenceBindings.some((item) => (
    item.evidenceId === 'resolver:event-binding'
    && item.jsonPointers.includes('/capabilities/event_series')
  )))
  assert.ok(result.evidenceBindings.some((item) => (
    item.evidenceId === 'overview:event-binding'
    && item.jsonPointers.includes('/data_through')
  )))
  const compareReceipt = result.operatorReceipts.find((item) => (
    item.operatorId === 'OP-P1-ADDRESS-FAMILY-COMPARE'
  ))
  assert.deepEqual(compareReceipt?.inputOperatorReceiptIds, [
    'operator:extrema:fixed_visible_ipv4_address_count',
    'operator:extrema:fixed_visible_ipv6_slash48_count',
  ])
  assert.equal(compareReceipt?.inputEvidenceIds.length, 0)
  assert.ok(result.operatorReceipts.some((item) => (
    item.operatorId === 'OP-P1-METRIC-DEFINITION-GATE'
  )))
  assert.match(result.answerText, /IPv4 固定 cohort/)
  assert.match(result.answerText, /IPv6 固定 cohort/)
  assert.match(result.answerText, /唯一 IPv4 地址/)
  assert.match(result.answerText, /IPv6 \/48 等价块/)
  assert.match(result.answerText, /新前缀补充/)
  assert.match(result.answerText, /有效观测 5 点，null 0 点/)
  assert.match(result.answerText, /incident_go_v1_/)
  assert.match(result.answerText, /country_outage_publication_v1_/)
  assert.match(result.answerText, /事件结束仍未知/)
  assert.doesNotMatch(result.answerText, /用户下降|全国恢复|政府/)
})

test('IP 地址变化趋势走当前 publication 时序算子而非正式历史趋势', async () => {
  const result = await answerControlledIpQuestion(
    goal('ip_address_trend'),
    new InMemoryReadSeriesTool(payload()),
  )
  assert.equal(result.groundingPlan.analysisMode, 'event_window_trend')
  assert.equal(result.answerability, 'supported')
  assert.match(result.answerText, /当前 publication 观测窗口内的确定性时序概括/)
  assert.doesNotMatch(result.answerText, /历史趋势产品|正式趋势不可用/)
  assert.doesNotMatch(result.answerText, /已恢复|正在恢复|恢复了|进入恢复/)
})

test('历史或跨事件趋势在 S1 失败关闭且零执行节点', () => {
  const plan = groundControlledIpGoal(goal('ip_address_trend', { timeScope: 'historical' }))
  assert.equal(plan.answerability, 'unsupported')
  assert.equal(plan.nodes.length, 0)
  assert.equal(plan.reasonCode, 'formal_or_cross_event_trend_not_in_s1')
})

test('显式请求正式历史趋势产品可表达，并在 S1 失败关闭', () => {
  const plan = groundControlledIpGoal(goal('ip_address_trend', {
    trendProduct: 'formal_historical',
  }))
  assert.equal(plan.normalizedUserGoal.timeScope, 'historical')
  assert.equal(plan.normalizedUserGoal.formalHistoricalTrend, true)
  assert.equal(plan.formalHistoricalTrend, true)
  assert.equal(plan.answerability, 'unsupported')
  assert.equal(plan.nodes.length, 0)
})

test('缺事件绑定返回 clarify、零节点且不提交状态', async () => {
  const unbound = goal('ip_address_change')
  unbound.binding = null
  const result = await answerControlledIpQuestion(unbound, new InMemoryReadSeriesTool(payload()))
  assert.equal(result.answerability, 'clarify')
  assert.equal(result.groundingPlan.nodes.length, 0)
  assert.equal(result.stateCommit, 'none')
})

test('单地址族轨道缺失降级 partial，缺失不解释为 0', async () => {
  const fixture = payload()
  delete fixture.tracks.fixed_visible_ipv6_slash48_count
  delete fixture.definitions.fixed_visible_ipv6_slash48_count
  const result = await answerControlledIpQuestion(goal('ip_address_change'), new InMemoryReadSeriesTool(fixture))
  assert.equal(result.answerability, 'partial')
  assert.equal(result.summaries.length, 1)
  assert.deepEqual(result.stateReceipt.committedVerifiedFamilies, ['ipv4'])
  assert.equal(result.stateReceipt.after.dialog.addressFamily, 'both')
  assert.equal(result.stateReceipt.after.evidence.verifiedMetrics.includes('fixed_visible_ipv6_slash48_count'), false)
  assert.equal(result.stateReceipt.after.evidence.verifiedGoalIds.includes(result.goalId), false)
  assert.equal(result.stateReceipt.rejectedOrMissingMetrics.includes('fixed_visible_ipv6_slash48_count'), true)
  assert.match(result.answerText, /轨道不可用/)
  assert.doesNotMatch(result.answerText, /IPv6 固定 cohort：从 0/)
})

test('两条固定轨道全 null 为 invalid_data，不发布事实、不提交状态', async () => {
  const fixture = payload()
  fixture.tracks.fixed_visible_ipv4_address_count = [null, null, null, null, null]
  fixture.tracks.fixed_visible_ipv6_slash48_count = [null, null, null, null, null]
  const result = await answerControlledIpQuestion(goal('ip_address_change'), new InMemoryReadSeriesTool(fixture))
  assert.equal(result.answerability, 'invalid_data')
  assert.equal(result.reasonCode, 'all_fixed_tracks_unavailable')
  assert.equal(result.summaries.length, 0)
  assert.equal(result.evidenceRefs.length, 0)
  assert.equal(result.stateCommit, 'none')
  assert.deepEqual(result.stateReceipt.after, result.stateReceipt.before)
  assert.match(result.answerText, /不可将缺失解释为 0/)
})

test('空时间轴、轨道长度错误、单位错误和身份冲突都失败关闭', async (t) => {
  const variants: Array<{ name: string; mutate(value: SeriesPayload): void; code: string }> = [
    { name: '空时间轴', mutate: (value) => { value.timestamps = [] }, code: 'empty_timestamps' },
    {
      name: '轨道长度错误',
      mutate: (value) => { value.tracks.fixed_visible_ipv4_address_count = [1] },
      code: 'track_length_mismatch',
    },
    {
      name: '单位错误',
      mutate: (value) => {
        value.definitions.fixed_visible_ipv4_address_count = { unit: 'prefix', definition: 'wrong' }
      },
      code: 'unit_mismatch',
    },
    {
      name: '指标定义错误',
      mutate: (value) => {
        value.definitions.fixed_visible_ipv4_address_count = {
          unit: 'unique_ipv4_address',
          definition: '错误人口定义',
        }
      },
      code: 'metric_definition_mismatch',
    },
    {
      name: '身份冲突',
      mutate: (value) => { value.binding.publicationId = 'other-publication' },
      code: 'identity_conflict',
    },
    {
      name: '原始响应 SHA 无效',
      mutate: (value) => { value.sourceReceipt.responseSha256 = 'caller-claim' },
      code: 'series_source_receipt_invalid',
    },
  ]
  for (const variant of variants) {
    await t.test(variant.name, async () => {
      const fixture = payload()
      variant.mutate(fixture)
      const result = await answerControlledIpQuestion(goal('ip_address_change'), new InMemoryReadSeriesTool(fixture))
      assert.equal(result.answerability, 'invalid_data')
      assert.equal(result.reasonCode, variant.code)
      assert.equal(result.summaries.length, 0)
      assert.equal(result.evidenceRefs.length, 0)
      assert.equal(result.stateCommit, 'none')
      assert.deepEqual(result.stateReceipt.after, result.stateReceipt.before)
    })
  }
})

test('累计新前缀只取各轨道最后有效值，不冒充 data-through 且不生成恢复语义', async () => {
  const result = await answerControlledIpQuestion(
    goal('ip_address_trend', { population: 'new_prefix_only', addressFamily: 'both' }),
    new InMemoryReadSeriesTool(payload()),
  )
  assert.equal(result.answerability, 'supported')
  assert.equal(result.summaries.length, 0)
  assert.equal(result.currentValues.length, 8)
  assert.doesNotMatch(result.answerText, /下降后回升|已恢复|正在恢复|恢复了|进入恢复/)
  assert.match(result.answerText, /累计出现 700 条/)
  assert.match(result.answerText, /data-through 点/)
})

test('data-through 新前缀尾点为 null 时 unavailable，不向前回填', async () => {
  const fixture = payload()
  fixture.tracks.new_visible_ipv4_prefix_count![4] = null
  const result = await answerControlledIpQuestion(
    goal('ip_address_change'),
    new InMemoryReadSeriesTool(fixture),
  )
  assert.equal(result.answerability, 'partial')
  assert.equal(result.currentValues.some((value) => (
    value.metric === 'new_visible_ipv4_prefix_count'
  )), false)
  assert.ok(result.stateReceipt.rejectedOrMissingMetrics.includes('new_visible_ipv4_prefix_count'))
  assert.match(result.answerText, /补充轨道不完整/)
})

test('非空 EvidenceState 在 partial 提交时合并保留，不丢失旧事实', async () => {
  const before = emptyPageQaState()
  before.evidence.bindingIdentity = [
    binding.incidentId,
    binding.publicationId,
    binding.revision,
    binding.collectorId,
    binding.dataThrough,
  ].join(':')
  before.evidence.verifiedGoalIds = ['old-goal']
  before.evidence.verifiedMetrics = ['fixed_visible_ipv6_slash48_count']
  before.evidence.metricEvidenceRefs.fixed_visible_ipv6_slash48_count = ['old:evidence']
  const fixture = payload()
  fixture.tracks.new_visible_ipv4_prefix_count![4] = null
  const result = await answerControlledIpQuestion(
    goal('ip_address_change', { addressFamily: 'ipv4' }),
    new InMemoryReadSeriesTool(fixture),
    undefined,
    before,
  )
  assert.equal(result.answerability, 'partial')
  assert.deepEqual(result.stateReceipt.after.evidence.verifiedGoalIds, ['old-goal'])
  assert.ok(result.stateReceipt.after.evidence.verifiedMetrics.includes('fixed_visible_ipv6_slash48_count'))
  assert.deepEqual(
    result.stateReceipt.after.evidence.metricEvidenceRefs.fixed_visible_ipv6_slash48_count,
    ['old:evidence'],
  )
})

test('预取消请求不调用 Tool 且不提交状态', async () => {
  const controller = new AbortController()
  controller.abort()
  let called = false
  const tool = {
    toolId: 'TOOL-P1-PAGE-SERIES-READ' as const,
    async read(): Promise<SeriesPayload> {
      called = true
      return payload()
    },
  }
  const result = await answerControlledIpQuestion(goal('ip_address_change'), tool, controller.signal)
  assert.equal(called, false)
  assert.equal(result.reasonCode, 'request_aborted')
  assert.equal(result.stateCommit, 'none')
})

test('权限拒绝在 resolver 和 series 前失败关闭', async () => {
  let verifierCalled = false
  let seriesCalled = false
  const verifier = {
    toolId: 'TOOL-P1-EVENT-BINDING-VERIFY' as const,
    async verify() {
      verifierCalled = true
      return new LocalEventBindingVerifier().verify(binding, [])
    },
  }
  const tool = {
    toolId: 'TOOL-P1-PAGE-SERIES-READ' as const,
    async read(): Promise<SeriesPayload> {
      seriesCalled = true
      return payload()
    },
  }
  const result = await answerControlledIpQuestion(
    goal('ip_address_change'),
    tool,
    undefined,
    undefined,
    verifier,
    { grantedPermissions: [], timeoutMs: 100 },
  )
  assert.equal(verifierCalled, false)
  assert.equal(seriesCalled, false)
  assert.equal(result.reasonCode, 'permission_denied')
  assert.equal(result.stateCommit, 'none')
})

test('宿主未显式传入权限上下文时默认拒绝', async () => {
  const result = await executeControlledIpQuestion(
    goal('ip_address_change'),
    new InMemoryReadSeriesTool(payload()),
  )
  assert.equal(result.reasonCode, 'permission_denied')
  assert.equal(result.stateCommit, 'none')
})

test('宿主授权但未显式注入 live verifier 时仍默认拒绝', async () => {
  const result = await executeControlledIpQuestion(
    goal('ip_address_change'),
    new InMemoryReadSeriesTool(payload()),
    undefined,
    undefined,
    undefined,
    { grantedPermissions: ['country_outage_event_read'], timeoutMs: 100 },
  )
  assert.equal(result.reasonCode, 'live_binding_verifier_required')
  assert.equal(result.stateCommit, 'none')
})

test('既有 EvidenceState 属于其他 publication 时在 verifier 和 Tool 前失败关闭', async () => {
  const before = emptyPageQaState()
  before.evidence.bindingIdentity = 'other:publication:2:rrc25:2026-03-12T00:00:00Z'
  before.evidence.verifiedGoalIds = ['old-goal']
  let verifierCalled = false
  let seriesCalled = false
  const verifier = {
    toolId: 'TOOL-P1-EVENT-BINDING-VERIFY' as const,
    async verify() {
      verifierCalled = true
      return new LocalEventBindingVerifier().verify(binding, [])
    },
  }
  const tool = {
    toolId: 'TOOL-P1-PAGE-SERIES-READ' as const,
    async read(): Promise<SeriesPayload> {
      seriesCalled = true
      return payload()
    },
  }
  const result = await answerControlledIpQuestion(
    goal('ip_address_change'),
    tool,
    undefined,
    before,
    verifier,
  )
  assert.equal(verifierCalled, false)
  assert.equal(seriesCalled, false)
  assert.equal(result.reasonCode, 'evidence_state_binding_conflict')
  assert.deepEqual(result.stateReceipt.after, before)
})

test('超时后零事实、零证据、零状态提交', async () => {
  let seriesCalled = false
  const tool = {
    toolId: 'TOOL-P1-PAGE-SERIES-READ' as const,
    async read(_request: unknown, signal?: AbortSignal): Promise<SeriesPayload> {
      seriesCalled = true
      await new Promise<void>((resolve, reject) => {
        const timer = setTimeout(resolve, 50)
        signal?.addEventListener('abort', () => {
          clearTimeout(timer)
          reject(new Error('aborted'))
        }, { once: true })
      })
      return payload()
    },
  }
  const result = await answerControlledIpQuestion(
    goal('ip_address_change'),
    tool,
    undefined,
    undefined,
    undefined,
    { grantedPermissions: ['country_outage_event_read'], timeoutMs: 5 },
  )
  assert.equal(seriesCalled, true)
  assert.equal(result.reasonCode, 'tool_timeout')
  assert.equal(result.evidenceRefs.length, 0)
  assert.equal(result.stateCommit, 'none')
})

test('正式 general series API 响应映射仍执行身份、单位和轨道校验', async () => {
  const bindingVerification = await new LocalEventBindingVerifier().verify(
    binding,
    ['CAP-001', 'CAP-002', 'CAP-006', 'CAP-007', 'CAP-016'],
  )
  const request = {
    binding,
    bindingVerification,
    metrics: [
      'fixed_visible_ipv4_address_count',
      'fixed_visible_ipv6_slash48_count',
    ] as AddressMetric[],
  }
  const sourceReceipt = payload().sourceReceipt
  const mapped = mapGeneralSeriesApiResponse(rawApiResponse(), request, sourceReceipt)
  assert.equal(mapped.timestamps.length, 5)
  assert.equal(mapped.tracks.fixed_visible_ipv4_address_count?.[0], 10156800)
  const summary = summarizeSeries(
    'fixed_visible_ipv4_address_count',
    mapped.timestamps,
    mapped.tracks.fixed_visible_ipv4_address_count!,
    sourceReceipt,
  )
  assert.ok(summary?.evidenceRefs.some((ref) => (
    ref.endsWith('#/track_definitions/fixed_visible_ipv4_address_count')
  )))
  const conflict = rawApiResponse()
  conflict.publication_id = 'wrong-publication'
  assert.throws(() => mapGeneralSeriesApiResponse(conflict, request, sourceReceipt), /identity mismatch/)
  const wrongEvent = rawApiResponse()
  wrongEvent.event_type = 'other_event'
  assert.throws(() => mapGeneralSeriesApiResponse(wrongEvent, request, sourceReceipt), /event_type/)
  const wrongCountry = rawApiResponse()
  wrongCountry.country_code = 'RU'
  assert.throws(() => mapGeneralSeriesApiResponse(wrongCountry, request, sourceReceipt), /country_code/)
})

test('HTTP Tool 固定调用事件 series 端点并携带 publication/revision', async () => {
  let requestedUrl = ''
  const tool = new HttpReadSeriesTool('http://example.test', async (url) => {
    requestedUrl = url
    const raw = JSON.stringify(rawApiResponse())
    return {
      ok: true,
      status: 200,
      async json(): Promise<unknown> { return JSON.parse(raw) },
      async text(): Promise<string> { return raw },
    }
  })
  const result = await answerControlledIpQuestion(goal('ip_address_change'), tool)
  assert.equal(result.answerability, 'supported')
  assert.match(requestedUrl, /\/api\/v2\/country-outages\/incident_go_v1_/)
  assert.match(requestedUrl, /publication_id=country_outage_publication_v1_/)
  assert.match(requestedUrl, /revision=1/)
})

test('HTTP 事件绑定验证器读取正式 resolver 原始回执并协商能力', async () => {
  let requestedUrl = ''
  const verifier = new HttpEventBindingVerifier('http://example.test', async (url) => {
    if (url.includes('/api/v2/events/resolve')) requestedUrl = url
    return identityFetcher(resolverApiResponse())(url)
  })
  const receipt = await verifier.verify(binding, [
    'CAP-001', 'CAP-002', 'CAP-006', 'CAP-007', 'CAP-008', 'CAP-009', 'CAP-016', 'CAP-017',
  ])
  assert.equal(receipt.verificationMode, 'live_resolver')
  assert.equal(receipt.resolverIdentity.countryCode, 'IR')
  assert.equal(receipt.resolverIdentity.publicationId, binding.publicationId)
  assert.equal(receipt.resolverResponseSha256.length, 64)
  assert.equal(receipt.overviewResponseSha256.length, 64)
  assert.deepEqual(receipt.sourceCapabilities, { eventSeries: 'available', overview: 'available' })
  assert.equal(receipt.negotiatedCapabilityIds.length, 8)
  assert.match(requestedUrl, /\/api\/v2\/events\/resolve\?ref=/)
})

test('真实 resolver 身份冲突时在 series Tool 之前失败关闭', async () => {
  const rawResolver = resolverApiResponse()
  rawResolver.country_code = 'RU'
  const verifier = new HttpEventBindingVerifier('http://example.test', identityFetcher(rawResolver))
  let seriesCalled = false
  const tool = {
    toolId: 'TOOL-P1-PAGE-SERIES-READ' as const,
    async read(): Promise<SeriesPayload> {
      seriesCalled = true
      return payload()
    },
  }
  const result = await answerControlledIpQuestion(
    goal('ip_address_change'),
    tool,
    undefined,
    undefined,
    verifier,
  )
  assert.equal(seriesCalled, false)
  assert.equal(result.reasonCode, 'binding_resolver_conflict')
  assert.equal(result.stateCommit, 'none')
  assert.equal(result.bindingVerification, null)
})

test('真实 resolver 能力不可用时不接受调用方自报 capability', async () => {
  const rawResolver = resolverApiResponse()
  rawResolver.capabilities = { event_series: 'unavailable', overview: 'available' }
  const verifier = new HttpEventBindingVerifier('http://example.test', identityFetcher(rawResolver))
  let seriesCalled = false
  const tool = {
    toolId: 'TOOL-P1-PAGE-SERIES-READ' as const,
    async read(): Promise<SeriesPayload> {
      seriesCalled = true
      return payload()
    },
  }
  const result = await answerControlledIpQuestion(
    goal('ip_address_change'),
    tool,
    undefined,
    undefined,
    verifier,
  )
  assert.equal(seriesCalled, false)
  assert.equal(result.reasonCode, 'source_capability_unavailable')
  assert.equal(result.stateCommit, 'none')
})

test('overview 与 resolver publication 身份冲突时在 series 前失败关闭', async () => {
  const wrongOverview = overviewApiResponse()
  wrongOverview.publication_id = 'wrong-publication'
  const verifier = new HttpEventBindingVerifier(
    'http://example.test',
    identityFetcher(resolverApiResponse(), wrongOverview),
  )
  let seriesCalled = false
  const tool = {
    toolId: 'TOOL-P1-PAGE-SERIES-READ' as const,
    async read(): Promise<SeriesPayload> {
      seriesCalled = true
      return payload()
    },
  }
  const result = await answerControlledIpQuestion(
    goal('ip_address_change'),
    tool,
    undefined,
    undefined,
    verifier,
  )
  assert.equal(seriesCalled, false)
  assert.equal(result.reasonCode, 'binding_overview_conflict')
  assert.equal(result.stateCommit, 'none')
})

test('真实 resolver 回执进入最终答案证据和状态提交链', async () => {
  const verifier = new HttpEventBindingVerifier(
    'http://example.test',
    identityFetcher(resolverApiResponse()),
  )
  const result = await answerControlledIpQuestion(
    goal('ip_address_trend'),
    new InMemoryReadSeriesTool(payload()),
    undefined,
    undefined,
    verifier,
  )
  assert.equal(result.answerability, 'supported')
  assert.equal(result.bindingVerification?.verificationMode, 'live_resolver')
  assert.ok(result.evidenceRefs.some((ref) => ref.startsWith('sha256:')))
  assert.equal(result.stateCommit, 'committed')
})

test('事件级 capability 未协商时在 series Tool 之前失败关闭', async () => {
  const unnegotiated = goal('ip_address_change')
  unnegotiated.binding = {
    ...binding,
    capabilityIds: binding.capabilityIds.filter((capabilityId) => capabilityId !== 'CAP-009'),
  }
  let called = false
  const tool = {
    toolId: 'TOOL-P1-PAGE-SERIES-READ' as const,
    async read(): Promise<SeriesPayload> {
      called = true
      return payload()
    },
  }
  const result = await answerControlledIpQuestion(unnegotiated, tool)
  assert.equal(called, false)
  assert.equal(result.reasonCode, 'capability_not_negotiated')
  assert.equal(result.stateCommit, 'none')
})

test('Tool 返回后的取消同样零发布、零证据、零状态提交', async () => {
  const controller = new AbortController()
  const tool = {
    toolId: 'TOOL-P1-PAGE-SERIES-READ' as const,
    async read(): Promise<SeriesPayload> {
      controller.abort()
      return payload()
    },
  }
  const result = await answerControlledIpQuestion(
    goal('ip_address_change'),
    tool,
    controller.signal,
  )
  assert.equal(result.reasonCode, 'request_aborted')
  assert.equal(result.summaries.length, 0)
  assert.equal(result.evidenceRefs.length, 0)
  assert.equal(result.stateCommit, 'none')
  assert.deepEqual(result.stateReceipt.after, result.stateReceipt.before)
})
