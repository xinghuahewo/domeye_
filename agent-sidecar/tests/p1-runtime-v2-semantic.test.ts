import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

import {
  P1ModelUserGoalPlanner,
  P1RuntimeV2Grounder,
  P1RuntimeV2SemanticTurnService,
  P1RuntimeV2SingleTurnError,
  P1RuntimeV2SingleTurnService,
  type P1ConversationBinding,
  type P1RawSemanticModel,
  type P1RuntimeV2ReadProvider,
  type P1UserGoalPlan,
  type P1UserGoalPlanner,
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
      fixed_prefix_count: 9257,
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
  }
}

class FakeProvider implements P1RuntimeV2ReadProvider {
  resolveCalls = 0
  overviewCalls = 0
  resolved = binding()

  async resolve() {
    this.resolveCalls += 1
    return structuredClone(this.resolved)
  }

  async readOverview() {
    this.overviewCalls += 1
    return structuredClone(overview())
  }
}

class FakeRawModel implements P1RawSemanticModel {
  readonly identity = 'fake-strong-model-v1'
  calls = 0
  prompts: string[] = []

  constructor(public output: string) {}

  async complete(prompt: string) {
    this.calls += 1
    this.prompts.push(prompt)
    return this.output
  }
}

function plan(
  question: string,
  goals: Array<{
    requested_goal: string
    normalized_kind: string
    ambiguity?: 'none' | 'non_blocking' | 'blocking'
  }>,
): P1UserGoalPlan {
  return {
    plan_revision: 'user-goal-plan-v2',
    original_question: question,
    goals: goals.map((goal, index) => ({
      goal_id: `goal-${index + 1}`,
      requested_goal: goal.requested_goal,
      normalized_kind: goal.normalized_kind,
      entities: {},
      references: [],
      ambiguity: goal.ambiguity ?? 'none',
      context_dependencies: [],
    })),
    state_proposal: {
      inherit: [],
      set: {},
      clear: [],
      reason_codes: [],
    },
    planner_identity: 'model-output',
    confidence: 0.96,
  }
}

function service(model: FakeRawModel, provider = new FakeProvider()) {
  const planner = new P1ModelUserGoalPlanner(model)
  return {
    provider,
    planner,
    service: new P1RuntimeV2SemanticTurnService(
      provider,
      new P1RuntimeV2SingleTurnService(
        provider,
        () => new Date('2026-08-09T12:00:00Z'),
      ),
      planner,
      new P1RuntimeV2Grounder(),
      () => new Date('2026-08-09T12:00:00Z'),
    ),
  }
}

const principal = {
  userId: 'semantic-user',
  authorizationScope: 'country_outage_event_read:IR',
}

function request(question: string) {
  return {
    event_reference: eventReference,
    publication_id: publicationId,
    revision: 1,
    question,
  }
}

test('同义和口语表达由开放 UserGoalPlan 进入同一封闭概览计划', async () => {
  const questions = ['这次伊朗事件发生了什么？', '这波到底咋回事']
  for (const question of questions) {
    const model = new FakeRawModel(JSON.stringify(plan(question, [{
      requested_goal: question,
      normalized_kind: 'event_summary',
    }])))
    const runtime = service(model)
    const answer = await runtime.service.answer(principal, request(question))
    assert.equal(answer.semantic_plan.user_goal_plan.original_question, question)
    assert.equal(answer.semantic_plan.user_goal_plan.goals[0]?.requested_goal, question)
    assert.deepEqual(
      answer.semantic_plan.grounding_plan.nodes.map((node) => node.execution_unit),
      ['TOOL-01', 'TOOL-02'],
    )
    assert.deepEqual(
      answer.semantic_plan.grounding_plan.nodes[1]?.capability_ids,
      ['CAP-002', 'CAP-003', 'CAP-004'],
    )
    assert.equal(answer.execution_trace.model_generated_fact_count, 0)
    assert.equal(answer.execution_trace.state_commit, 'none')
    assert.equal(answer.validation.grounding_legality, 'passed')
  }
})

