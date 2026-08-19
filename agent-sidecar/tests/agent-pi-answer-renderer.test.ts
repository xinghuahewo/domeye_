import assert from 'node:assert/strict'
import test from 'node:test'

import type {
  CreateAgentSessionOptions,
  SessionStats,
} from '@earendil-works/pi-coding-agent'

import type {
  DomeyeAnswerContext,
  DomeyeRendererDraft,
} from '../src/agent/contracts.js'
import {
  composeCountryOutageAnswer,
  renderCountryOutageDeterministicFallback,
} from '../src/agent/finding-answer.js'
import { PiAnswerRenderer } from '../src/agent/pi-answer-renderer.js'
import {
  DomeyeTurnProviderAccounting,
  type DomeyePiSessionFactory,
  type DomeyePiSessionHandle,
} from '../src/agent/pi-runtime-boundary.js'

const identity = {
  event_type: 'country_outage' as const,
  incident_id: 'incident-go',
  publication_id: 'publication-go',
  revision: 1,
  collector_id: 'rrc25' as const,
  cohort_id: 'cohort-go',
  country_code: 'IR',
  window_start_utc: '2026-02-27T00:10:00Z',
  window_end_utc: '2026-03-11T00:00:00Z',
  data_through: '2026-03-11T00:00:00Z',
  is_final_in_data_range: false,
  lifecycle_state: 'event_end_unknown' as const,
}

const limitations = [
  'unique_ipv4_address 是固定前缀规范化、去重并合并重叠后的 IPv4 唯一地址并集，不是用户数、设备数或流量。',
  'RRC25 是单一观察点，不能代表全国或全球互联网。',
  '极值、下降或末值回升不能单独证明事件原因、责任、全国中断或真实恢复。',
  '窗口冻结仅表示评测输入不静默变化，不表示真实事件已经结束。',
]

const context: DomeyeAnswerContext = {
  schema_version: 'domeye_agent_answer_context_v1',
  context_id: 'answer-context-1',
  candidate_id: 'candidate-1',
  contract_version: 'domeye.first-vertical-slice/v1.0',
  contract_digest: `sha256:${'a'.repeat(64)}`,
  data_identity: identity,
  finding: {
    schema_version: 'domeye_agent_typed_finding_v1',
    finding_id: 'finding-1',
    finding_type: 'fixed_visible_ipv4_series_extrema',
    value_state: 'known',
    candidate_id: 'candidate-1',
    tenant_id: 'domeye',
    data_identity: identity,
    metric: 'fixed_visible_ipv4_address_count',
    unit: 'unique_ipv4_address',
    population_definition:
      'normalized_deduplicated_merged_fixed_prefix_ipv4_unique_address_union',
    values: {
      first: 10156800,
      first_at_utc: '2026-02-27T00:10:00Z',
      last: 10069760,
      last_at_utc: '2026-03-11T00:00:00Z',
      minimum: 9577728,
      minimum_at_utc: '2026-02-28T14:35:00Z',
      maximum: 10156800,
      maximum_at_utc: '2026-02-27T00:10:00Z',
      difference: 579072,
      net_change: -87040,
    },
    time_slot_count: 3455,
    observed_point_count: 3455,
    null_point_count: 0,
    completeness_state: 'complete',
    limitation_codes: [
      'fixed_population_semantics',
      'rrc25_observer_scope_only',
      'no_cause_responsibility_or_recovery',
      'window_not_event_closure',
    ],
    tool_version: '1.0.0',
    operator_version: '1.0.0',
    artifact_refs: ['artifact-series', 'artifact-extrema'],
    receipt_refs: ['receipt-series', 'receipt-extrema'],
    evidence_refs: ['domeye:evidence-series'],
    result_digest: `sha256:${'b'.repeat(64)}`,
  },
  observer_scope_zh: 'RRC25 单一观察点的 BGP 控制面观测',
  mandatory_limitations_zh: limitations,
  forbidden_conclusions: [
    'national_outage',
    'real_user_impact',
    'cause',
    'responsibility',
    'real_recovery',
  ],
  evidence_refs: ['domeye:evidence-series'],
  context_digest: `sha256:${'c'.repeat(64)}`,
}

