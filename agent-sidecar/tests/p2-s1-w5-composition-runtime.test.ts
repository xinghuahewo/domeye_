import assert from 'node:assert/strict'
import { mkdtempSync, rmSync } from 'node:fs'
import { createServer } from 'node:http'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import test from 'node:test'

import {
  P2S1W5ContractError,
  P2S1_W5_EXECUTION_ORDER,
  P2S1_W5_FROZEN_STUDENT_IDENTITY,
  p2S1W5Digest,
  p2S1W5Identity,
  type P2S1Json,
  type P2S1W5DegradedAuthorization,
  type P2S1W5SharedAnswerBinding,
  type P2S1W5StudentAnswerPayload,
  type P2S1W5TrustedReplayFixture,
} from '../src/chat/p2-s1-composition-contracts.js'
import { P2S1W5CompositionRuntime } from '../src/chat/p2-s1-composition-runtime.js'
import { P2S1W5ArtifactStore } from '../src/chat/p2-s1-dual-artifact-store.js'
import {
  InMemoryP2S1W5TrustedFixtureCatalog,
  ReplayOnlyP2S1W5ModelPort,
} from '../src/chat/p2-s1-model-runner.js'
import {
  materializeP2S1W5QuestionOracle,
} from '../src/chat/p2-s1-oracle-materializer.js'
import {
  createP2S1W5CommittedEvidenceGraphRecord,
  createP2S1W5GroundingPlanRecord,
} from '../src/chat/p2-s1-teacher-plan-grounder.js'
import { createP2S1W5HttpHandler } from '../src/server/p2-s1-w5-http-handler.js'
import { P2S1W5IntegratedAnswerRuntime } from '../src/chat/p2-s1-integrated-answer-runtime.js'

type Scenario =
  | 'revision_success'
  | 'first_success'
  | 'planning_unavailable'
  | 'teacher_unavailable'
  | 'teacher_gate_rejected'
  | 'teacher_oracle_rejected'
  | 'student_rejected'
  | 'alignment_rejected'
  | 'tool_smuggling'
  | 'typed_claim_attack'
  | 'degraded_success'

const TEACHER_IDENTITY = p2S1W5Identity({
  provider: 'openai',
  model: 'gpt-5.6-sol',
  version: 'gpt-5.6-sol-fixture-replay-v1',
  expected_response_model: 'gpt-5.6-sol',
})

