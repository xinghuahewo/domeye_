import assert from 'node:assert/strict'
import test from 'node:test'
import { Check } from 'typebox/value'

import {
  DomeyeActionReceiptSchema,
  DomeyeArtifactEnvelopeSchema,
  DomeyeCapabilityObservationSchema,
  DomeyeInteractiveActionSchema,
  type DomeyeArtifactEnvelope,
  type DomeyeCapabilityProposal,
  type DomeyeDataIdentity,
  type DomeyeExecutionBinding,
  type DomeyeGoalState,
} from '../src/agent/contracts.js'
import {
  calculateFirstObservedSeriesExtrema,
  DomeyeCapabilityExecutionError,
  DomeyeCapabilityGateway,
  type CountryOutageMetricSeriesRead,
  type CountryOutageSeriesReadModel,
} from '../src/agent/capability-execution.js'
import {
  DomeyeTrustKernel,
  type DomeyeAdmissionRequest,
  type DomeyeAdmittedDecision,
} from '../src/agent/trust-kernel.js'

const NOW = '2026-08-19T06:00:00.000Z'
const CANDIDATE = `git:xinghuahewo/domeye_@${'a'.repeat(40)}`
const POPULATION =
  'normalized_deduplicated_merged_fixed_prefix_ipv4_unique_address_union' as const

function sha(character: string): `sha256:${string}` {
  return `sha256:${character.repeat(64)}`
}