function draft(): DomeyeRendererDraft {
  return {
    schema_version: 'domeye_agent_renderer_draft_v1',
    context_id: context.context_id,
    finding_id: context.finding.finding_id,
    candidate_id: context.candidate_id,
    publication_id: identity.publication_id,
    revision: identity.revision,
    collector_id: 'rrc25',
    window_start_utc: identity.window_start_utc,
    window_end_utc: identity.window_end_utc,
    metric: context.finding.metric,
    unit: context.finding.unit,
    values: context.finding.values,
    observer_scope_zh: context.observer_scope_zh,
    limitations_zh: limitations,
    evidence_refs: context.evidence_refs,
    text: renderCountryOutageDeterministicFallback(context),
  }
}

const model = {
  id: 'renderer-model',
  name: 'renderer-model',
  api: 'openai-completions',
  provider: 'deepseek',
  baseUrl: 'https://provider.invalid',
  reasoning: false,
  input: ['text'],
  contextWindow: 100_000,
  maxTokens: 4_096,
  cost: { input: 1, output: 1, cacheRead: 0, cacheWrite: 0 },
} as NonNullable<CreateAgentSessionOptions['model']>

function stats(): SessionStats {
  return {
    sessionFile: undefined,
    sessionId: 'renderer-test',
    userMessages: 1,
    assistantMessages: 1,
    toolCalls: 0,
    toolResults: 0,
    totalMessages: 2,
    tokens: { input: 10, output: 5, cacheRead: 0, cacheWrite: 0, total: 15 },
    cost: 0.01,
  }
}

function sessionFactory(
  output: string,
  networkCalls: { value: number },
  prompts: Record<string, unknown>[],
  forwardedPayloads: unknown[] = [],
  existingPayloadHook?: NonNullable<
    NonNullable<
      Parameters<DomeyePiSessionHandle['agent']['streamFunction']>[2]
    >['onPayload']
  >,
  rawPayload: unknown = {
    model: model.id,
    messages: [],
    stream: true,
  },
): DomeyePiSessionFactory {
  return async () => {
    let lastText: string | undefined
    const rawStream = (
      _streamModel: typeof model,
      _context: unknown,
      options?: {
        onPayload?: (
          payload: unknown,
          streamModel: typeof model,
        ) => unknown
      },
    ) => {
      const message = {
        role: 'assistant' as const,
        content: [],
        api: model.api,
        provider: model.provider,
        model: model.id,
        responseModel: model.id,
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
          const transformed = await options?.onPayload?.(rawPayload, model)
          forwardedPayloads.push(transformed ?? rawPayload)
          networkCalls.value += 1
          yield { type: 'done', reason: 'stop', message }
        },
        async result() { return message },
      }
    }
    const session: DomeyePiSessionHandle = {
      agent: {
        streamFunction: rawStream as unknown as DomeyePiSessionHandle['agent']['streamFunction'],
      },
      messages: [],
      async prompt(text) {
        prompts.push(JSON.parse(text) as Record<string, unknown>)
        const stream = await session.agent.streamFunction(
          model,
          { messages: [] },
          existingPayloadHook
            ? { onPayload: existingPayloadHook }
            : undefined,
        )
        for await (const _event of stream) {
          // 消费一次真实供应方尝试。
        }
        lastText = output
      },
      async abort() {},
      getSessionStats: stats,
      getLastAssistantText: () => lastText,
      dispose() {},
    }
    return { session }
  }
}

function renderer(
  output: string,
  accounting: DomeyeTurnProviderAccounting,
  networkCalls: { value: number },
  prompts: Record<string, unknown>[] = [],
  forwardedPayloads: unknown[] = [],
  existingPayloadHook?: NonNullable<
    NonNullable<
      Parameters<DomeyePiSessionHandle['agent']['streamFunction']>[2]
    >['onPayload']
  >,
  rawPayload?: unknown,
): PiAnswerRenderer {
  return new PiAnswerRenderer({
    model_binding: {
      identity: {
        provider: model.provider,
        model: model.id,
        model_version: model.id,
        expected_response_model: model.id,
      },
      model,
      model_runtime: {} as NonNullable<CreateAgentSessionOptions['modelRuntime']>,
      thinking_level: 'off',
    },
    accounting,
    session_factory: sessionFactory(
      output,
      networkCalls,
      prompts,
      forwardedPayloads,
      existingPayloadHook,
      rawPayload,
    ),
  })
}

