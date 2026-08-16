import assert from 'node:assert/strict'
import { mkdtempSync, rmSync } from 'node:fs'
import { createServer } from 'node:http'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import test from 'node:test'

import { createFormalP2S1W5Sidecar } from '../src/cli/formal-p2-s1-w5-sidecar.js'
import {
  P2S1W5ContractError,
  P2S1_W5_FROZEN_STUDENT_IDENTITY,
  p2S1W5Digest,
  p2S1W5DigestWithout,
  p2S1W5Identity,
  type P2S1Json,
  type P2S1W5TrustedReplayFixture,
} from '../src/chat/p2-s1-composition-contracts.js'
import type { P2S1W5CompositionRuntime } from '../src/chat/p2-s1-composition-runtime.js'
import { P2S1W5ArtifactStore } from '../src/chat/p2-s1-dual-artifact-store.js'
import {
  P2S1W5PlanningGroundingRuntime,
  p2S1W5PlanningBindingSummary,
  validateP2S1W5TrustedGroundingPlanProjection,
  validateP2S1W5PlanningGroundingClosure,
  type P2S1W5FrozenExecutionTemplate,
  type P2S1W5PlanningGroundingResult,
  type P2S1W5PlanningGroundingRequest,
} from '../src/chat/p2-s1-planning-grounding-port.js'
import {
  InMemoryP2S1W5TrustedFixtureCatalog,
  ReplayOnlyP2S1W5ModelPort,
  type P2S1W5InjectedModelPort,
  type P2S1W5ModelPortRequest,
  type P2S1W5ModelPortResult,
} from '../src/chat/p2-s1-model-runner.js'
import {
  createP2S1W5CommittedEvidenceGraphRecord,
  createP2S1W5GroundingPlanRecord,
} from '../src/chat/p2-s1-teacher-plan-grounder.js'
import { createP2S1W5HttpHandler } from '../src/server/p2-s1-w5-http-handler.js'

const TOKEN = 'w5-planning-test-token-24-characters'
const TEACHER_IDENTITY = p2S1W5Identity({
  provider: 'openai',
  model: 'gpt-5.6-sol',
  version: 'gpt-5.6-sol-fixture-replay-v1',
  expected_response_model: 'gpt-5.6-sol',
})

type PlanningFixture = P2S1W5TrustedReplayFixture & {
  frozen_execution_template: P2S1W5FrozenExecutionTemplate
}

