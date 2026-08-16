import assert from 'node:assert/strict'
import { createServer } from 'node:http'
import type { AddressInfo } from 'node:net'
import test from 'node:test'

import type { CountryOutageAgentOrchestrator } from '../src/application/index.js'
import type {
  CreateP1ConversationRequest,
  P1ConversationBinding,
  P1ChatApplication,
} from '../src/chat/index.js'
import {
  P1RuntimeV2ConversationService,
  P1RuntimeV2SemanticTurnService,
  P1RuntimeV2SingleTurnService,
  type P1FactBundle,
  type P1UserGoalPlanner,
  type P1RuntimeV2ReadProvider,
} from '../src/chat/index.js'
import type { CountryOutagePrincipal } from '../src/server/index.js'
import { createCountryOutageAgentHttpHandler } from '../src/server/index.js'

test('P1 HTTP 会话入口校验身份并传递幂等键', async () => {
  let received: unknown
  const chat = {
    async createConversation(
      _principal: CountryOutagePrincipal,
      request: CreateP1ConversationRequest,
    ) {
      received = request
      return {
        deduplicated: false,
        conversation: {
          schema_version: 'country_outage_p1_chat_v1',
          conversation_id: 'conv_http',
          binding: {}, state: {}, turns: [], expires_at: '', reminder_at: '', created_at: '',
        },
      }
    },
  } as unknown as P1ChatApplication
  const server = createServer(createCountryOutageAgentHttpHandler({
    application: {} as CountryOutageAgentOrchestrator,
    chat,
    authenticate: () => ({ userId: 'http-user', authorizationScope: 'event-read' }),
  }))
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve))
  const { port } = server.address() as AddressInfo
  try {
    const response = await fetch(`http://127.0.0.1:${port}/country-outage/chat/conversations`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Idempotency-Key': 'conversation-http-01' },
      body: JSON.stringify({
        event_reference: 'country_outage/2026-02-27 09:12:32/IR/1/r',
        publication_id: 'publication-http',
        revision: 1,
        idempotency_key: 'conversation-http-01',
      }),
    })
    assert.equal(response.status, 201)
    assert.deepEqual(received, {
      event_reference: 'country_outage/2026-02-27 09:12:32/IR/1/r',
      publication_id: 'publication-http',
      revision: 1,
      idempotency_key: 'conversation-http-01',
    })
  } finally {
    await new Promise<void>((resolve) => server.close(() => resolve()))
  }
})

const runtimeReference = 'country_outage/2026-02-27 09:12:32/IR/1/r'
const runtimePublication = 'country_outage_publication_v1_http'