test('Pi Renderer 只读取 Answer Context 受控投影且只产生一次供应方尝试', async () => {
  const accounting = new DomeyeTurnProviderAccounting()
  const networkCalls = { value: 0 }
  const prompts: Record<string, unknown>[] = []
  const result = await composeCountryOutageAnswer(
    context,
    renderer(JSON.stringify(draft()), accounting, networkCalls, prompts),
  )
  assert.equal(result.source, 'renderer')
  assert.equal(result.guard_result.decision, 'pass')
  assert.equal(networkCalls.value, 1)
  assert.equal(accounting.audit().attempt_count, 1)
  assert.equal(accounting.audit().estimated_cost_usd, 0.01)
  assert.equal(prompts.length, 1)
  assert.equal('answer_context' in prompts[0]!, false)
  assert.equal('instruction' in prompts[0]!, false)
  assert.equal('renderer_draft_skeleton' in prompts[0]!, false)
  assert.deepEqual(prompts[0], draft())
})

test('Renderer 将 DeepSeek JSON object 约束传到底层且保留既有 payload hook', async () => {
  const accounting = new DomeyeTurnProviderAccounting()
  const networkCalls = { value: 0 }
  const forwardedPayloads: unknown[] = []
  let existingHookCalls = 0
  const result = await composeCountryOutageAnswer(
    context,
    renderer(
      JSON.stringify(draft()),
      accounting,
      networkCalls,
      [],
      forwardedPayloads,
      async (payload) => {
        existingHookCalls += 1
        assert.ok(
          payload !== null
          && typeof payload === 'object'
          && !Array.isArray(payload),
        )
        return {
          ...(payload as Record<string, unknown>),
          existing_hook_preserved: true,
          tool_choice: 'auto',
          temperature: 1,
          response_format: { type: 'legacy-value' },
        }
      },
    ),
  )

  assert.equal(result.source, 'renderer')
  assert.equal(existingHookCalls, 1)
  assert.equal(networkCalls.value, 1)
  assert.equal(forwardedPayloads.length, 1)
  assert.deepEqual(forwardedPayloads[0], {
    model: model.id,
    messages: [],
    stream: true,
    existing_hook_preserved: true,
    tool_choice: 'none',
    temperature: 0,
    response_format: { type: 'json_object' },
  })
})

test('Renderer 对非普通 payload 或非普通既有 hook 结果失败关闭', async () => {
  const cases = [
    {
      name: '非普通输入',
      rawPayload: new Date('2026-08-19T00:00:00Z'),
      existingPayloadHook: undefined,
    },
    {
      name: '非普通既有 hook 结果',
      rawPayload: {
        model: model.id,
        messages: [],
        stream: true,
      },
      existingPayloadHook: async () => new Date('2026-08-19T00:00:00Z'),
    },
  ] as const

  for (const item of cases) {
    const accounting = new DomeyeTurnProviderAccounting()
    const networkCalls = { value: 0 }
    const forwardedPayloads: unknown[] = []
    const result = await composeCountryOutageAnswer(
      context,
      renderer(
        JSON.stringify(draft()),
        accounting,
        networkCalls,
        [],
        forwardedPayloads,
        item.existingPayloadHook,
        item.rawPayload,
      ),
    )

    assert.equal(result.source, 'deterministic_fallback', item.name)
    assert.equal(result.guard_result.decision, 'block', item.name)
    assert.deepEqual(
      result.guard_result.reason_codes,
      ['renderer_failed_or_invalid'],
      item.name,
    )
    assert.equal(networkCalls.value, 0, item.name)
    assert.equal(forwardedPayloads.length, 0, item.name)
    assert.equal(accounting.audit().attempt_count, 1, item.name)
    assert.equal(accounting.audit().attempts[0]?.outcome, 'failed', item.name)
  }
})

test('Renderer 非法输出不重试模型，直接使用同 Context 确定性回退', async () => {
  const accounting = new DomeyeTurnProviderAccounting()
  const networkCalls = { value: 0 }
  const result = await composeCountryOutageAnswer(
    context,
    renderer('not-json', accounting, networkCalls),
  )
  assert.equal(result.source, 'deterministic_fallback')
  assert.equal(result.answer, renderCountryOutageDeterministicFallback(context))
  assert.equal(result.guard_result.decision, 'block')
  assert.deepEqual(result.guard_result.reason_codes, ['renderer_failed_or_invalid'])
  assert.equal(networkCalls.value, 1)
  assert.equal(accounting.audit().attempt_count, 1)
})