function fixture(options: {
  unavailable?: boolean
  smuggleUnit?: boolean
  pageSize?: number
} = {}): PlanningFixture {
  const question = '什么时候最严重，证据边界是什么？'
  const goal = '组合调查 RRC25 控制面事实'
  const questionDigest = p2S1W5Digest(question)
  const goalDigest = p2S1W5Digest(goal)
  const plan = createP2S1W5GroundingPlanRecord({
    plan_id: 'plan-q03',
    plan_revision: 1,
    admitted_capability_ids: ['CAP-P2-003'],
    registry_snapshot_id: 'registry-snapshot-w5-fixture',
    registry_snapshot_digest: p2S1W5Digest('registry-snapshot-w5-fixture'),
    effective_teacher_required: true,
    degraded_authorization_digest: null,
  })
  const graph = createP2S1W5CommittedEvidenceGraphRecord({
    graph_id: 'graph-q03',
    graph_revision: 1,
    investigation_plan_digest: plan.investigation_plan_digest,
    registry_snapshot_id: plan.registry_snapshot_id,
    registry_snapshot_digest: plan.registry_snapshot_digest,
    facts: [{
      fact_id: 'fact:ipv4-low',
      source_node_id: 'node:ipv4-low',
      source_value_digest: p2S1W5Digest({ value: 9_577_728 }),
      evidence_refs: ['evidence:series:ipv4-low'],
    }],
    boundary_assertion_ids: ['boundary:rrc25-only'],
    unknown_ids: ['unknown:event-end'],
  })
  const semanticPlan = {
    plan_id: plan.plan_id,
    question_id: 'Q03',
    question_digest: questionDigest,
    goal_digest: goalDigest,
    subgoals: [{
      subgoal_id: 'subgoal-1',
      capability_id: options.smuggleUnit ? 'TOOL-03' : 'CAP-P2-003',
    }],
    ambiguity_ids: [],
    tool_selection_authority: false,
    executable_plan: false,
    output_digest: '$W5_RECOMPUTE_OUTPUT_DIGEST',
  }
  const fixtureId = options.smuggleUnit
    ? 'fixture-smuggling'
    : options.unavailable ? 'fixture-unavailable' : 'fixture-planning'
  const inputSource = {
    input_name: 'page_size',
    source_kind: 'trusted_fixture_parameter' as const,
    source_ref: `fixture:${fixtureId}:parameter:tool07:page_size`,
    source_digest: p2S1W5Digest({
      input_name: 'page_size',
      source_kind: 'trusted_fixture_parameter',
      source_ref: `fixture:${fixtureId}:parameter:tool07:page_size`,
      bound_parameter_value: options.pageSize ?? 1,
    }),
    source_artifact_digest: null,
  }
  const templateWithoutDigest = {
    schema_version: 'country_outage_p2_s1_w5_frozen_execution_template_v1' as const,
    template_group_id: 'template-group:CAP-P2-003:country-event-overview-v1',
    fixture_id: fixtureId,
    question_id: 'Q03',
    question_digest: questionDigest,
    goal_digest: goalDigest,
    semantic_capability_ids: ['CAP-P2-003'],
    plan_id: plan.plan_id,
    plan_revision: plan.plan_revision,
    registry_snapshot_id: plan.registry_snapshot_id,
    registry_snapshot_digest: plan.registry_snapshot_digest,
    nodes: [
      {
        node_id: 'gate01', depends_on: [], dependency_mode: 'hard' as const,
        requiredness: 'required' as const, unit_id: 'GATE-01',
        atomic_capability_id: 'validate.identity', parameters: {}, input_binding_sources: [],
      },
      {
        node_id: 'gate02', depends_on: [], dependency_mode: 'hard' as const,
        requiredness: 'required' as const, unit_id: 'GATE-02',
        atomic_capability_id: 'validate.evidence_refs', parameters: {}, input_binding_sources: [],
      },
      {
        node_id: 'gate03', depends_on: [], dependency_mode: 'hard' as const,
        requiredness: 'required' as const, unit_id: 'GATE-03',
        atomic_capability_id: 'validate.result_completeness', parameters: {}, input_binding_sources: [],
      },
      {
        node_id: 'tool07', depends_on: ['gate01', 'gate02', 'gate03'], dependency_mode: 'hard' as const,
        requiredness: 'required' as const, unit_id: 'TOOL-07',
        atomic_capability_id: 'read.fixed_cohort_members', parameters: { page_size: options.pageSize ?? 1 },
        input_binding_sources: [inputSource],
      },
      {
        node_id: 'boundary', depends_on: ['tool07'], dependency_mode: 'soft' as const,
        requiredness: 'boundary_only' as const, unit_id: 'BOUNDARY-01',
        atomic_capability_id: 'respond.boundary', parameters: {}, input_binding_sources: [],
      },
    ],
  }
  const frozenExecutionTemplate: P2S1W5FrozenExecutionTemplate = {
    ...templateWithoutDigest,
    template_digest: p2S1W5Digest(templateWithoutDigest),
  }
  const withoutDigest = {
    fixture_id: fixtureId,
    binding: {
      question_id: 'Q03',
      question,
      question_digest: questionDigest,
      goal,
      goal_digest: goalDigest,
      incident_id: 'incident-fixture-ir',
      publication_id: 'publication-fixture-ir-r1',
      publication_revision: 1,
      publication_digest: p2S1W5Digest('publication-fixture-ir-r1'),
      cohort_id: 'cohort-fixture-ir',
      cohort_digest: p2S1W5Digest('cohort-fixture-ir'),
      window_start_utc: '2026-02-27T00:10:00Z',
      window_end_utc: '2026-03-11T00:00:00Z',
      data_through_utc: '2026-03-11T00:00:00Z',
      finality: 'event_end_unknown' as const,
      binding_generation: 1,
      boundary_policy_digest: p2S1W5Digest('boundary-policy-v1'),
      prompt_version: 'p2-s1-w5-fixture-prompt-v1',
      prompt_digest: p2S1W5Digest('p2-s1-w5-fixture-prompt-v1'),
      policy_version: 'p2-s1-w5-policy-v1',
      policy_digest: p2S1W5Digest('p2-s1-w5-policy-v1'),
    },
    teacher_identity: TEACHER_IDENTITY,
    student_identity: P2S1_W5_FROZEN_STUDENT_IDENTITY,
    allowed_capability_ids: ['CAP-P2-003'],
    grounding_plan: plan,
    evidence_graph: graph,
    oracle_seed: {
      question_id: 'Q03',
      required_fact_ids: ['fact:ipv4-low'],
      required_boundary_assertions: ['仅为RRC25控制面观察'],
      allowed_boundary_assertions: [],
      required_unknowns: ['恢复状态未知'],
      prohibited_assertions: ['已经恢复'],
    },
    scripted_outputs: {
      sol_planning: semanticPlan as unknown as P2S1Json,
    },
    unavailable_phases: options.unavailable ? ['sol_planning' as const] : [],
    force_alignment_rejection: false,
    degraded_authorization: null,
    degraded_binding: null,
    frozen_execution_template: frozenExecutionTemplate,
  }
  return { ...withoutDigest, fixture_digest: p2S1W5Digest(withoutDigest) }
}

