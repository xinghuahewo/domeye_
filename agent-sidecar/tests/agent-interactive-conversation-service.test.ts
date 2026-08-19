import { createHash } from 'node:crypto'
import assert from 'node:assert/strict'
import test from 'node:test'

import type {
  DomeyeCapabilityObservation,
  DomeyeCapabilityProposal,
  DomeyeDataIdentity,
  DomeyeExecutionBinding,
  DomeyeGoalState,
  DomeyeRendererDraft,
  DomeyeTypedFinding,
} from '../src/agent/contracts.js'
import {
  DomeyeCapabilityGateway,
  type CountryOutageMetricSeriesRead,
  type CountryOutageSeriesReadModel,
} from '../src/agent/capability-execution.js'
import type {
  DomeyeVerifiedIdentityReceipt,
} from '../src/agent/country-outage-read-model.js'
import {
  DOMEYE_FIRST_SLICE_QUESTION,
  type DomeyeFirstSliceCandidateBinding,
  type DomeyeFirstSliceRunRequest,
  type DomeyeFirstSliceRunResult,
  DomeyeFirstSliceRuntime,
} from '../src/agent/first-slice-runtime.js'
import {
  DomeyeConversationError,
  DomeyeInteractiveConversationService,
} from '../src/agent/interactive-conversation-service.js'
import {
  buildCountryOutageAnswerContext,
  buildCountryOutageSeriesExtremaFinding,
  COUNTRY_OUTAGE_MANDATORY_LIMITATIONS,
  guardCountryOutageResponse,
  renderCountryOutageDeterministicFallback,
} from '../src/agent/finding-answer.js'
import {
  DomeyeTrustKernel,
  type DomeyeAdmissionRequest,
  type DomeyeAdmittedDecision,
} from '../src/agent/trust-kernel.js'
import { canonicalJsonSha256 } from '../src/shared/deterministic-json.js'

const NOW = '2026-08-19T07:00:00.000Z'
const EVENT_REFERENCE =
  'country_outage/2026-02-27 00:10:00/IR/1/first-slice'
const CANDIDATE_ID = `git:xinghuahewo/domeye_@${'a'.repeat(40)}`
const MODEL_IDENTITY = {
  provider: 'openai',
  model: 'gpt-test',
  model_version: 'gpt-test-2026-08-19',
  expected_response_model: 'gpt-test',
} as const
const PRINCIPAL = {
  userId: 'user-1',
  authorizationScope: 'country_outage_event_read:IR',
}

const IDENTITY: DomeyeDataIdentity = {
  event_type: 'country_outage',
  incident_id: 'incident_go_v1_a1de26f854831330c616a72af21597eb',
  publication_id:
    'country_outage_publication_v1_989f698fb6f6c32579eebe7bb2bc833f',
  revision: 1,
  collector_id: 'rrc25',
  cohort_id: 'country_event_cohort_v1_1e04abfc6430776bef20403fac528698',
  country_code: 'IR',
  window_start_utc: '2026-02-27T00:10:00Z',
  window_end_utc: '2026-02-27T00:25:00Z',
  data_through: '2026-02-27T00:25:00Z',
  is_final_in_data_range: false,
  lifecycle_state: 'event_end_unknown',
}

function referenceDigest(reference: string): string {
  return createHash('sha256').update(reference, 'utf8').digest('hex')
}

function identityReceipt(): DomeyeVerifiedIdentityReceipt {
  return {
    schema_version: 'domeye_verified_data_identity_receipt_v1',
    receipt_id: 'identity-receipt-1',
    candidate_id: CANDIDATE_ID,
    reference_sha256: referenceDigest(EVENT_REFERENCE),
    data_identity: structuredClone(IDENTITY),
    resolver_response_sha256: 'b'.repeat(64),
    overview_response_sha256: 'c'.repeat(64),
    evidence_refs: ['domeye:/api/v2/events/resolve', 'domeye:/overview'],
    immutable: true,
    verified_at_utc: NOW,
  }
}

function sha(character: string): `sha256:${string}` {
  return `sha256:${character.repeat(64)}`
}

const SERIES_DIGEST = sha('9')
const TOOL_BINDING: DomeyeExecutionBinding = {
  execution_unit_id: 'TOOL-03',
  execution_unit_name: 'read_metric_series',
  execution_unit_version: '1.0.0',
  contract_digest: sha('1'),
  implementation_digest: sha('2'),
  semantic_digest: sha('3'),
}
const OPERATOR_BINDING: DomeyeExecutionBinding = {
  execution_unit_id: 'OP-01',
  execution_unit_name: 'series_extrema',
  execution_unit_version: '1.0.0',
  contract_digest: sha('4'),
  implementation_digest: sha('5'),
  semantic_digest: sha('6'),
}
const CANDIDATE: DomeyeFirstSliceCandidateBinding = {
  candidate_id: CANDIDATE_ID,
  contract_version: 'domeye.first-vertical-slice/v1.0',
  contract_digest: sha('d'),
  data_identity: IDENTITY,
  series_response_sha256: SERIES_DIGEST,
  model_identity: {
    candidate_id: CANDIDATE_ID,
    resource_sha256: sha('e'),
    ...MODEL_IDENTITY,
    api: 'openai-completions',
    base_url: 'https://provider.invalid/v1',
    maximum_output_tokens: 4_096,
    thinking_level: 'off',
    pi_version: '0.84.1',
  },
  budget_policy: {
    model_api_attempt_limit: 10,
    approved_action_limit: 2,
    cost_policy: 'audit_only',
    monetary_limit_usd: null,
  },
  policy: {
    policy_id: 'country-outage-first-slice-policy-v1',
    policy_digest: sha('7'),
    state: 'active',
    allowed_capability_ids: ['CAP-006', 'CAP-016'],
  },
  registry: {
    registry_snapshot_id: 'registry-first-slice-v1',
    registry_digest: sha('8'),
    state: 'active',
    capabilities: [
      {
        capability_id: 'CAP-006',
        state: 'active',
        execution_binding: TOOL_BINDING,
      },
      {
        capability_id: 'CAP-016',
        state: 'active',
        execution_binding: OPERATOR_BINDING,
      },
    ],
  },
}

function candidate(): DomeyeFirstSliceCandidateBinding {
  return structuredClone(CANDIDATE)
}

