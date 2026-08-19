import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import test from 'node:test'
import { Check } from 'typebox/value'

import type {
  CreateAgentSessionOptions,
  SessionStats,
  ToolDefinition,
} from '@earendil-works/pi-coding-agent'

import type {
  CountryOutageMetricSeriesRead,
  CountryOutageSeriesReadModel,
} from '../src/agent/capability-execution.js'
import {
  DomeyeAnswerContextSchema,
  DomeyeTypedFindingSchema,
  type DomeyeCapabilityProposal,
  type DomeyeDataIdentity,
  type DomeyeGoalState,
  type DomeyeRendererDraft,
} from '../src/agent/contracts.js'
import type {
  DomeyeDataIdentityVerifier,
  DomeyeVerifiedIdentityReceipt,
} from '../src/agent/country-outage-read-model.js'
import {
  DOMEYE_FIRST_SLICE_QUESTION,
  DomeyeFirstSliceRunError,
  DomeyeFirstSliceRuntime,
  type DomeyeFirstSliceCandidateBinding,
} from '../src/agent/first-slice-runtime.js'
import {
  renderCountryOutageDeterministicFallback,
} from '../src/agent/finding-answer.js'
import type {
  DomeyePiModelBinding,
} from '../src/agent/pi-interactive-agent-loop.js'
import {
  DOMEYE_GOAL_DISPOSITION_TOOL,
} from '../src/agent/pi-interactive-agent-loop.js'
import type {
  DomeyePiSessionFactory,
  DomeyePiSessionHandle,
} from '../src/agent/pi-runtime-boundary.js'

const NOW = '2026-08-19T06:00:00.000Z'
const REFERENCE = 'IR-RRC25-first-slice'
const CANDIDATE_ID = `git:xinghuahewo/domeye_@${'a'.repeat(40)}`
const SERIES_DIGEST = `sha256:${'9'.repeat(64)}`
const MODEL_IDENTITY = {
  candidate_id: 'first-slice-model-candidate',
  resource_sha256: `sha256:${'b'.repeat(64)}`,
  provider: 'first-slice-provider',
  model: 'first-slice-model',
  model_version: 'first-slice-model-v1',
  expected_response_model: 'first-slice-model',
  api: 'openai-completions' as const,
  base_url: 'https://provider.invalid',
  maximum_output_tokens: 4_096,
  thinking_level: 'off' as const,
  pi_version: '0.84.1' as const,
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
  window_end_utc: '2026-03-11T00:00:00Z',
  data_through: '2026-03-11T00:00:00Z',
  is_final_in_data_range: false,
  lifecycle_state: 'event_end_unknown',
}

const CANDIDATE: DomeyeFirstSliceCandidateBinding = {
  candidate_id: CANDIDATE_ID,
  contract_version: 'domeye.first-vertical-slice/v1.0',
  contract_digest: `sha256:${'f'.repeat(64)}`,
  data_identity: IDENTITY,
  series_response_sha256: SERIES_DIGEST,
  model_identity: MODEL_IDENTITY,
  budget_policy: {
    model_api_attempt_limit: 10,
    approved_action_limit: 2,
    cost_policy: 'audit_only',
    monetary_limit_usd: null,
  },
  policy: {
    policy_id: 'first-slice-policy-v1',
    policy_digest: `sha256:${'7'.repeat(64)}`,
    state: 'active',
    allowed_capability_ids: ['CAP-006', 'CAP-016'],
  },
  registry: {
    registry_snapshot_id: 'first-slice-registry-v1',
    registry_digest: `sha256:${'8'.repeat(64)}`,
    state: 'active',
    capabilities: [
      {
        capability_id: 'CAP-006',
        state: 'active',
        execution_binding: {
          execution_unit_id: 'TOOL-03',
          execution_unit_name: 'read_metric_series',
          execution_unit_version: '1.0.0',
          contract_digest: `sha256:${'1'.repeat(64)}`,
          implementation_digest: `sha256:${'2'.repeat(64)}`,
          semantic_digest: `sha256:${'3'.repeat(64)}`,
        },
      },
      {
        capability_id: 'CAP-016',
        state: 'active',
        execution_binding: {
          execution_unit_id: 'OP-01',
          execution_unit_name: 'series_extrema',
          execution_unit_version: '1.0.0',
          contract_digest: `sha256:${'4'.repeat(64)}`,
          implementation_digest: `sha256:${'5'.repeat(64)}`,
          semantic_digest: `sha256:${'6'.repeat(64)}`,
        },
      },
    ],
  },
}

