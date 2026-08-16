import { randomUUID } from 'node:crypto'

import {
  P2S1W5ContractError,
  p2S1W5AssertDigest,
  p2S1W5AssertNonempty,
  p2S1W5AssertUnique,
  p2S1W5Clone,
  p2S1W5DeepFreeze,
  p2S1W5Digest,
  p2S1W5DigestWithout,
  type P2S1TeacherSemanticPlan,
  type P2S1W5CommittedEvidenceGraphRecord,
  type P2S1W5GroundingPlanRecord,
  type P2S1W5TeacherPlanGroundingReceipt,
} from './p2-s1-composition-contracts.js'

function objectValue(value: unknown, label: string): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new P2S1W5ContractError('teacher_plan_invalid', `${label} 必须是对象`)
  }
  return value as Record<string, unknown>
}

function exactKeys(value: Record<string, unknown>, expected: readonly string[], label: string): void {
  const actual = Object.keys(value).sort()
  const wanted = [...expected].sort()
  if (actual.length !== wanted.length || actual.some((key, index) => key !== wanted[index])) {
    throw new P2S1W5ContractError('teacher_plan_invalid', `${label} 字段集合不符合冻结语义计划合同`)
  }
}

export function createP2S1W5GroundingPlanRecord(input: Omit<
  P2S1W5GroundingPlanRecord,
  'grounding_plan_digest' | 'investigation_plan_digest'
>): P2S1W5GroundingPlanRecord {
  const definition = p2S1W5Clone(input)
  const investigationPlanDigest = p2S1W5Digest({
    schema_version: 'country_outage_p2_investigation_plan_projection_v1',
    ...definition,
  })
  const withoutGroundingDigest = {
    ...definition,
    investigation_plan_digest: investigationPlanDigest,
  }
  return p2S1W5DeepFreeze({
    ...withoutGroundingDigest,
    grounding_plan_digest: p2S1W5Digest({
      schema_version: 'country_outage_p2_grounding_plan_projection_v1',
      ...withoutGroundingDigest,
    }),
  })
}

export function createP2S1W5CommittedEvidenceGraphRecord(input: Omit<
  P2S1W5CommittedEvidenceGraphRecord,
  'evidence_graph_digest' | 'evidence_bundle_digest'
>): P2S1W5CommittedEvidenceGraphRecord {
  const facts = p2S1W5Clone(input.facts)
  const evidenceBundleDigest = p2S1W5Digest({
    facts,
    boundary_assertion_ids: input.boundary_assertion_ids,
    unknown_ids: input.unknown_ids,
  })
  const withoutGraphDigest = {
    ...p2S1W5Clone(input),
    facts,
    evidence_bundle_digest: evidenceBundleDigest,
  }
  return p2S1W5DeepFreeze({
    ...withoutGraphDigest,
    evidence_graph_digest: p2S1W5Digest({
      schema_version: 'country_outage_p2_evidence_graph_projection_v1',
      ...withoutGraphDigest,
    }),
  })
}

export function validateP2S1TeacherSemanticPlan(options: {
  value: unknown
  questionId: string
  questionDigest: string
  goalDigest: string
  allowedCapabilityIds: readonly string[]
}): P2S1TeacherSemanticPlan {
  const value = objectValue(options.value, 'TeacherSemanticPlan')
  exactKeys(value, [
    'plan_id', 'question_id', 'question_digest', 'goal_digest', 'subgoals',
    'ambiguity_ids', 'tool_selection_authority', 'executable_plan', 'output_digest',
  ], 'TeacherSemanticPlan')
  p2S1W5AssertNonempty(value.plan_id, 'TeacherSemanticPlan.plan_id')
  if (
    value.question_id !== options.questionId
    || value.question_digest !== options.questionDigest
    || value.goal_digest !== options.goalDigest
  ) throw new P2S1W5ContractError('teacher_plan_binding_mismatch', 'TeacherSemanticPlan 未绑定同一问题或目标')
  if (value.tool_selection_authority !== false || value.executable_plan !== false) {
    throw new P2S1W5ContractError('teacher_plan_authority_violation', 'Sol 只能提出 capability intent，不能选择 Tool 或提交可执行计划')
  }
  if (!Array.isArray(value.subgoals) || value.subgoals.length < 1) {
    throw new P2S1W5ContractError('teacher_plan_invalid', 'TeacherSemanticPlan 至少包含一个 subgoal')
  }
  const allowed = new Set(options.allowedCapabilityIds)
  const subgoals = value.subgoals.map((raw, index) => {
    const item = objectValue(raw, `TeacherSemanticPlan.subgoals[${index}]`)
    exactKeys(item, ['subgoal_id', 'capability_id'], `TeacherSemanticPlan.subgoals[${index}]`)
    p2S1W5AssertNonempty(item.subgoal_id, 'subgoal_id')
    p2S1W5AssertNonempty(item.capability_id, 'capability_id')
    if (/^(?:TOOL|OP|GATE|PLAN|RENDERER|DELIVERY)-/.test(item.capability_id)) {
      throw new P2S1W5ContractError('teacher_plan_unit_smuggling', 'TeacherSemanticPlan 不得夹带 execution unit ID')
    }
    if (!allowed.has(item.capability_id)) {
      throw new P2S1W5ContractError('teacher_plan_capability_denied', `未允许 capability：${item.capability_id}`)
    }
    return { subgoal_id: item.subgoal_id, capability_id: item.capability_id }
  })
  p2S1W5AssertUnique(subgoals.map((item) => item.subgoal_id), 'subgoal_id')
  if (!Array.isArray(value.ambiguity_ids) || value.ambiguity_ids.some((item) => typeof item !== 'string')) {
    throw new P2S1W5ContractError('teacher_plan_invalid', 'ambiguity_ids 无效')
  }
  p2S1W5AssertDigest(value.output_digest, 'TeacherSemanticPlan.output_digest')
  if (value.output_digest !== p2S1W5DigestWithout(value, 'output_digest')) {
    throw new P2S1W5ContractError('teacher_plan_digest_mismatch', 'TeacherSemanticPlan 摘要不一致')
  }
  return p2S1W5DeepFreeze(p2S1W5Clone(value as unknown as P2S1TeacherSemanticPlan))
}

