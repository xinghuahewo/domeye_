import { createHash } from 'node:crypto'

export type P2S1Json = null | boolean | number | string | P2S1Json[] | {
  [key: string]: P2S1Json
}

export type P2S1W5ModelPhase =
  | 'sol_planning'
  | 'sol_reference'
  | 'ds_first_answer'
  | 'ds_revision'

export type P2S1W5FinalDisposition =
  | 'aligned_published'
  | 'ds_unaligned_degraded'
  | 'teacher_unavailable'
  | 'teacher_rejected'
  | 'student_rejected'
  | 'alignment_rejected'

export type P2S1W5FlowState =
  | 'teacher_rejected'
  | 'published'
  | 'stopped_waiting_teacher'
  | 'degraded_published'
  | 'failed'
  | 'alignment_failed'

export interface P2S1W5ExactModelIdentity {
  provider: string
  model: string
  version: string
  expected_response_model?: string
  pi_version?: string
  candidate_resource_sha256?: string
  profile_registry_sha256?: string
  identity_digest: string
}

export interface P2S1W5RunCost {
  latency_ms: number
  input_tokens: number
  output_tokens: number
  cost_amount: number
  cost_currency: string
  retry_count: 0
}

export interface P2S1W5ModelRunReceipt {
  run_id: string
  role: 'teacher' | 'student'
  run_phase: P2S1W5ModelPhase
  exact_model_identity: P2S1W5ExactModelIdentity
  shared_answer_binding_digest: string
  role_specific_input_digest: string
  output_digest: string | null
  validation_receipt_digest: string | null
  cost: P2S1W5RunCost
  disposition: 'completed' | 'failed' | 'unavailable' | 'cancelled' | 'rejected'
}

export interface P2S1TeacherSemanticPlan {
  plan_id: string
  question_id: string
  question_digest: string
  goal_digest: string
  subgoals: Array<{
    subgoal_id: string
    capability_id: string
  }>
  ambiguity_ids: string[]
  tool_selection_authority: false
  executable_plan: false
  output_digest: string
}

export interface P2S1W5SharedAnswerBinding {
  question_id: string
  question_digest: string
  goal_digest: string
  incident_id: string
  publication_id: string
  publication_revision: number
  publication_digest: string
  collector_id: 'rrc25'
  cohort_id: string
  cohort_digest: string
  window_start_utc: string
  window_end_utc: string
  data_through_utc: string
  finality: 'event_end_unknown' | 'event_end_known'
  binding_generation: number
  teacher_semantic_plan_digest: string
  teacher_plan_grounding_receipt_digest: string
  grounding_plan_digest: string
  plan_id: string
  plan_revision: number
  investigation_plan_digest: string
  evidence_bundle_digest: string
  evidence_graph_revision: number
  evidence_graph_digest: string
  registry_snapshot_id: string
  registry_snapshot_digest: string
  boundary_policy_digest: string
  world_knowledge_bundle_digest: string | null
  world_knowledge_policy: 'explanation_and_hypothesis_only_not_event_evidence'
  prompt_version: string
  prompt_digest: string
  policy_version: string
  policy_digest: string
}

export interface P2S1W5GroundingPlanRecord {
  plan_id: string
  plan_revision: number
  grounding_plan_digest: string
  investigation_plan_digest: string
  admitted_capability_ids: string[]
  registry_snapshot_id: string
  registry_snapshot_digest: string
  effective_teacher_required: boolean
  degraded_authorization_digest: string | null
}

export interface P2S1W5EvidenceFact {
  fact_id: string
  source_node_id: string
  source_value_digest: string
  evidence_refs: string[]
}

export interface P2S1W5CommittedEvidenceGraphRecord {
  graph_id: string
  graph_revision: number
  evidence_graph_digest: string
  evidence_bundle_digest: string
  investigation_plan_digest: string
  registry_snapshot_id: string
  registry_snapshot_digest: string
  facts: P2S1W5EvidenceFact[]
  boundary_assertion_ids: string[]
  unknown_ids: string[]
}

