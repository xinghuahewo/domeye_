import type {
  IncomingMessage,
  RequestListener,
  ServerResponse,
} from 'node:http'

import type {
  AuthenticateCountryOutageRequest,
  CountryOutageAgentEvent,
  CountryOutagePrincipal,
  CreateReportRequest,
  DownloadArtifact,
} from './contracts.js'
import type {
  CountryOutageAgentOrchestrator,
} from '../application/country-outage-agent-orchestrator.js'
import type {
  CreateOrchestratedQuestionRequest,
} from '../application/contracts.js'
import type { P1RuntimeV2ConversationService } from '../chat/runtime-v2-conversation.js'
import type { CountryOutageSessionManager } from './country-outage-session-manager.js'
import { CountryOutageHttpError } from './errors.js'

const BASE_PATH = '/country-outage'
const MAX_REQUEST_BYTES = 64 * 1024

export interface CountryOutageAgentHttpHandlerOptions {
  /**
   * 新正式入口使用 application。manager 只为现有测试与旧调用方保留；
   * 两者不能同时提供。
   */
  application?: CountryOutageAgentOrchestrator
  manager?: CountryOutageSessionManager
  /**
   * P1 页面能力覆盖候选的只读聊天入口。未显式注入时路由不存在，
   * 不影响正式报告 application。
   */
  chatService?: P1RuntimeV2ConversationService
  authenticate: AuthenticateCountryOutageRequest
  basePath?: string
  sseHeartbeatMs?: number
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

function writeError(response: ServerResponse, error: unknown): void {
  if (error instanceof CountryOutageHttpError) {
    writeJson(response, error.status, {
      error: error.toPublicError(),
    })
    return
  }
  if (
    error instanceof Error
    && 'code' in error
    && typeof error.code === 'string'
    && [
      'P1SemanticPlanError',
      'P1RuntimeV2SingleTurnError',
      'P1ReadModelError',
    ].includes(error.name)
  ) {
    const code = error.code
    const status = code === 'permission_denied' ? 403
      : code === 'conversation_not_found' || code === 'turn_not_found' ? 404
        : code === 'conversation_expired' ? 410
          : code === 'tool_timeout' ? 504
            : code === 'data_api_unavailable' ? 503
              : [
                  'binding_conflict', 'idempotency_conflict',
                  'conversation_busy', 'event_binding_suspended_until_rebind',
                  'stale_idempotency_generation', 'revision_drift',
                ].includes(code) ? 409
                : 400
    writeJson(response, status, {
      error: {
        code,
        message: error.message,
        retryable: 'retryable' in error && error.retryable === true,
      },
    })
    return
  }
  writeJson(response, 500, {
    error: {
      code: 'internal_error',
      message: '国家中断 Agent 请求处理失败',
      retryable: false,
    },
  })
}

async function readJson(request: IncomingMessage): Promise<unknown> {
  const chunks: Buffer[] = []
  let byteLength = 0
  for await (const chunk of request) {
    const value = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk)
    byteLength += value.byteLength
    if (byteLength > MAX_REQUEST_BYTES) {
      throw new CountryOutageHttpError(
        413,
        'request_too_large',
        `请求体超过 ${MAX_REQUEST_BYTES} 字节限制`,
      )
    }
    chunks.push(value)
  }
  if (byteLength === 0) return {}
  try {
    return JSON.parse(Buffer.concat(chunks).toString('utf8'))
  } catch {
    throw new CountryOutageHttpError(
      400,
      'invalid_json',
      '请求体不是有效 JSON',
    )
  }
}

function objectBody(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new CountryOutageHttpError(
      400,
      'invalid_request',
      '请求体必须是 JSON 对象',
    )
  }
  return value as Record<string, unknown>
}

function exactKeys(
  body: Record<string, unknown>,
  allowed: readonly string[],
): void {
  const unexpected = Object.keys(body).filter(
    (key) => !allowed.includes(key),
  )
  if (unexpected.length > 0) {
    throw new CountryOutageHttpError(
      400,
      'invalid_request_fields',
      `请求包含未允许字段：${unexpected.join(', ')}`,
    )
  }
}

function idempotencyKey(
  request: IncomingMessage,
  body: Record<string, unknown>,
): string {
  const bodyValue = body.idempotency_key
  const headerValue = request.headers['idempotency-key']
  const header =
    typeof headerValue === 'string' ? headerValue : headerValue?.[0]
  if (
    typeof bodyValue === 'string' &&
    header &&
    bodyValue !== header
  ) {
    throw new CountryOutageHttpError(
      409,
      'idempotency_conflict',
      '请求体和 Header 的 idempotency_key 不一致',
    )
  }
  const selected =
    typeof bodyValue === 'string' && bodyValue ? bodyValue : header
  if (!selected) {
    throw new CountryOutageHttpError(
      400,
      'invalid_idempotency_key',
      '必须提供 idempotency_key',
    )
  }
  return selected
}

