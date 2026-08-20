import type {
  IncomingMessage,
  RequestListener,
  ServerResponse,
} from 'node:http'

import type {
  AuthenticateCountryOutageRequest,
  CountryOutagePrincipal,
} from '../server/contracts.js'
import { DomeyeReadModelError } from './country-outage-read-model.js'
import {
  DomeyeConversationError,
  type DomeyeInteractiveConversationService,
} from './interactive-conversation-service.js'

const DEFAULT_BASE_PATH = '/country-outage/chat'
const MAX_REQUEST_BYTES = 64 * 1024

export interface DomeyeInteractiveAgentHttpHandlerOptions {
  readonly service: DomeyeInteractiveConversationService
  readonly authenticate: AuthenticateCountryOutageRequest
  readonly authenticate_verifier: (request: IncomingMessage) => boolean
  readonly readiness: () => unknown
  readonly base_path?: string
}

function writeJson(
  response: ServerResponse,
  status: number,
  value: unknown,
  headers: Record<string, string> = {},
): void {
  const content = Buffer.from(JSON.stringify(value), 'utf8')
  response.writeHead(status, {
    'Content-Type': 'application/json; charset=utf-8',
    'Content-Length': String(content.byteLength),
    'Cache-Control': 'no-store',
    'X-Content-Type-Options': 'nosniff',
    ...headers,
  })
  response.end(content)
}

function publicError(
  response: ServerResponse,
  status: number,
  code: string,
  message: string,
  retryable = false,
): void {
  writeJson(response, status, {
    error: { code, message, retryable },
  })
}

function writeError(response: ServerResponse, error: unknown): void {
  if (error instanceof DomeyeConversationError) {
    if (error.code === 'internal_record_not_found') {
      publicError(response, 404, error.code, '内部 turn 记录不存在')
      return
    }
    const status = error.code === 'permission_denied' ? 403
      : error.code === 'conversation_not_found' ? 404
        : error.code === 'conversation_expired' ? 410
          : error.code === 'conversation_busy'
              || error.code === 'idempotency_conflict' ? 409
            : error.code === 'verified_identity_outside_candidate' ? 409
              : 400
    const publicCode = error.code === 'goal_outside_first_slice_contract'
      ? 'invalid_request'
      : error.code === 'verified_identity_outside_candidate'
        ? 'data_not_available'
        : error.code
    const message = error.code === 'permission_denied'
      ? '无权访问此资源'
      : error.code === 'conversation_not_found'
        ? '会话不存在'
        : error.code === 'conversation_expired'
          ? '会话已过期'
          : error.code === 'conversation_busy'
            ? '当前会话已有执行中的问题'
            : error.code === 'idempotency_conflict'
              ? '幂等键与已有请求冲突'
              : error.code === 'verified_identity_outside_candidate'
                ? '当前数据版本无法用于本次回答'
                : '当前请求超出此回答功能支持的范围'
    publicError(response, status, publicCode, message, error.retryable)
    return
  }
  if (error instanceof DomeyeReadModelError) {
    const status = error.code === 'evidence_not_found' ? 404
      : error.code === 'read_timeout' ? 504
        : error.code === 'data_api_unavailable' ? 503
          : error.code === 'cancelled' ? 409
            : 400
    const retryable = error.code === 'read_timeout'
      || error.code === 'data_api_unavailable'
    const code = retryable
      ? 'service_temporarily_unavailable'
      : error.code === 'cancelled'
        ? 'request_cancelled'
        : 'data_not_available'
    const message = retryable
      ? '数据暂时不可用，请稍后重试'
      : error.code === 'cancelled'
        ? '请求已取消'
        : '当前数据无法用于本次回答'
    publicError(response, status, code, message, retryable)
    return
  }
  publicError(
    response,
    500,
    'internal_error',
    '国家中断交互式 Agent 请求处理失败',
  )
}

async function authenticate(
  options: DomeyeInteractiveAgentHttpHandlerOptions,
  request: IncomingMessage,
): Promise<CountryOutagePrincipal> {
  const principal = await options.authenticate(request)
  if (!principal) {
    throw new DomeyeConversationError(
      'permission_denied',
      '需要通过内部身份认证',
    )
  }
  return principal
}

