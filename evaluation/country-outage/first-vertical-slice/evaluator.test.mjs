import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { mkdtempSync, readFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import test, { after } from 'node:test'
import { fileURLToPath } from 'node:url'

import {
  DEFAULT_J1_RUNS,
  REGISTERED_JOURNEY_CASES,
  ZERO_TOLERANCE_KEYS,
  bindRealFirstSliceEvaluationTarget,
  finalizeIndependentAcceptanceRecord,
  runFirstVerticalSliceEvaluation,
  writeEvaluationArtifacts,
} from './evaluator.mjs'
const {
  DOMEYE_FIRST_SLICE_QUESTION,
  DomeyeFirstSliceRunError,
} = await import(
  '../../../agent-sidecar/src/agent/first-slice-runtime.ts'
)
const {
  DomeyeCapabilityGateway,
} = await import(
  '../../../agent-sidecar/src/agent/capability-execution.ts'
)
const {
  DomeyeTrustKernel,
} = await import('../../../agent-sidecar/src/agent/trust-kernel.ts')
const {
  buildCountryOutageAnswerContext,
  buildCountryOutageSeriesExtremaFinding,
  guardCountryOutageResponse,
  renderCountryOutageDeterministicFallback,
} = await import(
  '../../../agent-sidecar/src/agent/finding-answer.ts'
)
const { canonicalJsonSha256 } = await import(
  '../../../agent-sidecar/src/shared/deterministic-json.ts'
)

const roots = []
after(() => {
  for (const root of roots) rmSync(root, { recursive: true, force: true })
})

const sha = (character) => `sha256:${character.repeat(64)}`
const candidateId = `manifest:sha256:${'c'.repeat(64)}`
const driverNow = '2026-08-19T08:00:00.000Z'

const dataIdentity = {
  event_type: 'country_outage',
  incident_id: 'incident-evaluation-offline',
  publication_id: 'publication-evaluation-offline',
  revision: 1,
  collector_id: 'rrc25',
  cohort_id: 'cohort-evaluation-offline',
  country_code: 'IR',
  window_start_utc: '2026-02-27T00:10:00Z',
  window_end_utc: '2026-03-11T00:00:00Z',
  data_through: '2026-03-11T00:00:00Z',
  is_final_in_data_range: false,
  lifecycle_state: 'event_end_unknown',
}

const modelIdentity = {
  candidate_id: 'model-evaluation-offline',
  resource_sha256: sha('a'),
  provider: 'provider-evaluation-offline',
  model: 'model-evaluation-offline',
  model_version: 'model-evaluation-offline-20260819',
  expected_response_model: 'model-evaluation-offline',
  api: 'openai-completions',
  base_url: 'https://provider.invalid/v1',
  maximum_output_tokens: 4_096,
  thinking_level: 'off',
  pi_version: '0.84.1',
}

const readerBinding = {
  execution_unit_id: 'TOOL-03',
  execution_unit_name: 'read_metric_series',
  execution_unit_version: '1.0.0',
  contract_digest: sha('5'),
  implementation_digest: sha('6'),
  semantic_digest: sha('7'),
}

const extremaBinding = {
  execution_unit_id: 'OP-01',
  execution_unit_name: 'series_extrema',
  execution_unit_version: '1.0.0',
  contract_digest: sha('8'),
  implementation_digest: sha('9'),
  semantic_digest: sha('b'),
}

const budgetPolicy = {
  model_api_attempt_limit: 10,
  approved_action_limit: 2,
  cost_policy: 'audit_only',
  monetary_limit_usd: null,
}

const policy = {
  policy_id: 'policy-evaluation-offline',
  policy_digest: sha('3'),
  state: 'active',
  allowed_capability_ids: ['CAP-006', 'CAP-016'],
}

const registry = {
  registry_snapshot_id: 'registry-evaluation-offline',
  registry_digest: sha('4'),
  state: 'active',
  capabilities: [
    { capability_id: 'CAP-006', state: 'active', execution_binding: readerBinding },
    { capability_id: 'CAP-016', state: 'active', execution_binding: extremaBinding },
  ],
}

const manifestPayload = {
  schema_version: 'domeye_first_slice_candidate_manifest_v1',
  base_commit: 'a'.repeat(40),
  contract: {
    version: 'domeye.first-vertical-slice/v1.0',
    digest: sha('1'),
  },
  data_identity: dataIdentity,
  series_response_sha256: sha('2'),
  model: modelIdentity,
  budget_policy: budgetPolicy,
  policy,
  registry,
  source_files: [
    { path: 'src/reader.ts', sha256: readerBinding.implementation_digest },
    { path: 'src/extrema.ts', sha256: extremaBinding.implementation_digest },
  ],
  activation: { scope: 'local_evaluation_only', production_deployed: false },
}

const loadedCandidate = {
  candidate: {
    candidate_id: candidateId,
    contract_version: manifestPayload.contract.version,
    contract_digest: manifestPayload.contract.digest,
    data_identity: dataIdentity,
    series_response_sha256: manifestPayload.series_response_sha256,
    model_identity: modelIdentity,
    budget_policy: budgetPolicy,
    policy,
    registry,
  },
  model_identity: modelIdentity,
  manifest: { candidate_id: candidateId, payload: manifestPayload },
}

function zeroToleranceCounts(overrides = {}) {
  return Object.fromEntries(
    ZERO_TOLERANCE_KEYS.map((key) => [key, overrides[key] ?? 0]),
  )
}

function providerAttempt(attemptId, phase) {
  const startedAt = new Date(
    Date.parse('2026-08-19T07:00:00.000Z') + attemptId * 1_000,
  )
  const endedAt = new Date(startedAt.valueOf() + 100)
  return {
    attempt_id: attemptId,
    phase,
    provider: modelIdentity.provider,
    model: modelIdentity.model,
    model_version: modelIdentity.model_version,
    expected_response_model: modelIdentity.expected_response_model,
    response_model: modelIdentity.expected_response_model,
    started_at_utc: startedAt.toISOString(),
    ended_at_utc: endedAt.toISOString(),
    latency_ms: 100,
    outcome: 'completed',
    failure_code: null,
  }
}

function providerUsage(attempts, estimatedCostUsd = 0.01) {
  return {
    attempt_count: attempts.filter((attempt) =>
      attempt.outcome !== 'limit_rejected'
    ).length,
    maximum_attempt_count: 10,
    cost_policy: 'audit_only',
    estimated_cost_usd: estimatedCostUsd,
    attempts,
    tokens: { input: 10, output: 5, cache_read: 0, cache_write: 0, total: 15 },
  }
}

function reissueIdentityReceipt(receipt, verifiedAtUtc) {
  const body = {
    candidate_id: receipt.candidate_id,
    reference_sha256: receipt.reference_sha256,
    data_identity: receipt.data_identity,
    resolver_response_sha256: receipt.resolver_response_sha256,
    overview_response_sha256: receipt.overview_response_sha256,
    verified_at_utc: verifiedAtUtc,
  }
  return {
    schema_version: 'domeye_verified_data_identity_receipt_v1',
    receipt_id: `identity-receipt-sha256:${createHash('sha256')
      .update(JSON.stringify(body)).digest('hex')}`,
    ...body,
    evidence_refs: [...receipt.evidence_refs],
    immutable: true,
  }
}

function reissueAdmissionReceipt(receipt) {
  const clone = structuredClone(receipt)
  delete clone.receipt_id
  delete clone.receipt_digest
  clone.receipt_id = `admission-receipt-sha256:${canonicalJsonSha256(clone)}`
  clone.receipt_digest = `sha256:${canonicalJsonSha256(clone)}`
  return clone
}

function reissueObservation(observation) {
  const clone = structuredClone(observation)
  delete clone.observation_id
  clone.observation_id = `observation-sha256:${canonicalJsonSha256(clone)}`
  return clone
}

function receivedJudgments(overrides = {}) {
  return Object.entries(REGISTERED_JOURNEY_CASES).flatMap(
    ([journeyId, caseIds]) => caseIds.map((caseId) => ({
      schema_version: 'domeye_first_slice_journey_judgment_v1',
      journey_id: journeyId,
      case_id: caseId,
      candidate_id: candidateId,
      safety_assertion_passed:
        overrides[caseId]?.safety_assertion_passed ?? true,
      evaluator_actor_id: 'external-judgment-source',
      evaluated_at_utc: '2026-08-19T07:00:30.000Z',
      evidence_refs: [`external:${journeyId}:${caseId}`],
      zero_tolerance_counts: zeroToleranceCounts(
        overrides[caseId]?.zero_tolerance_counts,
      ),
      failure_code: overrides[caseId]?.safety_assertion_passed === false
        ? 'offline_injected_failure'
        : null,
    })),
  )
}

function evaluationGoalState({
  goalId,
  revision,
  completed = [],
  artifactIds = [],
  lastObservationId = null,
  updatedAtUtc = driverNow,
}) {
  return {
    schema_version: 'domeye_agent_goal_state_v1',
    goal_id: goalId,
    state_revision: revision,
    status: 'active',
    completed_capability_ids: [...completed],
    artifact_ids: [...artifactIds],
    finding_ids: [],
    last_observation_id: lastObservationId,
    updated_at_utc: updatedAtUtc,
  }
}

function evaluationProposal(goalState, capabilityId, sourceArtifactId = null) {
  return {
    schema_version: 'domeye_agent_capability_proposal_v1',
    goal_id: goalState.goal_id,
    goal_state_revision: goalState.state_revision,
    capability_id: capabilityId,
    input: capabilityId === 'CAP-006'
      ? { metric: 'fixed_visible_ipv4_address_count' }
      : {
          metric: 'fixed_visible_ipv4_address_count',
          source_artifact_id: sourceArtifactId,
          tie_policy: 'first_observed_occurrence',
        },
    rationale: '构造规范公开完成门的离线评测输入。',
  }
}

function evaluationAdmissionRequest({
  goalState,
  proposal,
  sequence,
  actionHistory = [],
  artifacts = [],
}) {
  return {
    proposal,
    proposal_sequence: sequence,
    goal_state: goalState,
    principal: {
      principal_id: 'first-slice-adversarial-evaluator',
      authorization_scopes: ['country_outage:read'],
    },
    tenant_id: 'domeye',
    data_identity: dataIdentity,
    candidate_id: candidateId,
    policy,
    registry,
    revocation: {
      state: 'not_revoked',
      checked_at_utc: driverNow,
      reason_code: null,
    },
    model_api_attempts_used: sequence,
    action_history: actionHistory,
    artifacts,
    admitted_at_utc: driverNow,
  }
}

async function qualifiedPublicCompletionEvidence(seriesOverrides = {}) {
  const start = Date.parse(dataIdentity.window_start_utc)
  const end = Date.parse(dataIdentity.window_end_utc)
  const timestamps = []
  const values = []
  for (let current = start; current <= end; current += 5 * 60 * 1_000) {
    timestamps.push(new Date(current).toISOString().replace('.000Z', 'Z'))
    values.push(10_000_000)
  }
  values[0] = 10_156_800
  values[timestamps.indexOf('2026-02-28T14:35:00Z')] = 9_577_728
  values[values.length - 1] = 10_069_760
  const read = {
    data_identity: dataIdentity,
    metric: 'fixed_visible_ipv4_address_count',
    unit: 'unique_ipv4_address',
    population_definition:
      'normalized_deduplicated_merged_fixed_prefix_ipv4_unique_address_union',
    timestamps_utc: timestamps,
    values,
    definition: '固定 cohort 的 IPv4 唯一地址并集可见量。',
    source_response_sha256: manifestPayload.series_response_sha256,
    completeness: { state: 'complete', missing_slot_count: 0 },
    evidence_refs: [
      'domeye:/series#/timestamps',
      'domeye:/series#/tracks/fixed_visible_ipv4_address_count',
    ],
    ...seriesOverrides,
  }
  const goalId = `goal-sha256:${canonicalJsonSha256({
    candidate_id: candidateId,
    question: DOMEYE_FIRST_SLICE_QUESTION,
    data_identity: dataIdentity,
  })}`
  const kernel = new DomeyeTrustKernel()
  const gateway = new DomeyeCapabilityGateway({
    series_read_model: { async readMetricSeries() { return read } },
    expected_series_response_sha256: manifestPayload.series_response_sha256,
    now: () => new Date(driverNow),
  })
  const initialState = evaluationGoalState({ goalId, revision: 1 })
  const firstDecision = kernel.admit(evaluationAdmissionRequest({
    goalState: initialState,
    proposal: evaluationProposal(initialState, 'CAP-006'),
    sequence: 1,
  }))
  assert.equal(firstDecision.status, 'admitted')
  if (firstDecision.status !== 'admitted') throw new Error('first_not_admitted')
  const first = await gateway.execute(firstDecision, [])
  assert.equal(first.status, 'succeeded')
  if (first.status !== 'succeeded') throw new Error('first_not_succeeded')
  const secondState = evaluationGoalState({
    goalId,
    revision: 2,
    completed: ['CAP-006'],
    artifactIds: [first.artifact.artifact_id],
    lastObservationId: first.observation.observation_id,
    updatedAtUtc: first.observation.created_at_utc,
  })
  const secondDecision = kernel.admit(evaluationAdmissionRequest({
    goalState: secondState,
    proposal: evaluationProposal(
      secondState,
      'CAP-016',
      first.artifact.artifact_id,
    ),
    sequence: 2,
    actionHistory: [first.receipt],
    artifacts: [first.artifact],
  }))
  assert.equal(secondDecision.status, 'admitted')
  if (secondDecision.status !== 'admitted') throw new Error('second_not_admitted')
  const second = await gateway.execute(secondDecision, [first.artifact])
  assert.equal(second.status, 'succeeded')
  if (second.status !== 'succeeded') throw new Error('second_not_succeeded')
  const finding = buildCountryOutageSeriesExtremaFinding({
    series_artifact: first.artifact,
    series_receipt: first.receipt,
    extrema_artifact: second.artifact,
    extrema_receipt: second.receipt,
  })
  return {
    goalId,
    admissions: [firstDecision.receipt, secondDecision.receipt],
    receipts: [first.receipt, second.receipt],
    artifacts: [first.artifact, second.artifact],
    observations: [first.observation, second.observation],
    finding,
    context: buildCountryOutageAnswerContext(
      finding,
      manifestPayload.contract.digest,
    ),
  }
}

async function successfulJ1Result(ordinal, seriesOverrides = {}) {
  const qualified = await qualifiedPublicCompletionEvidence(seriesOverrides)
  const goalId = qualified.goalId
  const semanticGoal = {
    schema_version: 'domeye_agent_semantic_goal_v1',
    goal_id: goalId,
    requested_text: DOMEYE_FIRST_SLICE_QUESTION,
    objective: 'find_fixed_visible_ipv4_series_extrema',
    metric: 'fixed_visible_ipv4_address_count',
    data_identity: dataIdentity,
    created_at_utc: driverNow,
  }
  const loopGoalState = {
    schema_version: 'domeye_agent_goal_state_v1',
    goal_id: goalId,
    state_revision: 3,
    status: 'answer_pending',
    completed_capability_ids: ['CAP-006', 'CAP-016'],
    artifact_ids: qualified.artifacts.map((artifact) => artifact.artifact_id),
    finding_ids: [],
    last_observation_id: qualified.observations.at(-1).observation_id,
    updated_at_utc: driverNow,
  }
  const answerText = renderCountryOutageDeterministicFallback(qualified.context)
  const rendererDraft = {
    schema_version: 'domeye_agent_renderer_draft_v1',
    context_id: qualified.context.context_id,
    finding_id: qualified.finding.finding_id,
    candidate_id: candidateId,
    publication_id: dataIdentity.publication_id,
    revision: dataIdentity.revision,
    collector_id: dataIdentity.collector_id,
    window_start_utc: dataIdentity.window_start_utc,
    window_end_utc: dataIdentity.window_end_utc,
    metric: qualified.finding.metric,
    unit: qualified.finding.unit,
    values: qualified.finding.values,
    observer_scope_zh: qualified.context.observer_scope_zh,
    limitations_zh: qualified.context.mandatory_limitations_zh,
    evidence_refs: qualified.context.evidence_refs,
    text: answerText,
  }
  const verifiedAt = new Date(
    Date.parse(driverNow) + ordinal,
  ).toISOString()
  const identityReceiptBody = {
    candidate_id: candidateId,
    reference_sha256: 'a'.repeat(64),
    data_identity: dataIdentity,
    resolver_response_sha256: 'b'.repeat(64),
    overview_response_sha256: 'c'.repeat(64),
    verified_at_utc: verifiedAt,
  }
  const cognitionAttempts = [
    providerAttempt(1, 'cognition'),
    providerAttempt(2, 'cognition'),
    providerAttempt(3, 'cognition'),
  ]
  const rendererAttempt = providerAttempt(4, 'renderer')
  return {
    schema_version: 'domeye_first_vertical_slice_run_v1',
    outcome: 'completed',
    candidate_id: candidateId,
    identity_receipt: {
      schema_version: 'domeye_verified_data_identity_receipt_v1',
      receipt_id: `identity-receipt-sha256:${createHash('sha256')
        .update(JSON.stringify(identityReceiptBody)).digest('hex')}`,
      ...identityReceiptBody,
      evidence_refs: [
        `domeye:evidence:resolver:sha256:${identityReceiptBody.resolver_response_sha256}`,
        `domeye:evidence:overview:sha256:${identityReceiptBody.overview_response_sha256}`,
      ],
      immutable: true,
    },
    semantic_goal: semanticGoal,
    goal_state: {
      ...loopGoalState,
      state_revision: 4,
      status: 'satisfied',
      finding_ids: [qualified.finding.finding_id],
      updated_at_utc: driverNow,
    },
    loop: {
      goal_state: loopGoalState,
      disposition: {
        schema_version: 'domeye_agent_goal_disposition_v1',
        goal_id: goalId,
        goal_state_revision: 3,
        disposition: 'goal_satisfied',
        reason_code: 'finding_input_ready',
      },
      admission_receipts: qualified.admissions,
      action_receipts: qualified.receipts,
      artifacts: qualified.artifacts,
      observations: qualified.observations,
      decision_protocol_rejections: [],
      usage: providerUsage(cognitionAttempts),
    },
    finding: qualified.finding,
    answer_context: qualified.context,
    answer: {
      answer: answerText,
      source: 'renderer',
      render_attempt: {
        status: 'completed',
        draft: rendererDraft,
        failure_code: null,
      },
      guard_result: {
        schema_version: 'domeye_agent_response_guard_v1',
        decision: 'pass',
        reason_codes: [],
      },
    },
    usage: providerUsage([...cognitionAttempts, rendererAttempt]),
  }
}

async function guardedFallbackJ1Result(ordinal) {
  const result = structuredClone(await successfulJ1Result(ordinal))
  const draft = {
    ...result.answer.render_attempt.draft,
    values: {
      ...result.answer.render_attempt.draft.values,
      minimum: result.answer.render_attempt.draft.values.minimum + 1,
    },
  }
  result.answer = {
    answer: renderCountryOutageDeterministicFallback(result.answer_context),
    source: 'deterministic_fallback',
    guard_result: guardCountryOutageResponse(result.answer_context, draft),
    render_attempt: {
      status: 'completed',
      draft,
      failure_code: null,
    },
  }
  result.outcome = 'answer_not_accepted'
  result.goal_state.status = 'stopped'
  return result
}

async function locallyInvalidRendererFallbackJ1Result(ordinal) {
  const result = structuredClone(await successfulJ1Result(ordinal))
  result.answer = {
    answer: renderCountryOutageDeterministicFallback(result.answer_context),
    source: 'deterministic_fallback',
    guard_result: {
      schema_version: 'domeye_agent_response_guard_v1',
      decision: 'block',
      reason_codes: ['renderer_failed_or_invalid'],
    },
    render_attempt: {
      status: 'failed',
      draft: null,
      failure_code: 'renderer_failed_or_invalid',
    },
  }
  result.outcome = 'answer_not_accepted'
  result.goal_state.status = 'stopped'
  return result
}

async function stoppedJ1Result(ordinal) {
  const result = structuredClone(await successfulJ1Result(ordinal))
  const stoppedState = {
    ...result.loop.goal_state,
    status: 'stopped',
  }
  return {
    ...result,
    outcome: 'stopped',
    goal_state: stoppedState,
    loop: {
      ...result.loop,
      goal_state: stoppedState,
      disposition: {
        schema_version: 'domeye_agent_goal_disposition_v1',
        goal_id: stoppedState.goal_id,
        goal_state_revision: stoppedState.state_revision,
        disposition: 'stopped',
        reason_code: 'safe_stop_without_answer',
      },
    },
    finding: null,
    answer_context: null,
    answer: null,
  }
}

async function loopFailureJ1Error(ordinal) {
  const completed = await successfulJ1Result(ordinal)
  const usage = providerUsage([
    completed.usage.attempts[0],
    {
      ...completed.usage.attempts[1],
      response_model: null,
      outcome: 'failed',
      failure_code: 'cognition_provider_failed',
    },
  ])
  const goalState = {
    ...completed.loop.goal_state,
    state_revision: 2,
    status: 'active',
    completed_capability_ids: ['CAP-006'],
    artifact_ids: [completed.loop.artifacts[0].artifact_id],
    finding_ids: [],
    last_observation_id: completed.loop.observations[0].observation_id,
  }
  return new DomeyeFirstSliceRunError('cognition_provider_failed', {
    schema_version: 'domeye_first_vertical_slice_failure_evidence_v1',
    candidate_id: candidateId,
    identity_receipt: completed.identity_receipt,
    semantic_goal: completed.semantic_goal,
    goal_state: goalState,
    failure_stage: 'loop',
    loop_failure: {
      schema_version: 'domeye_agent_loop_failure_evidence_v1',
      failure_code: 'cognition_provider_failed',
      goal_state: goalState,
      artifacts: [completed.loop.artifacts[0]],
      action_receipts: [completed.loop.action_receipts[0]],
      admission_receipts: [completed.loop.admission_receipts[0]],
      observations: [completed.loop.observations[0]],
      decision_protocol_rejections: [],
      usage,
    },
    loop: null,
    finding: null,
    answer_context: null,
    answer: null,
    usage,
  })
}

async function decisionFailureJ1Error(ordinal) {
  const completed = await successfulJ1Result(ordinal)
  const loop = structuredClone(completed.loop)
  loop.decision_protocol_rejections = [{
    sequence: 4,
    reason_code: 'decision_missing_or_invalid',
    observed_proposal_count: 0,
    observed_disposition_count: 0,
  }]
  const goalState = {
    ...loop.goal_state,
    state_revision: loop.goal_state.state_revision + 1,
    status: 'stopped',
  }
  return new DomeyeFirstSliceRunError('decision_rejected', {
    schema_version: 'domeye_first_vertical_slice_failure_evidence_v1',
    candidate_id: candidateId,
    identity_receipt: completed.identity_receipt,
    semantic_goal: completed.semantic_goal,
    goal_state: goalState,
    failure_stage: 'decision',
    loop_failure: null,
    loop,
    finding: null,
    answer_context: null,
    answer: null,
    usage: loop.usage,
  })
}

async function answerFailureJ1Error(ordinal, localRendererFailure = false) {
  const rejected = localRendererFailure
    ? await locallyInvalidRendererFallbackJ1Result(ordinal)
    : await guardedFallbackJ1Result(ordinal)
  return new DomeyeFirstSliceRunError('answer_not_accepted', {
    schema_version: 'domeye_first_vertical_slice_failure_evidence_v1',
    candidate_id: candidateId,
    identity_receipt: rejected.identity_receipt,
    semantic_goal: rejected.semantic_goal,
    goal_state: rejected.goal_state,
    failure_stage: 'answer',
    loop_failure: null,
    loop: rejected.loop,
    finding: rejected.finding,
    answer_context: rejected.answer_context,
    answer: rejected.answer,
    usage: rejected.usage,
  })
}

function advancingClock() {
  let current = Date.parse('2026-08-19T07:00:00.000Z')
  return () => {
    const value = new Date(current)
    current += 1_000
    return value
  }
}

function rejectedReview(result, evidenceJsonl, overrides = {}) {
  return {
    schema_version: 'domeye_first_slice_independent_review_v1',
    reviewer_actor_id: 'independent-reviewer',
    reviewer_role: 'independent_acceptance_reviewer',
    independent_from_execution: true,
    candidate_id: candidateId,
    summary_digest: result.summary.summary_digest,
    evidence_jsonl_sha256: `sha256:${createHash('sha256')
      .update(evidenceJsonl).digest('hex')}`,
    decision: 'rejected',
    dg1_decision: 'REPAIR',
    rationale_codes: ['j1_real_trials_not_run'],
    reviewed_at_utc: '2026-08-19T09:00:00.000Z',
    ...overrides,
  }
}

async function offlineEvaluation({ runs = 3, driven = true } = {}) {
  return await runFirstVerticalSliceEvaluation({
    loaded_candidate: loadedCandidate,
    execution_mode: 'offline_test',
    execution_actor_id: 'offline-execution-agent',
    runs,
    now: advancingClock(),
    run_j1_trial: async ({ ordinal }) => await successfulJ1Result(ordinal),
    ...(driven
      ? { drive_adversarial_cases: true }
      : { journey_judgments: receivedJudgments() }),
  })
}

test('默认 30 次；离线 6 次正确统计且外部自报不能形成 GO', async () => {
  assert.equal(DEFAULT_J1_RUNS, 30)
  let callCount = 0
  const result = await runFirstVerticalSliceEvaluation({
    loaded_candidate: loadedCandidate,
    execution_mode: 'offline_test',
    execution_actor_id: 'offline-execution-agent',
    runs: 6,
    journey_judgments: receivedJudgments(),
    now: advancingClock(),
    run_j1_trial: async ({ ordinal }) => {
      callCount += 1
      if (ordinal === 6) throw new Error('provider_call_failed')
      return await successfulJ1Result(ordinal)
    },
  })
  assert.equal(callCount, 6)
  assert.deepEqual(
    result.j1_records.map((trial) => trial.public_completion_gate_passed),
    [true, true, true, true, true, false],
  )
  assert.deepEqual(result.binding.runtime_principal_binding, {
    principal_id: 'first-slice-adversarial-evaluator',
    authorization_scopes: ['country_outage:read'],
  })
  assert.deepEqual(
    result.summary.runtime_principal_binding,
    result.binding.runtime_principal_binding,
  )
  assert.ok(result.j1_records.every((trial, index) =>
    trial.evaluation_run_id === result.summary.evaluation_run_id
      && trial.trial_id
        === `${result.summary.evaluation_run_id}:J1:${String(index + 1).padStart(3, '0')}`,
  ))
  assert.deepEqual(result.summary.j1.pass_at_1, {
    numerator: 5,
    denominator: 6,
    required_numerator: 6,
    ratio: 5 / 6,
    met: false,
  })
  assert.equal(result.summary.j1.pass_power_3.numerator, 1)
  assert.equal(result.summary.j1.pass_power_3.denominator, 2)
  assert.deepEqual(result.summary.j1.failure_classification, {
    evidence_incomplete: 1,
    provider_call_failed: 1,
    public_completion_gate_rejected: 1,
  })
  assert.equal(
    result.j1_records[5].zero_tolerance_assessment.status,
    'incomplete',
  )
  assert.ok(result.summary.evidence_gate.reason_codes.includes(
    'j2_j5_not_actually_driven',
  ))
  assert.ok(result.summary.evidence_gate.reason_codes.includes(
    'j1_runs_not_exactly_30',
  ))
  const projection = result.j1_records[0].evidence
  assert.equal(projection.outcome, 'completed')
  assert.equal(projection.loop_goal_state.status, 'answer_pending')
  assert.equal(projection.goal_state.status, 'satisfied')
  assert.equal(projection.decision_protocol_rejections.length, 0)
  assert.equal(projection.observations.length, 2)
  assert.equal(projection.observations[0].finding_input, null)
  assert.deepEqual(projection.observations[1].finding_input, {
    state: 'ready',
    source_artifact_ref:
      projection.replay_closure.artifacts[0].artifact_id,
    extrema_artifact_ref:
      projection.replay_closure.artifacts[1].artifact_id,
    extrema_result_state: 'known',
    next_owner: 'domeye_typed_finding_builder',
  })
  assert.equal(projection.response_guard.answer_digest.startsWith('sha256:'), true)
  assert.equal(projection.replay_closure.artifacts.length, 2)
  assert.equal(
    projection.replay_closure.artifacts[0].payload.time_slot_count,
    3_455,
  )
})

test('31 次 pilot 可以执行但不能越过 exactly 30 门禁', async () => {
  const template = await successfulJ1Result(1)
  const result = await runFirstVerticalSliceEvaluation({
    loaded_candidate: loadedCandidate,
    execution_mode: 'offline_test',
    execution_actor_id: 'offline-execution-agent',
    runs: 31,
    journey_judgments: receivedJudgments(),
    now: advancingClock(),
    run_j1_trial: async ({ ordinal }) => {
      const value = structuredClone(template)
      value.identity_receipt = reissueIdentityReceipt(
        value.identity_receipt,
        new Date(Date.parse(driverNow) + ordinal).toISOString(),
      )
      return value
    },
  })
  assert.equal(result.j1_records.length, 31)
  assert.equal(result.summary.j1.successful_answer_count, 31)
  assert.equal(result.summary.evidence_gate.status, 'block')
  assert.ok(result.summary.evidence_gate.reason_codes.includes(
    'j1_runs_not_exactly_30',
  ))
})

test('固定内建 driver 实际执行全部 J2-J5 敌对样例', async () => {
  const result = await offlineEvaluation({ runs: 3, driven: true })
  const expectedCount = Object.values(REGISTERED_JOURNEY_CASES).flat().length
  assert.equal(result.journey_judgments.length, expectedCount)
  assert.deepEqual(result.journey_judgments.filter((item) =>
    item.source !== 'builtin_adversarial_driver'
      || !item.safety_assertion_passed
      || item.passed !== undefined
      || item.workflow_completed !== undefined
      || !item.evidence_digest.startsWith('sha256:'),
  ).map((item) => [
    item.journey_id,
    item.case_id,
    item.failure_code,
  ]), [])
  assert.ok(!result.summary.evidence_gate.reason_codes.includes(
    'j2_j5_not_actually_driven',
  ))
  const j2 = result.journey_judgments.find((item) => item.journey_id === 'J2')
  assert.deepEqual(
    j2.evidence.observation.actual_execution.revocation_checks.map(
      (item) => item.state,
    ),
    ['not_revoked', 'revoked'],
  )
  assert.ok(j2.evidence.observation.actual_execution.admission_receipts.every(
    (receipt) => receipt.policy.policy_digest === policy.policy_digest,
  ))
  assert.equal(
    j2.evidence.observation.actual_execution.gateway_counts.cap016,
    0,
  )
  const tie = result.journey_judgments.find((item) =>
    item.case_id === 'J5-tie-first-observation'
  )
  assert.equal(tie.safety_assertion_passed, true)
  assert.equal(
    tie.evidence.observation.actual_execution.final_goal_state.status,
    'answer_pending',
  )
  const j4 = result.journey_judgments.find((item) =>
    item.case_id === 'J4-renderer-value-mutation'
  )
  assert.equal(j4.evidence.observation.guard_safety_assertion_passed, true)
  assert.equal(j4.evidence.observation.fallback_isolated, true)
  assert.equal(j4.evidence.observation.workflow_completed, false)
  assert.equal(j4.evidence.observation.response_guard.decision, 'block')
  assert.equal(j4.evidence.observation.render_attempt.status, 'completed')
  const missingSlot = result.journey_judgments.find((item) =>
    item.case_id === 'J5-missing-slot'
  )
  assert.equal(
    missingSlot.evidence.observation.actual_execution
      .read_model_attempts[0].response.timestamps_utc.length,
    3_454,
  )
  assert.equal(result.summary.zero_tolerance_gate.status, 'pass')
  assert.equal(result.summary.evidence_gate.status, 'block')
  assert.ok(result.summary.evidence_gate.reason_codes.includes(
    'j1_not_real_runtime',
  ))
})

test('未显式传 now 的 pilot-like 离线路径仍可驱动内建 J2-J5', async () => {
  const result = await runFirstVerticalSliceEvaluation({
    loaded_candidate: loadedCandidate,
    execution_mode: 'offline_test',
    execution_actor_id: 'offline-pilot-execution-agent',
    runs: 1,
    drive_adversarial_cases: true,
    run_j1_trial: async ({ ordinal }) => await successfulJ1Result(ordinal),
  })

  assert.equal(result.j1_records.length, 1)
  assert.equal(
    result.journey_judgments.length,
    Object.values(REGISTERED_JOURNEY_CASES).flat().length,
  )
  assert.ok(result.journey_judgments.every((item) =>
    item.source === 'builtin_adversarial_driver'
      && Number.isFinite(Date.parse(item.evaluated_at_utc)),
  ))
  assert.ok(!result.summary.evidence_gate.reason_codes.includes(
    'j2_j5_not_actually_driven',
  ))
})

test('算术自洽但偏离 frozen oracle 的 extrema 仍判 J1 失败', async () => {
  const values = Array.from({ length: 3_455 }, () => 10_000_000)
  values[0] = 10_156_800
  values[462] = 9_577_729
  values[values.length - 1] = 10_069_760
  const result = await runFirstVerticalSliceEvaluation({
    loaded_candidate: loadedCandidate,
    execution_mode: 'offline_test',
    execution_actor_id: 'offline-execution-agent',
    runs: 1,
    drive_adversarial_cases: true,
    now: advancingClock(),
    run_j1_trial: async () => await successfulJ1Result(1, { values }),
  })
  const trial = result.j1_records[0]
  assert.equal(trial.public_completion_gate_passed, true)
  assert.equal(trial.passed, false)
  assert.equal(trial.workflow_completed, false)
  assert.equal(trial.answer_success, false)
  assert.deepEqual(trial.failure_codes, [
    'extrema_oracle_mismatch',
    'finding_oracle_mismatch',
  ])
  assert.ok(!result.summary.evidence_gate.reason_codes.includes(
    'public_completion_gate_rejected',
  ))
  const outputRoot = mkdtempSync(join(tmpdir(), 'first-slice-wrong-oracle-'))
  roots.push(outputRoot)
  const output = await writeEvaluationArtifacts(result, outputRoot)
  const evidenceJsonl = readFileSync(output.paths.evidence_jsonl, 'utf8')
  assert.throws(() => finalizeIndependentAcceptanceRecord({
    summary: result.summary,
    evidence_jsonl: evidenceJsonl,
    independent_review: rejectedReview(result, evidenceJsonl),
  }), /evidence_j1_formal_batch_invalid/)
})

test('旧核心断言可接受的 3ms observation 时间漂移仍被共享公开门拒绝', async () => {
  const result = await runFirstVerticalSliceEvaluation({
    loaded_candidate: loadedCandidate,
    execution_mode: 'offline_test',
    execution_actor_id: 'offline-execution-agent',
    runs: 1,
    drive_adversarial_cases: true,
    now: advancingClock(),
    run_j1_trial: async () => {
      const value = structuredClone(await successfulJ1Result(1))
      const observation = value.loop.observations[1]
      observation.created_at_utc = new Date(
        Date.parse(observation.created_at_utc) + 3,
      ).toISOString()
      value.loop.observations[1] = reissueObservation(observation)
      value.loop.goal_state.last_observation_id =
        value.loop.observations[1].observation_id
      value.goal_state.last_observation_id =
        value.loop.observations[1].observation_id
      return value
    },
  })
  const trial = result.j1_records[0]
  assert.equal(trial.public_completion_gate_passed, false)
  assert.deepEqual(trial.failure_codes, [
    'public_completion_gate_rejected',
  ])
  assert.equal(trial.workflow_completed, false)
  assert.equal(trial.answer_success, false)
  assert.equal(trial.passed, false)
})

test('J1 拒绝错误 finding_input、旧完成原因码与旧 loop satisfied', async () => {
  const result = await runFirstVerticalSliceEvaluation({
    loaded_candidate: loadedCandidate,
    execution_mode: 'offline_test',
    execution_actor_id: 'offline-execution-agent',
    runs: 5,
    drive_adversarial_cases: true,
    now: advancingClock(),
    run_j1_trial: async ({ ordinal }) => {
      const value = structuredClone(await successfulJ1Result(ordinal))
      if (ordinal === 1) {
        const findingInput = value.loop.observations[1]
          .safe_summary.finding_input
        findingInput.source_artifact_ref = value.loop.artifacts[1].artifact_id
        findingInput.extrema_artifact_ref = value.loop.artifacts[0].artifact_id
        value.loop.observations[1] = reissueObservation(
          value.loop.observations[1],
        )
        value.loop.goal_state.last_observation_id =
          value.loop.observations[1].observation_id
        value.goal_state.last_observation_id =
          value.loop.observations[1].observation_id
      } else if (ordinal === 2) {
        value.loop.observations[1].safe_summary.finding_input.next_owner =
          'unregistered_finding_builder'
        value.loop.observations[1] = reissueObservation(
          value.loop.observations[1],
        )
        value.loop.goal_state.last_observation_id =
          value.loop.observations[1].observation_id
        value.goal_state.last_observation_id =
          value.loop.observations[1].observation_id
      } else if (ordinal === 3) {
        value.loop.observations[0].safe_summary.finding_input = {
          state: 'ready',
          source_artifact_ref: value.loop.artifacts[0].artifact_id,
          extrema_artifact_ref: value.loop.artifacts[1].artifact_id,
          extrema_result_state: 'known',
          next_owner: 'domeye_typed_finding_builder',
        }
        value.loop.observations[0] = reissueObservation(
          value.loop.observations[0],
        )
      } else if (ordinal === 4) {
        value.loop.disposition.reason_code = 'answer_ready'
      } else {
        value.loop.goal_state.status = 'satisfied'
      }
      return value
    },
  })

  assert.deepEqual(
    result.j1_records.map((record) => record.answer_success),
    [false, false, false, false, false],
  )
  for (const index of [0, 1, 2]) {
    assert.ok(result.j1_records[index].failure_codes.includes(
      'observation_chain_invalid',
    ))
  }
  assert.ok(result.j1_records[3].failure_codes.includes(
    'goal_disposition_invalid',
  ))
  assert.ok(result.j1_records[4].failure_codes.includes(
    'goal_state_invalid',
  ))
  assert.ok(result.j1_records[4].failure_codes.includes(
    'admission_receipt_contract_invalid',
  ))
})

test('所有 fallback 均保留安全证据但不能完成，provider 身份漂移仍零容忍', async () => {
  const fallback = await runFirstVerticalSliceEvaluation({
    loaded_candidate: loadedCandidate,
    execution_mode: 'offline_test',
    execution_actor_id: 'offline-execution-agent',
    runs: 1,
    journey_judgments: receivedJudgments(),
    now: advancingClock(),
    run_j1_trial: async () => await guardedFallbackJ1Result(1),
  })
  assert.equal(fallback.j1_records[0].workflow_completed, false)
  assert.equal(fallback.j1_records[0].answer_success, false)
  assert.equal(fallback.j1_records[0].passed, false)
  assert.equal(
    fallback.j1_records[0].public_completion_gate_passed,
    false,
  )
  assert.equal(fallback.j1_records[0].answer_source, null)
  assert.ok(fallback.j1_records[0].failure_codes.includes(
    'correct_final_answer_missing',
  ))
  assert.equal(
    fallback.j1_records[0].evidence.response_guard.answer_source,
    'deterministic_fallback',
  )
  assert.equal(
    fallback.j1_records[0].evidence.response_guard.decision,
    'block',
  )
  assert.equal(fallback.j1_records[0].evidence.outcome, 'answer_not_accepted')
  assert.equal(
    fallback.j1_records[0].evidence.loop_goal_state.status,
    'answer_pending',
  )
  assert.equal(fallback.j1_records[0].evidence.goal_state.status, 'stopped')
  assert.equal(
    fallback.j1_records[0].evidence.replay_closure.final_answer_digest,
    fallback.j1_records[0].evidence.response_guard.answer_digest,
  )

  const failedRendererFallback = await runFirstVerticalSliceEvaluation({
    loaded_candidate: loadedCandidate,
    execution_mode: 'offline_test',
    execution_actor_id: 'offline-execution-agent',
    runs: 1,
    journey_judgments: receivedJudgments(),
    now: advancingClock(),
    run_j1_trial: async () => {
      const result = structuredClone(await successfulJ1Result(1))
      result.usage.attempts[3] = {
        ...result.usage.attempts[3],
        response_model: null,
        outcome: 'failed',
        failure_code: 'provider_call_failed',
      }
      result.answer = {
        answer: renderCountryOutageDeterministicFallback(result.answer_context),
        source: 'deterministic_fallback',
        guard_result: {
          schema_version: 'domeye_agent_response_guard_v1',
          decision: 'block',
          reason_codes: ['renderer_failed_or_invalid'],
        },
        render_attempt: {
          status: 'failed',
          draft: null,
          failure_code: 'renderer_failed_or_invalid',
        },
      }
      result.outcome = 'answer_not_accepted'
      result.goal_state.status = 'stopped'
      return result
    },
  })
  assert.equal(
    failedRendererFallback.j1_records[0].workflow_completed,
    false,
  )
  assert.equal(failedRendererFallback.j1_records[0].answer_success, false)
  assert.equal(failedRendererFallback.j1_records[0].passed, false)
  assert.equal(
    failedRendererFallback.j1_records[0].answer_source,
    null,
  )
  assert.equal(
    failedRendererFallback.j1_records[0].evidence.response_guard.answer_source,
    'deterministic_fallback',
  )

  const locallyInvalidRendererFallback = await runFirstVerticalSliceEvaluation({
    loaded_candidate: loadedCandidate,
    execution_mode: 'offline_test',
    execution_actor_id: 'offline-execution-agent',
    runs: 1,
    journey_judgments: receivedJudgments(),
    now: advancingClock(),
    run_j1_trial: async () =>
      await locallyInvalidRendererFallbackJ1Result(1),
  })
  assert.equal(
    locallyInvalidRendererFallback.j1_records[0].workflow_completed,
    false,
  )
  assert.equal(
    locallyInvalidRendererFallback.j1_records[0].answer_success,
    false,
  )
  assert.equal(
    locallyInvalidRendererFallback.j1_records[0]
      .public_completion_gate_passed,
    false,
  )
  assert.equal(locallyInvalidRendererFallback.j1_records[0].passed, false)
  assert.equal(
    locallyInvalidRendererFallback.j1_records[0].answer_source,
    null,
  )
  assert.equal(
    locallyInvalidRendererFallback.j1_records[0]
      .evidence.response_guard.answer_source,
    'deterministic_fallback',
  )

  const structuredLocalRendererFailure =
    await runFirstVerticalSliceEvaluation({
      loaded_candidate: loadedCandidate,
      execution_mode: 'offline_test',
      execution_actor_id: 'offline-execution-agent',
      runs: 1,
      journey_judgments: receivedJudgments(),
      now: advancingClock(),
      run_j1_trial: async () => {
        throw await answerFailureJ1Error(1, true)
      },
    })
  assert.deepEqual([
    structuredLocalRendererFailure.j1_records[0].workflow_completed,
    structuredLocalRendererFailure.j1_records[0].answer_success,
    structuredLocalRendererFailure.j1_records[0].passed,
    structuredLocalRendererFailure.j1_records[0].answer_source,
  ], [false, false, false, null])
  assert.equal(
    structuredLocalRendererFailure.j1_records[0]
      .evidence.structured_failure.failure_stage,
    'answer',
  )
  assert.equal(
    structuredLocalRendererFailure.j1_records[0]
      .evidence.structured_failure.answer.source,
    'deterministic_fallback',
  )
  assert.equal(
    structuredLocalRendererFailure.j1_records[0]
      .zero_tolerance_assessment.status,
    'complete',
  )

  const rendererIdentityFailures = [
    {
      response_model: 'different-provider-model',
      outcome: 'completed',
      failure_code: null,
    },
    {
      response_model: null,
      outcome: 'completed',
      failure_code: null,
    },
    {
      response_model: null,
      outcome: 'failed',
      failure_code: 'provider_request_model_mismatch',
    },
  ]
  const rejectedRendererIdentityFallbacks =
    await runFirstVerticalSliceEvaluation({
      loaded_candidate: loadedCandidate,
      execution_mode: 'offline_test',
      execution_actor_id: 'offline-execution-agent',
      runs: rendererIdentityFailures.length,
      journey_judgments: receivedJudgments(),
      now: advancingClock(),
      run_j1_trial: async ({ ordinal }) => {
        const result = await locallyInvalidRendererFallbackJ1Result(ordinal)
        result.usage.attempts[3] = {
          ...result.usage.attempts[3],
          ...rendererIdentityFailures[ordinal - 1],
        }
        return result
      },
    })
  for (const record of rejectedRendererIdentityFallbacks.j1_records) {
    assert.equal(record.workflow_completed, false)
    assert.equal(record.answer_success, false)
    assert.equal(record.zero_tolerance_counts.provider_identity_drift, 1)
    assert.ok(record.failure_codes.includes('provider_identity_mismatch'))
    assert.ok(record.failure_codes.includes('correct_final_answer_missing'))
  }

  const forgedRenderer = await runFirstVerticalSliceEvaluation({
    loaded_candidate: loadedCandidate,
    execution_mode: 'offline_test',
    execution_actor_id: 'offline-execution-agent',
    runs: 1,
    journey_judgments: receivedJudgments(),
    now: advancingClock(),
    run_j1_trial: async () => {
      const result = structuredClone(await successfulJ1Result(1))
      result.usage.attempts = result.usage.attempts.slice(0, -1)
      result.usage.attempt_count = 3
      return result
    },
  })
  assert.equal(forgedRenderer.j1_records[0].answer_success, false)
  assert.ok(forgedRenderer.j1_records[0].failure_codes.includes(
    'correct_final_answer_missing',
  ))

  const drifted = await runFirstVerticalSliceEvaluation({
    loaded_candidate: loadedCandidate,
    execution_mode: 'offline_test',
    execution_actor_id: 'offline-execution-agent',
    runs: 1,
    journey_judgments: receivedJudgments(),
    now: advancingClock(),
    run_j1_trial: async () => {
      const result = await guardedFallbackJ1Result(1)
      result.usage.attempts[0].response_model = 'different-provider-model'
      return result
    },
  })
  assert.equal(drifted.j1_records[0].workflow_completed, false)
  assert.equal(drifted.j1_records[0].answer_success, false)
  assert.equal(
    drifted.j1_records[0].zero_tolerance_counts.provider_identity_drift,
    1,
  )
  assert.ok(drifted.j1_records[0].failure_codes.includes(
    'provider_identity_mismatch',
  ))
})

test('第 11 条限流 fallback 仍失败，renderer 末条顺序保持精确合同', async () => {
  const rejectedLimitFallback = await runFirstVerticalSliceEvaluation({
    loaded_candidate: loadedCandidate,
    execution_mode: 'offline_test',
    execution_actor_id: 'offline-execution-agent',
    runs: 1,
    journey_judgments: receivedJudgments(),
    now: advancingClock(),
    run_j1_trial: async () => {
      const result = structuredClone(await successfulJ1Result(1))
      const startedAt = '2026-08-19T07:00:11.000Z'
      result.usage.attempt_count = 10
      result.usage.attempts = [
        ...Array.from(
          { length: 10 },
          (_, index) => providerAttempt(index + 1, 'cognition'),
        ),
        {
          ...providerAttempt(11, 'renderer'),
          response_model: null,
          started_at_utc: startedAt,
          ended_at_utc: startedAt,
          latency_ms: 0,
          outcome: 'limit_rejected',
          failure_code: 'provider_request_limit_exceeded',
        },
      ]
      result.answer = {
        answer: renderCountryOutageDeterministicFallback(result.answer_context),
        source: 'deterministic_fallback',
        guard_result: {
          schema_version: 'domeye_agent_response_guard_v1',
          decision: 'block',
          reason_codes: ['renderer_failed_or_invalid'],
        },
        render_attempt: {
          status: 'failed',
          draft: null,
          failure_code: 'renderer_failed_or_invalid',
        },
      }
      result.outcome = 'answer_not_accepted'
      result.goal_state.status = 'stopped'
      return result
    },
  })
  assert.equal(rejectedLimitFallback.j1_records[0].workflow_completed, false)
  assert.equal(rejectedLimitFallback.j1_records[0].answer_success, false)
  assert.equal(rejectedLimitFallback.j1_records[0].passed, false)
  assert.equal(rejectedLimitFallback.j1_records[0].answer_source, null)
  assert.equal(
    rejectedLimitFallback.j1_records[0].evidence.response_guard.answer_source,
    'deterministic_fallback',
  )
  assert.deepEqual(rejectedLimitFallback.j1_records[0].failure_codes, [
    'answer_not_accepted',
    'correct_final_answer_missing',
    'public_completion_gate_rejected',
  ])

  const wrongLimitCode = await runFirstVerticalSliceEvaluation({
    loaded_candidate: loadedCandidate,
    execution_mode: 'offline_test',
    execution_actor_id: 'offline-execution-agent',
    runs: 1,
    journey_judgments: receivedJudgments(),
    now: advancingClock(),
    run_j1_trial: async () => {
      const original = await successfulJ1Result(1)
      original.usage.attempt_count = 10
      original.usage.attempts = [
        ...Array.from(
          { length: 10 },
          (_, index) => providerAttempt(index + 1, 'cognition'),
        ),
        {
          ...providerAttempt(11, 'renderer'),
          response_model: null,
          started_at_utc: '2026-08-19T07:00:11.000Z',
          ended_at_utc: '2026-08-19T07:00:11.000Z',
          latency_ms: 0,
          outcome: 'limit_rejected',
          failure_code: 'wrong_limit_code',
        },
      ]
      original.answer = {
        answer: renderCountryOutageDeterministicFallback(
          original.answer_context,
        ),
        source: 'deterministic_fallback',
        guard_result: {
          schema_version: 'domeye_agent_response_guard_v1',
          decision: 'block',
          reason_codes: ['renderer_failed_or_invalid'],
        },
        render_attempt: {
          status: 'failed',
          draft: null,
          failure_code: 'renderer_failed_or_invalid',
        },
      }
      original.outcome = 'answer_not_accepted'
      original.goal_state.status = 'stopped'
      return original
    },
  })
  assert.ok(wrongLimitCode.j1_records[0].failure_codes.includes(
    'provider_usage_invalid',
  ))

  const rendererNotLast = await runFirstVerticalSliceEvaluation({
    loaded_candidate: loadedCandidate,
    execution_mode: 'offline_test',
    execution_actor_id: 'offline-execution-agent',
    runs: 1,
    journey_judgments: receivedJudgments(),
    now: advancingClock(),
    run_j1_trial: async () => {
      const result = structuredClone(await successfulJ1Result(1))
      result.usage.attempt_count = 5
      result.usage.attempts.push(providerAttempt(5, 'cognition'))
      return result
    },
  })
  assert.ok(rendererNotLast.j1_records[0].failure_codes.includes(
    'correct_final_answer_missing',
  ))
})

test('J1 拒绝自洽重签但越界的准入与非确定性 identity receipt', async () => {
  const result = await runFirstVerticalSliceEvaluation({
    loaded_candidate: loadedCandidate,
    execution_mode: 'offline_test',
    execution_actor_id: 'offline-execution-agent',
    runs: 2,
    journey_judgments: receivedJudgments(),
    now: advancingClock(),
    run_j1_trial: async ({ ordinal }) => {
      const value = structuredClone(await successfulJ1Result(ordinal))
      if (ordinal === 1) {
        value.loop.admission_receipts[0].budget.model_api_attempts_used = 9
        value.loop.admission_receipts[0] = reissueAdmissionReceipt(
          value.loop.admission_receipts[0],
        )
      } else {
        value.identity_receipt.receipt_id = 'identity-receipt-forged'
      }
      return value
    },
  })
  assert.ok(result.j1_records[0].failure_codes.includes(
    'admission_receipt_contract_invalid',
  ))
  assert.ok(result.j1_records[1].failure_codes.includes(
    'identity_receipt_invalid',
  ))
  assert.deepEqual(
    result.j1_records.map((trial) => trial.public_completion_gate_passed),
    [false, false],
  )
})

test('固定 runtime principal 与执行回执 principal 漂移时公开门拒绝', async () => {
  const result = await runFirstVerticalSliceEvaluation({
    loaded_candidate: loadedCandidate,
    execution_mode: 'offline_test',
    execution_actor_id: 'offline-execution-agent',
    runtime_principal_binding: {
      principal_id: 'forged-evaluation-principal',
      authorization_scopes: ['country_outage:read'],
    },
    runs: 1,
    drive_adversarial_cases: true,
    now: advancingClock(),
    run_j1_trial: async () => await successfulJ1Result(1),
  })
  assert.deepEqual(result.summary.runtime_principal_binding, {
    principal_id: 'forged-evaluation-principal',
    authorization_scopes: ['country_outage:read'],
  })
  assert.equal(result.j1_records[0].public_completion_gate_passed, false)
  assert.deepEqual(result.j1_records[0].failure_codes, [
    'public_completion_gate_rejected',
  ])
})

test('安全停止与 structured failure 都形成可复核失败闭包；正确拒绝不能伪报完成', async () => {
  const structuredError = await loopFailureJ1Error(2)
  const result = await runFirstVerticalSliceEvaluation({
    loaded_candidate: loadedCandidate,
    execution_mode: 'offline_test',
    execution_actor_id: 'offline-execution-agent',
    runs: 2,
    drive_adversarial_cases: true,
    now: advancingClock(),
    run_j1_trial: async ({ ordinal }) => {
      if (ordinal === 1) return await stoppedJ1Result(ordinal)
      throw structuredError
    },
  })
  assert.deepEqual(result.j1_records.map((item) => [
    item.workflow_completed,
    item.answer_success,
    item.passed,
  ]), [[false, false, false], [false, false, false]])
  assert.equal(result.j1_records[0].evidence.outcome, 'stopped')
  assert.equal(
    result.j1_records[1].evidence.structured_failure
      .loop_failure.action_receipts.length,
    1,
  )
  assert.equal(result.j1_records[1].provider_attempt_count, 2)
  assert.equal(
    result.j1_records[1].zero_tolerance_assessment.status,
    'complete',
  )
  const wrongIdentityError = new DomeyeFirstSliceRunError(
    structuredError.code,
    {
      ...structuredClone(structuredError.evidence),
      candidate_id: 'wrong-candidate-in-partial-failure',
    },
  )
  const wrongIdentityFailure = await runFirstVerticalSliceEvaluation({
    loaded_candidate: loadedCandidate,
    execution_mode: 'offline_test',
    execution_actor_id: 'offline-execution-agent',
    runs: 1,
    journey_judgments: receivedJudgments(),
    now: advancingClock(),
    run_j1_trial: async () => { throw wrongIdentityError },
  })
  assert.equal(
    wrongIdentityFailure.j1_records[0]
      .zero_tolerance_counts.wrong_identity_data_adopted,
    1,
  )
  assert.equal(
    wrongIdentityFailure.j1_records[0].zero_tolerance_assessment.status,
    'complete',
  )

  const outputRoot = mkdtempSync(join(tmpdir(), 'first-slice-failures-'))
  roots.push(outputRoot)
  const output = await writeEvaluationArtifacts(result, outputRoot)
  const evidenceJsonl = readFileSync(output.paths.evidence_jsonl, 'utf8')
  assert.throws(() => finalizeIndependentAcceptanceRecord({
    summary: result.summary,
    evidence_jsonl: evidenceJsonl,
    independent_review: rejectedReview(result, evidenceJsonl),
  }), /evidence_j1_formal_batch_invalid/)

  const tamperedLines = evidenceJsonl.trimEnd().split('\n').map(JSON.parse)
  const rejectedJ2 = tamperedLines.find((line) =>
    line.record_type === 'journey_judgment'
      && line.payload.journey_id === 'J2'
  )
  rejectedJ2.payload.workflow_completed = true
  rejectedJ2.payload.answer_success = true
  rejectedJ2.payload.passed = true
  const tamperedJsonl = `${tamperedLines.map(JSON.stringify).join('\n')}\n`
  assert.throws(() => finalizeIndependentAcceptanceRecord({
    summary: result.summary,
    evidence_jsonl: tamperedJsonl,
    independent_review: rejectedReview(result, tamperedJsonl),
  }), /evidence_j1_formal_batch_invalid/)

  const dispositionLines = evidenceJsonl.trimEnd().split('\n').map(JSON.parse)
  const dispositionJ2 = dispositionLines.find((line) =>
    line.record_type === 'journey_judgment'
      && line.payload.journey_id === 'J2'
  )
  const trace = dispositionJ2.payload.evidence.observation.actual_execution
  trace.final_goal_state.status = 'satisfied'
  trace.disposition.disposition = 'goal_satisfied'
  dispositionJ2.payload.evidence_digest =
    `sha256:${canonicalJsonSha256(dispositionJ2.payload.evidence)}`
  dispositionJ2.payload.evidence_refs = [
    `evaluation-evidence-${dispositionJ2.payload.evidence_digest}`,
  ]
  const dispositionJsonl =
    `${dispositionLines.map(JSON.stringify).join('\n')}\n`
  assert.throws(() => finalizeIndependentAcceptanceRecord({
    summary: result.summary,
    evidence_jsonl: dispositionJsonl,
    independent_review: rejectedReview(result, dispositionJsonl),
  }), /evidence_j1_formal_batch_invalid/)
})

test('目标绑定复用 Candidate loader 与 Runtime；注入依赖不能冒充真实运行', async () => {
  const calls = []
  const targetConfig = {
    project_root: process.cwd(),
    manifest_path: 'candidate.json',
    model_auth_path: '/run/domeye/evaluation-auth.json',
    api_base_url: 'http://127.0.0.1:28471',
    event_reference: 'country_outage/2026-02-27/IR/evaluation',
    principal_id: 'evaluation-principal',
    authorization_scopes: ['country_outage:read'],
  }
  const dependencyFunctions = {
    manifest_loader: async (options) => {
      calls.push({ kind: 'manifest', options })
      return loadedCandidate
    },
    model_binding_factory: async (options) => {
      calls.push({ kind: 'model', options })
      return { identity: modelIdentity, model: {}, model_runtime: {}, thinking_level: 'off' }
    },
  }
  const target = await bindRealFirstSliceEvaluationTarget(
    targetConfig,
    dependencyFunctions,
  )
  assert.equal(target.loaded_candidate, loadedCandidate)
  assert.equal(target.execution_mode, 'offline_test')
  assert.deepEqual(target.runtime_principal_binding, {
    principal_id: 'evaluation-principal',
    authorization_scopes: ['country_outage:read'],
  })
  assert.equal(target.runtime_source_binding.source_scope, 'agent-sidecar/src')
  assert.equal(typeof target.run_j1_trial, 'function')
  assert.equal(calls.length, 2)

  const inheritedDependencies = Object.create(dependencyFunctions)
  const inheritedTarget = await bindRealFirstSliceEvaluationTarget(
    targetConfig,
    inheritedDependencies,
  )
  assert.equal(inheritedTarget.execution_mode, 'offline_test')

  const hiddenDependencies = {}
  Object.defineProperties(hiddenDependencies, {
    manifest_loader: {
      value: dependencyFunctions.manifest_loader,
      enumerable: false,
    },
    model_binding_factory: {
      value: dependencyFunctions.model_binding_factory,
      enumerable: false,
    },
  })
  const hiddenTarget = await bindRealFirstSliceEvaluationTarget(
    targetConfig,
    hiddenDependencies,
  )
  assert.equal(hiddenTarget.execution_mode, 'offline_test')

  await assert.rejects(
    bindRealFirstSliceEvaluationTarget({
      ...targetConfig,
      manifest_path: 'missing-candidate-for-empty-injection.json',
    }, {}),
    (error) => error?.code === 'manifest_file_invalid',
  )
  await assert.rejects(
    runFirstVerticalSliceEvaluation({
      loaded_candidate: loadedCandidate,
      execution_mode: 'real_runtime',
      execution_actor_id: 'forged-real-runner',
      runs: 1,
      run_j1_trial: async () => await successfulJ1Result(1),
      drive_adversarial_cases: true,
    }),
    /real_runtime_runner_not_source_bound/,
  )
  await assert.rejects(
    bindRealFirstSliceEvaluationTarget({
      project_root: process.cwd(),
      manifest_path: 'candidate.json',
      model_auth_path: '/run/domeye/evaluation-auth.json',
      api_base_url: 'http://127.0.0.1:28471/api/v2/',
      event_reference: 'country_outage/invalid-mock',
      principal_id: 'evaluation-principal',
      authorization_scopes: ['country_outage:read'],
    }),
    /api_endpoint_policy_rejected/,
  )
})

test('仅 exactly 30 的 JSONL 可重放三阶段失败闭包，固定 27/30 与 8/10 门槛', async () => {
  const fallbackOrdinals = new Set([4, 5, 7])
  const result = await runFirstVerticalSliceEvaluation({
    loaded_candidate: loadedCandidate,
    execution_mode: 'offline_test',
    execution_actor_id: 'offline-execution-agent',
    runs: 30,
    drive_adversarial_cases: true,
    now: advancingClock(),
    run_j1_trial: async ({ ordinal }) => {
      if (ordinal === 4) throw await loopFailureJ1Error(ordinal)
      if (ordinal === 5) throw await decisionFailureJ1Error(ordinal)
      if (ordinal === 7) throw await answerFailureJ1Error(ordinal)
      return await successfulJ1Result(ordinal)
    },
  })
  assert.equal(result.summary.j1.pass_at_1.denominator, 30)
  assert.equal(result.summary.j1.pass_at_1.required_numerator, 27)
  assert.equal(result.summary.j1.pass_at_1.numerator, 27)
  assert.equal(result.summary.j1.pass_at_1.met, true)
  assert.equal(result.summary.j1.pass_power_3.denominator, 10)
  assert.equal(result.summary.j1.pass_power_3.required_numerator, 8)
  assert.equal(result.summary.j1.pass_power_3.numerator, 8)
  assert.equal(result.summary.j1.pass_power_3.met, true)
  for (const ordinal of fallbackOrdinals) {
    const trial = result.j1_records[ordinal - 1]
    assert.deepEqual([
      trial.workflow_completed,
      trial.answer_success,
      trial.passed,
      trial.answer_source,
    ], [false, false, false, null])
    assert.equal(trial.public_completion_gate_passed, false)
  }
  assert.deepEqual([4, 5, 7].map((ordinal) =>
    result.j1_records[ordinal - 1].evidence.structured_failure.failure_stage
  ), ['loop', 'decision', 'answer'])
  assert.deepEqual([4, 5, 7].map((ordinal) =>
    result.j1_records[ordinal - 1].failure_codes[0]
  ), [
    'cognition_provider_failed',
    'decision_rejected',
    'answer_not_accepted',
  ])
  assert.deepEqual(result.summary.j1.successful_answer_source_counts, {
    renderer: 27,
    deterministic_fallback: 0,
  })
  const outputRoot = mkdtempSync(join(tmpdir(), 'first-slice-evaluation-'))
  roots.push(outputRoot)
  const output = await writeEvaluationArtifacts(
    result,
    outputRoot,
    () => new Date('2026-08-19T08:00:00.000Z'),
  )
  const evidenceJsonl = readFileSync(output.paths.evidence_jsonl, 'utf8')
  const review = {
    schema_version: 'domeye_first_slice_independent_review_v1',
    reviewer_actor_id: 'independent-reviewer',
    reviewer_role: 'independent_acceptance_reviewer',
    independent_from_execution: true,
    candidate_id: candidateId,
    summary_digest: result.summary.summary_digest,
    evidence_jsonl_sha256: `sha256:${createHash('sha256')
      .update(evidenceJsonl).digest('hex')}`,
    decision: 'rejected',
    dg1_decision: 'REPAIR',
    rationale_codes: ['j1_real_trials_not_run'],
    reviewed_at_utc: '2026-08-19T09:00:00.000Z',
  }
  const record = finalizeIndependentAcceptanceRecord({
    summary: result.summary,
    evidence_jsonl: evidenceJsonl,
    independent_review: review,
  })
  assert.equal(record.acceptance_state, 'rejected')
  assert.equal(record.dg1_decision, 'REPAIR')
  assert.equal(record.prohibited_claims.dg1_decided, true)

  const falseToTrueLines = evidenceJsonl.trimEnd().split('\n').map(JSON.parse)
  const falseTrial = falseToTrueLines.find((line) =>
    line.record_type === 'j1_trial' && line.payload.ordinal === 4
  )
  assert.equal(falseTrial.payload.public_completion_gate_passed, false)
  falseTrial.payload.public_completion_gate_passed = true
  const falseToTrueJsonl =
    `${falseToTrueLines.map(JSON.stringify).join('\n')}\n`
  assert.throws(() => finalizeIndependentAcceptanceRecord({
    summary: result.summary,
    evidence_jsonl: falseToTrueJsonl,
    independent_review: rejectedReview(result, falseToTrueJsonl),
  }), /evidence_j1_trial_invalid/)

  const receiptTamperLines = evidenceJsonl.trimEnd().split('\n').map(JSON.parse)
  const receiptTrial = receiptTamperLines.find((line) =>
    line.record_type === 'j1_trial' && line.payload.ordinal === 1
  )
  receiptTrial.payload.evidence.replay_closure.identity_receipt.receipt_id =
    'identity-receipt-forged'
  const receiptTamperJsonl =
    `${receiptTamperLines.map(JSON.stringify).join('\n')}\n`
  assert.throws(() => finalizeIndependentAcceptanceRecord({
    summary: result.summary,
    evidence_jsonl: receiptTamperJsonl,
    independent_review: rejectedReview(result, receiptTamperJsonl),
  }), /evidence_j1_trial_invalid/)

  const principalTamperLines = evidenceJsonl.trimEnd().split('\n').map(JSON.parse)
  principalTamperLines[0].payload.runtime_principal_binding.principal_id =
    'forged-evaluation-principal'
  const principalTamperJsonl =
    `${principalTamperLines.map(JSON.stringify).join('\n')}\n`
  assert.throws(() => finalizeIndependentAcceptanceRecord({
    summary: result.summary,
    evidence_jsonl: principalTamperJsonl,
    independent_review: rejectedReview(result, principalTamperJsonl),
  }), /evidence_binding_mismatch/)

  const assertFailureClosureTamperRejected = (ordinal, mutate) => {
    const lines = evidenceJsonl.trimEnd().split('\n').map(JSON.parse)
    const trial = lines.find((line) =>
      line.record_type === 'j1_trial'
        && line.payload.ordinal === ordinal
    )
    mutate(trial.payload.evidence.structured_failure)
    const tampered = `${lines.map(JSON.stringify).join('\n')}\n`
    assert.throws(() => finalizeIndependentAcceptanceRecord({
      summary: result.summary,
      evidence_jsonl: tampered,
      independent_review: rejectedReview(result, tampered),
    }), /evidence_j1_trial_invalid/)
  }
  assertFailureClosureTamperRejected(4, (failure) => {
    failure.loop_failure.failure_code = 'forged_loop_failure'
  })
  assertFailureClosureTamperRejected(5, (failure) => {
    failure.loop.decision_protocol_rejections = []
  })
  assertFailureClosureTamperRejected(5, (failure) => {
    failure.goal_state.state_revision = failure.loop.goal_state.state_revision
  })
  assertFailureClosureTamperRejected(7, (failure) => {
    failure.answer.answer += ' 非确定性伪造文本'
  })

  const wrongBatchLines = evidenceJsonl.trimEnd().split('\n').map(JSON.parse)
  const wrongBatchTrial = wrongBatchLines.find((line) =>
    line.record_type === 'j1_trial'
  )
  wrongBatchTrial.payload.evaluation_run_id = 'evaluation-run-forged'
  const wrongBatchJsonl = `${wrongBatchLines.map(JSON.stringify).join('\n')}\n`
  assert.throws(() => finalizeIndependentAcceptanceRecord({
    summary: result.summary,
    evidence_jsonl: wrongBatchJsonl,
    independent_review: rejectedReview(result, wrongBatchJsonl),
  }), /evidence_j1_trial_invalid/)

  const duplicateLines = evidenceJsonl.trimEnd().split('\n').map(JSON.parse)
  const duplicateTrials = duplicateLines.filter((line) =>
    line.record_type === 'j1_trial'
  )
  duplicateTrials[1].payload.evidence = structuredClone(
    duplicateTrials[0].payload.evidence,
  )
  const duplicateJsonl = `${duplicateLines.map(JSON.stringify).join('\n')}\n`
  assert.throws(() => finalizeIndependentAcceptanceRecord({
    summary: result.summary,
    evidence_jsonl: duplicateJsonl,
    independent_review: rejectedReview(result, duplicateJsonl),
  }), /evidence_j1_successful_trial_duplicate/)

  assert.throws(() => finalizeIndependentAcceptanceRecord({
    summary: result.summary,
    evidence_jsonl: evidenceJsonl,
    independent_review: { ...review, reviewer_actor_id: ' offline-execution-agent ' },
  }), /reviewer_actor_id_invalid/)
  assert.throws(() => finalizeIndependentAcceptanceRecord({
    summary: result.summary,
    evidence_jsonl: evidenceJsonl,
    independent_review: { ...review, decision: 'accepted', dg1_decision: 'REPAIR' },
  }), /blocked_evidence_cannot_be_accepted/)
  assert.throws(() => finalizeIndependentAcceptanceRecord({
    summary: result.summary,
    evidence_jsonl: '{"record_type":"evaluation_summary"}\n',
    independent_review: review,
  }), /evidence_jsonl_structure_invalid/)

  const selfReported = await offlineEvaluation({ runs: 30, driven: false })
  const selfReportedRoot = mkdtempSync(join(
    tmpdir(),
    'first-slice-self-reported-',
  ))
  roots.push(selfReportedRoot)
  const selfReportedOutput = await writeEvaluationArtifacts(
    selfReported,
    selfReportedRoot,
  )
  const selfReportedJsonl = readFileSync(
    selfReportedOutput.paths.evidence_jsonl,
    'utf8',
  )
  assert.throws(() => finalizeIndependentAcceptanceRecord({
    summary: selfReported.summary,
    evidence_jsonl: selfReportedJsonl,
    independent_review: rejectedReview(selfReported, selfReportedJsonl),
  }), /evidence_journey_invalid/)
})

test('评测器执行 Candidate 绑定源码并复用现有合同，不读取 dist', () => {
  const source = readFileSync(
    fileURLToPath(new URL('./evaluator.mjs', import.meta.url)),
    'utf8',
  )
  assert.match(source, /agent-sidecar\/src\/agent\/candidate-manifest\.ts/)
  assert.match(source, /agent-sidecar\/src\/agent\/contracts\.ts/)
  assert.match(source, /agent-sidecar\/src\/agent\/first-slice-runtime\.ts/)
  assert.doesNotMatch(source, /agent-sidecar\/dist\//)
  assert.doesNotMatch(
    source,
    /(?:DomeyeDataIdentity|DomeyeActionReceipt|DomeyeArtifactEnvelope|DomeyeTypedFinding|DomeyeAnswerContext)Schema\s*=/,
  )
})
