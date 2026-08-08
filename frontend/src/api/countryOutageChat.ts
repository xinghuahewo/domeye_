import {
  CountryOutageAgentRequestError,
  resolveCountryOutageAgentBase,
} from './countryOutageAgent'

export type P1Answerability =
  | 'answerable'
  | 'partial'
  | 'clarify'
  | 'unsupported'
  | 'invalid_data'

export interface P1ChatBinding {
  incident_id: string
  legacy_reference: string
  publication_id: string
  revision: number
  collector_id: 'rrc25'
  cohort_id: string
  country_code: string
  window_start_utc: string
  window_end_utc: string
  data_through: string | null
  is_final_in_data_range: boolean
  lifecycle_state: string
}
export interface P1ChatEvidence {
  evidence_ref: string
  source: string
  label: string
  value: string | number | boolean | null
  unit: string | null
  observed_at_utc: string | null
  publication_id: string
  revision: number
}

export interface P1ChatSubanswer {
  subrequest_id: string
  intents: string[]
  operator: string | null
  answerability: P1Answerability
  text: string
  evidence_refs: string[]
  limitations: string[]
  unknowns: string[]
}

export interface P1ChatAnswer {
  schema_version: 'country_outage_p1_chat_v1'
  conversation_id: string
  turn_id: string
  turn_number: number
  answerability: P1Answerability
  binding: P1ChatBinding
  p0_case_set_revision: string
  p1_contract_revision: string
  results: P1ChatSubanswer[]
  answer_text: string
  evidence: P1ChatEvidence[]
  limitations: string[]
  unknowns: string[]
  transition: {
    inherit: string[]
    set: Record<string, string | number | null>
    clear: string[]
    reason_codes: string[]
  }
  validation: {
    passed: boolean
    errors: string[]
    checked_evidence_refs: string[]
  }
  completed_at: string
}

export interface P1ChatTurn {
  turn_id: string
  turn_number: number
  question: string
  state: 'queued' | 'understanding' | 'reading_facts' | 'validating' | 'completed' | 'failed' | 'cancelled'
  answer?: P1ChatAnswer
  error?: { code: string, message: string, retryable: boolean, next_action?: string }
  created_at: string
  completed_at?: string
}

export interface P1ChatConversation {
  schema_version: 'country_outage_p1_chat_v1'
  conversation_id: string
  binding: P1ChatBinding
  state: {
    topic: string | null
    asn: number | null
    address_family: 'ipv4' | 'ipv6' | 'both' | null
    metric: string | null
    evidence_anchor: string | null
    pending_clarification: string | null
    last_committed_turn_number: number
  }
  turns: P1ChatTurn[]
  expires_at: string
  reminder_at: string
  created_at: string
}

export interface P1ChatEvent {
  schema_version: 'country_outage_p1_chat_v1'
  event_id: number
  event_type: 'conversation_ready' | 'turn_state' | 'turn_answer' | 'session_notice'
  conversation_id: string
  turn_id?: string
  state?: P1ChatTurn['state']
  answer?: P1ChatAnswer
  phase?: 'session_expiring' | 'session_expired'
  emitted_at: string
}

function basePath(): string {
  return `${resolveCountryOutageAgentBase(import.meta.env.VITE_API_URL)}chat/`
}

function errorFromPayload(status: number, value: unknown): Error {
  if (value && typeof value === 'object' && 'error' in value) {
    const detail = value.error
    if (detail && typeof detail === 'object' && 'message' in detail && typeof detail.message === 'string') {
      return new CountryOutageAgentRequestError({
        code: 'code' in detail && typeof detail.code === 'string' ? detail.code : `HTTP_${status}`,
        message: detail.message,
        retryable: 'retryable' in detail && detail.retryable === true,
        ...('next_action' in detail && typeof detail.next_action === 'string'
          ? { next_action: detail.next_action }
          : {}),
      })
    }
  }
  return new Error(`P1 聊天请求失败（HTTP ${status}）`)
}

async function jsonRequest<T>(
  path: string,
  method: 'GET' | 'POST',
  body?: object,
  signal?: AbortSignal,
): Promise<T> {
  const response = await fetch(`${basePath()}${path}`, {
    method,
    credentials: 'same-origin',
    headers: {
      Accept: 'application/json',
      ...(body ? { 'Content-Type': 'application/json' } : {}),
      ...(body && 'idempotency_key' in body && typeof body.idempotency_key === 'string'
        ? { 'Idempotency-Key': body.idempotency_key }
        : {}),
    },
    ...(body ? { body: JSON.stringify(body) } : {}),
    signal,
  })
  let payload: unknown = null
  try {
    payload = await response.json()
  } catch {
    // 由下面的 HTTP/协议错误统一处理。
  }
  if (!response.ok) throw errorFromPayload(response.status, payload)
  if (!payload || typeof payload !== 'object') throw new Error('P1 聊天响应格式无效')
  return payload as T
}

export const countryOutageChatApi = {
  async createConversation(
    request: {
      event_reference: string
      publication_id: string
      revision: number
      idempotency_key: string
    },
    signal?: AbortSignal,
  ): Promise<{ conversation: P1ChatConversation, deduplicated: boolean }> {
    return jsonRequest('conversations', 'POST', request, signal)
  },
  async getConversation(conversationId: string, signal?: AbortSignal): Promise<P1ChatConversation> {
    return jsonRequest(`conversations/${encodeURIComponent(conversationId)}`, 'GET', undefined, signal)
  },
  async createTurn(
    conversationId: string,
    request: { question: string, idempotency_key: string },
    signal?: AbortSignal,
  ): Promise<{ turn: P1ChatTurn, deduplicated: boolean }> {
    return jsonRequest(`conversations/${encodeURIComponent(conversationId)}/turns`, 'POST', request, signal)
  },
  async cancelTurn(conversationId: string, turnId: string, signal?: AbortSignal) {
    return jsonRequest<{ turn_id: string, state: P1ChatTurn['state'] }>(
      `conversations/${encodeURIComponent(conversationId)}/turns/${encodeURIComponent(turnId)}/cancel`,
      'POST', {}, signal,
    )
  },
  subscribe(
    conversationId: string,
    callbacks: {
      onEvent(event: P1ChatEvent): void
      onConnectionChange?(state: 'connected' | 'retrying'): void
      onProtocolError?(message: string): void
    },
  ) {
    const source = new EventSource(
      `${basePath()}conversations/${encodeURIComponent(conversationId)}/events`,
      { withCredentials: true },
    )
    const receive = (message: MessageEvent<string>) => {
      try {
        const event = JSON.parse(message.data) as P1ChatEvent
        if (
          event.schema_version !== 'country_outage_p1_chat_v1'
          || event.conversation_id !== conversationId
        ) {
          callbacks.onProtocolError?.('收到与当前会话身份不一致的事件')
          return
        }
        callbacks.onEvent(event)
      } catch {
        callbacks.onProtocolError?.('聊天状态事件格式无效')
      }
    }
    source.onopen = () => callbacks.onConnectionChange?.('connected')
    source.onerror = () => callbacks.onConnectionChange?.('retrying')
    for (const name of ['conversation_ready', 'turn_state', 'turn_answer', 'session_notice']) {
      source.addEventListener(name, receive as EventListener)
    }
    return { close: () => source.close() }
  },
}

export function createP1IdempotencyKey(prefix: 'conversation' | 'turn'): string {
  return `${prefix}-${crypto.randomUUID()}`
}
