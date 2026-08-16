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
import type { CountryOutageSessionManager } from './country-outage-session-manager.js'
import { CountryOutageHttpError } from './errors.js'
import type {
  CreateP1ConversationRequest,
  CreateP1TurnRequest,
  P1ChatApplication,
  P1ChatEvent,
} from '../chat/contracts.js'
import {
  P1ReadModelError,
} from '../chat/general-read-model-provider.js'
import {
  P1RuntimeV2SingleTurnError,
  type P1RuntimeV2SingleTurnRequest,
  type P1RuntimeV2SingleTurnService,
} from '../chat/runtime-v2-single-turn.js'
import {
  P1SemanticPlanError,
  type P1RuntimeV2SemanticRequest,
  type P1RuntimeV2SemanticTurnService,
} from '../chat/runtime-v2-semantic.js'
import type {
  P1RuntimeV2ConversationService,
} from '../chat/runtime-v2-conversation.js'

const BASE_PATH = '/country-outage'
const MAX_REQUEST_BYTES = 64 * 1024

export interface CountryOutageAgentHttpHandlerOptions {
  /**
   * 新正式入口使用 application。manager 只为现有测试与旧调用方保留；
   * 两者不能同时提供。
   */
  application?: CountryOutageAgentOrchestrator
  manager?: CountryOutageSessionManager
  /** P1 事件绑定聊天是独立于报告追问的窄应用。 */
  chat?: P1ChatApplication
  /** P1 Runtime v2 S1 的无状态受控单轮垂直切片。 */
  runtimeV2SingleTurn?: P1RuntimeV2SingleTurnService
  /** P1 Runtime v2 S2 的开放 UserGoalPlan 到封闭 GroundingPlan 入口。 */
  runtimeV2SemanticTurn?: P1RuntimeV2SemanticTurnService
  /** P1 Runtime v2 S3 的事务化多轮状态与事件重绑定入口。 */
  runtimeV2Conversation?: P1RuntimeV2ConversationService
  authenticate: AuthenticateCountryOutageRequest
  basePath?: string
  sseHeartbeatMs?: number
}

function createRuntimeV2SemanticBody(
  value: unknown,
): P1RuntimeV2SemanticRequest {
  const body = objectBody(value)
  exactKeys(body, [
    'event_reference', 'publication_id', 'revision', 'question',
  ])
  const eventReference = body.event_reference
  if (
    typeof eventReference !== 'string'
    || !/^country_outage\/\d{4}-\d{2}-\d{2}[ +]\d{2}:\d{2}:\d{2}\/[A-Z]{2}\/[1-9]\d*\/r$/.test(eventReference)
  ) {
    throw new CountryOutageHttpError(
      400,
      'invalid_event_reference',
      'event_reference 不是合法 country_outage 引用',
    )
  }
  if (
    typeof body.publication_id !== 'string'
    || !body.publication_id
    || body.publication_id.length > 256
  ) {
    throw new CountryOutageHttpError(
      400,
      'invalid_publication_id',
      'publication_id 无效',
    )
  }
  if (
    typeof body.revision !== 'number'
    || !Number.isSafeInteger(body.revision)
    || body.revision < 1
  ) {
    throw new CountryOutageHttpError(
      400,
      'invalid_revision',
      'revision 必须是正整数',
    )
  }
  if (
    typeof body.question !== 'string'
    || body.question.trim().length < 1
    || body.question.length > 2_000
  ) {
    throw new CountryOutageHttpError(
      400,
      'invalid_question',
      'question 必须是 1 至 2,000 字符的非空文本',
    )
  }
  return {
    event_reference: eventReference,
    publication_id: body.publication_id,
    revision: body.revision,
    question: body.question,
  }
}