export interface P2S1W5TeacherPlanGroundingReceipt {
  receipt_id: string
  teacher_semantic_plan_digest: string
  grounding_plan_digest: string
  registry_snapshot_digest: string
  disposition: 'passed'
  receipt_digest: string
}

export interface P2S1W5TeacherReference {
  teacher_reference_id: string
  shared_answer_binding_digest: string
  required_fact_ids: string[]
  evidence_refs: string[]
  boundary_assertions: string[]
  unknowns: string[]
  answer_outline: string[]
  teacher_reference_is_ground_truth: false
  private_chain_of_thought_persisted: false
  output_digest: string
}

export type P2S1W5ClaimKind =
  | 'observed_fact'
  | 'derived_fact'
  | 'knowledge_explanation'
  | 'testable_hypothesis'
  | 'limitation'
  | 'unknown'

export type P2S1W5ClaimRelation =
  | 'states_observed_fact'
  | 'states_derived_fact'
  | 'explains_knowledge'
  | 'proposes_testable_hypothesis'
  | 'states_limitation'
  | 'states_unknown'

export interface P2S1W5AnswerClaim {
  claim_id: string
  claim_kind: P2S1W5ClaimKind
  claim_relation: P2S1W5ClaimRelation
  text: string
  fact_ids: string[]
  source_node_ids: string[]
  source_value_digests: string[]
  evidence_refs: string[]
  boundary_assertion_ids: string[]
  verification_requirements: string[]
}

export interface P2S1W5StudentAnswerPayload {
  claims: P2S1W5AnswerClaim[]
  evidence_refs: string[]
  limitations: string[]
  unknowns: string[]
  answer_text: string
}

export interface P2S1W5StudentAnswerArtifact {
  artifact_ref: string
  artifact_schema_ref: '#/$defs/studentAnswerPayload'
  answer_payload: P2S1W5StudentAnswerPayload
  answer_digest: string
  artifact_receipt_digest: string
}

export interface P2S1W5GateResult {
  gate_id: 'GATE-01' | 'GATE-02' | 'GATE-03' | 'GATE-04' | 'GATE-05'
  passed: boolean
  receipt_digest: string
}

export interface P2S1W5ValidationReceipt {
  validation_id: string
  subject_digest: string
  shared_answer_binding_digest: string
  gate_results: P2S1W5GateResult[]
  all_gates_passed: boolean
  receipt_digest: string
}

export interface P2S1W5QuestionOracleRecord {
  question_id: string
  required_fact_ids: string[]
  required_boundary_assertion_ids: string[]
  allowed_boundary_assertion_ids: string[]
  required_unknown_ids: string[]
  prohibited_assertion_ids: string[]
  oracle_digest: string
}

export interface P2S1W5QuestionOracleSeed {
  question_id: string
  required_fact_ids: string[]
  required_boundary_assertions: string[]
  allowed_boundary_assertions: string[]
  required_unknowns: string[]
  prohibited_assertions: string[]
}

export interface P2S1W5TeacherOracleCoverageReceipt {
  coverage_run_id: string
  question_id: string
  shared_answer_binding_digest: string
  oracle_digest: string
  teacher_reference_digest: string
  required_fact_ids_complete: boolean
  required_boundary_assertions_complete: boolean
  required_unknowns_complete: boolean
  prohibited_assertion_count: number
  disposition: 'passed' | 'rejected'
  receipt_digest: string
}

export interface P2S1W5DifferenceItem {
  kind:
    | 'missing_required_fact_id'
    | 'incorrect_evidence_ref'
    | 'missing_boundary_assertion'
    | 'missing_unknown'
    | 'structure_gap'
  reference_id: string
}

export interface P2S1W5StructuredFeedback {
  feedback_round: 1
  producer_kind: 'host_deterministic_alignment_evaluator'
  source_student_answer_digest: string
  source_validation_receipt_digest: string
  difference_items: P2S1W5DifferenceItem[]
  may_add_event_facts: false
  may_change_evidence: false
  may_change_prompt_or_policy: false
  feedback_digest: string
}

