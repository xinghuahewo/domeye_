import { apiV2Get, resolveApiTimeout } from './client'
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

export interface CountryOutageChatDataIdentity {
  event_type: 'country_outage'
  incident_id: string
  publication_id: string
  revision: number
  collector_id: 'rrc25'
  cohort_id: string
  country_code: string
  window_start_utc: string
  window_end_utc: string
  data_through: string
  is_final_in_data_range: boolean
  lifecycle_state: 'event_end_unknown'
}

export interface CountryOutageChatBinding extends CountryOutageChatDataIdentity {
  event_reference: string
}

export interface CountryOutageChatFindingValues {
  first: number | null
  first_at_utc: string | null
  last: number | null
  last_at_utc: string | null
  minimum: number | null
  minimum_at_utc: string | null
  maximum: number | null
  maximum_at_utc: string | null
  difference: number | null
  net_change: number | null
}

export interface CountryOutageChatFinding {
  schema_version: 'domeye_agent_typed_finding_v1'
  finding_id: string
  finding_type: 'fixed_visible_ipv4_series_extrema'
  value_state: 'known' | 'empty' | 'incomplete' | 'not_computable'
  candidate_id: string
  tenant_id: 'domeye'
  data_identity: CountryOutageChatDataIdentity
  metric: 'fixed_visible_ipv4_address_count'
  unit: 'unique_ipv4_address'
  population_definition: 'normalized_deduplicated_merged_fixed_prefix_ipv4_unique_address_union'
  values: CountryOutageChatFindingValues
  time_slot_count: number
  observed_point_count: number
  null_point_count: number
  completeness_state: 'complete' | 'incomplete'
  limitation_codes: string[]
  tool_version: '1.0.0'
  operator_version: '1.0.0'
  artifact_refs: string[]
  receipt_refs: string[]
  evidence_refs: string[]
  result_digest: string
}

export interface CountryOutageChatEvidence {
  evidence_ref: string
  label: string
  value: number | string | null
  unit: string | null
  observed_at_utc: string | null
}

export interface CountryOutageChatAdmissionReceipt {
  receipt_id: string
  decision: 'admitted' | 'rejected'
  reason_code: string | null
}

export interface CountryOutageChatActionReceipt {
  receipt_id: string
  capability_id: 'CAP-006' | 'CAP-016'
  status: 'succeeded' | 'failed'
  failure_code: string | null
}

export interface CountryOutageChatArtifact {
  artifact_id: string
  artifact_kind: 'metric_series' | 'series_extrema'
  content_digest: string
}

export interface CountryOutageChatObservation {
  observation_id: string
  capability_id: 'CAP-006' | 'CAP-016'
  status: 'succeeded' | 'rejected' | 'failed'
  reason_code: string | null
}

export interface CountryOutageChatAuthorizationDerivation {
  schema_version: 'domeye_authorization_derivation_v1'
  rule_id: 'country_outage_event_read_to_country_outage_read_v1'
  source_scope: 'country_outage_event_read' | `country_outage_event_read:${string}`
  source_scope_kind: 'global_event_read' | 'country_event_read'
  source_country_code: string
  derived_scope: 'country_outage:read'
}

export interface CountryOutageChatUsageAttempt {
  attempt_id: number
  phase: 'cognition' | 'renderer'
  provider: string
  model: string
  model_version: string
  expected_response_model: string
  response_model: string | null
  started_at_utc: string
  ended_at_utc: string | null
  latency_ms: number | null
  outcome: 'started' | 'completed' | 'failed' | 'limit_rejected'
  failure_code: string | null
}

export interface CountryOutageChatUsage {
  attempt_count: number
  maximum_attempt_count: 10
  cost_policy: 'audit_only'
  tokens: {
    input: number
    output: number
    cache_read: number
    cache_write: number
    total: number
  }
  estimated_cost_usd: number
  attempts: CountryOutageChatUsageAttempt[]
}

export interface CountryOutageChatTrace {
  goal_id: string
  goal_state_revision: number
  disposition: 'goal_satisfied' | 'clarification_required' | 'stopped'
  authorization_derivation: CountryOutageChatAuthorizationDerivation
  admission_receipts: CountryOutageChatAdmissionReceipt[]
  action_receipts: CountryOutageChatActionReceipt[]
  artifacts: CountryOutageChatArtifact[]
  observations: CountryOutageChatObservation[]
  response_guard: {
    decision: 'pass' | 'block'
    reason_codes: string[]
  } | null
}

