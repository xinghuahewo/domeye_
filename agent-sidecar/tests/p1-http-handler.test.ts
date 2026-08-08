import assert from 'node:assert/strict'
import { createServer } from 'node:http'
import type { AddressInfo } from 'node:net'
import test from 'node:test'

import type { CountryOutageAgentOrchestrator } from '../src/application/index.js'
import type {
  CreateP1ConversationRequest,
  P1ChatApplication,
} from '../src/chat/index.js'
import type { CountryOutagePrincipal } from '../src/server/index.js'
import { createCountryOutageAgentHttpHandler } from '../src/server/index.js'

test('P1 HTTP 会话入口校验身份并传递幂等键', async () => {
  let received: unknown
  const chat = {
    async createConversation(
      _principal: CountryOutagePrincipal,
      request: CreateP1ConversationRequest,
    ) {
      received = request
      return {
        deduplicated: false,
        conversation: {
          schema_version: 'country_outage_p1_chat_v1',
          conversation_id: 'conv_http',
          binding: {}, state: {}, turns: [], expires_at: '', reminder_at: '', created_at: '',
        },
      }
    },
  } as unknown as P1ChatApplication
  const server = createServer(createCountryOutageAgentHttpHandler({
    application: {} as CountryOutageAgentOrchestrator,
    chat,
    authenticate: () => ({ userId: 'http-user', authorizationScope: 'event-read' }),
  }))
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve))
  const { port } = server.address() as AddressInfo
  try {
    const response = await fetch(`http://127.0.0.1:${port}/country-outage/chat/conversations`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Idempotency-Key': 'conversation-http-01' },
      body: JSON.stringify({
        event_reference: 'country_outage/2026-02-27 09:12:32/IR/1/r',
        publication_id: 'publication-http',
        revision: 1,
        idempotency_key: 'conversation-http-01',
      }),
    })
    assert.equal(response.status, 201)
    assert.deepEqual(received, {
      event_reference: 'country_outage/2026-02-27 09:12:32/IR/1/r',
      publication_id: 'publication-http',
      revision: 1,
      idempotency_key: 'conversation-http-01',
    })
  } finally {
    await new Promise<void>((resolve) => server.close(() => resolve()))
  }
})