function finding(valueState: 'known' | 'empty'): DomeyeTypedFinding {
  const known = valueState === 'known'
  const findingContent = {
    schema_version: 'domeye_agent_typed_finding_v1' as const,
    finding_type: 'fixed_visible_ipv4_series_extrema' as const,
    value_state: valueState,
    candidate_id: CANDIDATE_ID,
    tenant_id: 'domeye' as const,
    data_identity: IDENTITY,
    metric: 'fixed_visible_ipv4_address_count' as const,
    unit: 'unique_ipv4_address' as const,
    population_definition:
      'normalized_deduplicated_merged_fixed_prefix_ipv4_unique_address_union' as const,
    values: {
      first: known ? 10_156_800 : null,
      first_at_utc: known ? '2026-02-27T00:10:00Z' : null,
      last: known ? 10_069_760 : null,
      last_at_utc: known ? '2026-03-11T00:00:00Z' : null,
      minimum: known ? 9_577_728 : null,
      minimum_at_utc: known ? '2026-02-28T14:35:00Z' : null,
      maximum: known ? 10_156_800 : null,
      maximum_at_utc: known ? '2026-02-27T00:10:00Z' : null,
      difference: known ? 579_072 : null,
      net_change: known ? -87_040 : null,
    },
    time_slot_count: 3_457,
    observed_point_count: known ? 3_457 : 0,
    null_point_count: 0,
    completeness_state: 'complete' as const,
    limitation_codes: COUNTRY_OUTAGE_MANDATORY_LIMITATIONS.map(
      (limitation) => limitation.code,
    ),
    tool_version: '1.0.0' as const,
    operator_version: '1.0.0' as const,
    artifact_refs: ['artifact-series', 'artifact-extrema'],
    receipt_refs: ['receipt-series', 'receipt-extrema'],
    evidence_refs: ['domeye:evidence:series'],
  }
  const resultDigest = `sha256:${canonicalJsonSha256(findingContent)}`
  return {
    ...findingContent,
    finding_id: `finding-${resultDigest}`,
    result_digest: resultDigest,
  }
}

function legacySelfReportedResult(
  valueState: 'known' | 'empty' = 'known',
): DomeyeFirstSliceRunResult {
  const resultFinding = finding(valueState)
  const answerContext = buildCountryOutageAnswerContext(
    resultFinding,
    candidate().contract_digest,
  )
  const answerText = renderCountryOutageDeterministicFallback(answerContext)
  const draft = {
    schema_version: 'domeye_agent_renderer_draft_v1' as const,
    context_id: answerContext.context_id,
    finding_id: resultFinding.finding_id,
    candidate_id: CANDIDATE_ID,
    publication_id: IDENTITY.publication_id,
    revision: IDENTITY.revision,
    collector_id: 'rrc25' as const,
    window_start_utc: IDENTITY.window_start_utc,
    window_end_utc: IDENTITY.window_end_utc,
    metric: resultFinding.metric,
    unit: resultFinding.unit,
    values: resultFinding.values,
    observer_scope_zh: answerContext.observer_scope_zh,
    limitations_zh: answerContext.mandatory_limitations_zh,
    evidence_refs: answerContext.evidence_refs,
    text: answerText,
  }
  const guardResult = guardCountryOutageResponse(answerContext, draft)
  if (valueState === 'known') assert.equal(guardResult.decision, 'pass')
  return {
    outcome: 'completed',
    candidate_id: CANDIDATE_ID,
    semantic_goal: { goal_id: 'goal-1', data_identity: IDENTITY },
    goal_state: {
      goal_id: 'goal-1',
      state_revision: 4,
      status: 'satisfied',
      completed_capability_ids: ['CAP-006', 'CAP-016'],
      artifact_ids: ['artifact-series', 'artifact-extrema'],
      finding_ids: [resultFinding.finding_id],
    },
    loop: {
      goal_state: {
        goal_id: 'goal-1',
        state_revision: 3,
        status: 'satisfied',
      },
      disposition: { disposition: 'goal_satisfied' },
      admission_receipts: [
        {
          receipt_id: 'admission-series',
          capability_id: 'CAP-006',
          decision: 'admitted',
          reason_code: null,
          candidate_id: CANDIDATE_ID,
          data_identity: IDENTITY,
        },
        {
          receipt_id: 'admission-extrema',
          capability_id: 'CAP-016',
          decision: 'admitted',
          reason_code: null,
          candidate_id: CANDIDATE_ID,
          data_identity: IDENTITY,
        },
      ],
      action_receipts: [
        {
          receipt_id: 'receipt-series',
          admission_receipt_id: 'admission-series',
          action_id: 'action-series',
          capability_id: 'CAP-006',
          candidate_id: CANDIDATE_ID,
          data_identity: IDENTITY,
          status: 'succeeded',
          artifact_ids: ['artifact-series'],
          failure_code: null,
        },
        {
          receipt_id: 'receipt-extrema',
          admission_receipt_id: 'admission-extrema',
          action_id: 'action-extrema',
          capability_id: 'CAP-016',
          candidate_id: CANDIDATE_ID,
          data_identity: IDENTITY,
          status: 'succeeded',
          artifact_ids: ['artifact-extrema'],
          failure_code: null,
        },
      ],
      artifacts: [
        {
          artifact_id: 'artifact-series',
          artifact_kind: 'metric_series',
          candidate_id: CANDIDATE_ID,
          data_identity: IDENTITY,
          immutable: true,
          content_digest: `sha256:${'f'.repeat(64)}`,
        },
        {
          artifact_id: 'artifact-extrema',
          artifact_kind: 'series_extrema',
          candidate_id: CANDIDATE_ID,
          data_identity: IDENTITY,
          immutable: true,
          content_digest: `sha256:${'f'.repeat(64)}`,
        },
      ],
      observations: [
        {
          observation_id: 'observation-series',
          action_id: 'action-series',
          capability_id: 'CAP-006',
          status: 'succeeded',
          reason_code: null,
          artifact_ref: 'artifact-series',
          data_identity: IDENTITY,
        },
        {
          observation_id: 'observation-extrema',
          action_id: 'action-extrema',
          capability_id: 'CAP-016',
          status: 'succeeded',
          reason_code: null,
          artifact_ref: 'artifact-extrema',
          data_identity: IDENTITY,
        },
      ],
      decision_protocol_rejections: [],
    },
    finding: resultFinding,
    answer_context: answerContext,
    answer: {
      answer: answerText,
      source: 'renderer',
      guard_result: guardResult,
      render_attempt: {
        status: 'completed',
        draft,
        failure_code: null,
      },
    },
    usage: {
      attempt_count: 3,
      maximum_attempt_count: 10,
      cost_policy: 'audit_only',
      tokens: {
        input: 10,
        output: 5,
        cache_read: 0,
        cache_write: 0,
        total: 15,
      },
      estimated_cost_usd: 0.01,
      attempts: [
        {
          attempt_id: 1,
          phase: 'cognition',
          ...MODEL_IDENTITY,
          response_model: MODEL_IDENTITY.expected_response_model,
          started_at_utc: NOW,
          ended_at_utc: NOW,
          latency_ms: 1,
          outcome: 'completed',
          failure_code: null,
        },
        {
          attempt_id: 2,
          phase: 'cognition',
          ...MODEL_IDENTITY,
          response_model: MODEL_IDENTITY.expected_response_model,
          started_at_utc: NOW,
          ended_at_utc: NOW,
          latency_ms: 1,
          outcome: 'completed',
          failure_code: null,
        },
        {
          attempt_id: 3,
          phase: 'renderer',
          ...MODEL_IDENTITY,
          response_model: MODEL_IDENTITY.expected_response_model,
          started_at_utc: NOW,
          ended_at_utc: NOW,
          latency_ms: 1,
          outcome: 'completed',
          failure_code: null,
        },
      ],
    },
  } as unknown as DomeyeFirstSliceRunResult
}

