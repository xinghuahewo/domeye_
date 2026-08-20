import assert from 'node:assert/strict'
import test from 'node:test'

import type {
  CreateAgentSessionOptions,
  SessionStats,
  ToolDefinition,
} from '@earendil-works/pi-coding-agent'
import { Check } from 'typebox/value'

import {
  DomeyeCapabilityGateway,
  type CountryOutageSeriesReadModel,
} from '../src/agent/capability-execution.js'
import type {
  DomeyeCapabilityObservation,
  DomeyeCapabilityProposal,
  DomeyeDataIdentity,
  DomeyeGoalDisposition,
  DomeyeGoalState,
  DomeyeSemanticGoal,
} from '../src/agent/contracts.js'
import { DomeyeGoalDispositionSchema } from '../src/agent/contracts.js'
import {
  DOMEYE_CAPABILITY_PROPOSAL_TOOL,
  DOMEYE_GOAL_DISPOSITION_TOOL,
  PiInteractiveAgentLoop,
  type DomeyePiModelBinding,
} from '../src/agent/pi-interactive-agent-loop.js'
import type {
  DomeyePiSessionFactory,
  DomeyePiSessionHandle,
} from '../src/agent/pi-runtime-boundary.js'
import {
  DomeyeTrustKernel,
  type DomeyePolicySnapshotView,
  type DomeyeRegistrySnapshotView,
} from '../src/agent/trust-kernel.js'