function authenticateVerifier(
  options: DomeyeInteractiveAgentHttpHandlerOptions,
  request: IncomingMessage,
): void {
  if (!options.authenticate_verifier(request)) {
    throw new DomeyeConversationError(
      'permission_denied',
      '需要独立验证器身份认证',
    )
  }
}

async function readJson(request: IncomingMessage): Promise<unknown> {
  const chunks: Buffer[] = []
  let byteLength = 0
  for await (const chunk of request) {
    const value = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk)
    byteLength += value.byteLength
    if (byteLength > MAX_REQUEST_BYTES) {
      throw new DomeyeConversationError(
        'goal_outside_first_slice_contract',
        `请求体超过 ${MAX_REQUEST_BYTES} 字节限制`,
      )
    }
    chunks.push(value)
  }
  if (byteLength === 0) return {}
  try {
    return JSON.parse(Buffer.concat(chunks).toString('utf8'))
  } catch {
    throw new DomeyeConversationError(
      'goal_outside_first_slice_contract',
      '请求体不是有效 JSON',
    )
  }
}

function objectBody(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new DomeyeConversationError(
      'goal_outside_first_slice_contract',
      '请求体必须是 JSON 对象',
    )
  }
  return value as Record<string, unknown>
}

function exactKeys(
  body: Record<string, unknown>,
  allowed: readonly string[],
): void {
  if (Object.keys(body).some((key) => !allowed.includes(key))) {
    throw new DomeyeConversationError(
      'goal_outside_first_slice_contract',
      '请求包含当前纵向切片合同之外的字段',
    )
  }
}

function idempotencyKey(
  request: IncomingMessage,
  body: Record<string, unknown>,
): string {
  const bodyValue = body.idempotency_key
  const headerValue = request.headers['idempotency-key']
  const header = typeof headerValue === 'string'
    ? headerValue
    : headerValue?.[0]
  if (
    typeof bodyValue === 'string'
    && header
    && bodyValue !== header
  ) throw new DomeyeConversationError(
    'idempotency_conflict',
    '请求体和 Header 的幂等键不一致',
  )
  const selected = typeof bodyValue === 'string' && bodyValue
    ? bodyValue
    : header
  if (!selected || !/^[A-Za-z0-9._:-]{8,128}$/.test(selected)) {
    throw new DomeyeConversationError(
      'goal_outside_first_slice_contract',
      '必须提供 8 至 128 位安全幂等键',
    )
  }
  return selected
}

function bindingBody(
  request: IncomingMessage,
  value: unknown,
) {
  const body = objectBody(value)
  exactKeys(body, [
    'event_reference',
    'publication_id',
    'revision',
    'idempotency_key',
  ])
  if (
    typeof body.event_reference !== 'string'
    || typeof body.publication_id !== 'string'
    || !body.publication_id
    || !Number.isSafeInteger(body.revision)
    || Number(body.revision) < 1
  ) throw new DomeyeConversationError(
    'goal_outside_first_slice_contract',
    '会话数据身份字段无效',
  )
  return {
    event_reference: body.event_reference,
    publication_id: body.publication_id,
    revision: Number(body.revision),
    idempotency_key: idempotencyKey(request, body),
  }
}

function turnBody(
  request: IncomingMessage,
  value: unknown,
) {
  const body = objectBody(value)
  exactKeys(body, ['question', 'idempotency_key'])
  if (
    typeof body.question !== 'string'
    || !body.question.trim()
    || body.question.length > 2_000
  ) throw new DomeyeConversationError(
    'goal_outside_first_slice_contract',
    'question 必须是 1 至 2,000 字符的非空文本',
  )
  return {
    question: body.question,
    idempotency_key: idempotencyKey(request, body),
  }
}

function identifier(value: string): string {
  let decoded: string
  try {
    decoded = decodeURIComponent(value)
  } catch {
    throw new DomeyeConversationError(
      'conversation_not_found',
      '资源标识无效',
    )
  }
  if (!decoded || decoded.length > 256 || decoded.includes('/')) {
    throw new DomeyeConversationError(
      'conversation_not_found',
      '资源标识无效',
    )
  }
  return decoded
}

