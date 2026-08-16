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

export interface P1RuntimeV2Evidence {
  evidence_ref: string
  source: 'resolution' | 'overview' | 'series' | 'asns' | 'paths' | 'audit' | 'derived'
  field_path: string
  value: string | number | boolean | null
  unit: string | null
  observed_at_utc: string | null
  incident_id: string
  publication_id: string
  revision: number
  collector_id: 'rrc25'
}

export interface P1RuntimeV2SingleTurnAnswer {
  schema_version: 'country_outage_p1_single_turn_v2'
  answerability: 'partial'
  binding: P1ChatBinding
  answer_text: string
  evidence: P1RuntimeV2Evidence[]
  limitations: string[]
  unknowns: string[]
  execution_trace: {
    nodes: Array<{
      node_id: string
      execution_unit: 'TOOL-01' | 'TOOL-02'
      capability_ids: string[]
      status: 'passed'
      evidence_refs: string[]
    }>
    authorization: {
      original_scope: string
      effective_permission: 'country_outage:read'
      basis: 'canonical_read' | 'event_read_global' | 'event_read_country'
      country_code: string
    }
    model_generated_fact_count: 0
    state_commit: 'none'
  }
  validation: {
    passed: true
    checked_identity_fields: string[]
    checked_evidence_refs: string[]
    errors: []
  }
  runtime_identity: {
    implementation: 'p1-runtime-v2-single-turn'
    contract_revision: string
    language_layer: 'controlled-s1-entry'
    collector: 'rrc25'
  }
  completed_at: string
}

export type P1SemanticAnswerability =
  | 'supported'
  | 'partial'
  | 'clarify'
  | 'unsupported'
  | 'invalid_data'

export interface P1RuntimeV2SemanticAnswer {
  schema_version: 'country_outage_p1_semantic_turn_v2'
  answerability: P1SemanticAnswerability
  binding: P1ChatBinding
  semantic_plan: {
    schema_version: 'country_outage_p1_semantic_plan_v2'
    user_goal_plan: {
      plan_revision: 'user-goal-plan-v2'
      original_question: string
      goals: Array<{
        goal_id: string
        requested_goal: string
        normalized_kind: string
        entities: Record<string, string | number | boolean | null>
        references: string[]
        ambiguity: 'none' | 'non_blocking' | 'blocking'
        context_dependencies: string[]
      }>
      planner_identity: string
      confidence: number
    }
    grounding_plan: {
      plan_revision: 'grounding-plan-v2'
      decisions: Array<{
        goal_id: string
        answerability: P1SemanticAnswerability
        node_ids: string[]
        reason_codes: string[]
      }>
      nodes: Array<{
        node_id: string
        execution_unit: string
        capability_ids: string[]
      }>
      authorization_scope: ['country_outage:read']
      validation: { status: 'passed', errors: [] }
    }
  }
  results: Array<{
    goal_id: string
    requested_goal: string
    normalized_kind: string
    answerability: P1SemanticAnswerability
    text: string
    evidence_refs: string[]
    limitations: string[]
  }>
  answer_text: string
  evidence: P1RuntimeV2Evidence[]
  limitations: string[]
  unknowns: string[]
  execution_trace: {
    binding_preflight: 'passed'
    nodes: Array<{
      node_id: string
      execution_unit: string
      capability_ids: string[]
      status: 'passed'
      evidence_refs: string[]
    }>
    planner_outcome: 'accepted' | 'safe_fallback'
    model_generated_fact_count: 0
    state_commit: 'none'
  }
  validation: {
    user_goal_schema: 'passed'
    grounding_schema: 'passed'
    grounding_legality: 'passed'
    answer_evidence: 'passed'
    errors: []
  }
  runtime_identity: {
    implementation: 'p1-runtime-v2-semantic-turn'
    contract_revision: string
    language_layer: string
    collector: 'rrc25'
  }
  completed_at: string
}

export interface P1RuntimeV2DialogState {
  topic: string | null
  asn: number | null
  address_family: 'ipv4' | 'ipv6' | 'both' | null
  metric: string | null
  evidence_anchor: string | null
  pending_clarification: string | null
  last_committed_turn_number: number
}

export interface P1RuntimeV2ConversationAnswer
  extends Omit<P1RuntimeV2SemanticAnswer, 'schema_version' | 'execution_trace'> {
  schema_version: 'country_outage_p1_runtime_v2_conversation_turn_v1'
  conversation_id: string
  turn_id: string
  turn_number: number
  execution_trace: Omit<P1RuntimeV2SemanticAnswer['execution_trace'], 'nodes' | 'state_commit'> & {
    nodes: Array<Omit<P1RuntimeV2SemanticAnswer['execution_trace']['nodes'][number], 'status'> & {
      status: 'passed' | 'failed'
      receipt_id: string
      execution_mode: 'verified_evidence_state_read'
    }>
    state_commit: 'committed' | 'none'
  }
  state_receipt: {
    before: P1RuntimeV2DialogState
    proposed: {
      inherit: string[]
      set: Record<string, string | number | null>
      clear: string[]
      reason_codes: string[]
    }
    after: P1RuntimeV2DialogState
    status: 'committed' | 'none' | 'rolled_back'
    transaction_checks: {
      plan_validated: boolean
      permission_validated: boolean
      execution_validated: boolean
      evidence_validated: boolean
      binding_revalidated: boolean
      cancelled: boolean
    }
  }
}