const MODEL = {
  id: 'first-slice-model',
  name: 'first-slice-model',
  api: 'openai-completions',
  provider: 'first-slice-provider',
  baseUrl: 'https://provider.invalid',
  reasoning: false,
  input: ['text'],
  contextWindow: 100_000,
  maxTokens: 4_096,
  cost: { input: 1, output: 1, cacheRead: 0, cacheWrite: 0 },
} as NonNullable<CreateAgentSessionOptions['model']>

const MODEL_BINDING: DomeyePiModelBinding = {
  identity: MODEL_IDENTITY,
  model: MODEL,
  model_runtime: {} as NonNullable<CreateAgentSessionOptions['modelRuntime']>,
  thinking_level: 'off',
}

function sessionStats(
  sessionId: string,
  calls: number,
): SessionStats {
  return {
    sessionFile: undefined,
    sessionId,
    userMessages: calls,
    assistantMessages: calls,
    toolCalls: 0,
    toolResults: 0,
    totalMessages: calls * 2,
    tokens: {
      input: calls * 10,
      output: calls * 5,
      cacheRead: 0,
      cacheWrite: 0,
      total: calls * 15,
    },
    cost: calls * 0.001,
  }
}

function providerStream(calls: { value: number }, providerError?: string) {
  return () => {
    calls.value += 1
    const message = {
      role: 'assistant' as const,
      content: [],
      api: MODEL.api,
      provider: MODEL.provider,
      model: MODEL.id,
      responseModel: MODEL_BINDING.identity.expected_response_model,
      usage: {
        input: 0,
        output: 0,
        cacheRead: 0,
        cacheWrite: 0,
        totalTokens: 0,
        cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 },
      },
      stopReason: 'stop' as const,
      timestamp: 0,
    }
    return {
      async *[Symbol.asyncIterator]() {
        if (providerError) {
          yield {
            type: 'error',
            reason: 'error',
            error: {
              ...message,
              stopReason: 'error',
              errorMessage: providerError,
            },
          }
        } else {
          yield { type: 'done', reason: 'stop', message }
        }
      },
      async result() {
        return providerError
          ? { ...message, stopReason: 'error', errorMessage: providerError }
          : message
      },
    }
  }
}

function capabilityProposal(
  prompt: Record<string, unknown>,
  capabilityId: 'CAP-006' | 'CAP-016',
): DomeyeCapabilityProposal {
  const goal = prompt.semantic_goal as { goal_id: string }
  const state = prompt.goal_state as DomeyeGoalState
  if (capabilityId === 'CAP-006') {
    return {
      schema_version: 'domeye_agent_capability_proposal_v1',
      goal_id: goal.goal_id,
      goal_state_revision: state.state_revision,
      rationale: '先读取冻结 publication 中的固定 IPv4 地址量时序。',
      capability_id: 'CAP-006',
      input: { metric: 'fixed_visible_ipv4_address_count' },
    }
  }
  const observation = prompt.observation as {
    status: string
    capability_id: string
    artifact_ref: string | null
  }
  assert.equal(observation.status, 'succeeded')
  assert.equal(observation.capability_id, 'CAP-006')
  assert.ok(observation.artifact_ref)
  return {
    schema_version: 'domeye_agent_capability_proposal_v1',
    goal_id: goal.goal_id,
    goal_state_revision: state.state_revision,
    rationale: '只消费刚观察到的同身份冻结 Artifact。',
    capability_id: 'CAP-016',
    input: {
      metric: 'fixed_visible_ipv4_address_count',
      source_artifact_id: observation.artifact_ref,
      tie_policy: 'first_observed_occurrence',
    },
  }
}