test('多意图只回答当前前缀事实并单独拒绝全国与用户影响', async () => {
  const question = '现在还有多少前缀不可见，是不是全国都断了？'
  const model = new FakeRawModel(JSON.stringify(plan(question, [
    {
      requested_goal: '现在还有多少前缀不可见',
      normalized_kind: 'current_prefix_state',
    },
    {
      requested_goal: '是不是全国都断了',
      normalized_kind: 'real_user_or_national_impact',
    },
  ])))
  const runtime = service(model)
  const answer = await runtime.service.answer(principal, request(question))
  assert.equal(answer.answerability, 'partial')
  assert.equal(answer.results.length, 2)
  assert.equal(answer.results[0]?.answerability, 'supported')
  assert.match(answer.results[0]?.text ?? '', /1,024 个前缀/)
  assert.equal(answer.results[1]?.answerability, 'unsupported')
  assert.match(answer.results[1]?.text ?? '', /不能据此判断全国是否断网/)
  assert.deepEqual(answer.results[1]?.evidence_refs, [])
  assert.equal(runtime.provider.resolveCalls, 1)
  assert.equal(runtime.provider.overviewCalls, 1)
  assert.equal(answer.evidence.length, 3)
})

test('现行对话 Prompt 不为单一用户或全国问题制造事实目标并区分剩余数量', async () => {
  const question = '普通用户现在还能上网吗'
  const model = new FakeRawModel(JSON.stringify(plan(question, [{
    requested_goal: question,
    normalized_kind: 'real_user_or_national_impact',
  }])))
  const runtime = service(model)
  await runtime.planner.plan(question, {
    event_type: 'country_outage',
    country_code: 'IR',
    event_reference: eventReference,
    has_dialog_state: true,
    dialog_state: {
      topic: null,
      asn: null,
      address_family: null,
      metric: null,
      evidence_anchor: null,
      pending_clarification: null,
    },
  })
  const prompt = model.prompts[0] ?? ''
  assert.match(prompt, /只保留 real_user_or_national_impact/)
  assert.match(prompt, /不得凭空增加 current_prefix_state/)
  assert.match(prompt, /到最后还剩多少路由没回来/)
  assert.match(prompt, /使用 current_prefix_state/)
  assert.match(prompt, /峰值之后还有多少前缀持续异常/)
  assert.match(prompt, /使用 remaining_vs_peak/)
  assert.match(prompt, /不能证明中间连续性/)
  assert.match(prompt, /它什么时候最严重/)
  assert.match(prompt, /使用 prefix_peak/)
  assert.match(prompt, /fact_timeline 只用于用户明确要求按时间线/)
  assert.match(prompt, /解释边界属于同一个 path_sample 目标/)
  assert.match(prompt, /不得创造 evidence_interpretation/)
  assert.match(prompt, /限制由确定性回答表达/)
  assert.match(prompt, /固定范围\/固定 cohort 里目前的中断规模/)
  assert.match(prompt, /不得用单一前缀值替代范围人口/)
  assert.match(prompt, /经济损失、金额或业务损失使用独立的 economic_impact/)
  assert.match(prompt, /不能把经济损失吞进用户影响/)
})