function redigestProjection(value: P2S1W5PlanningGroundingResult): void {
  const projection = value.trusted_grounding_plan_projection
  assert.ok(projection)
  projection.grounded_execution_recipe.recipe_digest = p2S1W5DigestWithout(
    projection.grounded_execution_recipe as unknown as Record<string, unknown>,
    'recipe_digest',
  )
  projection.grounding_plan_projection_digest = p2S1W5DigestWithout(
    projection as unknown as Record<string, unknown>,
    'grounding_plan_projection_digest',
  )
}

class CountingReplayPort implements P2S1W5InjectedModelPort {
  readonly mode = 'trusted_fixture_replay' as const
  readonly phases: string[] = []

  constructor(private readonly delegate: ReplayOnlyP2S1W5ModelPort) {}

  async complete(request: P2S1W5ModelPortRequest): Promise<P2S1W5ModelPortResult> {
    this.phases.push(request.phase)
    return this.delegate.complete(request)
  }
}

function requestFor(selected: P2S1W5TrustedReplayFixture, key: string): P2S1W5PlanningGroundingRequest {
  const summary = p2S1W5PlanningBindingSummary(selected)
  return {
    fixture_id: selected.fixture_id,
    goal: selected.binding.goal,
    goal_digest: selected.binding.goal_digest,
    binding_summary: structuredClone(summary),
    binding_summary_digest: p2S1W5Digest(summary),
    idempotency_key: key,
  }
}

function harness(selected = fixture()) {
  const catalog = new InMemoryP2S1W5TrustedFixtureCatalog([selected])
  const port = new CountingReplayPort(new ReplayOnlyP2S1W5ModelPort(catalog))
  const runtime = new P2S1W5PlanningGroundingRuntime({ fixtures: catalog, modelPort: port })
  return { selected, catalog, port, runtime }
}