function runtimeBinding(): P1ConversationBinding {
  return {
    event_type: 'country_outage',
    incident_id: 'incident_http_ir',
    legacy_reference: runtimeReference,
    publication_id: runtimePublication,
    revision: 1,
    collector_id: 'rrc25',
    cohort_id: 'cohort_http_ir',
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

function runtimeBundle(): P1FactBundle {
  const identity = runtimeBinding()
  return {
    binding: identity,
    resolution: identity,
    overview: {
      ...identity,
      event: {
        detected_at_utc: identity.detected_at_utc,
        event_end_at_utc: null,
        event_duration_seconds: null,
      },
      cohort: {
        fixed_asn_count: 572,
        fixed_prefix_count: 9257,
        independent_direction_relation_count: 368675,
      },
      current: {
        affected_asn_count: 121,
        interrupted_prefix_count: 1024,
        completely_interrupted_prefix_count: 318,
        invisible_direction_count: 14867,
      },
      peaks: {
        interrupted_prefix_count: {
          value: 3855,
          state_point_utc: '2026-02-27T23:15:00Z',
        },
      },
      affected_as_count: 525,
      semantic_boundary:
        'rrc25_control_plane_observation_not_user_impact_or_cause',
    },
    series: {
      point_count: 1,
      timestamps: ['2026-03-11T00:00:00Z'],
      tracks: {
        interrupted_prefix_count: [1024],
      },
    },
    asns: { items: [] },
    paths: { items: [] },
    audit: {
      dataset_id: 'dataset-http',
      implementation_id: 'git:http',
      event_content_sha256: 'b'.repeat(64),
    },
    derived: {
      ipv4: {
        maximum: 10156800,
        minimum: 9577728,
        drop: 579072,
        drop_percent: 5.701323,
        recovery: 492032,
        recovery_percent: 84.969054,
      },
      ipv6: {
        maximum: 267292,
        minimum: 267288,
        drop: 4,
        drop_percent: 0.001496,
      },
    },
  }
}

class RuntimeHttpProvider implements P1RuntimeV2ReadProvider {
  resolveCalls = 0
  overviewCalls = 0

  async resolve(): Promise<P1ConversationBinding> {
    this.resolveCalls += 1
    return runtimeBinding()
  }

  async readOverview() {
    this.overviewCalls += 1
    const binding = runtimeBinding()
    return {
      ...binding,
      event: {
        detected_at_utc: binding.detected_at_utc,
        event_end_at_utc: null,
      },
      cohort: { fixed_prefix_count: 9257 },
      current: { interrupted_prefix_count: 1024 },
      peaks: {
        interrupted_prefix_count: {
          value: 3855,
          state_point_utc: '2026-02-27T23:15:00Z',
        },
      },
      affected_as_count: 525,
    }
  }

  async load() {
    return runtimeBundle()
  }

  async findAsn(_bundle: P1FactBundle, _asn: number) {
    return null
  }
}

test('P1 Runtime v2 HTTP 以 IR scoped 权限完成同候选单轮事实旅程', async () => {
  const provider = new RuntimeHttpProvider()
  const server = createServer(createCountryOutageAgentHttpHandler({
    application: {} as CountryOutageAgentOrchestrator,
    runtimeV2SingleTurn: new P1RuntimeV2SingleTurnService(provider),
    authenticate: () => ({
      userId: 'http-runtime-user',
      authorizationScope: 'profile:read,country_outage_event_read:IR',
    }),
  }))
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve))
  const { port } = server.address() as AddressInfo
  try {
    const response = await fetch(
      `http://127.0.0.1:${port}/country-outage/runtime-v2/single-turn`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          event_reference: runtimeReference,
          publication_id: runtimePublication,
          revision: 1,
          controlled_goal: 'event_summary',
        }),
      },
    )
    assert.equal(response.status, 200)
    const answer = await response.json() as any
    assert.equal(answer.schema_version, 'country_outage_p1_single_turn_v2')
    assert.equal(answer.answerability, 'partial')
    assert.equal(answer.binding.publication_id, runtimePublication)
    assert.equal(answer.execution_trace.authorization.basis, 'event_read_country')
    assert.equal(
      answer.execution_trace.authorization.original_scope,
      'profile:read,country_outage_event_read:IR',
    )
    assert.equal(answer.execution_trace.state_commit, 'none')
    assert.equal(answer.evidence.length, 12)
    assert.equal(provider.resolveCalls, 1)
    assert.equal(provider.overviewCalls, 1)
  } finally {
    await new Promise<void>((resolve) => server.close(() => resolve()))
  }
})

test('P1 Runtime v2 HTTP 对错误国家权限和额外字段失败关闭', async () => {
  const provider = new RuntimeHttpProvider()
  const server = createServer(createCountryOutageAgentHttpHandler({
    application: {} as CountryOutageAgentOrchestrator,
    runtimeV2SingleTurn: new P1RuntimeV2SingleTurnService(provider),
    authenticate: () => ({
      userId: 'http-runtime-cn-user',
      authorizationScope: 'country_outage_event_read:CN',
    }),
  }))
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve))
  const { port } = server.address() as AddressInfo
  const url = `http://127.0.0.1:${port}/country-outage/runtime-v2/single-turn`
  const body = {
    event_reference: runtimeReference,
    publication_id: runtimePublication,
    revision: 1,
    controlled_goal: 'event_summary',
  }
  try {
    const denied = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    assert.equal(denied.status, 403)
    assert.equal((await denied.json() as any).error.code, 'permission_denied')
    assert.equal(provider.resolveCalls, 1)
    assert.equal(provider.overviewCalls, 0)

    const extra = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ...body, external_urls: ['https://example.test'] }),
    })
    assert.equal(extra.status, 400)
    assert.equal((await extra.json() as any).error.code, 'invalid_request_fields')
    assert.equal(provider.resolveCalls, 1)
  } finally {
    await new Promise<void>((resolve) => server.close(() => resolve()))
  }
})

