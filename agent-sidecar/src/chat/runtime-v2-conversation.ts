import { randomUUID } from 'node:crypto'

import type { CountryOutagePrincipal } from '../server/contracts.js'
import { CountryOutageHttpError } from '../server/errors.js'
import type {
  P1ConversationBinding,
  P1ConversationState,
  P1StateTransition,
} from './contracts.js'
import { P1DeterministicQuestionEngine } from './deterministic-engine.js'
import type {
  P1FactBundle,
  P1GeneralReadModelProvider,
} from './general-read-model-provider.js'
import { P1ReadModelError } from './general-read-model-provider.js'
import {
  P1RuntimeV2Grounder,
  P1SemanticPlanError,
  p1RuntimeV2BoundaryText,
  type P1GroundingDecision,
  type P1GroundingNode,
  type P1RuntimeV2SemanticAnswer,
  type P1SemanticAnswerability,
  type P1SemanticGoalResult,
  type P1SemanticPlan,
  type P1UserGoal,
  type P1UserGoalPlan,
  type P1UserGoalPlanner,
} from './runtime-v2-semantic.js'
import {
  P1RuntimeV2SingleTurnError,
  authorizeP1RuntimeV2Country,
  readP1RuntimeV2PermissionCandidate,
  throwIfP1RuntimeV2Cancelled,
  type P1RuntimeV2Evidence,
} from './runtime-v2-single-turn.js'

export const P1_RUNTIME_V2_CONVERSATION_SCHEMA =
  'country_outage_p1_runtime_v2_conversation_v1' as const
export const P1_RUNTIME_V2_CONVERSATION_TURN_SCHEMA =
  'country_outage_p1_runtime_v2_conversation_turn_v1' as const

type TurnState =
  | 'understanding'
  | 'executing'
  | 'validating'
  | 'completed'
  | 'failed'
  | 'cancelled'

type ControlledGoalKind =
  | 'event_summary'
  | 'event_identity'
  | 'observation_window'
  | 'detection_time'
  | 'event_end_state'
  | 'recovery_status'
  | 'prefix_peak'
  | 'current_prefix_state'
  | 'current_scope'
  | 'top_affected_asns'
  | 'asn_detail'
  | 'remaining_vs_peak'
  | 'address_family_change'
  | 'address_family_compare'
  | 'metric_semantics'
  | 'new_prefix_resources'
  | 'path_sample'
  | 'evidence_trace'
  | 'data_completeness'
  | 'rrc25_proof_boundary'
  | 'fact_timeline'
  | 'cause_or_responsibility'
  | 'event_switch'
  | 'unsupported_boundary'
  | 'unknown'

interface ResolvedGoal {
  goal: P1UserGoal
  kind: ControlledGoalKind
  asn: number | null
  addressFamily: 'ipv4' | 'ipv6' | 'both' | null
  inherited: string[]
  reasonCodes: string[]
}

