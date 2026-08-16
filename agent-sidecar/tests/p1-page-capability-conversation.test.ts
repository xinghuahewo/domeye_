import assert from 'node:assert/strict'
import { mkdirSync, writeFileSync } from 'node:fs'
import { dirname } from 'node:path'
import { after, test } from 'node:test'

import type { CountryOutagePrincipal } from '../src/server/contracts.js'
import type { P1ConversationBinding } from '../src/chat/contracts.js'
import {
  P1ReadModelError,
  type P1AsnReadRequest,
  type P1PageCapabilityReadProvider,
  type P1PathReadRequest,
} from '../src/chat/general-read-model-provider.js'
import {
  P1RuntimeV2ConversationService,
} from '../src/chat/runtime-v2-conversation.js'
import type {
  P1UserGoal,
  P1UserGoalPlan,
  P1UserGoalPlanner,
  P1UserGoalPlannerContext,
} from '../src/chat/runtime-v2-semantic.js'

const reference = 'country_outage/2026-02-27 09:12:32/IR/1/r'
const targetReference = 'country_outage/2026-03-01 10:00:00/IR/2/r'
const publication = 'country_outage_publication_v1_test'
const targetPublication = 'country_outage_publication_v1_target'
const principal: CountryOutagePrincipal = {
  userId: 'p1-s3-user',
  authorizationScope: 'country_outage_event_read:IR',
}

const capturedFailureScenarios: Array<Record<string, unknown>> = []

after(() => {
  const outputPath = process.env.P1_S3_FAILURE_EVIDENCE_PATH
  if (!outputPath) return
  mkdirSync(dirname(outputPath), { recursive: true })
  writeFileSync(outputPath, `${JSON.stringify({
    schema_version: 'country_outage_p1_page_coverage_s3_failure_fixture_set_v1',
    evidence_kind: 'failure_fixture_set',
    stage: 'S3',
    candidate_id: process.env.P1_S3_CANDIDATE_ID ?? 'missing-candidate',
    run_id: process.env.P1_S3_RUN_ID ?? 'missing-run',
    actor_id: 's3-deterministic-failure-probe',
    captured_at: new Date().toISOString(),
    scenarios: capturedFailureScenarios,
  }, null, 2)}\n`, 'utf8')
})