function proposal(
  goalId: string,
  revision: number,
  capabilityId: 'CAP-006' | 'CAP-016',
  sourceArtifactId?: string,
): DomeyeCapabilityProposal {
  return capabilityId === 'CAP-006'
    ? {
        schema_version: 'domeye_agent_capability_proposal_v1',
        goal_id: goalId,
        goal_state_revision: revision,
        capability_id: 'CAP-006',
        input: { metric: 'fixed_visible_ipv4_address_count' },
        rationale: '读取冻结窗口内的固定前缀可见 IPv4 地址量时序。',
      }
    : {
        schema_version: 'domeye_agent_capability_proposal_v1',
        goal_id: goalId,
        goal_state_revision: revision,
        capability_id: 'CAP-016',
        input: {
          metric: 'fixed_visible_ipv4_address_count',
          source_artifact_id: sourceArtifactId!,
          tie_policy: 'first_observed_occurrence',
        },
        rationale: '仅基于已观察到的同身份时序计算首个并列极值。',
      }
}

function admitted(
  decision: ReturnType<DomeyeTrustKernel['admit']>,
): DomeyeAdmittedDecision {
  assert.equal(decision.status, 'admitted')
  if (decision.status !== 'admitted') throw new Error('expected admitted')
  return decision
}

function admissionRequest(input: {
  proposal: DomeyeCapabilityProposal
  state: DomeyeGoalState
  proposalSequence: number
  modelAttemptsUsed: number
  principalId: string
  authorizationScopes: readonly string[]
  actionHistory?: DomeyeAdmissionRequest['action_history']
  artifacts?: DomeyeAdmissionRequest['artifacts']
}): DomeyeAdmissionRequest {
  return {
    proposal: input.proposal,
    proposal_sequence: input.proposalSequence,
    goal_state: input.state,
    principal: {
      principal_id: input.principalId,
      authorization_scopes: input.authorizationScopes,
    },
    tenant_id: 'domeye',
    data_identity: IDENTITY,
    candidate_id: CANDIDATE_ID,
    policy: CANDIDATE.policy,
    registry: CANDIDATE.registry,
    revocation: {
      state: 'not_revoked',
      checked_at_utc: NOW,
      reason_code: null,
    },
    model_api_attempts_used: input.modelAttemptsUsed,
    action_history: input.actionHistory ?? [],
    artifacts: input.artifacts ?? [],
    admitted_at_utc: NOW,
  }
}

function goalState(input: {
  goalId: string
  revision: number
  status: DomeyeGoalState['status']
  completed?: DomeyeGoalState['completed_capability_ids']
  artifactIds?: readonly string[]
  findingIds?: readonly string[]
  lastObservationId?: string | null
}): DomeyeGoalState {
  return {
    schema_version: 'domeye_agent_goal_state_v1',
    goal_id: input.goalId,
    state_revision: input.revision,
    status: input.status,
    completed_capability_ids: [...(input.completed ?? [])],
    artifact_ids: [...(input.artifactIds ?? [])],
    finding_ids: [...(input.findingIds ?? [])],
    last_observation_id: input.lastObservationId ?? null,
    updated_at_utc: NOW,
  }
}

function seriesRead(
  values: readonly (number | null)[],
): CountryOutageMetricSeriesRead {
  assert.equal(values.length, 4)
  return {
    data_identity: IDENTITY,
    metric: 'fixed_visible_ipv4_address_count',
    unit: 'unique_ipv4_address',
    population_definition:
      'normalized_deduplicated_merged_fixed_prefix_ipv4_unique_address_union',
    timestamps_utc: [
      '2026-02-27T00:10:00Z',
      '2026-02-27T00:15:00Z',
      '2026-02-27T00:20:00Z',
      '2026-02-27T00:25:00Z',
    ],
    values,
    definition: '规范化、去重并合并重叠后的 IPv4 唯一地址并集。',
    source_response_sha256: SERIES_DIGEST,
    completeness: { state: 'complete', missing_slot_count: 0 },
    evidence_refs: [
      'domeye:/series#/timestamps',
      'domeye:/series#/tracks/fixed_visible_ipv4_address_count',
    ],
  }
}

function gateway(values: readonly (number | null)[]): DomeyeCapabilityGateway {
  const readModel: CountryOutageSeriesReadModel = {
    async readMetricSeries() { return seriesRead(values) },
  }
  return new DomeyeCapabilityGateway({
    series_read_model: readModel,
    expected_series_response_sha256: SERIES_DIGEST,
    now: () => new Date(NOW),
  })
}

type ProviderAttempt = DomeyeFirstSliceRunResult['usage']['attempts'][number]

function completedAttempt(
  attemptId: number,
  phase: ProviderAttempt['phase'],
): ProviderAttempt {
  return {
    attempt_id: attemptId,
    phase,
    ...MODEL_IDENTITY,
    response_model: MODEL_IDENTITY.expected_response_model,
    started_at_utc: NOW,
    ended_at_utc: NOW,
    latency_ms: 0,
    outcome: 'completed',
    failure_code: null,
  }
}

function usage(
  attempts: readonly ProviderAttempt[],
): DomeyeFirstSliceRunResult['usage'] {
  return {
    attempt_count: attempts.filter(
      (attempt) => attempt.outcome !== 'limit_rejected',
    ).length,
    maximum_attempt_count: 10,
    cost_policy: 'audit_only',
    tokens: {
      input: 10,
      output: 5,
      cache_read: 0,
      cache_write: 0,
      total: 15,
    },
    estimated_cost_usd: 0.01,
    attempts,
  }
}

