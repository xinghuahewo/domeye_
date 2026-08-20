import assert from 'node:assert/strict'
import { createServer } from 'node:http'
import type { AddressInfo } from 'node:net'
import test from 'node:test'

import {
  createDomeyeInteractiveAgentHttpHandler,
} from '../src/agent/interactive-http-handler.js'
import type {
  DomeyeInteractiveConversationService,
} from '../src/agent/interactive-conversation-service.js'

const AUTHORIZATION = 'Bearer first-slice-test'
const VERIFIER_AUTHORIZATION = 'Bearer first-slice-verifier-test'
const REFERENCE =
  'country_outage/2026-02-27 00:10:00/IR/1/first-slice'
const PUBLICATION =
  'country_outage_publication_v1_989f698fb6f6c32579eebe7bb2bc833f'

interface ServiceCall {
  readonly operation: 'create' | 'get' | 'turn' | 'cancel' | 'internal'
  readonly principal: unknown
  readonly conversation_id?: string
  readonly turn_id?: string
  readonly input?: unknown
}

async function withServer(
  run: (
    baseUrl: string,
    calls: ServiceCall[],
    readinessCalls: { count: number },
  ) => Promise<void>,
): Promise<void> {
  const calls: ServiceCall[] = []
  const readinessCalls = { count: 0 }
  const conversation = {
    schema_version: 'domeye_interactive_agent_conversation_v2',
    conversation_id: 'conversation-first-slice',
    binding: {
      event_reference: REFERENCE,
      publication_id: PUBLICATION,
      revision: 1,
    },
    turns: [],
  }
  const service = {
    async createConversation(principal: unknown, input: unknown) {
      calls.push({ operation: 'create', principal, input })
      return { conversation, deduplicated: false }
    },
    async getConversation(principal: unknown, conversationId: string) {
      calls.push({
        operation: 'get',
        principal,
        conversation_id: conversationId,
      })
      return conversation
    },
    async createTurn(
      principal: unknown,
      conversationId: string,
      input: unknown,
    ) {
      calls.push({
        operation: 'turn',
        principal,
        conversation_id: conversationId,
        input,
      })
      return {
        turn: {
          turn_id: 'turn-first-slice',
          turn_number: 1,
          question: '首片固定问题',
          state: 'executing',
          created_at: '2026-08-19T07:00:00.000Z',
        },
        deduplicated: false,
      }
    },
    async cancelTurn(
      principal: unknown,
      conversationId: string,
      turnId: string,
    ) {
      calls.push({
        operation: 'cancel',
        principal,
        conversation_id: conversationId,
        turn_id: turnId,
      })
      return { turn_id: turnId, state: 'cancel_requested' as const }
    },
    getTurnInternalRecord(conversationId: string, turnId: string) {
      calls.push({
        operation: 'internal',
        principal: null,
        conversation_id: conversationId,
        turn_id: turnId,
      })
      return {
        schema_version: 'domeye_interactive_agent_turn_internal_record_v1',
        record_id: 'turn-internal-record-sha256:test',
        record_digest: 'sha256:test',
        conversation_id: conversationId,
        turn_id: turnId,
      }
    },
  } as unknown as DomeyeInteractiveConversationService
  const server = createServer(createDomeyeInteractiveAgentHttpHandler({
    service,
    authenticate: (request) => request.headers.authorization === AUTHORIZATION
      ? {
          userId: 'user-first-slice',
          authorizationScope: 'country_outage_event_read:IR',
        }
      : null,
    authenticate_verifier: (request) =>
      request.headers.authorization === VERIFIER_AUTHORIZATION,
    readiness: () => {
      readinessCalls.count += 1
      return {
        ready: true,
        candidate_id: 'candidate-first-slice',
        request_route: 'interactive_agent_only',
      }
    },
  }))
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve))
  const address = server.address() as AddressInfo
  try {
    await run(`http://127.0.0.1:${address.port}`, calls, readinessCalls)
  } finally {
    await new Promise<void>((resolve, reject) => {
      server.close((error) => error ? reject(error) : resolve())
    })
  }
}