test('P1 Runtime v2 Semantic HTTP 保留混合目标并只执行合法事实节点', async () => {
  const provider = new RuntimeHttpProvider()
  const question = '现在还有多少前缀不可见，是不是全国都断了？'
  const planner: P1UserGoalPlanner = {
    identity: 'http-semantic-fixture',
    async plan(receivedQuestion) {
      return {
        plan_revision: 'user-goal-plan-v2',
        original_question: receivedQuestion,
        goals: [
          {
            goal_id: 'goal-1',
            requested_goal: '现在还有多少前缀不可见',
            normalized_kind: 'current_prefix_state',
            entities: {},
            references: [],
            ambiguity: 'none',
            context_dependencies: [],
          },
          {
            goal_id: 'goal-2',
            requested_goal: '是不是全国都断了',
            normalized_kind: 'real_user_or_national_impact',
            entities: {},
            references: [],
            ambiguity: 'none',
            context_dependencies: [],
          },
        ],
        state_proposal: {
          inherit: [], set: {}, clear: [], reason_codes: [],
        },
        planner_identity: 'http-semantic-fixture',
        confidence: 1,
      }
    },
  }
  const deterministic = new P1RuntimeV2SingleTurnService(provider)
  const server = createServer(createCountryOutageAgentHttpHandler({
    application: {} as CountryOutageAgentOrchestrator,
    runtimeV2SemanticTurn: new P1RuntimeV2SemanticTurnService(
      provider,
      deterministic,
      planner,
    ),
    authenticate: () => ({
      userId: 'http-semantic-user',
      authorizationScope: 'country_outage_event_read:IR',
    }),
  }))
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve))
  const { port } = server.address() as AddressInfo
  try {
    const response = await fetch(
      `http://127.0.0.1:${port}/country-outage/runtime-v2/semantic-turn`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          event_reference: runtimeReference,
          publication_id: runtimePublication,
          revision: 1,
          question,
        }),
      },
    )
    assert.equal(response.status, 200)
    const answer = await response.json() as any
    assert.equal(answer.schema_version, 'country_outage_p1_semantic_turn_v2')
    assert.equal(answer.answerability, 'partial')
    assert.equal(answer.results[0].answerability, 'supported')
    assert.equal(answer.results[1].answerability, 'unsupported')
    assert.equal(answer.results[1].evidence_refs.length, 0)
    assert.equal(answer.execution_trace.model_generated_fact_count, 0)
    assert.equal(answer.execution_trace.state_commit, 'none')
    assert.equal(answer.validation.grounding_legality, 'passed')
    assert.equal(provider.resolveCalls, 1)
    assert.equal(provider.overviewCalls, 1)
  } finally {
    await new Promise<void>((resolve) => server.close(() => resolve()))
  }
})

test('P1 Runtime v2 Semantic HTTP 对未配置模型和额外字段失败关闭', async () => {
  const server = createServer(createCountryOutageAgentHttpHandler({
    application: {} as CountryOutageAgentOrchestrator,
    authenticate: () => ({
      userId: 'http-semantic-user',
      authorizationScope: 'country_outage_event_read:IR',
    }),
  }))
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve))
  const { port } = server.address() as AddressInfo
  try {
    const response = await fetch(
      `http://127.0.0.1:${port}/country-outage/runtime-v2/semantic-turn`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          event_reference: runtimeReference,
          publication_id: runtimePublication,
          revision: 1,
          question: '发生了什么',
          tool: 'root_cause_analysis',
        }),
      },
    )
    // 配置门在请求体读取之前生效，不能把未配置入口伪装为输入错误。
    assert.equal(response.status, 503)
    assert.equal(
      (await response.json() as any).error.code,
      'p1_runtime_v2_semantic_not_configured',
    )
  } finally {
    await new Promise<void>((resolve) => server.close(() => resolve()))
  }
})