async function buildCompletedResult(
  values: readonly (number | null)[],
  principalId = PRINCIPAL.userId,
  authorizationScopes: readonly string[] = ['country_outage:read'],
  admissionAttemptCounts: readonly [number, number] = [1, 2],
): Promise<Extract<DomeyeFirstSliceRunResult, { outcome: 'completed' }>> {
  const goalId = `goal-sha256:${canonicalJsonSha256({
    candidate_id: CANDIDATE_ID,
    question: DOMEYE_FIRST_SLICE_QUESTION,
    data_identity: IDENTITY,
  })}`
  const semanticGoal = {
    schema_version: 'domeye_agent_semantic_goal_v1' as const,
    goal_id: goalId,
    requested_text: DOMEYE_FIRST_SLICE_QUESTION,
    objective: 'find_fixed_visible_ipv4_series_extrema' as const,
    metric: 'fixed_visible_ipv4_address_count' as const,
    data_identity: IDENTITY,
    created_at_utc: NOW,
  }
  const kernel = new DomeyeTrustKernel()
  const initialState = goalState({
    goalId,
    revision: 1,
    status: 'active',
  })
  const firstDecision = admitted(kernel.admit(admissionRequest({
    proposal: proposal(goalId, 1, 'CAP-006'),
    state: initialState,
    proposalSequence: 1,
    modelAttemptsUsed: admissionAttemptCounts[0],
    principalId,
    authorizationScopes,
  })))
  const executor = gateway(values)
  const first = await executor.execute(firstDecision, [])
  assert.equal(first.status, 'succeeded')
  if (first.status !== 'succeeded') throw new Error('first action failed')
  const secondState = goalState({
    goalId,
    revision: 2,
    status: 'active',
    completed: ['CAP-006'],
    artifactIds: [first.artifact.artifact_id],
    lastObservationId: first.observation.observation_id,
  })
  const secondDecision = admitted(kernel.admit(admissionRequest({
    proposal: proposal(goalId, 2, 'CAP-016', first.artifact.artifact_id),
    state: secondState,
    proposalSequence: 2,
    modelAttemptsUsed: admissionAttemptCounts[1],
    principalId,
    authorizationScopes,
    actionHistory: [first.receipt],
    artifacts: [first.artifact],
  })))
  const second = await executor.execute(secondDecision, [first.artifact])
  assert.equal(second.status, 'succeeded')
  if (second.status !== 'succeeded') throw new Error('second action failed')
  const artifacts = [first.artifact, second.artifact] as const
  const receipts = [first.receipt, second.receipt] as const
  const loopState = goalState({
    goalId,
    revision: 3,
    status: 'satisfied',
    completed: ['CAP-006', 'CAP-016'],
    artifactIds: artifacts.map((artifact) => artifact.artifact_id),
    lastObservationId: second.observation.observation_id,
  })
  const resultFinding = buildCountryOutageSeriesExtremaFinding({
    series_artifact: first.artifact,
    series_receipt: first.receipt,
    extrema_artifact: second.artifact,
    extrema_receipt: second.receipt,
  })
  const answerContext = buildCountryOutageAnswerContext(
    resultFinding,
    CANDIDATE.contract_digest,
  )
  const answerText = renderCountryOutageDeterministicFallback(answerContext)
  const draft: DomeyeRendererDraft = {
    schema_version: 'domeye_agent_renderer_draft_v1',
    context_id: answerContext.context_id,
    finding_id: resultFinding.finding_id,
    candidate_id: CANDIDATE_ID,
    publication_id: IDENTITY.publication_id,
    revision: IDENTITY.revision,
    collector_id: 'rrc25',
    window_start_utc: IDENTITY.window_start_utc,
    window_end_utc: IDENTITY.window_end_utc,
    metric: resultFinding.metric,
    unit: resultFinding.unit,
    values: resultFinding.values,
    observer_scope_zh: answerContext.observer_scope_zh,
    limitations_zh: answerContext.mandatory_limitations_zh,
    evidence_refs: answerContext.evidence_refs,
    text: answerText,
  }
  const guardResult = guardCountryOutageResponse(answerContext, draft)
  const cognitionAttempts = [
    completedAttempt(1, 'cognition'),
    completedAttempt(2, 'cognition'),
    completedAttempt(3, 'cognition'),
  ]
  const allAttempts = [
    ...cognitionAttempts,
    completedAttempt(4, 'renderer'),
  ]
  return {
    schema_version: 'domeye_first_vertical_slice_run_v1',
    outcome: 'completed',
    candidate_id: CANDIDATE_ID,
    identity_receipt: identityReceipt(),
    semantic_goal: semanticGoal,
    goal_state: goalState({
      goalId,
      revision: 4,
      status: 'satisfied',
      completed: ['CAP-006', 'CAP-016'],
      artifactIds: artifacts.map((artifact) => artifact.artifact_id),
      findingIds: [resultFinding.finding_id],
      lastObservationId: second.observation.observation_id,
    }),
    loop: {
      goal_state: loopState,
      disposition: {
        schema_version: 'domeye_agent_goal_disposition_v1',
        goal_id: goalId,
        goal_state_revision: 3,
        disposition: 'goal_satisfied',
        reason_code: 'finding_input_ready',
      },
      artifacts,
      action_receipts: receipts,
      admission_receipts: [firstDecision.receipt, secondDecision.receipt],
      observations: [first.observation, second.observation],
      decision_protocol_rejections: [],
      usage: usage(cognitionAttempts),
    },
    finding: resultFinding,
    answer_context: answerContext,
    answer: resultFinding.value_state === 'known'
      ? {
          answer: answerText,
          source: 'renderer',
          guard_result: guardResult,
          render_attempt: {
            status: 'completed',
            draft,
            failure_code: null,
          },
        }
      : {
          answer: answerText,
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
        },
    usage: usage(allAttempts),
  }
}

const SUCCESSFUL_RESULT = await buildCompletedResult([10, null, 5, 10])
const EMPTY_RESULT = await buildCompletedResult([null, null, null, null])
const WRONG_PRINCIPAL_RESULT = await buildCompletedResult(
  [10, null, 5, 10],
  'different-user',
)
const EXTRA_SCOPE_RESULT = await buildCompletedResult(
  [10, null, 5, 10],
  PRINCIPAL.userId,
  ['country_outage:read', 'unexpected:scope'],
)
const WRONG_ADMISSION_USAGE_RESULT = await buildCompletedResult(
  [10, null, 5, 10],
  PRINCIPAL.userId,
  ['country_outage:read'],
  [2, 3],
)

function successfulResult(): DomeyeFirstSliceRunResult {
  return structuredClone(SUCCESSFUL_RESULT)
}

function clarificationResult(): DomeyeFirstSliceRunResult {
  const result = successfulResult()
  return {
    ...result,
    outcome: 'clarification_required',
    loop: {
      ...result.loop,
      disposition: { disposition: 'clarification_required' },
    },
    finding: null,
    answer_context: null,
    answer: null,
  } as unknown as DomeyeFirstSliceRunResult
}