test('成功：planning/grounding 只执行一次 Sol planning，返回受信 projection 并明确完整 Plan 归 Python Host', async () => {
  const value = harness()
  const result = await value.runtime.run(requestFor(value.selected, 'ground-ok'))
  assert.equal(result.disposition, 'grounded_projection')
  assert.deepEqual(value.port.phases, ['sol_planning'])
  assert.equal(result.semantic_plan_validation_receipt?.disposition, 'passed')
  assert.equal(result.host_grounding_receipt?.disposition, 'passed')
  assert.deepEqual(result.trusted_grounding_plan_projection?.admitted_capability_ids, ['CAP-P2-003'])
  const recipe = result.trusted_grounding_plan_projection?.grounded_execution_recipe
  assert.ok(recipe)
  assert.deepEqual(recipe.nodes.map((node) => [
    node.node_id, node.unit_id, node.atomic_capability_id, node.depends_on,
    node.dependency_mode, node.requiredness,
  ]), [
    ['gate01', 'GATE-01', 'validate.identity', [], 'hard', 'required'],
    ['gate02', 'GATE-02', 'validate.evidence_refs', [], 'hard', 'required'],
    ['gate03', 'GATE-03', 'validate.result_completeness', [], 'hard', 'required'],
    ['tool07', 'TOOL-07', 'read.fixed_cohort_members', ['gate01', 'gate02', 'gate03'], 'hard', 'required'],
    ['boundary', 'BOUNDARY-01', 'respond.boundary', ['tool07'], 'soft', 'boundary_only'],
  ])
  assert.deepEqual(recipe.nodes[3]?.parameters, { page_size: 1 })
  assert.equal(recipe.nodes[3]?.input_binding_sources[0]?.source_kind, 'trusted_fixture_parameter')
  assert.equal(result.host_grounding_receipt?.grounded_execution_recipe_digest, recipe.recipe_digest)
  assert.equal(
    result.host_grounding_receipt?.grounding_plan_projection_digest,
    result.trusted_grounding_plan_projection?.grounding_plan_projection_digest,
  )
  assert.equal(result.full_investigation_plan.status, 'host_runtime_required')
  assert.equal(result.full_investigation_plan.artifact_ref, null)
  assert.equal(result.full_investigation_plan.artifact_digest, null)
  assert.equal(result.full_investigation_plan.projection_is_full_plan, false)
  assert.equal(
    Object.hasOwn(result.trusted_grounding_plan_projection ?? {}, 'investigation_plan_digest'),
    false,
  )
  assert.equal(result.execution_boundary.dual_answer_flow_started, false)
  assert.equal(result.execution_boundary.sol_reference_attempt_count, 0)
  assert.equal(result.execution_boundary.student_attempt_count, 0)
})

test('缺冻结 execution template 的 fixture 在 Sol planning 前 typed fail-closed', async () => {
  const selected = structuredClone(fixture()) as unknown as P2S1W5TrustedReplayFixture & {
    frozen_execution_template?: P2S1W5FrozenExecutionTemplate
  }
  delete selected.frozen_execution_template
  selected.fixture_digest = p2S1W5DigestWithout(
    selected as unknown as Record<string, unknown>,
    'fixture_digest',
  )
  const catalog = new InMemoryP2S1W5TrustedFixtureCatalog([selected])
  const port = new CountingReplayPort(new ReplayOnlyP2S1W5ModelPort(catalog))
  const runtime = new P2S1W5PlanningGroundingRuntime({ fixtures: catalog, modelPort: port })
  await assert.rejects(
    runtime.run(requestFor(selected, 'missing-template')),
    (error: unknown) => error instanceof P2S1W5ContractError && error.code === 'planning_grounding_incomplete',
  )
  assert.deepEqual(port.phases, [])
})

test('projection A + recipe B 无法通过 projection/receipt 闭包', async () => {
  const left = harness(fixture({ pageSize: 1 }))
  const right = harness(fixture({ pageSize: 2 }))
  const leftResult = await left.runtime.run(requestFor(left.selected, 'splice-left'))
  const rightResult = await right.runtime.run(requestFor(right.selected, 'splice-right'))
  const splice = structuredClone(leftResult)
  assert.ok(splice.trusted_grounding_plan_projection)
  assert.ok(rightResult.trusted_grounding_plan_projection)
  splice.trusted_grounding_plan_projection.grounded_execution_recipe = structuredClone(
    rightResult.trusted_grounding_plan_projection.grounded_execution_recipe,
  )
  assert.throws(
    () => validateP2S1W5TrustedGroundingPlanProjection(splice.trusted_grounding_plan_projection),
    (error: unknown) => error instanceof P2S1W5ContractError && error.code === 'grounding_projection_digest_drift',
  )
  redigestProjection(splice)
  assert.throws(
    () => validateP2S1W5PlanningGroundingClosure(splice),
    (error: unknown) => error instanceof P2S1W5ContractError && error.code === 'planning_grounding_closure_drift',
  )
})