function cognitionSessionFactory(options: {
  readonly calls: { value: number }
  readonly prompts: Record<string, unknown>[]
  readonly empty_disposition: 'stopped' | 'clarification_required'
  readonly provider_error?: string
}): DomeyePiSessionFactory {
  return async (sessionOptions) => {
    let lastText: string | undefined
    const messages: unknown[] = []
    const rawStream = providerStream(options.calls, options.provider_error)
    const session: DomeyePiSessionHandle = {
      agent: {
        streamFunction: rawStream as unknown as
          DomeyePiSessionHandle['agent']['streamFunction'],
      },
      messages,
      async prompt(text) {
        const stream = await session.agent.streamFunction(
          MODEL,
          { messages: [] },
        )
        for await (const _event of stream) {
          // 消费脚本化供应方流，使共享尝试边界记录这一次调用。
        }
        if (options.provider_error) {
          messages.push({
            role: 'assistant',
            content: [],
            stopReason: 'error',
            errorMessage: options.provider_error,
          })
          return
        }
        const prompt = JSON.parse(text) as Record<string, unknown>
        options.prompts.push(prompt)
        const proposalTool = sessionOptions.customTools?.find(
          (tool) => tool.name === 'propose_domeye_capability',
        ) as ToolDefinition | undefined
        const dispositionTool = sessionOptions.customTools?.find(
          (tool) => tool.name === DOMEYE_GOAL_DISPOSITION_TOOL,
        ) as ToolDefinition | undefined
        const index = options.prompts.length - 1
        if (index === 0) {
          await proposalTool?.execute(
            'proposal-cap-006',
            capabilityProposal(prompt, 'CAP-006'),
            undefined,
            undefined,
            {} as never,
          )
          lastText = undefined
          return
        }
        if (index === 1) {
          await proposalTool?.execute(
            'proposal-cap-016',
            capabilityProposal(prompt, 'CAP-016'),
            undefined,
            undefined,
            {} as never,
          )
          lastText = undefined
          return
        }
        if (index !== 2) throw new Error('unexpected_cognition_prompt')
        const goal = prompt.semantic_goal as { goal_id: string }
        const state = prompt.goal_state as DomeyeGoalState
        const observation = prompt.observation as {
          status: string
          capability_id: string
          safe_summary: { result_state: string }
        }
        assert.equal(observation.status, 'succeeded')
        assert.equal(observation.capability_id, 'CAP-016')
        const empty = observation.safe_summary.result_state
          === 'empty_observed_set'
        await dispositionTool?.execute(
          'goal-disposition',
          {
            schema_version: 'domeye_agent_goal_disposition_v1',
            goal_id: goal.goal_id,
            goal_state_revision: state.state_revision,
            disposition: empty
              ? options.empty_disposition
              : 'goal_satisfied',
            reason_code: empty
              ? 'empty_observed_set'
              : 'finding_input_ready',
          },
          undefined,
          undefined,
          {} as never,
        )
        lastText = undefined
      },
      async abort() {},
      getSessionStats: () => sessionStats('cognition-session', options.calls.value),
      getLastAssistantText: () => lastText,
      dispose() {},
    }
    return { session }
  }
}

function rendererSessionFactory(options: {
  readonly calls: { value: number }
  readonly contexts: { context_id: string }[]
  readonly mutate?: (draft: DomeyeRendererDraft) => DomeyeRendererDraft
}): DomeyePiSessionFactory {
  return async () => {
    let lastText: string | undefined
    const rawStream = providerStream(options.calls)
    const session: DomeyePiSessionHandle = {
      agent: {
        streamFunction: rawStream as unknown as
          DomeyePiSessionHandle['agent']['streamFunction'],
      },
      messages: [],
      async prompt(text) {
        const stream = await session.agent.streamFunction(
          MODEL,
          { messages: [] },
        )
        for await (const _event of stream) {
          // 只消费一次 Renderer 供应方调用；不进行内部重试。
        }
        const input = JSON.parse(text) as {
          renderer_draft_skeleton: DomeyeRendererDraft
          text_must_include_exact: string[]
        }
        options.contexts.push({
          context_id: input.renderer_draft_skeleton.context_id,
        })
        const draft = input.renderer_draft_skeleton
        lastText = JSON.stringify(options.mutate?.(draft) ?? draft)
      },
      async abort() {},
      getSessionStats: () => sessionStats('renderer-session', options.calls.value),
      getLastAssistantText: () => lastText,
      dispose() {},
    }
    return { session }
  }
}