export interface P1RuntimeV2StateReceipt {
  before: P1ConversationState
  proposed: P1StateTransition
  after: P1ConversationState
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

export interface P1RuntimeV2ConversationTurnAnswer
extends Omit<P1RuntimeV2SemanticAnswer, 'schema_version' | 'execution_trace'> {
  schema_version: typeof P1_RUNTIME_V2_CONVERSATION_TURN_SCHEMA
  conversation_id: string
  turn_id: string
  turn_number: number
  execution_trace: Omit<
    P1RuntimeV2SemanticAnswer['execution_trace'],
    'nodes' | 'state_commit'
  > & {
    nodes: Array<
      Omit<
        P1RuntimeV2SemanticAnswer['execution_trace']['nodes'][number],
        'status'
      > & {
        status: 'passed' | 'failed'
        receipt_id: string
        execution_mode: 'verified_evidence_state_read'
      }
    >
    state_commit: 'committed' | 'none'
  }
  state_receipt: P1RuntimeV2StateReceipt
}

export interface P1RuntimeV2ConversationTurn {
  turn_id: string
  turn_number: number
  question: string
  state: TurnState
  answer?: P1RuntimeV2ConversationTurnAnswer
  error?: {
    code: string
    message: string
    retryable: boolean
  }
  created_at: string
  completed_at?: string
}

export interface P1RuntimeV2ConversationDescriptor {
  schema_version: typeof P1_RUNTIME_V2_CONVERSATION_SCHEMA
  conversation_id: string
  binding: P1ConversationBinding
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
  dialog_state: P1ConversationState
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

export interface CreateP1RuntimeV2ConversationRequest {
  event_reference: string
  publication_id: string
  revision: number
  idempotency_key: string
}

export interface CreateP1RuntimeV2ConversationTurnRequest {
  question: string
  idempotency_key: string
}

interface StoredConversation {
  owner: string
  descriptor: P1RuntimeV2ConversationDescriptor
  bundle: P1FactBundle
  idempotency: Map<string, {
    question: string
    turn: P1RuntimeV2ConversationTurn
  }>
  rebindIdempotency: Map<string, {
    request_fingerprint: string
    resulting_generation: number
    conversation: P1RuntimeV2ConversationDescriptor
    previous_binding: P1ConversationBinding
  }>
  active: Map<string, AbortController>
}

export interface P1RuntimeV2ConversationServiceOptions {
  provider: P1GeneralReadModelProvider
  planner: P1UserGoalPlanner
  ttlMs?: number
  now?: () => Date
}

const KIND_ALIASES: Record<string, ControlledGoalKind> = {
  event_summary: 'event_summary',
  overview: 'event_summary',
  event_identity: 'event_identity',
  publication_identity: 'event_identity',
  observation_window: 'observation_window',
  detection_time: 'detection_time',
  detected_time_not_true_onset: 'detection_time',
  anomaly_onset: 'detection_time',
  event_end_state: 'event_end_state',
  event_duration: 'event_end_state',
  recovery_status: 'recovery_status',
  recovery: 'recovery_status',
  prefix_peak: 'prefix_peak',
  timeline_peak: 'prefix_peak',
  primary_peak: 'prefix_peak',
  peak_prefix: 'prefix_peak',
  current_prefix_state: 'current_prefix_state',
  current_state: 'current_prefix_state',
  current_scope: 'current_scope',
  affected_scope: 'current_scope',
  top_affected_asns: 'top_affected_asns',
  affected_asn_ranking: 'top_affected_asns',
  top_asns: 'top_affected_asns',
  asn_detail: 'asn_detail',
  specified_asn: 'asn_detail',
  entity_correction: 'asn_detail',
  remaining_vs_peak: 'remaining_vs_peak',
  peak_to_current: 'remaining_vs_peak',
  address_family_change: 'address_family_change',
  ipv4_visibility_drop: 'address_family_change',
  maximum_ipv4_visibility_drop: 'address_family_change',
  address_family_compare: 'address_family_compare',
  address_family_comparison: 'address_family_compare',
  ipv4_ipv6_comparison: 'address_family_compare',
  metric_semantics: 'metric_semantics',
  metric_definitions: 'metric_semantics',
  new_prefix_resources: 'new_prefix_resources',
  new_prefixes: 'new_prefix_resources',
  path_sample: 'path_sample',
  evidence_trace: 'evidence_trace',
  evidence_identity: 'evidence_trace',
  data_completeness: 'data_completeness',
  rrc25_proof_boundary: 'rrc25_proof_boundary',
  evidence_boundary: 'rrc25_proof_boundary',
  fact_timeline: 'fact_timeline',
  timeline: 'fact_timeline',
  cause_or_responsibility: 'cause_or_responsibility',
  cause: 'cause_or_responsibility',
  responsibility: 'cause_or_responsibility',
  event_switch: 'event_switch',
  real_user_or_national_impact: 'unsupported_boundary',
  economic_impact: 'unsupported_boundary',
  economic_loss: 'unsupported_boundary',
  dns_http_traffic: 'unsupported_boundary',
  external_evidence: 'unsupported_boundary',
  remediation_recommendation: 'unsupported_boundary',
  incident_response_recommendations: 'unsupported_boundary',
  technical_cause: 'unsupported_boundary',
  technical_mechanism_attribution: 'unsupported_boundary',
  responsibility_and_economic_impact: 'unsupported_boundary',
  nationwide_user_connectivity: 'unsupported_boundary',
  actual_reachability: 'unsupported_boundary',
  external_measurement_request: 'unsupported_boundary',
}

const POLICY_KIND_ALIASES: Record<string, string> = {
  cause: 'cause_or_responsibility',
  responsibility: 'cause_or_responsibility',
  technical_cause: 'cause_or_responsibility',
  technical_mechanism_attribution: 'cause_or_responsibility',
  responsibility_and_economic_impact: 'cause_or_responsibility',
  economic_impact: 'economic_impact',
  economic_loss: 'economic_impact',
  user_impact: 'real_user_or_national_impact',
  nationwide_user_connectivity: 'real_user_or_national_impact',
  actual_reachability: 'real_user_or_national_impact',
  external_measurement_request: 'external_evidence',
}

function clone<T>(value: T): T {
  return structuredClone(value)
}

function initialState(): P1ConversationState {
  return {
    topic: null,
    asn: null,
    address_family: null,
    metric: null,
    evidence_anchor: null,
    pending_clarification: null,
    last_committed_turn_number: 0,
  }
}

function owner(principal: CountryOutagePrincipal): string {
  return `${principal.userId}\u0000${principal.authorizationScope}`
}

function normalizeReference(value: string): string {
  return value.trim().replaceAll('+', ' ')
}

function assertRequestBinding(
  request: Pick<CreateP1RuntimeV2ConversationRequest,
    'event_reference' | 'publication_id' | 'revision'>,
  binding: P1ConversationBinding,
): void {
  if (
    normalizeReference(request.event_reference)
      !== normalizeReference(binding.legacy_reference)
    || request.publication_id !== binding.publication_id
    || request.revision !== binding.revision
  ) {
    throw new P1RuntimeV2SingleTurnError(
      'binding_conflict',
      '请求事件、publication 或 revision 与解析结果不一致',
    )
  }
  if (
    binding.event_type !== 'country_outage'
    || binding.collector_id !== 'rrc25'
  ) {
    throw new P1RuntimeV2SingleTurnError(
      'unsupported_event',
      'P1 只接受 RRC25 country_outage 事件',
    )
  }
}

function bindingEquals(
  expected: P1ConversationBinding,
  actual: P1ConversationBinding,
): boolean {
  return [
    'event_type', 'incident_id', 'legacy_reference', 'publication_id',
    'revision', 'collector_id', 'cohort_id', 'country_code',
    'detected_at_utc', 'window_start_utc', 'window_end_utc', 'data_through',
    'is_final_in_data_range', 'lifecycle_state', 'observation_state',
    'quality_state', 'missing_slot_count', 'capabilities',
  ].every((key) =>
    JSON.stringify(expected[key as keyof P1ConversationBinding])
      === JSON.stringify(actual[key as keyof P1ConversationBinding])
  )
}

function assertFactBundleIdentity(bundle: P1FactBundle): void {
  const expected = bundle.binding as unknown as Record<string, unknown>
  const identityKeys = [
    'incident_id', 'publication_id', 'revision', 'collector_id', 'cohort_id',
    'window_start_utc', 'window_end_utc', 'data_through',
    'is_final_in_data_range', 'lifecycle_state',
  ]
  for (const [label, payload] of Object.entries({
    resolution: bundle.resolution,
    overview: bundle.overview,
    series: bundle.series,
    asns: bundle.asns,
    paths: bundle.paths,
    audit: bundle.audit,
  })) {
    for (const key of identityKeys) {
      if (
        Object.prototype.hasOwnProperty.call(payload, key)
        && payload[key] !== expected[key]
      ) {
        throw new P1ReadModelError(
          'publication_identity_conflict',
          `${label}.${key} 与 EvidenceState 身份不一致`,
        )
      }
    }
  }
  const pointCount = bundle.series.point_count
  const timestamps = bundle.series.timestamps
  const tracks = bundle.series.tracks
  if (
    !Number.isSafeInteger(pointCount)
    || pointCount < 1
    || !Array.isArray(timestamps)
    || timestamps.length !== pointCount
    || !tracks
    || Object.values(tracks).some((track) =>
      !Array.isArray(track) || track.length !== pointCount
    )
  ) {
    throw new P1ReadModelError(
      'invalid_series_shape',
      'series 点数、时间戳和轨道长度不一致',
    )
  }
}

function identity(binding: P1ConversationBinding):
P1SemanticPlan['grounding_plan']['identity'] {
  return {
    binding_phase: 'bound',
    event_type: 'country_outage',
    incident_id: binding.incident_id,
    publication_id: binding.publication_id,
    revision: binding.revision,
    collector_id: 'rrc25',
    cohort_id: binding.cohort_id,
    country_code: binding.country_code,
    window_start_utc: binding.window_start_utc,
    window_end_utc: binding.window_end_utc,
    data_through: binding.data_through,
    is_final_in_data_range: binding.is_final_in_data_range,
    lifecycle_state: binding.lifecycle_state,
    observation_state: binding.observation_state,
    capabilities: clone(binding.capabilities),
  }
}

function scalarAsn(value: unknown): number | null {
  if (
    typeof value === 'number'
    && Number.isSafeInteger(value)
    && value >= 1
    && value <= 4_294_967_295
  ) return value
  if (typeof value === 'string') {
    const normalized = value.trim().replace(/^AS/i, '')
    if (/^[1-9][0-9]{0,9}$/.test(normalized)) {
      const parsed = Number(normalized)
      if (parsed <= 4_294_967_295) return parsed
    }
  }
  return null
}

function scalarAddressFamily(
  value: unknown,
): 'ipv4' | 'ipv6' | 'both' | null {
  if (typeof value !== 'string') return null
  const normalized = value.toLowerCase()
  if (normalized === 'ipv4' || normalized === 'ipv6' || normalized === 'both') {
    return normalized
  }
  return null
}

function resolveGoal(
  goal: P1UserGoal,
  state: P1ConversationState,
): ResolvedGoal {
  let kind = KIND_ALIASES[goal.normalized_kind] ?? 'unknown'
  const inherited: string[] = []
  const reasonCodes: string[] = []
  let asn = scalarAsn(goal.entities.asn)
  let addressFamily = scalarAddressFamily(goal.entities.address_family)
  if (
    goal.ambiguity !== 'blocking'
    && kind === 'asn_detail'
    && asn === null
    && state.asn !== null
  ) {
    asn = state.asn
    inherited.push('asn')
    reasonCodes.push('inherited_verified_asn')
  }
  if (
    goal.ambiguity !== 'blocking'
    && (kind === 'address_family_compare' || kind === 'address_family_change')
    && addressFamily === null
    && state.address_family !== null
  ) {
    addressFamily = state.address_family
    inherited.push('address_family')
    reasonCodes.push('inherited_verified_address_family')
  }
  if (
    goal.ambiguity !== 'blocking'
    && kind === 'unknown'
    && ['metric_followup', 'current_metric_value']
      .includes(goal.normalized_kind)
    && goal.context_dependencies.includes('prior_metric')
    && state.metric === 'interrupted_prefix_count'
  ) {
    kind = 'current_prefix_state'
    inherited.push('metric')
    reasonCodes.push('inherited_verified_metric')
  }
  if (
    kind === 'cause_or_responsibility'
    && state.evidence_anchor !== null
  ) reasonCodes.push('prior_verified_evidence_context')
  return { goal, kind, asn, addressFamily, inherited, reasonCodes }
}

function uniqueEventSwitchReference(
  resolvedGoals: ResolvedGoal[],
): string | null {
  const switchGoals = resolvedGoals.filter((item) =>
    item.kind === 'event_switch'
  )
  if (switchGoals.length !== 1 || switchGoals[0]!.goal.ambiguity === 'blocking') {
    return null
  }
  const goal = switchGoals[0]!.goal
  const candidates = [
    ...goal.references,
    ...['event_reference', 'legacy_reference', 'reference']
      .map((key) => goal.entities[key])
      .filter((value): value is string => typeof value === 'string'),
  ]
    .map(normalizeReference)
    .filter((value) => /^country_outage\/[^/]+\/[A-Z]{2}\/[1-9][0-9]*\/r$/.test(value))
  const unique = [...new Set(candidates)]
  return unique.length === 1 ? unique[0]! : null
}

function commonIdentityInputs(binding: P1ConversationBinding):
Record<string, unknown> {
  return {
    incident_id: binding.incident_id,
    publication_id: binding.publication_id,
    revision: binding.revision,
  }
}

function inputSources(
  inputs: Record<string, unknown>,
  source = 'binding',
): Record<string, string> {
  return Object.fromEntries(Object.keys(inputs).map((key) => [key, source]))
}

function addNode(
  nodes: P1GroundingNode[],
  goalId: string,
  executionUnit: string,
  capabilityIds: string[],
  inputs: Record<string, unknown>,
  dependsOn: string[],
  evidenceSources: string[],
  source: string | Record<string, string> = 'binding',
): string {
  const nodeId = `node-${nodes.length + 1}`
  nodes.push({
    node_id: nodeId,
    goal_id: goalId,
    execution_unit: executionUnit,
    capability_ids: capabilityIds,
    inputs,
    input_sources: typeof source === 'string'
      ? inputSources(inputs, source)
      : source,
    depends_on: dependsOn,
    expected_evidence_sources: evidenceSources,
  })
  return nodeId
}

function eventCapabilityAvailable(
  kind: ControlledGoalKind,
  binding: P1ConversationBinding,
): boolean {
  if (
    [
      'event_summary', 'event_identity', 'observation_window',
      'detection_time', 'event_end_state', 'recovery_status', 'prefix_peak',
      'current_prefix_state', 'current_scope', 'remaining_vs_peak',
      'data_completeness', 'rrc25_proof_boundary', 'fact_timeline',
    ].includes(kind)
  ) return binding.capabilities.overview === 'available'
  if (kind === 'asn_detail' || kind === 'top_affected_asns') {
    return binding.capabilities.affected_as === 'available'
  }
  if (
    kind === 'address_family_change'
    || kind === 'address_family_compare'
    || kind === 'metric_semantics'
    || kind === 'new_prefix_resources'
  ) {
    return binding.capabilities.event_series === 'available'
  }
  if (kind === 'path_sample') {
    return binding.capabilities.path_downstreams === 'available'
      && binding.capabilities.full_path_evidence === 'audit_only'
  }
  if (kind === 'evidence_trace') {
    return binding.capabilities.full_path_evidence === 'audit_only'
  }
  if (kind === 'cause_or_responsibility') {
    return binding.capabilities.overview === 'available'
      && binding.capabilities.full_path_evidence === 'audit_only'
  }
  return true
}

function buildGroundingPlan(
  userGoalPlan: P1UserGoalPlan,
  resolvedGoals: ResolvedGoal[],
  binding: P1ConversationBinding,
  eventReference: string,
  validator: P1RuntimeV2Grounder,
  bindingActive: boolean,
  resolvedEventSwitch: boolean,
): P1SemanticPlan {
  const nodes: P1GroundingNode[] = []
  const decisions: P1GroundingDecision[] = []
  const hasEventSwitch = resolvedGoals.some((item) =>
    item.kind === 'event_switch'
  )
  let resolutionNodeId: string | null = null
  const ensureResolution = (goalId: string): string => {
    if (resolutionNodeId) return resolutionNodeId
    const inputs = {
      event_reference: eventReference,
      expected_publication_id: binding.publication_id,
      expected_revision: binding.revision,
    }
    resolutionNodeId = addNode(
      nodes,
      goalId,
      'TOOL-01',
      ['CAP-001'],
      inputs,
      [],
      ['resolution'],
    )
    return resolutionNodeId
  }
  for (const resolved of resolvedGoals) {
    const { goal } = resolved
    if (resolved.kind === 'event_switch') {
      if (resolvedEventSwitch) {
        const root = ensureResolution(goal.goal_id)
        decisions.push({
          goal_id: goal.goal_id,
          answerability: 'supported',
          node_ids: [root],
          reason_codes: ['event_switch_resolved_and_authorized'],
        })
        continue
      }
      decisions.push({
        goal_id: goal.goal_id,
        answerability: 'clarify',
        node_ids: [],
        reason_codes: ['event_switch_requires_unique_reference'],
      })
      continue
    }
    if (hasEventSwitch) {
      decisions.push({
        goal_id: goal.goal_id,
        answerability: 'clarify',
        node_ids: [],
        reason_codes: [resolvedEventSwitch
          ? 'event_switch_completed_reask_target_fact'
          : 'event_binding_suspended_until_rebind'],
      })
      continue
    }
    if (goal.normalized_kind === 'capability_absent_not_zero') {
      decisions.push({
        goal_id: goal.goal_id,
        answerability: 'invalid_data',
        node_ids: [],
        reason_codes: ['update_track_unavailable_not_zero'],
      })
      continue
    }
    const policyBoundary = validator.boundaryDecision(
      POLICY_KIND_ALIASES[goal.normalized_kind] ?? goal.normalized_kind,
    )
    const maySummarizePriorEvidence = resolved.kind === 'cause_or_responsibility'
      && resolved.reasonCodes.includes('prior_verified_evidence_context')
    if (policyBoundary && !maySummarizePriorEvidence) {
      decisions.push({
        goal_id: goal.goal_id,
        answerability: policyBoundary.decision,
        node_ids: [],
        reason_codes: [policyBoundary.reason_code],
      })
      continue
    }
    if (!bindingActive) {
      decisions.push({
        goal_id: goal.goal_id,
        answerability: 'clarify',
        node_ids: [],
        reason_codes: ['event_binding_suspended_until_rebind'],
      })
      continue
    }
    if (goal.ambiguity === 'blocking') {
      decisions.push({
        goal_id: goal.goal_id,
        answerability: 'clarify',
        node_ids: [],
        reason_codes: ['required_goal_or_entity_not_safely_groundable'],
      })
      continue
    }
    if (resolved.kind === 'unsupported_boundary') {
      decisions.push({
        goal_id: goal.goal_id,
        answerability: 'unsupported',
        node_ids: [],
        reason_codes: ['goal_outside_rrc25_p1_boundary'],
      })
      continue
    }
    if (
      resolved.kind === 'unknown'
      || (resolved.kind === 'asn_detail' && resolved.asn === null)
    ) {
      decisions.push({
        goal_id: goal.goal_id,
        answerability: 'clarify',
        node_ids: [],
        reason_codes: [resolved.kind === 'asn_detail'
          ? 'asn_required'
          : 'goal_or_context_not_safely_groundable'],
      })
      continue
    }
    if (!eventCapabilityAvailable(resolved.kind, binding)) {
      decisions.push({
        goal_id: goal.goal_id,
        answerability: 'unsupported',
        node_ids: [],
        reason_codes: ['capability_unavailable'],
      })
      continue
    }
    const root = ensureResolution(goal.goal_id)
    const goalNodeIds: string[] = nodes.at(-1)?.node_id === root
      && nodes.at(-1)?.goal_id === goal.goal_id ? [root] : []
    const identityInputs = commonIdentityInputs(binding)
    if (
      [
        'event_summary', 'event_identity', 'observation_window',
        'detection_time', 'event_end_state', 'recovery_status', 'prefix_peak',
        'current_prefix_state', 'current_scope', 'remaining_vs_peak',
      ].includes(resolved.kind)
    ) {
      const capabilities: Record<string, string[]> = {
        event_summary: ['CAP-002', 'CAP-003', 'CAP-004'],
        event_identity: ['CAP-002'],
        observation_window: ['CAP-002'],
        detection_time: ['CAP-002'],
        event_end_state: ['CAP-002'],
        recovery_status: ['CAP-002', 'CAP-003', 'CAP-004'],
        prefix_peak: ['CAP-004'],
        current_prefix_state: ['CAP-003'],
        current_scope: ['CAP-003', 'CAP-005'],
        remaining_vs_peak: ['CAP-003', 'CAP-004'],
      }
      goalNodeIds.push(addNode(
        nodes,
        goal.goal_id,
        'TOOL-02',
        capabilities[resolved.kind]!,
        identityInputs,
        [root],
        ['overview'],
      ))
    } else if (resolved.kind === 'top_affected_asns') {
      const inputs = {
        ...identityInputs,
        query: '',
        classification: 'all',
        sort: 'default',
        page: 1,
        page_size: 5,
      }
      goalNodeIds.push(addNode(
        nodes, goal.goal_id, 'TOOL-04', ['CAP-010'], inputs,
        [root], ['asns'], {
          incident_id: 'binding',
          publication_id: 'binding',
          revision: 'binding',
          query: 'policy_default',
          classification: 'policy_default',
          sort: 'policy_default',
          page: 'policy_default',
          page_size: 'user_goal',
        },
      ))
    } else if (resolved.kind === 'asn_detail') {
      const inputs = {
        ...identityInputs,
        asn: resolved.asn,
        query: String(resolved.asn),
        classification: 'all',
        sort: 'default',
        page: 1,
        page_size: 60,
      }
      goalNodeIds.push(addNode(
        nodes, goal.goal_id, 'TOOL-04', ['CAP-011'], inputs,
        [root], ['asns'], {
          incident_id: 'binding',
          publication_id: 'binding',
          revision: 'binding',
          asn: resolved.inherited.includes('asn')
            ? 'dialog_state' : 'user_goal',
          query: resolved.inherited.includes('asn')
            ? 'dialog_state' : 'user_goal',
          classification: 'policy_default',
          sort: 'policy_default',
          page: 'policy_default',
          page_size: 'policy_default',
        },
      ))
    } else if (resolved.kind === 'address_family_change') {
      const family = resolved.addressFamily === 'ipv6' ? 'ipv6' : 'ipv4'
      const metric = family === 'ipv4'
        ? 'fixed_visible_ipv4_address_count'
        : 'fixed_visible_ipv6_slash48_count'
      const capability = family === 'ipv4' ? 'CAP-006' : 'CAP-007'
      const seriesNode = addNode(
        nodes,
        goal.goal_id,
        'TOOL-03',
        [capability],
        { ...identityInputs, metrics: [metric] },
        [root],
        ['series'],
        {
          incident_id: 'binding',
          publication_id: 'binding',
          revision: 'binding',
          metrics: 'user_goal',
        },
      )
      const extremaNode = addNode(
        nodes,
        goal.goal_id,
        'OP-01',
        ['CAP-016'],
        {
          source_node_id: seriesNode,
          metric,
          tie_policy: 'first_observed_occurrence',
        },
        [seriesNode],
        ['derived'],
        {
          source_node_id: 'tool_result',
          metric: 'user_goal',
          tie_policy: 'policy_default',
        },
      )
      goalNodeIds.push(seriesNode, extremaNode)
    } else if (resolved.kind === 'address_family_compare') {
      const ipv4Series = addNode(
        nodes,
        goal.goal_id,
        'TOOL-03',
        ['CAP-006'],
        { ...identityInputs, metrics: ['fixed_visible_ipv4_address_count'] },
        [root],
        ['series'],
        {
          incident_id: 'binding',
          publication_id: 'binding',
          revision: 'binding',
          metrics: 'user_goal',
        },
      )
      const ipv6Series = addNode(
        nodes,
        goal.goal_id,
        'TOOL-03',
        ['CAP-007'],
        { ...identityInputs, metrics: ['fixed_visible_ipv6_slash48_count'] },
        [root],
        ['series'],
        {
          incident_id: 'binding',
          publication_id: 'binding',
          revision: 'binding',
          metrics: 'user_goal',
        },
      )
      const ipv4ExtremaInputs = {
        source_node_id: ipv4Series,
        metric: 'fixed_visible_ipv4_address_count',
        tie_policy: 'first_observed_occurrence',
      }
      const ipv4Extrema = addNode(
        nodes, goal.goal_id, 'OP-01', ['CAP-016'], ipv4ExtremaInputs,
        [ipv4Series], ['derived'], {
          source_node_id: 'tool_result',
          metric: 'user_goal',
          tie_policy: 'policy_default',
        },
      )
      const ipv6ExtremaInputs = {
        source_node_id: ipv6Series,
        metric: 'fixed_visible_ipv6_slash48_count',
        tie_policy: 'first_observed_occurrence',
      }
      const ipv6Extrema = addNode(
        nodes, goal.goal_id, 'OP-01', ['CAP-016'], ipv6ExtremaInputs,
        [ipv6Series], ['derived'], {
          source_node_id: 'tool_result',
          metric: 'user_goal',
          tie_policy: 'policy_default',
        },
      )
      const comparisonInputs = {
        ipv4_extrema_node_id: ipv4Extrema,
        ipv6_extrema_node_id: ipv6Extrema,
      }
      const comparison = addNode(
        nodes, goal.goal_id, 'OP-02', ['CAP-017'], comparisonInputs,
        [ipv4Extrema, ipv6Extrema], ['derived'], {
          ipv4_extrema_node_id: 'tool_result',
          ipv6_extrema_node_id: 'tool_result',
        },
      )
      goalNodeIds.push(
        ipv4Series,
        ipv6Series,
        ipv4Extrema,
        ipv6Extrema,
        comparison,
      )
    } else if (resolved.kind === 'metric_semantics') {
      goalNodeIds.push(addNode(
        nodes,
        goal.goal_id,
        'TOOL-03',
        ['CAP-009'],
        {
          ...identityInputs,
          metrics: [
            'interrupted_prefix_count',
            'completely_interrupted_prefix_count',
            'invisible_direction_count',
          ],
        },
        [root],
        ['series'],
      ))
    } else if (resolved.kind === 'new_prefix_resources') {
      goalNodeIds.push(addNode(
        nodes,
        goal.goal_id,
        'TOOL-03',
        ['CAP-008'],
        {
          ...identityInputs,
          metrics: [
            'new_cumulative_ipv4_prefix_count',
            'new_cumulative_ipv6_prefix_count',
            'new_visible_ipv4_prefix_count',
            'new_visible_ipv6_prefix_count',
          ],
        },
        [root],
        ['series'],
      ))
    } else if (resolved.kind === 'path_sample') {
      const inputs = {
        ...identityInputs,
        affected_asn: resolved.asn,
        scope: 'all',
        query: '',
        page: 1,
        page_size: 60,
      }
      goalNodeIds.push(addNode(
        nodes, goal.goal_id, 'TOOL-05', ['CAP-012', 'CAP-013'],
        inputs, [root], ['paths'], {
          incident_id: 'binding',
          publication_id: 'binding',
          revision: 'binding',
          affected_asn: resolved.asn === null
            ? 'policy_default'
            : resolved.inherited.includes('asn')
              ? 'dialog_state' : 'user_goal',
          scope: 'policy_default',
          query: 'policy_default',
          page: 'policy_default',
          page_size: 'policy_default',
        },
      ))
    } else if (resolved.kind === 'evidence_trace') {
      goalNodeIds.push(addNode(
        nodes, goal.goal_id, 'TOOL-06', ['CAP-014'], identityInputs,
        [root], ['audit'],
      ))
    } else if (resolved.kind === 'data_completeness') {
      goalNodeIds.push(addNode(
        nodes, goal.goal_id, 'TOOL-02', ['CAP-002'], identityInputs,
        [root], ['overview'],
      ))
      goalNodeIds.push(addNode(
        nodes, goal.goal_id, 'TOOL-03', ['CAP-009'], {
          ...identityInputs,
          metrics: ['interrupted_prefix_count'],
        }, [root], ['series'],
      ))
      goalNodeIds.push(addNode(
        nodes, goal.goal_id, 'TOOL-06', ['CAP-014'], identityInputs,
        [root], ['audit'],
      ))
    } else if (resolved.kind === 'rrc25_proof_boundary') {
      goalNodeIds.push(addNode(
        nodes, goal.goal_id, 'TOOL-02', ['CAP-002'], identityInputs,
        [root], ['overview'],
      ))
      goalNodeIds.push(addNode(
        nodes, goal.goal_id, 'TOOL-06', ['CAP-014'], identityInputs,
        [root], ['audit'],
      ))
    } else if (resolved.kind === 'fact_timeline') {
      const overviewNode = addNode(
        nodes, goal.goal_id, 'TOOL-02',
        ['CAP-002', 'CAP-003', 'CAP-004'], identityInputs,
        [root], ['overview'],
      )
      const seriesNode = addNode(
        nodes, goal.goal_id, 'TOOL-03', ['CAP-009'], {
          ...identityInputs,
          metrics: [
            'interrupted_prefix_count',
            'completely_interrupted_prefix_count',
            'affected_asn_count',
          ],
        }, [root], ['series'],
      )
      const timelineNode = addNode(
        nodes, goal.goal_id, 'OP-03', ['CAP-018'], {
          source_node_ids: [overviewNode, seriesNode],
          lifecycle_state: binding.lifecycle_state,
        }, [overviewNode, seriesNode], ['derived'],
      )
      goalNodeIds.push(overviewNode, seriesNode, timelineNode)
    } else if (resolved.kind === 'cause_or_responsibility') {
      goalNodeIds.push(addNode(
        nodes, goal.goal_id, 'TOOL-02', ['CAP-004'], identityInputs,
        [root], ['overview'],
      ))
      goalNodeIds.push(addNode(
        nodes, goal.goal_id, 'TOOL-06', ['CAP-014'], identityInputs,
        [root], ['audit'],
      ))
    }
    decisions.push({
      goal_id: goal.goal_id,
      answerability: [
        'event_end_state', 'event_summary', 'cause_or_responsibility',
      ].includes(resolved.kind) ? 'partial' : 'supported',
      node_ids: goalNodeIds,
      reason_codes: [
        ...resolved.reasonCodes,
        `${resolved.kind}_grounded`,
      ],
    })
  }
  const plan: P1SemanticPlan = {
    schema_version: 'country_outage_p1_semantic_plan_v2',
    user_goal_plan: clone(userGoalPlan),
    grounding_plan: {
      plan_revision: 'grounding-plan-v2',
      identity: identity(binding),
      decisions,
      nodes,
      authorization_scope: ['country_outage:read'],
      validation: { status: 'pending', errors: [] },
    },
  }
  const errors = validator.validate(plan, binding)
  if (errors.length > 0) {
    plan.grounding_plan.validation = { status: 'rejected', errors }
    throw new P1SemanticPlanError(
      'grounding_plan_rejected',
      `S3 GroundingPlan 被拒绝：${errors.join('; ')}`,
    )
  }
  plan.grounding_plan.validation = { status: 'passed', errors: [] }
  return plan
}

function canonicalQuestion(resolved: ResolvedGoal): string {
  switch (resolved.kind) {
    case 'event_summary': return '这次事件发生了什么？'
    case 'event_identity': return '这页绑定的是哪个 publication 和 revision，数据到哪里，是否最终？'
    case 'observation_window': return '这次事件的观测窗口是什么？'
    case 'detection_time': return '异常是什么时候开始的？'
    case 'event_end_state': return 'event_end_at_utc 为 null 时，事件结束了吗？'
    case 'recovery_status': return '从这页看已经恢复了吗？'
    case 'prefix_peak': return '这个峰值是多少？'
    case 'current_prefix_state': return '数据截止时还剩多少路由不可见？'
    case 'current_scope': return '这次观测覆盖多大范围？'
    case 'top_affected_asns': return '页面列出的前五个受影响 AS 是哪些？'
    case 'asn_detail': return `AS${resolved.asn} 的情况呢？`
    case 'remaining_vs_peak': return '峰值之后还有多少前缀持续异常？'
    case 'address_family_change': return resolved.addressFamily === 'ipv6'
      ? '那 IPv6 呢？'
      : '固定前缀可见 IPv4 地址规模最大下降了多少？'
    case 'address_family_compare': return 'IPv4 和 IPv6 哪个变化更明显？'
    case 'metric_semantics': return '“中断前缀”“完全中断前缀”和“不可见方向”分别是什么意思？'
    case 'new_prefix_resources': return '窗口内新出现了多少 IPv4 和 IPv6 前缀？'
    case 'path_sample': return '给我看一个实际路径样本。'
    case 'evidence_trace': return '证据在哪里？'
    case 'data_completeness': return '这份页面数据完整吗，还缺什么？'
    case 'rrc25_proof_boundary': return '仅凭这页 RRC25 数据能证明什么、不能证明什么？'
    case 'fact_timeline': return '这次事件发生了什么？'
    case 'cause_or_responsibility': return '所以到底是谁造成的？'
    default: return ''
  }
}

function answerability(value: string): P1SemanticAnswerability {
  if (value === 'answerable') return 'supported'
  if (
    value === 'partial'
    || value === 'clarify'
    || value === 'unsupported'
    || value === 'invalid_data'
  ) return value
  return 'invalid_data'
}

function adaptEvidence(
  bundle: P1FactBundle,
  values: Awaited<ReturnType<P1DeterministicQuestionEngine['answer']>>['evidence'],
): P1RuntimeV2Evidence[] {
  return values.map((item) => ({
    evidence_ref: item.evidence_ref,
    source: item.source,
    field_path: item.evidence_ref,
    value: item.value,
    unit: item.unit,
    observed_at_utc: item.observed_at_utc,
    incident_id: bundle.binding.incident_id,
    publication_id: item.publication_id,
    revision: item.revision,
    collector_id: 'rrc25',
  }))
}

function syntheticResolutionEvidence(
  binding: P1ConversationBinding,
): P1RuntimeV2Evidence {
  return {
    evidence_ref: 'resolution.data_through',
    source: 'resolution',
    field_path: 'data_through',
    value: binding.data_through,
    unit: 'UTC',
    observed_at_utc: binding.data_through,
    incident_id: binding.incident_id,
    publication_id: binding.publication_id,
    revision: binding.revision,
    collector_id: 'rrc25',
  }
}

function syntheticSeriesEvidence(
  bundle: P1FactBundle,
  metric: string,
): P1RuntimeV2Evidence {
  const values = bundle.series.tracks?.[metric]
  return {
    evidence_ref: `series.${metric}.point_count`,
    source: 'series',
    field_path: `tracks.${metric}`,
    value: Array.isArray(values) ? values.length : bundle.series.point_count,
    unit: 'state_point',
    observed_at_utc: bundle.binding.data_through,
    incident_id: bundle.binding.incident_id,
    publication_id: bundle.binding.publication_id,
    revision: bundle.binding.revision,
    collector_id: 'rrc25',
  }
}

function syntheticAuditEvidence(
  bundle: P1FactBundle,
  field: 'dataset_id' | 'causal_boundary',
): P1RuntimeV2Evidence {
  const value = bundle.audit[field]
  return {
    evidence_ref: `audit.${field}`,
    source: 'audit',
    field_path: field,
    value: typeof value === 'string' ? value : null,
    unit: null,
    observed_at_utc: bundle.binding.data_through,
    incident_id: bundle.binding.incident_id,
    publication_id: bundle.binding.publication_id,
    revision: bundle.binding.revision,
    collector_id: 'rrc25',
  }
}

function syntheticEventEndEvidence(
  bundle: P1FactBundle,
): P1RuntimeV2Evidence[] {
  const common = {
    incident_id: bundle.binding.incident_id,
    publication_id: bundle.binding.publication_id,
    revision: bundle.binding.revision,
    collector_id: 'rrc25' as const,
    source: 'overview' as const,
    observed_at_utc: bundle.binding.data_through,
  }
  return [
    {
      ...common,
      evidence_ref: 'event.event_end_at_utc',
      field_path: 'event.event_end_at_utc',
      value: null,
      unit: 'UTC',
    },
    {
      ...common,
      evidence_ref: 'event.event_duration_seconds',
      field_path: 'event.event_duration_seconds',
      value: null,
      unit: 'second',
    },
  ]
}

function syntheticScopePeakEvidence(
  bundle: P1FactBundle,
): P1RuntimeV2Evidence[] {
  const values = [
    ['affected_asn_count', bundle.overview.peaks.affected_asn_count],
    [
      'route_interrupted_asn_count',
      bundle.overview.peaks.route_interrupted_asn_count,
    ],
  ] as const
  const common = {
    incident_id: bundle.binding.incident_id,
    publication_id: bundle.binding.publication_id,
    revision: bundle.binding.revision,
    collector_id: 'rrc25' as const,
    source: 'overview' as const,
  }
  return values.flatMap(([metric, peak]) => {
    if (
      typeof peak?.value !== 'number'
      || !Number.isFinite(peak.value)
      || typeof peak.state_point_utc !== 'string'
      || !peak.state_point_utc
    ) {
      throw new P1ReadModelError(
        'metric_missing',
        `current_scope 缺少 ${metric} 峰值或时点`,
      )
    }
    return [
      {
        ...common,
        evidence_ref: `peaks.${metric}.value`,
        field_path: `peaks.${metric}.value`,
        value: peak.value,
        unit: 'asn',
        observed_at_utc: peak.state_point_utc,
      },
      {
        ...common,
        evidence_ref: `peaks.${metric}.state_point_utc`,
        field_path: `peaks.${metric}.state_point_utc`,
        value: peak.state_point_utc,
        unit: 'UTC',
        observed_at_utc: peak.state_point_utc,
      },
    ]
  })
}

interface TimelineFactNode {
  kind: string
  atUtc: string
  value: string | number
  unit: string
  evidenceRef: string
  source: P1RuntimeV2Evidence['source']
  fieldPath: string
}

function timelineSeriesPeak(
  bundle: P1FactBundle,
  metric: string,
): { value: number; atUtc: string } {
  const values = bundle.series.tracks?.[metric]
  const timestamps = bundle.series.timestamps
  if (!Array.isArray(values) || !Array.isArray(timestamps)) {
    throw new P1ReadModelError(
      'timeline_node_missing',
      `事实时间线缺少 ${metric} 时序`,
    )
  }
  let maximum = Number.NEGATIVE_INFINITY
  let maximumIndex = -1
  for (const [index, value] of values.entries()) {
    if (typeof value !== 'number' || !Number.isFinite(value)) continue
    if (value > maximum) {
      maximum = value
      maximumIndex = index
    }
  }
  const atUtc = timestamps[maximumIndex]
  if (maximumIndex < 0 || typeof atUtc !== 'string' || !atUtc) {
    throw new P1ReadModelError(
      'timeline_node_missing',
      `事实时间线无法确定 ${metric} 首个峰值`,
    )
  }
  return { value: maximum, atUtc }
}

function timelineNodes(bundle: P1FactBundle): TimelineFactNode[] {
  const peaks = bundle.overview.peaks
  const interrupted = timelineSeriesPeak(bundle, 'interrupted_prefix_count')
  const complete = timelineSeriesPeak(
    bundle,
    'completely_interrupted_prefix_count',
  )
  const affected = timelineSeriesPeak(bundle, 'affected_asn_count')
  for (const [label, seriesPeak, overviewPeak] of [
    ['interrupted_prefix_count', interrupted, peaks.interrupted_prefix_count],
    [
      'completely_interrupted_prefix_count',
      complete,
      peaks.completely_interrupted_prefix_count,
    ],
    ['affected_asn_count', affected, peaks.affected_asn_count],
  ] as const) {
    if (
      overviewPeak?.value !== seriesPeak.value
      || overviewPeak?.state_point_utc !== seriesPeak.atUtc
    ) {
      throw new P1ReadModelError(
        'evidence_value_conflict',
        `overview 与 series 的 ${label} 峰值不一致`,
      )
    }
  }
  const detected = bundle.overview.event.detected_at_utc
  if (typeof detected !== 'string' || !detected) {
    throw new P1ReadModelError(
      'timeline_node_missing',
      '事实时间线缺少页面检测时点',
    )
  }
  const current = bundle.overview.current.interrupted_prefix_count
  if (typeof current !== 'number' || !Number.isFinite(current)) {
    throw new P1ReadModelError(
      'timeline_node_missing',
      '事实时间线缺少数据截止时中断前缀数',
    )
  }
  const dataThrough = bundle.binding.data_through
  if (typeof dataThrough !== 'string' || !dataThrough) {
    throw new P1ReadModelError(
      'timeline_node_missing',
      '事实时间线缺少 data_through',
    )
  }
  return [
    {
      kind: 'window_start',
      atUtc: bundle.binding.window_start_utc,
      value: bundle.binding.window_start_utc,
      unit: 'UTC',
      evidenceRef: 'resolution.window_start_utc',
      source: 'resolution',
      fieldPath: 'window_start_utc',
    },
    {
      kind: 'detected',
      atUtc: detected,
      value: detected,
      unit: 'UTC',
      evidenceRef: 'overview.event.detected_at_utc',
      source: 'overview',
      fieldPath: 'event.detected_at_utc',
    },
    {
      kind: 'interrupted_prefix_peak',
      atUtc: interrupted.atUtc,
      value: interrupted.value,
      unit: 'prefix',
      evidenceRef: 'overview.peaks.interrupted_prefix_count',
      source: 'overview',
      fieldPath: 'peaks.interrupted_prefix_count',
    },
    {
      kind: 'completely_interrupted_prefix_peak',
      atUtc: complete.atUtc,
      value: complete.value,
      unit: 'prefix',
      evidenceRef: 'overview.peaks.completely_interrupted_prefix_count',
      source: 'overview',
      fieldPath: 'peaks.completely_interrupted_prefix_count',
    },
    {
      kind: 'affected_asn_peak',
      atUtc: affected.atUtc,
      value: affected.value,
      unit: 'asn',
      evidenceRef: 'series.extrema.affected_asn_count',
      source: 'series',
      fieldPath: 'extrema.affected_asn_count',
    },
    {
      kind: 'data_through',
      atUtc: dataThrough,
      value: current,
      unit: 'prefix',
      evidenceRef: 'overview.current.interrupted_prefix_count',
      source: 'overview',
      fieldPath: 'current.interrupted_prefix_count',
    },
  ]
}

function syntheticTimelineEvidence(
  bundle: P1FactBundle,
): P1RuntimeV2Evidence[] {
  const common = {
    incident_id: bundle.binding.incident_id,
    publication_id: bundle.binding.publication_id,
    revision: bundle.binding.revision,
    collector_id: 'rrc25' as const,
  }
  const nodes = timelineNodes(bundle)
  const sourceEvidence = nodes.map((node) => ({
    ...common,
    evidence_ref: node.evidenceRef,
    source: node.source,
    field_path: node.fieldPath,
    value: node.value,
    unit: node.unit,
    observed_at_utc: node.atUtc,
  }))
  const orderedNodeEvidence = nodes.map((node, index) => ({
    ...common,
    evidence_ref: `derived.fact_timeline.ordered_fact_nodes.${index}`,
    source: 'derived' as const,
    field_path: `fact_timeline.ordered_fact_nodes.${index}.${node.kind}`,
    value: node.value,
    unit: node.unit,
    observed_at_utc: node.atUtc,
  }))
  return [
    ...sourceEvidence,
    ...orderedNodeEvidence,
    {
      ...common,
      evidence_ref: 'overview.event.event_end_at_utc',
      source: 'overview',
      field_path: 'event.event_end_at_utc',
      value: null,
      unit: 'UTC',
      observed_at_utc: bundle.binding.data_through,
    },
    {
      ...common,
      evidence_ref: 'derived.fact_timeline.terminal_unknown',
      source: 'derived',
      field_path: 'fact_timeline.terminal_unknown.event_end_unknown',
      value: 'event_end_unknown',
      unit: null,
      observed_at_utc: bundle.binding.data_through,
    },
  ]
}

function boundaryResult(
  goal: P1UserGoal,
  answerabilityValue: 'clarify' | 'unsupported' | 'invalid_data',
  text: string,
): P1SemanticGoalResult {
  return {
    goal_id: goal.goal_id,
    requested_goal: goal.requested_goal,
    normalized_kind: goal.normalized_kind,
    answerability: answerabilityValue,
    text,
    evidence_refs: [],
    limitations: [text],
  }
}

function overall(values: P1SemanticAnswerability[]): P1SemanticAnswerability {
  const set = new Set(values)
  if (
    set.has('invalid_data')
    && (set.has('supported') || set.has('partial'))
  ) return 'partial'
  if (set.has('invalid_data')) return 'invalid_data'
  if (
    set.has('partial')
    || ((set.has('supported') || set.has('partial'))
      && (set.has('unsupported') || set.has('clarify')))
  ) return 'partial'
  if (set.has('supported')) return 'supported'
  if (set.has('unsupported')) return 'unsupported'
  return 'clarify'
}

function transitionFor(
  before: P1ConversationState,
  resolvedGoals: ResolvedGoal[],
  results: P1SemanticGoalResult[],
  turnNumber: number,
  bindingActive: boolean,
): { transition: P1StateTransition, after: P1ConversationState } {
  const transition: P1StateTransition = {
    inherit: bindingActive ? ['event_binding'] : [],
    set: {},
    clear: [],
    reason_codes: [],
  }
  const clear = new Set<string>()
  const set = transition.set
  const executable = new Set(['supported', 'partial'])
  let eventSwitchRebound = false
  for (const resolved of resolvedGoals) {
    const result = results.find((item) => item.goal_id === resolved.goal.goal_id)
    if (!result) continue
    if (resolved.kind === 'event_switch') {
      transition.inherit = transition.inherit.filter(
        (item) => item !== 'event_binding',
      )
      for (const key of [
        'event_binding', 'topic', 'asn', 'address_family', 'metric',
        'evidence_anchor',
      ]) clear.add(key)
      if (result.answerability === 'supported') {
        eventSwitchRebound = true
        clear.add('pending_clarification')
        transition.reason_codes.push('event_switch_rebound_atomically')
      } else {
        set.pending_clarification = 'event_reference'
        transition.reason_codes.push('pending_event_switch_requires_reference')
      }
      continue
    }
    if (result.answerability === 'clarify') {
      set.pending_clarification = before.pending_clarification
        === 'event_reference' && !eventSwitchRebound
        ? 'event_reference'
        : `goal:${resolved.goal.goal_id}`
      if (
        resolved.goal.ambiguity === 'blocking'
        && resolved.kind === 'asn_detail'
      ) clear.add('asn')
      if (
        resolved.goal.ambiguity === 'blocking'
        && (
          resolved.kind === 'address_family_compare'
          || resolved.kind === 'address_family_change'
        )
      ) clear.add('address_family')
      transition.reason_codes.push('pending_goal_clarification')
      continue
    }
    if (!executable.has(result.answerability)) continue
    clear.add('pending_clarification')
    for (const field of resolved.inherited) transition.inherit.push(field)
    if (resolved.kind === 'event_summary') set.topic = 'event_summary'
    if (
      resolved.kind === 'prefix_peak'
      || resolved.kind === 'remaining_vs_peak'
      || resolved.kind === 'recovery_status'
      || resolved.kind === 'fact_timeline'
    ) {
      set.topic = 'timeline'
      set.metric = 'interrupted_prefix_count'
      set.evidence_anchor = result.evidence_refs[0] ?? null
    }
    if (resolved.kind === 'current_prefix_state') {
      set.topic = 'timeline'
      set.metric = 'interrupted_prefix_count'
      set.evidence_anchor = result.evidence_refs[0] ?? null
    }
    if (resolved.kind === 'asn_detail' && resolved.asn !== null) {
      set.topic = 'asn'
      set.asn = resolved.asn
      clear.add('address_family')
      clear.add('metric')
      clear.add('evidence_anchor')
      transition.reason_codes.push(
        before.asn === null ? 'explicit_asn' : 'explicit_asn_correction',
      )
    }
    if (resolved.kind === 'top_affected_asns') {
      set.topic = 'asn'
      clear.add('asn')
      clear.add('address_family')
      clear.add('metric')
      set.evidence_anchor = result.evidence_refs[0] ?? null
    }
    if (
      resolved.kind === 'address_family_compare'
      || resolved.kind === 'address_family_change'
    ) {
      set.topic = 'address_family'
      set.address_family = resolved.addressFamily ?? 'both'
      clear.add('asn')
      clear.add('metric')
      clear.add('evidence_anchor')
    }
    if (resolved.kind === 'path_sample') {
      set.topic = 'path'
      clear.add('asn')
      clear.add('address_family')
      clear.add('metric')
      clear.add('evidence_anchor')
      transition.reason_codes.push('topic_switch_isolated')
    }
    if (resolved.kind === 'evidence_trace') {
      set.topic = 'evidence'
      set.evidence_anchor = result.evidence_refs[0] ?? null
    }
    if (resolved.kind === 'metric_semantics') {
      set.topic = 'metric_semantics'
      clear.add('asn')
      clear.add('address_family')
      clear.add('metric')
      set.evidence_anchor = result.evidence_refs[0] ?? null
    }
    if (resolved.kind === 'new_prefix_resources') {
      set.topic = 'address_family'
      set.address_family = 'both'
      clear.add('asn')
      clear.add('metric')
      set.evidence_anchor = result.evidence_refs[0] ?? null
    }
    if (resolved.kind === 'data_completeness') {
      set.topic = 'evidence'
      set.evidence_anchor = result.evidence_refs[0] ?? null
    }
    if (resolved.kind === 'rrc25_proof_boundary') {
      set.topic = 'boundary'
      clear.add('asn')
      clear.add('address_family')
      clear.add('metric')
      set.evidence_anchor = result.evidence_refs[0] ?? null
    }
    if (resolved.kind === 'cause_or_responsibility') {
      set.topic = 'boundary'
      clear.add('asn')
      clear.add('address_family')
      clear.add('metric')
      transition.reason_codes.push('boundary_context_isolated')
    }
  }
  transition.inherit = [...new Set(transition.inherit)]
  transition.clear = [...clear]
  const after = clone(before) as unknown as Record<string, unknown>
  for (const key of transition.clear) {
    if (key in after) after[key] = null
  }
  for (const [key, value] of Object.entries(transition.set)) after[key] = value
  const changed = JSON.stringify(after) !== JSON.stringify(before)
  if (changed) after.last_committed_turn_number = turnNumber
  return {
    transition,
    after: after as unknown as P1ConversationState,
  }
}

function evidenceForNode(
  node: P1GroundingNode,
  evidence: P1RuntimeV2Evidence[],
): string[] {
  const sourcesByUnit: Record<string, P1RuntimeV2Evidence['source'][]> = {
    'TOOL-01': ['resolution'],
    'TOOL-02': ['overview'],
    'TOOL-03': ['series'],
    'TOOL-04': ['asns'],
    'TOOL-05': ['paths'],
    'TOOL-06': ['audit'],
    'OP-01': ['derived'],
    'OP-02': ['derived'],
    'OP-03': ['derived'],
  }
  const sources = new Set(sourcesByUnit[node.execution_unit] ?? [])
  return evidence
    .filter((item) => sources.has(item.source))
    .map((item) => item.evidence_ref)
}

export class P1RuntimeV2ConversationService {
  private readonly conversations = new Map<string, StoredConversation>()
  private readonly creationIdempotency = new Map<string, {
    conversation_id: string
    request_fingerprint: string
  }>()
  private readonly provider: P1GeneralReadModelProvider
  private readonly planner: P1UserGoalPlanner
  private readonly engine: P1DeterministicQuestionEngine
  private readonly grounder = new P1RuntimeV2Grounder()
  private readonly ttlMs: number
  private readonly now: () => Date