function fixture(scenario: Scenario): P2S1W5TrustedReplayFixture {
  const question = '什么时候最严重，证据边界是什么？'
  const goal = '组合调查 RRC25 控制面事实'
  const questionId = 'Q03'
  const oracleSeed = {
    question_id: questionId,
    required_fact_ids: ['fact:ipv4-low'],
    required_boundary_assertions: ['仅为RRC25控制面观察'],
    allowed_boundary_assertions: [] as string[],
    required_unknowns: ['恢复状态未知'],
    prohibited_assertions: ['已经恢复'],
  }
  const oracle = materializeP2S1W5QuestionOracle(oracleSeed)
  const requiredBoundary = oracle.required_boundary_assertion_ids[0]!
  const requiredUnknown = oracle.required_unknown_ids[0]!
  const extraBoundary = 'boundary:graph-only-not-oracle'
  const degraded = scenario === 'degraded_success'
  let authorization: P2S1W5DegradedAuthorization | null = null
  if (degraded) {
    const withoutDigest = {
      authorization_id: 'degraded-auth-1',
      user_confirmed: true as const,
      mode: 'ds_unaligned_degraded' as const,
      parent_plan_revision: 1,
      new_plan_revision: 2,
      may_claim_sol_ds_alignment: false as const,
    }
    authorization = { ...withoutDigest, authorization_digest: p2S1W5Digest(withoutDigest) }
  }
  const plan = createP2S1W5GroundingPlanRecord({
    plan_id: 'plan-q03',
    plan_revision: degraded ? 2 : 1,
    admitted_capability_ids: ['CAP-P2-003'],
    registry_snapshot_id: 'registry-snapshot-w5-fixture',
    registry_snapshot_digest: p2S1W5Digest('registry-snapshot-w5-fixture'),
    effective_teacher_required: !degraded,
    degraded_authorization_digest: authorization?.authorization_digest ?? null,
  })
  const graph = createP2S1W5CommittedEvidenceGraphRecord({
    graph_id: 'graph-q03',
    graph_revision: degraded ? 2 : 1,
    investigation_plan_digest: plan.investigation_plan_digest,
    registry_snapshot_id: plan.registry_snapshot_id,
    registry_snapshot_digest: plan.registry_snapshot_digest,
    facts: [{
      fact_id: 'fact:ipv4-low',
      source_node_id: 'node:ipv4-low',
      source_value_digest: p2S1W5Digest({ value: 9_577_728 }),
      evidence_refs: ['evidence:series:ipv4-low'],
    }],
    boundary_assertion_ids: [requiredBoundary, extraBoundary],
    unknown_ids: [requiredUnknown],
  })
  const bindingBase = {
    question_id: questionId,
    question,
    question_digest: p2S1W5Digest(question),
    goal,
    goal_digest: p2S1W5Digest(goal),
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
  }
  const semanticPlan = {
    plan_id: 'plan-q03',
    question_id: questionId,
    question_digest: bindingBase.question_digest,
    goal_digest: bindingBase.goal_digest,
    subgoals: [{
      subgoal_id: 'subgoal-1',
      capability_id: scenario === 'tool_smuggling' ? 'TOOL-03' : 'CAP-P2-003',
    }],
    ambiguity_ids: [],
    tool_selection_authority: false,
    executable_plan: false,
    output_digest: '$W5_RECOMPUTE_OUTPUT_DIGEST',
  }
  const teacherReference = {
    teacher_reference_id: 'teacher-reference-q03',
    shared_answer_binding_digest: '$W5_SHARED_ANSWER_BINDING_DIGEST',
    required_fact_ids: ['fact:ipv4-low'],
    evidence_refs: [scenario === 'teacher_gate_rejected' ? 'evidence:ghost' : 'evidence:series:ipv4-low'],
    boundary_assertions: [requiredBoundary],
    unknowns: scenario === 'teacher_oracle_rejected' ? [] : [requiredUnknown],
    answer_outline: ['报告 IPv4 低点，并保持恢复状态未知'],
    teacher_reference_is_ground_truth: false,
    private_chain_of_thought_persisted: false,
    output_digest: '$W5_RECOMPUTE_OUTPUT_DIGEST',
  }
  const answer = (valid: boolean, extra = false): P2S1W5StudentAnswerPayload => ({
    claims: [{
      claim_id: 'claim:ipv4-low',
      claim_kind: scenario === 'typed_claim_attack' ? 'knowledge_explanation' : 'observed_fact',
      claim_relation: scenario === 'typed_claim_attack' ? 'explains_knowledge' : 'states_observed_fact',
      text: 'RRC25 在该窗口观察到 IPv4 可见地址低点。',
      fact_ids: ['fact:ipv4-low'],
      source_node_ids: ['node:ipv4-low'],
      source_value_digests: [p2S1W5Digest({ value: 9_577_728 })],
      evidence_refs: ['evidence:series:ipv4-low'],
      boundary_assertion_ids: extra ? [requiredBoundary, extraBoundary] : [requiredBoundary],
      verification_requirements: [],
    }],
    evidence_refs: ['evidence:series:ipv4-low'],
    limitations: ['仅覆盖 RRC25 控制面观测。'],
    unknowns: valid ? [requiredUnknown] : [],
    answer_text: 'RRC25 控制面证据显示该窗口存在 IPv4 低点；恢复状态仍未知。',
  })
  const firstValid = ['first_success', 'alignment_rejected', 'degraded_success', 'typed_claim_attack'].includes(scenario)
  const scriptedOutputs: Partial<Record<'sol_planning' | 'sol_reference' | 'ds_first_answer' | 'ds_revision', P2S1Json>> = {
    sol_planning: semanticPlan as unknown as P2S1Json,
    sol_reference: teacherReference as unknown as P2S1Json,
    ds_first_answer: answer(firstValid, scenario === 'alignment_rejected') as unknown as P2S1Json,
  }
  if (scenario === 'revision_success' || scenario === 'first_success') {
    scriptedOutputs.ds_revision = answer(true) as unknown as P2S1Json
  }
  const unavailable = scenario === 'planning_unavailable'
    ? ['sol_planning' as const]
    : scenario === 'teacher_unavailable' ? ['sol_reference' as const] : []
  let degradedBinding: P2S1W5SharedAnswerBinding | null = null
  if (degraded && authorization) {
    degradedBinding = {
      question_id: bindingBase.question_id,
      question_digest: bindingBase.question_digest,
      goal_digest: bindingBase.goal_digest,
      incident_id: bindingBase.incident_id,
      publication_id: bindingBase.publication_id,
      publication_revision: bindingBase.publication_revision,
      publication_digest: bindingBase.publication_digest,
      collector_id: 'rrc25',
      cohort_id: bindingBase.cohort_id,
      cohort_digest: bindingBase.cohort_digest,
      window_start_utc: bindingBase.window_start_utc,
      window_end_utc: bindingBase.window_end_utc,
      data_through_utc: bindingBase.data_through_utc,
      finality: bindingBase.finality,
      binding_generation: 2,
      teacher_semantic_plan_digest: p2S1W5Digest('degraded-no-teacher-plan'),
      teacher_plan_grounding_receipt_digest: p2S1W5Digest(authorization.authorization_digest),
      grounding_plan_digest: plan.grounding_plan_digest,
      plan_id: plan.plan_id,
      plan_revision: plan.plan_revision,
      investigation_plan_digest: plan.investigation_plan_digest,
      evidence_bundle_digest: graph.evidence_bundle_digest,
      evidence_graph_revision: graph.graph_revision,
      evidence_graph_digest: graph.evidence_graph_digest,
      registry_snapshot_id: graph.registry_snapshot_id,
      registry_snapshot_digest: graph.registry_snapshot_digest,
      boundary_policy_digest: bindingBase.boundary_policy_digest,
      world_knowledge_bundle_digest: null,
      world_knowledge_policy: 'explanation_and_hypothesis_only_not_event_evidence',
      prompt_version: bindingBase.prompt_version,
      prompt_digest: bindingBase.prompt_digest,
      policy_version: bindingBase.policy_version,
      policy_digest: bindingBase.policy_digest,
    }
  }
  const withoutDigest = {
    fixture_id: `fixture-${scenario}`,
    binding: bindingBase,
    teacher_identity: TEACHER_IDENTITY,
    student_identity: P2S1_W5_FROZEN_STUDENT_IDENTITY,
    allowed_capability_ids: ['CAP-P2-003'],
    grounding_plan: plan,
    evidence_graph: graph,
    oracle_seed: oracleSeed,
    scripted_outputs: scriptedOutputs,
    unavailable_phases: unavailable,
    force_alignment_rejection: false,
    degraded_authorization: authorization,
    degraded_binding: degradedBinding,
  }
  return {
    ...withoutDigest,
    fixture_digest: p2S1W5Digest(withoutDigest),
  }
}