export interface P2S1W5StudentRun {
  revision_ordinal: 0 | 1
  run_receipt: P2S1W5ModelRunReceipt
  teacher_reference_digest: string | null
  teacher_validation_receipt_digest: string | null
  teacher_oracle_coverage_receipt_digest: string | null
  student_answer_digest: string
  student_answer_artifact: P2S1W5StudentAnswerArtifact
  validation_receipt: P2S1W5ValidationReceipt
  may_call_tools: false
  may_add_event_facts: false
}

export interface P2S1W5AlignmentReceipt {
  alignment_run_id: string
  shared_answer_binding_digest: string
  teacher_reference_digest: string
  student_answer_digest: string
  oracle_digest: string
  evidence_graph_digest: string
  teacher_oracle_coverage_receipt_digest: string
  evaluator_id: 'country_outage_p2_alignment_evaluator'
  evaluator_version: '1.0.0'
  evaluator_contract_digest: '2a7b9dc73194dc78fc797b9be30676ccdfde38599466e47305e50ef963e53b3b'
  evaluator_implementation_digest: 'd193bee50269c2e464ed3b98ef0deb293dcaee1b55ea12aebbbd2e11ed4947cc'
  metric_inputs_digest: string
  hard_gate_metrics: {
    fact_precision: number
    evidence_ref_precision: number
    boundary_compliance: number
  }
  advisory_text_similarity: number
  hard_gates_passed: boolean
  disposition: 'passed' | 'rejected'
  receipt_digest: string
}

export interface P2S1W5PublishedAnswer {
  answer_digest: string
  shared_answer_binding_digest: string
  claims: P2S1W5AnswerClaim[]
  claims_digest: string
  evidence_refs: string[]
  limitations: string[]
  unknowns: string[]
  aligned_claim: boolean
  event_causality_claimed: false
  recovery_claimed: false
}

export interface P2S1W5DegradedAuthorization {
  authorization_id: string
  user_confirmed: true
  mode: 'ds_unaligned_degraded'
  parent_plan_revision: number
  new_plan_revision: number
  may_claim_sol_ds_alignment: false
  authorization_digest: string
}

export interface P2S1W5DualModelFlow {
  schema_version: 'country_outage_p2_dual_model_answer_flow_v1'
  flow_id: string
  flow_revision: number
  parent_flow_revision: number | null
  flow_state: P2S1W5FlowState
  execution_order: readonly [
    'gpt-5.6-sol',
    'teacher_reference_validator',
    'ds_student',
    'student_answer_validator',
    'alignment_evaluator',
  ]
  default_teacher_required: true
  effective_teacher_required: boolean
  shared_answer_binding: P2S1W5SharedAnswerBinding
  shared_answer_binding_digest: string
  teacher_model_identity: P2S1W5ExactModelIdentity
  student_model_identity: P2S1W5ExactModelIdentity
  teacher_plan_run_receipt: P2S1W5ModelRunReceipt | null
  teacher_plan_grounding_receipt: P2S1W5TeacherPlanGroundingReceipt | null
  teacher_run_receipt: P2S1W5ModelRunReceipt | null
  teacher_reference: P2S1W5TeacherReference | null
  teacher_validation_receipt: P2S1W5ValidationReceipt | null
  teacher_oracle_coverage_receipt: P2S1W5TeacherOracleCoverageReceipt | null
  student_runs: P2S1W5StudentRun[]
  structured_feedback: P2S1W5StructuredFeedback | null
  student_validation_receipt: P2S1W5ValidationReceipt | null
  alignment_run_receipt: P2S1W5AlignmentReceipt | null
  degraded_authorization: P2S1W5DegradedAuthorization | null
  teacher_unavailable_phase: 'none' | 'sol_reference'
  final_disposition: P2S1W5FinalDisposition
  published_answer: P2S1W5PublishedAnswer | null
  publish_receipt_digest: string | null
  design_boundary: {
    design_only: true
    model_calls_implemented: false
    runtime_integrated: false
    production_deployed: false
  }
}