test('Plan、Registry、capability、node、parameter 与 binding source 漂移均失败关闭', async () => {
  const value = harness()
  const original = await value.runtime.run(requestFor(value.selected, 'drift-base'))
  const cases: Array<[string, (copy: P2S1W5PlanningGroundingResult) => void]> = [
    ['plan', (copy) => {
      copy.trusted_grounding_plan_projection!.plan_id = 'plan-ghost'
      copy.trusted_grounding_plan_projection!.grounded_execution_recipe.plan_id = 'plan-ghost'
    }],
    ['registry', (copy) => {
      copy.trusted_grounding_plan_projection!.registry_snapshot_id = 'registry-ghost'
      copy.trusted_grounding_plan_projection!.grounded_execution_recipe.registry_snapshot_id = 'registry-ghost'
    }],
    ['capability', (copy) => {
      copy.trusted_grounding_plan_projection!.admitted_capability_ids = ['CAP-P2-GHOST']
      copy.trusted_grounding_plan_projection!.grounded_execution_recipe.semantic_capability_ids = ['CAP-P2-GHOST']
    }],
    ['node', (copy) => {
      copy.trusted_grounding_plan_projection!.grounded_execution_recipe.nodes[4]!.node_id = 'boundary-drift'
    }],
    ['parameter', (copy) => {
      const node = copy.trusted_grounding_plan_projection!.grounded_execution_recipe.nodes[3]!
      node.parameters.page_size = 2
      const source = node.input_binding_sources[0]!
      source.source_digest = p2S1W5Digest({
        input_name: source.input_name,
        source_kind: source.source_kind,
        source_ref: source.source_ref,
        bound_parameter_value: 2,
      })
    }],
    ['binding-source', (copy) => {
      const node = copy.trusted_grounding_plan_projection!.grounded_execution_recipe.nodes[3]!
      const source = node.input_binding_sources[0]!
      source.source_ref = 'fixture:ghost:parameter:tool07:page_size'
      source.source_digest = p2S1W5Digest({
        input_name: source.input_name,
        source_kind: source.source_kind,
        source_ref: source.source_ref,
        bound_parameter_value: node.parameters.page_size,
      })
    }],
  ]
  for (const [label, mutate] of cases) {
    const copy = structuredClone(original)
    mutate(copy)
    redigestProjection(copy)
    assert.throws(
      () => validateP2S1W5PlanningGroundingClosure(copy),
      (error: unknown) => error instanceof P2S1W5ContractError
        && [
          'planning_grounding_closure_drift', 'grounding_projection_recipe_mismatch',
          'execution_recipe_template_digest_drift', 'execution_recipe_binding_source_drift',
        ].includes(error.code),
      label,
    )
  }
})

test('ghost unit 即使重算 recipe/projection 摘要也被冻结 unit 映射拒绝', async () => {
  const value = harness()
  const result = structuredClone(await value.runtime.run(requestFor(value.selected, 'ghost-unit-base')))
  const node = result.trusted_grounding_plan_projection!.grounded_execution_recipe.nodes[3]!
  node.unit_id = 'TOOL-99'
  node.atomic_capability_id = 'read.ghost'
  redigestProjection(result)
  assert.throws(
    () => validateP2S1W5PlanningGroundingClosure(result),
    (error: unknown) => error instanceof P2S1W5ContractError && error.code === 'execution_recipe_ghost_unit',
  )
})

test('缺完整 fixture：不得把 grounding projection 冒充完整 InvestigationPlan artifact', async () => {
  const value = harness()
  const result = await value.runtime.run(requestFor(value.selected, 'full-plan-missing'))
  assert.equal(result.disposition, 'grounded_projection')
  assert.deepEqual(result.full_investigation_plan, {
    status: 'host_runtime_required',
    required_schema_version: 'country_outage_p2_investigation_plan_v1',
    required_schema_sha256: '949b8dcb10a4c95ea6060789d174ca6c37277720724a67cf228f15be58ed5b07',
    artifact_ref: null,
    artifact_digest: null,
    projection_is_full_plan: false,
  })
})