test('多个事实目标逐节点真实执行且只绑定各自证据', async () => {
  const question = '观测窗口到什么时候，事件结束了吗？'
  const model = new FakeRawModel(JSON.stringify(plan(question, [
    {
      requested_goal: '观测窗口到什么时候',
      normalized_kind: 'observation_window',
    },
    {
      requested_goal: '事件结束了吗',
      normalized_kind: 'event_end_state',
    },
  ])))
  const runtime = service(model)
  const answer = await runtime.service.answer(principal, request(question))

  assert.equal(answer.answerability, 'partial')
  assert.equal(runtime.provider.resolveCalls, 1)
  assert.equal(runtime.provider.overviewCalls, 2)
  assert.deepEqual(
    answer.execution_trace.nodes.map((node) => [
      node.node_id,
      node.execution_unit,
      node.capability_ids,
    ]),
    [
      ['node-1', 'TOOL-01', ['CAP-001']],
      ['node-2', 'TOOL-02', ['CAP-002']],
      ['node-3', 'TOOL-02', ['CAP-002']],
    ],
  )
  const resultByGoal = new Map(answer.results.map((result) => [
    result.goal_id,
    new Set(result.evidence_refs),
  ]))
  const nodeById = new Map(
    answer.semantic_plan.grounding_plan.nodes.map((node) => [node.node_id, node]),
  )
  for (const receipt of answer.execution_trace.nodes) {
    const groundingNode = nodeById.get(receipt.node_id)
    assert.ok(groundingNode)
    const allowed = resultByGoal.get(groundingNode.goal_id)
    assert.ok(allowed)
    assert.ok(receipt.evidence_refs.length > 0)
    assert.ok(receipt.evidence_refs.every((ref) => allowed.has(ref)))
    if (receipt.execution_unit === 'TOOL-02') {
      assert.ok(receipt.evidence_refs.every((ref) => ref.startsWith('overview.')))
    }
  }
  assert.deepEqual(
    answer.execution_trace.nodes[1]?.evidence_refs,
    ['overview.event.detected_at_utc'],
  )
  assert.deepEqual(
    answer.execution_trace.nodes[2]?.evidence_refs,
    ['overview.event.event_end_at_utc'],
  )
})

test('明确的事件处置建议属于越界而不是需要澄清', async () => {
  const question = '给这次事件写一套处置建议'
  for (const normalizedKind of [
    'remediation_recommendation',
    'incident_response_recommendations',
  ]) {
    const model = new FakeRawModel(JSON.stringify(plan(question, [{
      requested_goal: question,
      normalized_kind: normalizedKind,
    }])))
    const runtime = service(model)
    const answer = await runtime.service.answer(principal, request(question))
    assert.equal(answer.answerability, 'unsupported')
    assert.equal(answer.results[0]?.answerability, 'unsupported')
    assert.equal(answer.semantic_plan.grounding_plan.nodes.length, 0)
    assert.equal(runtime.provider.overviewCalls, 0)
    assert.match(answer.answer_text, /事件处置建议不属于 P1/)
  }
})

test('开放标签不做宿主关键词改写且否定提及不压掉合法事实目标', async () => {
  const question = '不要给我处置建议，只说当前还有多少前缀不可见'
  const supportedModel = new FakeRawModel(JSON.stringify(plan(question, [{
    requested_goal: question,
    normalized_kind: 'current_prefix_state',
  }])))
  const supportedRuntime = service(supportedModel)
  const supported = await supportedRuntime.service.answer(
    principal,
    request(question),
  )
  assert.equal(supported.answerability, 'supported')
  assert.match(supported.answer_text, /1,024 个前缀/)
  assert.equal(supportedRuntime.provider.overviewCalls, 1)

  const unknownModel = new FakeRawModel(JSON.stringify(plan(question, [{
    requested_goal: question,
    normalized_kind: 'unknown',
    ambiguity: 'blocking',
  }])))
  const unknownRuntime = service(unknownModel)
  const unknown = await unknownRuntime.service.answer(principal, request(question))
  assert.equal(unknown.answerability, 'clarify')
  assert.equal(unknownRuntime.provider.overviewCalls, 0)

  const positiveUnknownQuestion = '给这次事件写一套处置建议'
  const positiveUnknownModel = new FakeRawModel(JSON.stringify(plan(
    positiveUnknownQuestion,
    [{
      requested_goal: positiveUnknownQuestion,
      normalized_kind: 'unknown',
      ambiguity: 'blocking',
    }],
  )))
  const positiveUnknownRuntime = service(positiveUnknownModel)
  const positiveUnknown = await positiveUnknownRuntime.service.answer(
    principal,
    request(positiveUnknownQuestion),
  )
  assert.equal(positiveUnknown.answerability, 'clarify')
  assert.equal(positiveUnknownRuntime.provider.overviewCalls, 0)

  for (const negatedQuestion of [
    '我没让你写应急方案，那个数字现在呢',
    '解释为什么系统不提供响应方案',
  ]) {
    const negatedModel = new FakeRawModel(JSON.stringify(plan(
      negatedQuestion,
      [{
        requested_goal: negatedQuestion,
        normalized_kind: 'unknown',
        ambiguity: 'blocking',
      }],
    )))
    const negatedRuntime = service(negatedModel)
    const negated = await negatedRuntime.service.answer(
      principal,
      request(negatedQuestion),
    )
    assert.equal(negated.answerability, 'clarify')
    assert.notEqual(
      negated.semantic_plan.grounding_plan.decisions[0]?.reason_codes[0],
      'remediation_recommendation_not_in_p1',
    )
    assert.equal(negatedRuntime.provider.overviewCalls, 0)
  }
})

