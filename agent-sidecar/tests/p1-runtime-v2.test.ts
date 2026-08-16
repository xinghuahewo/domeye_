import assert from 'node:assert/strict'
import test from 'node:test'

import {
  P1ReadModelError,
  HttpP1GeneralReadModelProvider,
  P1RuntimeV2SingleTurnError,
  P1RuntimeV2SingleTurnService,
  type P1ConversationBinding,
  type P1RuntimeV2ReadProvider,
} from '../src/chat/index.js'

const eventReference = 'country_outage/2026-02-27 09:12:32/IR/1/r'
const publicationId = 'country_outage_publication_v1_989f698fb6f6c32579eebe7bb2bc833f'

function binding(): P1ConversationBinding {
  return {
    event_type: 'country_outage',
    incident_id: 'incident_go_v1_a1de26f854831330c616a72af21597eb',
    legacy_reference: eventReference,
    publication_id: publicationId,
    revision: 1,
    collector_id: 'rrc25',
    cohort_id: 'country_event_cohort_v1_1e04abfc6430776bef20403fac528698',
    country_code: 'IR',
    detected_at_utc: '2026-02-27T01:12:32Z',
    window_start_utc: '2026-02-27T00:10:00Z',
    window_end_utc: '2026-03-11T00:00:00Z',
    data_through: '2026-03-11T00:00:00Z',
    is_final_in_data_range: false,
    lifecycle_state: 'event_end_unknown',
    observation_state: 'evidence_complete',
    quality_state: 'complete',
    missing_slot_count: 0,
    capabilities: {
      overview: 'available',
      event_series: 'available',
      affected_as: 'available',
      path_downstreams: 'available',
      full_path_evidence: 'audit_only',
    },
  }
}

function overview() {
  const identity = binding()
  return {
    ...identity,
    event: {
      detected_at_utc: identity.detected_at_utc,
      event_end_at_utc: null,
      event_duration_seconds: null,
    },
    cohort: {
      cohort_id: identity.cohort_id,
      fixed_asn_count: 572,
      fixed_prefix_count: 9257,
      independent_direction_relation_count: 368675,
      new_prefix_count: 701,
    },
    current: {
      interrupted_prefix_count: 1024,
    },
    peaks: {
      interrupted_prefix_count: {
        value: 3855,
        state_point_utc: '2026-02-27T23:15:00Z',
      },
    },
    affected_as_count: 525,
    semantic_boundary: 'rrc25_control_plane_observation_not_user_impact_or_cause',
  }
}

class FakeRuntimeProvider implements P1RuntimeV2ReadProvider {
  resolved = binding()
  overviewValue = overview()
  resolveCalls = 0
  overviewCalls = 0
  overviewError: Error | null = null
  afterResolve: (() => void) | null = null
  afterOverview: (() => void) | null = null

  async resolve() {
    this.resolveCalls += 1
    const result = structuredClone(this.resolved)
    this.afterResolve?.()
    return result
  }

  async readOverview() {
    this.overviewCalls += 1
    if (this.overviewError) throw this.overviewError
    const result = structuredClone(this.overviewValue)
    this.afterOverview?.()
    return result
  }
}

const principal = {
  userId: 'p1-runtime-v2-user',
  authorizationScope: 'country_outage_event_read',
}

const request = {
  event_reference: eventReference,
  publication_id: publicationId,
  revision: 1,
  controlled_goal: 'event_summary' as const,
}

test('S1 event_summary 垂直切片只发布同一身份的确定性事实', async () => {
  const provider = new FakeRuntimeProvider()
  const service = new P1RuntimeV2SingleTurnService(
    provider,
    () => new Date('2026-08-09T11:00:00Z'),
  )
  const answer = await service.answer(principal, request)
  assert.equal(answer.answerability, 'partial')
  assert.equal(answer.binding.publication_id, publicationId)
  assert.equal(answer.execution_trace.model_generated_fact_count, 0)
  assert.equal(answer.execution_trace.state_commit, 'none')
  assert.deepEqual(answer.execution_trace.authorization, {
    original_scope: 'country_outage_event_read',
    effective_permission: 'country_outage:read',
    basis: 'event_read_global',
    country_code: 'IR',
  })
  assert.deepEqual(
    answer.execution_trace.nodes.map((node) => node.execution_unit),
    ['TOOL-01', 'TOOL-02'],
  )
  assert.equal(
    answer.evidence.find((item) =>
      item.evidence_ref === 'overview.peaks.interrupted_prefix_count.value'
    )?.value,
    3855,
  )
  assert.match(answer.answer_text, /事件结束时间未知/)
  assert.match(answer.answer_text, /不能写成事件已经恢复或结束/)
  assert.doesNotMatch(answer.answer_text, /全国完全断网|政府/)
  assert.equal(provider.resolveCalls, 1)
  assert.equal(provider.overviewCalls, 1)
})

