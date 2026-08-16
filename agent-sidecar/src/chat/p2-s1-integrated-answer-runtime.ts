import {
  P2S1W5ContractError,
  P2S1_W5_FROZEN_STUDENT_IDENTITY,
  p2S1W5Digest,
  p2S1W5Identity,
  type P2S1Json,
  type P2S1W5TrustedReplayFixture,
} from './p2-s1-composition-contracts.js'
import { P2S1W5CompositionRuntime } from './p2-s1-composition-runtime.js'
import { P2S1W5ArtifactStore } from './p2-s1-dual-artifact-store.js'
import {
  InMemoryP2S1W5TrustedFixtureCatalog,
  ReplayOnlyP2S1W5ModelPort,
} from './p2-s1-model-runner.js'
import { materializeP2S1W5QuestionOracle } from './p2-s1-oracle-materializer.js'
import {
  createP2S1W5CommittedEvidenceGraphRecord,
  createP2S1W5GroundingPlanRecord,
} from './p2-s1-teacher-plan-grounder.js'

const TEACHER_IDENTITY = p2S1W5Identity({
  provider: 'openai', model: 'gpt-5.6-sol', version: 'gpt-5.6-sol-fixture-replay-v1',
  expected_response_model: 'gpt-5.6-sol',
})

const prefixed = (value: unknown): string => `sha256:${p2S1W5Digest(value)}`

function record(value: unknown, label: string): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new P2S1W5ContractError('invalid_request', `${label} 必须是对象`)
  return value as Record<string, unknown>
}

function strings(value: unknown, label: string): string[] {
  if (!Array.isArray(value) || value.length === 0 || value.some((item) => typeof item !== 'string' || !item)) throw new P2S1W5ContractError('invalid_request', `${label} 必须是非空字符串数组`)
  return [...value] as string[]
}

export class P2S1W5IntegratedAnswerRuntime {
  constructor(private readonly artifactStoreRoot: string) {}