test('原因、责任和政府行为目标原样保留且不调用 overview', async () => {
  const question = '是不是政府操作导致的，谁负责？'
  const model = new FakeRawModel(JSON.stringify(plan(question, [{
    requested_goal: '是不是政府操作导致的，谁负责',
    normalized_kind: 'cause_or_responsibility',
  }])))
  const runtime = service(model)
  const answer = await runtime.service.answer(principal, request(question))
  assert.equal(answer.answerability, 'unsupported')
  assert.equal(
    answer.semantic_plan.user_goal_plan.goals[0]?.requested_goal,
    '是不是政府操作导致的，谁负责',
  )
  assert.equal(answer.semantic_plan.grounding_plan.nodes.length, 0)
  assert.equal(runtime.provider.overviewCalls, 0)
  assert.equal(answer.evidence.length, 0)
  assert.match(answer.answer_text, /不能据此判断原因、责任主体或政府行为/)
})

test('模型无效 JSON 安全回退为完整原目标的澄清且不执行事实 Tool', async () => {
  const question = '帮我看看这次的情况'
  const runtime = service(new FakeRawModel('```json\n{}\n```'))
  const answer = await runtime.service.answer(principal, request(question))
  assert.equal(answer.execution_trace.planner_outcome, 'safe_fallback')
  assert.equal(answer.answerability, 'clarify')
  assert.equal(
    answer.semantic_plan.user_goal_plan.goals[0]?.requested_goal,
    question,
  )
  assert.equal(answer.semantic_plan.grounding_plan.nodes.length, 0)
  assert.equal(runtime.provider.overviewCalls, 0)
  assert.equal(answer.execution_trace.state_commit, 'none')
})

test('模型试图提交状态时整份输出被拒绝并安全回退', async () => {
  const question = '这次发生了什么'
  const invalid = plan(question, [{
    requested_goal: question,
    normalized_kind: 'event_summary',
  }])
  invalid.state_proposal.set = { metric: 'secret-model-state' }
  const runtime = service(new FakeRawModel(JSON.stringify(invalid)))
  const answer = await runtime.service.answer(principal, request(question))
  assert.equal(answer.execution_trace.planner_outcome, 'safe_fallback')
  assert.deepEqual(answer.semantic_plan.user_goal_plan.state_proposal.set, {})
  assert.equal(runtime.provider.overviewCalls, 0)
})

test('模型重复声明当前 event_reference 继承时由宿主机械移除而不获得状态权限', async () => {
  const question = '这次发生了什么'
  const redundant = plan(question, [{
    requested_goal: question,
    normalized_kind: 'event_summary',
  }])
  redundant.state_proposal.inherit = ['event_reference']
  const runtime = service(new FakeRawModel(JSON.stringify(redundant)))
  const answer = await runtime.service.answer(principal, request(question))
  assert.equal(answer.execution_trace.planner_outcome, 'accepted')
  assert.deepEqual(
    answer.semantic_plan.user_goal_plan.state_proposal.inherit,
    [],
  )
  assert.ok(
    answer.semantic_plan.user_goal_plan.state_proposal.reason_codes
      .includes('host_removed_redundant_event_binding_inherit'),
  )
  assert.equal(answer.execution_trace.state_commit, 'none')
})