test('代表事件以外的受影响 AS 人口不会被硬编码为 525', async () => {
  const provider = new FakeRuntimeProvider()
  provider.overviewValue.affected_as_count = 7
  const answer = await new P1RuntimeV2SingleTurnService(provider).answer(
    principal,
    request,
  )
  assert.match(answer.answer_text, /7 个不同 AS/)
  assert.match(answer.limitations.join('\n'), /受影响 AS 7 是窗口内不同 AS 人口/)
  assert.doesNotMatch(answer.answer_text + answer.limitations.join('\n'), /受影响 AS 525/)
})

test('event_end_at_utc=null 保持未知且不解释为零或恢复', async () => {
  const answer = await new P1RuntimeV2SingleTurnService(
    new FakeRuntimeProvider(),
  ).answer(principal, request)
  const end = answer.evidence.find((item) =>
    item.evidence_ref === 'overview.event.event_end_at_utc'
  )
  assert.equal(end?.value, null)
  assert.ok(answer.unknowns.includes('event_end_at_utc'))
  assert.doesNotMatch(answer.answer_text, /0 秒|已结束|已恢复/)
})

test('关键事实缺失时整轮失败且不把空值解释为零', async () => {
  const provider = new FakeRuntimeProvider()
  ;(provider.overviewValue.current as Record<string, unknown>)
    .interrupted_prefix_count = undefined
  const service = new P1RuntimeV2SingleTurnService(provider)
  await assert.rejects(
    service.answer(principal, request),
    (error: unknown) =>
      error instanceof P1RuntimeV2SingleTurnError
      && error.code === 'invalid_data'
      && !error.message.includes('0'),
  )
})

test('关键数值为 null 时整轮失败且不把 null 解释为零', async () => {
  const provider = new FakeRuntimeProvider()
  ;(provider.overviewValue.current as Record<string, unknown>)
    .interrupted_prefix_count = null
  await assert.rejects(
    new P1RuntimeV2SingleTurnService(provider).answer(principal, request),
    (error: unknown) =>
      error instanceof P1RuntimeV2SingleTurnError
      && error.code === 'invalid_data',
  )
})

test('关键字符串为空时整轮失败且不发布证据', async () => {
  const provider = new FakeRuntimeProvider()
  provider.overviewValue.event.detected_at_utc = '   '
  await assert.rejects(
    new P1RuntimeV2SingleTurnService(provider).answer(principal, request),
    (error: unknown) =>
      error instanceof P1RuntimeV2SingleTurnError
      && error.code === 'invalid_data',
  )
})

test('空事件引用在任何 Tool 调用前失败关闭', async () => {
  const provider = new FakeRuntimeProvider()
  await assert.rejects(
    new P1RuntimeV2SingleTurnService(provider).answer(principal, {
      ...request,
      event_reference: '   ',
    }),
    (error: unknown) =>
      error instanceof P1RuntimeV2SingleTurnError
      && error.code === 'invalid_reference',
  )
  assert.equal(provider.resolveCalls, 0)
  assert.equal(provider.overviewCalls, 0)
})

test('错误 publication 在调用 overview 前失败关闭', async () => {
  const provider = new FakeRuntimeProvider()
  provider.resolved.publication_id = 'different-publication'
  const service = new P1RuntimeV2SingleTurnService(provider)
  await assert.rejects(
    service.answer(principal, request),
    (error: unknown) =>
      error instanceof P1RuntimeV2SingleTurnError
      && error.code === 'binding_conflict',
  )
  assert.equal(provider.overviewCalls, 0)
})