function harness(scenario: Scenario) {
  const root = mkdtempSync(join(tmpdir(), 'domeye-p2-s1-w5-'))
  const selected = fixture(scenario)
  const catalog = new InMemoryP2S1W5TrustedFixtureCatalog([selected])
  const store = new P2S1W5ArtifactStore(root)
  const runtime = new P2S1W5CompositionRuntime({
    fixtures: catalog,
    modelPort: new ReplayOnlyP2S1W5ModelPort(catalog),
    artifactStore: store,
  })
  return {
    selected,
    catalog,
    store,
    runtime,
    close: () => rmSync(root, { recursive: true, force: true }),
  }
}

test('W5 按 Sol planning→Host→Sol reference→DS first/revision 顺序发布，revision 最多一次', async () => {
  const value = harness('revision_success')
  try {
    const result = await value.runtime.run({ fixture_id: value.selected.fixture_id, idempotency_key: 'revision-success' })
    assert.equal(result.flow?.final_disposition, 'aligned_published')
    assert.equal(result.flow?.student_runs.length, 2)
    assert.equal(result.flow?.structured_feedback?.feedback_round, 1)
    assert.deepEqual(result.model_call_summary.phase_attempt_counts, {
      sol_planning: 1,
      sol_reference: 1,
      ds_first_answer: 1,
      ds_revision: 1,
    })
    assert.equal(result.model_call_summary.external_provider_called, false)
    assert.equal(result.flow?.design_boundary.model_calls_implemented, false)
  } finally { value.close() }
})

test('DS first 已通过时即使 fixture 含 revision 也不得调用第二次', async () => {
  const value = harness('first_success')
  try {
    const result = await value.runtime.run({ fixture_id: value.selected.fixture_id, idempotency_key: 'first-success' })
    assert.equal(result.flow?.student_runs.length, 1)
    assert.equal(result.flow?.structured_feedback, null)
    assert.equal(result.model_call_summary.phase_attempt_counts.ds_revision, 0)
  } finally { value.close() }
})

