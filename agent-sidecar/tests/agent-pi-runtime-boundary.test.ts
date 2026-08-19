import assert from 'node:assert/strict'
import test from 'node:test'

import type {
  CreateAgentSessionOptions,
  SessionStats,
} from '@earendil-works/pi-coding-agent'

import {
  DomeyeProviderAttemptBudget,
  installDomeyeProviderAttemptBoundary,
  providerUsageAudit,
  type DomeyePiSessionHandle,
} from '../src/agent/pi-runtime-boundary.js'

const model = {
  id: 'model-for-first-slice',
  name: 'model-for-first-slice',
  api: 'openai-completions',
  provider: 'provider-for-first-slice',
  baseUrl: 'https://provider.invalid',
  reasoning: false,
  input: ['text'],
  contextWindow: 100_000,
  maxTokens: 4_096,
  cost: { input: 1, output: 1, cacheRead: 0, cacheWrite: 0 },
} as NonNullable<CreateAgentSessionOptions['model']>

const providerIdentity = {
  provider: model.provider,
  model: model.id,
  model_version: model.id,
  expected_response_model: model.id,
}

function assistantMessage(responseModel: string | null = model.id) {
  return {
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
}

function stats(cost: number): SessionStats {
  return {
    sessionFile: undefined,
    sessionId: 'first-slice-session',
    userMessages: 1,
    assistantMessages: 1,
    toolCalls: 0,
    toolResults: 0,
    totalMessages: 2,
    tokens: {
      input: 10,
      output: 5,
      cacheRead: 2,
      cacheWrite: 1,
      total: 18,
    },
    cost,
  }
}

function session(networkCalls: { value: number }): DomeyePiSessionHandle {
  const streamFunction = () => {
    networkCalls.value += 1
    const message = assistantMessage()
    return {
      async *[Symbol.asyncIterator]() {
        yield { type: 'done', reason: 'stop', message }
      },
      async result() {
        return message
      },
    }
  }
  return {
    agent: {
      streamFunction: streamFunction as unknown as DomeyePiSessionHandle['agent']['streamFunction'],
    },
    messages: [],
    async prompt() {},
    async abort() {},
    getSessionStats: () => stats(0.01),
    dispose() {},
  }
}

async function consume(sessionHandle: DomeyePiSessionHandle): Promise<void> {
  const stream = await sessionHandle.agent.streamFunction(
    model,
    { messages: [] },
  )
  for await (const _event of stream) {
    // 消费供应方流，触发一次尝试完成记录。
  }
}

test('PiLoop 与 Renderer 共用每轮十次真实供应方尝试上限', async () => {
  const networkCalls = { value: 0 }
  const budget = new DomeyeProviderAttemptBudget()
  const cognition = session(networkCalls)
  const renderer = session(networkCalls)
  installDomeyeProviderAttemptBoundary(
    cognition,
    budget,
    'cognition',
    providerIdentity,
  )
  installDomeyeProviderAttemptBoundary(
    renderer,
    budget,
    'renderer',
    providerIdentity,
  )

  for (let index = 0; index < 7; index += 1) await consume(cognition)
  for (let index = 0; index < 3; index += 1) await consume(renderer)
  await consume(renderer)

  assert.equal(networkCalls.value, 10)
  assert.equal(budget.used, 10)
  assert.equal(budget.remaining, 0)
  assert.equal(
    budget.snapshot().filter((item) => item.outcome === 'completed')
      .every((item) => item.response_model === model.id),
    true,
  )
  assert.equal(
    budget.snapshot().filter((item) => item.outcome === 'limit_rejected').length,
    1,
  )
})

test('费用不参与拒绝，但 token、费用、延迟与失败分类进入审核', () => {
  const budget = new DomeyeProviderAttemptBudget()
  const identity = {
    provider: 'provider',
    model: 'model',
    model_version: 'model',
    expected_response_model: 'model',
  }
  const completed = budget.begin('cognition', identity)
  const failed = budget.begin('renderer', identity)
  assert.ok(completed)
  assert.ok(failed)
  budget.complete(completed, 'model')
  budget.fail(failed, new Error('renderer_provider_failed'))

  const audit = providerUsageAudit(budget, [stats(12.5), stats(8.25)])
  assert.equal(audit.cost_policy, 'audit_only')
  assert.equal(audit.estimated_cost_usd, 20.75)
  assert.deepEqual(audit.tokens, {
    input: 20,
    output: 10,
    cache_read: 4,
    cache_write: 2,
    total: 36,
  })
  assert.equal(audit.attempt_count, 2)
  assert.equal(audit.attempts[1]?.failure_code, 'renderer_provider_failed')
  assert.equal(audit.attempts.every((item) => item.latency_ms !== null), true)
})

test('供应方错误流被记为失败尝试而不是成功完成', async () => {
  const budget = new DomeyeProviderAttemptBudget()
  const handle = session({ value: 0 })
  handle.agent.streamFunction = (async () => ({
    async *[Symbol.asyncIterator]() {
      yield {
        type: 'error',
        error: {
          stopReason: 'error',
          errorMessage: 'provider_request_failed',
        },
      }
    },
    async result() {
      return {
        stopReason: 'error',
        errorMessage: 'provider_request_failed',
      }
    },
  })) as unknown as DomeyePiSessionHandle['agent']['streamFunction']
  installDomeyeProviderAttemptBoundary(
    handle,
    budget,
    'cognition',
    providerIdentity,
  )

  await consume(handle)

  assert.equal(budget.used, 1)
  assert.equal(budget.snapshot()[0]?.outcome, 'failed')
  assert.equal(
    budget.snapshot()[0]?.failure_code,
    'provider_request_failed',
  )
})

test('请求模型漂移在网络调用前失败关闭且留下共享账本记录', async () => {
  const networkCalls = { value: 0 }
  const budget = new DomeyeProviderAttemptBudget()
  const handle = session(networkCalls)
  installDomeyeProviderAttemptBoundary(
    handle,
    budget,
    'renderer',
    providerIdentity,
  )
  const wrongModel = {
    ...model,
    id: 'different-request-model',
  } as NonNullable<CreateAgentSessionOptions['model']>

  await assert.rejects(
    async () => {
      await handle.agent.streamFunction(wrongModel, { messages: [] })
    },
    /provider_request_model_mismatch/,
  )

  assert.equal(networkCalls.value, 0)
  assert.equal(budget.used, 1)
  assert.deepEqual(budget.snapshot().map((attempt) => ({
    phase: attempt.phase,
    outcome: attempt.outcome,
    failure_code: attempt.failure_code,
    response_model: attempt.response_model,
  })), [{
    phase: 'renderer',
    outcome: 'failed',
    failure_code: 'provider_request_model_mismatch',
    response_model: null,
  }])
})

for (const scenario of [
  { name: '缺少 responseModel', responseModel: null },
  { name: 'responseModel 漂移', responseModel: 'different-model' },
] as const) {
  test(`${scenario.name}时成功响应在 Pi 边界失败关闭`, async () => {
    const budget = new DomeyeProviderAttemptBudget()
    const handle = session({ value: 0 })
    const message = assistantMessage(scenario.responseModel)
    handle.agent.streamFunction = (async () => ({
      async *[Symbol.asyncIterator]() {
        yield { type: 'done', reason: 'stop', message }
      },
      async result() { return message },
    })) as unknown as DomeyePiSessionHandle['agent']['streamFunction']
    installDomeyeProviderAttemptBoundary(
      handle,
      budget,
      'cognition',
      providerIdentity,
    )

    const stream = await handle.agent.streamFunction(model, { messages: [] })
    const events: unknown[] = []
    for await (const event of stream) events.push(event)

    assert.equal(budget.snapshot()[0]?.outcome, 'failed')
    assert.equal(
      budget.snapshot()[0]?.failure_code,
      'provider_response_identity_mismatch',
    )
    assert.equal(
      budget.snapshot()[0]?.response_model,
      scenario.responseModel,
    )
    assert.equal(
      (events[0] as { type?: string }).type,
      'error',
    )
  })
}

test('错误终态后伪造成功 done 仍锁存失败且不下传成功终态', async () => {
  const budget = new DomeyeProviderAttemptBudget()
  const handle = session({ value: 0 })
  const success = assistantMessage()
  handle.agent.streamFunction = (async () => ({
    async *[Symbol.asyncIterator]() {
      yield {
        type: 'error',
        reason: 'error',
        error: {
          ...success,
          stopReason: 'error',
          errorMessage: 'provider_request_failed',
        },
      }
      yield { type: 'done', reason: 'stop', message: success }
    },
    async result() { return success },
  })) as unknown as DomeyePiSessionHandle['agent']['streamFunction']
  installDomeyeProviderAttemptBoundary(
    handle,
    budget,
    'cognition',
    providerIdentity,
  )

  const stream = await handle.agent.streamFunction(model, { messages: [] })
  const eventTypes: string[] = []
  for await (const event of stream) {
    eventTypes.push((event as { type: string }).type)
  }

  assert.deepEqual(eventTypes, ['error'])
  assert.equal(budget.snapshot()[0]?.outcome, 'failed')
  assert.equal(
    budget.snapshot()[0]?.failure_code,
    'provider_request_failed',
  )
  assert.equal(budget.snapshot()[0]?.response_model, model.id)
})