test('权限拒绝不调用任何 Tool', async () => {
  const provider = new FakeRuntimeProvider()
  const service = new P1RuntimeV2SingleTurnService(provider)
  await assert.rejects(
    service.answer(
      { userId: 'denied', authorizationScope: 'country_outage_report_write' },
      request,
    ),
    (error: unknown) =>
      error instanceof P1RuntimeV2SingleTurnError
      && error.code === 'permission_denied',
  )
  assert.equal(provider.resolveCalls, 0)
  assert.equal(provider.overviewCalls, 0)
})

test('国家 scoped 权限只允许与解析国家一致的事件并保留原始 scope', async () => {
  const provider = new FakeRuntimeProvider()
  const answer = await new P1RuntimeV2SingleTurnService(provider).answer(
    {
      userId: 'ir-reader',
      authorizationScope: 'profile:read,country_outage_event_read:IR',
    },
    request,
  )
  assert.deepEqual(answer.execution_trace.authorization, {
    original_scope: 'profile:read,country_outage_event_read:IR',
    effective_permission: 'country_outage:read',
    basis: 'event_read_country',
    country_code: 'IR',
  })
})

test('错误国家 scoped 权限在解析后、overview 前失败关闭', async () => {
  const provider = new FakeRuntimeProvider()
  await assert.rejects(
    new P1RuntimeV2SingleTurnService(provider).answer(
      {
        userId: 'cn-reader',
        authorizationScope: 'profile:read,country_outage_event_read:CN',
      },
      request,
    ),
    (error: unknown) =>
      error instanceof P1RuntimeV2SingleTurnError
      && error.code === 'permission_denied',
  )
  assert.equal(provider.resolveCalls, 1)
  assert.equal(provider.overviewCalls, 0)
})

test('canonical 权限在组合 scope 中优先映射且可审计', async () => {
  const provider = new FakeRuntimeProvider()
  const answer = await new P1RuntimeV2SingleTurnService(provider).answer(
    {
      userId: 'canonical-reader',
      authorizationScope: 'country_outage_event_read:CN,country_outage:read',
    },
    request,
  )
  assert.equal(answer.execution_trace.authorization.basis, 'canonical_read')
  assert.equal(
    answer.execution_trace.authorization.original_scope,
    'country_outage_event_read:CN,country_outage:read',
  )
})

test('事件未协商 overview 能力时不执行 TOOL-02', async () => {
  const provider = new FakeRuntimeProvider()
  provider.resolved.capabilities.overview = 'unavailable'
  const service = new P1RuntimeV2SingleTurnService(provider)
  await assert.rejects(
    service.answer(principal, request),
    (error: unknown) =>
      error instanceof P1RuntimeV2SingleTurnError
      && error.code === 'capability_unavailable',
  )
  assert.equal(provider.resolveCalls, 1)
  assert.equal(provider.overviewCalls, 0)
})

test('Tool 超时保持 retryable 且没有半成品回答', async () => {
  const provider = new FakeRuntimeProvider()
  provider.overviewError = new P1ReadModelError(
    'tool_timeout',
    'overview 超时',
    true,
  )
  const service = new P1RuntimeV2SingleTurnService(provider)
  await assert.rejects(
    service.answer(principal, request),
    (error: unknown) =>
      error instanceof P1RuntimeV2SingleTurnError
      && error.code === 'tool_timeout'
      && error.retryable,
  )
})

test('调用前已取消时不执行任何 Tool 且不发布半成品', async () => {
  const provider = new FakeRuntimeProvider()
  const controller = new AbortController()
  controller.abort()
  await assert.rejects(
    new P1RuntimeV2SingleTurnService(provider).answer(
      principal,
      request,
      controller.signal,
    ),
    (error: unknown) =>
      error instanceof P1RuntimeV2SingleTurnError
      && error.code === 'cancelled'
      && !error.retryable,
  )
  assert.equal(provider.resolveCalls, 0)
  assert.equal(provider.overviewCalls, 0)
})

test('TOOL-01 与 TOOL-02 之间取消时不执行第二个 Tool', async () => {
  const provider = new FakeRuntimeProvider()
  const controller = new AbortController()
  provider.afterResolve = () => controller.abort()
  await assert.rejects(
    new P1RuntimeV2SingleTurnService(provider).answer(
      principal,
      request,
      controller.signal,
    ),
    (error: unknown) =>
      error instanceof P1RuntimeV2SingleTurnError
      && error.code === 'cancelled',
  )
  assert.equal(provider.resolveCalls, 1)
  assert.equal(provider.overviewCalls, 0)
})