test('planning unavailable 不创建 Dual flow；reference unavailable 闭合 teacher_unavailable', async () => {
  const planning = harness('planning_unavailable')
  try {
    const result = await planning.runtime.run({ fixture_id: planning.selected.fixture_id, idempotency_key: 'planning-down' })
    assert.equal(result.flow, null)
    assert.equal(result.planning_failure_receipt?.run_phase, 'sol_planning')
    assert.equal(result.model_call_summary.total_attempts, 1)
  } finally { planning.close() }
  const reference = harness('teacher_unavailable')
  try {
    const result = await reference.runtime.run({ fixture_id: reference.selected.fixture_id, idempotency_key: 'reference-down' })
    assert.equal(result.flow?.final_disposition, 'teacher_unavailable')
    assert.equal(result.flow?.teacher_unavailable_phase, 'sol_reference')
    assert.equal(result.flow?.student_runs.length, 0)
  } finally { reference.close() }
})

test('Teacher Gate 拒绝不生成 Oracle receipt；Oracle coverage 拒绝必须先通过五 Gate', async () => {
  const gate = harness('teacher_gate_rejected')
  try {
    const result = await gate.runtime.run({ fixture_id: gate.selected.fixture_id, idempotency_key: 'teacher-gate' })
    assert.equal(result.flow?.final_disposition, 'teacher_rejected')
    assert.equal(result.flow?.teacher_validation_receipt?.all_gates_passed, false)
    assert.equal(result.flow?.teacher_oracle_coverage_receipt, null)
  } finally { gate.close() }
  const oracle = harness('teacher_oracle_rejected')
  try {
    const result = await oracle.runtime.run({ fixture_id: oracle.selected.fixture_id, idempotency_key: 'teacher-oracle' })
    assert.equal(result.flow?.teacher_validation_receipt?.all_gates_passed, true)
    assert.equal(result.flow?.teacher_oracle_coverage_receipt?.disposition, 'rejected')
  } finally { oracle.close() }
})

test('Student Gate 和 Alignment 各自形成穷尽拒绝终态', async () => {
  const student = harness('student_rejected')
  try {
    const result = await student.runtime.run({ fixture_id: student.selected.fixture_id, idempotency_key: 'student-rejected' })
    assert.equal(result.flow?.final_disposition, 'student_rejected')
    assert.equal(result.flow?.flow_state, 'failed')
    assert.equal(result.flow?.alignment_run_receipt, null)
  } finally { student.close() }
  const alignment = harness('alignment_rejected')
  try {
    const result = await alignment.runtime.run({ fixture_id: alignment.selected.fixture_id, idempotency_key: 'alignment-rejected' })
    assert.equal(result.flow?.final_disposition, 'alignment_rejected')
    assert.equal(result.flow?.student_validation_receipt?.all_gates_passed, true)
    assert.equal(result.flow?.alignment_run_receipt?.hard_gate_metrics.boundary_compliance, 0)
  } finally { alignment.close() }
})

test('显式降级必须绑定受信授权、新 plan revision 与新 EvidenceGraph，且不得声称 aligned', async () => {
  const value = harness('degraded_success')
  try {
    await assert.rejects(
      value.runtime.run({ fixture_id: value.selected.fixture_id, idempotency_key: 'silent-degrade', degraded_authorization_id: 'ghost' }),
      (error: unknown) => error instanceof P2S1W5ContractError && error.code === 'degraded_authorization_denied',
    )
    const result = await value.runtime.run({
      fixture_id: value.selected.fixture_id,
      idempotency_key: 'explicit-degrade',
      degraded_authorization_id: 'degraded-auth-1',
    })
    assert.equal(result.flow?.final_disposition, 'ds_unaligned_degraded')
    assert.equal(result.flow?.published_answer?.aligned_claim, false)
    assert.equal(result.flow?.teacher_run_receipt, null)
    assert.deepEqual(result.model_call_summary.phase_attempt_counts, {
      sol_planning: 0,
      sol_reference: 0,
      ds_first_answer: 1,
      ds_revision: 0,
    })
  } finally { value.close() }
})