test('goal、identity binding 与 fixture mismatch 在模型调用前失败关闭', async () => {
  const value = harness()
  const goalAttack = requestFor(value.selected, 'goal-attack')
  goalAttack.goal = '另一个目标'
  goalAttack.goal_digest = p2S1W5Digest(goalAttack.goal)
  await assert.rejects(
    value.runtime.run(goalAttack),
    (error: unknown) => error instanceof P2S1W5ContractError && error.code === 'goal_fixture_mismatch',
  )
  const identityAttack = requestFor(value.selected, 'identity-attack')
  identityAttack.binding_summary.incident_id = 'incident-ghost'
  identityAttack.binding_summary_digest = p2S1W5Digest(identityAttack.binding_summary)
  await assert.rejects(
    value.runtime.run(identityAttack),
    (error: unknown) => error instanceof P2S1W5ContractError && error.code === 'identity_binding_fixture_mismatch',
  )
  const fixtureAttack = requestFor(value.selected, 'fixture-attack')
  fixtureAttack.fixture_id = 'fixture-ghost'
  await assert.rejects(
    value.runtime.run(fixtureAttack),
    (error: unknown) => error instanceof P2S1W5ContractError && error.code === 'fixture_not_found',
  )
  assert.deepEqual(value.port.phases, [])
})

test('摘要漂移：goal、binding summary 与 fixture 自身摘要均失败关闭', async () => {
  const value = harness()
  const goalDigestAttack = requestFor(value.selected, 'goal-digest-attack')
  goalDigestAttack.goal_digest = p2S1W5Digest('错误目标')
  await assert.rejects(
    value.runtime.run(goalDigestAttack),
    (error: unknown) => error instanceof P2S1W5ContractError && error.code === 'goal_digest_mismatch',
  )
  const bindingDigestAttack = requestFor(value.selected, 'binding-digest-attack')
  bindingDigestAttack.binding_summary_digest = p2S1W5Digest('错误 binding')
  await assert.rejects(
    value.runtime.run(bindingDigestAttack),
    (error: unknown) => error instanceof P2S1W5ContractError && error.code === 'binding_summary_digest_mismatch',
  )
  const driftedFixture = structuredClone(value.selected)
  driftedFixture.binding.goal = '被篡改的 fixture 目标'
  assert.throws(
    () => new InMemoryP2S1W5TrustedFixtureCatalog([driftedFixture]),
    (error: unknown) => error instanceof P2S1W5ContractError && error.code === 'fixture_digest_mismatch',
  )
  assert.deepEqual(value.port.phases, [])
})

test('相同幂等请求只 replay 一次；冲突请求不触发第二次 planning', async () => {
  const value = harness()
  const request = requestFor(value.selected, 'same-key')
  const first = await value.runtime.run(request)
  const replay = await value.runtime.run(request)
  assert.equal(replay.host_grounding_receipt?.receipt_id, first.host_grounding_receipt?.receipt_id)
  const conflict = requestFor(value.selected, 'same-key')
  conflict.goal = '冲突目标'
  conflict.goal_digest = p2S1W5Digest(conflict.goal)
  await assert.rejects(
    value.runtime.run(conflict),
    (error: unknown) => error instanceof P2S1W5ContractError && error.code === 'idempotency_conflict',
  )
  assert.deepEqual(value.port.phases, ['sol_planning'])
})

test('planning unavailable 返回 typed fail-closed，且不运行 reference、DS 或完整 Dual flow', async () => {
  const value = harness(fixture({ unavailable: true }))
  const result = await value.runtime.run(requestFor(value.selected, 'planning-down'))
  assert.equal(result.disposition, 'planning_unavailable')
  assert.equal(result.teacher_semantic_plan, null)
  assert.equal(result.host_grounding_receipt, null)
  assert.equal(result.trusted_grounding_plan_projection, null)
  assert.equal(result.full_investigation_plan.status, 'host_runtime_required')
  assert.deepEqual(value.port.phases, ['sol_planning'])
  assert.equal(result.execution_boundary.dual_answer_flow_started, false)
})

test('TeacherSemanticPlan 夹带 execution unit 时在 Host validation 拒绝', async () => {
  const value = harness(fixture({ smuggleUnit: true }))
  await assert.rejects(
    value.runtime.run(requestFor(value.selected, 'smuggling')),
    (error: unknown) => error instanceof P2S1W5ContractError && error.code === 'teacher_plan_unit_smuggling',
  )
  assert.deepEqual(value.port.phases, ['sol_planning'])
})