export interface P2S1W5FixtureBindingBase {
  question_id: string
  question: string
  question_digest: string
  goal: string
  goal_digest: string
  incident_id: string
  publication_id: string
  publication_revision: number
  publication_digest: string
  cohort_id: string
  cohort_digest: string
  window_start_utc: string
  window_end_utc: string
  data_through_utc: string
  finality: 'event_end_unknown' | 'event_end_known'
  binding_generation: number
  boundary_policy_digest: string
  prompt_version: string
  prompt_digest: string
  policy_version: string
  policy_digest: string
}

export interface P2S1W5TrustedReplayFixture {
  fixture_id: string
  fixture_digest: string
  binding: P2S1W5FixtureBindingBase
  teacher_identity: P2S1W5ExactModelIdentity
  student_identity: P2S1W5ExactModelIdentity
  allowed_capability_ids: string[]
  grounding_plan: P2S1W5GroundingPlanRecord
  evidence_graph: P2S1W5CommittedEvidenceGraphRecord
  oracle_seed: P2S1W5QuestionOracleSeed
  scripted_outputs: Partial<Record<P2S1W5ModelPhase, P2S1Json>>
  unavailable_phases: P2S1W5ModelPhase[]
  force_alignment_rejection: boolean
  degraded_authorization: P2S1W5DegradedAuthorization | null
  degraded_binding: P2S1W5SharedAnswerBinding | null
}

export interface P2S1W5RunRequest {
  fixture_id: string
  idempotency_key: string
  degraded_authorization_id?: string
}

export type P2S1W5RunResult =
  | {
      schema_version: 'country_outage_p2_s1_w5_run_result_v1'
      fixture_id: string
      fixture_digest: string
      idempotency_key: string
      flow: P2S1W5DualModelFlow
      planning_failure_receipt: null
      model_call_summary: P2S1W5ModelCallSummary
    }
  | {
      schema_version: 'country_outage_p2_s1_w5_run_result_v1'
      fixture_id: string
      fixture_digest: string
      idempotency_key: string
      flow: null
      planning_failure_receipt: P2S1W5ModelRunReceipt
      model_call_summary: P2S1W5ModelCallSummary
    }

export interface P2S1W5ModelCallSummary {
  external_provider_called: false
  fixture_replay_only: true
  phase_attempt_counts: Record<P2S1W5ModelPhase, number>
  total_attempts: number
  successful_attempts: number
  failed_attempts: number
  total_input_tokens: number
  total_output_tokens: number
  total_cost_amount: number
  cost_currency: string
}

export class P2S1W5ContractError extends Error {
  constructor(
    public readonly code: string,
    message: string,
  ) {
    super(message)
    this.name = 'P2S1W5ContractError'
  }
}

function canonicalNumber(value: number): string {
  if (!Number.isFinite(value)) throw new P2S1W5ContractError('non_json_value', '存在非有限数字')
  if (Object.is(value, -0) || value === 0) return '0'
  return JSON.stringify(value)
}

export function p2S1W5CanonicalJson(value: unknown): string {
  if (value === null) return 'null'
  if (typeof value === 'boolean') return value ? 'true' : 'false'
  if (typeof value === 'number') return canonicalNumber(value)
  if (typeof value === 'string') return JSON.stringify(value)
  if (Array.isArray(value)) return `[${value.map(p2S1W5CanonicalJson).join(',')}]`
  if (value && typeof value === 'object') {
    return `{${Object.entries(value as Record<string, unknown>)
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([key, item]) => `${JSON.stringify(key)}:${p2S1W5CanonicalJson(item)}`)
      .join(',')}}`
  }
  throw new P2S1W5ContractError('non_json_value', '制品只能包含 JSON 值')
}