function makeBinding(
  legacyReference = reference,
  publicationId = publication,
  revision = 1,
): P1ConversationBinding {
  return {
    event_type: 'country_outage',
    incident_id: publicationId === publication ? 'incident_test' : 'incident_target',
    legacy_reference: legacyReference,
    publication_id: publicationId,
    revision,
    collector_id: 'rrc25',
    cohort_id: publicationId === publication ? 'cohort_test' : 'cohort_target',
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

const primaryBinding = makeBinding()
const secondaryBinding = makeBinding(targetReference, targetPublication, 2)

function overviewFor(binding: P1ConversationBinding): Record<string, unknown> {
  return {
    ...binding,
    event: {
      legacy_reference: binding.legacy_reference,
      country_code: 'IR',
      detected_at_utc: binding.detected_at_utc,
      event_end_at_utc: null,
      event_duration_seconds: null,
    },
    cohort: { fixed_prefix_count: 9257, fixed_asn_count: 572 },
    current: {
      interrupted_prefix_count: 1024,
      completely_interrupted_prefix_count: 318,
      invisible_direction_count: 14867,
    },
    peaks: {
      interrupted_prefix_count: {
        value: 3855,
        state_point_utc: '2026-02-27T23:15:00Z',
      },
      completely_interrupted_prefix_count: {
        value: 1553,
        state_point_utc: '2026-02-28T14:35:00Z',
      },
      affected_asn_count: {
        value: 350,
        state_point_utc: '2026-03-02T11:30:00Z',
      },
      route_interrupted_asn_count: {
        value: 94,
        state_point_utc: '2026-02-28T13:50:00Z',
      },
    },
    affected_as_count: 525,
    route_interrupted_as_count: 151,
    semantic_boundary:
      'rrc25_control_plane_observation_not_user_impact_or_cause',
  }
}

const timestamps = [
  primaryBinding.window_start_utc,
  '2026-02-28T14:35:00Z',
  primaryBinding.data_through!,
]
const tracks: Record<string, Array<number | null>> = {
  interrupted_prefix_count: [0, 3855, 1024],
  completely_interrupted_prefix_count: [0, 1553, 318],
  invisible_direction_count: [0, 65783, 14867],
  affected_asn_count: [0, 350, 121],
  route_interrupted_asn_count: [0, 94, 35],
  fixed_visible_ipv4_address_count: [10156800, 9577728, 10069760],
  fixed_visible_ipv6_slash48_count: [267292, 267288, 267288],
  new_cumulative_ipv4_prefix_count: [0, 400, 700],
  new_cumulative_ipv4_address_count: [0, 100000, 244291],
  new_visible_ipv4_prefix_count: [0, 200, 111],
  new_visible_ipv4_address_count: [0, 100000, 19523],
  new_cumulative_ipv6_prefix_count: [0, 1, 1],
  new_cumulative_ipv6_slash48_count: [0, 524288, 524288],
  new_visible_ipv6_prefix_count: [0, 1, 1],
  new_visible_ipv6_slash48_count: [0, 524288, 524288],
}
const trackDefinitions = Object.fromEntries(
  Object.keys(tracks).map((metric) => [
    metric,
    {
      definition: `${metric} 的冻结测试定义`,
      unit: metric.includes('ipv6') && metric.includes('slash48')
        ? 'ipv6_slash48_equivalent'
        : metric.includes('address') ? 'unique_ipv4_address'
          : metric.includes('asn') ? 'asn' : 'prefix',
    },
  ]),
)

function seriesFor(binding: P1ConversationBinding): Record<string, unknown> {
  return {
    ...binding,
    point_count: timestamps.length,
    timestamps,
    tracks,
    track_definitions: trackDefinitions,
  }
}

class FixtureProvider implements P1PageCapabilityReadProvider {
  calls: string[] = []
  resolveCount = 0
  driftAtResolve: number | null = null
  failOverview = false
  invalidOverview = false
  blockOverview = false

  async resolve(value: string): Promise<P1ConversationBinding> {
    this.resolveCount += 1
    this.calls.push(`resolve:${value}`)
    const selected = normalizeRef(value) === normalizeRef(targetReference)
      ? secondaryBinding : primaryBinding
    if (this.driftAtResolve === this.resolveCount) {
      return { ...structuredClone(selected), revision: selected.revision + 1 }
    }
    return structuredClone(selected)
  }

  async readOverview(
    binding: P1ConversationBinding,
    signal?: AbortSignal,
  ): Promise<Record<string, unknown>> {
    this.calls.push('overview')
    if (this.failOverview) {
      throw new P1ReadModelError('data_api_unavailable', 'fixture failure', true)
    }
    if (this.invalidOverview) {
      throw new P1ReadModelError(
        'invalid_data',
        'fixture adapter rejected null current.interrupted_prefix_count',
      )
    }
    if (this.blockOverview) {
      return await new Promise((_, reject) => {
        const abort = (): void => reject(
          new P1ReadModelError('cancelled', 'fixture cancelled'),
        )
        signal?.addEventListener('abort', abort, { once: true })
        if (signal?.aborted) abort()
      })
    }
    return overviewFor(binding)
  }

  async readSeries(
    binding: P1ConversationBinding,
    metrics: string[],
  ): Promise<Record<string, unknown>> {
    this.calls.push(`series:${metrics.join(',')}`)
    return seriesFor(binding)
  }

  async readAsns(
    binding: P1ConversationBinding,
    request: P1AsnReadRequest,
  ): Promise<Record<string, unknown>> {
    this.calls.push(`asns:${request.asn ?? request.query}`)
    const asn = request.asn ?? Number(request.query || 48715)
    return {
      ...binding,
      page: 1,
      page_size: request.pageSize,
      page_count: 1,
      total: 1,
      classification: request.classification,
      query: String(request.asn ?? request.query ?? ''),
      sort: request.sort,
      items: [{
        rank: 1,
        asn,
        as_name: `AS-${asn}`,
        event_classification: 'route_interrupted',
        fixed_prefix_count: 73,
        peak_complete_prefix_count: 73,
        path_downstream_asn_count: 1,
        concurrent_downstream_asn_count: 1,
      }],
    }
  }

  async readPaths(
    binding: P1ConversationBinding,
    request: P1PathReadRequest,
  ): Promise<Record<string, unknown>> {
    this.calls.push(`paths:${request.affectedAsn ?? 'all'}`)
    return {
      ...binding,
      page: 1,
      page_size: request.pageSize,
      page_count: 1,
      total: 1,
      affected_asn: request.affectedAsn,
      scope: request.scope,
      query: request.query,
      relationship_semantics:
        'observed_ordered_rrc25_path_association_not_dependency_or_cause',
      items: [{
        affected_asn: request.affectedAsn ?? 48715,
        downstream_asn: 58224,
        observed_path_count: 4,
        path_samples: [{
          prefix: '109.74.224.0/20',
          as_path_canonical: '33874 6758 48715 58224',
          independent_peer_asns: [33874],
        }],
      }],
    }
  }

  async readAudit(binding: P1ConversationBinding): Promise<Record<string, unknown>> {
    this.calls.push('audit')
    return {
      ...binding,
      dataset_id: 'dataset_test',
      run_id: 'run_test',
      implementation_id: 'git:data-implementation',
      manifest_sha256: 'a'.repeat(64),
      event_content_sha256: 'b'.repeat(64),
      causal_boundary:
        'rrc25_path_association_is_not_dependency_propagation_user_impact_or_cause',
    }
  }
}

function normalizeRef(value: string): string {
  return value.replaceAll('+', ' ').trim()
}

function makeGoal(
  id: number,
  question: string,
  kind: string,
  entities: P1UserGoal['entities'] = {},
  dependencies: string[] = [],
  ambiguity: P1UserGoal['ambiguity'] = 'none',
  references: string[] = [],
): P1UserGoal {
  return {
    goal_id: `goal-${id}`,
    requested_goal: question,
    normalized_kind: kind,
    entities,
    references,
    ambiguity,
    context_dependencies: dependencies,
  }
}

function plan(question: string, goals: P1UserGoal[]): P1UserGoalPlan {
  return {
    plan_revision: 'user-goal-plan-v2',
    original_question: question,
    goals,
    state_proposal: { inherit: [], set: {}, clear: [], reason_codes: [] },
    planner_identity: 'scripted-output',
    confidence: 1,
  }
}

class JourneyPlanner implements P1UserGoalPlanner {
  readonly identity = 'semantic-model:s3-journey-planner'
  contexts: P1UserGoalPlannerContext[] = []

  async plan(
    question: string,
    context: P1UserGoalPlannerContext,
  ): Promise<P1UserGoalPlan> {
    this.contexts.push(structuredClone(context))
    switch (question) {
      case 'IPv4地址变化情况':
        return plan(question, [makeGoal(1, question, 'address_family_change', {
          address_family: 'ipv4',
          population: 'fixed_cohort',
          include_new_prefixes: false,
          analysis_mode: 'change_summary',
          time_scope: 'current_publication_window',
        })])
      case '那 IPv6 呢':
        return plan(question, [makeGoal(1, question, 'address_family_change', {
          address_family: 'ipv6',
        }, ['prior_population', 'prior_include_new_prefixes', 'prior_analysis_mode', 'prior_time_scope'])])
      case '把新出现前缀也带上':
        return plan(question, [
          makeGoal(1, question, 'address_family_change', {
            include_new_prefixes: false,
          }, ['prior_address_family', 'prior_population', 'prior_analysis_mode', 'prior_time_scope']),
          makeGoal(2, question, 'new_prefix_state', {
            population: 'new_prefix_only',
          }, ['prior_address_family', 'prior_time_scope']),
        ])
      case '不看新增，只看原来的':
        return plan(question, [makeGoal(1, question, 'address_family_change', {
          include_new_prefixes: false,
          population: 'fixed_cohort',
        }, ['prior_address_family', 'prior_analysis_mode', 'prior_time_scope'])])
      case 'IP地址变化情况':
        return plan(question, [
          makeGoal(1, question, 'address_family_change', {
            address_family: 'both', population: 'fixed_cohort',
            include_new_prefixes: false, analysis_mode: 'change_summary',
            time_scope: 'current_publication_window',
          }),
          makeGoal(2, question, 'new_prefix_resources', {
            address_family: 'both', population: 'new_prefix_only',
            time_scope: 'current_publication_window',
          }),
        ])
      case '这能说明用户断网吗':
        return plan(question, [makeGoal(
          1, question, 'real_user_or_national_impact', {},
        )])
      case '页面没有 Update 轨道，是不是说明 Update 一直为 0':
        return plan(question, [makeGoal(
          1, question, 'capability_absent_not_zero', {},
        )])
      case '现在还有多少前缀不可见，是不是全国都断了':
        return plan(question, [
          makeGoal(1, '现在还有多少前缀不可见', 'current_prefix_state'),
          makeGoal(2, '是不是全国都断了', 'real_user_or_national_impact'),
        ])
      case 'AS48715 的情况':
        return plan(question, [makeGoal(1, question, 'asn_detail', { asn: 48715 })])
      case '看一条 AS48715 的实际路径样本':
        return plan(question, [makeGoal(
          1, question, 'path_sample', { affected_asn: 48715 },
        )])
      case '这能说明它依赖谁吗':
        return plan(question, [makeGoal(
          1, question, 'propagation_inference', {}, ['prior_asn'],
        )])
      case '改成 AS49556':
        return plan(question, [makeGoal(1, question, 'asn_detail', { asn: 49556 })])
      case '它什么时候最严重':
        return plan(question, [makeGoal(1, question, 'prefix_peak')])
      case '到最后还剩多少':
        return plan(question, [makeGoal(
          1, question, 'metric_followup', {}, ['prior_metric'],
        )])
      case '哪个最严重':
        return plan(question, [makeGoal(
          1, question, 'ambiguous_peak_metric', {}, [], 'blocking',
        )])
      case '切换到另一个事件':
        return plan(question, [makeGoal(
          1, question, 'event_switch', {}, [], 'none', [targetReference],
        )])
      case `请切换到 ${targetReference}`:
        return plan(question, [makeGoal(
          1, question, 'event_switch', {}, [], 'none', [targetReference],
        )])
      case '当前中断前缀':
        return plan(question, [makeGoal(1, question, 'current_prefix_state')])
      default:
        return plan(question, [makeGoal(1, question, 'event_summary')])
    }
  }
}

class FailingPlanner implements P1UserGoalPlanner {
  readonly identity = 'semantic-model:failing'
  async plan(): Promise<P1UserGoalPlan> {
    throw new Error('model unavailable')
  }
}

async function create(
  provider = new FixtureProvider(),
  planner: P1UserGoalPlanner = new JourneyPlanner(),
  options: { ttlMs?: number; turnTimeoutMs?: number; now?: () => Date } = {},
): Promise<{
  provider: FixtureProvider
  service: P1RuntimeV2ConversationService
  conversationId: string
}> {
  const service = new P1RuntimeV2ConversationService({
    provider,
    planner,
    ...options,
  })
  const created = await service.createConversation(principal, {
    event_reference: reference,
    publication_id: publication,
    revision: 1,
    idempotency_key: 'create-1',
  })
  return {
    provider,
    service,
    conversationId: created.conversation.conversation_id,
  }
}

async function turn(
  service: P1RuntimeV2ConversationService,
  conversationId: string,
  question: string,
  key: string,
) {
  return (await service.createTurn(principal, conversationId, {
    question,
    idempotency_key: key,
  })).turn
}

test('S3 多轮地址族、人口和否定覆盖只在验证后提交', async () => {
  const planner = new JourneyPlanner()
  const { service, conversationId } = await create(
    new FixtureProvider(),
    planner,
  )
  const first = await turn(service, conversationId, 'IPv4地址变化情况', 't1')
  assert.equal(first.state, 'completed')
  assert.equal(first.answer?.state_receipt.after.address_family, 'ipv4')
  assert.equal(first.answer?.state_receipt.after.population, 'fixed_cohort')
  assert.equal(first.answer?.state_receipt.after.include_new_prefixes, false)

  const second = await turn(service, conversationId, '那 IPv6 呢', 't2')
  assert.equal(second.answer?.state_receipt.after.address_family, 'ipv6')
  assert.equal(second.answer?.state_receipt.after.population, 'fixed_cohort')
  assert.ok(second.answer?.semantic_plan.grounding_plan.nodes.some((node) =>
    node.execution_unit === 'TOOL-03'
    && node.input_sources.metrics === 'dialog_state'
  ))

  const third = await turn(service, conversationId, '把新出现前缀也带上', 't3')
  assert.deepEqual(
    third.answer?.semantic_plan.user_goal_plan.goals.map((goal) => goal.normalized_kind),
    ['address_family_change', 'new_prefix_state'],
  )
  assert.deepEqual(
    third.answer?.results.map((item) => item.answerability),
    ['supported', 'supported'],
  )
  assert.equal(third.answer?.state_receipt.after.address_family, 'ipv6')
  assert.equal(third.answer?.state_receipt.after.include_new_prefixes, true)
  assert.match(third.answer?.answer_text ?? '', /新前缀补充/)

  const fourth = await turn(service, conversationId, '不看新增，只看原来的', 't4')
  assert.equal(fourth.answer?.state_receipt.after.include_new_prefixes, false)
  assert.equal(fourth.answer?.state_receipt.after.population, 'fixed_cohort')
  assert.doesNotMatch(fourth.answer?.answer_text ?? '', /新前缀补充/)
  assert.equal(planner.contexts[1]?.has_dialog_state, true)
})

test('S3 泛指 IP 保持 fixed cohort 主答和新前缀独立补充', async () => {
  const { service, conversationId } = await create()
  const result = await turn(service, conversationId, 'IP地址变化情况', 'ip')
  assert.equal(result.answer?.results.length, 2)
  assert.equal(result.answer?.state_receipt.after.address_family, 'both')
  assert.equal(result.answer?.state_receipt.after.population, 'fixed_cohort')
  assert.equal(result.answer?.state_receipt.after.include_new_prefixes, true)
  assert.match(result.answer?.answer_text ?? '', /单位不同/)
})

test('S3 越界追问不覆盖前一轮可执行上下文', async () => {
  const { service, conversationId } = await create()
  await turn(service, conversationId, 'IPv4地址变化情况', 'b1')
  const boundary = await turn(service, conversationId, '这能说明用户断网吗', 'b2')
  assert.equal(boundary.answer?.answerability, 'unsupported')
  assert.equal(boundary.answer?.execution_trace.nodes.length, 0)
  assert.equal(boundary.answer?.state_receipt.status, 'none')
  assert.equal(boundary.answer?.state_receipt.after.address_family, 'ipv4')
  assert.match(boundary.answer?.answer_text ?? '', /不能据此判断全国是否断网/)
})

test('S3 缺失 Update 轨道属于 unsupported 而不是零且不改状态', async () => {
  const { service, conversationId } = await create()
  await turn(service, conversationId, 'IPv4地址变化情况', 'update-setup')
  const before = await service.getConversation(principal, conversationId)
  const result = await turn(
    service,
    conversationId,
    '页面没有 Update 轨道，是不是说明 Update 一直为 0',
    'unsupported-update',
  )
  const after = await service.getConversation(principal, conversationId)
  assert.equal(result.answer?.answerability, 'unsupported')
  assert.equal(result.answer?.execution_trace.nodes.length, 0)
  assert.equal(result.answer?.state_receipt.status, 'none')
  assert.deepEqual(after.dialog_state, before.dialog_state)
  assert.deepEqual(after.evidence_state, before.evidence_state)
  assert.match(result.answer?.answer_text ?? '', /不可用，不是观测值为 0/)
})

test('S3 数据适配器拒绝 null 必填字段时整轮 invalid_data 回滚', async () => {
  const provider = new FixtureProvider()
  const { service, conversationId } = await create(provider)
  await turn(service, conversationId, 'IPv4地址变化情况', 'invalid-setup')
  const before = await service.getConversation(principal, conversationId)
  provider.invalidOverview = true
  const failed = await turn(service, conversationId, '当前中断前缀', 'invalid-null')
  const after = await service.getConversation(principal, conversationId)
  assert.equal(failed.state, 'failed')
  assert.equal(failed.error?.code, 'invalid_data')
  assert.equal(failed.answer, undefined)
  assert.equal(failed.failure_receipt?.status, 'rolled_back')
  assert.deepEqual(after.binding, before.binding)
  assert.deepEqual(after.dialog_state, before.dialog_state)
  assert.deepEqual(after.evidence_state, before.evidence_state)
  assert.equal(after.binding_generation, before.binding_generation)
  capturedFailureScenarios.push({
    case_id: 'S3-F08-invalid-data-null',
    failure_injection:
      'validated read-model adapter rejects null current.interrupted_prefix_count',
    request: { question: '当前中断前缀' },
    before,
    turn: failed,
    after,
  })
})

test('S3 混合支持与越界逐目标裁决并只提交有证据目标', async () => {
  const { service, conversationId } = await create()
  const mixed = await turn(
    service,
    conversationId,
    '现在还有多少前缀不可见，是不是全国都断了',
    'mixed',
  )
  assert.equal(mixed.answer?.answerability, 'partial')
  assert.deepEqual(
    mixed.answer?.results.map((item) => item.answerability),
    ['supported', 'unsupported'],
  )
  assert.ok((mixed.answer?.execution_trace.nodes.length ?? 0) > 0)
  assert.equal(mixed.answer?.state_receipt.after.metric, 'interrupted_prefix_count')
  assert.equal(mixed.answer?.semantic_plan.grounding_plan.decisions[1]?.node_ids.length, 0)
})

test('S3 ASN 显式修正覆盖旧 ASN 且不会伪装继承', async () => {
  const { service, conversationId } = await create()
  await turn(service, conversationId, 'AS48715 的情况', 'a1')
  const corrected = await turn(service, conversationId, '改成 AS49556', 'a2')
  assert.equal(corrected.answer?.state_receipt.before.asn, 48715)
  assert.equal(corrected.answer?.state_receipt.after.asn, 49556)
  assert.ok(corrected.answer?.state_receipt.proposed.reason_codes.includes(
    'verified_asn_override',
  ))
  assert.match(corrected.answer?.answer_text ?? '', /AS49556/)
})

test('S3 路径样本后越界依赖追问保留 RRC25 观测边界', async () => {
  const { service, conversationId } = await create()
  const sample = await turn(
    service,
    conversationId,
    '看一条 AS48715 的实际路径样本',
    'p1',
  )
  assert.equal(sample.answer?.answerability, 'supported')
  assert.match(sample.answer?.answer_text ?? '', /AS_PATH/)
  assert.equal(sample.answer?.state_receipt.after.asn, 48715)
  const inference = await turn(
    service,
    conversationId,
    '这能说明它依赖谁吗',
    'p2',
  )
  assert.equal(inference.answer?.answerability, 'unsupported')
  assert.equal(inference.answer?.execution_trace.nodes.length, 0)
  assert.equal(inference.answer?.state_receipt.after.asn, 48715)
  assert.match(inference.answer?.answer_text ?? '', /不能据此判断传播方向|不能据此判断原因|不能安全映射/)
})

test('S3 prior metric 省略追问由 DialogState 解析但事实仍来自 Tool', async () => {
  const { service, conversationId } = await create()
  await turn(service, conversationId, '它什么时候最严重', 'm1')
  const followup = await turn(service, conversationId, '到最后还剩多少', 'm2')
  assert.deepEqual(
    followup.answer?.semantic_plan.user_goal_plan.goals[0]?.context_dependencies,
    ['prior_metric'],
  )
  assert.equal(
    followup.answer?.semantic_plan.user_goal_plan.goals[0]?.entities.metric,
    'interrupted_prefix_count',
  )
  assert.match(followup.answer?.answer_text ?? '', /截至 data-through/)
  assert.ok(followup.answer?.evidence.every((item) => item.source !== undefined))
})

test('S3 blocking 澄清不写可执行槽位且下一完整问题清除 pending', async () => {
  const { service, conversationId } = await create()
  const ambiguous = await turn(service, conversationId, '哪个最严重', 'c1')
  assert.equal(ambiguous.answer?.answerability, 'clarify')
  assert.equal(ambiguous.answer?.state_receipt.after.metric, null)
  assert.equal(ambiguous.answer?.state_receipt.after.pending_clarification, 'goal_clarification')
  const complete = await turn(service, conversationId, '当前中断前缀', 'c2')
  assert.equal(complete.answer?.state_receipt.after.pending_clarification, null)
  assert.equal(complete.answer?.state_receipt.after.metric, 'interrupted_prefix_count')
})

test('S3 模型失败安全回退不提交任何状态', async () => {
  const { service, conversationId } = await create(
    new FixtureProvider(),
    new FailingPlanner(),
  )
  const failed = await turn(service, conversationId, 'IPv4地址变化情况', 'model')
  assert.equal(failed.state, 'completed')
  assert.equal(failed.answer?.execution_trace.planner_outcome, 'safe_fallback')
  assert.equal(failed.answer?.execution_trace.nodes.length, 0)
  assert.equal(failed.answer?.state_receipt.status, 'none')
  assert.deepEqual(failed.answer?.state_receipt.before, failed.answer?.state_receipt.after)
  capturedFailureScenarios.push({
    case_id: 'S3-F01-model-failure',
    failure_injection: 'planner throws model unavailable',
    turn: failed,
  })
})

test('S3 Tool 失败整轮回滚且不发布半答案', async () => {
  const provider = new FixtureProvider()
  const { service, conversationId } = await create(provider)
  await turn(service, conversationId, 'IPv4地址变化情况', 'f0')
  const before = await service.getConversation(principal, conversationId)
  provider.failOverview = true
  const failed = await turn(service, conversationId, '当前中断前缀', 'f1')
  assert.equal(failed.state, 'failed')
  assert.equal(failed.error?.code, 'data_api_unavailable')
  assert.equal(failed.answer, undefined)
  assert.equal(failed.failure_receipt?.status, 'rolled_back')
  assert.deepEqual(failed.failure_receipt?.after, before.dialog_state)
  capturedFailureScenarios.push({
    case_id: 'S3-F02-tool-failure',
    failure_injection: 'TOOL-02 data_api_unavailable',
    state_before: before.dialog_state,
    turn: failed,
  })
})

test('S3 超时和取消均保持状态不变', async () => {
  const timeoutProvider = new FixtureProvider()
  timeoutProvider.blockOverview = true
  const timeoutCreated = await create(
    timeoutProvider,
    new JourneyPlanner(),
    { turnTimeoutMs: 5 },
  )
  const timedOut = await turn(
    timeoutCreated.service,
    timeoutCreated.conversationId,
    '当前中断前缀',
    'timeout',
  )
  assert.equal(timedOut.state, 'cancelled')
  assert.equal(timedOut.error?.code, 'tool_timeout')
  assert.deepEqual(
    timedOut.failure_receipt?.before,
    timedOut.failure_receipt?.after,
  )
  capturedFailureScenarios.push({
    case_id: 'S3-F03-timeout',
    failure_injection: 'TOOL-02 blocks past shared deadline',
    turn: timedOut,
  })

  const cancelProvider = new FixtureProvider()
  cancelProvider.blockOverview = true
  const cancelledCreated = await create(cancelProvider)
  const pending = cancelledCreated.service.createTurn(
    principal,
    cancelledCreated.conversationId,
    { question: '当前中断前缀', idempotency_key: 'cancel' },
  )
  await new Promise((resolve) => setImmediate(resolve))
  const snapshot = await cancelledCreated.service.getConversation(
    principal,
    cancelledCreated.conversationId,
  )
  const activeTurn = snapshot.turns.at(-1)!
  await cancelledCreated.service.cancelTurn(
    principal,
    cancelledCreated.conversationId,
    activeTurn.turn_id,
  )
  const cancelled = (await pending).turn
  assert.equal(cancelled.state, 'cancelled')
  assert.equal(cancelled.error?.code, 'cancelled')
  assert.deepEqual(
    cancelled.failure_receipt?.before,
    cancelled.failure_receipt?.after,
  )
  capturedFailureScenarios.push({
    case_id: 'S3-F04-cancel',
    failure_injection: 'host cancelTurn aborts active Tool read',
    turn: cancelled,
  })
})

test('S3 revision 漂移在 postflight 触发整轮回滚', async () => {
  const provider = new FixtureProvider()
  const { service, conversationId } = await create(provider)
  provider.driftAtResolve = provider.resolveCount + 3
  const result = await turn(service, conversationId, '当前中断前缀', 'drift')
  assert.equal(result.state, 'failed')
  assert.equal(result.error?.code, 'revision_drift')
  assert.equal(result.failure_receipt?.status, 'rolled_back')
  assert.equal(result.failure_receipt?.transaction_checks.binding_revalidated, false)
  capturedFailureScenarios.push({
    case_id: 'S3-F05-revision-drift',
    failure_injection: 'postflight resolve revision mismatch',
    turn: result,
  })
})

test('S3 完整事件引用在同轮原子 rebind 并清空上下文', async () => {
  const { service, conversationId } = await create()
  await turn(service, conversationId, 'AS48715 的情况', 's0')
  const before = await service.getConversation(principal, conversationId)
  const switchTurn = await turn(
    service,
    conversationId,
    `请切换到 ${targetReference}`,
    'switch',
  )
  assert.equal(
    switchTurn.state,
    'completed',
    JSON.stringify(switchTurn.error),
  )
  assert.equal(switchTurn.answer?.answerability, 'supported')
  assert.equal(switchTurn.answer?.execution_trace.nodes.length, 1)
  const rebound = await service.getConversation(principal, conversationId)
  assert.equal(rebound.active_binding_generation, 2)
  assert.equal(rebound.binding_generation, 2)
  assert.equal(rebound.binding.publication_id, targetPublication)
  assert.equal(rebound.dialog_state.asn, null)
  assert.equal(rebound.dialog_state.pending_clarification, null)
  assert.equal(rebound.evidence_state.publication_id, targetPublication)
  const fact = await turn(service, conversationId, '当前中断前缀', 'after-switch')
  assert.equal(fact.answer?.binding.publication_id, targetPublication)
  capturedFailureScenarios.push({
    case_id: 'S3-F06-event-switch',
    failure_injection: 'complete reference triggers atomic event rebind',
    request: { question: `请切换到 ${targetReference}` },
    before,
    switch_turn: switchTurn,
    rebound_state: rebound,
    next_generation_fact: fact,
  })
})

test('S3 generation 变化后同一 turn 幂等键不会返回旧 publication', async () => {
  const { service, conversationId } = await create()
  const oldTurn = await turn(service, conversationId, '当前中断前缀', 'same-key')
  await service.rebind(principal, conversationId, {
    event_reference: targetReference,
    publication_id: targetPublication,
    revision: 2,
    idempotency_key: 'rebind-generation',
  })
  const newTurn = await turn(service, conversationId, '当前中断前缀', 'same-key')
  assert.notEqual(newTurn.turn_id, oldTurn.turn_id)
  assert.equal(oldTurn.answer?.binding.publication_id, publication)
  assert.equal(newTurn.answer?.binding.publication_id, targetPublication)
  assert.equal(newTurn.binding_generation, 2)
})

test('S3 非法 rebind 保持 binding、EvidenceState 和 DialogState 完全不变', async () => {
  const { service, conversationId } = await create()
  await turn(service, conversationId, 'AS48715 的情况', 'r0')
  const before = await service.getConversation(principal, conversationId)
  const badRequest = {
    event_reference: targetReference,
    publication_id: 'wrong-publication',
    revision: 2,
    idempotency_key: 'bad-rebind',
  }
  let rejection: { code: string, message: string } | null = null
  try {
    await service.rebind(principal, conversationId, badRequest)
    assert.fail('非法 rebind 必须失败关闭')
  } catch (error) {
    rejection = {
      code: error && typeof error === 'object' && 'code' in error
        ? String(error.code) : 'unknown_error',
      message: error instanceof Error ? error.message : String(error),
    }
  }
  assert.equal(rejection?.code, 'binding_conflict')
  const after = await service.getConversation(principal, conversationId)
  assert.deepEqual(after.binding, before.binding)
  assert.deepEqual(after.evidence_state, before.evidence_state)
  assert.deepEqual(after.dialog_state, before.dialog_state)
  assert.equal(after.binding_generation, before.binding_generation)
  capturedFailureScenarios.push({
    case_id: 'S3-F07-invalid-rebind',
    failure_injection: 'target publication mismatch',
    request: badRequest,
    rejection,
    failure_receipt: {
      status: 'rolled_back',
      binding_unchanged: true,
      evidence_state_unchanged: true,
      dialog_state_unchanged: true,
      generation_unchanged: true,
    },
    before,
    after,
  })
})

test('S3 会话到期拒绝新轮次且旧状态不变', async () => {
  let current = Date.parse('2026-08-11T00:00:00Z')
  const now = (): Date => new Date(current)
  const { service, conversationId } = await create(
    new FixtureProvider(),
    new JourneyPlanner(),
    { ttlMs: 1000, now },
  )
  current += 1001
  await assert.rejects(
    service.createTurn(principal, conversationId, {
      question: '当前中断前缀', idempotency_key: 'expired',
    }),
    (error: unknown) => (
      error instanceof Error
      && 'code' in error
      && error.code === 'conversation_expired'
    ),
  )
})

test('S3 权限默认拒绝发生在会话创建前', async () => {
  const service = new P1RuntimeV2ConversationService({
    provider: new FixtureProvider(),
    planner: new JourneyPlanner(),
  })
  await assert.rejects(
    service.createConversation(
      { userId: 'denied', authorizationScope: 'profile:read' },
      {
        event_reference: reference,
        publication_id: publication,
        revision: 1,
        idempotency_key: 'denied',
      },
    ),
    (error: unknown) => (
      error instanceof Error
      && 'code' in error
      && error.code === 'permission_denied'
    ),
  )
})
