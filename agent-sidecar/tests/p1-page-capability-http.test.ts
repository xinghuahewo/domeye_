import assert from 'node:assert/strict'
import { createServer } from 'node:http'
import type { AddressInfo } from 'node:net'
import { test } from 'node:test'

import type { CountryOutageAgentOrchestrator } from '../src/application/index.js'
import {
  createCountryOutageAgentHttpHandler,
  type CountryOutageSessionManager,
} from '../src/server/index.js'

async function withReportServer(
  run: (baseUrl: string, authenticationCalls: () => number) => Promise<void>,
): Promise<void> {
  let authenticationCallCount = 0
  const server = createServer(
    createCountryOutageAgentHttpHandler({
      application: {} as CountryOutageAgentOrchestrator,
      authenticate: () => {
        authenticationCallCount += 1
        return {
          userId: 'report-user',
          authorizationScope: 'country_outage_event_read:IR',
        }
      },
    }),
  )
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve))
  const address = server.address() as AddressInfo
  try {
    await run(
      `http://127.0.0.1:${address.port}`,
      () => authenticationCallCount,
    )
  } finally {
    await new Promise<void>((resolve, reject) =>
      server.close((error) => (error ? reject(error) : resolve())),
    )
  }
}

test('报告 HTTP handler 不再暴露任何聊天或 rebind 路由', async () => {
  await withReportServer(async (baseUrl, authenticationCalls) => {
    const requests = [
      { method: 'GET', path: '/country-outage/chat/readiness' },
      { method: 'POST', path: '/country-outage/chat/conversations' },
      {
        method: 'GET',
        path: '/country-outage/chat/conversations/conversation-old',
      },
      {
        method: 'POST',
        path: '/country-outage/chat/conversations/conversation-old/turns',
      },
      {
        method: 'POST',
        path: '/country-outage/chat/conversations/conversation-old/rebind',
      },
      {
        method: 'POST',
        path:
          '/country-outage/chat/conversations/conversation-old/turns/turn-old/cancel',
      },
    ] as const

    for (const request of requests) {
      const response = await fetch(`${baseUrl}${request.path}`, {
        method: request.method,
      })
      assert.equal(response.status, 404, request.path)
      assert.deepEqual(await response.json(), {
        error: {
          code: 'route_not_found',
          message: '接口不存在',
          retryable: false,
        },
      })
    }

    assert.equal(authenticationCalls(), 0)
  })
})

test('报告 HTTP handler 必须且只能注入一个报告应用', () => {
  const application = {} as CountryOutageAgentOrchestrator
  const manager = {} as CountryOutageSessionManager
  const authenticate = (): null => null

  assert.throws(
    () => createCountryOutageAgentHttpHandler({ authenticate }),
    /必须且只能提供一个报告 application/,
  )
  assert.throws(
    () => createCountryOutageAgentHttpHandler({
      application,
      manager,
      authenticate,
    }),
    /必须且只能提供一个报告 application/,
  )
})
