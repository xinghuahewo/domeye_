import { apiV2Get, resolveApiTimeout } from './client'

function chatApiV2BaseUrl(value: string): string {
  const normalized = value.endsWith('/') ? value : `${value}/`
  if (/\/api\/v1\/$/i.test(normalized)) {
    return normalized.replace(/\/api\/v1\/$/i, '/api/v2/')
  }
  return '/api/v2/'
}

async function chatApiV2Post<T>(
  path: string,
  data: unknown,
  headers: Record<string, string> = {},
): Promise<T> {
  const response = await fetch(
    `${chatApiV2BaseUrl(import.meta.env.VITE_API_URL || '/api/v1/')}${path}`,
    {
      method: 'POST',
      headers: {
        Accept: 'application/json',
        'Content-Type': 'application/json',
        ...headers,
      },
      body: JSON.stringify(data),
      signal: AbortSignal.timeout(resolveApiTimeout(import.meta.env.VITE_API_TIMEOUT_MS)),
    },
  )
  if (!response.ok) {
    throw new Error(`国家中断聊天请求失败：HTTP ${response.status}`)
  }
  return await response.json() as T
}

export type CountryOutageChatAnswerability =
  | 'supported'
  | 'partial'
  | 'clarify'
  | 'unsupported'
  | 'invalid_data'

export interface CountryOutageChatBinding {
  event_type: 'country_outage'
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
  capabilities: Record<string, string>
}

export interface CountryOutageChatDialogState {
  topic: string | null
  asn: number | null
  address_family: 'ipv4' | 'ipv6' | 'both' | null
  metric: string | null
  population: 'fixed_cohort' | 'new_prefix_only' | null
  include_new_prefixes: boolean | null
  analysis_mode: string | null
  time_scope: string | null
  evidence_anchor: string | null
  pending_clarification: string | null
  last_committed_turn_number: number
}

export interface CountryOutageChatEvidenceState {
  immutable: true
  incident_id: string
  publication_id: string
  revision: number
  collector_id: 'rrc25'
  cohort_id: string
  data_through: string | null
  capabilities: Record<string, string>
  loaded_at: string
}

export interface CountryOutageChatGoal {
  goal_id: string
  requested_goal: string
  normalized_kind: string
  entities: Record<string, string | number | boolean | null>
  references: string[]
  ambiguity: 'none' | 'non_blocking' | 'blocking'
  context_dependencies: string[]
}

export interface CountryOutageChatDecision {
  goal_id: string
  answerability: CountryOutageChatAnswerability
  node_ids: string[]
  reason_codes: string[]
}

export interface CountryOutageChatNode {
  node_id: string
  goal_id: string
  execution_unit: string
  capability_ids: string[]
  inputs: Record<string, unknown>
  input_sources: Record<string, string>
  depends_on: string[]
  expected_evidence_sources: string[]
}

export interface CountryOutageChatEvidence {
  evidence_ref: string
  source: string
  field_path: string
  value?: unknown
  unit?: string | null
  observed_at_utc?: string | null
  publication_id: string
  revision: number
  collector_id: 'rrc25'
}

export interface CountryOutageChatGoalResult {
  goal_id: string
  requested_goal: string
  normalized_kind: string
  answerability: CountryOutageChatAnswerability
  text: string
  evidence_refs: string[]
  limitations: string[]
}

export interface CountryOutageChatTurnAnswer {
  schema_version: 'country_outage_p1_runtime_v2_conversation_turn_v2'
  conversation_id: string
  turn_id: string
  turn_number: number
  binding_generation: number
  binding: CountryOutageChatBinding
  answerability: CountryOutageChatAnswerability
  answer_text: string
  results: CountryOutageChatGoalResult[]
  evidence: CountryOutageChatEvidence[]
  semantic_plan: {
    user_goal_plan: {
      plan_revision: 'user-goal-plan-v2'
      original_question: string
      goals: CountryOutageChatGoal[]
      planner_identity: string
      confidence: number
    }
    grounding_plan: {
      plan_revision: 'grounding-plan-v2'
      identity: Record<string, unknown>
      decisions: CountryOutageChatDecision[]
      nodes: CountryOutageChatNode[]
      validation: { status: string; errors: string[] }
    }
  }
  execution_trace: {
    nodes: Array<{
      node_id: string
      goal_id: string
      execution_unit: string
      capability_ids: string[]
      status: string
      evidence_refs: string[]
      error_code?: string | null
    }>
    state_commit: 'committed' | 'none'
  }
  state_receipt: {
    before: CountryOutageChatDialogState
    proposed: Record<string, unknown>
    after: CountryOutageChatDialogState
    status: 'committed' | 'none' | 'rolled_back'
    transaction_checks: Record<string, boolean>
  }
  runtime_identity: {
    implementation: string
    contract_revision: string
    language_layer: string
    collector: 'rrc25'
  }
}

export interface CountryOutageChatTurn {
  turn_id: string
  turn_number: number
  binding_generation: number
  question: string
  state: 'understanding' | 'executing' | 'validating' | 'completed' | 'failed' | 'cancelled'
  answer?: CountryOutageChatTurnAnswer
  error?: { code: string; message: string; retryable: boolean }
  failure_receipt?: CountryOutageChatTurnAnswer['state_receipt']
  created_at: string
  completed_at?: string
}

export interface CountryOutageChatConversation {
  schema_version: 'country_outage_p1_runtime_v2_conversation_v2'
  conversation_id: string
  binding: CountryOutageChatBinding
  binding_generation: number
  active_binding_generation: number | null
  evidence_state: CountryOutageChatEvidenceState
  dialog_state: CountryOutageChatDialogState
  turns: CountryOutageChatTurn[]
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

export interface CountryOutageChatBindingRequest {
  event_reference: string
  publication_id: string
  revision: number
  idempotency_key: string
}

export async function createCountryOutageChatConversation(
  request: CountryOutageChatBindingRequest,
) {
  return chatApiV2Post<{
    conversation: CountryOutageChatConversation
    deduplicated: boolean
  }>('country-outage/chat/conversations', request, {
    'Idempotency-Key': request.idempotency_key,
  })
}

export async function getCountryOutageChatConversation(conversationId: string) {
  return apiV2Get<{ conversation: CountryOutageChatConversation }>(
    `country-outage/chat/conversations/${encodeURIComponent(conversationId)}`,
  )
}

export async function createCountryOutageChatTurn(
  conversationId: string,
  question: string,
  idempotencyKey: string,
) {
  return chatApiV2Post<{ turn: CountryOutageChatTurn; deduplicated: boolean }>(
    `country-outage/chat/conversations/${encodeURIComponent(conversationId)}/turns`,
    { question, idempotency_key: idempotencyKey },
    { 'Idempotency-Key': idempotencyKey },
  )
}

export async function rebindCountryOutageChatConversation(
  conversationId: string,
  request: CountryOutageChatBindingRequest,
) {
  return chatApiV2Post<{
    conversation: CountryOutageChatConversation
    previous_binding: CountryOutageChatBinding
  }>(
    `country-outage/chat/conversations/${encodeURIComponent(conversationId)}/rebind`,
    request,
    { 'Idempotency-Key': request.idempotency_key },
  )
}

export async function cancelCountryOutageChatTurn(
  conversationId: string,
  turnId: string,
) {
  return chatApiV2Post<{ turn_id: string; state: 'cancel_requested' | 'not_active' }>(
    `country-outage/chat/conversations/${encodeURIComponent(conversationId)}/turns/${encodeURIComponent(turnId)}/cancel`,
    {},
  )
}