function identityVerifier(calls: { value: number }): DomeyeDataIdentityVerifier {
  return {
    async verify(request): Promise<DomeyeVerifiedIdentityReceipt> {
      calls.value += 1
      assert.equal(request.candidate_id, CANDIDATE_ID)
      assert.equal(request.publication_id, IDENTITY.publication_id)
      assert.equal(request.revision, IDENTITY.revision)
      return Object.freeze({
        schema_version: 'domeye_verified_data_identity_receipt_v1',
        receipt_id: 'identity-receipt-first-slice',
        candidate_id: CANDIDATE_ID,
        reference_sha256: createHash('sha256')
          .update(request.reference, 'utf8')
          .digest('hex'),
        data_identity: IDENTITY,
        resolver_response_sha256: 'a'.repeat(64),
        overview_response_sha256: 'b'.repeat(64),
        evidence_refs: ['domeye:/events/resolve', 'domeye:/overview'],
        immutable: true,
        verified_at_utc: NOW,
      })
    },
  }
}

function seriesReadModel(
  values: readonly (number | null)[],
  calls: { value: number },
): CountryOutageSeriesReadModel {
  return {
    async readMetricSeries(request): Promise<CountryOutageMetricSeriesRead> {
      calls.value += 1
      assert.deepEqual(request.data_identity, IDENTITY)
      assert.equal(request.metric, 'fixed_visible_ipv4_address_count')
      const start = Date.parse(IDENTITY.window_start_utc)
      const end = Date.parse(IDENTITY.window_end_utc)
      const timestamps = Array.from(
        { length: (end - start) / 300_000 + 1 },
        (_value, index) => new Date(start + index * 300_000)
          .toISOString()
          .replace('.000Z', 'Z'),
      )
      const expandedValues = Array<(number | null)>(timestamps.length)
        .fill(values[0] ?? null)
      if (values.every((value) => value === null)) {
        expandedValues.fill(null)
      } else {
        const minimumIndex = timestamps.indexOf('2026-02-28T14:35:00Z')
        expandedValues[minimumIndex] = values[1] ?? null
        expandedValues[expandedValues.length - 1] = values[2] ?? null
      }
      return {
        data_identity: IDENTITY,
        metric: 'fixed_visible_ipv4_address_count',
        unit: 'unique_ipv4_address',
        population_definition:
          'normalized_deduplicated_merged_fixed_prefix_ipv4_unique_address_union',
        timestamps_utc: timestamps,
        values: expandedValues,
        definition: '固定前缀经规范化、去重和重叠合并后的 IPv4 唯一地址并集',
        source_response_sha256: SERIES_DIGEST,
        completeness: { state: 'complete', missing_slot_count: 0 },
        evidence_refs: [
          'domeye:/series#/timestamps',
          'domeye:/series#/tracks/fixed_visible_ipv4_address_count',
        ],
      }
    },
  }
}