test('TOOL-02 返回后取消时不发布完整或半成品回答', async () => {
  const provider = new FakeRuntimeProvider()
  const controller = new AbortController()
  provider.afterOverview = () => controller.abort()
  await assert.rejects(
    new P1RuntimeV2SingleTurnService(provider).answer(
      principal,
      request,
      controller.signal,
    ),
    (error: unknown) =>
      error instanceof P1RuntimeV2SingleTurnError
      && error.code === 'cancelled',
  )
  assert.equal(provider.resolveCalls, 1)
  assert.equal(provider.overviewCalls, 1)
})

test('overview 检测时间与绑定身份冲突时拒绝发布', async () => {
  const provider = new FakeRuntimeProvider()
  provider.overviewValue.event.detected_at_utc = '2026-02-27T02:00:00Z'
  const service = new P1RuntimeV2SingleTurnService(provider)
  await assert.rejects(
    service.answer(principal, request),
    (error: unknown) =>
      error instanceof P1RuntimeV2SingleTurnError
      && error.code === 'publication_identity_conflict',
  )
})

test('resolve 不提供检测时间时由同 publication overview 补齐而不从引用猜测', async () => {
  const provider = new FakeRuntimeProvider()
  provider.resolved.detected_at_utc = null
  const answer = await new P1RuntimeV2SingleTurnService(provider).answer(
    principal,
    request,
  )
  assert.equal(answer.binding.detected_at_utc, '2026-02-27T01:12:32Z')
  assert.equal(
    answer.evidence.find((item) =>
      item.evidence_ref === 'overview.event.detected_at_utc'
    )?.value,
    '2026-02-27T01:12:32Z',
  )
  assert.equal(
    answer.evidence.some((item) =>
      item.evidence_ref === 'resolution.detected_at_utc'
    ),
    false,
  )
})

test('真实 HTTP Provider 将内部超时区分为 retryable tool_timeout', async () => {
  const previousFetch = globalThis.fetch
  globalThis.fetch = ((_input: string | URL | Request, init?: RequestInit) =>
    new Promise((_resolve, reject) => {
      init?.signal?.addEventListener('abort', () => {
        const error = new Error('aborted')
        error.name = 'AbortError'
        reject(error)
      }, { once: true })
    })) as typeof fetch
  try {
    const provider = new HttpP1GeneralReadModelProvider('http://127.0.0.1/', 5)
    await assert.rejects(
      provider.resolve(eventReference),
      (error: unknown) =>
        error instanceof P1ReadModelError
        && error.code === 'tool_timeout'
        && error.retryable,
    )
  } finally {
    globalThis.fetch = previousFetch
  }
})

test('真实 HTTP Provider 将用户取消区分为非重试 cancelled', async () => {
  const previousFetch = globalThis.fetch
  globalThis.fetch = ((_input: string | URL | Request, init?: RequestInit) =>
    new Promise((_resolve, reject) => {
      init?.signal?.addEventListener('abort', () => {
        const error = new Error('aborted')
        error.name = 'AbortError'
        reject(error)
      }, { once: true })
    })) as typeof fetch
  const controller = new AbortController()
  try {
    const provider = new HttpP1GeneralReadModelProvider('http://127.0.0.1/', 1000)
    const pending = provider.resolve(eventReference, controller.signal)
    controller.abort()
    await assert.rejects(
      pending,
      (error: unknown) =>
        error instanceof P1ReadModelError
        && error.code === 'cancelled'
        && !error.retryable,
    )
  } finally {
    globalThis.fetch = previousFetch
  }
})

test('真实 HTTP Provider 对预取消信号不发起 fetch', async () => {
  const previousFetch = globalThis.fetch
  let fetchCalls = 0
  globalThis.fetch = (() => {
    fetchCalls += 1
    throw new Error('不应调用 fetch')
  }) as typeof fetch
  const controller = new AbortController()
  controller.abort()
  try {
    const provider = new HttpP1GeneralReadModelProvider('http://127.0.0.1/')
    await assert.rejects(
      provider.resolve(eventReference, controller.signal),
      (error: unknown) =>
        error instanceof P1ReadModelError
        && error.code === 'cancelled'
        && !error.retryable,
    )
    assert.equal(fetchCalls, 0)
  } finally {
    globalThis.fetch = previousFetch
  }
})