test('Renderer 继续拒绝 fenced、非 JSON、Schema 非法与 Guard 不通过输出', async () => {
  const invalidSchema = {
    ...draft(),
    unexpected_field: true,
  }
  const guardRejected = {
    ...draft(),
    text: `${draft().text}\n事件已经恢复。`,
  }
  const wrappedDraft = {
    renderer_draft_skeleton: draft(),
  }
  const cases = [
    { name: 'fenced', output: `\`\`\`json\n${JSON.stringify(draft())}\n\`\`\`` },
    { name: '空文本', output: '' },
    { name: '非 JSON', output: 'not-json' },
    { name: '外层 wrapper', output: JSON.stringify(wrappedDraft) },
    { name: 'Schema 非法', output: JSON.stringify(invalidSchema) },
    { name: 'Guard 不通过', output: JSON.stringify(guardRejected) },
  ]

  for (const item of cases) {
    const accounting = new DomeyeTurnProviderAccounting()
    const networkCalls = { value: 0 }
    const result = await composeCountryOutageAnswer(
      context,
      renderer(item.output, accounting, networkCalls),
    )

    assert.equal(result.source, 'deterministic_fallback', item.name)
    assert.equal(result.guard_result.decision, 'block', item.name)
    assert.equal(networkCalls.value, 1, item.name)
    assert.equal(accounting.audit().attempt_count, 1, item.name)
    if (item.name === 'Guard 不通过') {
      assert.equal(result.render_attempt.status, 'completed')
      assert.ok(
        result.guard_result.reason_codes.includes(
          'forbidden_recovery_claim',
        ),
      )
    } else {
      assert.deepEqual(
        result.guard_result.reason_codes,
        ['renderer_failed_or_invalid'],
        item.name,
      )
      assert.equal(result.render_attempt.status, 'failed', item.name)
    }
  }
})

test('Renderer 未消费完供应方流时关闭未决账本并使用确定性回退', async () => {
  const accounting = new DomeyeTurnProviderAccounting()
  const networkCalls = { value: 0 }
  const incompleteFactory: DomeyePiSessionFactory = async () => {
    let lastText: string | undefined
    const rawStream = () => {
      networkCalls.value += 1
      const message = {
        role: 'assistant' as const,
        content: [],
        api: model.api,
        provider: model.provider,
        model: model.id,
        responseModel: model.id,
        usage: {
          input: 0,
          output: 0,
          cacheRead: 0,
          cacheWrite: 0,
          totalTokens: 0,
          cost: {
            input: 0,
            output: 0,
            cacheRead: 0,
            cacheWrite: 0,
            total: 0,
          },
        },
        stopReason: 'stop' as const,
        timestamp: 0,
      }
      return {
        async *[Symbol.asyncIterator]() {
          yield { type: 'done', reason: 'stop', message }
        },
        async result() { return message },
      }
    }
    const session: DomeyePiSessionHandle = {
      agent: {
        streamFunction: rawStream as unknown as
          DomeyePiSessionHandle['agent']['streamFunction'],
      },
      messages: [],
      async prompt() {
        await session.agent.streamFunction(model, { messages: [] })
        lastText = JSON.stringify(draft())
      },
      async abort() {},
      getSessionStats: stats,
      getLastAssistantText: () => lastText,
      dispose() {},
    }
    return { session }
  }
  const rendererWithIncompleteAttempt = new PiAnswerRenderer({
    model_binding: {
      identity: {
        provider: model.provider,
        model: model.id,
        model_version: model.id,
        expected_response_model: model.id,
      },
      model,
      model_runtime: {} as NonNullable<
        CreateAgentSessionOptions['modelRuntime']
      >,
      thinking_level: 'off',
    },
    accounting,
    session_factory: incompleteFactory,
  })

  const result = await composeCountryOutageAnswer(
    context,
    rendererWithIncompleteAttempt,
  )

  assert.equal(result.source, 'deterministic_fallback')
  assert.equal(result.answer, renderCountryOutageDeterministicFallback(context))
  assert.equal(networkCalls.value, 1)
  assert.equal(accounting.audit().attempt_count, 1)
  assert.equal(accounting.audit().attempts[0]?.outcome, 'failed')
  assert.equal(
    accounting.audit().attempts[0]?.failure_code,
    'renderer_attempt_unclosed',
  )
  assert.equal(accounting.audit().attempts[0]?.ended_at_utc !== null, true)
})