  async run(envelope: Record<string, unknown>): Promise<Record<string, unknown>> {
    const request = record(envelope.request, 'request')
    const identity = record(request.identity, 'identity')
    const binding = record(request.shared_answer_binding, 'shared_answer_binding')
    const planRef = record(request.plan_ref, 'plan_ref')
    const graphRef = record(request.evidence_graph_ref, 'evidence_graph_ref')
    const oracleInput = record(request.host_oracle, 'host_oracle')
    const hostFacts = request.host_graph_facts
    if (!Array.isArray(hostFacts) || hostFacts.length === 0) throw new P2S1W5ContractError('graph_fact_missing', 'Host Graph facts 为空')
    const firstFact = record(hostFacts[0], 'graph fact')
    const boundaryTexts = strings(oracleInput.boundary_texts, 'boundary_texts')
    const limitationTexts = strings(oracleInput.limitations, 'limitations')
    const unknownTexts = strings(oracleInput.unknown_texts, 'unknown_texts')
    const question = String(request.question)
    const questionId = 'Q01'
    const oracleSeed = {
      question_id: questionId,
      required_fact_ids: [String(firstFact.fact_id)],
      required_boundary_assertions: boundaryTexts,
      allowed_boundary_assertions: [],
      required_unknowns: unknownTexts,
      prohibited_assertions: strings(oracleInput.prohibited_claim_patterns, 'prohibited_claim_patterns'),
    }
    const oracle = materializeP2S1W5QuestionOracle(oracleSeed)
    if (
      JSON.stringify(oracle.required_boundary_assertion_ids) !== JSON.stringify(oracleInput.boundary_assertions)
      || JSON.stringify(oracle.required_unknown_ids) !== JSON.stringify(oracleInput.unknowns)
    ) throw new P2S1W5ContractError('host_oracle_projection_drift', 'Host Oracle IDs 与 Sidecar materializer 不一致')
    const plan = createP2S1W5GroundingPlanRecord({
      plan_id: String(planRef.plan_id), plan_revision: Number(planRef.plan_revision),
      admitted_capability_ids: ['CAP-P2-INTEGRATED-ANSWER'],
      registry_snapshot_id: String(identity.registry_snapshot_id),
      registry_snapshot_digest: String(identity.registry_snapshot_digest).replace(/^sha256:/, ''),
      effective_teacher_required: true, degraded_authorization_digest: null,
    })
    const graph = createP2S1W5CommittedEvidenceGraphRecord({
      graph_id: String(graphRef.graph_id), graph_revision: Number(graphRef.graph_revision),
      investigation_plan_digest: plan.investigation_plan_digest,
      registry_snapshot_id: plan.registry_snapshot_id,
      registry_snapshot_digest: plan.registry_snapshot_digest,
      facts: hostFacts.map((item) => {
        const fact = record(item, 'graph fact')
        return {
          fact_id: String(fact.fact_id), source_node_id: String(fact.source_node_id),
          source_value_digest: String(fact.source_value_digest), evidence_refs: strings(fact.evidence_refs, 'fact.evidence_refs'),
        }
      }),
      boundary_assertion_ids: oracle.allowed_boundary_assertion_ids,
      unknown_ids: oracle.required_unknown_ids,
    })
    const bindingBase = {
      question_id: questionId, question, question_digest: p2S1W5Digest(question),
      goal: question, goal_digest: p2S1W5Digest(question),
      incident_id: String(identity.incident_id), publication_id: String(identity.publication_id),
      publication_revision: Number(identity.publication_revision), publication_digest: String(identity.publication_digest).replace(/^sha256:/, ''),
      cohort_id: String(identity.cohort_id), cohort_digest: String(identity.cohort_digest).replace(/^sha256:/, ''),
      window_start_utc: String(identity.window_start_utc), window_end_utc: String(identity.window_end_utc),
      data_through_utc: String(identity.data_through_utc), finality: identity.finality as 'event_end_unknown' | 'event_end_known',
      binding_generation: Number(identity.binding_generation), boundary_policy_digest: p2S1W5Digest(oracleInput.oracle_digest),
      prompt_version: 'p2-s1-w5-integrated-fixture-v1', prompt_digest: p2S1W5Digest('p2-s1-w5-integrated-fixture-v1'),
      policy_version: 'p2-s1-w5-host-oracle-v1', policy_digest: p2S1W5Digest(oracleInput),
    }
    const semanticPlanBase = {
      plan_id: String(planRef.plan_id), question_id: questionId,
      question_digest: bindingBase.question_digest, goal_digest: bindingBase.goal_digest,
      subgoals: [{ subgoal_id: 'integrated-answer', capability_id: 'CAP-P2-INTEGRATED-ANSWER' }],
      ambiguity_ids: [], tool_selection_authority: false as const, executable_plan: false as const,
    }
    const semanticPlan = { ...semanticPlanBase, output_digest: p2S1W5Digest(semanticPlanBase) }
    const claim = {
      claim_id: `claim-sha256:${p2S1W5Digest({ binding: request.shared_answer_binding_digest, fact_id: firstFact.fact_id })}`,
      claim_kind: String(firstFact.claim_kind), claim_relation: String(firstFact.claim_relation),
      text: String(firstFact.allowed_claim_text),
      fact_ids: [String(firstFact.fact_id)], source_node_ids: [String(firstFact.source_node_id)],
      source_value_digests: [String(firstFact.source_value_digest)], evidence_refs: strings(firstFact.evidence_refs, 'fact evidence'),
      boundary_assertion_ids: oracle.required_boundary_assertion_ids, verification_requirements: [],
    }
    const answer = {
      claims: [claim], evidence_refs: claim.evidence_refs, limitations: limitationTexts,
      unknowns: oracle.required_unknown_ids,
      answer_text: [claim.text, ...limitationTexts, ...unknownTexts].join(' '),
    }
    const teacherReferenceBase = {
      teacher_reference_id: 'teacher-reference-integrated', shared_answer_binding_digest: '$W5_SHARED_ANSWER_BINDING_DIGEST',
      required_fact_ids: [String(firstFact.fact_id)], evidence_refs: claim.evidence_refs,
      boundary_assertions: oracle.required_boundary_assertion_ids, unknowns: oracle.required_unknown_ids,
      answer_outline: ['仅报告绑定 Graph fact，并保留 Host Oracle 边界与未知项'],
      teacher_reference_is_ground_truth: false as const, private_chain_of_thought_persisted: false as const,
    }
    const teacherReference = { ...teacherReferenceBase, output_digest: '$W5_RECOMPUTE_OUTPUT_DIGEST' }
    const fixtureWithoutDigest = {
      fixture_id: String(envelope.fixture_id), binding: bindingBase,
      teacher_identity: TEACHER_IDENTITY, student_identity: P2S1_W5_FROZEN_STUDENT_IDENTITY,
      allowed_capability_ids: ['CAP-P2-INTEGRATED-ANSWER'], grounding_plan: plan, evidence_graph: graph,
      oracle_seed: oracleSeed,
      scripted_outputs: { sol_planning: semanticPlan, sol_reference: teacherReference, ds_first_answer: answer },
      unavailable_phases: [], force_alignment_rejection: false,
      degraded_authorization: null, degraded_binding: null,
    }
    const fixture: P2S1W5TrustedReplayFixture = {
      ...fixtureWithoutDigest, fixture_digest: p2S1W5Digest(fixtureWithoutDigest),
    }
    const catalog = new InMemoryP2S1W5TrustedFixtureCatalog([fixture])
    const runtime = new P2S1W5CompositionRuntime({
      fixtures: catalog, modelPort: new ReplayOnlyP2S1W5ModelPort(catalog),
      artifactStore: new P2S1W5ArtifactStore(this.artifactStoreRoot),
    })
    const result = await runtime.run({ fixture_id: fixture.fixture_id, idempotency_key: String(envelope.idempotency_key) })
    const flow = result.flow
    if (!flow || flow.final_disposition !== 'aligned_published' || !flow.published_answer) throw new P2S1W5ContractError('integrated_flow_not_publishable', '真实 CompositionRuntime flow 未发布')
    const selected = flow.student_runs.find((item) => item.validation_receipt.receipt_digest === flow.student_validation_receipt?.receipt_digest)
    if (!selected) throw new P2S1W5ContractError('integrated_student_artifact_missing', '发布 flow 缺少选中 Student artifact')
    const answerBase = selected.student_answer_artifact.answer_payload
    const answerPayload = { ...answerBase, answer_payload_digest: prefixed(answerBase) }
    const sourceModelReceipts = [flow.teacher_plan_run_receipt, flow.teacher_run_receipt, selected.run_receipt]
    if (sourceModelReceipts.some((item) => !item)) throw new P2S1W5ContractError('integrated_model_receipt_missing', 'CompositionRuntime model receipts 不闭合')
    const modelRoles = ['sol-planning', 'sol-teacher', 'ds-student']
    const modelReceipts = sourceModelReceipts.map((source, index) => {
      const sourceDigest = p2S1W5Digest(source)
      const base = { receipt_kind: 'composition_model_run', model_role: modelRoles[index], model_identity: JSON.stringify(source!.exact_model_identity), shared_answer_binding_digest: request.shared_answer_binding_digest, subject_digest: index === 2 ? answerPayload.answer_payload_digest : request.question_digest, source_composition_receipt_digest: `sha256:${sourceDigest}`, external_provider_called: false, fixture_replay_only: true, disposition: 'passed' }
      return { ...base, receipt_digest: prefixed(base) }
    })
    const gateSources = [flow.teacher_plan_grounding_receipt, flow.teacher_validation_receipt, flow.student_validation_receipt, flow.alignment_run_receipt]
    const gateKinds = ['composition_plan_grounding_gate', 'composition_teacher_gate', 'composition_student_gate', 'composition_alignment_gate']
    const gateSubjects = [request.shared_answer_binding_digest, graphRef.graph_digest, oracleInput.oracle_digest, answerPayload.answer_payload_digest]
    const gateReceipts = gateSources.map((source, index) => {
      if (!source) throw new P2S1W5ContractError('integrated_gate_receipt_missing', 'CompositionRuntime gate receipts 不闭合')
      const base = { receipt_kind: gateKinds[index], model_role: 'sidecar-host', model_identity: 'country_outage_p2_s1_w5_composition_runtime', shared_answer_binding_digest: request.shared_answer_binding_digest, subject_digest: gateSubjects[index], source_composition_receipt_digest: prefixed(source), external_provider_called: false, fixture_replay_only: true, disposition: 'passed' }
      return { ...base, receipt_digest: prefixed(base) }
    })
    return {
      schema_version: 'country_outage_p2_s1_w5_model_turn_v1', fixture_id: envelope.fixture_id,
      fixture_digest: envelope.fixture_digest, shared_answer_binding: binding,
      shared_answer_binding_digest: request.shared_answer_binding_digest,
      answer_payload: answerPayload, model_receipts: modelReceipts, gate_receipts: gateReceipts,
      execution_order: flow.execution_order,
      integration_binding: {
        investigation_id: request.investigation_id, source_investigation_revision: request.source_investigation_revision,
        source_current_digest: request.source_current_digest, plan_digest: planRef.plan_digest,
        result_set_refs_digest: prefixed(request.result_set_refs), graph_digest: graphRef.graph_digest,
        oracle_digest: oracleInput.oracle_digest, binding_generation: identity.binding_generation,
      },
      external_provider_called: false, fixture_replay_only: true, runtime_integrated: true, production_deployed: false,
    }
  }
}