function emptyResult(): DomeyeFirstSliceRunResult {
  const result = structuredClone(EMPTY_RESULT)
  assert.equal(result.outcome, 'completed')
  return {
    ...result,
    answer: {
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
    },
  } as unknown as DomeyeFirstSliceRunResult
}

function knownFallbackResult(): DomeyeFirstSliceRunResult {
  const result = successfulResult()
  assert.equal(result.outcome, 'completed')
  assert.equal(result.answer.render_attempt.status, 'completed')
  if (result.answer.render_attempt.status !== 'completed') {
    throw new Error('expected completed renderer draft')
  }
  const blockedDraft: DomeyeRendererDraft = {
    ...result.answer.render_attempt.draft,
    values: {
      ...result.answer.render_attempt.draft.values,
      minimum: (result.answer.render_attempt.draft.values.minimum ?? 0) + 1,
    },
  }
  const blockedGuard = guardCountryOutageResponse(
    result.answer_context,
    blockedDraft,
  )
  assert.equal(blockedGuard.decision, 'block')
  return {
    ...result,
    answer: {
      ...result.answer,
      source: 'deterministic_fallback',
      guard_result: blockedGuard,
      render_attempt: {
        status: 'completed',
        draft: blockedDraft,
        failure_code: null,
      },
    },
  }
}

function tenCognitionThenLocalLimitFallback(): DomeyeFirstSliceRunResult {
  const result = knownFallbackResult()
  assert.equal(result.outcome, 'completed')
  const cognitionAttempts = Array.from(
    { length: 10 },
    (_value, index) => completedAttempt(index + 1, 'cognition'),
  )
  const limitRejected: ProviderAttempt = {
    attempt_id: 11,
    phase: 'renderer',
    ...MODEL_IDENTITY,
    response_model: null,
    started_at_utc: NOW,
    ended_at_utc: NOW,
    latency_ms: 0,
    outcome: 'limit_rejected',
    failure_code: 'provider_request_limit_exceeded',
  }
  return {
    ...result,
    answer: {
      ...result.answer,
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
    },
    loop: {
      ...result.loop,
      decision_protocol_rejections: Array.from(
        { length: 7 },
        (_value, index) => ({
          sequence: index + 3,
          reason_code: 'decision_missing_or_invalid' as const,
          observed_proposal_count: 0,
          observed_disposition_count: 0,
        }),
      ),
      usage: usage(cognitionAttempts),
    },
    usage: usage([...cognitionAttempts, limitRejected]),
  }
}

function preActionProtocolRetryResult(): DomeyeFirstSliceRunResult {
  const result = structuredClone(WRONG_ADMISSION_USAGE_RESULT)
  const cognitionAttempts = Array.from(
    { length: 4 },
    (_value, index) => completedAttempt(index + 1, 'cognition'),
  )
  return {
    ...result,
    loop: {
      ...result.loop,
      decision_protocol_rejections: [{
        sequence: 1,
        reason_code: 'decision_missing_or_invalid',
        observed_proposal_count: 0,
        observed_disposition_count: 0,
      }],
      usage: usage(cognitionAttempts),
    },
    usage: usage([
      ...cognitionAttempts,
      completedAttempt(5, 'renderer'),
    ]),
  }
}

function completedRendererWithoutResponseIdentity(): DomeyeFirstSliceRunResult {
  const result = successfulResult()
  return {
    ...result,
    usage: {
      ...result.usage,
      attempts: result.usage.attempts.map((attempt) =>
        attempt.phase === 'renderer'
          ? { ...attempt, response_model: null }
          : attempt,
      ),
    },
  }
}

function invalidProviderAttemptTimestamp(): DomeyeFirstSliceRunResult {
  const result = successfulResult()
  return {
    ...result,
    usage: {
      ...result.usage,
      attempts: result.usage.attempts.map((attempt) =>
        attempt.phase === 'renderer'
          ? {
              ...attempt,
              started_at_utc: '2026-02-30T00:00:00Z',
              ended_at_utc: '2026-02-30T00:00:00Z',
            }
          : attempt,
      ),
    },
  }
}

function localRendererInvalidAfterCompletedProvider(): DomeyeFirstSliceRunResult {
  const result = successfulResult()
  assert.equal(result.outcome, 'completed')
  return {
    ...result,
    answer: {
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
    },
  }
}

function selfConsistentWrongFindingValue(): DomeyeFirstSliceRunResult {
  const result = successfulResult()
  assert.equal(result.outcome, 'completed')
  const {
    finding_id: _findingId,
    result_digest: _resultDigest,
    ...findingContent
  } = result.finding
  const wrongContent = {
    ...findingContent,
    values: {
      ...findingContent.values,
      minimum: 6,
      difference: 4,
    },
  }
  const wrongDigest = `sha256:${canonicalJsonSha256(wrongContent)}`
  const wrongFinding: DomeyeTypedFinding = {
    ...wrongContent,
    finding_id: `finding-${wrongDigest}`,
    result_digest: wrongDigest,
  }
  const wrongContext = buildCountryOutageAnswerContext(
    wrongFinding,
    CANDIDATE.contract_digest,
  )
  const wrongText = renderCountryOutageDeterministicFallback(wrongContext)
  const priorDraft = result.answer.render_attempt.status === 'completed'
    ? result.answer.render_attempt.draft
    : null
  assert.ok(priorDraft)
  const wrongDraft: DomeyeRendererDraft = {
    ...priorDraft,
    context_id: wrongContext.context_id,
    finding_id: wrongFinding.finding_id,
    values: wrongFinding.values,
    text: wrongText,
  }
  const wrongGuard = guardCountryOutageResponse(wrongContext, wrongDraft)
  assert.equal(wrongGuard.decision, 'pass')
  return {
    ...result,
    goal_state: {
      ...result.goal_state,
      finding_ids: [wrongFinding.finding_id],
    },
    finding: wrongFinding,
    answer_context: wrongContext,
    answer: {
      answer: wrongText,
      source: 'renderer',
      guard_result: wrongGuard,
      render_attempt: {
        status: 'completed',
        draft: wrongDraft,
        failure_code: null,
      },
    },
  }
}

function selfReportedPassWithWrongText(): DomeyeFirstSliceRunResult {
  const result = successfulResult()
  assert.equal(result.outcome, 'completed')
  return {
    ...result,
    answer: {
      ...result.answer,
      answer: '错误但自报 Guard pass 的答案。',
    },
  }
}