test('未知开放目标不被 Tool 白名单删除而是原样进入不执行裁决', async () => {
  const question = '把这次事件和邻国做一个反事实模拟'
  const unknown = plan(question, [{
    requested_goal: '把这次事件和邻国做一个反事实模拟',
    normalized_kind: 'counterfactual_neighbor_simulation',
  }])
  const runtime = service(new FakeRawModel(JSON.stringify(unknown)))
  const answer = await runtime.service.answer(principal, request(question))
  assert.equal(answer.answerability, 'clarify')
  assert.equal(
    answer.semantic_plan.user_goal_plan.goals[0]?.normalized_kind,
    'counterfactual_neighbor_simulation',
  )
  assert.equal(
    answer.semantic_plan.user_goal_plan.goals[0]?.requested_goal,
    question,
  )
  assert.equal(runtime.provider.overviewCalls, 0)
})

test('Grounding Validator 以 100% 硬门拒绝模型或代码创造的能力', () => {
  const userPlan = plan('发生了什么', [{
    requested_goal: '发生了什么',
    normalized_kind: 'event_summary',
  }])
  const grounder = new P1RuntimeV2Grounder()
  const legal = grounder.ground(userPlan, binding(), eventReference)
  const illegal = structuredClone(legal)
  illegal.grounding_plan.nodes[1]!.capability_ids = [
    'CAP-GOVERNMENT-SHUTDOWN',
  ]
  const errors = grounder.validate(illegal, binding())
  assert.ok(errors.some((error) =>
    error.includes('GND-05:capability_unit_mismatch')
  ))
})

test('原问题漂移、附加 tool 字段和目标编号跳号均不得进入 grounding', async () => {
  const question = '发生了什么'
  const invalidPlans: unknown[] = [
    { ...plan('被模型改写的问题', [{
      requested_goal: '发生了什么',
      normalized_kind: 'event_summary',
    }]) },
    {
      ...plan(question, [{
        requested_goal: question,
        normalized_kind: 'event_summary',
      }]),
      tool: 'root_cause_analysis',
    },
    {
      ...plan(question, [{
        requested_goal: question,
        normalized_kind: 'event_summary',
      }]),
      goals: [{
        ...plan(question, [{
          requested_goal: question,
          normalized_kind: 'event_summary',
        }]).goals[0],
        goal_id: 'goal-2',
      }],
    },
  ]
  for (const invalid of invalidPlans) {
    const runtime = service(new FakeRawModel(JSON.stringify(invalid)))
    const answer = await runtime.service.answer(principal, request(question))
    assert.equal(answer.execution_trace.planner_outcome, 'safe_fallback')
    assert.equal(answer.semantic_plan.grounding_plan.nodes.length, 0)
    assert.equal(runtime.provider.overviewCalls, 0)
  }
})

test('无权限主体在 resolver 和模型之前失败关闭', async () => {
  const question = '发生了什么'
  const model = new FakeRawModel(JSON.stringify(plan(question, [{
    requested_goal: question,
    normalized_kind: 'event_summary',
  }])))
  const runtime = service(model)
  await assert.rejects(
    runtime.service.answer(
      { userId: 'denied', authorizationScope: 'country_outage:write' },
      request(question),
    ),
    (error: unknown) =>
      error instanceof P1RuntimeV2SingleTurnError
      && error.code === 'permission_denied',
  )
  assert.equal(runtime.provider.resolveCalls, 0)
  assert.equal(model.calls, 0)
})

test('错误 publication 在模型调用前失败关闭', async () => {
  const question = '发生了什么'
  const model = new FakeRawModel(JSON.stringify(plan(question, [{
    requested_goal: question,
    normalized_kind: 'event_summary',
  }])))
  const runtime = service(model)
  runtime.provider.resolved.publication_id = 'wrong-publication'
  await assert.rejects(
    runtime.service.answer(principal, request(question)),
    (error: unknown) =>
      error instanceof P1RuntimeV2SingleTurnError
      && error.code === 'binding_conflict',
  )
  assert.equal(model.calls, 0)
  assert.equal(runtime.provider.overviewCalls, 0)
})