function runtime(options: {
  readonly values: readonly (number | null)[]
  readonly cognition_calls: { value: number }
  readonly renderer_calls: { value: number }
  readonly read_calls: { value: number }
  readonly identity_calls: { value: number }
  readonly prompts: Record<string, unknown>[]
  readonly contexts: { context_id: string }[]
  readonly empty_disposition?: 'stopped' | 'clarification_required'
  readonly mutate_draft?: (draft: DomeyeRendererDraft) => DomeyeRendererDraft
  readonly cognition_provider_error?: string
}): DomeyeFirstSliceRuntime {
  return new DomeyeFirstSliceRuntime({
    candidate: CANDIDATE,
    model_binding: MODEL_BINDING,
    identity_verifier: identityVerifier(options.identity_calls),
    series_read_model: seriesReadModel(options.values, options.read_calls),
    revocation: () => ({
      state: 'not_revoked',
      checked_at_utc: NOW,
      reason_code: null,
    }),
    cognition_session_factory: cognitionSessionFactory({
      calls: options.cognition_calls,
      prompts: options.prompts,
      empty_disposition: options.empty_disposition ?? 'stopped',
      ...(options.cognition_provider_error
        ? { provider_error: options.cognition_provider_error }
        : {}),
    }),
    renderer_session_factory: rendererSessionFactory({
      calls: options.renderer_calls,
      contexts: options.contexts,
      ...(options.mutate_draft
        ? { mutate: options.mutate_draft }
        : {}),
    }),
    now: () => new Date(NOW),
  })
}

function runRequest() {
  return {
    reference: REFERENCE,
    publication_id: IDENTITY.publication_id,
    revision: IDENTITY.revision,
    question: DOMEYE_FIRST_SLICE_QUESTION,
    principal: {
      principal_id: 'user-first-slice',
      authorization_scopes: ['country_outage:read'],
    },
  }
}

function counters() {
  return {
    cognition_calls: { value: 0 },
    renderer_calls: { value: 0 },
    read_calls: { value: 0 },
    identity_calls: { value: 0 },
    prompts: [] as Record<string, unknown>[],
    contexts: [] as { context_id: string }[],
  }
}

test('同一 Candidate 完成 Observation 后再规划 CAP-016，并经 Finding→Context→Renderer→Guard 回答', async () => {
  const observed = counters()
  const result = await runtime({
    ...observed,
    values: [10_156_800, 9_577_728, 10_069_760],
  }).run(runRequest())

  assert.equal(result.outcome, 'completed')
  if (result.outcome !== 'completed') return
  assert.equal(observed.identity_calls.value, 1)
  assert.equal(observed.read_calls.value, 1)
  assert.equal(observed.cognition_calls.value, 3)
  assert.equal(observed.renderer_calls.value, 1)
  assert.equal(observed.prompts.length, 3)
  assert.equal(observed.contexts.length, 1)
  assert.deepEqual(
    result.loop.action_receipts.map((receipt) => receipt.capability_id),
    ['CAP-006', 'CAP-016'],
  )
  assert.deepEqual(
    result.loop.observations.map((observation) => observation.capability_id),
    ['CAP-006', 'CAP-016'],
  )
  const secondPromptObservation = observed.prompts[1]?.observation as {
    capability_id: string
    artifact_ref: string | null
  }
  assert.equal(secondPromptObservation.capability_id, 'CAP-006')
  assert.equal(
    secondPromptObservation.artifact_ref,
    result.loop.artifacts[0]?.artifact_id,
  )
  assert.equal(result.candidate_id, CANDIDATE_ID)
  assert.equal(result.finding.candidate_id, CANDIDATE_ID)
  assert.equal(result.answer_context.candidate_id, CANDIDATE_ID)
  assert.equal(result.answer_context.context_id, observed.contexts[0]?.context_id)
  assert.equal(Check(DomeyeTypedFindingSchema, result.finding), true)
  assert.equal(Check(DomeyeAnswerContextSchema, result.answer_context), true)
  assert.equal(result.finding.values.minimum, 9_577_728)
  assert.equal(
    result.finding.values.minimum_at_utc,
    '2026-02-28T14:35:00Z',
  )
  assert.equal(result.finding.values.difference, 579_072)
  assert.equal(result.answer.source, 'renderer')
  assert.equal(result.answer.guard_result.decision, 'pass')
  assert.equal(result.usage.attempt_count, 4)
  assert.equal(result.usage.maximum_attempt_count, 10)
  assert.equal(result.usage.cost_policy, 'audit_only')
  assert.deepEqual(
    result.usage.attempts.map((attempt) => attempt.phase),
    ['cognition', 'cognition', 'cognition', 'renderer'],
  )
})