  constructor(options: P1RuntimeV2ConversationServiceOptions) {
    this.provider = options.provider
    this.planner = options.planner
    this.engine = new P1DeterministicQuestionEngine(options.provider)
    this.ttlMs = options.ttlMs ?? 30 * 60 * 1000
    this.now = options.now ?? (() => new Date())
  }

  private assertNotExpired(stored: StoredConversation): void {
    if (Date.parse(stored.descriptor.expires_at) <= this.now().getTime()) {
      throw new CountryOutageHttpError(
        410,
        'conversation_expired',
        '会话已到期，请从当前事件重新绑定；旧回答不会改写为新事实',
      )
    }
  }

  private require(
    principal: CountryOutagePrincipal,
    conversationId: string,
  ): StoredConversation {
    const stored = this.conversations.get(conversationId)
    if (!stored || stored.owner !== owner(principal)) {
      throw new CountryOutageHttpError(
        404,
        'conversation_not_found',
        '会话不存在或无权访问',
      )
    }
    this.assertNotExpired(stored)
    return stored
  }

  private async loadAuthorizedBundle(
    principal: CountryOutagePrincipal,
    request: Pick<CreateP1RuntimeV2ConversationRequest,
      'event_reference' | 'publication_id' | 'revision'>,
    signal?: AbortSignal,
  ): Promise<P1FactBundle> {
    const permission = readP1RuntimeV2PermissionCandidate(principal)
    throwIfP1RuntimeV2Cancelled(signal)
    const resolved = await this.provider.resolve(request.event_reference, signal)
    throwIfP1RuntimeV2Cancelled(signal)
    assertRequestBinding(request, resolved)
    authorizeP1RuntimeV2Country(permission, resolved.country_code)
    const bundle = await this.provider.load(
      request.event_reference,
      request.publication_id,
      request.revision,
      signal,
    )
    throwIfP1RuntimeV2Cancelled(signal)
    if (!bindingEquals(resolved, bundle.binding)) {
      throw new P1RuntimeV2SingleTurnError(
        'publication_identity_conflict',
        'EvidenceState 与解析身份不一致',
      )
    }
    assertFactBundleIdentity(bundle)
    return bundle
  }