const IDENTITY: DomeyeDataIdentity = {
  event_type: 'country_outage',
  incident_id: 'incident_go_v1_a1de26f854831330c616a72af21597eb',
  publication_id: 'country_outage_publication_v1_989f698fb6f6c32579eebe7bb2bc833f',
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

function goalState(
  revision: number,
  artifacts: readonly string[] = [],
  completed: DomeyeGoalState['completed_capability_ids'] = [],
  lastObservationId: string | null = null,
): DomeyeGoalState {
  return {
    schema_version: 'domeye_agent_goal_state_v1',
    goal_id: 'goal-fixed-ipv4-extrema',
    state_revision: revision,
    status: 'active',
    completed_capability_ids: [...completed],
    artifact_ids: [...artifacts],
    finding_ids: [],
    last_observation_id: lastObservationId,
    updated_at_utc: NOW,
  }
}

function cap006(revision = 1): DomeyeCapabilityProposal {
  return {
    schema_version: 'domeye_agent_capability_proposal_v1',
    goal_id: 'goal-fixed-ipv4-extrema',
    goal_state_revision: revision,
    capability_id: 'CAP-006',
    input: { metric: 'fixed_visible_ipv4_address_count' },
    rationale: '先读取冻结窗口内的真实指标时序。',
  }
}

function cap016(sourceArtifactId: string, revision = 2): DomeyeCapabilityProposal {
  return {
    schema_version: 'domeye_agent_capability_proposal_v1',
    goal_id: 'goal-fixed-ipv4-extrema',
    goal_state_revision: revision,
    capability_id: 'CAP-016',
    input: {
      metric: 'fixed_visible_ipv4_address_count',
      source_artifact_id: sourceArtifactId,
      tie_policy: 'first_observed_occurrence',
    },
    rationale: '基于已观察到的同身份时序计算首个并列极值。',
  }
}

function request(
  proposal: DomeyeCapabilityProposal,
  state: DomeyeGoalState,
  overrides: Partial<DomeyeAdmissionRequest> = {},
): DomeyeAdmissionRequest {
  return {
    proposal,
    proposal_sequence: proposal.capability_id === 'CAP-006' ? 1 : 2,
    goal_state: state,
    principal: {
      principal_id: 'user-1',
      authorization_scopes: ['country_outage:read'],
    },
    tenant_id: 'domeye',
    data_identity: IDENTITY,
    candidate_id: CANDIDATE,
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
    revocation: {
      state: 'not_revoked',
      checked_at_utc: NOW,
      reason_code: null,
    },
    model_api_attempts_used: proposal.capability_id === 'CAP-006' ? 1 : 2,
    action_history: [],
    artifacts: [],
    admitted_at_utc: NOW,
    ...overrides,
  }
}

function seriesRead(
  overrides: Partial<CountryOutageMetricSeriesRead> = {},
): CountryOutageMetricSeriesRead {
  return {
    data_identity: IDENTITY,
    metric: 'fixed_visible_ipv4_address_count',
    unit: 'unique_ipv4_address',
    population_definition: POPULATION,
    timestamps_utc: [
      '2026-02-27T00:10:00Z',
      '2026-02-27T00:15:00Z',
      '2026-02-27T00:20:00Z',
      '2026-02-27T00:25:00Z',
    ],
    values: [10, null, 5, 10],
    definition: '规范化、去重并合并重叠后的 IPv4 唯一地址并集。',
    source_response_sha256: sha('9'),
    completeness: { state: 'complete', missing_slot_count: 0 },
    evidence_refs: [
      'domeye:/series#/timestamps',
      'domeye:/series#/tracks/fixed_visible_ipv4_address_count',
    ],
    ...overrides,
  }
}

function readModel(
  read: () => Promise<CountryOutageMetricSeriesRead>,
  calls: { value: number },
): CountryOutageSeriesReadModel {
  return {
    async readMetricSeries() {
      calls.value += 1
      return read()
    },
  }
}

function gateway(
  model: CountryOutageSeriesReadModel,
): DomeyeCapabilityGateway {
  return new DomeyeCapabilityGateway({
    series_read_model: model,
    expected_series_response_sha256: sha('9'),
    now: () => new Date(NOW),
  })
}

function admitted(
  decision: ReturnType<DomeyeTrustKernel['admit']>,
): DomeyeAdmittedDecision {
  assert.equal(decision.status, 'admitted')
  if (decision.status !== 'admitted') throw new Error('expected admitted')
  assert.equal(Check(DomeyeInteractiveActionSchema, decision.action), true)
  return decision
}

async function successfulSeriesStep() {
  const calls = { value: 0 }
  const kernel = new DomeyeTrustKernel()
  const decision = admitted(kernel.admit(request(cap006(), goalState(1))))
  const result = await gateway(readModel(
    async () => seriesRead(),
    calls,
  )).execute(decision, [])
  assert.equal(result.status, 'succeeded')
  if (result.status !== 'succeeded') throw new Error('expected success')
  return { calls, kernel, decision, result }
}

test('逐 Action 准入和 Gateway 形成 CAP-006→CAP-016 的同身份确定性链', async () => {
  const first = await successfulSeriesStep()
  assert.equal(first.calls.value, 1)
  assert.equal(Check(DomeyeArtifactEnvelopeSchema, first.result.artifact), true)
  assert.equal(Check(DomeyeActionReceiptSchema, first.result.receipt), true)
  assert.equal(Check(
    DomeyeCapabilityObservationSchema,
    first.result.observation,
  ), true)
  assert.equal(first.result.artifact.artifact_kind, 'metric_series')
  assert.equal(first.result.artifact.candidate_id, CANDIDATE)
  assert.equal(first.result.artifact.tenant_id, 'domeye')
  assert.equal(first.result.artifact.data_identity, IDENTITY)
  assert.equal(Object.isFrozen(first.result.artifact), true)
  assert.equal(Object.isFrozen(first.result.artifact.payload), true)
  assert.equal(Object.isFrozen(first.result.artifact.payload.values), true)
  assert.equal(first.result.observation.safe_summary.finding_input, null)

  const nextState = goalState(
    2,
    [first.result.artifact.artifact_id],
    ['CAP-006'],
    first.result.observation.observation_id,
  )
  const secondDecision = admitted(first.kernel.admit(request(
    cap016(first.result.artifact.artifact_id),
    nextState,
    {
      action_history: [first.result.receipt],
      artifacts: [first.result.artifact],
    },
  )))
  assert.deepEqual(
    secondDecision.action.trust_binding.occurred_action_ids,
    [first.decision.action.action_id],
  )
  assert.equal(secondDecision.action.trust_binding.goal_state.state_revision, 2)
  assert.equal(secondDecision.action.trust_binding.budget.cost_policy, 'audit_only')
  assert.equal(secondDecision.action.trust_binding.budget.monetary_limit_usd, null)
  assert.equal(secondDecision.action.execution_binding.execution_unit_id, 'OP-01')

  const second = await gateway(readModel(
    async () => { throw new Error('OP-01 不应读取 API') },
    first.calls,
  )).execute(secondDecision, [first.result.artifact])
  assert.equal(second.status, 'succeeded')
  if (second.status !== 'succeeded') throw new Error('expected success')
  assert.equal(first.calls.value, 1)
  assert.equal(second.artifact.artifact_kind, 'series_extrema')
  if (second.artifact.artifact_kind !== 'series_extrema') return
  assert.deepEqual(second.artifact.payload, {
    schema_version: 'domeye_series_extrema_artifact_v1',
    metric: 'fixed_visible_ipv4_address_count',
    unit: 'unique_ipv4_address',
    tie_policy: 'first_observed_occurrence',
    source_artifact_id: first.result.artifact.artifact_id,
    evidence_refs: [
      'domeye:/series#/timestamps',
      'domeye:/series#/tracks/fixed_visible_ipv4_address_count',
      'derived:/operators/series_extrema/fixed_visible_ipv4_address_count',
    ],
    result_state: 'known',
    time_slot_count: 4,
    observed_point_count: 3,
    null_point_count: 1,
    first: 10,
    first_at_utc: '2026-02-27T00:10:00Z',
    last: 10,
    last_at_utc: '2026-02-27T00:25:00Z',
    minimum: 5,
    minimum_at_utc: '2026-02-27T00:20:00Z',
    maximum: 10,
    maximum_at_utc: '2026-02-27T00:10:00Z',
    difference: 5,
    net_change: 0,
  })
  assert.equal(Check(DomeyeArtifactEnvelopeSchema, second.artifact), true)
  assert.equal(Check(DomeyeActionReceiptSchema, second.receipt), true)
  assert.equal(Check(DomeyeCapabilityObservationSchema, second.observation), true)
  assert.deepEqual(second.observation.safe_summary.finding_input, {
    state: 'ready',
    source_artifact_ref: first.result.artifact.artifact_id,
    extrema_artifact_ref: second.artifact.artifact_id,
    extrema_result_state: 'known',
    next_owner: 'domeye_typed_finding_builder',
  })
  assert.equal(Check(DomeyeCapabilityObservationSchema, {
    ...second.observation,
    capability_id: 'CAP-006',
  }), false)
  assert.equal(Check(DomeyeCapabilityObservationSchema, {
    ...second.observation,
    status: 'failed',
    reason_code: 'forced_failure',
    artifact_ref: null,
  }), false)
})

test('已完成两个合法 Action 后第三个 Proposal 被上限拒绝且不执行', async () => {
  const first = await successfulSeriesStep()
  const secondState = goalState(
    2,
    [first.result.artifact.artifact_id],
    ['CAP-006'],
    first.result.observation.observation_id,
  )
  const secondDecision = admitted(first.kernel.admit(request(
    cap016(first.result.artifact.artifact_id),
    secondState,
    {
      action_history: [first.result.receipt],
      artifacts: [first.result.artifact],
    },
  )))
  const second = await gateway(readModel(
    async () => { throw new Error('OP-01 不应读取 API') },
    first.calls,
  )).execute(secondDecision, [first.result.artifact])
  assert.equal(second.status, 'succeeded')
  if (second.status !== 'succeeded') throw new Error('expected success')

  const thirdState = goalState(
    3,
    [first.result.artifact.artifact_id, second.artifact.artifact_id],
    ['CAP-006', 'CAP-016'],
    second.observation.observation_id,
  )
  const thirdDecision = first.kernel.admit(request(
    cap006(3),
    thirdState,
    {
      proposal_sequence: 3,
      action_history: [first.result.receipt, second.receipt],
      artifacts: [first.result.artifact, second.artifact],
    },
  ))
  assert.equal(thirdDecision.status, 'rejected')
  assert.equal(
    thirdDecision.receipt.reason_code,
    'approved_action_limit_exceeded',
  )
  assert.equal(thirdDecision.action, null)
  assert.equal(thirdDecision.receipt.budget.approved_actions_used, 2)

  const thirdExecutionCalls = { value: 0 }
  await assert.rejects(
    gateway(readModel(async () => seriesRead(), thirdExecutionCalls)).execute(
      thirdDecision as unknown as DomeyeAdmittedDecision,
      [first.result.artifact, second.artifact],
    ),
    (error: unknown) => error instanceof DomeyeCapabilityExecutionError
      && error.code === 'admission_required',
  )
  assert.equal(thirdExecutionCalls.value, 0)
})

test('J2：拒绝、撤销、预算与 Goal State 冲突均不产生 Action 或执行', async () => {
  const kernel = new DomeyeTrustKernel()
  const calls = { value: 0 }
  const blockedPolicy = kernel.admit(request(
    cap016('artifact-not-authorized'),
    goalState(2, ['artifact-not-authorized'], ['CAP-006']),
    {
      policy: {
        policy_id: 'read-only-first-step',
        policy_digest: sha('a'),
        state: 'active',
        allowed_capability_ids: ['CAP-006'],
      },
    },
  ))
  assert.equal(blockedPolicy.status, 'rejected')
  assert.equal(blockedPolicy.receipt.reason_code, 'capability_not_allowed')
  assert.equal(blockedPolicy.action, null)
  await assert.rejects(
    gateway(readModel(async () => seriesRead(), calls)).execute(
      blockedPolicy as unknown as DomeyeAdmittedDecision,
      [],
    ),
    (error: unknown) => error instanceof DomeyeCapabilityExecutionError
      && error.code === 'admission_required',
  )
  assert.equal(calls.value, 0)

  const revoked = kernel.admit(request(cap006(), goalState(1), {
    revocation: {
      state: 'revoked',
      checked_at_utc: NOW,
      reason_code: 'principal_revoked',
    },
  }))
  assert.equal(revoked.status, 'rejected')
  assert.equal(revoked.receipt.reason_code, 'revoked')

  const attemptLimit = kernel.admit(request(cap006(), goalState(1), {
    model_api_attempts_used: 10,
  }))
  assert.equal(attemptLimit.status, 'rejected')
  assert.equal(
    attemptLimit.receipt.reason_code,
    'model_api_attempt_limit_exceeded',
  )

  const mismatchedState = kernel.admit(request(cap006(1), goalState(2)))
  assert.equal(mismatchedState.status, 'rejected')
  assert.equal(mismatchedState.receipt.reason_code, 'goal_state_conflict')
})

test('J3：TOOL-03 失败、不完整、错身份或错单位均失败关闭', async () => {
  const decision = admitted(
    new DomeyeTrustKernel().admit(request(cap006(), goalState(1))),
  )
  const cases: Array<{
    expected: string
    read: () => Promise<CountryOutageMetricSeriesRead>
  }> = [
    {
      expected: 'identity_conflict',
      read: async () => seriesRead({
        data_identity: { ...IDENTITY, publication_id: 'wrong-publication' },
      }),
    },
    {
      expected: 'unit_mismatch',
      read: async () => seriesRead({ unit: 'prefix' }),
    },
    {
      expected: 'incomplete_series',
      read: async () => seriesRead({
        completeness: { state: 'incomplete', missing_slot_count: 1 },
      }),
    },
    {
      expected: 'incomplete_series',
      read: async () => seriesRead({
        timestamps_utc: [
          '2026-02-27T00:10:00Z',
          '2026-02-27T00:20:00Z',
          '2026-02-27T00:25:00Z',
        ],
        values: [10, 5, 10],
        completeness: { state: 'complete', missing_slot_count: 0 },
      }),
    },
    {
      expected: 'source_response_digest_mismatch',
      read: async () => seriesRead({
        source_response_sha256: sha('a'),
      }),
    },
    {
      expected: 'invalid_series_shape',
      read: async () => seriesRead({
        evidence_refs: ['domeye:/api/v2/private-series'],
      }),
    },
    {
      expected: 'read_model_failure',
      read: async () => { throw new Error('timeout') },
    },
  ]
  for (const item of cases) {
    const calls = { value: 0 }
    const result = await gateway(readModel(item.read, calls))
      .execute(decision, [])
    assert.equal(result.status, 'failed')
    assert.equal(result.artifact, null)
    assert.equal(result.receipt.status, 'failed')
    assert.equal(result.receipt.failure_code, item.expected)
    assert.equal(result.observation.safe_summary.result_state, 'unavailable')
    assert.equal(result.observation.safe_summary.finding_input, null)
    assert.equal(result.observation.artifact_ref, null)
    assert.equal(calls.value, 1)
    assert.equal(Check(DomeyeActionReceiptSchema, result.receipt), true)
    assert.equal(Check(DomeyeCapabilityObservationSchema, result.observation), true)
  }
})

test('J5：并列取首次、null 不补 0，全空进入 empty_observed_set，篡改源制品失败关闭', async () => {
  const known = calculateFirstObservedSeriesExtrema(
    [
      '2026-02-27T00:10:00Z',
      '2026-02-27T00:15:00Z',
      '2026-02-27T00:20:00Z',
      '2026-02-27T00:25:00Z',
    ],
    [7, null, 2, 7],
  )
  assert.equal(known.result_state, 'known')
  if (known.result_state !== 'known') return
  assert.equal(known.maximum_at_utc, '2026-02-27T00:10:00Z')
  assert.equal(known.minimum_at_utc, '2026-02-27T00:20:00Z')
  assert.equal(known.null_point_count, 1)

  const empty = calculateFirstObservedSeriesExtrema(
    ['2026-02-27T00:10:00Z', '2026-02-27T00:25:00Z'],
    [null, null],
  )
  assert.equal(empty.result_state, 'empty_observed_set')
  assert.equal(empty.minimum, null)
  assert.equal(empty.observed_point_count, 0)

  const calls = { value: 0 }
  const kernel = new DomeyeTrustKernel()
  const firstDecision = admitted(kernel.admit(request(cap006(), goalState(1))))
  const first = await gateway(readModel(async () => seriesRead({
    values: [null, null, null, null],
  }), calls)).execute(firstDecision, [])
  assert.equal(first.status, 'succeeded')
  if (first.status !== 'succeeded') return
  const state = goalState(
    2,
    [first.artifact.artifact_id],
    ['CAP-006'],
    first.observation.observation_id,
  )
  const secondDecision = admitted(kernel.admit(request(
    cap016(first.artifact.artifact_id),
    state,
    { action_history: [first.receipt], artifacts: [first.artifact] },
  )))
  const second = await gateway(readModel(
    async () => { throw new Error('OP-01 不应读 API') },
    calls,
  )).execute(secondDecision, [first.artifact])
  assert.equal(second.status, 'succeeded')
  if (second.status !== 'succeeded') return
  assert.equal(second.artifact.artifact_kind, 'series_extrema')
  if (second.artifact.artifact_kind === 'series_extrema') {
    assert.equal(second.artifact.payload.result_state, 'empty_observed_set')
    assert.equal(second.artifact.payload.minimum, null)
  }
  assert.equal(second.observation.safe_summary.finding_input, null)

  const tampered = structuredClone(first.artifact) as DomeyeArtifactEnvelope
  if (tampered.artifact_kind !== 'metric_series') return
  tampered.payload.values[0] = 0
  const blocked = await gateway(readModel(
    async () => { throw new Error('OP-01 不应读 API') },
    calls,
  )).execute(secondDecision, [tampered])
  assert.equal(blocked.status, 'failed')
  assert.equal(blocked.receipt.failure_code, 'source_artifact_conflict')

  const wrongIdentity = structuredClone(first.artifact) as DomeyeArtifactEnvelope
  wrongIdentity.data_identity.publication_id = 'wrong-publication'
  const rejected = kernel.admit(request(
    cap016(wrongIdentity.artifact_id),
    goalState(2, [wrongIdentity.artifact_id], ['CAP-006']),
    { action_history: [first.receipt], artifacts: [wrongIdentity] },
  ))
  assert.equal(rejected.status, 'rejected')
  assert.equal(rejected.receipt.reason_code, 'source_artifact_conflict')
})
