import { resolveApiTimeout } from './client'
import type { components as OpenApiComponents } from '../types/openapi.generated'

export const COUNTRY_OUTAGE_FIRST_SLICE_QUESTION =
  '在这次冻结 publication 的观测窗口内，RRC25 看到的固定前缀可见 IPv4 地址量最低是多少，首次在什么观测时刻出现？首值、末值、最大值和极差分别是多少？' as const

function chatApiV2BaseUrl(value: string): string {
  const normalized = value.endsWith('/') ? value : `${value}/`
  if (/\/api\/v1\/$/i.test(normalized)) {
    return normalized.replace(/\/api\/v1\/$/i, '/api/v2/')
  }
  return '/api/v2/'
}

export class CountryOutageChatApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly retryable: boolean,
  ) {
    super(message)
    this.name = 'CountryOutageChatApiError'
  }
}

async function chatApiV2Request<T>(
  path: string,
  method: 'GET' | 'POST',
  data?: unknown,
  headers: Record<string, string> = {},
): Promise<T> {
  const response = await fetch(
    `${chatApiV2BaseUrl(import.meta.env.VITE_API_URL || '/api/v1/')}${path}`,
    {
      method,
      headers: {
        Accept: 'application/json',
        ...(method === 'POST' ? { 'Content-Type': 'application/json' } : {}),
        ...headers,
      },
      ...(method === 'POST' ? { body: JSON.stringify(data) } : {}),
      signal: AbortSignal.timeout(resolveApiTimeout(import.meta.env.VITE_API_TIMEOUT_MS)),
    },
  )
  if (!response.ok) {
    let body: unknown
    try {
      body = await response.json()
    } catch {
      body = null
    }
    const error = body && typeof body === 'object'
      ? (body as { error?: unknown }).error
      : null
    const detail = error && typeof error === 'object'
      ? error as { code?: unknown; message?: unknown; retryable?: unknown }
      : null
    throw new CountryOutageChatApiError(
      response.status,
      typeof detail?.code === 'string' ? detail.code : 'chat_request_failed',
      typeof detail?.message === 'string'
        ? detail.message
        : `国家中断 Agent 请求失败：HTTP ${response.status}`,
      detail?.retryable === true,
    )
  }
  return await response.json() as T
}

async function chatApiV2Post<T>(
  path: string,
  data: unknown,
  headers: Record<string, string> = {},
): Promise<T> {
  return chatApiV2Request(path, 'POST', data, headers)
}

export type CountryOutageChatBinding = OpenApiComponents['schemas'][
  'CountryOutageInteractiveBinding'
]
export type CountryOutageChatBasis = OpenApiComponents['schemas'][
  'CountryOutageInteractiveAnswerBasis'
]
export type CountryOutageChatSuccessfulTurnAnswer = OpenApiComponents['schemas'][
  'CountryOutageInteractiveSuccessfulTurnAnswer'
]
export type CountryOutageChatClarificationTurnAnswer = OpenApiComponents['schemas'][
  'CountryOutageInteractiveClarificationTurnAnswer'
]
export type CountryOutageChatStoppedTurnAnswer = OpenApiComponents['schemas'][
  'CountryOutageInteractiveStoppedTurnAnswer'
]
export type CountryOutageChatTurnAnswer =
  | CountryOutageChatSuccessfulTurnAnswer
  | CountryOutageChatClarificationTurnAnswer
  | CountryOutageChatStoppedTurnAnswer
export type CountryOutageChatTurn = OpenApiComponents['schemas'][
  'CountryOutageInteractiveTurn'
]
export type CountryOutageChatConversation = OpenApiComponents['schemas'][
  'CountryOutageInteractiveConversation'
]
export type CountryOutageChatBindingRequest = OpenApiComponents['schemas'][
  'CountryOutageChatBindingRequest'
]

export async function createCountryOutageChatConversation(
  request: CountryOutageChatBindingRequest,
): Promise<OpenApiComponents['schemas']['CountryOutageInteractiveConversationCreateResponse']> {
  return chatApiV2Post(
    'country-outage/chat/conversations',
    request,
    { 'Idempotency-Key': request.idempotency_key },
  )
}

export async function getCountryOutageChatConversation(
  conversationId: string,
): Promise<OpenApiComponents['schemas']['CountryOutageInteractiveConversationGetResponse']> {
  return chatApiV2Request(
    `country-outage/chat/conversations/${encodeURIComponent(conversationId)}`,
    'GET',
  )
}

export async function createCountryOutageChatTurn(
  conversationId: string,
  question: string,
  idempotencyKey: string,
): Promise<OpenApiComponents['schemas']['CountryOutageInteractiveTurnCreateResponse']> {
  return chatApiV2Post(
    `country-outage/chat/conversations/${encodeURIComponent(conversationId)}/turns`,
    { question, idempotency_key: idempotencyKey },
    { 'Idempotency-Key': idempotencyKey },
  )
}

export async function cancelCountryOutageChatTurn(
  conversationId: string,
  turnId: string,
): Promise<OpenApiComponents['schemas']['CountryOutageInteractiveTurnCancelResponse']> {
  return chatApiV2Post(
    `country-outage/chat/conversations/${encodeURIComponent(conversationId)}/turns/${encodeURIComponent(turnId)}/cancel`,
    {},
  )
}
