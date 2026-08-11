import assert from 'node:assert/strict'
import test from 'node:test'

import type {
  CreateAgentSessionOptions,
  SessionStats,
} from '@earendil-works/pi-coding-agent'

import {
  P1PiSemanticModel,
  type P1PiSemanticModelAuditRecord,
} from '../src/chat/pi-semantic-model.js'
import type {
  PiModelRunSelection,
} from '../src/pi/formal-model-runtime.js'
import type {
  PiSessionFactory,
} from '../src/pi/pi-report-narrator.js'

const model = {
  id: 'deepseek-v4-flash',
  name: 'DeepSeek V4 Flash',
  api: 'openai-completions',
  provider: 'deepseek',
  baseUrl: 'https://api.deepseek.com',
  reasoning: true,
  input: ['text'],
  contextWindow: 1_000_000,
  maxTokens: 16_384,
  cost: { input: 0.14, output: 0.28, cacheRead: 0.0028, cacheWrite: 0 },
} as NonNullable<CreateAgentSessionOptions['model']>

const selection: PiModelRunSelection = {
  runtimeIdentity: 'candidate',
  candidateId: 'deepseek-v4-flash-pi-0.84.1-v1',
  candidateResourceSha256: 'a'.repeat(64),
  profile: {
    id: 'deepseek-v4-flash-pi-0.84.1-v1',
    status: 'candidate',
    provider: 'deepseek',
    model: 'deepseek-v4-flash',
    modelVersion: 'deepseek-v4-flash',
    expectedResponseModel: 'deepseek-v4-flash',
    thinkingLevel: 'off',
    piVersion: '0.84.1',
  },
}

function stats(): SessionStats {
  return {
    sessionFile: undefined,
    sessionId: 'p1-semantic-test',
    userMessages: 1,
    assistantMessages: 1,
    toolCalls: 0,
    toolResults: 0,
    totalMessages: 2,
    tokens: {
      input: 300,
      output: 100,
      cacheRead: 0,
      cacheWrite: 0,
      total: 400,
    },
    cost: 0.00007,
  }
}

function sessionFactory(
  output: string,
  counters: { network: number, disposed: number },
): PiSessionFactory {
  return async () => {
    const messages: unknown[] = []
    const agent = {
      streamFunction(streamModel: typeof model, _context: unknown, options?: {
        onPayload?: (payload: unknown, model: typeof streamModel) => unknown
      }) {
        return {
          async *[Symbol.asyncIterator]() {
            await options?.onPayload?.(
              { model: streamModel.id, messages: [], stream: true },
              streamModel,
            )
            counters.network += 1
          },
          async result() {
            return { stopReason: 'stop' }
          },
        }
      },
    }
    return {
      session: {
        agent: agent as unknown as Awaited<ReturnType<PiSessionFactory>>['session']['agent'],
        messages,
        async prompt() {
          const stream = await agent.streamFunction(
            model,
            { messages: [] },
          )
          for await (const _event of stream) {
            // 模拟真实 agent loop 消费 provider stream。
          }
          messages.push({
            role: 'assistant',
            provider: 'deepseek',
            model: 'deepseek-v4-flash',
            responseModel: 'deepseek-v4-flash',
            stopReason: 'stop',
            usage: {
              input: 300,
              output: 100,
              cacheRead: 0,
              cacheWrite: 0,
              totalTokens: 400,
              cost: {
                input: 0.000042,
                output: 0.000028,
                cacheRead: 0,
                cacheWrite: 0,
                total: 0.00007,
              },
            },
            content: [{ type: 'text', text: output }],
          })
        },
        async abort() {},
        getSessionStats: stats,
        dispose() {
          counters.disposed += 1
        },
      },
    }
  }
}

test('P1 Pi 语义模型每轮仅一次无工具请求并记录 token 与费用', async () => {
  const audits: P1PiSemanticModelAuditRecord[] = []
  const counters = { network: 0, disposed: 0 }
  const output = '{"plan_revision":"user-goal-plan-v2"}'
  const semantic = new P1PiSemanticModel({
    binding: {
      model,
      modelRuntime: {} as NonNullable<CreateAgentSessionOptions['modelRuntime']>,
      runSelection: selection,
    },
    allowCandidate: true,
    sessionFactory: sessionFactory(output, counters),
    auditSink(record) {
      audits.push(record)
    },
  })

  assert.equal(await semantic.complete('只输出 JSON'), output)
  assert.equal(counters.network, 1)
  assert.equal(counters.disposed, 1)
  assert.equal(audits.length, 1)
  assert.equal(audits[0]?.outcome, 'completed')
  assert.equal(
    audits[0]?.runtimeSecurity.forwardedProviderRequestCount,
    1,
  )
  assert.equal(audits[0]?.runtimeSecurity.toolExecutionCount, 0)
  assert.equal(
    audits[0]?.runtimeSecurity.unauthorizedToolAttemptCount,
    0,
  )
  assert.equal(
    audits[0]?.runtimeSecurity.structuredOutputPayloadPreparedCount,
    1,
  )
  assert.equal(audits[0]?.usage?.tokens.total, 400)
  assert.equal(audits[0]?.billing.estimatedCost, 0.00007)
  assert.equal(audits[0]?.billing.source, 'pi_session_stats')
})

test('P1 Pi 语义模型拒绝并发复用且不产生第二次供应商请求', async () => {
  let release: (() => void) | undefined
  const wait = new Promise<void>((resolve) => { release = resolve })
  let calls = 0
  const semantic = new P1PiSemanticModel({
    binding: {
      model,
      modelRuntime: {} as NonNullable<CreateAgentSessionOptions['modelRuntime']>,
      runSelection: selection,
    },
    allowCandidate: true,
    auditSink() {},
    sessionFactory: async () => ({
      session: {
        agent: { streamFunction() { throw new Error('unused') } },
        messages: [],
        async prompt() { calls += 1; await wait },
        async abort() {},
        getSessionStats: stats,
        dispose() {},
      },
    }),
  })
  const first = semantic.complete('第一个请求')
  await new Promise((resolve) => setImmediate(resolve))
  await assert.rejects(
    semantic.complete('第二个请求'),
    /p1_pi_semantic_busy/,
  )
  release?.()
  await assert.rejects(first)
  assert.equal(calls, 1)
})
