import { randomUUID } from 'node:crypto'

import {
  P2S1W5ContractError,
  P2S1_W5_DESIGN_BOUNDARY,
  P2S1_W5_EXECUTION_ORDER,
  p2S1W5AssertNonempty,
  p2S1W5Clone,
  p2S1W5DeepFreeze,
  p2S1W5Digest,
  p2S1W5ValidateTerminalClosure,
  type P2S1Json,
  type P2S1TeacherSemanticPlan,
  type P2S1W5DualModelFlow,
  type P2S1W5FixtureBindingBase,
  type P2S1W5ModelCallSummary,
  type P2S1W5ModelRunReceipt,
  type P2S1W5PublishedAnswer,
  type P2S1W5RunRequest,
  type P2S1W5RunResult,
  type P2S1W5SharedAnswerBinding,
  type P2S1W5StudentAnswerPayload,
  type P2S1W5StudentRun,
  type P2S1W5TrustedReplayFixture,
  type P2S1W5ValidationReceipt,
} from './p2-s1-composition-contracts.js'
import { evaluateP2S1W5Alignment } from './p2-s1-alignment-evaluator.js'
import { P2S1W5ArtifactStore } from './p2-s1-dual-artifact-store.js'
import {
  createP2S1W5StructuredFeedback,
  validateP2S1W5StudentAnswerPayload,
  validateP2S1W5StudentGates,
  validateP2S1W5TeacherGates,
  validateP2S1W5TeacherReference,
} from './p2-s1-gate-validator.js'
import {
  P2S1W5CallBudget,
  runP2S1W5ModelPhase,
  type P2S1W5InjectedModelPort,
  type P2S1W5TrustedFixtureCatalog,
} from './p2-s1-model-runner.js'
import {
  evaluateP2S1W5TeacherOracleCoverage,
  materializeP2S1W5QuestionOracle,
} from './p2-s1-oracle-materializer.js'
import {
  groundP2S1TeacherSemanticPlan,
  validateP2S1TeacherSemanticPlan,
} from './p2-s1-teacher-plan-grounder.js'

function asJson(value: unknown): P2S1Json {
  return p2S1W5Clone(value) as P2S1Json
}

function finalizedRunReceipt(
  receipt: P2S1W5ModelRunReceipt,
  options: {
    sharedBindingDigest: string
    outputDigest?: string | null
    validationReceiptDigest?: string | null
  },
): P2S1W5ModelRunReceipt {
  return p2S1W5DeepFreeze({
    ...p2S1W5Clone(receipt),
    shared_answer_binding_digest: options.sharedBindingDigest,
    ...(options.outputDigest !== undefined ? { output_digest: options.outputDigest } : {}),
    ...(options.validationReceiptDigest !== undefined
      ? { validation_receipt_digest: options.validationReceiptDigest }
      : {}),
  })
}

function hostArtifactValidation(subjectDigest: string, validatorId: string): P2S1Json {
  const withoutDigest = {
    validator_id: validatorId,
    validator_version: '1.0.0',
    subject_digest: subjectDigest,
    disposition: 'passed',
  }
  return {
    ...withoutDigest,
    receipt_digest: p2S1W5Digest(withoutDigest),
  }
}

