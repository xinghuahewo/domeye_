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
  composeCountryOutageRendererDraftText,
  renderCountryOutageDeterministicFallback,
} from '../src/agent/finding-answer.js'
import { PiAnswerRenderer } from '../src/agent/pi-answer-renderer.js'
import {
  DomeyeTurnProviderAccounting,
  type DomeyePiSessionFactory,
  type DomeyePiSessionHandle,
} from '../src/agent/pi-runtime-boundary.js'

const context: DomeyeAnswerContext = {
  schema_version: 'domeye_agent_answer_context_v2',
  question_zh:
    '在这次冻结 publication 的观测窗口内，RRC25 看到的固定前缀可见 IPv4 地址量最低是多少，首次在什么观测时刻出现？首值、末值、最大值和极差分别是多少？',
  metric_zh: '固定前缀可见 IPv4 地址量',
  unit_zh: '个唯一 IPv4 地址',
  facts: {
    minimum: { value: 9577728, display_zh: '9,577,728' },
    minimum_at_utc: {
      value: '2026-02-28T14:35:00Z',
      display_zh: '2026 年 2 月 28 日 14:35 UTC',
    },
    first: { value: 10156800, display_zh: '10,156,800' },
    last: { value: 10069760, display_zh: '10,069,760' },
    maximum: { value: 10156800, display_zh: '10,156,800' },
    difference: { value: 579072, display_zh: '579,072' },
  },
  required_boundaries: [
    {
      code: 'fixed_prefix_population_not_users',
      meaning_zh: '地址量是固定前缀 IPv4 唯一地址并集，不是用户数。',
    },
    {
      code: 'rrc25_control_plane_observation_only',
      meaning_zh: '结果只表示 RRC25 的 BGP 控制面观测。',
    },
    {
      code: 'no_national_or_user_impact_cause_responsibility_recovery',
      meaning_zh: '不能据此判断全国状态、用户影响、原因、责任或恢复。',
    },
  ],
  forbidden_conclusions: [
    'national_outage',
    'real_user_impact',
    'cause',
    'responsibility',
    'real_recovery',
  ],
  style_constraints: {
    max_lead_graphemes: 90,
    max_fact_blocks: 3,
    required_boundary_blocks: 1,
    max_total_graphemes: 360,
    max_sentences: 6,
  },
}

function draft(): DomeyeRendererDraft {
  return {
    schema_version: 'domeye_agent_renderer_draft_v2',
    lead: {
      fact_keys: ['minimum', 'minimum_at_utc'],
      text: `最低值为 ${context.facts.minimum.display_zh} ${context.unit_zh}，首次观测于 ${context.facts.minimum_at_utc.display_zh}。`,
    },
    fact_blocks: [
      {
        fact_keys: ['first', 'last'],
        text: `首值为 ${context.facts.first.display_zh}，末值为 ${context.facts.last.display_zh}。`,
      },
      {
        fact_keys: ['maximum', 'difference'],
        text: `最大值为 ${context.facts.maximum.display_zh}，极差为 ${context.facts.difference.display_zh}。`,
      },
    ],
    boundary: {
      boundary_codes: context.required_boundaries.map((item) => item.code),
      text: '地址量是固定前缀 IPv4 唯一地址并集，不是用户数；结果只表示 RRC25 的 BGP 控制面观测，不能据此判断全国状态、用户影响、原因、责任或恢复。',
    },
    next_step: null,
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
  systemPrompts: string[] = [],
): DomeyePiSessionFactory {
  return async (options) => {
    systemPrompts.push(options.resourceLoader?.getSystemPrompt() ?? '')
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
  systemPrompts: string[] = [],
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
      systemPrompts,
    ),
  })
}

test('Pi Renderer 只读取 Answer Context 受控投影且只产生一次供应方尝试', async () => {
  const accounting = new DomeyeTurnProviderAccounting()
  const networkCalls = { value: 0 }
  const prompts: Record<string, unknown>[] = []
  const systemPrompts: string[] = []
  const result = await composeCountryOutageAnswer(
    context,
    renderer(
      JSON.stringify(draft()),
      accounting,
      networkCalls,
      prompts,
      [],
      undefined,
      undefined,
      systemPrompts,
    ),
  )
  assert.equal(result.source, 'renderer')
  assert.equal(result.guard_result.decision, 'pass')
  assert.equal(result.answer, composeCountryOutageRendererDraftText(draft()))
  assert.equal(networkCalls.value, 1)
  assert.equal(accounting.audit().attempt_count, 1)
  assert.equal(accounting.audit().estimated_cost_usd, 0.01)
  assert.equal(prompts.length, 1)
  assert.equal(systemPrompts.length, 1)
  assert.match(
    systemPrompts[0]!,
    /unit_zh 只能在 lead\.text 中紧跟 minimum\.display_zh 原样出现一次/,
  )
  assert.match(
    systemPrompts[0]!,
    /边界中的“唯一地址并集”不能代替 unit_zh/,
  )
  assert.equal('answer_context' in prompts[0]!, false)
  assert.equal('instruction' in prompts[0]!, false)
  assert.equal('renderer_draft_skeleton' in prompts[0]!, false)
  assert.deepEqual(prompts[0], context)
  assert.doesNotMatch(
    JSON.stringify(prompts[0]),
    /candidate|finding_id|context_id|digest|sha256|receipt|artifact|evidence|usage/iu,
  )
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
  assert.equal(result.guard_result.assessment_status, 'not_evaluated')
  assert.equal(result.guard_result.style_assessment, null)
  assert.deepEqual(result.guard_result.reason_codes, ['renderer_failed_or_invalid'])
  assert.equal(networkCalls.value, 1)
  assert.equal(accounting.audit().attempt_count, 1)
})

test('Renderer 继续拒绝 fenced、非 JSON、Schema 非法与 Guard 不通过输出', async () => {
  const invalidSchema = {
    ...draft(),
    unexpected_field: true,
  }
  const acceptedDraft = draft()
  const guardRejected = {
    ...acceptedDraft,
    fact_blocks: [{
      ...acceptedDraft.fact_blocks[0]!,
      text: `${acceptedDraft.fact_blocks[0]!.text} 事件已经恢复。`,
    }, acceptedDraft.fact_blocks[1]!],
  }
  const unitMissing = {
    ...acceptedDraft,
    lead: {
      ...acceptedDraft.lead,
      text: acceptedDraft.lead.text.replace(` ${context.unit_zh}`, ''),
    },
  }
  const unitDuplicated = {
    ...acceptedDraft,
    fact_blocks: [
      acceptedDraft.fact_blocks[0]!,
      {
        ...acceptedDraft.fact_blocks[1]!,
        text: acceptedDraft.fact_blocks[1]!.text.replace(
          `${context.facts.difference.display_zh}`,
          `${context.facts.difference.display_zh} ${context.unit_zh}`,
        ),
      },
    ],
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
    {
      name: 'Guard 拒绝恢复结论',
      output: JSON.stringify(guardRejected),
      guard_reason: 'forbidden_recovery_claim',
    },
    {
      name: 'Guard 拒绝单位缺失',
      output: JSON.stringify(unitMissing),
      guard_reason: 'unit_missing_or_duplicate',
    },
    {
      name: 'Guard 拒绝单位重复',
      output: JSON.stringify(unitDuplicated),
      guard_reason: 'unit_missing_or_duplicate',
    },
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
    if ('guard_reason' in item) {
      assert.equal(result.render_attempt.status, 'completed')
      assert.ok(
        result.guard_result.reason_codes.includes(
          item.guard_reason,
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