function createReportBody(
  request: IncomingMessage,
  value: unknown,
): CreateReportRequest {
  const body = objectBody(value)
  exactKeys(body, [
    'event_reference',
    'publication_id',
    'revision',
    'idempotency_key',
  ])
  return {
    event_reference:
      typeof body.event_reference === 'string'
        ? body.event_reference
        : '',
    publication_id:
      typeof body.publication_id === 'string' ? body.publication_id : '',
    revision:
      typeof body.revision === 'number' ? body.revision : Number.NaN,
    idempotency_key: idempotencyKey(request, body),
  }
}

function createChatTurnBody(
  request: IncomingMessage,
  value: unknown,
): { question: string; idempotency_key: string } {
  const body = objectBody(value)
  exactKeys(body, ['question', 'idempotency_key'])
  const question = typeof body.question === 'string' ? body.question : ''
  if (!question.trim() || question.length > 2_000) {
    throw new CountryOutageHttpError(
      400,
      'invalid_question',
      'question 必须是 1 至 2,000 字符的非空文本',
    )
  }
  return {
    question,
    idempotency_key: idempotencyKey(request, body),
  }
}

function createQuestionBody(
  request: IncomingMessage,
  value: unknown,
): CreateOrchestratedQuestionRequest {
  const body = objectBody(value)
  exactKeys(body, [
    'question',
    'evidence_mode',
    'quote',
    'external_authorization',
    'external_urls',
    'idempotency_key',
  ])
  let quote: CreateOrchestratedQuestionRequest['quote']
  if (body.quote !== undefined) {
    const rawQuote = objectBody(body.quote)
    exactKeys(rawQuote, [
      'kind',
      'highlight_index',
      'section_id',
      'paragraph_index',
      'evidence_refs',
    ])
    if (
      rawQuote.evidence_refs !== undefined &&
      (!Array.isArray(rawQuote.evidence_refs) ||
        rawQuote.evidence_refs.some((value) => typeof value !== 'string'))
    ) {
      throw new CountryOutageHttpError(
        400,
        'invalid_report_quote',
        'quote.evidence_refs 必须是字符串数组',
      )
    }
    quote = {
      kind:
        rawQuote.kind === 'summary' ||
        rawQuote.kind === 'highlight' ||
        rawQuote.kind === 'section_paragraph'
          ? rawQuote.kind
          : (String(rawQuote.kind) as 'summary'),
      ...(typeof rawQuote.highlight_index === 'number'
        ? { highlight_index: rawQuote.highlight_index }
        : {}),
      ...(typeof rawQuote.section_id === 'string'
        ? { section_id: rawQuote.section_id }
        : {}),
      ...(typeof rawQuote.paragraph_index === 'number'
        ? { paragraph_index: rawQuote.paragraph_index }
        : {}),
      ...(Array.isArray(rawQuote.evidence_refs)
        ? { evidence_refs: rawQuote.evidence_refs as string[] }
        : {}),
    }
  }
  let externalAuthorization:
    | {
        authorized: true
        authorized_at: string
      }
    | undefined
  if (body.external_authorization !== undefined) {
    const rawAuthorization = objectBody(body.external_authorization)
    exactKeys(rawAuthorization, ['authorized', 'authorized_at'])
    if (
      rawAuthorization.authorized !== true ||
      typeof rawAuthorization.authorized_at !== 'string'
    ) {
      throw new CountryOutageHttpError(
        400,
        'invalid_external_authorization',
        '外部证据授权必须明确包含 authorized=true 和授权时间',
      )
    }
    externalAuthorization = {
      authorized: true,
      authorized_at: rawAuthorization.authorized_at,
    }
  }
  let externalUrls: string[] | undefined
  if (body.external_urls !== undefined) {
    if (
      !Array.isArray(body.external_urls) ||
      body.external_urls.some((value) => typeof value !== 'string')
    ) {
      throw new CountryOutageHttpError(
        400,
        'invalid_external_urls',
        'external_urls 必须是公开 HTTP/HTTPS URL 数组',
      )
    }
    externalUrls = body.external_urls
  }
  const common = {
    question: typeof body.question === 'string' ? body.question : '',
    ...(quote ? { quote } : {}),
    idempotency_key: idempotencyKey(request, body),
  }
  if (body.evidence_mode === 'domeye_only') {
    if (externalAuthorization || externalUrls !== undefined) {
      throw new CountryOutageHttpError(
        400,
        'unexpected_external_fields',
        '仅使用 Domeye 数据时不能携带外部授权或 URL',
      )
    }
    return {
      ...common,
      evidence_mode: 'domeye_only',
    }
  }
  if (body.evidence_mode === 'domeye_plus_external') {
    if (!externalAuthorization) {
      throw new CountryOutageHttpError(
        400,
        'external_authorization_required',
        '读取指定 URL 必须包含本次用户显式授权及授权时间',
      )
    }
    if (!externalUrls) {
      throw new CountryOutageHttpError(
        400,
        'invalid_external_urls',
        'domeye_plus_external 必须包含 1 至 5 个指定公开 URL',
      )
    }
    return {
      ...common,
      evidence_mode: 'domeye_plus_external',
      external_authorization: externalAuthorization,
      external_urls: externalUrls,
    }
  }
  throw new CountryOutageHttpError(
    400,
    'invalid_evidence_mode',
    'evidence_mode 只允许 domeye_only 或 domeye_plus_external',
  )
}