export interface P1RuntimeV2ConversationTurn {
  turn_id: string
  turn_number: number
  question: string
  state: 'understanding' | 'executing' | 'validating' | 'completed' | 'failed' | 'cancelled'
  answer?: P1RuntimeV2ConversationAnswer
  error?: { code: string, message: string, retryable: boolean }
  created_at: string
  completed_at?: string
}

export interface P1RuntimeV2Conversation {
  schema_version: 'country_outage_p1_runtime_v2_conversation_v1'
  conversation_id: string
  binding: P1ChatBinding
  binding_generation: number
  active_binding_generation: number | null
  evidence_state: {
    immutable: true
    incident_id: string
    publication_id: string
    revision: number
    collector_id: 'rrc25'
    loaded_at: string
  }
  dialog_state: P1RuntimeV2DialogState
  turns: P1RuntimeV2ConversationTurn[]
  binding_history: Array<{
    generation: number
    incident_id: string
    publication_id: string
    revision: number
    switched_at: string
  }>
  expires_at: string
  created_at: string
}

function basePath(): string {
  return `${resolveCountryOutageAgentBase(import.meta.env.VITE_API_URL)}chat/`
}

function runtimeV2BasePath(): string {
  return `${resolveCountryOutageAgentBase(import.meta.env.VITE_API_URL)}runtime-v2/`
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
  requestBase = basePath(),
): Promise<T> {
  const response = await fetch(`${requestBase}${path}`, {
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
  async createRuntimeV2Conversation(
    request: {
      event_reference: string
      publication_id: string
      revision: number
      idempotency_key: string
    },
    signal?: AbortSignal,
  ): Promise<{ conversation: P1RuntimeV2Conversation, deduplicated: boolean }> {
    return jsonRequest('conversations', 'POST', request, signal, runtimeV2BasePath())
  },
  async getRuntimeV2Conversation(
    conversationId: string,
    signal?: AbortSignal,
  ): Promise<P1RuntimeV2Conversation> {
    return jsonRequest(
      `conversations/${encodeURIComponent(conversationId)}`,
      'GET',
      undefined,
      signal,
      runtimeV2BasePath(),
    )
  },
  async createRuntimeV2ConversationTurn(
    conversationId: string,
    request: { question: string, idempotency_key: string },
    signal?: AbortSignal,
  ): Promise<{ turn: P1RuntimeV2ConversationTurn, deduplicated: boolean }> {
    return jsonRequest(
      `conversations/${encodeURIComponent(conversationId)}/turns`,
      'POST',
      request,
      signal,
      runtimeV2BasePath(),
    )
  },
  async cancelRuntimeV2ConversationTurn(
    conversationId: string,
    turnId: string,
    signal?: AbortSignal,
  ): Promise<{ turn_id: string, state: 'cancel_requested' | 'not_active' }> {
    return jsonRequest(
      `conversations/${encodeURIComponent(conversationId)}/turns/${encodeURIComponent(turnId)}/cancel`,
      'POST',
      {},
      signal,
      runtimeV2BasePath(),
    )
  },
  async rebindRuntimeV2Conversation(
    conversationId: string,
    request: {
      event_reference: string
      publication_id: string
      revision: number
      idempotency_key: string
    },
    signal?: AbortSignal,
  ): Promise<{
    conversation: P1RuntimeV2Conversation
    previous_binding: P1ChatBinding
  }> {
    return jsonRequest(
      `conversations/${encodeURIComponent(conversationId)}/rebind`,
      'POST',
      request,
      signal,
      runtimeV2BasePath(),
    )
  },
  async createRuntimeV2SemanticTurn(
    request: {
      event_reference: string
      publication_id: string
      revision: number
      question: string
    },
    signal?: AbortSignal,
  ): Promise<P1RuntimeV2SemanticAnswer> {
    return jsonRequest(
      'semantic-turn',
      'POST',
      request,
      signal,
      runtimeV2BasePath(),
    )
  },
  async createRuntimeV2SingleTurn(
    request: {
      event_reference: string
      publication_id: string
      revision: number
      controlled_goal: 'event_summary'
    },
    signal?: AbortSignal,
  ): Promise<P1RuntimeV2SingleTurnAnswer> {
    return jsonRequest(
      'single-turn',
      'POST',
      request,
      signal,
      runtimeV2BasePath(),
    )
  },
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
