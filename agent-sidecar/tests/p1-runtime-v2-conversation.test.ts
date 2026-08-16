import assert from 'node:assert/strict'
import { writeFileSync } from 'node:fs'
import test from 'node:test'

import {
  P1ReadModelError,
  P1RuntimeV2ConversationService,
  type P1ConversationBinding,
  type P1FactBundle,
  type P1GeneralReadModelProvider,
  type P1UserGoalPlan,
  type P1UserGoalPlanner,
  type P1UserGoalPlannerContext,
} from '../src/chat/index.js'

const reference = 'country_outage/2026-02-27 09:12:32/IR/1/r'
const publicationId = 'publication-ir-r1'
const principal = {
  userId: 'p1-runtime-v2-conversation-user',
  authorizationScope: 'country_outage_event_read:IR',
}

function binding(overrides: Partial<P1ConversationBinding> = {}):
P1ConversationBinding {
  return {
    event_type: 'country_outage',
    incident_id: 'incident-ir-r1',
    legacy_reference: reference,
    publication_id: publicationId,
    revision: 1,
    collector_id: 'rrc25',
    cohort_id: 'cohort-ir-r1',
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
    ...overrides,
  }
}

function factBundle(value = binding()): P1FactBundle {
  const metadata = { ...value, quality_state: 'complete', missing_slot_count: 0 }
  return {
    binding: value,
    resolution: metadata,
    overview: {
      ...metadata,
      event: {
        detected_at_utc: value.detected_at_utc,
        event_end_at_utc: null,
        event_duration_seconds: null,
      },
      cohort: {
        cohort_id: value.cohort_id,
        fixed_asn_count: 572,
        fixed_prefix_count: 9257,
        independent_direction_relation_count: 368675,
      },
      current: {
        affected_asn_count: 121,
        interrupted_prefix_count: 1024,
        completely_interrupted_prefix_count: 318,
        invisible_direction_count: 14867,
        fixed_visible_ipv4_address_count: 10069760,
        new_cumulative_ipv4_prefix_count: 700,
        new_cumulative_ipv6_prefix_count: 1,
        new_visible_ipv4_prefix_count: 111,
        new_visible_ipv6_prefix_count: 1,
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
    },
    series: {
      point_count: 6,
      timestamps: [
        '2026-02-27T00:10:00Z',
        '2026-02-27T23:15:00Z',
        '2026-02-28T13:50:00Z',
        '2026-02-28T14:35:00Z',
        '2026-03-02T11:30:00Z',
        '2026-03-11T00:00:00Z',
      ],
      tracks: {
        interrupted_prefix_count: [0, 3855, 3200, 2800, 1800, 1024],
        completely_interrupted_prefix_count: [0, 1200, 1400, 1553, 900, 318],
        invisible_direction_count: [0, 60000, 62000, 65783, 40000, 14867],
        affected_asn_count: [0, 200, 300, 320, 350, 121],
        fixed_visible_ipv4_address_count: [
          10156800, 9577728, 9700000, 9800000, 9950000, 10069760,
        ],
        fixed_visible_ipv6_slash48_count: [
          267292, 267288, 267288, 267288, 267288, 267288,
        ],
        new_cumulative_ipv4_prefix_count: [0, 300, 500, 600, 650, 700],
        new_cumulative_ipv6_prefix_count: [0, 1, 1, 1, 1, 1],
        new_visible_ipv4_prefix_count: [0, 300, 250, 200, 150, 111],
        new_visible_ipv6_prefix_count: [0, 1, 1, 1, 1, 1],
      },
      track_definitions: {
        interrupted_prefix_count: {
          definition: '部分中断与完全中断的固定唯一前缀合计。',
        },
        completely_interrupted_prefix_count: {
          definition: '在全部 RRC25 观察方向均不可见的固定唯一前缀。',
        },
        invisible_direction_count: {
          definition: '固定前缀与独立 RRC25 观察方向的不可见关系数。',
        },
      },
    },
    asns: {
      items: [
        {
          asn: 48715,
          as_name: 'SEFROYEKPARDAZENG-AS',
          fixed_prefix_count: 73,
          peak_complete_prefix_count: 73,
          peak_invisible_direction_count: 3138,
          path_downstream_asn_count: 1,
        },
        {
          asn: 49556,
          as_name: 'webdade',
          fixed_prefix_count: 50,
          peak_complete_prefix_count: 50,
          peak_invisible_direction_count: 2103,
          path_downstream_asn_count: 0,
        },
      ],
    },
    paths: {
      items: [{
        affected_asn: 49666,
        downstream_asn: 58224,
        downstream_as_name: 'TCI',
        path_samples: [{
          prefix: '109.74.224.0/20',
          as_path_canonical: '33874 6758 1273 3257 49666 48159 58224',
        }],
      }],
    },
    audit: {
      dataset_id: 'dataset-ir-r1',
      implementation_id: 'git:test',
      event_content_sha256: 'a'.repeat(64),
      causal_boundary:
        'rrc25_path_association_is_not_dependency_propagation_user_impact_or_cause',
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

class FakeProvider implements P1GeneralReadModelProvider {
  readonly bundles = new Map<string, P1FactBundle>()
  resolveCalls = 0
  driftOnResolveCall: number | null = null
  capabilityDriftOnResolveCall: number | null = null
  failLoadReference: string | null = null
  failAsn: number | null = null
  lastLoadedBundle: P1FactBundle | null = null

  constructor() {
    this.bundles.set(reference, factBundle())
  }

  async load(value: string) {
    if (value === this.failLoadReference) throw new Error('read model unavailable')
    const result = this.bundles.get(value)
    if (!result) throw new Error('unknown event reference')
    this.lastLoadedBundle = structuredClone(result)
    return this.lastLoadedBundle
  }

  async resolve(value: string): Promise<P1ConversationBinding> {
    this.resolveCalls += 1
    const result = this.bundles.get(value)
    if (!result) throw new Error('unknown event reference')
    if (this.resolveCalls === this.driftOnResolveCall) {
      return {
        ...structuredClone(result.binding),
        publication_id: `${result.binding.publication_id}-drift`,
        revision: result.binding.revision + 1,
      }
    }
    if (this.resolveCalls === this.capabilityDriftOnResolveCall) {
      return {
        ...structuredClone(result.binding),
        capabilities: {
          ...structuredClone(result.binding.capabilities),
          event_series: 'unavailable' as const,
        },
      }
    }
    return structuredClone(result.binding)
  }

  async findAsn(value: P1FactBundle, asn: number) {
    if (asn === this.failAsn) {
      throw new P1ReadModelError('asn_tool_unavailable', 'AS 读取暂不可用', true)
    }
    return value.asns.items.find((item: { asn: number }) => item.asn === asn)
      ?? null
  }
}

interface GoalInput {
  kind: string
  requested?: string
  entities?: Record<string, string | number | boolean | null>
  ambiguity?: 'none' | 'non_blocking' | 'blocking'
  dependencies?: string[]
  references?: string[]
}

function userGoalPlan(question: string, goals: GoalInput[]): P1UserGoalPlan {
  return {
    plan_revision: 'user-goal-plan-v2',
    original_question: question,
    goals: goals.map((goal, index) => ({
      goal_id: `goal-${index + 1}`,
      requested_goal: goal.requested ?? question,
      normalized_kind: goal.kind,
      entities: goal.entities ?? {},
      references: goal.references ?? [],
      ambiguity: goal.ambiguity ?? 'none',
      context_dependencies: goal.dependencies ?? [],
    })),
    state_proposal: { inherit: [], set: {}, clear: [], reason_codes: [] },
    planner_identity: 'fake-reviewed-multiturn-planner',
    confidence: 0.99,
  }
}

class FakePlanner implements P1UserGoalPlanner {
  readonly identity = 'fake-reviewed-multiturn-planner'
  readonly contexts: P1UserGoalPlannerContext[] = []
  readonly answers = new Map<string, GoalInput[]>()
  failQuestion: string | null = null

  add(question: string, ...goals: GoalInput[]): this {
    this.answers.set(question, goals)
    return this
  }

  async plan(question: string, context: P1UserGoalPlannerContext) {
    this.contexts.push(structuredClone(context))
    if (question === this.failQuestion) throw new Error('model unavailable')
    const goals = this.answers.get(question)
    if (!goals) throw new Error(`missing fake semantic plan: ${question}`)
    return userGoalPlan(question, goals)
  }
}

async function service(planner = new FakePlanner(), provider = new FakeProvider()) {
  const runtime = new P1RuntimeV2ConversationService({
    provider,
    planner,
    now: () => new Date('2026-08-09T14:00:00Z'),
  })
  const created = await runtime.createConversation(principal, {
    event_reference: reference,
    publication_id: publicationId,
    revision: 1,
    idempotency_key: 'create-1',
  })
  return {
    runtime,
    planner,
    provider,
    id: created.conversation.conversation_id,
  }
}

async function turn(
  runtime: P1RuntimeV2ConversationService,
  id: string,
  question: string,
  index: number,
) {
  const result = await runtime.createTurn(principal, id, {
    question,
    idempotency_key: `turn-${index}`,
  })
  assert.equal(result.turn.state, 'completed', result.turn.error?.message)
  return result.turn.answer!
}

function writeControlledFailureReceipt(
  caseId: 'P013-X-04' | 'P013-X-05',
  value: Record<string, unknown>,
): void {
  const target = process.env.P1_S4_CONTROLLED_RECEIPT_PATH
  const requestedCase = process.env.P1_S4_CONTROLLED_RECEIPT_CASE
  if (!target || requestedCase !== caseId) return
  writeFileSync(target, `${JSON.stringify({
    schema_version: 'country_outage_p1_controlled_failure_receipt_v1',
    candidate_id: process.env.P1_S4_CANDIDATE_ID ?? null,
    case_id: caseId,
    runtime_identity: {
      implementation: 'p1-runtime-v2-conversation',
      runtime_source: 'agent-sidecar/src/chat/runtime-v2-conversation.ts',
      test_source: 'agent-sidecar/tests/p1-runtime-v2-conversation.test.ts',
    },
    ...value,
  }, null, 2)}\n`, 'utf8')
}

test('S4 已登记 P0 直接能力均可从开放目标落到封闭计划和确定性事实', async () => {
  const planner = new FakePlanner()
  const cases: Array<{
    question: string
    goal: GoalInput
    answerability: string
    text: string
    executes: boolean
  }> = [
    {
      question: '异常是什么时候开始的？',
      goal: { kind: 'detection_time' },
      answerability: 'partial',
      text: '真实异常起点',
      executes: true,
    },
    {
      question: '从这页看已经恢复了吗？',
      goal: { kind: 'recovery_status' },
      answerability: 'partial',
      text: '不能判定事件结束',
      executes: true,
    },
    {
      question: '这次观测覆盖多大范围？',
      goal: { kind: 'current_scope' },
      answerability: 'supported',
      text: '受影响 AS 峰值为 350',
      executes: true,
    },
    {
      question: '页面列出的前五个受影响 AS 是哪些？',
      goal: { kind: 'top_affected_asns' },
      answerability: 'supported',
      text: 'AS48715',
      executes: true,
    },
    {
      question: '峰值之后还有多少前缀持续异常？',
      goal: { kind: 'remaining_vs_peak' },
      answerability: 'partial',
      text: '1,024',
      executes: true,
    },
    {
      question: '固定前缀可见 IPv4 地址规模最大下降了多少？',
      goal: {
        kind: 'address_family_change',
        entities: { address_family: 'ipv4' },
      },
      answerability: 'supported',
      text: '579,072',
      executes: true,
    },
    {
      question: '“中断前缀”“完全中断前缀”和“不可见方向”分别是什么意思？',
      goal: { kind: 'metric_semantics' },
      answerability: 'supported',
      text: '不可见方向',
      executes: true,
    },
    {
      question: '窗口内新出现了多少 IPv4 和 IPv6 前缀？',
      goal: { kind: 'new_prefix_resources' },
      answerability: 'supported',
      text: 'IPv4 前缀 700',
      executes: true,
    },
    {
      question: '这份页面数据完整吗，还缺什么？',
      goal: { kind: 'data_completeness' },
      answerability: 'partial',
      text: '未暴露 Update/Withdraw',
      executes: true,
    },
    {
      question: '仅凭这页 RRC25 数据能证明什么、不能证明什么？',
      goal: { kind: 'rrc25_proof_boundary' },
      answerability: 'supported',
      text: '不能证明真实用户可达性',
      executes: true,
    },
    {
      question: '按时间线列出这次事件的已知事实。',
      goal: { kind: 'fact_timeline' },
      answerability: 'supported',
      text: '这是事实时间线，不是因果链',
      executes: true,
    },
    {
      question: '这页绑定的 publication、revision 和数据截止时间是什么？',
      goal: { kind: 'event_identity' },
      answerability: 'supported',
      text: 'publication-ir-r1',
      executes: true,
    },
    {
      question: '从技术上看，这次是不是通过撤销 BGP 路由切断互联网的？',
      goal: { kind: 'cause_or_responsibility' },
      answerability: 'unsupported',
      text: '当前只有 RRC25 BGP 控制面观测',
      executes: false,
    },
  ]
  for (const item of cases) planner.add(item.question, item.goal)
  for (const item of cases) {
    const value = await service(planner)
    const answer = await turn(
      value.runtime,
      value.id,
      item.question,
      1,
    )
    assert.equal(answer.answerability, item.answerability, item.question)
    assert.match(answer.answer_text, new RegExp(item.text), item.question)
    assert.equal(
      answer.execution_trace.nodes.length > 0,
      item.executes,
      item.question,
    )
    assert.equal(answer.execution_trace.model_generated_fact_count, 0)
    assert.equal(answer.validation.grounding_legality, 'passed')
    if (item.goal.kind === 'fact_timeline') {
      assert.match(answer.answer_text, /完全中断前缀.*1,553|1553/)
      assert.match(answer.answer_text, /受影响 AS.*350/)
      const ordered = answer.evidence.filter((evidence) =>
        evidence.evidence_ref.startsWith(
          'derived.fact_timeline.ordered_fact_nodes.',
        )
      )
      assert.deepEqual(
        ordered.map((evidence) => evidence.observed_at_utc),
        [
          '2026-02-27T00:10:00Z',
          '2026-02-27T01:12:32Z',
          '2026-02-27T23:15:00Z',
          '2026-02-28T14:35:00Z',
          '2026-03-02T11:30:00Z',
          '2026-03-11T00:00:00Z',
        ],
      )
      assert.deepEqual(
        ordered.map((evidence) => evidence.unit),
        ['UTC', 'UTC', 'prefix', 'prefix', 'asn', 'prefix'],
      )
      assert.ok(answer.evidence.some((evidence) =>
        evidence.evidence_ref === 'derived.fact_timeline.terminal_unknown'
        && evidence.value === 'event_end_unknown'
      ))
      const op03 = answer.execution_trace.nodes.find((node) =>
        node.execution_unit === 'OP-03'
      )
      assert.equal(op03?.evidence_refs.length, 7)
      assert.match(answer.unknowns.join('\n'), /event_end_unknown/)
    }
    if (item.goal.kind === 'current_scope') {
      assert.match(answer.answer_text, /94.*2026-02-28T13:50:00Z/)
      assert.ok(answer.evidence.some((evidence) =>
        evidence.evidence_ref === 'peaks.affected_asn_count.value'
        && evidence.value === 350
      ))
      assert.ok(answer.evidence.some((evidence) =>
        evidence.evidence_ref
          === 'peaks.route_interrupted_asn_count.state_point_utc'
        && evidence.value === '2026-02-28T13:50:00Z'
      ))
      assert.ok(answer.semantic_plan.grounding_plan.nodes.some((node) =>
        node.capability_ids.includes('CAP-005')
      ))
    }
    if (item.goal.kind === 'remaining_vs_peak') {
      assert.match(answer.answer_text, /不能证明期间一直持续异常/)
      assert.match(answer.limitations.join('\n'), /不能证明中间连续性/)
    }
  }
})

test('S4 当前用户/全国问题局部回答控制面事实并逐目标拒绝越界推断', async () => {
  const question = '伊朗人现在还有互联网吗，是不是全国都断了？'
  const planner = new FakePlanner().add(
    question,
    { kind: 'current_prefix_state', requested: '当前可观测状态' },
    {
      kind: 'real_user_or_national_impact',
      requested: '真实用户和全国连接状态',
    },
  )
  const instance = await service(planner)
  const answer = await turn(instance.runtime, instance.id, question, 1)
  assert.equal(answer.answerability, 'partial')
  assert.equal(answer.results[0]?.answerability, 'supported')
  assert.match(answer.results[0]?.text ?? '', /1,024/)
  assert.equal(answer.results[1]?.answerability, 'unsupported')
  assert.equal(answer.results[1]?.evidence_refs.length, 0)
  assert.deepEqual(
    answer.execution_trace.nodes.map((node) => node.execution_unit),
    ['TOOL-01', 'TOOL-02'],
  )
})

test('S4 责任、用户和经济影响复合目标全部按已登记政策零执行', async () => {
  const question = '谁应该负责，造成了多少用户和经济损失？'
  const planner = new FakePlanner().add(
    question,
    { kind: 'cause_or_responsibility' },
    { kind: 'real_user_or_national_impact' },
    { kind: 'economic_impact' },
  )
  const instance = await service(planner)
  const answer = await turn(instance.runtime, instance.id, question, 1)
  assert.equal(answer.answerability, 'unsupported')
  assert.deepEqual(
    answer.results.map((item) => item.answerability),
    ['unsupported', 'unsupported', 'unsupported'],
  )
  assert.equal(answer.execution_trace.nodes.length, 0)
  assert.equal(answer.evidence.length, 0)
  assert.equal(answer.state_receipt.status, 'none')
})

test('P0-M01 峰值追问继承已验证指标，随后读取当前值而非事件结束值', async () => {
  const planner = new FakePlanner()
    .add('发生了什么', { kind: 'event_summary' })
    .add('它什么时候最严重', { kind: 'prefix_peak' })
    .add('到最后还剩多少', {
      kind: 'metric_followup',
      dependencies: ['prior_metric'],
    })
  const { runtime, id } = await service(planner)
  await turn(runtime, id, '发生了什么', 1)
  const peak = await turn(runtime, id, '它什么时候最严重', 2)
  const current = await turn(runtime, id, '到最后还剩多少', 3)
  assert.match(peak.answer_text, /3,855|3855/)
  assert.match(peak.answer_text, /07:15/)
  assert.match(current.answer_text, /1,024|1024/)
  assert.equal(current.state_receipt.after.metric, 'interrupted_prefix_count')
  assert.ok(current.state_receipt.proposed.inherit.includes('metric'))
  assert.equal(planner.contexts[2]?.dialog_state?.metric, 'interrupted_prefix_count')
})

test('P0-M02 显式 ASN 修正覆盖旧 ASN 且事实不串扰', async () => {
  const planner = new FakePlanner()
    .add('AS48715 呢', { kind: 'asn_detail', entities: { asn: 48715 } })
    .add('不对，看 AS49556', { kind: 'entity_correction', entities: { asn: 49556 } })
  const { runtime, id } = await service(planner)
  const first = await turn(runtime, id, 'AS48715 呢', 1)
  const corrected = await turn(runtime, id, '不对，看 AS49556', 2)
  assert.match(first.answer_text, /AS48715/)
  assert.match(corrected.answer_text, /AS49556/)
  assert.match(corrected.answer_text, /50/)
  assert.match(corrected.answer_text, /2,103|2103/)
  assert.doesNotMatch(corrected.answer_text, /AS48715|3,138|3138/)
  assert.equal(corrected.state_receipt.after.asn, 49556)
  assert.ok(corrected.state_receipt.proposed.reason_codes.includes(
    'explicit_asn_correction',
  ))
})

test('P0-M03 地址族比较后切到路径主题会清除地址族上下文', async () => {
  const planner = new FakePlanner()
    .add('IPv4 和 IPv6 哪个变化大', {
      kind: 'address_family_compare',
      entities: { address_family: 'both' },
    })
    .add('给个实际路径', { kind: 'path_sample' })
  const { runtime, id } = await service(planner)
  const compare = await turn(runtime, id, 'IPv4 和 IPv6 哪个变化大', 1)
  const path = await turn(runtime, id, '给个实际路径', 2)
  assert.match(compare.answer_text, /579,072|579072/)
  assert.match(compare.answer_text, /IPv6.*4|4.*IPv6/s)
  assert.match(path.answer_text, /109\.74\.224\.0\/20/)
  assert.equal(path.state_receipt.after.topic, 'path')
  assert.equal(path.state_receipt.after.address_family, null)
  assert.ok(path.state_receipt.proposed.reason_codes.includes(
    'topic_switch_isolated',
  ))
})

test('P0-M04 模糊事件切换只形成待澄清，不复用旧峰值', async () => {
  const planner = new FakePlanner()
    .add('峰值呢', { kind: 'prefix_peak' })
    .add('换成伊朗另一次事件', { kind: 'event_switch', ambiguity: 'blocking' })
    .add('最近那次', { kind: 'event_switch', ambiguity: 'blocking' })
    .add('继续说峰值', { kind: 'prefix_peak' })
  const { runtime, id } = await service(planner)
  await turn(runtime, id, '峰值呢', 1)
  const switchTurn = await turn(runtime, id, '换成伊朗另一次事件', 2)
  const recent = await turn(runtime, id, '最近那次', 3)
  assert.equal(switchTurn.answerability, 'clarify')
  assert.equal(switchTurn.execution_trace.nodes.length, 0)
  assert.equal(switchTurn.evidence.length, 0)
  assert.equal(switchTurn.state_receipt.after.pending_clarification, 'event_reference')
  assert.equal(switchTurn.state_receipt.after.metric, null)
  assert.ok(switchTurn.state_receipt.proposed.clear.includes('event_binding'))
  assert.ok(!switchTurn.state_receipt.proposed.inherit.includes('event_binding'))
  assert.equal(recent.answerability, 'clarify')
  assert.equal(recent.evidence.length, 0)
  assert.doesNotMatch(recent.answer_text, /3,855|3855/)
  const suspended = await turn(runtime, id, '继续说峰值', 4)
  assert.equal(suspended.answerability, 'clarify')
  assert.equal(suspended.execution_trace.nodes.length, 0)
  assert.equal(suspended.evidence.length, 0)
  assert.match(suspended.answer_text, /绑定已暂停|重新绑定/)
  assert.equal((await runtime.getConversation(principal, id))
    .active_binding_generation, null)
})

test('缺少替代实体的 blocking ASN 修正不会继承旧 ASN 或执行事实节点', async () => {
  const planner = new FakePlanner()
    .add('AS48715 呢', { kind: 'asn_detail', entities: { asn: 48715 } })
    .add('不是这个 AS，换另一个', {
      kind: 'entity_correction',
      ambiguity: 'blocking',
    })
  const { runtime, id } = await service(planner)
  await turn(runtime, id, 'AS48715 呢', 1)
  const correction = await turn(runtime, id, '不是这个 AS，换另一个', 2)
  assert.equal(correction.answerability, 'clarify')
  assert.equal(correction.execution_trace.nodes.length, 0)
  assert.equal(correction.evidence.length, 0)
  assert.equal(correction.state_receipt.after.asn, null)
  assert.doesNotMatch(correction.answer_text, /AS48715|73|3,138|3138/)
})

test('未完成事件切换与事实目标同轮时所有旧事件事实均零执行', async () => {
  const question = '换一个事件，同时告诉我峰值'
  const planner = new FakePlanner().add(
    question,
    { kind: 'event_switch', requested: '换一个事件', ambiguity: 'blocking' },
    { kind: 'prefix_peak', requested: '告诉我峰值' },
  )
  const { runtime, id } = await service(planner)
  const answer = await turn(runtime, id, question, 1)
  assert.equal(answer.answerability, 'clarify')
  assert.equal(answer.execution_trace.nodes.length, 0)
  assert.equal(answer.evidence.length, 0)
  assert.doesNotMatch(answer.answer_text, /3,855|3855/)
  assert.equal((await runtime.getConversation(principal, id))
    .active_binding_generation, null)
})

test('聊天中的唯一事件引用完成两阶段原子 rebind，同行事实不读取旧事件', async () => {
  const nextReference = 'country_outage/2026-03-20 08:00:00/IR/1/r'
  const nextBinding = binding({
    incident_id: 'incident-ir-chat-r2',
    legacy_reference: nextReference,
    publication_id: 'publication-ir-chat-r2',
    revision: 2,
    cohort_id: 'cohort-ir-chat-r2',
  })
  const nextBundle = factBundle(nextBinding)
  nextBundle.overview.peaks.interrupted_prefix_count.value = 2222
  const switchQuestion = `切换到 ${nextReference}，然后告诉我峰值`
  const planner = new FakePlanner()
    .add('换一个事件', {
      kind: 'event_switch', ambiguity: 'blocking',
    })
    .add(
      switchQuestion,
      { kind: 'event_switch', references: [nextReference] },
      { kind: 'prefix_peak', requested: '告诉我峰值' },
    )
    .add('现在说峰值', { kind: 'prefix_peak' })
  const provider = new FakeProvider()
  provider.bundles.set(nextReference, nextBundle)
  const { runtime, id } = await service(planner, provider)
  const pending = await turn(runtime, id, '换一个事件', 1)
  assert.equal(pending.answerability, 'clarify')
  const switched = await turn(runtime, id, switchQuestion, 2)
  assert.equal(switched.answerability, 'partial')
  assert.equal(switched.results[0]?.answerability, 'supported')
  assert.equal(switched.results[1]?.answerability, 'clarify')
  assert.deepEqual(
    switched.execution_trace.nodes.map((node) => node.execution_unit),
    ['TOOL-01'],
  )
  assert.doesNotMatch(switched.answer_text, /3,855|3855/)
  assert.equal(switched.binding.publication_id, nextBinding.publication_id)
  const descriptor = await runtime.getConversation(principal, id)
  assert.equal(descriptor.binding_generation, 2)
  assert.equal(descriptor.active_binding_generation, 2)
  assert.equal(descriptor.binding.publication_id, nextBinding.publication_id)
  assert.equal(descriptor.dialog_state.pending_clarification?.startsWith('goal:'), true)
  const targetPeak = await turn(runtime, id, '现在说峰值', 3)
  assert.match(targetPeak.answer_text, /2,222|2222/)
  assert.equal(targetPeak.binding.publication_id, nextBinding.publication_id)
  assert.equal(descriptor.turns[0]?.answer?.binding.publication_id, publicationId)
})

test('P0-M05 证据追问后原因边界为 partial，并清除指标执行上下文', async () => {
  const planner = new FakePlanner()
    .add('峰值呢', { kind: 'prefix_peak' })
    .add('证据在哪里', { kind: 'evidence_trace' })
    .add('所以到底是谁造成的', { kind: 'cause_or_responsibility' })
  const { runtime, id } = await service(planner)
  await turn(runtime, id, '峰值呢', 1)
  const evidence = await turn(runtime, id, '证据在哪里', 2)
  const cause = await turn(runtime, id, '所以到底是谁造成的', 3)
  assert.match(evidence.answer_text, /dataset-ir-r1/)
  assert.equal(cause.answerability, 'partial')
  assert.match(cause.answer_text + cause.limitations.join(''), /不能|不足/)
  assert.equal(cause.state_receipt.after.metric, null)
  assert.equal(cause.state_receipt.after.topic, 'boundary')
})

test('模型失败整轮回滚，之后完整事实问题清除 stale clarification 并继续使用', async () => {
  const planner = new FakePlanner()
    .add('这个指的是谁', { kind: 'unknown', ambiguity: 'blocking' })
    .add('现在还有多少前缀不可见', { kind: 'current_prefix_state' })
  planner.failQuestion = '模型失败'
  const { runtime, id } = await service(planner)
  const clarify = await turn(runtime, id, '这个指的是谁', 1)
  assert.match(clarify.state_receipt.after.pending_clarification ?? '', /goal:/)
  const beforeFailure = await runtime.getConversation(principal, id)
  const failed = await runtime.createTurn(principal, id, {
    question: '模型失败',
    idempotency_key: 'turn-failure',
  })
  assert.equal(failed.turn.state, 'failed')
  const afterFailure = await runtime.getConversation(principal, id)
  assert.deepEqual(afterFailure.dialog_state, beforeFailure.dialog_state)
  const recovered = await turn(runtime, id, '现在还有多少前缀不可见', 3)
  assert.match(recovered.answer_text, /1,024|1024/)
  assert.equal(recovered.state_receipt.after.pending_clarification, null)
})

test('revision 执行中漂移与预取消均不提交回答或 DialogState', async () => {
  const planner = new FakePlanner().add('峰值呢', { kind: 'prefix_peak' })
  const first = await service(planner)
  first.provider.driftOnResolveCall = 3
  const drift = await first.runtime.createTurn(principal, first.id, {
    question: '峰值呢',
    idempotency_key: 'drift',
  })
  assert.equal(drift.turn.state, 'failed')
  assert.equal(drift.turn.error?.code, 'revision_drift')
  assert.equal((await first.runtime.getConversation(principal, first.id))
    .dialog_state.metric, null)

  const second = await service(planner)
  const controller = new AbortController()
  controller.abort()
  const cancelled = await second.runtime.createTurn(principal, second.id, {
    question: '峰值呢',
    idempotency_key: 'cancelled',
  }, controller.signal)
  assert.equal(cancelled.turn.state, 'cancelled')
  assert.equal(cancelled.turn.answer, undefined)
  assert.equal((await second.runtime.getConversation(principal, second.id))
    .dialog_state.metric, null)
})

test('两阶段合法重绑定清空旧状态、保留旧回答身份且重复请求不重复提交', async () => {
  const planner = new FakePlanner().add('AS48715 呢', {
    kind: 'asn_detail',
    entities: { asn: 48715 },
  })
  const { runtime, provider, id } = await service(planner)
  const oldAnswer = await turn(runtime, id, 'AS48715 呢', 1)
  const nextReference = 'country_outage/2026-03-20 08:00:00/IR/1/r'
  const nextBinding = binding({
    incident_id: 'incident-ir-r2',
    legacy_reference: nextReference,
    publication_id: 'publication-ir-r2',
    revision: 2,
    cohort_id: 'cohort-ir-r2',
  })
  provider.bundles.set(nextReference, factBundle(nextBinding))
  const request = {
    event_reference: nextReference,
    publication_id: nextBinding.publication_id,
    revision: nextBinding.revision,
    idempotency_key: 'rebind-1',
  }
  const rebound = await runtime.rebind(principal, id, request)
  const repeated = await runtime.rebind(principal, id, request)
  assert.equal(rebound.conversation.binding_generation, 2)
  assert.equal(rebound.conversation.active_binding_generation, 2)
  assert.equal(repeated.conversation.binding_generation, 2)
  assert.deepEqual(rebound.conversation.dialog_state, {
    topic: null,
    asn: null,
    address_family: null,
    metric: null,
    evidence_anchor: null,
    pending_clarification: null,
    last_committed_turn_number: 0,
  })
  assert.equal(oldAnswer.binding.publication_id, publicationId)
  assert.equal(rebound.conversation.binding.publication_id, 'publication-ir-r2')
  assert.equal(rebound.conversation.binding_history.length, 2)
})

test('失败重绑定保持旧 binding、EvidenceState 与 DialogState 字节等价', async () => {
  const planner = new FakePlanner().add('峰值呢', { kind: 'prefix_peak' })
  const { runtime, provider, id } = await service(planner)
  await turn(runtime, id, '峰值呢', 1)
  const before = await runtime.getConversation(principal, id)
  const badReference = 'country_outage/2026-03-20 08:00:00/IR/1/r'
  const badBinding = binding({
    legacy_reference: badReference,
    incident_id: 'bad-incident',
    publication_id: 'bad-publication',
  })
  provider.bundles.set(badReference, factBundle(badBinding))
  provider.failLoadReference = badReference
  await assert.rejects(runtime.rebind(principal, id, {
    event_reference: badReference,
    publication_id: badBinding.publication_id,
    revision: 1,
    idempotency_key: 'failed-rebind',
  }))
  const after = await runtime.getConversation(principal, id)
  assert.deepEqual(after.binding, before.binding)
  assert.deepEqual(after.evidence_state, before.evidence_state)
  assert.deepEqual(after.dialog_state, before.dialog_state)
  assert.deepEqual(after.binding_history, before.binding_history)
})

test('会话所有者、国家权限、到期和空幂等键都在执行前失败关闭', async () => {
  const { runtime, id } = await service(new FakePlanner())
  await assert.rejects(runtime.getConversation({
    userId: 'another-user',
    authorizationScope: 'country_outage_event_read:IR',
  }, id), /会话不存在或无权访问/)
  await assert.rejects(runtime.createTurn(principal, id, {
    question: '问题',
    idempotency_key: '',
  }), /幂等键不能为空/)

  const provider = new FakeProvider()
  await assert.rejects(new P1RuntimeV2ConversationService({
    provider,
    planner: new FakePlanner(),
  }).createConversation({
    userId: 'cn-only',
    authorizationScope: 'country_outage_event_read:CN',
  }, {
    event_reference: reference,
    publication_id: publicationId,
    revision: 1,
    idempotency_key: 'permission-denied',
  }), /无权读取 IR/)

  let now = new Date('2026-08-09T14:00:00Z')
  const expiring = new P1RuntimeV2ConversationService({
    provider: new FakeProvider(),
    planner: new FakePlanner(),
    ttlMs: 1_000,
    now: () => now,
  })
  const created = await expiring.createConversation(principal, {
    event_reference: reference,
    publication_id: publicationId,
    revision: 1,
    idempotency_key: 'expires',
  })
  now = new Date('2026-08-09T14:00:02Z')
  await assert.rejects(expiring.getConversation(
    principal,
    created.conversation.conversation_id,
  ), /会话已到期/)
})

test('轮次执行跨过 TTL 时在发布与状态提交前整轮回滚', async () => {
  let now = new Date('2026-08-09T14:00:00Z')
  const provider = new FakeProvider()
  const planner = new FakePlanner().add('峰值呢', { kind: 'prefix_peak' })
  const runtime = new P1RuntimeV2ConversationService({
    provider,
    planner,
    ttlMs: 1_000,
    now: () => now,
  })
  const created = await runtime.createConversation(principal, {
    event_reference: reference,
    publication_id: publicationId,
    revision: 1,
    idempotency_key: 'ttl-create',
  })
  const originalResolve = provider.resolve.bind(provider)
  provider.resolve = async (value: string) => {
    const resolved = await originalResolve(value)
    if (provider.resolveCalls === 3) {
      now = new Date('2026-08-09T14:00:02Z')
    }
    return resolved
  }
  const result = await runtime.createTurn(
    principal,
    created.conversation.conversation_id,
    { question: '峰值呢', idempotency_key: 'ttl-turn' },
  )
  assert.equal(result.turn.state, 'failed')
  assert.equal(result.turn.error?.code, 'conversation_expired')
  assert.equal(result.turn.answer, undefined)
  now = new Date('2026-08-09T14:00:00Z')
  const restored = await runtime.getConversation(
    principal,
    created.conversation.conversation_id,
  )
  assert.equal(restored.dialog_state.metric, null)
})

test('能力不可用只裁决对应子目标，不拒绝可回答兄弟目标', async () => {
  const question = '现在还有多少前缀不可见，同时看 AS49556'
  const planner = new FakePlanner().add(
    question,
    { kind: 'current_prefix_state', requested: '现在还有多少前缀不可见' },
    { kind: 'asn_detail', requested: '看 AS49556', entities: { asn: 49556 } },
  )
  const provider = new FakeProvider()
  const unavailable = binding({
    capabilities: {
      ...binding().capabilities,
      affected_as: 'unavailable',
    },
  })
  provider.bundles.set(reference, factBundle(unavailable))
  const { runtime, id } = await service(planner, provider)
  const answer = await turn(runtime, id, question, 1)
  assert.equal(answer.answerability, 'partial')
  assert.equal(answer.results[0]?.answerability, 'supported')
  assert.match(answer.results[0]?.text ?? '', /1,024|1024/)
  assert.equal(answer.results[1]?.answerability, 'unsupported')
  assert.match(answer.results[1]?.text ?? '', /没有协商到所需能力/)
  assert.ok(!answer.execution_trace.nodes.some(
    (node) => node.execution_unit === 'TOOL-04',
  ))
  assert.equal(answer.state_receipt.after.metric, 'interrupted_prefix_count')
})

test('SCE-10 Update、trend 与外部证据边界复用冻结 policy 且均为零执行', async () => {
  const missingUpdateQuestion =
    'series 里没有 Update 轨道，是不是说明 Update 数量一直为 0？'
  const planner = new FakePlanner()
    .add('BGP Update 有多少', { kind: 'bgp_update_activity' })
    .add(missingUpdateQuestion, { kind: 'capability_absent_not_zero' })
    .add('趋势怎么样', { kind: 'trend_analysis' })
    .add('查一下 OONI', { kind: 'external_evidence' })
  const { runtime, id } = await service(planner)
  const update = await turn(runtime, id, 'BGP Update 有多少', 1)
  const missingUpdate = await turn(runtime, id, missingUpdateQuestion, 2)
  const trend = await turn(runtime, id, '趋势怎么样', 3)
  const external = await turn(runtime, id, '查一下 OONI', 4)
  for (const answer of [update, missingUpdate, trend, external]) {
    assert.equal(answer.execution_trace.nodes.length, 0)
    assert.equal(answer.evidence.length, 0)
  }
  assert.equal(update.answerability, 'unsupported')
  assert.equal(missingUpdate.answerability, 'invalid_data')
  assert.equal(trend.answerability, 'unsupported')
  assert.equal(external.answerability, 'unsupported')
  assert.match(update.answer_text, /不可用.*不是.*0/)
  assert.match(missingUpdate.answer_text, /不可用.*不是.*0/)
  assert.match(trend.answer_text, /未提供已发布趋势能力|不能把缺失解释为没有变化/)
  assert.match(external.answer_text, /未配置.*外部证据/)
})

test('SCE-10 event_end=null 保持未知，AS 空结果不写入可执行状态', async () => {
  const planner = new FakePlanner()
    .add('事件结束了吗', { kind: 'event_end_state' })
    .add('AS64512 呢', { kind: 'asn_detail', entities: { asn: 64512 } })
  const { runtime, id } = await service(planner)
  const eventEnd = await turn(runtime, id, '事件结束了吗', 1)
  assert.equal(eventEnd.answerability, 'partial')
  assert.ok(eventEnd.evidence.some((item) =>
    item.evidence_ref === 'event.event_end_at_utc'
      && item.value === null
  ))
  assert.ok(eventEnd.evidence.some((item) =>
    item.evidence_ref === 'event.event_duration_seconds'
      && item.value === null
      && item.unit === 'second'
  ))
  assert.match(eventEnd.answer_text, /未知|不能确认|无法确认/)
  assert.doesNotMatch(eventEnd.answer_text, /已经恢复|恢复正常/)
  const emptyAsn = await turn(runtime, id, 'AS64512 呢', 2)
  assert.equal(emptyAsn.results[0]?.answerability, 'invalid_data')
  assert.equal(emptyAsn.results[0]?.evidence_refs.length, 0)
  assert.equal(emptyAsn.state_receipt.after.asn, null)
})

test('SCE-10 多目标共享身份冲突不发布任何局部事实并整轮回滚', async () => {
  const question = '现在还有多少前缀不可见，同时看 AS49556'
  const planner = new FakePlanner().add(
    question,
    { kind: 'current_prefix_state' },
    { kind: 'asn_detail', entities: { asn: 49556 } },
  )
  const instance = await service(planner)
  instance.provider.driftOnResolveCall = 3
  const before = await instance.runtime.getConversation(principal, instance.id)
  const result = await instance.runtime.createTurn(principal, instance.id, {
    question,
    idempotency_key: 'shared-identity-conflict',
  })
  assert.equal(result.turn.state, 'failed')
  assert.equal(result.turn.error?.code, 'revision_drift')
  assert.equal(result.turn.answer, undefined)
  const after = await instance.runtime.getConversation(principal, instance.id)
  assert.deepEqual(after.dialog_state, before.dialog_state)
  assert.equal(after.active_binding_generation, before.active_binding_generation)
})

test('P0-X-04 overview 与 series 跨 publication 冲突时整轮失败且旧状态不变', async () => {
  const question =
    'overview 是当前 publication，但 series 返回另一个 publication，能继续回答峰值吗？'
  const planner = new FakePlanner().add(question, { kind: 'prefix_peak' })
  const instance = await service(planner)
  const before = await instance.runtime.getConversation(principal, instance.id)
  assert.ok(instance.provider.lastLoadedBundle)
  const expectedPublicationId =
    instance.provider.lastLoadedBundle!.binding.publication_id
  instance.provider.lastLoadedBundle!.series.publication_id =
    'country_outage_publication_conflict_fixture'
  const result = await instance.runtime.createTurn(principal, instance.id, {
    question,
    idempotency_key: 'cross-tool-publication-conflict',
  })
  assert.equal(result.turn.state, 'failed')
  assert.equal(result.turn.error?.code, 'publication_identity_conflict')
  assert.equal(result.turn.answer, undefined)
  const after = await instance.runtime.getConversation(principal, instance.id)
  assert.deepEqual(after.binding, before.binding)
  assert.deepEqual(after.evidence_state, before.evidence_state)
  assert.deepEqual(after.dialog_state, before.dialog_state)
  assert.equal(after.active_binding_generation, before.active_binding_generation)
  assert.equal(planner.contexts.length, 0)
  writeControlledFailureReceipt('P013-X-04', {
    test_name:
      'P0-X-04 overview 与 series 跨 publication 冲突时整轮失败且旧状态不变',
    question,
    fault_injection: {
      gate: 'pre_execution_fact_bundle_identity',
      field: 'series.publication_id',
      expected: expectedPublicationId,
      actual: instance.provider.lastLoadedBundle!.series.publication_id,
    },
    pipeline_checkpoints: {
      user_goal_plan: { status: 'not_reached', planner_call_count: 0 },
      grounding_plan: { status: 'not_reached', nodes: [] },
      tool_execution: { status: 'not_started', nodes: [] },
    },
    failure: {
      turn_state: result.turn.state,
      error: result.turn.error,
      published_answer: result.turn.answer ?? null,
      published_evidence: [],
      model_generated_fact_count: 0,
    },
    state_receipt: {
      status: 'rolled_back',
      before: {
        binding: before.binding,
        evidence_state: before.evidence_state,
        dialog_state: before.dialog_state,
        active_binding_generation: before.active_binding_generation,
      },
      after: {
        binding: after.binding,
        evidence_state: after.evidence_state,
        dialog_state: after.dialog_state,
        active_binding_generation: after.active_binding_generation,
      },
      equality: {
        binding: true,
        evidence_state: true,
        dialog_state: true,
        active_binding_generation: true,
      },
    },
  })
})

test('P0-X-05 series 声明人口与轨道长度不一致时整轮失败且不计算极值', async () => {
  const question =
    'series 声明 3,455 个点，但 timestamps 和轨道长度少了一个，应该怎么回答？'
  const planner = new FakePlanner().add(
    question,
    {
      kind: 'address_family_change',
      entities: { address_family: 'ipv4' },
    },
  )
  const instance = await service(planner)
  const before = await instance.runtime.getConversation(principal, instance.id)
  assert.ok(instance.provider.lastLoadedBundle?.series.tracks)
  const series = instance.provider.lastLoadedBundle!.series
  series.point_count = 3455
  series.timestamps = Array.from(
    { length: 3454 },
    (_value, index) => `2026-02-27T${String(index % 24).padStart(2, '0')}:00:00Z`,
  )
  for (const metric of Object.keys(series.tracks!)) {
    const key = metric as keyof typeof series.tracks
    series.tracks![key] = Array.from({ length: 3454 }, () => 0)
  }
  const declaredPointCount = series.point_count
  const timestampCount = series.timestamps.length
  const result = await instance.runtime.createTurn(principal, instance.id, {
    question,
    idempotency_key: 'invalid-series-shape',
  })
  assert.equal(result.turn.state, 'failed')
  assert.equal(result.turn.error?.code, 'invalid_series_shape')
  assert.equal(result.turn.answer, undefined)
  const after = await instance.runtime.getConversation(principal, instance.id)
  assert.deepEqual(after.binding, before.binding)
  assert.deepEqual(after.evidence_state, before.evidence_state)
  assert.deepEqual(after.dialog_state, before.dialog_state)
  assert.equal(after.active_binding_generation, before.active_binding_generation)
  assert.equal(planner.contexts.length, 0)
  writeControlledFailureReceipt('P013-X-05', {
    test_name:
      'P0-X-05 series 声明人口与轨道长度不一致时整轮失败且不计算极值',
    question,
    fault_injection: {
      gate: 'pre_execution_series_shape',
      declared_point_count: declaredPointCount,
      timestamps_length: timestampCount,
      track: 'fixed_visible_ipv4_address_count',
      track_length:
        series.tracks!.fixed_visible_ipv4_address_count.length,
    },
    pipeline_checkpoints: {
      user_goal_plan: { status: 'not_reached', planner_call_count: 0 },
      grounding_plan: { status: 'not_reached', nodes: [] },
      tool_execution: { status: 'not_started', nodes: [] },
    },
    failure: {
      turn_state: result.turn.state,
      error: result.turn.error,
      published_answer: result.turn.answer ?? null,
      published_evidence: [],
      model_generated_fact_count: 0,
    },
    state_receipt: {
      status: 'rolled_back',
      before: {
        binding: before.binding,
        evidence_state: before.evidence_state,
        dialog_state: before.dialog_state,
        active_binding_generation: before.active_binding_generation,
      },
      after: {
        binding: after.binding,
        evidence_state: after.evidence_state,
        dialog_state: after.dialog_state,
        active_binding_generation: after.active_binding_generation,
      },
      equality: {
        binding: true,
        evidence_state: true,
        dialog_state: true,
        active_binding_generation: true,
      },
    },
  })
})

test('postflight capability-only 漂移按共享绑定冲突整轮回滚', async () => {
  const planner = new FakePlanner().add('峰值呢', { kind: 'prefix_peak' })
  const instance = await service(planner)
  instance.provider.capabilityDriftOnResolveCall = 3
  const before = await instance.runtime.getConversation(principal, instance.id)
  const result = await instance.runtime.createTurn(principal, instance.id, {
    question: '峰值呢',
    idempotency_key: 'capability-drift',
  })
  assert.equal(result.turn.state, 'failed')
  assert.equal(result.turn.error?.code, 'revision_drift')
  assert.equal(result.turn.answer, undefined)
  const after = await instance.runtime.getConversation(principal, instance.id)
  assert.deepEqual(after.dialog_state, before.dialog_state)
  assert.deepEqual(after.binding.capabilities, before.binding.capabilities)
})

test('幂等键同代冲突，跨 binding generation 则建立当前 publication 新轮次', async () => {
  const planner = new FakePlanner().add('峰值呢', { kind: 'prefix_peak' })
  const { runtime, provider, id } = await service(planner)
  await runtime.createTurn(principal, id, {
    question: '峰值呢',
    idempotency_key: 'same-turn-key',
  })
  await assert.rejects(runtime.createTurn(principal, id, {
    question: '另一个问题',
    idempotency_key: 'same-turn-key',
  }), /同一幂等键不能用于不同问题/)

  const nextReference = 'country_outage/2026-04-01 00:00:00/IR/1/r'
  const nextBinding = binding({
    legacy_reference: nextReference,
    incident_id: 'incident-next',
    publication_id: 'publication-next',
  })
  provider.bundles.set(nextReference, factBundle(nextBinding))
  await runtime.rebind(principal, id, {
    event_reference: nextReference,
    publication_id: nextBinding.publication_id,
    revision: 1,
    idempotency_key: 'same-rebind-key',
  })
  const newGenerationTurn = await runtime.createTurn(principal, id, {
    question: '峰值呢',
    idempotency_key: 'same-turn-key',
  })
  assert.equal(newGenerationTurn.deduplicated, false)
  assert.equal(
    newGenerationTurn.turn.answer?.binding.publication_id,
    'publication-next',
  )
  await assert.rejects(runtime.rebind(principal, id, {
    event_reference: reference,
    publication_id: publicationId,
    revision: 1,
    idempotency_key: 'same-rebind-key',
  }), /同一幂等键不能切换到不同事件/)
})

test('多目标中单一 Tool 失败只关闭对应子目标并保留其他已验证事实', async () => {
  const question = '现在还有多少前缀不可见，同时看 AS49556'
  const planner = new FakePlanner().add(
    question,
    { kind: 'current_prefix_state', requested: '现在还有多少前缀不可见' },
    { kind: 'asn_detail', requested: '看 AS49556', entities: { asn: 49556 } },
  )
  const provider = new FakeProvider()
  provider.failAsn = 49556
  const { runtime, id } = await service(planner, provider)
  const answer = await turn(runtime, id, question, 1)
  assert.equal(answer.answerability, 'partial')
  assert.equal(answer.results[0]?.answerability, 'supported')
  assert.match(answer.results[0]?.text ?? '', /1,024|1024/)
  assert.equal(answer.results[1]?.answerability, 'invalid_data')
  assert.equal(answer.results[1]?.evidence_refs.length, 0)
  assert.deepEqual(
    answer.execution_trace.nodes
      .filter((node) => node.status === 'failed')
      .map((node) => node.execution_unit),
    ['TOOL-04'],
  )
  assert.equal(answer.state_receipt.after.metric, 'interrupted_prefix_count')
})

test('执行回执按 goal/node 绑定真实证据，解析节点不被下游失败误标', async () => {
  const question = '看 AS49556，同时告诉我现在还有多少前缀不可见'
  const planner = new FakePlanner().add(
    question,
    { kind: 'asn_detail', entities: { asn: 49556 } },
    { kind: 'current_prefix_state' },
  )
  const provider = new FakeProvider()
  provider.failAsn = 49556
  const { runtime, id } = await service(planner, provider)
  const answer = await turn(runtime, id, question, 1)
  const resolution = answer.execution_trace.nodes.find(
    (node) => node.execution_unit === 'TOOL-01',
  )
  const asnNode = answer.execution_trace.nodes.find(
    (node) => node.execution_unit === 'TOOL-04',
  )
  const overviewNode = answer.execution_trace.nodes.find(
    (node) => node.execution_unit === 'TOOL-02',
  )
  assert.equal(resolution?.status, 'passed')
  assert.deepEqual(resolution?.evidence_refs, ['resolution.data_through'])
  assert.equal(asnNode?.status, 'failed')
  assert.deepEqual(asnNode?.evidence_refs, [])
  assert.equal(overviewNode?.status, 'passed')
  assert.deepEqual(
    new Set(overviewNode?.evidence_refs),
    new Set(answer.results[1]?.evidence_refs),
  )
  assert.ok(!(overviewNode?.evidence_refs ?? []).some((ref) =>
    ref.startsWith('asns.')
  ))
})