const identity: DomeyeDataIdentity = {
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

const goal: DomeyeSemanticGoal = {
  schema_version: 'domeye_agent_semantic_goal_v1',
  goal_id: 'goal-first-slice',
  requested_text: '最低是多少，首次何时出现？首末最大和极差呢？',
  objective: 'find_fixed_visible_ipv4_series_extrema',
  metric: 'fixed_visible_ipv4_address_count',
  data_identity: identity,
  created_at_utc: '2026-08-19T06:00:00Z',
}

const initialState: DomeyeGoalState = {
  schema_version: 'domeye_agent_goal_state_v1',
  goal_id: goal.goal_id,
  state_revision: 1,
  status: 'active',
  completed_capability_ids: [],
  artifact_ids: [],
  finding_ids: [],
  last_observation_id: null,
  updated_at_utc: '2026-08-19T06:00:00Z',
}

const binding06 = {
  execution_unit_id: 'TOOL-03' as const,
  execution_unit_name: 'read_metric_series' as const,
  execution_unit_version: '1.0.0' as const,
  contract_digest: `sha256:${'1'.repeat(64)}`,
  implementation_digest: `sha256:${'2'.repeat(64)}`,
  semantic_digest: `sha256:${'3'.repeat(64)}`,
}

const binding16 = {
  execution_unit_id: 'OP-01' as const,
  execution_unit_name: 'series_extrema' as const,
  execution_unit_version: '1.0.0' as const,
  contract_digest: `sha256:${'4'.repeat(64)}`,
  implementation_digest: `sha256:${'5'.repeat(64)}`,
  semantic_digest: `sha256:${'6'.repeat(64)}`,
}

const policy: DomeyePolicySnapshotView = {
  policy_id: 'first-slice-policy-v1',
  policy_digest: `sha256:${'7'.repeat(64)}`,
  state: 'active',
  allowed_capability_ids: ['CAP-006', 'CAP-016'],
}

const registry: DomeyeRegistrySnapshotView = {
  registry_snapshot_id: 'first-slice-registry-v1',
  registry_digest: `sha256:${'8'.repeat(64)}`,
  state: 'active',
  capabilities: [
    { capability_id: 'CAP-006', state: 'active', execution_binding: binding06 },
    { capability_id: 'CAP-016', state: 'active', execution_binding: binding16 },
  ],
}

const model = {
  id: 'model-first-slice',
  name: 'model-first-slice',
  api: 'openai-completions',
  provider: 'provider-first-slice',
  baseUrl: 'https://provider.invalid',
  reasoning: false,
  input: ['text'],
  contextWindow: 100_000,
  maxTokens: 4_096,
  cost: { input: 1, output: 1, cacheRead: 0, cacheWrite: 0 },
} as NonNullable<CreateAgentSessionOptions['model']>

const modelBinding: DomeyePiModelBinding = {
  identity: {
    candidate_id: 'model-first-slice-candidate',
    resource_sha256: `sha256:${'a'.repeat(64)}`,
    provider: 'provider-first-slice',
    model: 'model-first-slice',
    model_version: 'model-first-slice-v1',
    expected_response_model: 'model-first-slice',
    api: 'openai-completions',
    base_url: 'https://provider.invalid',
    maximum_output_tokens: 4_096,
    thinking_level: 'off',
    pi_version: '0.84.1',
  },
  model,
  model_runtime: {} as NonNullable<CreateAgentSessionOptions['modelRuntime']>,
  thinking_level: 'off',
}

type ScriptStep = (
  prompt: Record<string, unknown>,
) => {
  proposals?: unknown[]
  dispositions?: unknown[]
  assistantText?: string
  providerError?: string
  responseModel?: string | null
}

function stats(apiCalls: number): SessionStats {
  return {
    sessionFile: undefined,
    sessionId: 'interactive-loop-test',
    userMessages: apiCalls,
    assistantMessages: apiCalls,
    toolCalls: 0,
    toolResults: 0,
    totalMessages: apiCalls * 2,
    tokens: {
      input: apiCalls * 10,
      output: apiCalls * 5,
      cacheRead: 0,
      cacheWrite: 0,
      total: apiCalls * 15,
    },
    cost: apiCalls * 0.001,
  }
}

function scriptedSessionFactory(
  steps: readonly ScriptStep[],
  networkCalls: { value: number },
): DomeyePiSessionFactory {
  return async (options) => {
    let index = 0
    let lastText: string | undefined
    let providerError: string | undefined
    let responseModel: string | null =
      modelBinding.identity.expected_response_model
    const messages: unknown[] = []
    const rawStream = () => {
      networkCalls.value += 1
      const message = {
        role: 'assistant' as const,
        content: [],
        api: model.api,
        provider: model.provider,
        model: model.id,
        ...(responseModel === null ? {} : { responseModel }),
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
              error: {
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
            ? { stopReason: 'error', errorMessage: providerError }
            : message
        },
      }
    }
    const session: DomeyePiSessionHandle = {
      agent: {
        streamFunction: rawStream as unknown as DomeyePiSessionHandle['agent']['streamFunction'],
      },
      messages,
      async prompt(text) {
        const step = steps[index++]
        if (!step) throw new Error('unexpected_prompt')
        const response = step(JSON.parse(text) as Record<string, unknown>)
        providerError = response.providerError
        responseModel = response.responseModel === undefined
          ? modelBinding.identity.expected_response_model
          : response.responseModel
        const stream = await session.agent.streamFunction(model, { messages: [] })
        let successfulDone = false
        let boundaryError = false
        for await (const event of stream) {
          const item = event as { type?: string }
          successfulDone ||= item.type === 'done'
          boundaryError ||= item.type === 'error'
        }
        if (providerError || boundaryError || !successfulDone) {
          messages.push({
            role: 'assistant',
            content: [],
            stopReason: 'error',
            errorMessage: providerError ?? 'provider_response_identity_mismatch',
          })
          return
        }
        const proposalTool = options.customTools?.find(
          (tool) => tool.name === DOMEYE_CAPABILITY_PROPOSAL_TOOL,
        ) as ToolDefinition | undefined
        const dispositionTool = options.customTools?.find(
          (tool) => tool.name === DOMEYE_GOAL_DISPOSITION_TOOL,
        ) as ToolDefinition | undefined
        assert.ok(proposalTool)
        assert.ok(dispositionTool)
        for (const proposal of response.proposals ?? []) {
          const result = await proposalTool.execute(
            `tool-${index}`,
            proposal,
            undefined,
            undefined,
            {} as never,
          )
          assert.equal(result.terminate, true)
        }
        for (const disposition of response.dispositions ?? []) {
          const result = await dispositionTool.execute(
            `disposition-${index}`,
            disposition,
            undefined,
            undefined,
            {} as never,
          )
          assert.equal(result.terminate, true)
        }
        lastText = response.assistantText
        if (lastText) {
          messages.push({
            role: 'assistant',
            content: [{ type: 'text', text: lastText }],
          })
        }
      },
      async abort() {},
      getSessionStats: () => stats(networkCalls.value),
      getLastAssistantText: () => lastText,
      dispose() {},
    }
    return { session }
  }
}

function loop(
  steps: readonly ScriptStep[],
  readModel: CountryOutageSeriesReadModel,
  networkCalls = { value: 0 },
  now: () => Date = () => new Date('2026-08-19T06:00:00Z'),
): PiInteractiveAgentLoop {
  return new PiInteractiveAgentLoop({
    model_binding: modelBinding,
    candidate_id: 'first-slice-candidate',
    principal: {
      principal_id: 'user-1',
      authorization_scopes: ['country_outage:read'],
    },
    policy,
    registry,
    revocation: () => ({
      state: 'not_revoked',
      checked_at_utc: '2026-08-19T06:00:00Z',
      reason_code: null,
    }),
    trust_kernel: new DomeyeTrustKernel(),
    capability_gateway: new DomeyeCapabilityGateway({
      series_read_model: readModel,
      expected_series_response_sha256: `sha256:${'9'.repeat(64)}`,
      now,
    }),
    session_factory: scriptedSessionFactory(steps, networkCalls),
    now,
  })
}

function advancingClock(): () => Date {
  const startedAt = Date.parse('2026-08-19T06:00:00.001Z')
  let elapsedMs = 0
  return () => new Date(startedAt + elapsedMs++)
}

function cap006(prompt: Record<string, unknown>): DomeyeCapabilityProposal {
  const state = prompt.goal_state as DomeyeGoalState
  return {
    schema_version: 'domeye_agent_capability_proposal_v1',
    goal_id: goal.goal_id,
    goal_state_revision: state.state_revision,
    rationale: '先读取固定指标时序',
    capability_id: 'CAP-006',
    input: { metric: 'fixed_visible_ipv4_address_count' },
  }
}

function cap016(prompt: Record<string, unknown>): DomeyeCapabilityProposal {
  const state = prompt.goal_state as DomeyeGoalState
  const observation = prompt.observation as { artifact_ref: string }
  return {
    schema_version: 'domeye_agent_capability_proposal_v1',
    goal_id: goal.goal_id,
    goal_state_revision: state.state_revision,
    rationale: '只计算已冻结 Artifact 的极值',
    capability_id: 'CAP-016',
    input: {
      metric: 'fixed_visible_ipv4_address_count',
      source_artifact_id: observation.artifact_ref,
      tie_policy: 'first_observed_occurrence',
    },
  }
}

function goalDisposition(
  prompt: Record<string, unknown>,
  disposition: DomeyeGoalDisposition['disposition'],
  reasonCode: string,
): DomeyeGoalDisposition {
  const state = prompt.goal_state as DomeyeGoalState
  return {
    schema_version: 'domeye_agent_goal_disposition_v1',
    goal_id: goal.goal_id,
    goal_state_revision: state.state_revision,
    disposition,
    reason_code: reasonCode,
  }
}

const readModel: CountryOutageSeriesReadModel = {
  async readMetricSeries() {
    const start = Date.parse(identity.window_start_utc)
    const end = Date.parse(identity.window_end_utc)
    const timestamps = Array.from(
      { length: (end - start) / 300_000 + 1 },
      (_value, index) => new Date(start + index * 300_000)
        .toISOString()
        .replace('.000Z', 'Z'),
    )
    const values: number[] = timestamps.map((_timestamp, index) =>
      index === 0
        ? 10156800
        : index === timestamps.length - 1
          ? 10069760
          : 10000000,
    )
    const minimumIndex = timestamps.indexOf('2026-02-28T14:35:00Z')
    values[minimumIndex] = 9577728
    return {
      data_identity: identity,
      metric: 'fixed_visible_ipv4_address_count',
      unit: 'unique_ipv4_address',
      population_definition:
        'normalized_deduplicated_merged_fixed_prefix_ipv4_unique_address_union',
      timestamps_utc: timestamps,
      values,
      definition: '固定前缀经规范化去重和重叠合并后的 IPv4 唯一地址并集',
      source_response_sha256: `sha256:${'9'.repeat(64)}`,
      completeness: { state: 'complete', missing_slot_count: 0 },
      evidence_refs: ['domeye:/series#fixed_visible_ipv4_address_count'],
    }
  },
}

test('真实交互顺序为 CAP-006 Observation 后才提出 CAP-016', async () => {
  const networkCalls = { value: 0 }
  const runtime = loop([
    (prompt) => ({ proposals: [cap006(prompt)] }),
    (prompt) => ({ proposals: [cap016(prompt)] }),
    (prompt) => {
      assert.equal(prompt.evidence_ready_for_finding_context, true)
      assert.deepEqual(prompt.required_goal_disposition, {
        disposition: 'goal_satisfied',
        reason_code: 'finding_input_ready',
      })
      assert.match(String(prompt.instruction), /不负责渲染最终答案/)
      assert.match(String(prompt.instruction), /Finding 输入已就绪/)
      const observation = prompt.observation as {
        artifact_ref: string
        safe_summary: { finding_input: unknown }
      }
      assert.deepEqual(observation.safe_summary.finding_input, {
        state: 'ready',
        source_artifact_ref: (prompt.goal_state as DomeyeGoalState)
          .artifact_ids[0],
        extrema_artifact_ref: observation.artifact_ref,
        extrema_result_state: 'known',
        next_owner: 'domeye_typed_finding_builder',
      })
      return {
        dispositions: [goalDisposition(
          prompt,
          'goal_satisfied',
          'finding_input_ready',
        )],
      }
    },
  ], readModel, networkCalls)

  const result = await runtime.run(goal, initialState)
  assert.equal(result.goal_state.status, 'answer_pending')
  assert.deepEqual(
    result.action_receipts.map((item) => item.capability_id),
    ['CAP-006', 'CAP-016'],
  )
  assert.deepEqual(
    result.artifacts.map((item) => item.artifact_kind),
    ['metric_series', 'series_extrema'],
  )
  const extrema = result.artifacts[1]
  assert.equal(extrema?.artifact_kind, 'series_extrema')
  if (extrema?.artifact_kind === 'series_extrema') {
    assert.equal(extrema.payload.minimum, 9577728)
    assert.equal(extrema.payload.minimum_at_utc, '2026-02-28T14:35:00Z')
    assert.equal(extrema.payload.difference, 579072)
  }
  assert.equal(result.usage.attempt_count, 3)
  assert.equal(networkCalls.value, 3)
  assert.equal(result.decision_protocol_rejections.length, 0)
  assert.equal(result.disposition.disposition, 'goal_satisfied')
})

test('真实递增时钟下成功 Goal State 精确继承对应 Observation 时间', async () => {
  const networkCalls = { value: 0 }
  const runtime = loop([
    (prompt) => ({ proposals: [cap006(prompt)] }),
    (prompt) => {
      const state = prompt.goal_state as DomeyeGoalState
      const observation = prompt.observation as DomeyeCapabilityObservation
      assert.equal(state.updated_at_utc, observation.created_at_utc)
      return { proposals: [cap016(prompt)] }
    },
    (prompt) => {
      const state = prompt.goal_state as DomeyeGoalState
      const observation = prompt.observation as DomeyeCapabilityObservation
      assert.equal(state.updated_at_utc, observation.created_at_utc)
      return {
        dispositions: [goalDisposition(
          prompt,
          'goal_satisfied',
          'finding_input_ready',
        )],
      }
    },
  ], readModel, networkCalls, advancingClock())

  const result = await runtime.run(goal, initialState)
  assert.equal(result.goal_state.status, 'answer_pending')
  assert.equal(result.observations.length, 2)
  assert.ok(
    Date.parse(result.goal_state.updated_at_utc)
      > Date.parse(result.observations[1]!.created_at_utc),
  )
})

test('真实递增时钟下 rejected Goal State 精确继承拒绝 Observation 时间', async () => {
  const runtime = loop([
    (prompt) => {
      const state = prompt.goal_state as DomeyeGoalState
      return {
        proposals: [{
          schema_version: 'domeye_agent_capability_proposal_v1',
          goal_id: goal.goal_id,
          goal_state_revision: state.state_revision,
          rationale: '敌对地跳过读取',
          capability_id: 'CAP-016',
          input: {
            metric: 'fixed_visible_ipv4_address_count',
            source_artifact_id: 'artifact-not-present',
            tie_policy: 'first_observed_occurrence',
          },
        }],
      }
    },
    (prompt) => {
      const state = prompt.goal_state as DomeyeGoalState
      const observation = prompt.observation as DomeyeCapabilityObservation
      assert.equal(observation.status, 'rejected')
      assert.equal(state.updated_at_utc, observation.created_at_utc)
      return {
        dispositions: [goalDisposition(
          prompt,
          'stopped',
          'source_artifact_missing',
        )],
      }
    },
  ], readModel, { value: 0 }, advancingClock())

  const result = await runtime.run(goal, initialState)
  assert.equal(result.goal_state.status, 'stopped')
  assert.equal(result.observations[0]?.status, 'rejected')
  assert.ok(
    Date.parse(result.goal_state.updated_at_utc)
      > Date.parse(result.observations[0]!.created_at_utc),
  )
})

test('Finding 输入 ready 后拒绝澄清、停止与错误成功原因，并精确重问', async () => {
  const networkCalls = { value: 0 }
  const wrongReadyDispositions = [
    ['clarification_required', 'numeric_findings_not_surfaced'],
    ['stopped', 'premature_stop'],
    ['goal_satisfied', 'extrema_artifact_available'],
  ] as const
  const runtime = loop([
    (prompt) => ({ proposals: [cap006(prompt)] }),
    (prompt) => ({ proposals: [cap016(prompt)] }),
    ...wrongReadyDispositions.map(([disposition, reasonCode]) =>
      (prompt: Record<string, unknown>) => {
        assert.equal(prompt.evidence_ready_for_finding_context, true)
        assert.deepEqual(prompt.required_goal_disposition, {
          disposition: 'goal_satisfied',
          reason_code: 'finding_input_ready',
        })
        return {
          dispositions: [goalDisposition(prompt, disposition, reasonCode)],
        }
      }),
    (prompt) => ({
      dispositions: [goalDisposition(
        prompt,
        'goal_satisfied',
        'finding_input_ready',
      )],
    }),
  ], readModel, networkCalls)

  const result = await runtime.run(goal, initialState)
  assert.equal(result.goal_state.status, 'answer_pending')
  assert.equal(result.disposition.reason_code, 'finding_input_ready')
  assert.equal(result.action_receipts.length, 2)
  assert.deepEqual(
    result.decision_protocol_rejections.map((item) => item.sequence),
    [3, 4, 5],
  )
  assert.equal(result.usage.attempt_count, 6)
  assert.equal(networkCalls.value, 6)
})

test('同一响应提交多个决策工具时整批拒绝且领域执行数为零', async () => {
  let readCount = 0
  const runtime = loop([
    (prompt) => ({ proposals: [cap006(prompt), cap006(prompt)] }),
    (prompt) => ({
      dispositions: [goalDisposition(
        prompt,
        'stopped',
        'proposal_protocol_rejected',
      )],
    }),
  ], {
    async readMetricSeries() {
      readCount += 1
      return await readModel.readMetricSeries({
        data_identity: identity,
        metric: 'fixed_visible_ipv4_address_count',
      })
    },
  })

  const result = await runtime.run(goal, initialState)
  assert.equal(readCount, 0)
  assert.equal(result.action_receipts.length, 0)
  assert.equal(result.admission_receipts.length, 0)
  assert.equal(
    result.decision_protocol_rejections[0]?.reason_code,
    'multiple_decisions_in_single_response',
  )
  assert.equal(
    result.decision_protocol_rejections[0]?.observed_disposition_count,
    0,
  )
})

test('未取得 TOOL-03 Artifact 就提出 CAP-016 时 Trust Kernel 拒绝且 Operator 不执行', async () => {
  const runtime = loop([
    (prompt) => {
      const state = prompt.goal_state as DomeyeGoalState
      return {
        proposals: [{
          schema_version: 'domeye_agent_capability_proposal_v1',
          goal_id: goal.goal_id,
          goal_state_revision: state.state_revision,
          rationale: '敌对地跳过读取',
          capability_id: 'CAP-016',
          input: {
            metric: 'fixed_visible_ipv4_address_count',
            source_artifact_id: 'artifact-not-present',
            tie_policy: 'first_observed_occurrence',
          },
        }],
      }
    },
    (prompt) => ({
      dispositions: [goalDisposition(
        prompt,
        'stopped',
        'source_artifact_missing',
      )],
    }),
  ], readModel)

  const result = await runtime.run(goal, initialState)
  assert.equal(result.action_receipts.length, 0)
  assert.equal(result.artifacts.length, 0)
  assert.equal(result.admission_receipts[0]?.decision, 'rejected')
  assert.equal(result.admission_receipts[0]?.reason_code, 'goal_state_conflict')
  assert.equal(result.observations[0]?.status, 'rejected')
  assert.equal(result.observations[0]?.safe_summary.finding_input, null)
})

test('Proposal 与 Goal Disposition 同轮出现时二者都不进入领域执行', async () => {
  let readCount = 0
  const runtime = loop([
    (prompt) => ({
      proposals: [cap006(prompt)],
      dispositions: [goalDisposition(
        prompt,
        'stopped',
        'mixed_decision_tools',
      )],
    }),
    (prompt) => ({
      dispositions: [goalDisposition(
        prompt,
        'stopped',
        'decision_protocol_rejected',
      )],
    }),
  ], {
    async readMetricSeries() {
      readCount += 1
      return await readModel.readMetricSeries({
        data_identity: identity,
        metric: 'fixed_visible_ipv4_address_count',
      })
    },
  })

  const result = await runtime.run(goal, initialState)
  assert.equal(readCount, 0)
  assert.equal(result.action_receipts.length, 0)
  assert.equal(result.admission_receipts.length, 0)
  assert.deepEqual(result.decision_protocol_rejections[0], {
    sequence: 1,
    reason_code: 'multiple_decisions_in_single_response',
    observed_proposal_count: 1,
    observed_disposition_count: 1,
  })
  assert.equal(result.usage.attempt_count, 2)
})

test('assistant 文本中的 Disposition JSON 不被猜测为决策', async () => {
  const networkCalls = { value: 0 }
  const textDisposition = {
    schema_version: 'domeye_agent_goal_disposition_v1',
    goal_id: goal.goal_id,
    goal_state_revision: initialState.state_revision,
    disposition: 'stopped',
    reason_code: 'text_only_decision',
  }
  const runtime = loop([
    () => ({ assistantText: JSON.stringify(textDisposition) }),
    (prompt) => ({
      dispositions: [goalDisposition(
        prompt,
        'stopped',
        'dedicated_tool_used',
      )],
    }),
  ], readModel, networkCalls)

  const result = await runtime.run(goal, initialState)
  assert.equal(result.disposition.reason_code, 'dedicated_tool_used')
  assert.deepEqual(result.decision_protocol_rejections[0], {
    sequence: 1,
    reason_code: 'decision_missing_or_invalid',
    observed_proposal_count: 0,
    observed_disposition_count: 0,
  })
  assert.equal(result.action_receipts.length, 0)
  assert.equal(result.usage.attempt_count, 2)
  assert.equal(networkCalls.value, 2)
})

test('专用工具过早声明 goal_satisfied 时失败关闭且不执行 Action', async () => {
  const runtime = loop([
    (prompt) => ({
      dispositions: [goalDisposition(
        prompt,
        'goal_satisfied',
        'claimed_without_artifacts',
      )],
    }),
    (prompt) => ({
      dispositions: [goalDisposition(
        prompt,
        'stopped',
        'premature_satisfaction_rejected',
      )],
    }),
  ], readModel)

  const result = await runtime.run(goal, initialState)
  assert.equal(result.goal_state.status, 'stopped')
  assert.equal(result.action_receipts.length, 0)
  assert.deepEqual(result.decision_protocol_rejections[0], {
    sequence: 1,
    reason_code: 'goal_disposition_not_yet_valid',
    observed_proposal_count: 0,
    observed_disposition_count: 1,
  })
  assert.equal(result.usage.attempt_count, 2)
})

test('Goal Disposition 专用工具输入使用严格机器合同并对非法参数失败关闭', async () => {
  const valid = {
    schema_version: 'domeye_agent_goal_disposition_v1',
    goal_id: goal.goal_id,
    goal_state_revision: 1,
    disposition: 'stopped',
    reason_code: 'strict_contract_passed',
  }
  assert.equal(Check(DomeyeGoalDispositionSchema, valid), true)
  assert.equal(Check(DomeyeGoalDispositionSchema, {
    ...valid,
    unexpected_field: true,
  }), false)
  assert.equal(Check(DomeyeGoalDispositionSchema, {
    ...valid,
    reason_code: '不是机器原因码',
  }), false)

  const runtime = loop([
    () => ({
      dispositions: [{ ...valid, unexpected_field: true }],
    }),
  ], readModel)
  await assert.rejects(
    () => runtime.run(goal, initialState),
    /goal_disposition_invalid/,
  )
})

test('供应方错误立即失败关闭，不伪装成决策协议重试', async () => {
  const networkCalls = { value: 0 }
  const runtime = loop([
    () => ({ providerError: 'provider_unavailable' }),
    () => ({ assistantText: '不得执行第二次调用' }),
  ], readModel, networkCalls)

  await assert.rejects(
    () => runtime.run(goal, initialState),
    /cognition_provider_failed/,
  )
  assert.equal(networkCalls.value, 1)
})

test('成功工具调用响应的 responseModel 漂移时不进入准入或领域执行', async () => {
  const networkCalls = { value: 0 }
  const readCalls = { value: 0 }
  const runtime = loop([
    (prompt) => ({
      proposals: [cap006(prompt)],
      responseModel: 'unbound-model',
    }),
  ], {
    async readMetricSeries() {
      readCalls.value += 1
      return readModel.readMetricSeries({
        data_identity: identity,
        metric: 'fixed_visible_ipv4_address_count',
      })
    },
  }, networkCalls)

  await assert.rejects(
    () => runtime.run(goal, initialState),
    /cognition_provider_failed/,
  )
  assert.equal(networkCalls.value, 1)
  assert.equal(readCalls.value, 0)
})