export interface P2S1W5GroundingResult {
  plan: P2S1W5GroundingPlanRecord
  graph: P2S1W5CommittedEvidenceGraphRecord
  receipt: P2S1W5TeacherPlanGroundingReceipt
}

export function groundP2S1TeacherSemanticPlan(options: {
  semanticPlan: P2S1TeacherSemanticPlan
  trustedPlan: P2S1W5GroundingPlanRecord
  trustedGraph: P2S1W5CommittedEvidenceGraphRecord
}): P2S1W5GroundingResult {
  const plan = p2S1W5Clone(options.trustedPlan)
  const graph = p2S1W5Clone(options.trustedGraph)
  if (options.semanticPlan.plan_id !== plan.plan_id) {
    throw new P2S1W5ContractError('grounding_plan_id_mismatch', 'Teacher semantic plan 与 Host plan_id 不一致')
  }
  const expectedPlan = createP2S1W5GroundingPlanRecord({
    plan_id: plan.plan_id,
    plan_revision: plan.plan_revision,
    admitted_capability_ids: plan.admitted_capability_ids,
    registry_snapshot_id: plan.registry_snapshot_id,
    registry_snapshot_digest: plan.registry_snapshot_digest,
    effective_teacher_required: plan.effective_teacher_required,
    degraded_authorization_digest: plan.degraded_authorization_digest,
  })
  if (
    expectedPlan.grounding_plan_digest !== plan.grounding_plan_digest
    || expectedPlan.investigation_plan_digest !== plan.investigation_plan_digest
  ) throw new P2S1W5ContractError('trusted_plan_invalid', '受信计划的确定性摘要无法重算')
  const expectedGraph = createP2S1W5CommittedEvidenceGraphRecord({
    graph_id: graph.graph_id,
    graph_revision: graph.graph_revision,
    investigation_plan_digest: graph.investigation_plan_digest,
    registry_snapshot_id: graph.registry_snapshot_id,
    registry_snapshot_digest: graph.registry_snapshot_digest,
    facts: graph.facts,
    boundary_assertion_ids: graph.boundary_assertion_ids,
    unknown_ids: graph.unknown_ids,
  })
  if (
    expectedGraph.evidence_graph_digest !== graph.evidence_graph_digest
    || expectedGraph.evidence_bundle_digest !== graph.evidence_bundle_digest
  ) throw new P2S1W5ContractError('trusted_graph_invalid', '受信证据图的确定性摘要无法重算')
  if (
    plan.investigation_plan_digest !== graph.investigation_plan_digest
    || plan.registry_snapshot_id !== graph.registry_snapshot_id
    || plan.registry_snapshot_digest !== graph.registry_snapshot_digest
  ) throw new P2S1W5ContractError('plan_graph_binding_mismatch', '计划、证据图和 Registry 未闭合')
  const proposed = new Set(options.semanticPlan.subgoals.map((item) => item.capability_id))
  const admitted = new Set(plan.admitted_capability_ids)
  if (proposed.size !== admitted.size || [...proposed].some((id) => !admitted.has(id))) {
    throw new P2S1W5ContractError('grounding_capability_mismatch', 'Host admission 与 Teacher capability intents 不一致')
  }
  const withoutDigest = {
    receipt_id: `teacher-plan-grounding:${randomUUID()}`,
    teacher_semantic_plan_digest: options.semanticPlan.output_digest,
    grounding_plan_digest: plan.grounding_plan_digest,
    registry_snapshot_digest: plan.registry_snapshot_digest,
    disposition: 'passed' as const,
  }
  return p2S1W5DeepFreeze({
    plan,
    graph,
    receipt: {
      ...withoutDigest,
      receipt_digest: p2S1W5Digest(withoutDigest),
    },
  })
}