function buildSharedBinding(options: {
  fixture: P2S1W5TrustedReplayFixture
  semanticPlan: P2S1TeacherSemanticPlan
  groundingReceiptDigest: string
}): P2S1W5SharedAnswerBinding {
  const { binding, grounding_plan: plan, evidence_graph: graph } = options.fixture
  if (binding.question_digest !== p2S1W5Digest(binding.question)
    || binding.goal_digest !== p2S1W5Digest(binding.goal)) {
    throw new P2S1W5ContractError('fixture_question_digest_mismatch', 'fixture 问题或目标摘要无法重算')
  }
  const start = Date.parse(binding.window_start_utc)
  const end = Date.parse(binding.window_end_utc)
  const through = Date.parse(binding.data_through_utc)
  if (![start, end, through].every(Number.isFinite) || start > end || end > through) {
    throw new P2S1W5ContractError('fixture_time_order_invalid', 'fixture 时间边界无效')
  }
  return p2S1W5DeepFreeze({
    question_id: binding.question_id,
    question_digest: binding.question_digest,
    goal_digest: binding.goal_digest,
    incident_id: binding.incident_id,
    publication_id: binding.publication_id,
    publication_revision: binding.publication_revision,
    publication_digest: binding.publication_digest,
    collector_id: 'rrc25',
    cohort_id: binding.cohort_id,
    cohort_digest: binding.cohort_digest,
    window_start_utc: binding.window_start_utc,
    window_end_utc: binding.window_end_utc,
    data_through_utc: binding.data_through_utc,
    finality: binding.finality,
    binding_generation: binding.binding_generation,
    teacher_semantic_plan_digest: options.semanticPlan.output_digest,
    teacher_plan_grounding_receipt_digest: options.groundingReceiptDigest,
    grounding_plan_digest: plan.grounding_plan_digest,
    plan_id: plan.plan_id,
    plan_revision: plan.plan_revision,
    investigation_plan_digest: plan.investigation_plan_digest,
    evidence_bundle_digest: graph.evidence_bundle_digest,
    evidence_graph_revision: graph.graph_revision,
    evidence_graph_digest: graph.evidence_graph_digest,
    registry_snapshot_id: graph.registry_snapshot_id,
    registry_snapshot_digest: graph.registry_snapshot_digest,
    boundary_policy_digest: binding.boundary_policy_digest,
    world_knowledge_bundle_digest: null,
    world_knowledge_policy: 'explanation_and_hypothesis_only_not_event_evidence',
    prompt_version: binding.prompt_version,
    prompt_digest: binding.prompt_digest,
    policy_version: binding.policy_version,
    policy_digest: binding.policy_digest,
  })
}

function callSummary(budget: P2S1W5CallBudget): P2S1W5ModelCallSummary {
  const receipts = budget.receipts()
  const currencies = new Set(receipts.map((receipt) => receipt.cost.cost_currency))
  if (currencies.size > 1) throw new P2S1W5ContractError('mixed_cost_currency', 'W5 单次 flow 不支持混合币种汇总')
  return p2S1W5DeepFreeze({
    external_provider_called: false,
    fixture_replay_only: true,
    phase_attempt_counts: budget.counts(),
    total_attempts: receipts.length,
    successful_attempts: receipts.filter((receipt) => receipt.disposition === 'completed').length,
    failed_attempts: receipts.filter((receipt) => receipt.disposition !== 'completed').length,
    total_input_tokens: receipts.reduce((sum, receipt) => sum + receipt.cost.input_tokens, 0),
    total_output_tokens: receipts.reduce((sum, receipt) => sum + receipt.cost.output_tokens, 0),
    total_cost_amount: receipts.reduce((sum, receipt) => sum + receipt.cost.cost_amount, 0),
    cost_currency: [...currencies][0] ?? 'USD',
  })
}

function flowBase(options: {
  fixture: P2S1W5TrustedReplayFixture
  binding: P2S1W5SharedAnswerBinding
  flowId: string
  flowRevision?: number
  parentFlowRevision?: number | null
}): Pick<P2S1W5DualModelFlow,
  'schema_version' | 'flow_id' | 'flow_revision' | 'parent_flow_revision'
  | 'execution_order' | 'default_teacher_required' | 'shared_answer_binding'
  | 'shared_answer_binding_digest' | 'teacher_model_identity' | 'student_model_identity'
  | 'design_boundary'> {
  return {
    schema_version: 'country_outage_p2_dual_model_answer_flow_v1',
    flow_id: options.flowId,
    flow_revision: options.flowRevision ?? 1,
    parent_flow_revision: options.parentFlowRevision ?? null,
    execution_order: P2S1_W5_EXECUTION_ORDER,
    default_teacher_required: true,
    shared_answer_binding: options.binding,
    shared_answer_binding_digest: p2S1W5Digest(options.binding),
    teacher_model_identity: options.fixture.teacher_identity,
    student_model_identity: options.fixture.student_identity,
    design_boundary: P2S1_W5_DESIGN_BOUNDARY,
  }
}