function authenticatedHeaders(
  idempotencyKey?: string,
): Record<string, string> {
  return {
    Authorization: AUTHORIZATION,
    'Content-Type': 'application/json',
    ...(idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : {}),
  }
}

test('HTTP 窄入口只调用交互会话服务完成 readiness、create、get、turn 与 cancel', async () => {
  await withServer(async (baseUrl, calls, readinessCalls) => {
    const readiness = await fetch(`${baseUrl}/country-outage/chat/readiness`, {
      headers: authenticatedHeaders(),
    })
    assert.equal(readiness.status, 200)
    assert.deepEqual(await readiness.json(), {
      ready: true,
      candidate_id: 'candidate-first-slice',
      request_route: 'interactive_agent_only',
    })

    const created = await fetch(
      `${baseUrl}/country-outage/chat/conversations`,
      {
        method: 'POST',
        headers: authenticatedHeaders('create-key-0001'),
        body: JSON.stringify({
          event_reference: REFERENCE,
          publication_id: PUBLICATION,
          revision: 1,
          idempotency_key: 'create-key-0001',
        }),
      },
    )
    assert.equal(created.status, 201)
    assert.equal(
      (await created.json() as { conversation: { conversation_id: string } })
        .conversation.conversation_id,
      'conversation-first-slice',
    )

    const snapshot = await fetch(
      `${baseUrl}/country-outage/chat/conversations/conversation-first-slice`,
      { headers: authenticatedHeaders() },
    )
    assert.equal(snapshot.status, 200)

    const started = await fetch(
      `${baseUrl}/country-outage/chat/conversations/conversation-first-slice/turns`,
      {
        method: 'POST',
        headers: authenticatedHeaders('turn-key-000001'),
        body: JSON.stringify({
          question: '首片固定问题',
          idempotency_key: 'turn-key-000001',
        }),
      },
    )
    assert.equal(started.status, 201)
    assert.equal(
      (await started.json() as { turn: { state: string } }).turn.state,
      'executing',
    )

    const cancelled = await fetch(
      `${baseUrl}/country-outage/chat/conversations/conversation-first-slice/turns/turn-first-slice/cancel`,
      {
        method: 'POST',
        headers: authenticatedHeaders(),
        body: '{}',
      },
    )
    assert.equal(cancelled.status, 200)
    assert.deepEqual(await cancelled.json(), {
      turn_id: 'turn-first-slice',
      state: 'cancel_requested',
    })

    assert.equal(readinessCalls.count, 1)
    assert.deepEqual(calls.map((call) => call.operation), [
      'create',
      'get',
      'turn',
      'cancel',
    ])
    assert.deepEqual(calls[0]?.input, {
      event_reference: REFERENCE,
      publication_id: PUBLICATION,
      revision: 1,
      idempotency_key: 'create-key-0001',
    })
    assert.deepEqual(calls[2]?.input, {
      question: '首片固定问题',
      idempotency_key: 'turn-key-000001',
    })
    assert.deepEqual(calls[3], {
      operation: 'cancel',
      principal: {
        userId: 'user-first-slice',
        authorizationScope: 'country_outage_event_read:IR',
      },
      conversation_id: 'conversation-first-slice',
      turn_id: 'turn-first-slice',
    })
  })
})