test('全 null 保留 empty_observed_set，不生成 0 或调用 Renderer', async () => {
  const observed = counters()
  const result = await runtime({
    ...observed,
    values: [null, null, null],
    empty_disposition: 'stopped',
  }).run(runRequest())

  assert.equal(result.outcome, 'stopped')
  assert.equal(result.finding, null)
  assert.equal(result.answer_context, null)
  assert.equal(result.answer, null)
  assert.equal(observed.read_calls.value, 1)
  assert.equal(observed.cognition_calls.value, 3)
  assert.equal(observed.renderer_calls.value, 0)
  assert.equal(result.usage.attempt_count, 3)
  const extrema = result.loop.artifacts.find(
    (artifact) => artifact.artifact_kind === 'series_extrema',
  )
  assert.equal(extrema?.artifact_kind, 'series_extrema')
  if (extrema?.artifact_kind === 'series_extrema') {
    assert.equal(extrema.payload.result_state, 'empty_observed_set')
    assert.equal(extrema.payload.observed_point_count, 0)
    assert.equal(extrema.payload.minimum, null)
    assert.equal(extrema.payload.maximum, null)
    assert.equal(extrema.payload.difference, null)
  }
  assert.equal(
    result.loop.observations[1]?.safe_summary.result_state,
    'empty_observed_set',
  )
})

test('Renderer 草稿被 Guard 阻断后只用同一 Context 回退且不重试模型', async () => {
  const observed = counters()
  const result = await runtime({
    ...observed,
    values: [10_156_800, 9_577_728, 10_069_760],
    mutate_draft: (draft) => ({
      ...draft,
      values: {
        ...draft.values,
        minimum: (draft.values.minimum ?? 0) + 1,
      },
    }),
  }).run(runRequest())

  assert.equal(result.outcome, 'completed')
  if (result.outcome !== 'completed') return
  assert.equal(result.answer.source, 'deterministic_fallback')
  assert.equal(result.answer.guard_result.decision, 'block')
  assert.ok(result.answer.guard_result.reason_codes.includes('number_mismatch'))
  assert.equal(
    result.answer.answer,
    renderCountryOutageDeterministicFallback(result.answer_context),
  )
  assert.equal(observed.contexts.length, 1)
  assert.equal(observed.contexts[0]?.context_id, result.answer_context.context_id)
  assert.equal(observed.renderer_calls.value, 1)
  assert.equal(observed.cognition_calls.value, 3)
  assert.equal(result.usage.attempt_count, 4)
  assert.equal(
    result.usage.attempts.filter((attempt) => attempt.phase === 'renderer').length,
    1,
  )
})

test('供应方失败携带同一 Candidate 的部分执行证据与共享调用账本', async () => {
  const observed = counters()
  await assert.rejects(
    () => runtime({
      ...observed,
      values: [10_156_800, 9_577_728, 10_069_760],
      cognition_provider_error: 'provider_request_failed',
    }).run(runRequest()),
    (error: unknown) => {
      assert.ok(error instanceof DomeyeFirstSliceRunError)
      assert.equal(error.code, 'cognition_provider_failed')
      assert.equal(error.evidence.candidate_id, CANDIDATE_ID)
      assert.equal(error.evidence.usage.attempt_count, 1)
      assert.equal(error.evidence.usage.attempts[0]?.outcome, 'failed')
      assert.equal(
        error.evidence.usage.attempts[0]?.failure_code,
        'provider_request_failed',
      )
      assert.equal(error.evidence.loop_failure.admission_receipts.length, 0)
      assert.equal(error.evidence.loop_failure.action_receipts.length, 0)
      assert.equal(error.evidence.loop_failure.artifacts.length, 0)
      return true
    },
  )
  assert.equal(observed.cognition_calls.value, 1)
  assert.equal(observed.read_calls.value, 0)
  assert.equal(observed.renderer_calls.value, 0)
})