function parseAfterEventId(request: IncomingMessage, url: URL): number {
  const headerValue = request.headers['last-event-id']
  const header =
    typeof headerValue === 'string' ? headerValue : headerValue?.[0]
  const raw = header ?? url.searchParams.get('last_event_id') ?? '0'
  const parsed = Number(raw)
  if (!Number.isSafeInteger(parsed) || parsed < 0) {
    throw new CountryOutageHttpError(
      400,
      'invalid_last_event_id',
      'Last-Event-ID 必须为非负整数',
    )
  }
  return parsed
}

function writeSseEvent(
  response: ServerResponse,
  event: CountryOutageAgentEvent,
): void {
  response.write(`id: ${event.event_id}\n`)
  response.write(`event: ${event.event_type}\n`)
  response.write(`data: ${JSON.stringify(event)}\n\n`)
}

async function authenticate(
  options: CountryOutageAgentHttpHandlerOptions,
  request: IncomingMessage,
): Promise<CountryOutagePrincipal> {
  const principal = await options.authenticate(request)
  if (!principal) {
    throw new CountryOutageHttpError(
      401,
      'authentication_required',
      '需要登录后访问国家中断报告',
    )
  }
  return principal
}

function methodNotAllowed(
  response: ServerResponse,
  allowed: string,
): void {
  writeJson(
    response,
    405,
    {
      error: {
        code: 'method_not_allowed',
        message: '请求方法不受支持',
        retryable: false,
      },
    },
    { Allow: allowed },
  )
}

function writeDownloadArtifact(
  response: ServerResponse,
  artifact: DownloadArtifact,
): void {
  const graceTimeout = setTimeout(
    () => {
      if (!response.writableFinished) response.destroy()
    },
    Math.max(1, artifact.downloadDeadlineAtMs - Date.now()),
  )
  graceTimeout.unref()
  const clearGraceTimeout = (): void => clearTimeout(graceTimeout)
  response.once('finish', clearGraceTimeout)
  response.once('close', clearGraceTimeout)
  response.writeHead(200, {
    'Content-Type': artifact.mediaType,
    'Content-Length': String(artifact.byteLength),
    'Content-Disposition': `attachment; filename="${artifact.filename}"`,
    'Cache-Control': 'private, no-store',
    'X-Content-Type-Options': 'nosniff',
    'X-Artifact-Id': artifact.artifactId,
    'X-Content-SHA256': artifact.sha256,
  })
  response.end(artifact.content)
}