test('P1 Runtime v2 S3 HTTP 创建、追问、恢复会话并暴露状态提交回执', async () => {
  const provider = new RuntimeHttpProvider()
  const planner: P1UserGoalPlanner = {
    identity: 'http-s3-fixture',
    async plan(question, context) {
      return {
        plan_revision: 'user-goal-plan-v2',
        original_question: question,
        goals: [{
          goal_id: 'goal-1',
          requested_goal: question,
          normalized_kind: context.dialog_state?.metric
            ? 'metric_followup' : 'prefix_peak',
          entities: {},
          references: [],
          ambiguity: 'none',
          context_dependencies: context.dialog_state?.metric
            ? ['prior_metric'] : [],
        }],
        state_proposal: {
          inherit: [], set: {}, clear: [], reason_codes: [],
        },
        planner_identity: 'http-s3-fixture',
        confidence: 1,
      }
    },
  }
  const conversation = new P1RuntimeV2ConversationService({
    provider,
    planner,
  })
  const server = createServer(createCountryOutageAgentHttpHandler({
    application: {} as CountryOutageAgentOrchestrator,
    runtimeV2Conversation: conversation,
    authenticate: () => ({
      userId: 'http-s3-user',
      authorizationScope: 'country_outage_event_read:IR',
    }),
  }))
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve))
  const { port } = server.address() as AddressInfo
  const base = `http://127.0.0.1:${port}/country-outage/runtime-v2`
  try {
    const createdResponse = await fetch(`${base}/conversations`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Idempotency-Key': 's3-create-http',
      },
      body: JSON.stringify({
        event_reference: runtimeReference,
        publication_id: runtimePublication,
        revision: 1,
        idempotency_key: 's3-create-http',
      }),
    })
    const createdResponseText = await createdResponse.text()
    assert.equal(createdResponse.status, 201, createdResponseText)
    const created = JSON.parse(createdResponseText) as any
    const id = created.conversation.conversation_id as string

    const firstResponse = await fetch(`${base}/conversations/${id}/turns`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Idempotency-Key': 's3-turn-http-1',
      },
      body: JSON.stringify({
        question: '什么时候最严重',
        idempotency_key: 's3-turn-http-1',
      }),
    })
    assert.equal(firstResponse.status, 201)
    const first = await firstResponse.json() as any
    assert.equal(first.turn.state, 'completed')
    assert.equal(first.turn.answer.execution_trace.state_commit, 'committed')
    assert.equal(first.turn.answer.state_receipt.after.metric,
      'interrupted_prefix_count')

    const followResponse = await fetch(`${base}/conversations/${id}/turns`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Idempotency-Key': 's3-turn-http-2',
      },
      body: JSON.stringify({
        question: '到最后还剩多少',
        idempotency_key: 's3-turn-http-2',
      }),
    })
    assert.equal(followResponse.status, 201)
    const follow = await followResponse.json() as any
    assert.match(follow.turn.answer.answer_text, /1,024|1024/)
    assert.ok(follow.turn.answer.state_receipt.proposed.inherit.includes('metric'))

    const restoredResponse = await fetch(`${base}/conversations/${id}`)
    assert.equal(restoredResponse.status, 200)
    const restored = await restoredResponse.json() as any
    assert.equal(restored.turns.length, 2)
    assert.equal(restored.dialog_state.metric, 'interrupted_prefix_count')
    assert.equal(restored.evidence_state.immutable, true)
    assert.equal(restored.binding.publication_id, runtimePublication)
  } finally {
    await new Promise<void>((resolve) => server.close(() => resolve()))
  }
})