function createRuntimeV2SingleTurnBody(
  value: unknown,
): P1RuntimeV2SingleTurnRequest {
  const body = objectBody(value)
  exactKeys(body, [
    'event_reference',
    'publication_id',
    'revision',
    'controlled_goal',
  ])
  const eventReference = body.event_reference
  if (
    typeof eventReference !== 'string'
    || !/^country_outage\/\d{4}-\d{2}-\d{2}[ +]\d{2}:\d{2}:\d{2}\/[A-Z]{2}\/[1-9]\d*\/r$/.test(eventReference)
  ) {
    throw new CountryOutageHttpError(
      400,
      'invalid_event_reference',
      'event_reference 不是合法 country_outage 引用',
    )
  }
  if (
    typeof body.publication_id !== 'string'
    || !body.publication_id
    || body.publication_id.length > 256
  ) {
    throw new CountryOutageHttpError(
      400,
      'invalid_publication_id',
      'publication_id 无效',
    )
  }
  if (
    typeof body.revision !== 'number'
    || !Number.isSafeInteger(body.revision)
    || body.revision < 1
  ) {
    throw new CountryOutageHttpError(
      400,
      'invalid_revision',
      'revision 必须是正整数',
    )
  }
  if (body.controlled_goal !== 'event_summary') {
    throw new CountryOutageHttpError(
      400,
      'unsupported_goal',
      'S1 受控入口只接受 event_summary',
    )
  }
  return {
    event_reference: eventReference,
    publication_id: body.publication_id,
    revision: body.revision,
    controlled_goal: 'event_summary',
  }
}

function runtimeV2HttpError(error: unknown): CountryOutageHttpError {
  if (error instanceof CountryOutageHttpError) return error
  if (
    !(error instanceof P1RuntimeV2SingleTurnError)
    && !(error instanceof P1SemanticPlanError)
    && !(error instanceof P1ReadModelError)
  ) {
    return new CountryOutageHttpError(
      500,
      'runtime_v2_internal_error',
      'P1 Runtime v2 单轮请求处理失败',
    )
  }
  const statusByCode: Record<string, number> = {
    permission_denied: 403,
    invalid_reference: 400,
    unsupported_goal: 400,
    binding_conflict: 409,
    publication_identity_conflict: 409,
    lifecycle_identity_conflict: 409,
    unsupported_event: 409,
    capability_unavailable: 409,
    evidence_not_found: 404,
    invalid_data: 422,
    data_api_unavailable: 503,
    tool_timeout: 504,
    cancelled: 499,
    invalid_question: 400,
    grounding_plan_rejected: 422,
    grounding_plan_schema_invalid: 422,
    contract_invalid: 503,
    invalid_idempotency_key: 400,
    idempotency_conflict: 409,
    revision_drift: 409,
    conversation_expired: 410,
  }
  return new CountryOutageHttpError(
    statusByCode[error.code] ?? 500,
    error.code,
    error.message,
    error.retryable,
  )
}

function createP1ConversationBody(
  request: IncomingMessage,
  value: unknown,
): CreateP1ConversationRequest {
  const body = objectBody(value)
  exactKeys(body, [
    'event_reference',
    'publication_id',
    'revision',
    'idempotency_key',
  ])
  const eventReference = body.event_reference
  if (
    typeof eventReference !== 'string' ||
    !/^country_outage\/\d{4}-\d{2}-\d{2}[ +]\d{2}:\d{2}:\d{2}\/[A-Z]{2}\/[1-9]\d*\/r$/.test(eventReference)
  ) {
    throw new CountryOutageHttpError(
      400,
      'invalid_event_reference',
      'event_reference 不是合法 country_outage 引用',
    )
  }
  if (
    typeof body.publication_id !== 'string' ||
    body.publication_id.length < 1 ||
    body.publication_id.length > 256
  ) {
    throw new CountryOutageHttpError(400, 'invalid_publication_id', 'publication_id 无效')
  }
  if (
    typeof body.revision !== 'number' ||
    !Number.isSafeInteger(body.revision) ||
    body.revision < 1
  ) {
    throw new CountryOutageHttpError(400, 'invalid_revision', 'revision 必须是正整数')
  }
  return {
    event_reference: eventReference,
    publication_id: body.publication_id,
    revision: body.revision,
    idempotency_key: idempotencyKey(request, body),
  }
}