function methodNotAllowed(
  response: ServerResponse,
  allowed: string,
): void {
  writeJson(response, 405, {
    error: {
      code: 'method_not_allowed',
      message: '请求方法不受支持',
      retryable: false,
    },
  }, { Allow: allowed })
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

export function createDomeyeInteractiveAgentHttpHandler(
  options: DomeyeInteractiveAgentHttpHandlerOptions,
): RequestListener {
  const basePath = (options.base_path ?? DEFAULT_BASE_PATH).replace(/\/+$/, '')
  if (!basePath.startsWith('/') || basePath.includes('?') || basePath.includes('#')) {
    throw new TypeError('交互式 Agent base path 无效')
  }
  const escapedBasePath = escapeRegExp(basePath)

  return (request, response): void => {
    void (async () => {
      const pathname = new URL(
        request.url ?? '/',
        'http://interactive-agent.invalid',
      ).pathname

      if (pathname === `${basePath}/readiness`) {
        if (request.method !== 'GET') {
          methodNotAllowed(response, 'GET')
          return
        }
        await authenticate(options, request)
        writeJson(response, 200, options.readiness())
        return
      }

      const internalRecordMatch = pathname.match(
        new RegExp(
          `^${escapedBasePath}/internal/conversations/([^/]+)/turns/([^/]+)$`,
        ),
      )
      if (internalRecordMatch) {
        if (request.method !== 'GET') {
          methodNotAllowed(response, 'GET')
          return
        }
        authenticateVerifier(options, request)
        const record = options.service.getTurnInternalRecord(
          identifier(internalRecordMatch[1]!),
          identifier(internalRecordMatch[2]!),
        )
        writeJson(response, 200, { record })
        return
      }

      if (pathname === `${basePath}/conversations`) {
        if (request.method !== 'POST') {
          methodNotAllowed(response, 'POST')
          return
        }
        const principal = await authenticate(options, request)
        const result = await options.service.createConversation(
          principal,
          bindingBody(request, await readJson(request)),
        )
        writeJson(response, result.deduplicated ? 200 : 201, result)
        return
      }

      const conversationMatch = pathname.match(
        new RegExp(`^${escapedBasePath}/conversations/([^/]+)$`),
      )
      if (conversationMatch) {
        if (request.method !== 'GET') {
          methodNotAllowed(response, 'GET')
          return
        }
        const principal = await authenticate(options, request)
        const conversation = await options.service.getConversation(
          principal,
          identifier(conversationMatch[1]!),
        )
        writeJson(response, 200, { conversation })
        return
      }

      const turnsMatch = pathname.match(
        new RegExp(`^${escapedBasePath}/conversations/([^/]+)/turns$`),
      )
      if (turnsMatch) {
        if (request.method !== 'POST') {
          methodNotAllowed(response, 'POST')
          return
        }
        const principal = await authenticate(options, request)
        const result = await options.service.createTurn(
          principal,
          identifier(turnsMatch[1]!),
          turnBody(request, await readJson(request)),
        )
        writeJson(response, result.deduplicated ? 200 : 201, result)
        return
      }

      const cancelMatch = pathname.match(
        new RegExp(
          `^${escapedBasePath}/conversations/([^/]+)/turns/([^/]+)/cancel$`,
        ),
      )
      if (cancelMatch) {
        if (request.method !== 'POST') {
          methodNotAllowed(response, 'POST')
          return
        }
        const principal = await authenticate(options, request)
        const body = objectBody(await readJson(request))
        exactKeys(body, [])
        const result = await options.service.cancelTurn(
          principal,
          identifier(cancelMatch[1]!),
          identifier(cancelMatch[2]!),
        )
        writeJson(response, 200, result)
        return
      }

      publicError(response, 404, 'route_not_found', '接口不存在')
    })().catch((error: unknown) => {
      if (!response.headersSent) writeError(response, error)
    })
  }
}