export function createCountryOutageAgentHttpHandler(
  options: CountryOutageAgentHttpHandlerOptions,
): RequestListener {
  if (
    (options.application && options.manager) ||
    (!options.application && !options.manager)
  ) {
    throw new TypeError('必须且只能提供一个国家中断 HTTP application')
  }
  const application = options.application ?? options.manager!
  const basePath = (options.basePath ?? BASE_PATH).replace(/\/+$/, '')
  const heartbeatMs = Math.max(5_000, options.sseHeartbeatMs ?? 15_000)

  return (request, response): void => {
    void (async () => {
      const url = new URL(request.url ?? '/', 'http://sidecar.invalid')
      const pathname = url.pathname

      if (
        options.chatService
        && pathname === `${basePath}/chat/conversations`
      ) {
        if (request.method !== 'POST') {
          methodNotAllowed(response, 'POST')
          return
        }
        const principal = await authenticate(options, request)
        const body = createReportBody(request, await readJson(request))
        const result = await options.chatService.createConversation(
          principal,
          body,
        )
        writeJson(response, result.deduplicated ? 200 : 201, result)
        return
      }

      const chatConversationMatch = options.chatService
        ? pathname.match(
            new RegExp(`^${basePath}/chat/conversations/([^/]+)$`),
          )
        : null
      if (chatConversationMatch) {
        if (request.method !== 'GET') {
          methodNotAllowed(response, 'GET')
          return
        }
        const principal = await authenticate(options, request)
        const conversationId = decodeURIComponent(chatConversationMatch[1]!)
        const conversation = await options.chatService!.getConversation(
          principal,
          conversationId,
        )
        writeJson(response, 200, { conversation })
        return
      }

      const chatTurnsMatch = options.chatService
        ? pathname.match(
            new RegExp(`^${basePath}/chat/conversations/([^/]+)/turns$`),
          )
        : null
      if (chatTurnsMatch) {
        if (request.method !== 'POST') {
          methodNotAllowed(response, 'POST')
          return
        }
        const principal = await authenticate(options, request)
        const conversationId = decodeURIComponent(chatTurnsMatch[1]!)
        const body = createChatTurnBody(request, await readJson(request))
        const result = await options.chatService!.createTurn(
          principal,
          conversationId,
          body,
        )
        writeJson(response, result.deduplicated ? 200 : 201, result)
        return
      }

      const chatRebindMatch = options.chatService
        ? pathname.match(
            new RegExp(`^${basePath}/chat/conversations/([^/]+)/rebind$`),
          )
        : null
      if (chatRebindMatch) {
        if (request.method !== 'POST') {
          methodNotAllowed(response, 'POST')
          return
        }
        const principal = await authenticate(options, request)
        const conversationId = decodeURIComponent(chatRebindMatch[1]!)
        const body = createReportBody(request, await readJson(request))
        const result = await options.chatService!.rebind(
          principal,
          conversationId,
          body,
        )
        writeJson(response, 200, result)
        return
      }

      const chatCancelMatch = options.chatService
        ? pathname.match(
            new RegExp(
              `^${basePath}/chat/conversations/([^/]+)/turns/([^/]+)/cancel$`,
            ),
          )
        : null
      if (chatCancelMatch) {
        if (request.method !== 'POST') {
          methodNotAllowed(response, 'POST')
          return
        }
        const principal = await authenticate(options, request)
        const body = objectBody(await readJson(request))
        exactKeys(body, [])
        const result = await options.chatService!.cancelTurn(
          principal,
          decodeURIComponent(chatCancelMatch[1]!),
          decodeURIComponent(chatCancelMatch[2]!),
        )
        writeJson(response, 200, result)
        return
      }

      if (
        pathname ===
        `${basePath}/capabilities/external-evidence`
      ) {
        if (request.method !== 'GET') {
          methodNotAllowed(response, 'GET')
          return
        }
        await authenticate(options, request)
        const readiness =
          'getExternalEvidenceReadiness' in application
            ? application.getExternalEvidenceReadiness()
            : {
                schema_version:
                  'country_outage_external_evidence_capability_v1',
                capability: 'external_evidence',
                state: 'not_configured',
                provider: 'disabled',
                checked_at: new Date().toISOString(),
                policy: null,
                reason_code: 'external_evidence_not_configured',
              }
        writeJson(response, 200, readiness)
        return
      }

      if (pathname === `${basePath}/reports`) {
        if (request.method !== 'POST') {
          methodNotAllowed(response, 'POST')
          return
        }
        const principal = await authenticate(options, request)
        const body = createReportBody(request, await readJson(request))
        const result = await application.createReport(principal, body)
        writeJson(response, result.deduplicated ? 200 : 202, result)
        return
      }

      const eventsMatch = pathname.match(
        new RegExp(`^${basePath}/reports/([^/]+)/events$`),
      )
      if (eventsMatch) {
        if (request.method !== 'GET') {
          methodNotAllowed(response, 'GET')
          return
        }
        const reportId = decodeURIComponent(eventsMatch[1]!)
        const principal = await authenticate(options, request)
        const afterEventId = parseAfterEventId(request, url)
        let closed = false
        let heartbeat: ReturnType<typeof setInterval> | undefined
        let subscription:
          | Awaited<
              ReturnType<CountryOutageSessionManager['subscribe']>
            >
          | undefined
        const close = (): void => {
          if (closed) return
          closed = true
          if (subscription) subscription.close()
          if (heartbeat) clearInterval(heartbeat)
          if (!response.writableEnded) response.end()
        }
        subscription = await application.subscribe(
          principal,
          reportId,
          afterEventId,
          (event) => {
            if (closed) return
            writeSseEvent(response, event)
            if (
              event.event_type === 'session_notice' &&
              event.phase === 'session_expired'
            ) {
              close()
            }
          },
        )
        response.writeHead(200, {
          'Content-Type': 'text/event-stream; charset=utf-8',
          'Cache-Control': 'no-store, no-transform',
          Connection: 'keep-alive',
          'X-Accel-Buffering': 'no',
          'X-Content-Type-Options': 'nosniff',
        })
        response.write('retry: 3000\n\n')
        heartbeat = setInterval(() => {
          if (!closed) response.write(': heartbeat\n\n')
        }, heartbeatMs)
        heartbeat.unref()
        request.once('close', close)
        for (const event of subscription.replay) {
          writeSseEvent(response, event)
        }
        subscription.activate()
        return
      }

      const questionsMatch = pathname.match(
        new RegExp(`^${basePath}/reports/([^/]+)/questions$`),
      )
      if (questionsMatch) {
        if (request.method !== 'POST') {
          methodNotAllowed(response, 'POST')
          return
        }
        const reportId = decodeURIComponent(questionsMatch[1]!)
        const principal = await authenticate(options, request)
        const body = createQuestionBody(request, await readJson(request))
        const result =
          body.evidence_mode === 'domeye_plus_external'
            ? 'getExternalEvidenceReadiness' in application
              ? await application.createQuestion(
                  principal,
                  reportId,
                  body,
                )
              : (() => {
                  throw new CountryOutageHttpError(
                    409,
                    'external_evidence_not_configured',
                    '当前环境未配置外部证据能力',
                  )
                })()
            : await application.createQuestion(
                principal,
                reportId,
                body,
              )
        writeJson(response, result.deduplicated ? 200 : 202, result)
        return
      }

      const abortMatch = pathname.match(
        new RegExp(`^${basePath}/runs/([^/]+)/abort$`),
      )
      if (abortMatch) {
        if (request.method !== 'POST') {
          methodNotAllowed(response, 'POST')
          return
        }
        const runId = decodeURIComponent(abortMatch[1]!)
        const principal = await authenticate(options, request)
        const body = objectBody(await readJson(request))
        exactKeys(body, [])
        const result = await application.abortRun(principal, runId)
        writeJson(response, 200, result)
        return
      }

      const externalAppendixArtifactMatch = pathname.match(
        new RegExp(
          `^${basePath}/reports/([^/]+)/questions/([^/]+)/artifacts/external-appendix$`,
        ),
      )
      if (externalAppendixArtifactMatch) {
        if (request.method !== 'GET') {
          methodNotAllowed(response, 'GET')
          return
        }
        const reportId = decodeURIComponent(
          externalAppendixArtifactMatch[1]!,
        )
        const questionId = decodeURIComponent(
          externalAppendixArtifactMatch[2]!,
        )
        const principal = await authenticate(options, request)
        if (!('getExternalAppendixArtifact' in application)) {
          throw new CountryOutageHttpError(
            409,
            'external_evidence_not_configured',
            '当前环境未配置外部证据能力',
          )
        }
        const artifact = await application.getExternalAppendixArtifact(
          principal,
          reportId,
          questionId,
        )
        writeDownloadArtifact(response, artifact)
        return
      }

      const artifactMatch = pathname.match(
        new RegExp(
          `^${basePath}/reports/([^/]+)/artifacts/(markdown|pdf)$`,
        ),
      )
      if (artifactMatch) {
        if (request.method !== 'GET') {
          methodNotAllowed(response, 'GET')
          return
        }
        const reportId = decodeURIComponent(artifactMatch[1]!)
        const format = artifactMatch[2] as 'markdown' | 'pdf'
        const principal = await authenticate(options, request)
        const artifact = await application.getArtifact(
          principal,
          reportId,
          format,
        )
        writeDownloadArtifact(response, artifact)
        return
      }

      writeJson(response, 404, {
        error: {
          code: 'route_not_found',
          message: '接口不存在',
          retryable: false,
        },
      })
    })().catch((error: unknown) => {
      if (!response.headersSent) writeError(response, error)
      else if (!response.writableEnded) response.end()
    })
  }
}