export interface CountryOutageChatTurnAnswer {
  schema_version: 'domeye_interactive_agent_turn_answer_v1'
  answerability: 'supported' | 'clarification_required' | 'stopped'
  answer_text: string
  answer_source: 'renderer' | 'none'
  candidate_id: string
  data_identity: CountryOutageChatDataIdentity
  finding: CountryOutageChatFinding | null
  evidence: CountryOutageChatEvidence[]
  limitations: string[]
  trace: CountryOutageChatTrace
  usage: CountryOutageChatUsage
}

export type CountryOutageChatSuccessfulFinding = Omit<
  CountryOutageChatFinding,
  'value_state' | 'values' | 'observed_point_count' | 'null_point_count' | 'completeness_state'
> & {
  value_state: 'known'
  values: {
    first: number
    first_at_utc: string
    last: number
    last_at_utc: string
    minimum: number
    minimum_at_utc: string
    maximum: number
    maximum_at_utc: string
    difference: number
    net_change: number
  }
  observed_point_count: number
  null_point_count: number
  completeness_state: 'complete'
}

type CountryOutageChatSuccessfulAnswerCommon = Omit<
  CountryOutageChatTurnAnswer,
  'answerability' | 'answer_source' | 'finding' | 'trace'
> & {
  answerability: 'supported'
  finding: CountryOutageChatSuccessfulFinding
}

type CountryOutageChatSuccessfulTrace = OpenApiComponents['schemas'][
  'CountryOutageInteractiveSuccessfulTrace'
] & {
  response_guard: {
    decision: 'pass'
    reason_codes: []
  }
}

export type CountryOutageChatSuccessfulTurnAnswer =
  CountryOutageChatSuccessfulAnswerCommon & {
    answer_source: 'renderer'
    trace: CountryOutageChatSuccessfulTrace
  }

type CountryOutageChatNonSuccessAnswer = Omit<
  CountryOutageChatTurnAnswer,
  'answerability' | 'answer_source' | 'finding' | 'evidence' | 'limitations'
> & {
  answer_source: 'none'
  finding: null
  evidence: []
  limitations: []
}

interface CountryOutageChatTurnCommon {
  turn_id: string
  turn_number: number
  question: string
  created_at: string
}

export type CountryOutageChatTurn =
  | CountryOutageChatTurnCommon & {
    state: 'executing'
    answer_success: false
    workflow_completed: false
    answer?: never
    error?: never
    completed_at?: never
  }
  | CountryOutageChatTurnCommon & {
    state: 'completed'
    answer_success: true
    workflow_completed: true
    answer: CountryOutageChatSuccessfulTurnAnswer
    error?: never
    completed_at: string
  }
  | CountryOutageChatTurnCommon & {
    state: 'clarification_required'
    answer_success: false
    workflow_completed: false
    answer: CountryOutageChatNonSuccessAnswer & {
      answerability: 'clarification_required'
    }
    error?: never
    completed_at: string
  }
  | CountryOutageChatTurnCommon & {
    state: 'stopped'
    answer_success: false
    workflow_completed: false
    answer: CountryOutageChatNonSuccessAnswer & {
      answerability: 'stopped'
    }
    error?: never
    completed_at: string
  }
  | CountryOutageChatTurnCommon & {
    state: 'failed' | 'cancelled'
    answer_success: false
    workflow_completed: false
    answer?: never
    error: { code: string; message: string; retryable: boolean }
    completed_at: string
  }

export interface CountryOutageChatConversation {
  schema_version: 'domeye_interactive_agent_conversation_v1'
  conversation_id: string
  binding: CountryOutageChatBinding
  identity_receipt_id: string
  candidate_id: string
  turns: CountryOutageChatTurn[]
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

export async function cancelCountryOutageChatTurn(
  conversationId: string,
  turnId: string,
) {
  return chatApiV2Post<{ turn_id: string; state: 'cancel_requested' | 'not_active' }>(
    `country-outage/chat/conversations/${encodeURIComponent(conversationId)}/turns/${encodeURIComponent(turnId)}/cancel`,
    {},
  )
}