function createStudentRun(options: {
  revisionOrdinal: 0 | 1
  runReceipt: P2S1W5ModelRunReceipt
  artifact: ReturnType<P2S1W5ArtifactStore['putStudentAnswer']>
  validationReceipt: P2S1W5ValidationReceipt
  teacherReferenceDigest: string | null
  teacherValidationReceiptDigest: string | null
  teacherOracleCoverageReceiptDigest: string | null
}): P2S1W5StudentRun {
  return p2S1W5DeepFreeze({
    revision_ordinal: options.revisionOrdinal,
    run_receipt: finalizedRunReceipt(options.runReceipt, {
      sharedBindingDigest: options.runReceipt.shared_answer_binding_digest,
      outputDigest: options.artifact.answer_digest,
      validationReceiptDigest: options.validationReceipt.receipt_digest,
    }),
    teacher_reference_digest: options.teacherReferenceDigest,
    teacher_validation_receipt_digest: options.teacherValidationReceiptDigest,
    teacher_oracle_coverage_receipt_digest: options.teacherOracleCoverageReceiptDigest,
    student_answer_digest: options.artifact.answer_digest,
    student_answer_artifact: options.artifact,
    validation_receipt: options.validationReceipt,
    may_call_tools: false,
    may_add_event_facts: false,
  })
}

function publishedAnswer(
  bindingDigest: string,
  payload: P2S1W5StudentAnswerPayload,
  alignedClaim: boolean,
): P2S1W5PublishedAnswer {
  const withoutDigest = {
    shared_answer_binding_digest: bindingDigest,
    claims: p2S1W5Clone(payload.claims),
    claims_digest: p2S1W5Digest(payload.claims),
    evidence_refs: p2S1W5Clone(payload.evidence_refs),
    limitations: p2S1W5Clone(payload.limitations),
    unknowns: p2S1W5Clone(payload.unknowns),
    aligned_claim: alignedClaim,
    event_causality_claimed: false as const,
    recovery_claimed: false as const,
  }
  return p2S1W5DeepFreeze({
    ...withoutDigest,
    answer_digest: p2S1W5Digest(withoutDigest),
  })
}

export interface P2S1W5CompositionRuntimeOptions {
  fixtures: P2S1W5TrustedFixtureCatalog
  modelPort: P2S1W5InjectedModelPort
  artifactStore: P2S1W5ArtifactStore
}

export class P2S1W5CompositionRuntime {
  readonly #completed = new Map<string, { requestDigest: string; result: P2S1W5RunResult }>()
  readonly #inflight = new Map<string, { requestDigest: string; promise: Promise<P2S1W5RunResult> }>()

  constructor(private readonly options: P2S1W5CompositionRuntimeOptions) {
    if (options.modelPort.mode !== 'trusted_fixture_replay') {
      throw new P2S1W5ContractError('external_provider_forbidden', 'W5 runtime 只允许 fixture replay port')
    }
  }

  async run(request: P2S1W5RunRequest): Promise<P2S1W5RunResult> {
    p2S1W5AssertNonempty(request.fixture_id, 'fixture_id')
    p2S1W5AssertNonempty(request.idempotency_key, 'idempotency_key')
    const requestDigest = p2S1W5Digest(request)
    const completed = this.#completed.get(request.idempotency_key)
    if (completed) {
      if (completed.requestDigest !== requestDigest) throw new P2S1W5ContractError('idempotency_conflict', '同一幂等键绑定了不同请求')
      return p2S1W5DeepFreeze(p2S1W5Clone(completed.result))
    }
    const inflight = this.#inflight.get(request.idempotency_key)
    if (inflight) {
      if (inflight.requestDigest !== requestDigest) throw new P2S1W5ContractError('idempotency_conflict', '并发幂等请求内容冲突')
      return p2S1W5DeepFreeze(p2S1W5Clone(await inflight.promise))
    }
    const promise = this.#runFresh(request)
    this.#inflight.set(request.idempotency_key, { requestDigest, promise })
    try {
      const result = await promise
      this.#completed.set(request.idempotency_key, { requestDigest, result })
      return p2S1W5DeepFreeze(p2S1W5Clone(result))
    } finally {
      this.#inflight.delete(request.idempotency_key)
    }
  }