test('直接注入的 planner 仍必须经过同一 Grounding Validator', async () => {
  const question = '数据截至什么时候'
  const injected: P1UserGoalPlanner = {
    identity: 'injected-planner',
    async plan() {
      return plan(question, [{
        requested_goal: question,
        normalized_kind: 'observation_window',
      }])
    },
  }
  const provider = new FakeProvider()
  const runtime = new P1RuntimeV2SemanticTurnService(
    provider,
    new P1RuntimeV2SingleTurnService(provider),
    injected,
  )
  const answer = await runtime.answer(principal, request(question))
  assert.equal(answer.answerability, 'supported')
  assert.match(answer.answer_text, /观测窗口为/)
  assert.equal(answer.semantic_plan.grounding_plan.validation.status, 'passed')
})

test('冻结 runtime prompt 回归 24 案全部通过同一 UserGoalPlan 与 GroundingPlan 机器门', async () => {
  const repositoryRoot = resolve(
    dirname(fileURLToPath(import.meta.url)),
    '../../..',
  )
  const evaluationRoot = resolve(
    repositoryRoot,
    'evaluation/country-outage/p1-runtime-v2',
  )
  const variants = JSON.parse(readFileSync(
    resolve(evaluationRoot, 's2-semantic-variants.json'),
    'utf8',
  )) as any
  const candidate = JSON.parse(readFileSync(
    resolve(
      evaluationRoot,
      's2-codex-cli-runtime-prompt-v2.json',
    ),
    'utf8',
  )) as any
  const candidates = new Map(
    candidate.results.map((item: any) => [item.case_id, item]),
  )
  const grounder = new P1RuntimeV2Grounder()
  let legal = 0
  for (const variant of variants.cases) {
    const candidateCase = candidates.get(variant.case_id) as any
    assert.ok(candidateCase, `缺少 ${variant.case_id}`)
    const model = new FakeRawModel(JSON.stringify(candidateCase.user_goal_plan))
    const planner = new P1ModelUserGoalPlanner(model)
    const userPlan = await planner.plan(
      variant.question,
      {
        event_type: 'country_outage',
        country_code: 'IR',
        event_reference: eventReference,
        has_dialog_state: false,
      },
    )
    assert.deepEqual(userPlan.state_proposal.inherit, [])
    assert.deepEqual(userPlan.state_proposal.set, {})
    assert.deepEqual(userPlan.state_proposal.clear, [])
    const semantic = grounder.ground(userPlan, binding(), eventReference)
    assert.equal(semantic.grounding_plan.validation.status, 'passed')
    for (const decision of semantic.grounding_plan.decisions) {
      if (
        decision.answerability === 'unsupported'
        || decision.answerability === 'clarify'
        || decision.answerability === 'invalid_data'
      ) assert.equal(decision.node_ids.length, 0)
    }
    const replay = service(new FakeRawModel(JSON.stringify(
      candidateCase.user_goal_plan,
    )))
    const answer = await replay.service.answer(
      principal,
      request(variant.question),
    )
    const expectedOverviewCalls = semantic.grounding_plan.nodes.filter(
      (node) => node.execution_unit === 'TOOL-02',
    ).length
    assert.equal(replay.provider.overviewCalls, expectedOverviewCalls)
    assert.deepEqual(
      answer.execution_trace.nodes.map((node) => node.node_id),
      semantic.grounding_plan.nodes.map((node) => node.node_id),
    )
    const resultRefsByGoal = new Map(answer.results.map((result) => [
      result.goal_id,
      new Set(result.evidence_refs),
    ]))
    const plannedNodeById = new Map(
      semantic.grounding_plan.nodes.map((node) => [node.node_id, node]),
    )
    for (const receipt of answer.execution_trace.nodes) {
      const plannedNode = plannedNodeById.get(receipt.node_id)
      assert.ok(plannedNode)
      assert.ok(receipt.evidence_refs.length > 0)
      const resultRefs = resultRefsByGoal.get(plannedNode.goal_id)
      assert.ok(resultRefs)
      assert.ok(receipt.evidence_refs.every((ref) => resultRefs.has(ref)))
    }
    legal += 1
  }
  assert.equal(legal, 24)
})