export function p2S1W5Digest(value: unknown): string {
  return createHash('sha256').update(p2S1W5CanonicalJson(value)).digest('hex')
}

export function p2S1W5DigestWithout<T extends Record<string, unknown>>(
  value: T,
  field: keyof T,
): string {
  const copy = structuredClone(value)
  delete copy[field]
  return p2S1W5Digest(copy)
}

export function p2S1W5DeepFreeze<T>(value: T): T {
  if (value && typeof value === 'object' && !Object.isFrozen(value)) {
    Object.freeze(value)
    for (const item of Object.values(value as Record<string, unknown>)) p2S1W5DeepFreeze(item)
  }
  return value
}

export function p2S1W5Clone<T>(value: T): T {
  return structuredClone(value)
}

export function p2S1W5AssertDigest(value: unknown, label: string): asserts value is string {
  if (typeof value !== 'string' || !/^[0-9a-f]{64}$/.test(value)) {
    throw new P2S1W5ContractError('invalid_digest', `${label} 不是冻结合同要求的 SHA-256 摘要`)
  }
}

export function p2S1W5AssertNonempty(value: unknown, label: string): asserts value is string {
  if (typeof value !== 'string' || !value.trim()) {
    throw new P2S1W5ContractError('invalid_string', `${label} 必须是非空字符串`)
  }
}

export function p2S1W5AssertUnique(values: readonly string[], label: string): void {
  if (new Set(values).size !== values.length) {
    throw new P2S1W5ContractError('duplicate_value', `${label} 不允许重复值`)
  }
}

export function p2S1W5ValidateExactModelIdentity(
  value: P2S1W5ExactModelIdentity,
): P2S1W5ExactModelIdentity {
  p2S1W5AssertNonempty(value.provider, 'model.provider')
  p2S1W5AssertNonempty(value.model, 'model.model')
  p2S1W5AssertNonempty(value.version, 'model.version')
  if (/latest/i.test(`${value.model}:${value.version}`)) {
    throw new P2S1W5ContractError('mutable_latest_forbidden', '模型身份不得使用 latest')
  }
  p2S1W5AssertDigest(value.identity_digest, 'model.identity_digest')
  const expected = p2S1W5DigestWithout(
    value as unknown as Record<string, unknown>,
    'identity_digest',
  )
  if (value.identity_digest !== expected) {
    throw new P2S1W5ContractError('model_identity_digest_mismatch', '模型身份摘要不一致')
  }
  return p2S1W5DeepFreeze(p2S1W5Clone(value))
}

export function p2S1W5Identity<T extends Omit<P2S1W5ExactModelIdentity, 'identity_digest'>>(
  value: T,
): T & { identity_digest: string } {
  return p2S1W5DeepFreeze({
    ...p2S1W5Clone(value),
    identity_digest: p2S1W5Digest(value),
  })
}