  private async loadAuthorizedTargetReference(
    principal: CountryOutagePrincipal,
    eventReference: string,
    signal?: AbortSignal,
  ): Promise<P1FactBundle> {
    const permission = readP1RuntimeV2PermissionCandidate(principal)
    throwIfP1RuntimeV2Cancelled(signal)
    const resolved = await this.provider.resolve(eventReference, signal)
    throwIfP1RuntimeV2Cancelled(signal)
    assertRequestBinding({
      event_reference: eventReference,
      publication_id: resolved.publication_id,
      revision: resolved.revision,
    }, resolved)
    authorizeP1RuntimeV2Country(permission, resolved.country_code)
    const bundle = await this.provider.load(
      resolved.legacy_reference,
      resolved.publication_id,
      resolved.revision,
      signal,
    )
    throwIfP1RuntimeV2Cancelled(signal)
    if (!bindingEquals(resolved, bundle.binding)) {
      throw new P1RuntimeV2SingleTurnError(
        'publication_identity_conflict',
        '目标事件 EvidenceState 与解析身份不一致',
      )
    }
    assertFactBundleIdentity(bundle)
    return bundle
  }

  async createConversation(
    principal: CountryOutagePrincipal,
    request: CreateP1RuntimeV2ConversationRequest,
    signal?: AbortSignal,
  ): Promise<{
    conversation: P1RuntimeV2ConversationDescriptor
    deduplicated: boolean
  }> {
    if (!request.idempotency_key.trim()) {
      throw new P1SemanticPlanError('invalid_idempotency_key', '幂等键不能为空')
    }
    const key = `${owner(principal)}\u0000${request.idempotency_key}`
    const requestFingerprint = JSON.stringify([
      normalizeReference(request.event_reference),
      request.publication_id,
      request.revision,
    ])
    const existing = this.creationIdempotency.get(key)
    if (existing) {
      if (existing.request_fingerprint !== requestFingerprint) {
        throw new P1SemanticPlanError(
          'idempotency_conflict',
          '同一幂等键不能绑定不同事件、publication 或 revision',
        )
      }
      return {
        conversation: clone(this.require(
          principal,
          existing.conversation_id,
        ).descriptor),
        deduplicated: true,
      }
    }
    const bundle = await this.loadAuthorizedBundle(principal, request, signal)
    const now = this.now()
    const conversationId = `p1v2_${randomUUID().replaceAll('-', '')}`
    const descriptor: P1RuntimeV2ConversationDescriptor = {
      schema_version: P1_RUNTIME_V2_CONVERSATION_SCHEMA,
      conversation_id: conversationId,
      binding: clone(bundle.binding),
      binding_generation: 1,
      active_binding_generation: 1,
      evidence_state: {
        immutable: true,
        incident_id: bundle.binding.incident_id,
        publication_id: bundle.binding.publication_id,
        revision: bundle.binding.revision,
        collector_id: 'rrc25',
        loaded_at: now.toISOString(),
      },
      dialog_state: initialState(),
      turns: [],
      binding_history: [{
        generation: 1,
        incident_id: bundle.binding.incident_id,
        publication_id: bundle.binding.publication_id,
        revision: bundle.binding.revision,
        switched_at: now.toISOString(),
      }],
      expires_at: new Date(now.getTime() + this.ttlMs).toISOString(),
      created_at: now.toISOString(),
    }
    this.conversations.set(conversationId, {
      owner: owner(principal),
      descriptor,
      bundle,
      idempotency: new Map(),
      rebindIdempotency: new Map(),
      active: new Map(),
    })
    this.creationIdempotency.set(key, {
      conversation_id: conversationId,
      request_fingerprint: requestFingerprint,
    })
    return { conversation: clone(descriptor), deduplicated: false }
  }

