import assert from 'node:assert/strict'
import { createServer } from 'node:http'
import type { AddressInfo } from 'node:net'
import { test } from 'node:test'

import type { CountryOutageAgentOrchestrator } from '../src/application/index.js'
import type { P1RuntimeV2ConversationService } from '../src/chat/index.js'
import { createCountryOutageAgentHttpHandler } from '../src/server/index.js'

const reference = 'country_outage/2026-02-27 09:12:32/IR/1/r'
const publication = 'country_outage_publication_v1_test'

async function withChatServer(
  run: (baseUrl: string, calls: Array<Record<string, unknown>>) => Promise<void>,
): Promise<void> {
  const calls: Array<Record<string, unknown>> = []
  const conversation = {
    schema_version: 'country_outage_p1_runtime_v2_conversation_v2',
    conversation_id: 'p1v2_http_test',
    binding: { publication_id: publication },
    turns: [],
  }
  const chatService = {
    async createConversation(principal: unknown, body: unknown) {
      calls.push({ operation: 'create', principal, body })
      return { conversation, deduplicated: false }
    },
    async getConversation(principal: unknown, conversationId: string) {
      calls.push({ operation: 'get', principal, conversationId })
      return conversation
    },
    async createTurn(principal: unknown, conversationId: string, body: unknown) {
      calls.push({ operation: 'turn', principal, conversationId, body })
      return {
        turn: {
          turn_id: 'p1v2turn_http_test',
          state: 'completed',
          question: '地址变化情况',
        },
        deduplicated: false,
      }
    },
    async rebind(principal: unknown, conversationId: string, body: unknown) {
      calls.push({ operation: 'rebind', principal, conversationId, body })
      return { conversation, previous_binding: { publication_id: 'old' } }
    },
    async cancelTurn(principal: unknown, conversationId: string, turnId: string) {
      calls.push({ operation: 'cancel', principal, conversationId, turnId })
      return { turn_id: turnId, state: 'cancel_requested' }
    },
  } as unknown as P1RuntimeV2ConversationService
  const server = createServer(createCountryOutageAgentHttpHandler({
    application: {} as CountryOutageAgentOrchestrator,
    chatService,
    authenticate: (request) => request.headers.authorization === 'Bearer allowed'
      ? { userId: 'chat-user', authorizationScope: 'country_outage_event_read:IR' }
      : null,
  }))
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve))
  const address = server.address() as AddressInfo
  try {
    await run(`http://127.0.0.1:${address.port}`, calls)
  } finally {
    await new Promise<void>((resolve, reject) =>
      server.close((error) => error ? reject(error) : resolve())
    )
  }
}

function headers(key: string): Record<string, string> {
  return {
    Authorization: 'Bearer allowed',
    'Content-Type': 'application/json',
    'Idempotency-Key': key,
  }
}

test('P1 聊天 HTTP 窄入口传递身份、幂等键与只读会话操作', async () => {
  await withChatServer(async (baseUrl, calls) => {
    const created = await fetch(`${baseUrl}/country-outage/chat/conversations`, {
      method: 'POST',
      headers: headers('chat-create-0001'),
      body: JSON.stringify({
        event_reference: reference,
        publication_id: publication,
        revision: 1,
        idempotency_key: 'chat-create-0001',
      }),
    })
    assert.equal(created.status, 201)
    assert.equal((await created.json() as any).conversation.conversation_id, 'p1v2_http_test')

    const turn = await fetch(
      `${baseUrl}/country-outage/chat/conversations/p1v2_http_test/turns`,
      {
        method: 'POST',
        headers: headers('chat-turn-00001'),
        body: JSON.stringify({
          question: '地址变化情况',
          idempotency_key: 'chat-turn-00001',
        }),
      },
    )
    assert.equal(turn.status, 201)
    assert.equal((await turn.json() as any).turn.state, 'completed')

    const snapshot = await fetch(
      `${baseUrl}/country-outage/chat/conversations/p1v2_http_test`,
      { headers: { Authorization: 'Bearer allowed' } },
    )
    assert.equal(snapshot.status, 200)

    const cancelled = await fetch(
      `${baseUrl}/country-outage/chat/conversations/p1v2_http_test/turns/p1v2turn_http_test/cancel`,
      {
        method: 'POST',
        headers: { ...headers('unused-cancel-key'), 'Idempotency-Key': '' },
        body: '{}',
      },
    )
    assert.equal(cancelled.status, 200)

    assert.deepEqual(
      calls.map((item) => item.operation),
      ['create', 'turn', 'get', 'cancel'],
    )
    assert.deepEqual((calls[1]!.body as any), {
      question: '地址变化情况',
      idempotency_key: 'chat-turn-00001',
    })
  })
})

test('P1 聊天 HTTP 入口在鉴权和请求字段阶段失败关闭', async () => {
  await withChatServer(async (baseUrl, calls) => {
    const unauthorized = await fetch(
      `${baseUrl}/country-outage/chat/conversations`,
      { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' },
    )
    assert.equal(unauthorized.status, 401)

    const invalid = await fetch(
      `${baseUrl}/country-outage/chat/conversations/p1v2_http_test/turns`,
      {
        method: 'POST',
        headers: headers('chat-turn-00002'),
        body: JSON.stringify({
          question: '测试',
          idempotency_key: 'chat-turn-00002',
          tool: 'root_cause',
        }),
      },
    )
    assert.equal(invalid.status, 400)
    assert.equal(calls.length, 0)
  })
})