export function p2S1W5ValidateTerminalClosure(flow: P2S1W5DualModelFlow): void {
  const fail = (message: string): never => {
    throw new P2S1W5ContractError('terminal_closure_violation', message)
  }
  if (flow.shared_answer_binding_digest !== p2S1W5Digest(flow.shared_answer_binding)) {
    fail('shared_answer_binding_digest 无法重算')
  }
  if (flow.student_runs.length > 2) fail('Student 调用超过两次')
  if (flow.student_runs.some((run, index) => run.revision_ordinal !== index)) {
    fail('Student revision ordinal 不连续')
  }
  if (flow.student_runs.length === 2 && !flow.structured_feedback) fail('第二次 Student 运行缺少反馈')
  if (flow.student_runs.length < 2 && flow.structured_feedback) fail('无 revision 时禁止反馈')
  const nullPublished = (): void => {
    if (flow.published_answer || flow.publish_receipt_digest) fail('非发布终态携带发布制品')
  }
  switch (flow.final_disposition) {
    case 'aligned_published':
      if (
        flow.flow_state !== 'published'
        || !flow.teacher_plan_run_receipt
        || !flow.teacher_plan_grounding_receipt
        || !flow.teacher_run_receipt
        || !flow.teacher_reference
        || flow.teacher_validation_receipt?.all_gates_passed !== true
        || flow.teacher_oracle_coverage_receipt?.disposition !== 'passed'
        || flow.student_validation_receipt?.all_gates_passed !== true
        || flow.alignment_run_receipt?.disposition !== 'passed'
        || !flow.published_answer?.aligned_claim
        || !flow.publish_receipt_digest
      ) fail('aligned_published 终态未闭合')
      break
    case 'teacher_unavailable':
      if (
        flow.flow_state !== 'stopped_waiting_teacher'
        || flow.teacher_unavailable_phase !== 'sol_reference'
        || flow.teacher_run_receipt?.disposition !== 'unavailable'
        || flow.teacher_reference
        || flow.student_runs.length
      ) fail('teacher_unavailable 终态未闭合')
      nullPublished()
      break
    case 'teacher_rejected':
      if (
        flow.flow_state !== 'teacher_rejected'
        || flow.student_runs.length
        || flow.alignment_run_receipt
      ) fail('teacher_rejected 终态未闭合')
      const teacherValidation = flow.teacher_validation_receipt
      if (!teacherValidation) {
        throw new P2S1W5ContractError('terminal_closure_violation', 'teacher_rejected 缺少 Teacher validation')
      }
      if (
        teacherValidation.all_gates_passed
        && flow.teacher_oracle_coverage_receipt?.disposition !== 'rejected'
      ) fail('Teacher Gate 通过后的拒绝必须来自 Oracle coverage')
      if (!teacherValidation.all_gates_passed && flow.teacher_oracle_coverage_receipt) {
        fail('Teacher Gate 拒绝不得生成 Oracle coverage 回执')
      }
      nullPublished()
      break
    case 'student_rejected':
      if (
        flow.flow_state !== 'failed'
        || flow.student_runs.length < 1
        || flow.student_validation_receipt?.all_gates_passed !== false
        || flow.alignment_run_receipt
      ) fail('student_rejected 终态未闭合')
      nullPublished()
      break
    case 'alignment_rejected':
      if (
        flow.flow_state !== 'alignment_failed'
        || !flow.student_validation_receipt?.all_gates_passed
        || flow.alignment_run_receipt?.disposition !== 'rejected'
      ) fail('alignment_rejected 终态未闭合')
      nullPublished()
      break
    case 'ds_unaligned_degraded':
      if (
        flow.flow_state !== 'degraded_published'
        || flow.effective_teacher_required
        || !flow.degraded_authorization?.user_confirmed
        || flow.teacher_plan_run_receipt
        || flow.teacher_run_receipt
        || flow.teacher_reference
        || flow.alignment_run_receipt
        || flow.published_answer?.aligned_claim !== false
        || !flow.publish_receipt_digest
      ) fail('ds_unaligned_degraded 终态未闭合')
      break
  }
}

export const P2S1_W5_EXECUTION_ORDER = [
  'gpt-5.6-sol',
  'teacher_reference_validator',
  'ds_student',
  'student_answer_validator',
  'alignment_evaluator',
] as const

export const P2S1_W5_DESIGN_BOUNDARY = {
  design_only: true,
  model_calls_implemented: false,
  runtime_integrated: false,
  production_deployed: false,
} as const

export const P2S1_W5_FROZEN_STUDENT_IDENTITY = p2S1W5Identity({
  provider: 'deepseek',
  model: 'deepseek-v4-flash',
  version: 'deepseek-v4-flash-pi-0.84.1-v1',
  expected_response_model: 'deepseek-v4-flash',
  pi_version: '0.84.1',
  candidate_resource_sha256: 'ac00eeb087bc9651fd27391066d9d16a416aad887cb552737696289ded3ce2b5',
  profile_registry_sha256: 'e8881aa2b79f495da3ea551bb3b2423af45c118f5e622ac1877852bf0087bf4f',
})