test('外部模型端口在装配阶段即被拒绝，不能发起任何 provider 调用', () => {
  const selected = fixture()
  const catalog = new InMemoryP2S1W5TrustedFixtureCatalog([selected])
  let externalCallCount = 0
  const externalPort = {
    mode: 'external_provider',
    complete: async () => {
      externalCallCount += 1
      throw new Error('不得调用')
    },
  } as unknown as P2S1W5InjectedModelPort
  assert.throws(
    () => new P2S1W5PlanningGroundingRuntime({ fixtures: catalog, modelPort: externalPort }),
    (error: unknown) => error instanceof P2S1W5ContractError && error.code === 'external_provider_forbidden',
  )
  assert.equal(externalCallCount, 0)
})

test('HTTP 合同只接收 fixture、goal、binding summary 和幂等键；不得启动 /runs', async () => {
  const value = harness()
  let dualRunCount = 0
  const compositionRuntime = {
    run: async () => {
      dualRunCount += 1
      throw new Error('planning endpoint 不得启动完整 Dual flow')
    },
  } as unknown as P2S1W5CompositionRuntime
  const server = createServer(createP2S1W5HttpHandler({
    runtime: compositionRuntime,
    planningGroundingRuntime: value.runtime,
    sharedToken: TOKEN,
  }))
  try {
    await new Promise<void>((resolvePromise) => server.listen(0, '127.0.0.1', resolvePromise))
    const address = server.address()
    assert.ok(address && typeof address === 'object')
    const url = `http://127.0.0.1:${address.port}/country-outage/p2-s1-w5/planning-groundings`
    const accepted = await fetch(url, {
      method: 'POST',
      headers: { Authorization: `Bearer ${TOKEN}`, 'Content-Type': 'application/json' },
      body: JSON.stringify(requestFor(value.selected, 'http-ok')),
    })
    assert.equal(accepted.status, 200)
    const result = await accepted.json() as Record<string, unknown>
    assert.equal(result.disposition, 'grounded_projection')
    const attack = await fetch(url, {
      method: 'POST',
      headers: { Authorization: `Bearer ${TOKEN}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({
        ...requestFor(value.selected, 'http-attack'),
        full_investigation_plan: { caller_self_attested: true },
        nodes: [{ node_id: 'caller-node', unit_id: 'TOOL-99' }],
      }),
    })
    assert.equal(attack.status, 400)
    const attackBody = await attack.json() as { error: { code: string } }
    assert.equal(attackBody.error.code, 'invalid_request_fields')
    assert.equal(dualRunCount, 0)
    assert.deepEqual(value.port.phases, ['sol_planning'])
  } finally {
    await new Promise<void>((resolvePromise) => server.close(() => resolvePromise()))
  }
})

test('CLI 正式 W5 Sidecar 默认装配独立 planning/grounding runtime，仍保持本地 fixture 边界', () => {
  const selected = fixture()
  const catalog = new InMemoryP2S1W5TrustedFixtureCatalog([selected])
  const port = new ReplayOnlyP2S1W5ModelPort(catalog)
  const root = mkdtempSync(join(tmpdir(), 'domeye-w5-planning-cli-'))
  try {
    const sidecar = createFormalP2S1W5Sidecar({
      COUNTRY_OUTAGE_P2_S1_W5_SHARED_TOKEN: TOKEN,
      COUNTRY_OUTAGE_P2_S1_W5_HOST: '127.0.0.1',
      COUNTRY_OUTAGE_P2_S1_W5_PORT: '28485',
    }, {
      fixtureCatalog: catalog,
      modelPort: port,
      artifactStore: new P2S1W5ArtifactStore(root),
    })
    assert.ok(sidecar.planningGroundingRuntime instanceof P2S1W5PlanningGroundingRuntime)
    assert.equal(sidecar.execution.mode, 'trusted_fixture_replay_only')
    assert.equal(sidecar.execution.externalProviderEnabled, false)
    assert.equal(sidecar.execution.productionHandlerIntegrated, false)
    assert.equal(sidecar.execution.productionDeployed, false)
    sidecar.server.close()
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})