test('攻击：Teacher 不得夹带 Tool ID；typed claim 不得用知识解释冒充事件事实', async () => {
  const smuggling = harness('tool_smuggling')
  try {
    await assert.rejects(
      smuggling.runtime.run({ fixture_id: smuggling.selected.fixture_id, idempotency_key: 'smuggle' }),
      (error: unknown) => error instanceof P2S1W5ContractError && error.code === 'teacher_plan_unit_smuggling',
    )
  } finally { smuggling.close() }
  const typed = harness('typed_claim_attack')
  try {
    await assert.rejects(
      typed.runtime.run({ fixture_id: typed.selected.fixture_id, idempotency_key: 'typed-attack' }),
      (error: unknown) => error instanceof P2S1W5ContractError && error.code === 'typed_claim_invalid',
    )
  } finally { typed.close() }
})

test('幂等键固定请求，冲突请求失败关闭', async () => {
  const value = harness('first_success')
  try {
    const first = await value.runtime.run({ fixture_id: value.selected.fixture_id, idempotency_key: 'same-key' })
    const replay = await value.runtime.run({ fixture_id: value.selected.fixture_id, idempotency_key: 'same-key' })
    assert.equal(replay.flow?.flow_id, first.flow?.flow_id)
    await assert.rejects(
      value.runtime.run({
        fixture_id: value.selected.fixture_id,
        idempotency_key: 'same-key',
        degraded_authorization_id: 'different-request',
      }),
      (error: unknown) => error instanceof P2S1W5ContractError && error.code === 'idempotency_conflict',
    )
  } finally { value.close() }
})

test('独立 HTTP 只接受 fixture ID，不接受调用方自报模型、计划、证据或 Oracle', async () => {
  const value = harness('first_success')
  const token = 'w5-test-token-at-least-24-characters'
  const server = createServer(createP2S1W5HttpHandler({ runtime: value.runtime, sharedToken: token }))
  try {
    await new Promise<void>((resolvePromise) => server.listen(0, '127.0.0.1', resolvePromise))
    const address = server.address()
    assert.ok(address && typeof address === 'object')
    const base = `http://127.0.0.1:${address.port}/country-outage/p2-s1-w5`
    const ready = await fetch(`${base}/readyz`, { headers: { Authorization: `Bearer ${token}` } })
    assert.equal(ready.status, 200)
    const readiness = await ready.json() as Record<string, unknown>
    assert.equal(readiness.external_provider_enabled, false)
    const attack = await fetch(`${base}/runs`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({
        fixture_id: value.selected.fixture_id,
        idempotency_key: 'http-attack',
        evidence_graph: { caller_self_attested: true },
      }),
    })
    assert.equal(attack.status, 400)
    const accepted = await fetch(`${base}/runs`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({ fixture_id: value.selected.fixture_id, idempotency_key: 'http-ok' }),
    })
    assert.equal(accepted.status, 200)
  } finally {
    await new Promise<void>((resolvePromise) => server.close(() => resolvePromise()))
    value.close()
  }
})