test('内部记录只允许独立验证器 Token 读取', async () => {
  await withServer(async (baseUrl, calls) => {
    const path = `${baseUrl}/country-outage/chat/internal/conversations/conversation-first-slice/turns/turn-first-slice`
    for (const authorization of [undefined, AUTHORIZATION]) {
      const response = await fetch(path, {
        headers: authorization ? { Authorization: authorization } : {},
      })
      assert.equal(response.status, 403)
    }
    assert.equal(calls.length, 0)

    const verified = await fetch(path, {
      headers: { Authorization: VERIFIER_AUTHORIZATION },
    })
    assert.equal(verified.status, 200)
    assert.equal(
      (await verified.json() as { record: { turn_id: string } })
        .record.turn_id,
      'turn-first-slice',
    )
    assert.deepEqual(calls, [{
      operation: 'internal',
      principal: null,
      conversation_id: 'conversation-first-slice',
      turn_id: 'turn-first-slice',
    }])
  })
})

test('HTTP 窄入口在认证与精确字段边界失败关闭', async () => {
  await withServer(async (baseUrl, calls, readinessCalls) => {
    const unauthorizedReadiness = await fetch(
      `${baseUrl}/country-outage/chat/readiness`,
    )
    assert.equal(unauthorizedReadiness.status, 403)

    const unauthorizedCreate = await fetch(
      `${baseUrl}/country-outage/chat/conversations`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: '{}',
      },
    )
    assert.equal(unauthorizedCreate.status, 403)

    const extraCreateField = await fetch(
      `${baseUrl}/country-outage/chat/conversations`,
      {
        method: 'POST',
        headers: authenticatedHeaders('create-key-0002'),
        body: JSON.stringify({
          event_reference: REFERENCE,
          publication_id: PUBLICATION,
          revision: 1,
          idempotency_key: 'create-key-0002',
          execution_hint: 'anything',
        }),
      },
    )
    assert.equal(extraCreateField.status, 400)
    assert.equal(
      (await extraCreateField.json() as { error: { code: string } }).error.code,
      'invalid_request',
    )

    const conflictingKey = await fetch(
      `${baseUrl}/country-outage/chat/conversations`,
      {
        method: 'POST',
        headers: authenticatedHeaders('create-key-header'),
        body: JSON.stringify({
          event_reference: REFERENCE,
          publication_id: PUBLICATION,
          revision: 1,
          idempotency_key: 'create-key-body-01',
        }),
      },
    )
    assert.equal(conflictingKey.status, 409)

    const extraTurnField = await fetch(
      `${baseUrl}/country-outage/chat/conversations/conversation-first-slice/turns`,
      {
        method: 'POST',
        headers: authenticatedHeaders('turn-key-000002'),
        body: JSON.stringify({
          question: '首片固定问题',
          idempotency_key: 'turn-key-000002',
          future_actions: [],
        }),
      },
    )
    assert.equal(extraTurnField.status, 400)

    const extraCancelField = await fetch(
      `${baseUrl}/country-outage/chat/conversations/conversation-first-slice/turns/turn-first-slice/cancel`,
      {
        method: 'POST',
        headers: authenticatedHeaders(),
        body: JSON.stringify({ reason: 'client-choice' }),
      },
    )
    assert.equal(extraCancelField.status, 400)

    assert.equal(readinessCalls.count, 0)
    assert.equal(calls.length, 0)
  })
})

test('未登记路径与会话重绑定路径固定返回 404 且不触发服务', async () => {
  await withServer(async (baseUrl, calls) => {
    const unknown = await fetch(
      `${baseUrl}/country-outage/chat/unknown`,
      { headers: authenticatedHeaders() },
    )
    assert.equal(unknown.status, 404)
    assert.equal(
      (await unknown.json() as { error: { code: string } }).error.code,
      'route_not_found',
    )

    const rebind = await fetch(
      `${baseUrl}/country-outage/chat/conversations/conversation-first-slice/rebind`,
      {
        method: 'POST',
        headers: authenticatedHeaders(),
        body: JSON.stringify({ publication_id: 'another-publication' }),
      },
    )
    assert.equal(rebind.status, 404)
    assert.equal(
      (await rebind.json() as { error: { code: string } }).error.code,
      'route_not_found',
    )
    assert.equal(calls.length, 0)
  })
})