function modelIdentityDriftFallback(): DomeyeFirstSliceRunResult {
  const result = knownFallbackResult()
  return {
    ...result,
    usage: {
      ...result.usage,
      attempts: result.usage.attempts.map((attempt) =>
        attempt.phase === 'renderer'
          ? {
              ...attempt,
              response_model: 'drifted-model',
              outcome: 'failed' as const,
              failure_code: 'provider_response_identity_mismatch',
            }
          : attempt,
      ),
    },
  } as DomeyeFirstSliceRunResult
}

function incompleteExecutionChain(): DomeyeFirstSliceRunResult {
  const result = successfulResult()
  return {
    ...result,
    loop: {
      ...result.loop,
      action_receipts: result.loop.action_receipts.slice(0, 1),
    },
  } as DomeyeFirstSliceRunResult
}

function selfConsistentWrongFindingInput(): DomeyeFirstSliceRunResult {
  const result = successfulResult()
  const observation = result.loop.observations[1]
  const extremaArtifact = result.loop.artifacts[1]
  if (
    !observation
    || !extremaArtifact
    || observation.safe_summary.finding_input === null
  ) throw new Error('expected ready finding input')
  const mutableObservation = observation as {
    -readonly [Key in keyof DomeyeCapabilityObservation]:
      DomeyeCapabilityObservation[Key]
  }
  mutableObservation.safe_summary = {
    ...observation.safe_summary,
    finding_input: {
      ...observation.safe_summary.finding_input,
      source_artifact_ref: extremaArtifact.artifact_id,
    },
  }
  const {
    observation_id: _oldObservationId,
    ...observationBody
  } = mutableObservation
  mutableObservation.observation_id =
    `observation-sha256:${canonicalJsonSha256(observationBody)}`
  ;(result.loop.goal_state as { last_observation_id: string })
    .last_observation_id = mutableObservation.observation_id
  ;(result.goal_state as { last_observation_id: string })
    .last_observation_id = mutableObservation.observation_id
  return result
}

class RuntimeDouble {
  readonly requests: DomeyeFirstSliceRunRequest[] = []

  constructor(
    readonly execute: (
      request: DomeyeFirstSliceRunRequest,
    ) => Promise<DomeyeFirstSliceRunResult>,
  ) {}

  async run(
    request: DomeyeFirstSliceRunRequest,
  ): Promise<DomeyeFirstSliceRunResult> {
    this.requests.push(request)
    return await this.execute(request)
  }
}

function service(
  receipt: DomeyeVerifiedIdentityReceipt,
  runtime: RuntimeDouble,
): DomeyeInteractiveConversationService {
  return new DomeyeInteractiveConversationService({
    candidate: candidate(),
    identity_verifier: { async verify() { return receipt } },
    runtime: runtime as unknown as DomeyeFirstSliceRuntime,
    now: () => new Date(NOW),
  })
}

async function createConversation(
  conversationService: DomeyeInteractiveConversationService,
) {
  return await conversationService.createConversation(PRINCIPAL, {
    event_reference: EVENT_REFERENCE,
    publication_id: IDENTITY.publication_id,
    revision: IDENTITY.revision,
    idempotency_key: 'create-1',
  })
}

test('会话冻结验证回执并以中性事件引用绑定同一 Candidate', async () => {
  const sourceReceipt = identityReceipt()
  const runtime = new RuntimeDouble(async () => successfulResult())
  const conversationService = service(sourceReceipt, runtime)
  const created = await createConversation(conversationService)

  assert.equal(created.conversation.binding.event_reference, EVENT_REFERENCE)
  ;(sourceReceipt.data_identity as { publication_id: string }).publication_id =
    'mutated-publication'
  ;(created.conversation.binding as { publication_id: string }).publication_id =
    'mutated-return-value'

  const reread = await conversationService.getConversation(
    PRINCIPAL,
    created.conversation.conversation_id,
  )
  assert.equal(reread.binding.publication_id, IDENTITY.publication_id)

  const started = await conversationService.createTurn(
    PRINCIPAL,
    reread.conversation_id,
    { question: DOMEYE_FIRST_SLICE_QUESTION, idempotency_key: 'turn-1' },
  )
  await conversationService.waitForTurn(reread.conversation_id, started.turn.turn_id)
  const suppliedReceipt = runtime.requests[0]?.identity_receipt
  assert.ok(suppliedReceipt)
  assert.equal(suppliedReceipt.data_identity.publication_id, IDENTITY.publication_id)
  assert.equal(Object.isFrozen(suppliedReceipt), true)
  assert.equal(Object.isFrozen(suppliedReceipt.data_identity), true)
  assert.deepEqual(runtime.requests[0]?.principal, {
    principal_id: PRINCIPAL.userId,
    authorization_scopes: ['country_outage:read'],
  })
})

test('会话创建拒绝非冻结身份回执', async () => {
  const receipt = {
    ...identityReceipt(),
    immutable: false,
  } as unknown as DomeyeVerifiedIdentityReceipt
  const conversationService = service(
    receipt,
    new RuntimeDouble(async () => successfulResult()),
  )

  await assert.rejects(
    () => createConversation(conversationService),
    (error: unknown) => error instanceof DomeyeConversationError
      && error.code === 'verified_identity_outside_candidate',
  )
})

test('turn 异步启动并在完成后发布结构化答案', async () => {
  let resolveRun: ((result: DomeyeFirstSliceRunResult) => void) | undefined
  const runtime = new RuntimeDouble(async () => await new Promise((resolve) => {
    resolveRun = resolve
  }))
  const conversationService = service(identityReceipt(), runtime)
  const { conversation } = await createConversation(conversationService)

  const started = await conversationService.createTurn(
    PRINCIPAL,
    conversation.conversation_id,
    { question: DOMEYE_FIRST_SLICE_QUESTION, idempotency_key: 'turn-async' },
  )
  assert.equal(started.turn.state, 'executing')
  assert.equal(started.turn.answer_success, false)
  assert.equal(started.turn.workflow_completed, false)
  assert.equal(runtime.requests.length, 1)

  assert.ok(resolveRun)
  resolveRun(successfulResult())
  await conversationService.waitForTurn(
    conversation.conversation_id,
    started.turn.turn_id,
  )
  const completed = await conversationService.getConversation(
    PRINCIPAL,
    conversation.conversation_id,
  )
  assert.equal(completed.turns[0]?.state, 'completed')
  assert.equal(completed.turns[0]?.answer_success, true)
  assert.equal(completed.turns[0]?.workflow_completed, true)
  assert.equal(completed.turns[0]?.answer?.answerability, 'supported')
  assert.equal(completed.turns[0]?.answer?.trace.response_guard?.decision, 'pass')
  assert.deepEqual(
    completed.turns[0]?.answer?.trace.authorization_derivation,
    {
      schema_version: 'domeye_authorization_derivation_v1',
      rule_id: 'country_outage_event_read_to_country_outage_read_v1',
      source_scope: 'country_outage_event_read:IR',
      source_scope_kind: 'country_event_read',
      source_country_code: 'IR',
      derived_scope: 'country_outage:read',
    },
  )
  const publishedFindingId = completed.turns[0]?.answer?.finding?.finding_id
  assert.ok(publishedFindingId)
  assert.equal(completed.turns[0]?.answer?.finding?.observed_point_count, 3)
  assert.equal(completed.turns[0]?.answer?.finding?.null_point_count, 1)
  assert.deepEqual(
    completed.turns[0]?.answer?.evidence.map((item) => item.evidence_ref),
    ['first', 'last', 'minimum', 'maximum', 'difference', 'net_change']
      .map((field) => `${publishedFindingId}#/values/${field}`),
  )
})