test('integrated answer HTTP 实际执行 CompositionRuntime 全阶段并绑定 Host Graph/Oracle', async () => {
  const value = harness('first_success')
  const token = 'w5-integrated-token-at-least-24-chars'
  const integratedRoot = mkdtempSync(join(tmpdir(), 'domeye-p2-s1-w5-integrated-'))
  const integrated = new P2S1W5IntegratedAnswerRuntime(integratedRoot)
  const server = createServer(createP2S1W5HttpHandler({
    runtime: value.runtime, sharedToken: token,
    integratedAnswerRuntimeEnabled: true, integratedAnswerRuntime: integrated,
  }))
  try {
    await new Promise<void>((resolvePromise) => server.listen(0, '127.0.0.1', resolvePromise))
    const address = server.address()
    assert.ok(address && typeof address === 'object')
    const base = `http://127.0.0.1:${address.port}/country-outage/p2-s1-w5`
    const selected = value.selected
    const integratedOracleSeed = { ...selected.oracle_seed, question_id: 'Q01' }
    const materialized = materializeP2S1W5QuestionOracle(integratedOracleSeed)
    const oracleBase = {
      schema_version: 'country_outage_p2_s1_w5_host_answer_oracle_v1',
      boundary_texts: integratedOracleSeed.required_boundary_assertions,
      boundary_assertions: materialized.required_boundary_assertion_ids,
      limitations: ['仅覆盖绑定 publication、RRC25 collector 与已提交调查图。'],
      unknown_texts: integratedOracleSeed.required_unknowns,
      unknowns: materialized.required_unknown_ids,
      prohibited_claim_patterns: ['customer_cone', 'commercial_relationship', 'causality'],
    }
    const oracle = { ...oracleBase, oracle_digest: `sha256:${p2S1W5Digest(oracleBase)}` }
    const planRef = { plan_id: selected.grounding_plan.plan_id, plan_revision: 1, plan_digest: p2S1W5Digest(selected.grounding_plan) }
    const graphRef = { graph_id: selected.evidence_graph.graph_id, graph_revision: 1, graph_digest: selected.evidence_graph.evidence_graph_digest }
    const resultSetRefs: never[] = []
    const anchor = { node_id: selected.evidence_graph.facts[0]!.source_node_id, node_revision: 1 }
    const sharedBase = {
      investigation_id: 'inv_integrated', source_investigation_revision: 3,
      source_current_digest: `sha256:${'a'.repeat(64)}`, identity_digest: p2S1W5Digest('identity'),
      plan_ref: planRef, result_set_refs: resultSetRefs, evidence_graph_ref: graphRef,
      host_oracle_digest: oracle.oracle_digest, question_digest: `sha256:${p2S1W5Digest(selected.binding.question)}`,
      anchor, registry_snapshot_id: selected.grounding_plan.registry_snapshot_id,
      registry_snapshot_digest: `sha256:${selected.grounding_plan.registry_snapshot_digest}`,
      binding_generation: 1,
    }
    const sharedDigest = `sha256:${p2S1W5Digest(sharedBase)}`
    const hostGraphFacts = selected.evidence_graph.facts.map((fact) => ({
      ...fact,
      claim_kind: 'observed_fact',
      claim_relation: 'states_observed_fact',
      allowed_claim_text: `RRC25 当前绑定调查图已提交观测事实 ${fact.fact_id}（结构化值摘要 ${fact.source_value_digest}）。`,
    }))
    const request = {
      schema_version: 'country_outage_p2_s1_w5_model_turn_request_v1', investigation_id: 'inv_integrated',
      source_investigation_revision: 3, source_current_digest: sharedBase.source_current_digest,
      identity: {
        incident_id: selected.binding.incident_id, publication_id: selected.binding.publication_id,
        publication_revision: selected.binding.publication_revision, publication_digest: selected.binding.publication_digest,
        collector_id: 'rrc25', cohort_id: selected.binding.cohort_id, cohort_digest: selected.binding.cohort_digest,
        window_start_utc: selected.binding.window_start_utc, window_end_utc: selected.binding.window_end_utc,
        data_through_utc: selected.binding.data_through_utc, finality: selected.binding.finality,
        binding_generation: 1, registry_snapshot_id: selected.grounding_plan.registry_snapshot_id,
        registry_snapshot_digest: `sha256:${selected.grounding_plan.registry_snapshot_digest}`, identity_digest: sharedBase.identity_digest,
      },
      question: selected.binding.question, question_digest: sharedBase.question_digest, anchor,
      plan_ref: planRef, result_set_refs: resultSetRefs, evidence_graph_ref: graphRef,
      evidence_refs: selected.evidence_graph.facts[0]!.evidence_refs,
      host_graph_facts: hostGraphFacts, host_oracle: oracle,
      shared_answer_binding: sharedBase, shared_answer_binding_digest: sharedDigest,
    }
    const response = await fetch(`${base}/answer-turns`, {
      method: 'POST', headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({ schema_version: 'country_outage_p2_s1_w5_integrated_answer_request_v1', fixture_id: 'fixture-integrated-http', fixture_digest: `sha256:${'b'.repeat(64)}`, idempotency_key: 'integrated-http-001', request }),
    })
    const responseText = await response.text()
    assert.equal(response.status, 200, responseText)
    const payload = JSON.parse(responseText) as Record<string, any>
    assert.deepEqual(payload.execution_order, P2S1_W5_EXECUTION_ORDER)
    assert.equal(payload.runtime_integrated, true)
    assert.equal(payload.external_provider_called, false)
    assert.equal(payload.model_receipts.length, 3)
    assert.ok(payload.model_receipts.every((item: Record<string, unknown>) => 'source_composition_receipt_digest' in item))
    assert.equal(payload.gate_receipts.length, 4)
  } finally {
    await new Promise<void>((resolvePromise) => server.close(() => resolvePromise()))
    rmSync(integratedRoot, { recursive: true, force: true })
    value.close()
  }
})