  async #runFresh(request: P2S1W5RunRequest): Promise<P2S1W5RunResult> {
    const fixture = this.options.fixtures.resolve(request.fixture_id)
    if (request.degraded_authorization_id) return this.#runDegraded(request, fixture)
    const budget = new P2S1W5CallBudget()
    const planningInput = {
      role: 'teacher',
      run_phase: 'sol_planning',
      question_digest: fixture.binding.question_digest,
      goal_digest: fixture.binding.goal_digest,
      incident_id: fixture.binding.incident_id,
      publication_id: fixture.binding.publication_id,
      publication_revision: fixture.binding.publication_revision,
      collector_id: 'rrc25',
      prompt_digest: fixture.binding.prompt_digest,
      policy_digest: fixture.binding.policy_digest,
    }
    const planning = await runP2S1W5ModelPhase({
      port: this.options.modelPort,
      budget,
      fixtureId: fixture.fixture_id,
      phase: 'sol_planning',
      identity: fixture.teacher_identity,
      sharedAnswerBindingDigest: fixture.binding.question_digest,
      roleSpecificInput: asJson(planningInput),
    })
    if (planning.receipt.disposition !== 'completed' || !planning.output) {
      this.options.artifactStore.putModelRun(fixture.binding.question_digest, planning.receipt)
      return p2S1W5DeepFreeze({
        schema_version: 'country_outage_p2_s1_w5_run_result_v1',
        fixture_id: fixture.fixture_id,
        fixture_digest: fixture.fixture_digest,
        idempotency_key: request.idempotency_key,
        flow: null,
        planning_failure_receipt: planning.receipt,
        model_call_summary: callSummary(budget),
      })
    }
    const semanticPlan = validateP2S1TeacherSemanticPlan({
      value: planning.output,
      questionId: fixture.binding.question_id,
      questionDigest: fixture.binding.question_digest,
      goalDigest: fixture.binding.goal_digest,
      allowedCapabilityIds: fixture.allowed_capability_ids,
    })
    const grounding = groundP2S1TeacherSemanticPlan({
      semanticPlan,
      trustedPlan: fixture.grounding_plan,
      trustedGraph: fixture.evidence_graph,
    })
    const binding = buildSharedBinding({
      fixture,
      semanticPlan,
      groundingReceiptDigest: grounding.receipt.receipt_digest,
    })
    const bindingDigest = p2S1W5Digest(binding)
    const planningReceipt = finalizedRunReceipt(planning.receipt, {
      sharedBindingDigest: bindingDigest,
      outputDigest: semanticPlan.output_digest,
      validationReceiptDigest: grounding.receipt.receipt_digest,
    })
    this.options.artifactStore.putModelRun(bindingDigest, planningReceipt)
    this.options.artifactStore.putValidatedPlan(
      bindingDigest,
      grounding.plan,
      hostArtifactValidation(grounding.plan.investigation_plan_digest, 'country_outage_p2_validated_plan_instance_validator'),
    )
    this.options.artifactStore.putCommittedGraph(
      bindingDigest,
      grounding.graph,
      hostArtifactValidation(grounding.graph.evidence_graph_digest, 'country_outage_p2_committed_evidence_graph_validator'),
    )
    const flowId = `dual-flow:${randomUUID()}`
    const base = flowBase({ fixture, binding, flowId })
    const referenceInput = {
      role: 'teacher',
      run_phase: 'sol_reference',
      shared_answer_binding_digest: bindingDigest,
    }
    const referenceAttempt = await runP2S1W5ModelPhase({
      port: this.options.modelPort,
      budget,
      fixtureId: fixture.fixture_id,
      phase: 'sol_reference',
      identity: fixture.teacher_identity,
      sharedAnswerBindingDigest: bindingDigest,
      roleSpecificInput: asJson(referenceInput),
    })
    if (referenceAttempt.receipt.disposition !== 'completed' || !referenceAttempt.output) {
      const teacherRun = finalizedRunReceipt(referenceAttempt.receipt, { sharedBindingDigest: bindingDigest })
      this.options.artifactStore.putModelRun(bindingDigest, teacherRun)
      const flow: P2S1W5DualModelFlow = {
        ...base,
        flow_state: 'stopped_waiting_teacher',
        effective_teacher_required: true,
        teacher_plan_run_receipt: planningReceipt,
        teacher_plan_grounding_receipt: grounding.receipt,
        teacher_run_receipt: teacherRun,
        teacher_reference: null,
        teacher_validation_receipt: null,
        teacher_oracle_coverage_receipt: null,
        student_runs: [],
        structured_feedback: null,
        student_validation_receipt: null,
        alignment_run_receipt: null,
        degraded_authorization: null,
        teacher_unavailable_phase: 'sol_reference',
        final_disposition: 'teacher_unavailable',
        published_answer: null,
        publish_receipt_digest: null,
      }
      return this.#finish(request, fixture, budget, flow)
    }
    const reference = validateP2S1W5TeacherReference({
      value: referenceAttempt.output,
      sharedAnswerBindingDigest: bindingDigest,
    })
    const teacherValidation = validateP2S1W5TeacherGates({
      reference,
      graph: grounding.graph,
      sharedAnswerBindingDigest: bindingDigest,
      prohibitedAssertions: fixture.oracle_seed.prohibited_assertions,
    })
    const teacherRun = finalizedRunReceipt(referenceAttempt.receipt, {
      sharedBindingDigest: bindingDigest,
      outputDigest: reference.output_digest,
      validationReceiptDigest: teacherValidation.receipt_digest,
    })
    this.options.artifactStore.putModelRun(bindingDigest, teacherRun)
    this.options.artifactStore.putGate(bindingDigest, teacherValidation)
    if (!teacherValidation.all_gates_passed) {
      const flow: P2S1W5DualModelFlow = {
        ...base,
        flow_state: 'teacher_rejected',
        effective_teacher_required: true,
        teacher_plan_run_receipt: planningReceipt,
        teacher_plan_grounding_receipt: grounding.receipt,
        teacher_run_receipt: teacherRun,
        teacher_reference: reference,
        teacher_validation_receipt: teacherValidation,
        teacher_oracle_coverage_receipt: null,
        student_runs: [],
        structured_feedback: null,
        student_validation_receipt: null,
        alignment_run_receipt: null,
        degraded_authorization: null,
        teacher_unavailable_phase: 'none',
        final_disposition: 'teacher_rejected',
        published_answer: null,
        publish_receipt_digest: null,
      }
      return this.#finish(request, fixture, budget, flow)
    }
    const oracle = materializeP2S1W5QuestionOracle(fixture.oracle_seed)
    this.options.artifactStore.putOracle(bindingDigest, oracle)
    const coverage = evaluateP2S1W5TeacherOracleCoverage({
      oracle,
      reference,
      sharedAnswerBindingDigest: bindingDigest,
      prohibitedAssertionTexts: fixture.oracle_seed.prohibited_assertions,
    })
    if (coverage.disposition === 'rejected') {
      const flow: P2S1W5DualModelFlow = {
        ...base,
        flow_state: 'teacher_rejected',
        effective_teacher_required: true,
        teacher_plan_run_receipt: planningReceipt,
        teacher_plan_grounding_receipt: grounding.receipt,
        teacher_run_receipt: teacherRun,
        teacher_reference: reference,
        teacher_validation_receipt: teacherValidation,
        teacher_oracle_coverage_receipt: coverage,
        student_runs: [],
        structured_feedback: null,
        student_validation_receipt: null,
        alignment_run_receipt: null,
        degraded_authorization: null,
        teacher_unavailable_phase: 'none',
        final_disposition: 'teacher_rejected',
        published_answer: null,
        publish_receipt_digest: null,
      }
      return this.#finish(request, fixture, budget, flow)
    }
    const studentRuns: P2S1W5StudentRun[] = []
    const first = await this.#studentAttempt({
      fixture,
      budget,
      bindingDigest,
      phase: 'ds_first_answer',
      revisionOrdinal: 0,
      graph: grounding.graph,
      oracle,
      reference,
      teacherValidationReceiptDigest: teacherValidation.receipt_digest,
      coverageReceiptDigest: coverage.receipt_digest,
      structuredFeedbackDigest: null,
    })
    if (!first) throw new P2S1W5ContractError('student_first_unavailable', 'DS first 未返回可验证制品')
    studentRuns.push(first.run)
    let selected = first
    let feedback = null
    if (!first.evaluation.receipt.all_gates_passed && fixture.scripted_outputs.ds_revision !== undefined) {
      feedback = createP2S1W5StructuredFeedback({
        answerDigest: first.run.student_answer_digest,
        validationReceipt: first.evaluation.receipt,
        differences: first.evaluation.differences,
      })
      const revision = await this.#studentAttempt({
        fixture,
        budget,
        bindingDigest,
        phase: 'ds_revision',
        revisionOrdinal: 1,
        graph: grounding.graph,
        oracle,
        reference,
        teacherValidationReceiptDigest: teacherValidation.receipt_digest,
        coverageReceiptDigest: coverage.receipt_digest,
        structuredFeedbackDigest: feedback.feedback_digest,
      })
      if (revision) {
        studentRuns.push(revision.run)
        selected = revision
      }
    }
    if (!selected.evaluation.receipt.all_gates_passed) {
      const flow: P2S1W5DualModelFlow = {
        ...base,
        flow_state: 'failed',
        effective_teacher_required: true,
        teacher_plan_run_receipt: planningReceipt,
        teacher_plan_grounding_receipt: grounding.receipt,
        teacher_run_receipt: teacherRun,
        teacher_reference: reference,
        teacher_validation_receipt: teacherValidation,
        teacher_oracle_coverage_receipt: coverage,
        student_runs: studentRuns,
        structured_feedback: studentRuns.length === 2 ? feedback : null,
        student_validation_receipt: selected.evaluation.receipt,
        alignment_run_receipt: null,
        degraded_authorization: null,
        teacher_unavailable_phase: 'none',
        final_disposition: 'student_rejected',
        published_answer: null,
        publish_receipt_digest: null,
      }
      return this.#finish(request, fixture, budget, flow)
    }
    const alignment = evaluateP2S1W5Alignment({
      questionId: binding.question_id,
      sharedAnswerBindingDigest: bindingDigest,
      graph: grounding.graph,
      oracle,
      teacherReference: reference,
      coverageReceipt: coverage,
      studentArtifact: selected.run.student_answer_artifact,
      store: this.options.artifactStore,
    })
    if (alignment.disposition === 'rejected') {
      const flow: P2S1W5DualModelFlow = {
        ...base,
        flow_state: 'alignment_failed',
        effective_teacher_required: true,
        teacher_plan_run_receipt: planningReceipt,
        teacher_plan_grounding_receipt: grounding.receipt,
        teacher_run_receipt: teacherRun,
        teacher_reference: reference,
        teacher_validation_receipt: teacherValidation,
        teacher_oracle_coverage_receipt: coverage,
        student_runs: studentRuns,
        structured_feedback: studentRuns.length === 2 ? feedback : null,
        student_validation_receipt: selected.evaluation.receipt,
        alignment_run_receipt: alignment,
        degraded_authorization: null,
        teacher_unavailable_phase: 'none',
        final_disposition: 'alignment_rejected',
        published_answer: null,
        publish_receipt_digest: null,
      }
      return this.#finish(request, fixture, budget, flow)
    }
    const answer = publishedAnswer(bindingDigest, selected.payload, true)
    const publishWithoutDigest = {
      shared_answer_binding_digest: bindingDigest,
      answer_digest: answer.answer_digest,
      student_answer_digest: selected.run.student_answer_digest,
      alignment_receipt_digest: alignment.receipt_digest,
    }
    const publishReceiptDigest = p2S1W5Digest(publishWithoutDigest)
    this.options.artifactStore.putPublish(bindingDigest, asJson({
      ...publishWithoutDigest,
      receipt_digest: publishReceiptDigest,
    }))
    const flow: P2S1W5DualModelFlow = {
      ...base,
      flow_state: 'published',
      effective_teacher_required: true,
      teacher_plan_run_receipt: planningReceipt,
      teacher_plan_grounding_receipt: grounding.receipt,
      teacher_run_receipt: teacherRun,
      teacher_reference: reference,
      teacher_validation_receipt: teacherValidation,
      teacher_oracle_coverage_receipt: coverage,
      student_runs: studentRuns,
      structured_feedback: studentRuns.length === 2 ? feedback : null,
      student_validation_receipt: selected.evaluation.receipt,
      alignment_run_receipt: alignment,
      degraded_authorization: null,
      teacher_unavailable_phase: 'none',
      final_disposition: 'aligned_published',
      published_answer: answer,
      publish_receipt_digest: publishReceiptDigest,
    }
    return this.#finish(request, fixture, budget, flow)
  }

  async #studentAttempt(options: {
    fixture: P2S1W5TrustedReplayFixture
    budget: P2S1W5CallBudget
    bindingDigest: string
    phase: 'ds_first_answer' | 'ds_revision'
    revisionOrdinal: 0 | 1
    graph: P2S1W5TrustedReplayFixture['evidence_graph']
    oracle: ReturnType<typeof materializeP2S1W5QuestionOracle>
    reference: ReturnType<typeof validateP2S1W5TeacherReference>
    teacherValidationReceiptDigest: string
    coverageReceiptDigest: string
    structuredFeedbackDigest: string | null
  }): Promise<{
    run: P2S1W5StudentRun
    payload: P2S1W5StudentAnswerPayload
    evaluation: ReturnType<typeof validateP2S1W5StudentGates>
  } | null> {
    const input = {
      role: 'student',
      run_phase: options.phase,
      revision_ordinal: options.revisionOrdinal,
      shared_answer_binding_digest: options.bindingDigest,
      teacher_reference_digest: options.reference.output_digest,
      teacher_validation_receipt_digest: options.teacherValidationReceiptDigest,
      teacher_oracle_coverage_receipt_digest: options.coverageReceiptDigest,
      structured_feedback_digest: options.structuredFeedbackDigest,
    }
    const attempt = await runP2S1W5ModelPhase({
      port: this.options.modelPort,
      budget: options.budget,
      fixtureId: options.fixture.fixture_id,
      phase: options.phase,
      identity: options.fixture.student_identity,
      sharedAnswerBindingDigest: options.bindingDigest,
      roleSpecificInput: asJson(input),
    })
    this.options.artifactStore.putModelRun(options.bindingDigest, attempt.receipt)
    if (attempt.receipt.disposition !== 'completed' || !attempt.output) return null
    const payload = validateP2S1W5StudentAnswerPayload(attempt.output)
    const artifact = this.options.artifactStore.putStudentAnswer(options.bindingDigest, payload)
    const evaluation = validateP2S1W5StudentGates({
      payload,
      answerDigest: artifact.answer_digest,
      graph: options.graph,
      oracle: options.oracle,
      teacherReference: options.reference,
      sharedAnswerBindingDigest: options.bindingDigest,
      prohibitedAssertions: options.fixture.oracle_seed.prohibited_assertions,
    })
    this.options.artifactStore.putGate(options.bindingDigest, evaluation.receipt)
    const run = createStudentRun({
      revisionOrdinal: options.revisionOrdinal,
      runReceipt: finalizedRunReceipt(attempt.receipt, { sharedBindingDigest: options.bindingDigest }),
      artifact,
      validationReceipt: evaluation.receipt,
      teacherReferenceDigest: options.reference.output_digest,
      teacherValidationReceiptDigest: options.teacherValidationReceiptDigest,
      teacherOracleCoverageReceiptDigest: options.coverageReceiptDigest,
    })
    this.options.artifactStore.putModelRun(options.bindingDigest, run.run_receipt)
    return { run, payload, evaluation }
  }

  async #runDegraded(
    request: P2S1W5RunRequest,
    fixture: P2S1W5TrustedReplayFixture,
  ): Promise<P2S1W5RunResult> {
    const authorization = fixture.degraded_authorization
    const binding = fixture.degraded_binding
    if (
      !authorization
      || !binding
      || request.degraded_authorization_id !== authorization.authorization_id
      || authorization.user_confirmed !== true
      || authorization.new_plan_revision <= authorization.parent_plan_revision
      || binding.plan_revision !== authorization.new_plan_revision
      || fixture.grounding_plan.plan_revision !== authorization.new_plan_revision
      || fixture.grounding_plan.degraded_authorization_digest !== authorization.authorization_digest
      || fixture.grounding_plan.effective_teacher_required !== false
      || binding.investigation_plan_digest !== fixture.grounding_plan.investigation_plan_digest
      || binding.evidence_graph_digest !== fixture.evidence_graph.evidence_graph_digest
      || binding.registry_snapshot_digest !== fixture.evidence_graph.registry_snapshot_digest
    ) throw new P2S1W5ContractError('degraded_authorization_denied', '降级必须绑定受信授权、新计划 revision 与新证据图')
    const bindingDigest = p2S1W5Digest(binding)
    const budget = new P2S1W5CallBudget(true)
    const oracle = materializeP2S1W5QuestionOracle(fixture.oracle_seed)
    this.options.artifactStore.putValidatedPlan(
      bindingDigest,
      fixture.grounding_plan,
      hostArtifactValidation(fixture.grounding_plan.investigation_plan_digest, 'country_outage_p2_validated_plan_instance_validator'),
    )
    this.options.artifactStore.putCommittedGraph(
      bindingDigest,
      fixture.evidence_graph,
      hostArtifactValidation(fixture.evidence_graph.evidence_graph_digest, 'country_outage_p2_committed_evidence_graph_validator'),
    )
    this.options.artifactStore.putOracle(bindingDigest, oracle)
    const attempt = await runP2S1W5ModelPhase({
      port: this.options.modelPort,
      budget,
      fixtureId: fixture.fixture_id,
      phase: 'ds_first_answer',
      identity: fixture.student_identity,
      sharedAnswerBindingDigest: bindingDigest,
      roleSpecificInput: asJson({
        role: 'student',
        run_phase: 'ds_first_answer',
        revision_ordinal: 0,
        shared_answer_binding_digest: bindingDigest,
        teacher_reference_digest: null,
        teacher_validation_receipt_digest: null,
        teacher_oracle_coverage_receipt_digest: null,
        structured_feedback_digest: null,
        degraded_authorization_digest: authorization.authorization_digest,
      }),
    })
    if (attempt.receipt.disposition !== 'completed' || !attempt.output) {
      throw new P2S1W5ContractError('degraded_student_unavailable', '显式降级 DS 未返回可验证答案')
    }
    const payload = validateP2S1W5StudentAnswerPayload(attempt.output)
    const artifact = this.options.artifactStore.putStudentAnswer(bindingDigest, payload)
    const evaluation = validateP2S1W5StudentGates({
      payload,
      answerDigest: artifact.answer_digest,
      graph: fixture.evidence_graph,
      oracle,
      teacherReference: null,
      sharedAnswerBindingDigest: bindingDigest,
      prohibitedAssertions: fixture.oracle_seed.prohibited_assertions,
    })
    if (!evaluation.receipt.all_gates_passed) {
      throw new P2S1W5ContractError('degraded_student_rejected', '降级答案未通过五 Gate')
    }
    const run = createStudentRun({
      revisionOrdinal: 0,
      runReceipt: finalizedRunReceipt(attempt.receipt, { sharedBindingDigest: bindingDigest }),
      artifact,
      validationReceipt: evaluation.receipt,
      teacherReferenceDigest: null,
      teacherValidationReceiptDigest: null,
      teacherOracleCoverageReceiptDigest: null,
    })
    this.options.artifactStore.putModelRun(bindingDigest, run.run_receipt)
    this.options.artifactStore.putGate(bindingDigest, evaluation.receipt)
    const answer = publishedAnswer(bindingDigest, payload, false)
    const publishReceiptDigest = p2S1W5Digest({
      shared_answer_binding_digest: bindingDigest,
      answer_digest: answer.answer_digest,
      student_answer_digest: run.student_answer_digest,
      degraded_authorization_digest: authorization.authorization_digest,
    })
    this.options.artifactStore.putPublish(bindingDigest, asJson({
      shared_answer_binding_digest: bindingDigest,
      answer_digest: answer.answer_digest,
      student_answer_digest: run.student_answer_digest,
      degraded_authorization_digest: authorization.authorization_digest,
      receipt_digest: publishReceiptDigest,
    }))
    const flow: P2S1W5DualModelFlow = {
      ...flowBase({
        fixture,
        binding,
        flowId: `dual-flow:${randomUUID()}`,
        flowRevision: 2,
        parentFlowRevision: 1,
      }),
      flow_state: 'degraded_published',
      effective_teacher_required: false,
      teacher_plan_run_receipt: null,
      teacher_plan_grounding_receipt: null,
      teacher_run_receipt: null,
      teacher_reference: null,
      teacher_validation_receipt: null,
      teacher_oracle_coverage_receipt: null,
      student_runs: [run],
      structured_feedback: null,
      student_validation_receipt: evaluation.receipt,
      alignment_run_receipt: null,
      degraded_authorization: authorization,
      teacher_unavailable_phase: 'none',
      final_disposition: 'ds_unaligned_degraded',
      published_answer: answer,
      publish_receipt_digest: publishReceiptDigest,
    }
    return this.#finish(request, fixture, budget, flow)
  }

  #finish(
    request: P2S1W5RunRequest,
    fixture: P2S1W5TrustedReplayFixture,
    budget: P2S1W5CallBudget,
    flow: P2S1W5DualModelFlow,
  ): P2S1W5RunResult {
    p2S1W5ValidateTerminalClosure(flow)
    this.options.artifactStore.putFlow(flow.shared_answer_binding_digest, flow)
    return p2S1W5DeepFreeze({
      schema_version: 'country_outage_p2_s1_w5_run_result_v1',
      fixture_id: fixture.fixture_id,
      fixture_digest: fixture.fixture_digest,
      idempotency_key: request.idempotency_key,
      flow,
      planning_failure_receipt: null,
      model_call_summary: callSummary(budget),
    })
  }
}