test('只有匹配事件的认证读取 scope 才能派生运行时读取 scope', async () => {
  const runtime = new RuntimeDouble(async () => successfulResult())
  const conversationService = service(identityReceipt(), runtime)

  for (const authorizationScope of [
    'country_outage:read',
    'country_outage_event_read:US',
    'report_read',
  ]) {
    await assert.rejects(
      () => conversationService.createConversation(
        { userId: 'user-denied', authorizationScope },
        {
          event_reference: EVENT_REFERENCE,
          publication_id: IDENTITY.publication_id,
          revision: IDENTITY.revision,
          idempotency_key: `denied-${authorizationScope.length}`,
        },
      ),
      (error: unknown) => error instanceof DomeyeConversationError
        && error.code === 'permission_denied',
    )
  }
  assert.equal(runtime.requests.length, 0)
})

test('澄清与空观测都不伪装成完成或成功回答', async () => {
  for (const [result, expectedState] of [
    [clarificationResult(), 'clarification_required'],
    [emptyResult(), 'stopped'],
  ] as const) {
    const conversationService = service(
      identityReceipt(),
      new RuntimeDouble(async () => result),
    )
    const { conversation } = await createConversation(conversationService)
    const started = await conversationService.createTurn(
      PRINCIPAL,
      conversation.conversation_id,
      {
        question: DOMEYE_FIRST_SLICE_QUESTION,
        idempotency_key: `turn-${expectedState}`,
      },
    )
    await conversationService.waitForTurn(
      conversation.conversation_id,
      started.turn.turn_id,
    )
    const final = (await conversationService.getConversation(
      PRINCIPAL,
      conversation.conversation_id,
    )).turns[0]
    assert.equal(final?.state, expectedState)
    assert.equal(final?.answer_success, false)
    assert.equal(final?.workflow_completed, false)
    assert.notEqual(final?.state, 'completed')
    if (final?.state === 'clarification_required' || final?.state === 'stopped') {
      assert.notEqual(final.answer.answerability, 'supported')
      assert.equal(final.answer.answer_source, 'none')
    }
  }
})

test('已知完整 Finding 的确定性 fallback 在 Guard block 后仍是正确完成', async () => {
  const conversationService = service(
    identityReceipt(),
    new RuntimeDouble(async () => knownFallbackResult()),
  )
  const { conversation } = await createConversation(conversationService)
  const started = await conversationService.createTurn(
    PRINCIPAL,
    conversation.conversation_id,
    { question: DOMEYE_FIRST_SLICE_QUESTION, idempotency_key: 'turn-fallback' },
  )
  await conversationService.waitForTurn(
    conversation.conversation_id,
    started.turn.turn_id,
  )
  const final = (await conversationService.getConversation(
    PRINCIPAL,
    conversation.conversation_id,
  )).turns[0]
  assert.equal(final?.state, 'completed')
  assert.equal(final?.answer_success, true)
  assert.equal(final?.workflow_completed, true)
  if (final?.state === 'completed') {
    assert.equal(final.answer.answer_source, 'deterministic_fallback')
    assert.equal(final.answer.trace.response_guard?.decision, 'block')
  }
})

test('Renderer 调用成功但本地解析失败时，精确 fallback 仍是正确完成', async () => {
  const conversationService = service(
    identityReceipt(),
    new RuntimeDouble(async () => localRendererInvalidAfterCompletedProvider()),
  )
  const { conversation } = await createConversation(conversationService)
  const started = await conversationService.createTurn(
    PRINCIPAL,
    conversation.conversation_id,
    {
      question: DOMEYE_FIRST_SLICE_QUESTION,
      idempotency_key: 'turn-local-renderer-invalid',
    },
  )
  await conversationService.waitForTurn(
    conversation.conversation_id,
    started.turn.turn_id,
  )
  const final = (await conversationService.getConversation(
    PRINCIPAL,
    conversation.conversation_id,
  )).turns[0]
  assert.equal(final?.state, 'completed')
  assert.equal(final?.answer_success, true)
  assert.equal(final?.workflow_completed, true)
  if (final?.state === 'completed') {
    assert.equal(final.answer.answer_source, 'deterministic_fallback')
    assert.equal(final.answer.trace.response_guard?.decision, 'block')
    assert.equal(final.answer.usage.attempts.at(-1)?.outcome, 'completed')
  }
})

test('前置协议拒绝后同一完整链仍按实际 cognition cycle 完成', async () => {
  const conversationService = service(
    identityReceipt(),
    new RuntimeDouble(async () => preActionProtocolRetryResult()),
  )
  const { conversation } = await createConversation(conversationService)
  const started = await conversationService.createTurn(
    PRINCIPAL,
    conversation.conversation_id,
    { question: DOMEYE_FIRST_SLICE_QUESTION, idempotency_key: 'turn-retry' },
  )
  await conversationService.waitForTurn(
    conversation.conversation_id,
    started.turn.turn_id,
  )
  const final = (await conversationService.getConversation(
    PRINCIPAL,
    conversation.conversation_id,
  )).turns[0]
  assert.equal(final?.state, 'completed')
  assert.equal(final?.answer_success, true)
  assert.equal(final?.answer?.usage.attempt_count, 5)
})

test('第 10 次 cognition 后 Renderer 本地限流可用同 Context fallback 完成', async () => {
  const conversationService = service(
    identityReceipt(),
    new RuntimeDouble(async () => tenCognitionThenLocalLimitFallback()),
  )
  const { conversation } = await createConversation(conversationService)
  const started = await conversationService.createTurn(
    PRINCIPAL,
    conversation.conversation_id,
    { question: DOMEYE_FIRST_SLICE_QUESTION, idempotency_key: 'turn-limit' },
  )
  await conversationService.waitForTurn(
    conversation.conversation_id,
    started.turn.turn_id,
  )
  const final = (await conversationService.getConversation(
    PRINCIPAL,
    conversation.conversation_id,
  )).turns[0]
  assert.equal(final?.state, 'completed')
  assert.equal(final?.answer_success, true)
  if (final?.state === 'completed') {
    assert.equal(final.answer.answer_source, 'deterministic_fallback')
    assert.equal(final.answer.usage.attempt_count, 10)
    assert.equal(final.answer.usage.attempts.length, 11)
    assert.equal(final.answer.usage.attempts[10]?.outcome, 'limit_rejected')
  }
})