  async getConversation(
    principal: CountryOutagePrincipal,
    conversationId: string,
  ): Promise<P1RuntimeV2ConversationDescriptor> {
    return clone(this.require(principal, conversationId).descriptor)
  }

  async cancelTurn(
    principal: CountryOutagePrincipal,
    conversationId: string,
    turnId: string,
  ): Promise<{ turn_id: string, state: 'cancel_requested' | 'not_active' }> {
    const stored = this.require(principal, conversationId)
    const turn = stored.descriptor.turns.find((item) => item.turn_id === turnId)
    if (!turn) {
      throw new CountryOutageHttpError(
        404,
        'turn_not_found',
        '轮次不存在或不属于当前会话',
      )
    }
    const controller = stored.active.get(turnId)
    if (!controller) return { turn_id: turnId, state: 'not_active' }
    controller.abort()
    return { turn_id: turnId, state: 'cancel_requested' }
  }

  async createTurn(
    principal: CountryOutagePrincipal,
    conversationId: string,
    request: CreateP1RuntimeV2ConversationTurnRequest,
    signal?: AbortSignal,
  ): Promise<{ turn: P1RuntimeV2ConversationTurn, deduplicated: boolean }> {
    if (!request.question.trim() || request.question.length > 2_000) {
      throw new P1SemanticPlanError(
        'invalid_question',
        'question 必须是 1 至 2,000 字符的非空文本',
      )
    }
    if (!request.idempotency_key.trim()) {
      throw new P1SemanticPlanError('invalid_idempotency_key', '幂等键不能为空')
    }
    const stored = this.require(principal, conversationId)
    const idempotencyNamespace = [
      stored.descriptor.binding_generation,
      request.idempotency_key,
    ].join('\u0000')
    const existing = stored.idempotency.get(idempotencyNamespace)
    if (existing) {
      if (existing.question !== request.question) {
        throw new P1SemanticPlanError(
          'idempotency_conflict',
          '同一幂等键不能用于不同问题',
        )
      }
      return { turn: clone(existing.turn), deduplicated: true }
    }
    if (stored.active.size > 0) {
      throw new CountryOutageHttpError(
        409,
        'conversation_busy',
        '当前会话已有一轮正在处理',
        true,
      )
    }
    const turnNumber = stored.descriptor.turns.length + 1
    const turnId = `p1v2turn_${randomUUID().replaceAll('-', '')}`
    const turn: P1RuntimeV2ConversationTurn = {
      turn_id: turnId,
      turn_number: turnNumber,
      question: request.question,
      state: 'understanding',
      created_at: this.now().toISOString(),
    }
    stored.descriptor.turns.push(turn)
    stored.idempotency.set(idempotencyNamespace, {
      question: request.question,
      turn,
    })
    const controller = new AbortController()
    const abort = (): void => controller.abort()
    signal?.addEventListener('abort', abort, { once: true })
    if (signal?.aborted) controller.abort()
    stored.active.set(turnId, controller)
    const before = clone(stored.descriptor.dialog_state)
    const beforeActiveBindingGeneration =
      stored.descriptor.active_binding_generation
    const bindingActive = beforeActiveBindingGeneration
      === stored.descriptor.binding_generation
    try {
      assertFactBundleIdentity(stored.bundle)
      const permission = readP1RuntimeV2PermissionCandidate(principal)
      throwIfP1RuntimeV2Cancelled(controller.signal)
      const preflight = await this.provider.resolve(
        stored.descriptor.binding.legacy_reference,
        controller.signal,
      )
      throwIfP1RuntimeV2Cancelled(controller.signal)
      if (!bindingEquals(stored.descriptor.binding, preflight)) {
        throw new P1RuntimeV2SingleTurnError(
          'revision_drift',
          '活动 publication/revision 已变化；本轮未执行且旧状态保持不变',
        )
      }
      let authorization = authorizeP1RuntimeV2Country(
        permission,
        preflight.country_code,
      )
      const userGoalPlan = await this.planner.plan(
        request.question,
        {
          event_type: 'country_outage',
          country_code: preflight.country_code,
          event_reference: preflight.legacy_reference,
          has_dialog_state: true,
          dialog_state: clone(before),
        },
        controller.signal,
      )
      let resolvedGoals = userGoalPlan.goals.map((goal) =>
        resolveGoal(goal, before)
      )
      const targetReference = uniqueEventSwitchReference(resolvedGoals)
      let executionBundle = stored.bundle
      let executionBinding = preflight
      let executionReference = preflight.legacy_reference
      let targetSwitchBundle: P1FactBundle | null = null
      if (targetReference !== null) {
        targetSwitchBundle = await this.loadAuthorizedTargetReference(
          principal,
          targetReference,
          controller.signal,
        )
        executionBundle = targetSwitchBundle
        executionBinding = targetSwitchBundle.binding
        executionReference = targetSwitchBundle.binding.legacy_reference
        authorization = authorizeP1RuntimeV2Country(
          permission,
          executionBinding.country_code,
        )
        resolvedGoals = userGoalPlan.goals.map((goal) =>
          resolveGoal(goal, initialState())
        )
      }
      const semanticPlan = buildGroundingPlan(
        userGoalPlan,
        resolvedGoals,
        executionBinding,
        executionReference,
        this.grounder,
        targetSwitchBundle !== null || bindingActive,
        targetSwitchBundle !== null,
      )
      turn.state = 'executing'
      const results: P1SemanticGoalResult[] = []
      const goalExecutionFailures = new Map<string, string>()
      const evidence = new Map<string, P1RuntimeV2Evidence>()
      const goalEvidenceRefs = new Map<string, Set<string>>()
      if (semanticPlan.grounding_plan.nodes.some(
        (node) => node.execution_unit === 'TOOL-01',
      )) {
        evidence.set(
          'resolution.data_through',
          syntheticResolutionEvidence(executionBinding),
        )
      }
      for (const resolved of resolvedGoals) {
        const decision = semanticPlan.grounding_plan.decisions.find(
          (item) => item.goal_id === resolved.goal.goal_id,
        )!
        if (decision.answerability === 'clarify') {
          const reasonCode = decision.reason_codes[0] ?? ''
          results.push(boundaryResult(
            resolved.goal,
            'clarify',
            resolved.kind === 'event_switch'
              ? '请提供唯一 country_outage 事件引用、明确检测时间或从明确候选中选择；我不会默认“最近一次”，也不会复用旧事件数值。'
              : p1RuntimeV2BoundaryText(reasonCode),
          ))
          continue
        }
        if (decision.answerability === 'unsupported') {
          const reasonCode = decision.reason_codes[0] ?? ''
          results.push(boundaryResult(
            resolved.goal,
            'unsupported',
            p1RuntimeV2BoundaryText(reasonCode),
          ))
          continue
        }
        if (decision.answerability === 'invalid_data') {
          const reasonCode = decision.reason_codes[0] ?? ''
          results.push(boundaryResult(
            resolved.goal,
            'invalid_data',
            p1RuntimeV2BoundaryText(reasonCode),
          ))
          continue
        }
        if (resolved.kind === 'event_switch' && targetSwitchBundle !== null) {
          const refs = new Set(['resolution.data_through'])
          goalEvidenceRefs.set(resolved.goal.goal_id, refs)
          results.push({
            goal_id: resolved.goal.goal_id,
            requested_goal: resolved.goal.requested_goal,
            normalized_kind: resolved.goal.normalized_kind,
            answerability: 'supported',
            text: `已验证并切换到 ${executionBinding.legacy_reference}，绑定 publication ${executionBinding.publication_id}、revision ${executionBinding.revision}、collector RRC25。旧回答保留原身份。`,
            evidence_refs: [...refs],
            limitations: [
              '事件切换只改变后续执行身份，不会把旧回答改写成新 publication 事实。',
            ],
          })
          continue
        }
        throwIfP1RuntimeV2Cancelled(controller.signal)
        let legacy: Awaited<ReturnType<P1DeterministicQuestionEngine['answer']>>
        try {
          legacy = await this.engine.answer({
            conversationId,
            turnId,
            turnNumber,
            question: canonicalQuestion(resolved),
            state: clone(before),
            bundle: executionBundle,
            signal: controller.signal,
          })
        } catch (error) {
          if (controller.signal.aborted) throw error
          if (error instanceof P1ReadModelError) {
            goalExecutionFailures.set(resolved.goal.goal_id, error.code)
            results.push({
              goal_id: resolved.goal.goal_id,
              requested_goal: resolved.goal.requested_goal,
              normalized_kind: resolved.goal.normalized_kind,
              answerability: 'invalid_data',
              text: `该子目标的确定性事实读取失败（${error.code}）；未用其他子目标或常识补齐。`,
              evidence_refs: [],
              limitations: [
                '该子目标没有可发布证据；同轮其他已验证子目标保持独立。',
              ],
            })
            continue
          }
          throw error
        }
        throwIfP1RuntimeV2Cancelled(controller.signal)
        if (!legacy.validation.passed) {
          if (legacy.answerability === 'invalid_data') {
            goalExecutionFailures.set(
              resolved.goal.goal_id,
              legacy.validation.errors[0] ?? 'invalid_data',
            )
            results.push({
              goal_id: resolved.goal.goal_id,
              requested_goal: resolved.goal.requested_goal,
              normalized_kind: resolved.goal.normalized_kind,
              answerability: 'invalid_data',
              text: legacy.answer_text,
              evidence_refs: [],
              limitations: [
                ...legacy.limitations,
                '该子目标没有可发布事实证据，空结果或无效数据不解释为 0。',
              ],
            })
            continue
          }
          throw new P1SemanticPlanError(
            'deterministic_execution_invalid',
            `确定性事实执行失败：${legacy.validation.errors.join('; ')}`,
          )
        }
        const adapted = adaptEvidence(executionBundle, legacy.evidence)
        const executedRefs = new Set<string>()
        for (const item of adapted) {
          evidence.set(item.evidence_ref, item)
          executedRefs.add(item.evidence_ref)
        }
        if (resolved.kind === 'address_family_compare') {
          for (const metric of [
            'fixed_visible_ipv4_address_count',
            'fixed_visible_ipv6_slash48_count',
          ]) {
            const item = syntheticSeriesEvidence(executionBundle, metric)
            evidence.set(item.evidence_ref, item)
            executedRefs.add(item.evidence_ref)
          }
        }
        if (resolved.kind === 'address_family_change') {
          const metric = resolved.addressFamily === 'ipv6'
            ? 'fixed_visible_ipv6_slash48_count'
            : 'fixed_visible_ipv4_address_count'
          const item = syntheticSeriesEvidence(executionBundle, metric)
          evidence.set(item.evidence_ref, item)
          executedRefs.add(item.evidence_ref)
        }
        if (resolved.kind === 'metric_semantics') {
          for (const metric of [
            'interrupted_prefix_count',
            'completely_interrupted_prefix_count',
            'invisible_direction_count',
          ]) {
            const item = syntheticSeriesEvidence(executionBundle, metric)
            evidence.set(item.evidence_ref, item)
            executedRefs.add(item.evidence_ref)
          }
        }
        if (resolved.kind === 'new_prefix_resources') {
          for (const metric of [
            'new_cumulative_ipv4_prefix_count',
            'new_cumulative_ipv6_prefix_count',
            'new_visible_ipv4_prefix_count',
            'new_visible_ipv6_prefix_count',
          ]) {
            const item = syntheticSeriesEvidence(executionBundle, metric)
            evidence.set(item.evidence_ref, item)
            executedRefs.add(item.evidence_ref)
          }
        }
        if (resolved.kind === 'data_completeness') {
          const seriesItem = syntheticSeriesEvidence(
            executionBundle,
            'interrupted_prefix_count',
          )
          const auditItem = syntheticAuditEvidence(
            executionBundle,
            'dataset_id',
          )
          for (const item of [seriesItem, auditItem]) {
            evidence.set(item.evidence_ref, item)
            executedRefs.add(item.evidence_ref)
          }
        }
        if (resolved.kind === 'event_end_state') {
          for (const item of syntheticEventEndEvidence(executionBundle)) {
            evidence.set(item.evidence_ref, item)
            executedRefs.add(item.evidence_ref)
          }
        }
        if (resolved.kind === 'current_scope') {
          for (const item of syntheticScopePeakEvidence(executionBundle)) {
            evidence.set(item.evidence_ref, item)
            executedRefs.add(item.evidence_ref)
          }
        }
        if (resolved.kind === 'rrc25_proof_boundary') {
          const item = syntheticAuditEvidence(
            executionBundle,
            'causal_boundary',
          )
          evidence.set(item.evidence_ref, item)
          executedRefs.add(item.evidence_ref)
        }
        if (resolved.kind === 'fact_timeline') {
          const timelineItems = syntheticTimelineEvidence(executionBundle)
          for (const item of timelineItems) {
            evidence.set(item.evidence_ref, item)
            executedRefs.add(item.evidence_ref)
          }
        }
        goalEvidenceRefs.set(resolved.goal.goal_id, executedRefs)
        const resultEvidence = [...executedRefs]
        results.push({
          goal_id: resolved.goal.goal_id,
          requested_goal: resolved.goal.requested_goal,
          normalized_kind: resolved.goal.normalized_kind,
          answerability: resolved.kind === 'fact_timeline'
            ? 'supported'
            : answerability(legacy.answerability),
          text: resolved.kind === 'fact_timeline'
            ? `按时间排序：观测窗口从 ${executionBundle.binding.window_start_utc} 开始；页面检测时间为 ${executionBundle.overview.event.detected_at_utc}；中断前缀在 ${executionBundle.overview.peaks.interrupted_prefix_count.state_point_utc} 达到峰值 ${executionBundle.overview.peaks.interrupted_prefix_count.value}；完全中断前缀在 ${executionBundle.overview.peaks.completely_interrupted_prefix_count.state_point_utc} 达到峰值 ${executionBundle.overview.peaks.completely_interrupted_prefix_count.value}；受影响 AS 在 ${executionBundle.overview.peaks.affected_asn_count.state_point_utc} 达到峰值 ${executionBundle.overview.peaks.affected_asn_count.value}；数据截至 ${executionBundle.binding.data_through} 时仍有 ${executionBundle.overview.current.interrupted_prefix_count} 个中断前缀；事件结束时点仍未知。这是事实时间线，不是因果链。`
            : resolved.kind === 'current_scope'
              ? `固定 cohort 含 ${executionBundle.overview.cohort.fixed_asn_count} 个 AS、${executionBundle.overview.cohort.fixed_prefix_count} 个前缀和 ${executionBundle.overview.cohort.independent_direction_relation_count} 个独立观察方向；窗口内累计 ${executionBundle.overview.affected_as_count} 个 AS 受影响、${executionBundle.overview.route_interrupted_as_count} 个 AS 的固定前缀曾全部不可见。逐槽受影响 AS 峰值为 ${executionBundle.overview.peaks.affected_asn_count.value}（${executionBundle.overview.peaks.affected_asn_count.state_point_utc}），固定前缀全部不可见 AS 峰值为 ${executionBundle.overview.peaks.route_interrupted_asn_count.value}（${executionBundle.overview.peaks.route_interrupted_asn_count.state_point_utc}）。累计人口与逐槽峰值不同，也不等于全国或真实用户影响。`
            : legacy.answer_text,
          evidence_refs: resultEvidence,
          limitations: resolved.kind === 'fact_timeline'
            ? [
                ...legacy.limitations,
                '相邻事实节点只表示时间顺序，不表示因果关系。',
                'event_end_at_utc 为 null，事件结束时点仍未知。',
              ]
            : legacy.limitations,
        })
      }
      turn.state = 'validating'
      throwIfP1RuntimeV2Cancelled(controller.signal)
      const postflight = await this.provider.resolve(
        executionReference,
        controller.signal,
      )
      throwIfP1RuntimeV2Cancelled(controller.signal)
      if (!bindingEquals(executionBinding, postflight)) {
        throw new P1RuntimeV2SingleTurnError(
          'revision_drift',
          '执行期间 publication/revision 漂移；回答和状态均未提交',
        )
      }
      const evidenceValues = [...evidence.values()]
      if (evidenceValues.some((item) =>
        item.incident_id !== executionBinding.incident_id
        || item.publication_id !== executionBinding.publication_id
        || item.revision !== executionBinding.revision
        || item.collector_id !== 'rrc25'
      )) {
        throw new P1SemanticPlanError(
          'answer_evidence_identity_conflict',
          '回答证据身份与当前 EvidenceState 不一致',
        )
      }
      this.assertNotExpired(stored)
      const { transition, after } = transitionFor(
        before,
        resolvedGoals,
        results,
        turnNumber,
        targetSwitchBundle !== null || bindingActive,
      )
      const dialogChanged = JSON.stringify(before) !== JSON.stringify(after)
      const changed = dialogChanged || targetSwitchBundle !== null
      const executionNodes = semanticPlan.grounding_plan.nodes.map((node) => {
        const scopedRefs = goalEvidenceRefs.get(node.goal_id) ?? new Set()
        const scopedEvidence = evidenceValues.filter((item) =>
          scopedRefs.has(item.evidence_ref)
        )
        let refs = node.execution_unit === 'TOOL-01'
          ? evidenceValues
            .filter((item) => item.source === 'resolution')
            .map((item) => item.evidence_ref)
          : evidenceForNode(node, scopedEvidence)
        if (node.execution_unit === 'TOOL-03') {
          const metrics = Array.isArray(node.inputs.metrics)
            ? node.inputs.metrics.map(String)
            : []
          refs = refs.filter((ref) =>
            metrics.some((metric) => ref.includes(metric))
          )
        }
        if (
          (node.execution_unit === 'OP-01' || node.execution_unit === 'OP-02')
          && refs.length === 0
        ) {
          refs = results
            .find((item) => item.goal_id === node.goal_id)
            ?.evidence_refs.filter((ref) =>
              ref.startsWith('address_family_extrema.')
            ) ?? []
        }
        return {
          node_id: node.node_id,
          execution_unit: node.execution_unit,
          capability_ids: clone(node.capability_ids),
          status: node.execution_unit !== 'TOOL-01'
            && goalExecutionFailures.has(node.goal_id)
            ? 'failed' as const : 'passed' as const,
          evidence_refs: refs,
          receipt_id: `${turnId}:${node.node_id}`,
          execution_mode: 'verified_evidence_state_read' as const,
        }
      })
      const answer: P1RuntimeV2ConversationTurnAnswer = {
        schema_version: P1_RUNTIME_V2_CONVERSATION_TURN_SCHEMA,
        conversation_id: conversationId,
        turn_id: turnId,
        turn_number: turnNumber,
        answerability: overall(results.map((item) => item.answerability)),
        binding: clone(postflight),
        semantic_plan: semanticPlan,
        results,
        answer_text: results.map((item) => item.text).join('\n'),
        evidence: evidenceValues,
        limitations: [...new Set(results.flatMap((item) => item.limitations))],
        unknowns: [
          ...results
            .filter((item) => item.answerability !== 'supported')
            .map((item) => item.requested_goal),
          ...(resolvedGoals.some((item) => item.kind === 'fact_timeline')
            ? ['event_end_at_utc：event_end_unknown']
            : []),
        ],
        execution_trace: {
          binding_preflight: 'passed',
          nodes: executionNodes,
          authorization,
          planner_outcome: 'accepted',
          model_generated_fact_count: 0,
          state_commit: changed ? 'committed' : 'none',
        },
        state_receipt: {
          before,
          proposed: transition,
          after: clone(changed ? after : before),
          status: changed ? 'committed' : 'none',
          transaction_checks: {
            plan_validated: true,
            permission_validated: true,
            execution_validated: true,
            evidence_validated: true,
            binding_revalidated: true,
            cancelled: false,
          },
        },
        validation: {
          user_goal_schema: 'passed',
          grounding_schema: 'passed',
          grounding_legality: 'passed',
          answer_evidence: 'passed',
          errors: [],
        },
        runtime_identity: {
          implementation: 'p1-runtime-v2-semantic-turn',
          contract_revision: 'p1-runtime-v2-s0-20260809-r2',
          language_layer: this.planner.identity,
          collector: 'rrc25',
        },
        completed_at: this.now().toISOString(),
      }
      if (dialogChanged) stored.descriptor.dialog_state = clone(after)
      if (targetSwitchBundle !== null) {
        const generation = stored.descriptor.binding_generation + 1
        stored.bundle = targetSwitchBundle
        stored.descriptor.binding = clone(targetSwitchBundle.binding)
        stored.descriptor.binding_generation = generation
        stored.descriptor.active_binding_generation = generation
        stored.descriptor.evidence_state = {
          immutable: true,
          incident_id: targetSwitchBundle.binding.incident_id,
          publication_id: targetSwitchBundle.binding.publication_id,
          revision: targetSwitchBundle.binding.revision,
          collector_id: 'rrc25',
          loaded_at: answer.completed_at,
        }
        stored.descriptor.binding_history.push({
          generation,
          incident_id: targetSwitchBundle.binding.incident_id,
          publication_id: targetSwitchBundle.binding.publication_id,
          revision: targetSwitchBundle.binding.revision,
          switched_at: answer.completed_at,
        })
        stored.idempotency.set([
          generation,
          request.idempotency_key,
        ].join('\u0000'), {
          question: request.question,
          turn,
        })
      } else if (transition.clear.includes('event_binding')) {
        stored.descriptor.active_binding_generation = null
      }
      turn.state = 'completed'
      turn.answer = answer
      turn.completed_at = answer.completed_at
    } catch (error) {
      const cancelled = controller.signal.aborted
      stored.descriptor.dialog_state = before
      stored.descriptor.active_binding_generation =
        beforeActiveBindingGeneration
      turn.state = cancelled ? 'cancelled' : 'failed'
      turn.completed_at = this.now().toISOString()
      turn.error = {
        code: cancelled
          ? 'cancelled'
          : error instanceof P1ReadModelError
            ? error.code
            : error instanceof CountryOutageHttpError
              ? error.code
            : error instanceof P1RuntimeV2SingleTurnError
              ? error.code
              : error instanceof P1SemanticPlanError
                ? error.code
                : 'turn_failed',
        message: cancelled
          ? '本轮已取消；回答、EvidenceState 和 DialogState 均未提交'
          : error instanceof Error ? error.message : '轮次失败关闭',
        retryable: error instanceof P1ReadModelError
          ? error.retryable
          : error instanceof CountryOutageHttpError
            ? error.retryable
          : error instanceof P1RuntimeV2SingleTurnError
            ? error.retryable
            : error instanceof P1SemanticPlanError
              ? error.retryable
              : false,
      }
    } finally {
      signal?.removeEventListener('abort', abort)
      stored.active.delete(turnId)
    }
    return { turn: clone(turn), deduplicated: false }
  }

