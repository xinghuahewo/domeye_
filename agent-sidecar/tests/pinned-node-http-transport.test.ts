import assert from 'node:assert/strict'
import { EventEmitter } from 'node:events'
import type {
  ClientRequest,
  IncomingMessage,
} from 'node:http'
import { PassThrough } from 'node:stream'
import test from 'node:test'

import {
  ExternalEvidenceSafetyError,
  PinnedNodeHttpTransport,
  type PinnedNodeRequestFactory,
  type PinnedNodeRequestOptions,
} from '../src/external/index.js'

interface CapturedRequest {
  protocol: 'http:' | 'https:'
  url: URL
  options: PinnedNodeRequestOptions
}

interface FakeRequestContext extends CapturedRequest {
  respond: (
    response: IncomingMessage,
    chunks?: readonly (Buffer | string)[],
  ) => void
  fail: (error: Error) => void
}

function incomingResponse(
  headers: Record<string, string>,
  statusCode = 200,
): IncomingMessage {
  const response = new PassThrough()
  return Object.assign(response, {
    headers,
    statusCode,
  }) as unknown as IncomingMessage
}

function requestFactory(
  onEnd: (context: FakeRequestContext) => void,
): {
  factory: PinnedNodeRequestFactory
  calls: CapturedRequest[]
} {
  const calls: CapturedRequest[] = []
  const factory: PinnedNodeRequestFactory = (
    protocol,
    url,
    options,
    onResponse,
  ) => {
    calls.push({ protocol, url, options })
    const request = new EventEmitter() as EventEmitter & {
      end: () => void
    }
    request.end = () => {
      onEnd({
        protocol,
        url,
        options,
        respond(response, chunks = []) {
          onResponse(response)
          for (const chunk of chunks) response.push(chunk)
          response.push(null)
        },
        fail(error) {
          request.emit('error', error)
        },
      })
    }
    return request as unknown as ClientRequest
  }
  return { factory, calls }
}

function transportInput(
  signal = new AbortController().signal,
  maximumBytes = 1_024,
) {
  return {
    url: new URL('https://public.example/report?id=1'),
    addresses: [{ address: '93.184.216.34', family: 4 as const }],
    headers: {
      Accept: 'text/plain',
      host: '10.0.0.1',
    },
    signal,
    maximumBytes,
  }
}

test('固定 verified IP，同时保留原始 Host 与 HTTPS SNI', async () => {
  let pinnedAddress:
    | { address: string; family: number }
    | undefined
  let pinnedAddresses:
    | { address: string; family: number }[]
    | undefined
  const harness = requestFactory((context) => {
    const lookupPinned = context.options.lookup as (
      hostname: string,
      options: { all?: boolean },
      callback: (
        error: NodeJS.ErrnoException | null,
        address:
          | string
          | { address: string; family: number }[],
        family?: number,
      ) => void,
    ) => void
    lookupPinned(
      'public.example',
      {},
      (error, address, family) => {
        assert.equal(error, null)
        if (
          typeof address !== 'string' ||
          typeof family !== 'number'
        ) {
          throw new Error('单地址 lookup 回调类型无效')
        }
        pinnedAddress = { address, family }
      },
    )
    lookupPinned(
      'public.example',
      { all: true },
      (error, addresses) => {
        assert.equal(error, null)
        assert.ok(Array.isArray(addresses))
        pinnedAddresses = addresses
      },
    )
    context.respond(
      incomingResponse({
        'content-type': 'text/plain',
        'content-length': '2',
      }),
      ['ok'],
    )
  })
  const signal = new AbortController().signal
  const result = await new PinnedNodeHttpTransport(
    harness.factory,
  ).request(transportInput(signal))

  assert.equal(result.body.toString('utf8'), 'ok')
  assert.deepEqual(pinnedAddress, {
    address: '93.184.216.34',
    family: 4,
  })
  assert.deepEqual(pinnedAddresses, [{
    address: '93.184.216.34',
    family: 4,
  }])
  assert.equal(harness.calls.length, 1)
  assert.equal(harness.calls[0]?.protocol, 'https:')
  assert.equal(
    harness.calls[0]?.url.toString(),
    'https://public.example/report?id=1',
  )
  assert.equal(harness.calls[0]?.options.servername, 'public.example')
  assert.equal(harness.calls[0]?.options.setHost, true)
  assert.equal(harness.calls[0]?.options.signal, signal)
  assert.deepEqual(harness.calls[0]?.options.headers, {
    Accept: 'text/plain',
    Host: 'public.example',
  })
})

test('声明长度和流式累计长度分别在底层传输处失败关闭', async (t) => {
  await t.test('声明长度超限', async () => {
    const harness = requestFactory((context) => {
      context.respond(incomingResponse({
        'content-type': 'text/plain',
        'content-length': '6',
      }))
    })
    await assert.rejects(
      new PinnedNodeHttpTransport(harness.factory).request(
        transportInput(undefined, 5),
      ),
      (error) => (
        error instanceof ExternalEvidenceSafetyError &&
        error.code === 'external_response_too_large'
      ),
    )
  })

  await t.test('无 Content-Length 时流式超限', async () => {
    const harness = requestFactory((context) => {
      context.respond(
        incomingResponse({ 'content-type': 'text/plain' }),
        ['abc', 'def'],
      )
    })
    await assert.rejects(
      new PinnedNodeHttpTransport(harness.factory).request(
        transportInput(undefined, 5),
      ),
      (error) => (
        error instanceof ExternalEvidenceSafetyError &&
        error.code === 'external_response_too_large'
      ),
    )
  })
})

test('AbortSignal 原样交给 Node 请求并中止等待中的读取', async () => {
  const controller = new AbortController()
  const harness = requestFactory((context) => {
    assert.equal(context.options.signal, controller.signal)
    context.options.signal?.addEventListener('abort', () => {
      const error = new Error('aborted')
      error.name = 'AbortError'
      context.fail(error)
    }, { once: true })
  })
  const pending = new PinnedNodeHttpTransport(harness.factory).request(
    transportInput(controller.signal),
  )
  controller.abort()

  await assert.rejects(pending, { name: 'AbortError' })
})