test('自报 pass、模型身份漂移或伪造执行链都不能发布事实或标记完成', async () => {
  for (const [label, result] of [
    ['wrong-text', selfReportedPassWithWrongText()],
    ['model-drift', modelIdentityDriftFallback()],
    ['broken-chain', incompleteExecutionChain()],
    ['wrong-finding-input', selfConsistentWrongFindingInput()],
    ['legacy-self-report', legacySelfReportedResult()],
    ['wrong-principal', structuredClone(WRONG_PRINCIPAL_RESULT)],
    ['extra-scope', structuredClone(EXTRA_SCOPE_RESULT)],
    ['wrong-admission-usage', structuredClone(WRONG_ADMISSION_USAGE_RESULT)],
    ['missing-response-model', completedRendererWithoutResponseIdentity()],
    ['invalid-attempt-time', invalidProviderAttemptTimestamp()],
    ['wrong-finding-value', selfConsistentWrongFindingValue()],
  ] as const) {
    const conversationService = service(
      identityReceipt(),
      new RuntimeDouble(async () => result),
    )
    const { conversation } = await createConversation(conversationService)
    const started = await conversationService.createTurn(
      PRINCIPAL,
      conversation.conversation_id,
      {
        question: DOMEYE_FIRST_SLICE_QUESTION,
        idempotency_key: `turn-invalid-${label}`,
      },
    )
    await conversationService.waitForTurn(
      conversation.conversation_id,
      started.turn.turn_id,
    )
    const final = (await conversationService.getConversation(
      PRINCIPAL,
      conversation.conversation_id,
    )).turns[0]
    assert.equal(final?.state, 'stopped')
    assert.equal(final?.answer_success, false)
    assert.equal(final?.workflow_completed, false)
    if (final?.state === 'stopped') {
      assert.equal(final.answer.answer_source, 'none')
      assert.equal(final.answer.finding, null)
      assert.deepEqual(final.answer.evidence, [])
      assert.deepEqual(final.answer.limitations, [])
      assert.equal(
        final.answer.answer_text,
        '未形成满足公开合同的正确完整答案。',
      )
    }
  }
})

test('取消执行中的 turn 后不发布答案', async () => {
  const runtime = new RuntimeDouble(async (request) => await new Promise(
    (_resolve, reject) => {
      request.signal?.addEventListener(
        'abort',
        () => reject(request.signal?.reason ?? new Error('cancelled')),
        { once: true },
      )
    },
  ))
  const conversationService = service(identityReceipt(), runtime)
  const { conversation } = await createConversation(conversationService)
  const started = await conversationService.createTurn(
    PRINCIPAL,
    conversation.conversation_id,
    { question: DOMEYE_FIRST_SLICE_QUESTION, idempotency_key: 'turn-cancel' },
  )

  assert.deepEqual(
    await conversationService.cancelTurn(
      PRINCIPAL,
      conversation.conversation_id,
      started.turn.turn_id,
    ),
    { turn_id: started.turn.turn_id, state: 'cancel_requested' },
  )
  await conversationService.waitForTurn(
    conversation.conversation_id,
    started.turn.turn_id,
  )
  const cancelled = await conversationService.getConversation(
    PRINCIPAL,
    conversation.conversation_id,
  )
  assert.equal(cancelled.turns[0]?.state, 'cancelled')
  assert.equal(cancelled.turns[0]?.answer_success, false)
  assert.equal(cancelled.turns[0]?.workflow_completed, false)
  assert.ok(cancelled.turns[0])
  assert.equal('answer' in cancelled.turns[0], false)
  assert.equal(
    'error' in cancelled.turns[0]
      ? cancelled.turns[0].error.code
      : null,
    'cancelled',
  )
})

test('runtime 忽略 abort 后迟到成功也不能覆盖 cancelled 终态', async () => {
  let resolveRun: ((result: DomeyeFirstSliceRunResult) => void) | undefined
  const runtime = new RuntimeDouble(async () => await new Promise((resolve) => {
    resolveRun = resolve
  }))
  const conversationService = service(identityReceipt(), runtime)
  const { conversation } = await createConversation(conversationService)
  const started = await conversationService.createTurn(
    PRINCIPAL,
    conversation.conversation_id,
    { question: DOMEYE_FIRST_SLICE_QUESTION, idempotency_key: 'turn-late' },
  )

  assert.deepEqual(
    await conversationService.cancelTurn(
      PRINCIPAL,
      conversation.conversation_id,
      started.turn.turn_id,
    ),
    { turn_id: started.turn.turn_id, state: 'cancel_requested' },
  )
  const immediatelyCancelled = (await conversationService.getConversation(
    PRINCIPAL,
    conversation.conversation_id,
  )).turns[0]
  assert.equal(immediatelyCancelled?.state, 'cancelled')
  assert.equal(immediatelyCancelled?.answer_success, false)
  assert.equal(immediatelyCancelled?.workflow_completed, false)

  assert.ok(resolveRun)
  resolveRun(successfulResult())
  await conversationService.waitForTurn(
    conversation.conversation_id,
    started.turn.turn_id,
  )
  const final = (await conversationService.getConversation(
    PRINCIPAL,
    conversation.conversation_id,
  )).turns[0]
  assert.equal(final?.state, 'cancelled')
  assert.equal(final?.answer_success, false)
  assert.equal(final?.workflow_completed, false)
  assert.ok(final)
  assert.equal('answer' in final, false)
  assert.equal('error' in final ? final.error.code : null, 'cancelled')
})

test('首片唯一问题之外的 turn 在运行前被拒绝', async () => {
  const runtime = new RuntimeDouble(async () => successfulResult())
  const conversationService = service(identityReceipt(), runtime)
  const { conversation } = await createConversation(conversationService)

  await assert.rejects(
    () => conversationService.createTurn(
      PRINCIPAL,
      conversation.conversation_id,
      { question: '请判断真实用户影响和中断原因', idempotency_key: 'turn-wrong' },
    ),
    (error: unknown) => error instanceof DomeyeConversationError
      && error.code === 'goal_outside_first_slice_contract',
  )
  assert.equal(runtime.requests.length, 0)
})