  async rebind(
    principal: CountryOutagePrincipal,
    conversationId: string,
    request: CreateP1RuntimeV2ConversationRequest,
    signal?: AbortSignal,
  ): Promise<{
    conversation: P1RuntimeV2ConversationDescriptor
    previous_binding: P1ConversationBinding
  }> {
    const stored = this.require(principal, conversationId)
    if (!request.idempotency_key.trim()) {
      throw new P1SemanticPlanError('invalid_idempotency_key', '幂等键不能为空')
    }
    const requestFingerprint = JSON.stringify([
      normalizeReference(request.event_reference),
      request.publication_id,
      request.revision,
    ])
    const repeated = stored.rebindIdempotency.get(request.idempotency_key)
    if (repeated) {
      if (repeated.request_fingerprint !== requestFingerprint) {
        throw new P1SemanticPlanError(
          'idempotency_conflict',
          '同一幂等键不能切换到不同事件、publication 或 revision',
        )
      }
      if (
        stored.descriptor.binding_generation
        !== repeated.resulting_generation
      ) {
        throw new P1SemanticPlanError(
          'stale_idempotency_generation',
          '该重绑定幂等结果属于旧 binding generation，不能作为当前状态返回',
        )
      }
      const {
        request_fingerprint: _ignoredFingerprint,
        resulting_generation: _ignoredGeneration,
        ...result
      } = repeated
      return clone(result)
    }
    if (stored.active.size > 0) {
      throw new CountryOutageHttpError(
        409,
        'conversation_busy',
        '当前轮次结束前不能切换事件',
      )
    }
    const beforeBinding = clone(stored.descriptor.binding)
    const beforeState = clone(stored.descriptor.dialog_state)
    let bundle: P1FactBundle
    try {
      bundle = await this.loadAuthorizedBundle(principal, request, signal)
    } catch (error) {
      stored.descriptor.binding = beforeBinding
      stored.descriptor.dialog_state = beforeState
      throw error
    }
    const now = this.now().toISOString()
    const generation = stored.descriptor.binding_generation + 1
    stored.bundle = bundle
    stored.descriptor.binding = clone(bundle.binding)
    stored.descriptor.binding_generation = generation
    stored.descriptor.active_binding_generation = generation
    stored.descriptor.evidence_state = {
      immutable: true,
      incident_id: bundle.binding.incident_id,
      publication_id: bundle.binding.publication_id,
      revision: bundle.binding.revision,
      collector_id: 'rrc25',
      loaded_at: now,
    }
    stored.descriptor.dialog_state = initialState()
    stored.descriptor.binding_history.push({
      generation,
      incident_id: bundle.binding.incident_id,
      publication_id: bundle.binding.publication_id,
      revision: bundle.binding.revision,
      switched_at: now,
    })
    const result = {
      conversation: clone(stored.descriptor),
      previous_binding: beforeBinding,
    }
    stored.rebindIdempotency.set(request.idempotency_key, {
      request_fingerprint: requestFingerprint,
      resulting_generation: generation,
      ...clone(result),
    })
    return result
  }
}
