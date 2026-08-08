import type { CountryOutagePrincipal } from '../server/contracts.js'

export const P1_CHAT_SCHEMA_VERSION =
  'country_outage_p1_chat_v1' as const
export const P1_CASE_SET_REVISION =
  'p0-v1-20260808-ir-r1' as const
export const P1_CONTRACT_REVISION = 'p1-contract-v1' as const

export type P1Answerability =
  | 'answerable'
  | 'partial'
  | 'clarify'
  | 'unsupported'
  | 'invalid_data'

export type P1TurnState =
  | 'queued'
  | 'understanding'
  | 'reading_facts'
  | 'validating'
  | 'completed'
  | 'failed'
  | 'cancelled'

export interface P1ConversationBinding {
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
export interface P1ConversationState {
  topic: string | null
  asn: number | null
  address_family: 'ipv4' | 'ipv6' | 'both' | null
  metric: string | null
  evidence_anchor: string | null
  pending_clarification: string | null
  last_committed_turn_number: number
}

export interface P1StateTransition {
  inherit: string[]
  set: Record<string, string | number | null>
  clear: string[]
  reason_codes: string[]
}

export interface P1EvidenceRecord {
  evidence_ref: string
  source: 'resolution' | 'overview' | 'series' | 'asns' | 'paths' | 'audit' | 'derived'
  label: string
  value: string | number | boolean | null
  unit: string | null
  observed_at_utc: string | null
  publication_id: string
  revision: number
}

export interface P1Subanswer {
  subrequest_id: string
  intents: string[]
  operator: string | null
  answerability: P1Answerability
  text: string
  evidence_refs: string[]
  limitations: string[]
  unknowns: string[]
}

export interface P1IntentPlan {
  normalized_question: string
  subrequests: Array<{
    subrequest_id: string
    intents: string[]
    entities: Record<string, string | number | null>
    operator: string | null
    answerability: P1Answerability
    confidence: number
    reason_codes: string[]
  }>
  transition: P1StateTransition
  blocking_errors: string[]
}

export interface P1AnswerEnvelope {
  schema_version: typeof P1_CHAT_SCHEMA_VERSION
  conversation_id: string
  turn_id: string
  turn_number: number
  answerability: P1Answerability
  binding: P1ConversationBinding
  p0_case_set_revision: typeof P1_CASE_SET_REVISION
  p1_contract_revision: typeof P1_CONTRACT_REVISION
  plan: P1IntentPlan
  results: P1Subanswer[]
  answer_text: string
  evidence: P1EvidenceRecord[]
  limitations: string[]
  unknowns: string[]
  transition: P1StateTransition
  validation: {
    passed: boolean
    errors: string[]
    checked_evidence_refs: string[]
  }
  runtime_identity: {
    implementation: 'p1-deterministic-chat'
    rule_set: 'p1-rules-v1'
    language_layer: 'deterministic-fallback'
    collector: 'rrc25'
  }
  completed_at: string
}

export interface P1TurnDescriptor {
  turn_id: string
  turn_number: number
  question: string
  state: P1TurnState
  answer?: P1AnswerEnvelope
  error?: {
    code: string
    message: string
    retryable: boolean
    next_action?: string
  }
  created_at: string
  completed_at?: string
}

export interface P1ConversationDescriptor {
  schema_version: typeof P1_CHAT_SCHEMA_VERSION
  conversation_id: string
  binding: P1ConversationBinding
  state: P1ConversationState
  turns: P1TurnDescriptor[]
  expires_at: string
  reminder_at: string
  created_at: string
}

export interface P1ChatEvent {
  schema_version: typeof P1_CHAT_SCHEMA_VERSION
  event_id: number
  event_type: 'conversation_ready' | 'turn_state' | 'turn_answer' | 'session_notice'
  conversation_id: string
  turn_id?: string
  state?: P1TurnState
  answer?: P1AnswerEnvelope
  phase?: 'session_expiring' | 'session_expired'
  emitted_at: string
}

export interface CreateP1ConversationRequest {
  event_reference: string
  publication_id: string
  revision: number
  idempotency_key: string
}

export interface CreateP1TurnRequest {
  question: string
  idempotency_key: string
}

export interface P1ChatSubscription {
  replay: P1ChatEvent[]
  activate(): void
  close(): void
}

export interface P1ChatApplication {
  createConversation(
    principal: CountryOutagePrincipal,
    request: CreateP1ConversationRequest,
  ): Promise<{ conversation: P1ConversationDescriptor, deduplicated: boolean }>
  getConversation(
    principal: CountryOutagePrincipal,
    conversationId: string,
  ): Promise<P1ConversationDescriptor>
  createTurn(
    principal: CountryOutagePrincipal,
    conversationId: string,
    request: CreateP1TurnRequest,
  ): Promise<{ turn: P1TurnDescriptor, deduplicated: boolean }>
  cancelTurn(
    principal: CountryOutagePrincipal,
    conversationId: string,
    turnId: string,
  ): Promise<{ turn_id: string, state: P1TurnState }>
  subscribe(
    principal: CountryOutagePrincipal,
    conversationId: string,
    afterEventId: number,
    listener: (event: P1ChatEvent) => void,
  ): Promise<P1ChatSubscription>
  rebind(
    principal: CountryOutagePrincipal,
    conversationId: string,
    request: CreateP1ConversationRequest,
  ): Promise<{ conversation: P1ConversationDescriptor, previous_conversation_id: string }>
}