function createP1TurnBody(
  request: IncomingMessage,
  value: unknown,
): CreateP1TurnRequest {
  const body = objectBody(value)
  exactKeys(body, ['question', 'idempotency_key'])
  if (
    typeof body.question !== 'string' ||
    body.question.trim().length < 1 ||
    body.question.length > 2_000
  ) {
    throw new CountryOutageHttpError(
      400,
      'invalid_question',
      'question 必须是 1 至 2,000 字符的非空文本',
    )
  }
  return {
    question: body.question.trim(),
    idempotency_key: idempotencyKey(request, body),
  }
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

function writeP1SseEvent(
  response: ServerResponse,
  event: P1ChatEvent,
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

      if (pathname === `${basePath}/runtime-v2/conversations`) {
        if (!options.runtimeV2Conversation) {
          throw new CountryOutageHttpError(
            503,
            'p1_runtime_v2_conversation_not_configured',
            'P1 Runtime v2 多轮会话尚未配置',
          )
        }
        if (request.method !== 'POST') {
          methodNotAllowed(response, 'POST')
          return
        }
        const principal = await authenticate(options, request)
        const body = createP1ConversationBody(request, await readJson(request))
        const controller = new AbortController()
        const abort = (): void => controller.abort()
        request.once('aborted', abort)
        response.once('close', () => {
          if (!response.writableEnded) abort()
        })
        try {
          const result = await options.runtimeV2Conversation.createConversation(
            principal,
            body,
            controller.signal,
          )
          writeJson(response, result.deduplicated ? 200 : 201, result)
        } catch (error) {
          throw runtimeV2HttpError(error)
        } finally {
          request.removeListener('aborted', abort)
        }
        return
      }

      const runtimeV2ConversationMatch = pathname.match(
        new RegExp(`^${basePath}/runtime-v2/conversations/([^/]+)$`),
      )
      if (runtimeV2ConversationMatch) {
        if (!options.runtimeV2Conversation) {
          throw new CountryOutageHttpError(
            503,
            'p1_runtime_v2_conversation_not_configured',
            'P1 Runtime v2 多轮会话尚未配置',
          )
        }
        if (request.method !== 'GET') {
          methodNotAllowed(response, 'GET')
          return
        }
        const principal = await authenticate(options, request)
        const result = await options.runtimeV2Conversation.getConversation(
          principal,
          decodeURIComponent(runtimeV2ConversationMatch[1]!),
        )
        writeJson(response, 200, result)
        return
      }

      const runtimeV2TurnsMatch = pathname.match(
        new RegExp(`^${basePath}/runtime-v2/conversations/([^/]+)/turns$`),
      )
      if (runtimeV2TurnsMatch) {
        if (!options.runtimeV2Conversation) {
          throw new CountryOutageHttpError(
            503,
            'p1_runtime_v2_conversation_not_configured',
            'P1 Runtime v2 多轮会话尚未配置',
          )
        }
        if (request.method !== 'POST') {
          methodNotAllowed(response, 'POST')
          return
        }
        const principal = await authenticate(options, request)
        const body = createP1TurnBody(request, await readJson(request))
        const controller = new AbortController()
        const abort = (): void => controller.abort()
        request.once('aborted', abort)
        response.once('close', () => {
          if (!response.writableEnded) abort()
        })
        try {
          const result = await options.runtimeV2Conversation.createTurn(
            principal,
            decodeURIComponent(runtimeV2TurnsMatch[1]!),
            body,
            controller.signal,
          )
          writeJson(response, result.deduplicated ? 200 : 201, result)
        } catch (error) {
          throw runtimeV2HttpError(error)
        } finally {
          request.removeListener('aborted', abort)
        }
        return
      }

      const runtimeV2CancelMatch = pathname.match(
        new RegExp(
          `^${basePath}/runtime-v2/conversations/([^/]+)/turns/([^/]+)/cancel$`,
        ),
      )
      if (runtimeV2CancelMatch) {
        if (!options.runtimeV2Conversation) {
          throw new CountryOutageHttpError(
            503,
            'p1_runtime_v2_conversation_not_configured',
            'P1 Runtime v2 多轮会话尚未配置',
          )
        }
        if (request.method !== 'POST') {
          methodNotAllowed(response, 'POST')
          return
        }
        const body = objectBody(await readJson(request))
        exactKeys(body, [])
        const principal = await authenticate(options, request)
        const result = await options.runtimeV2Conversation.cancelTurn(
          principal,
          decodeURIComponent(runtimeV2CancelMatch[1]!),
          decodeURIComponent(runtimeV2CancelMatch[2]!),
        )
        writeJson(response, 200, result)
        return
      }

      const runtimeV2RebindMatch = pathname.match(
        new RegExp(`^${basePath}/runtime-v2/conversations/([^/]+)/rebind$`),
      )
      if (runtimeV2RebindMatch) {
        if (!options.runtimeV2Conversation) {
          throw new CountryOutageHttpError(
            503,
            'p1_runtime_v2_conversation_not_configured',
            'P1 Runtime v2 多轮会话尚未配置',
          )
        }
        if (request.method !== 'POST') {
          methodNotAllowed(response, 'POST')
          return
        }
        const principal = await authenticate(options, request)
        const body = createP1ConversationBody(request, await readJson(request))
        const controller = new AbortController()
        const abort = (): void => controller.abort()
        request.once('aborted', abort)
        response.once('close', () => {
          if (!response.writableEnded) abort()
        })
        try {
          const result = await options.runtimeV2Conversation.rebind(
            principal,
            decodeURIComponent(runtimeV2RebindMatch[1]!),
            body,
            controller.signal,
          )
          writeJson(response, 200, result)
        } catch (error) {
          throw runtimeV2HttpError(error)
        } finally {
          request.removeListener('aborted', abort)
        }
        return
      }

      if (pathname === `${basePath}/runtime-v2/semantic-turn`) {
        if (!options.runtimeV2SemanticTurn) {
          throw new CountryOutageHttpError(
            503,
            'p1_runtime_v2_semantic_not_configured',
            'P1 Runtime v2 语义模型候选尚未配置',
          )
        }
        if (request.method !== 'POST') {
          methodNotAllowed(response, 'POST')
          return
        }
        const principal = await authenticate(options, request)
        const body = createRuntimeV2SemanticBody(await readJson(request))
        const controller = new AbortController()
        const abort = (): void => controller.abort()
        request.once('aborted', abort)
        response.once('close', () => {
          if (!response.writableEnded) abort()
        })
        try {
          const result = await options.runtimeV2SemanticTurn.answer(
            principal,
            body,
            controller.signal,
          )
          writeJson(response, 200, result)
        } catch (error) {
          throw runtimeV2HttpError(error)
        } finally {
          request.removeListener('aborted', abort)
        }
        return
      }

      if (pathname === `${basePath}/runtime-v2/single-turn`) {
        if (!options.runtimeV2SingleTurn) {
          throw new CountryOutageHttpError(
            503,
            'p1_runtime_v2_not_configured',
            'P1 Runtime v2 单轮能力尚未配置',
          )
        }
        if (request.method !== 'POST') {
          methodNotAllowed(response, 'POST')
          return
        }
        const principal = await authenticate(options, request)
        const body = createRuntimeV2SingleTurnBody(await readJson(request))
        const controller = new AbortController()
        const abort = (): void => controller.abort()
        request.once('aborted', abort)
        response.once('close', () => {
          if (!response.writableEnded) abort()
        })
        try {
          const result = await options.runtimeV2SingleTurn.answer(
            principal,
            body,
            controller.signal,
          )
          writeJson(response, 200, result)
        } catch (error) {
          throw runtimeV2HttpError(error)
        } finally {
          request.removeListener('aborted', abort)
        }
        return
      }

      if (pathname === `${basePath}/chat/conversations`) {
        if (!options.chat) {
          throw new CountryOutageHttpError(503, 'p1_chat_not_configured', 'P1 事件聊天尚未配置')
        }
        if (request.method !== 'POST') {
          methodNotAllowed(response, 'POST')
          return
        }
        const principal = await authenticate(options, request)
        const body = createP1ConversationBody(request, await readJson(request))
        const result = await options.chat.createConversation(principal, body)
        writeJson(response, result.deduplicated ? 200 : 201, result)
        return
      }

      const chatConversationMatch = pathname.match(
        new RegExp(`^${basePath}/chat/conversations/([^/]+)$`),
      )
      if (chatConversationMatch) {
        if (!options.chat) {
          throw new CountryOutageHttpError(503, 'p1_chat_not_configured', 'P1 事件聊天尚未配置')
        }
        if (request.method !== 'GET') {
          methodNotAllowed(response, 'GET')
          return
        }
        const principal = await authenticate(options, request)
        const result = await options.chat.getConversation(
          principal,
          decodeURIComponent(chatConversationMatch[1]!),
        )
        writeJson(response, 200, result)
        return
      }

      const chatTurnsMatch = pathname.match(
        new RegExp(`^${basePath}/chat/conversations/([^/]+)/turns$`),
      )
      if (chatTurnsMatch) {
        if (!options.chat) {
          throw new CountryOutageHttpError(503, 'p1_chat_not_configured', 'P1 事件聊天尚未配置')
        }
        if (request.method !== 'POST') {
          methodNotAllowed(response, 'POST')
          return
        }
        const principal = await authenticate(options, request)
        const body = createP1TurnBody(request, await readJson(request))
        const result = await options.chat.createTurn(
          principal,
          decodeURIComponent(chatTurnsMatch[1]!),
          body,
        )
        writeJson(response, result.deduplicated ? 200 : 201, result)
        return
      }

      const chatCancelMatch = pathname.match(
        new RegExp(`^${basePath}/chat/conversations/([^/]+)/turns/([^/]+)/cancel$`),
      )
      if (chatCancelMatch) {
        if (!options.chat) {
          throw new CountryOutageHttpError(503, 'p1_chat_not_configured', 'P1 事件聊天尚未配置')
        }
        if (request.method !== 'POST') {
          methodNotAllowed(response, 'POST')
          return
        }
        const body = objectBody(await readJson(request))
        exactKeys(body, [])
        const principal = await authenticate(options, request)
        const result = await options.chat.cancelTurn(
          principal,
          decodeURIComponent(chatCancelMatch[1]!),
          decodeURIComponent(chatCancelMatch[2]!),
        )
        writeJson(response, 200, result)
        return
      }

      const chatRebindMatch = pathname.match(
        new RegExp(`^${basePath}/chat/conversations/([^/]+)/rebind$`),
      )
      if (chatRebindMatch) {
        if (!options.chat) {
          throw new CountryOutageHttpError(503, 'p1_chat_not_configured', 'P1 事件聊天尚未配置')
        }
        if (request.method !== 'POST') {
          methodNotAllowed(response, 'POST')
          return
        }
        const principal = await authenticate(options, request)
        const body = createP1ConversationBody(request, await readJson(request))
        const result = await options.chat.rebind(
          principal,
          decodeURIComponent(chatRebindMatch[1]!),
          body,
        )
        writeJson(response, 201, result)
        return
      }

      const chatEventsMatch = pathname.match(
        new RegExp(`^${basePath}/chat/conversations/([^/]+)/events$`),
      )
      if (chatEventsMatch) {
        if (!options.chat) {
          throw new CountryOutageHttpError(503, 'p1_chat_not_configured', 'P1 事件聊天尚未配置')
        }
        if (request.method !== 'GET') {
          methodNotAllowed(response, 'GET')
          return
        }
        const principal = await authenticate(options, request)
        const conversationId = decodeURIComponent(chatEventsMatch[1]!)
        const afterEventId = parseAfterEventId(request, url)
        let closed = false
        let heartbeat: ReturnType<typeof setInterval> | undefined
        const subscription = await options.chat.subscribe(
          principal,
          conversationId,
          afterEventId,
          (event) => {
            if (!closed) writeP1SseEvent(response, event)
          },
        )
        const close = (): void => {
          if (closed) return
          closed = true
          subscription.close()
          if (heartbeat) clearInterval(heartbeat)
          if (!response.writableEnded) response.end()
        }
        response.writeHead(200, {
          'Content-Type': 'text/event-stream; charset=utf-8',
          'Cache-Control': 'no-store, no-transform',
          Connection: 'keep-alive',
          'X-Accel-Buffering': 'no',
          'X-Content-Type-Options': 'nosniff',
        })
        response.write('retry: 3000\n\n')
        for (const event of subscription.replay) writeP1SseEvent(response, event)
        subscription.activate()
        heartbeat = setInterval(() => {
          if (!closed) response.write(': heartbeat\n\n')
        }, heartbeatMs)
        heartbeat.unref()
        request.once('close', close)
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
