import { randomUUID } from 'node:crypto'

import type { CountryOutagePrincipal } from '../server/contracts.js'
import type {
  P1ConversationBinding,
  P1ConversationState,
  P1StateTransition,
} from './contracts.js'
import type { P1PageCapabilityReadProvider } from './general-read-model-provider.js'
import {
  P1SemanticPlanError,
  P1RuntimeV2SemanticTurnService,
  type P1RuntimeV2SemanticAnswer,
  type P1SemanticGoalResult,
  type P1UserGoal,
  type P1UserGoalPlanner,
} from './runtime-v2-semantic.js'
import {
  authorizeP1RuntimeV2Country,
  readP1RuntimeV2PermissionCandidate,
  throwIfP1RuntimeV2Cancelled,
} from './runtime-v2-single-turn.js'

export const P1_RUNTIME_V2_CONVERSATION_SCHEMA =
  'country_outage_p1_runtime_v2_conversation_v2' as const
export const P1_RUNTIME_V2_CONVERSATION_TURN_SCHEMA =
  'country_outage_p1_runtime_v2_conversation_turn_v2' as const

type TurnState =
  | 'understanding'
  | 'executing'
  | 'validating'
  | 'completed'
  | 'failed'
  | 'cancelled'

export interface P1RuntimeV2EvidenceState {
  immutable: true
  incident_id: string
  publication_id: string
  revision: number
  collector_id: 'rrc25'
  cohort_id: string
  data_through: string | null
  capabilities: P1ConversationBinding['capabilities']
  loaded_at: string
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
    ttl_validated: boolean
    cancelled: boolean
  }
}

export type P1RuntimeV2ConversationTurnAnswer = Omit<
  P1RuntimeV2SemanticAnswer,
  'schema_version' | 'execution_trace'
> & {
  schema_version: typeof P1_RUNTIME_V2_CONVERSATION_TURN_SCHEMA
  conversation_id: string
  turn_id: string
  turn_number: number
  binding_generation: number
  execution_trace: Omit<
    P1RuntimeV2SemanticAnswer['execution_trace'],
    'state_commit'
  > & {
    state_commit: 'committed' | 'none'
  }
  state_receipt: P1RuntimeV2StateReceipt
}

export interface P1RuntimeV2ConversationTurn {
  turn_id: string
  turn_number: number
  binding_generation: number
  question: string
  state: TurnState
  answer?: P1RuntimeV2ConversationTurnAnswer
  error?: {
    code: string
    message: string
    retryable: boolean
  }
  failure_receipt?: P1RuntimeV2StateReceipt
  created_at: string
  completed_at?: string
}

export interface P1RuntimeV2ConversationDescriptor {
  schema_version: typeof P1_RUNTIME_V2_CONVERSATION_SCHEMA
  conversation_id: string
  binding: P1ConversationBinding
  binding_generation: number
  active_binding_generation: number | null
  evidence_state: P1RuntimeV2EvidenceState
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

export interface P1RuntimeV2ConversationServiceOptions {
  provider: P1PageCapabilityReadProvider
  planner: P1UserGoalPlanner
  ttlMs?: number
  turnTimeoutMs?: number
  now?: () => Date
}

interface StoredConversation {
  owner: string
  descriptor: P1RuntimeV2ConversationDescriptor
  idempotency: Map<string, {
    question: string
    turn: P1RuntimeV2ConversationTurn
  }>
  rebindIdempotency: Map<string, {
    fingerprint: string
    generation: number
    descriptor: P1RuntimeV2ConversationDescriptor
    previous_binding: P1ConversationBinding
  }>
  active: Map<string, AbortController>
}

function clone<T>(value: T): T {
  return structuredClone(value)
}

function owner(principal: CountryOutagePrincipal): string {
  return `${principal.userId}\u0000${principal.authorizationScope}`
}

function initialState(): P1ConversationState {
  return {
    topic: null,
    asn: null,
    address_family: null,
    metric: null,
    population: null,
    include_new_prefixes: null,
    analysis_mode: null,
    time_scope: null,
    evidence_anchor: null,
    pending_clarification: null,
    last_committed_turn_number: 0,
  }
}

function emptyTransition(): P1StateTransition {
  return { inherit: [], set: {}, clear: [], reason_codes: [] }
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
    throw new P1SemanticPlanError(
      'binding_conflict',
      '请求事件、publication 或 revision 与解析结果不一致',
    )
  }
  if (
    binding.event_type !== 'country_outage'
    || binding.collector_id !== 'rrc25'
  ) {
    throw new P1SemanticPlanError(
      'unsupported_event',
      'P1 只接受 RRC25 country_outage 事件',
    )
  }
}

function bindingEquals(
  left: P1ConversationBinding,
  right: P1ConversationBinding,
): boolean {
  const fields: Array<keyof P1ConversationBinding> = [
    'event_type', 'incident_id', 'legacy_reference', 'publication_id',
    'revision', 'collector_id', 'cohort_id', 'country_code',
    'detected_at_utc', 'window_start_utc', 'window_end_utc', 'data_through',
    'is_final_in_data_range', 'lifecycle_state', 'observation_state',
    'quality_state', 'missing_slot_count',
  ]
  return fields.every((field) => (
    field === 'legacy_reference'
      ? normalizeReference(String(left[field]))
        === normalizeReference(String(right[field]))
      : left[field] === right[field]
  )) && JSON.stringify(left.capabilities) === JSON.stringify(right.capabilities)
}

function evidenceState(
  binding: P1ConversationBinding,
  loadedAt: string,
): P1RuntimeV2EvidenceState {
  return {
    immutable: true,
    incident_id: binding.incident_id,
    publication_id: binding.publication_id,
    revision: binding.revision,
    collector_id: 'rrc25',
    cohort_id: binding.cohort_id,
    data_through: binding.data_through,
    capabilities: clone(binding.capabilities),
    loaded_at: loadedAt,
  }
}

function scalarString(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value.trim() : null
}

function scalarAsn(value: unknown): number | null {
  if (typeof value === 'number' && Number.isSafeInteger(value) && value > 0) {
    return value
  }
  if (typeof value === 'string' && /^[1-9][0-9]*$/.test(value)) {
    const parsed = Number(value)
    return Number.isSafeInteger(parsed) ? parsed : null
  }
  return null
}

function goalHasExecutedEvidence(
  goal: P1UserGoal,
  result: P1SemanticGoalResult,
  answer: P1RuntimeV2SemanticAnswer,
): boolean {
  const decision = answer.semantic_plan.grounding_plan.decisions.find(
    (item) => item.goal_id === goal.goal_id,
  )
  return Boolean(
    decision
    && decision.node_ids.length > 0
    && result.evidence_refs.length > 0,
  )
}

function applyTransition(
  before: P1ConversationState,
  answer: P1RuntimeV2SemanticAnswer,
  turnNumber: number,
): {
  transition: P1StateTransition
  after: P1ConversationState
  suspendBinding: boolean
} {
  if (answer.execution_trace.planner_outcome === 'safe_fallback') {
    return {
      transition: emptyTransition(),
      after: clone(before),
      suspendBinding: false,
    }
  }
  const transition: P1StateTransition = {
    inherit: ['event_binding'],
    set: {},
    clear: [],
    reason_codes: [],
  }
  const clear = new Set<string>()
  let suspendBinding = false
  let hasExecutableSuccess = false
  let hasBlockingClarification = false
  const goalById = new Map(
    answer.semantic_plan.user_goal_plan.goals.map((goal) => [goal.goal_id, goal]),
  )
  const hasFixedAddressGoal = answer.semantic_plan.user_goal_plan.goals.some(
    (goal) =>
      (goal.normalized_kind === 'address_family_change'
        || goal.normalized_kind === 'address_family_compare')
      && goal.entities.population !== 'new_prefix_only',
  )

  for (const result of answer.results) {
    const goal = goalById.get(result.goal_id)
    if (!goal) continue
    if (goal.normalized_kind === 'event_switch') {
      if (result.answerability === 'supported') {
        suspendBinding = true
      } else {
        transition.set.pending_clarification = 'event_reference'
        transition.reason_codes.push('event_switch_reference_required')
      }
      continue
    }
    if (result.answerability === 'clarify') {
      hasBlockingClarification = true
      continue
    }
    if (
      (result.answerability !== 'supported'
        && result.answerability !== 'partial')
      || !goalHasExecutedEvidence(goal, result, answer)
    ) continue

    hasExecutableSuccess = true
    transition.set.topic = goal.normalized_kind
    transition.set.evidence_anchor = result.evidence_refs[0] ?? null
    for (const dependency of goal.context_dependencies) {
      transition.inherit.push(dependency)
    }

    const family = scalarString(goal.entities.address_family)
    if (family === 'ipv4' || family === 'ipv6' || family === 'both') {
      transition.set.address_family = family
    }
    const population = scalarString(goal.entities.population)
    if (population === 'fixed_cohort' || population === 'new_prefix_only') {
      transition.set.population = population
    }
    if (typeof goal.entities.include_new_prefixes === 'boolean') {
      transition.set.include_new_prefixes = goal.entities.include_new_prefixes
    }
    const analysisMode = scalarString(goal.entities.analysis_mode)
    if (analysisMode !== null) transition.set.analysis_mode = analysisMode
    const timeScope = scalarString(goal.entities.time_scope)
    if (timeScope !== null) transition.set.time_scope = timeScope

    const asn = scalarAsn(goal.entities.asn ?? goal.entities.affected_asn)
    if (asn !== null) {
      transition.set.asn = asn
      clear.add('address_family')
      clear.add('population')
      clear.add('include_new_prefixes')
      transition.reason_codes.push(
        before.asn === null ? 'verified_asn_set' : 'verified_asn_override',
      )
    }
    const metric = scalarString(goal.entities.metric)
    if (metric !== null) transition.set.metric = metric

    if (
      goal.normalized_kind === 'current_prefix_state'
      || goal.normalized_kind === 'metric_followup'
      || goal.normalized_kind === 'prefix_peak'
      || goal.normalized_kind === 'remaining_vs_peak'
    ) {
      transition.set.metric = 'interrupted_prefix_count'
    }
    if (
      goal.normalized_kind === 'address_family_change'
      || goal.normalized_kind === 'address_family_compare'
    ) {
      clear.add('asn')
      transition.set.population = population ?? 'fixed_cohort'
      if (typeof goal.entities.include_new_prefixes !== 'boolean') {
        transition.set.include_new_prefixes = false
      }
    }
    if (
      goal.normalized_kind === 'new_prefix_resources'
      || goal.normalized_kind === 'new_prefix_state'
    ) {
      clear.add('asn')
      transition.set.population = hasFixedAddressGoal
        ? 'fixed_cohort' : 'new_prefix_only'
      transition.set.include_new_prefixes = true
      if (family === null) transition.set.address_family = 'both'
    }
  }

  if (!suspendBinding) {
    if (hasBlockingClarification && !hasExecutableSuccess) {
      transition.set.pending_clarification = 'goal_clarification'
      transition.reason_codes.push('non_executable_clarification_only')
    } else if (
      hasExecutableSuccess
      || answer.semantic_plan.user_goal_plan.goals.some(
        (goal) => goal.ambiguity === 'none',
      )
    ) {
      clear.add('pending_clarification')
    }
  }

  transition.inherit = [...new Set(transition.inherit)]
  transition.clear = [...clear]
  const after = clone(before) as unknown as Record<string, unknown>
  for (const key of transition.clear) {
    if (key in after) after[key] = null
  }
  for (const [key, value] of Object.entries(transition.set)) {
    if (key in after) after[key] = value
  }
  if (JSON.stringify(after) !== JSON.stringify(before)) {
    after.last_committed_turn_number = turnNumber
  }
  return {
    transition,
    after: after as unknown as P1ConversationState,
    suspendBinding,
  }
}

function errorCode(error: unknown, cancelled: boolean, timedOut: boolean): string {
  if (timedOut) return 'tool_timeout'
  if (cancelled) return 'cancelled'
  if (error instanceof P1SemanticPlanError) return error.code
  if (
    error && typeof error === 'object'
    && 'code' in error && typeof error.code === 'string'
  ) return error.code
  return 'turn_failed'
}

function retryable(error: unknown, timedOut: boolean): boolean {
  if (timedOut) return true
  return Boolean(
    error && typeof error === 'object'
    && 'retryable' in error && error.retryable === true,
  )
}

export class P1RuntimeV2ConversationService {
  readonly #conversations = new Map<string, StoredConversation>()
  readonly #creationIdempotency = new Map<string, {
    conversation_id: string
    fingerprint: string
  }>()
  readonly #provider: P1PageCapabilityReadProvider
  readonly #semantic: P1RuntimeV2SemanticTurnService
  readonly #ttlMs: number
  readonly #turnTimeoutMs: number
  readonly #now: () => Date

  constructor(options: P1RuntimeV2ConversationServiceOptions) {
    this.#provider = options.provider
    this.#semantic = new P1RuntimeV2SemanticTurnService(
      options.provider,
      options.planner,
      undefined,
      options.now,
    )
    this.#ttlMs = options.ttlMs ?? 30 * 60 * 1000
    this.#turnTimeoutMs = options.turnTimeoutMs ?? 30_000
    this.#now = options.now ?? (() => new Date())
  }

  #assertNotExpired(stored: StoredConversation): void {
    if (Date.parse(stored.descriptor.expires_at) <= this.#now().getTime()) {
      throw new P1SemanticPlanError(
        'conversation_expired',
        '会话已到期，请从当前事件重新绑定；旧回答不会改写为新事实',
      )
    }
  }

  #require(
    principal: CountryOutagePrincipal,
    conversationId: string,
  ): StoredConversation {
    const stored = this.#conversations.get(conversationId)
    if (!stored || stored.owner !== owner(principal)) {
      throw new P1SemanticPlanError(
        'conversation_not_found',
        '会话不存在或无权访问',
      )
    }
    this.#assertNotExpired(stored)
    return stored
  }

  async #resolveAuthorized(
    principal: CountryOutagePrincipal,
    request: Pick<CreateP1RuntimeV2ConversationRequest,
      'event_reference' | 'publication_id' | 'revision'>,
    signal?: AbortSignal,
  ): Promise<P1ConversationBinding> {
    throwIfP1RuntimeV2Cancelled(signal)
    const binding = await this.#provider.resolve(request.event_reference, signal)
    throwIfP1RuntimeV2Cancelled(signal)
    assertRequestBinding(request, binding)
    authorizeP1RuntimeV2Country(
      readP1RuntimeV2PermissionCandidate(principal),
      binding.country_code,
    )
    return binding
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
    const fingerprint = JSON.stringify([
      normalizeReference(request.event_reference),
      request.publication_id,
      request.revision,
    ])
    const existing = this.#creationIdempotency.get(key)
    if (existing) {
      if (existing.fingerprint !== fingerprint) {
        throw new P1SemanticPlanError(
          'idempotency_conflict',
          '同一幂等键不能绑定不同事件、publication 或 revision',
        )
      }
      return {
        conversation: clone(this.#require(
          principal,
          existing.conversation_id,
        ).descriptor),
        deduplicated: true,
      }
    }
    const binding = await this.#resolveAuthorized(principal, request, signal)
    const now = this.#now()
    const capturedAt = now.toISOString()
    const conversationId = `p1v2_${randomUUID().replaceAll('-', '')}`
    const descriptor: P1RuntimeV2ConversationDescriptor = {
      schema_version: P1_RUNTIME_V2_CONVERSATION_SCHEMA,
      conversation_id: conversationId,
      binding: clone(binding),
      binding_generation: 1,
      active_binding_generation: 1,
      evidence_state: evidenceState(binding, capturedAt),
      dialog_state: initialState(),
      turns: [],
      binding_history: [{
        generation: 1,
        incident_id: binding.incident_id,
        publication_id: binding.publication_id,
        revision: binding.revision,
        switched_at: capturedAt,
      }],
      expires_at: new Date(now.getTime() + this.#ttlMs).toISOString(),
      created_at: capturedAt,
    }
    this.#conversations.set(conversationId, {
      owner: owner(principal),
      descriptor,
      idempotency: new Map(),
      rebindIdempotency: new Map(),
      active: new Map(),
    })
    this.#creationIdempotency.set(key, {
      conversation_id: conversationId,
      fingerprint,
    })
    return { conversation: clone(descriptor), deduplicated: false }
  }

  async getConversation(
    principal: CountryOutagePrincipal,
    conversationId: string,
  ): Promise<P1RuntimeV2ConversationDescriptor> {
    return clone(this.#require(principal, conversationId).descriptor)
  }

  async cancelTurn(
    principal: CountryOutagePrincipal,
    conversationId: string,
    turnId: string,
  ): Promise<{ turn_id: string; state: 'cancel_requested' | 'not_active' }> {
    const stored = this.#require(principal, conversationId)
    const turn = stored.descriptor.turns.find((item) => item.turn_id === turnId)
    if (!turn) {
      throw new P1SemanticPlanError('turn_not_found', '轮次不存在')
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
  ): Promise<{ turn: P1RuntimeV2ConversationTurn; deduplicated: boolean }> {
    if (!request.question.trim() || request.question.length > 2_000) {
      throw new P1SemanticPlanError(
        'invalid_question',
        'question 必须是 1 至 2,000 字符的非空文本',
      )
    }
    if (!request.idempotency_key.trim()) {
      throw new P1SemanticPlanError('invalid_idempotency_key', '幂等键不能为空')
    }
    const stored = this.#require(principal, conversationId)
    if (
      stored.descriptor.active_binding_generation
      !== stored.descriptor.binding_generation
    ) {
      throw new P1SemanticPlanError(
        'event_binding_suspended_until_rebind',
        '当前事件绑定已暂停；必须先验证并重新绑定目标事件',
      )
    }
    const namespace = [
      stored.descriptor.binding_generation,
      request.idempotency_key,
    ].join('\u0000')
    const existing = stored.idempotency.get(namespace)
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
      throw new P1SemanticPlanError(
        'conversation_busy',
        '当前会话已有一轮正在处理',
        true,
      )
    }

    const generation = stored.descriptor.binding_generation
    const turnNumber = stored.descriptor.turns.length + 1
    const turnId = `p1v2turn_${randomUUID().replaceAll('-', '')}`
    const turn: P1RuntimeV2ConversationTurn = {
      turn_id: turnId,
      turn_number: turnNumber,
      binding_generation: generation,
      question: request.question,
      state: 'understanding',
      created_at: this.#now().toISOString(),
    }
    stored.descriptor.turns.push(turn)
    stored.idempotency.set(namespace, { question: request.question, turn })

    const controller = new AbortController()
    let timedOut = false
    const timeout = setTimeout(() => {
      timedOut = true
      controller.abort()
    }, this.#turnTimeoutMs)
    const abort = (): void => controller.abort()
    signal?.addEventListener('abort', abort, { once: true })
    if (signal?.aborted) controller.abort()
    stored.active.set(turnId, controller)

    const before = clone(stored.descriptor.dialog_state)
    const beforeActiveGeneration = stored.descriptor.active_binding_generation
    const checks = {
      plan_validated: false,
      permission_validated: false,
      execution_validated: false,
      evidence_validated: false,
      binding_revalidated: false,
      ttl_validated: false,
      cancelled: false,
    }
    try {
      throwIfP1RuntimeV2Cancelled(controller.signal)
      const preflight = await this.#provider.resolve(
        stored.descriptor.binding.legacy_reference,
        controller.signal,
      )
      if (!bindingEquals(stored.descriptor.binding, preflight)) {
        throw new P1SemanticPlanError(
          'revision_drift',
          '执行前 publication、revision、能力或水位发生漂移',
        )
      }
      authorizeP1RuntimeV2Country(
        readP1RuntimeV2PermissionCandidate(principal),
        preflight.country_code,
      )
      checks.permission_validated = true

      turn.state = 'executing'
      const semantic = await this.#semantic.answer(
        principal,
        {
          event_reference: preflight.legacy_reference,
          publication_id: preflight.publication_id,
          revision: preflight.revision,
          question: request.question,
          dialog_state: before,
        },
        controller.signal,
      )
      checks.plan_validated = semantic.validation.user_goal_schema === 'passed'
        && semantic.validation.grounding_schema === 'passed'
        && semantic.validation.grounding_legality === 'passed'
      checks.execution_validated = semantic.execution_trace.nodes.every(
        (node) => node.status === 'passed' || node.status === 'reused_preflight',
      )
      if (!checks.execution_validated) {
        const failed = semantic.execution_trace.nodes.find(
          (node) => node.status === 'failed',
        )
        throw new P1SemanticPlanError(
          failed?.error_code ?? 'tool_execution_failed',
          `确定性 Tool/算子执行失败（${failed?.error_code ?? 'unknown'}）；整轮不发布并回滚状态`,
          true,
        )
      }
      const hasSwitch = semantic.semantic_plan.user_goal_plan.goals.some(
        (goal) => goal.normalized_kind === 'event_switch',
      )
      const hasExecutedFact = semantic.execution_trace.nodes.some(
        (node) => node.status === 'passed' && node.execution_unit !== 'TOOL-01',
      )
      if (hasSwitch && hasExecutedFact) {
        throw new P1SemanticPlanError(
          'event_switch_mixed_with_fact_forbidden',
          '事件切换不能与旧事件事实读取同轮执行',
        )
      }

      turn.state = 'validating'
      const answerBinding = hasSwitch ? semantic.binding : preflight
      const evidenceRefs = new Set(
        semantic.evidence.map((item) => item.evidence_ref),
      )
      if (
        semantic.evidence.some((item) =>
          item.incident_id !== answerBinding.incident_id
          || item.publication_id !== answerBinding.publication_id
          || item.revision !== answerBinding.revision
          || item.collector_id !== 'rrc25'
        )
        || semantic.results.some((result) =>
          result.evidence_refs.some((ref) => !evidenceRefs.has(ref))
        )
      ) {
        throw new P1SemanticPlanError(
          'answer_evidence_identity_conflict',
          '回答证据与当前 EvidenceState 不一致',
        )
      }
      checks.evidence_validated = true
      throwIfP1RuntimeV2Cancelled(controller.signal)
      const postflight = await this.#provider.resolve(
        answerBinding.legacy_reference,
        controller.signal,
      )
      if (!bindingEquals(answerBinding, postflight)) {
        throw new P1SemanticPlanError(
          'revision_drift',
          '执行期间 publication、revision、能力或水位漂移；整轮回滚',
        )
      }
      checks.binding_revalidated = true
      this.#assertNotExpired(stored)
      checks.ttl_validated = true
      throwIfP1RuntimeV2Cancelled(controller.signal)

      const atomicSwitch = hasSwitch && semantic.answerability === 'supported'
      const applied = atomicSwitch
        ? {
            transition: {
              inherit: [],
              set: {},
              clear: [
                'topic', 'asn', 'address_family', 'metric', 'population',
                'include_new_prefixes', 'analysis_mode', 'time_scope',
                'evidence_anchor', 'pending_clarification',
              ],
              reason_codes: ['event_switch_atomic_rebind'],
            } satisfies P1StateTransition,
            after: initialState(),
            suspendBinding: false,
          }
        : applyTransition(before, semantic, turnNumber)
      const { transition, after, suspendBinding } = applied
      const changed = atomicSwitch
        || JSON.stringify(before) !== JSON.stringify(after)
      const answerGeneration = atomicSwitch ? generation + 1 : generation
      const stateReceipt: P1RuntimeV2StateReceipt = {
        before,
        proposed: transition,
        after: clone(changed ? after : before),
        status: changed ? 'committed' : 'none',
        transaction_checks: { ...checks },
      }
      const { execution_trace: semanticTrace, ...semanticRest } = semantic
      const answer: P1RuntimeV2ConversationTurnAnswer = {
        ...semanticRest,
        schema_version: P1_RUNTIME_V2_CONVERSATION_TURN_SCHEMA,
        conversation_id: conversationId,
        turn_id: turnId,
        turn_number: turnNumber,
        binding_generation: answerGeneration,
        execution_trace: {
          ...semanticTrace,
          state_commit: changed ? 'committed' : 'none',
        },
        state_receipt: stateReceipt,
      }
      if (atomicSwitch) {
        const switchedAt = this.#now().toISOString()
        stored.descriptor.binding = clone(semantic.binding)
        stored.descriptor.binding_generation = answerGeneration
        stored.descriptor.active_binding_generation = answerGeneration
        stored.descriptor.evidence_state = evidenceState(
          semantic.binding,
          switchedAt,
        )
        stored.descriptor.dialog_state = clone(after)
        stored.descriptor.binding_history.push({
          generation: answerGeneration,
          incident_id: semantic.binding.incident_id,
          publication_id: semantic.binding.publication_id,
          revision: semantic.binding.revision,
          switched_at: switchedAt,
        })
        turn.binding_generation = answerGeneration
        stored.idempotency.set(
          [answerGeneration, request.idempotency_key].join('\u0000'),
          { question: request.question, turn },
        )
      } else if (changed) stored.descriptor.dialog_state = clone(after)
      if (!atomicSwitch && suspendBinding) {
        stored.descriptor.active_binding_generation = null
      }
      turn.state = 'completed'
      turn.answer = answer
      turn.completed_at = this.#now().toISOString()
    } catch (error) {
      const cancelled = controller.signal.aborted
      checks.cancelled = cancelled
      stored.descriptor.dialog_state = before
      stored.descriptor.active_binding_generation = beforeActiveGeneration
      turn.state = cancelled ? 'cancelled' : 'failed'
      turn.completed_at = this.#now().toISOString()
      turn.error = {
        code: errorCode(error, cancelled, timedOut),
        message: timedOut
          ? '本轮超时；回答、EvidenceState 和 DialogState 均未提交'
          : cancelled
            ? '本轮已取消；回答、EvidenceState 和 DialogState 均未提交'
            : error instanceof Error ? error.message : '轮次失败关闭',
        retryable: retryable(error, timedOut),
      }
      turn.failure_receipt = {
        before,
        proposed: emptyTransition(),
        after: clone(before),
        status: 'rolled_back',
        transaction_checks: { ...checks },
      }
    } finally {
      clearTimeout(timeout)
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
    const stored = this.#require(principal, conversationId)
    if (!request.idempotency_key.trim()) {
      throw new P1SemanticPlanError('invalid_idempotency_key', '幂等键不能为空')
    }
    if (stored.active.size > 0) {
      throw new P1SemanticPlanError(
        'conversation_busy',
        '当前轮次结束前不能切换事件',
      )
    }
    const fingerprint = JSON.stringify([
      normalizeReference(request.event_reference),
      request.publication_id,
      request.revision,
    ])
    const existing = stored.rebindIdempotency.get(request.idempotency_key)
    if (existing) {
      if (existing.fingerprint !== fingerprint) {
        throw new P1SemanticPlanError(
          'idempotency_conflict',
          '同一幂等键不能切换到不同事件身份',
        )
      }
      if (stored.descriptor.binding_generation !== existing.generation) {
        throw new P1SemanticPlanError(
          'stale_idempotency_generation',
          '该重绑定结果属于旧 generation',
        )
      }
      return {
        conversation: clone(stored.descriptor),
        previous_binding: clone(existing.previous_binding),
      }
    }

    const previousBinding = clone(stored.descriptor.binding)
    const previousState = clone(stored.descriptor.dialog_state)
    const previousEvidence = clone(stored.descriptor.evidence_state)
    const previousActive = stored.descriptor.active_binding_generation
    let binding: P1ConversationBinding
    try {
      binding = await this.#resolveAuthorized(principal, request, signal)
    } catch (error) {
      stored.descriptor.binding = previousBinding
      stored.descriptor.dialog_state = previousState
      stored.descriptor.evidence_state = previousEvidence
      stored.descriptor.active_binding_generation = previousActive
      throw error
    }
    const switchedAt = this.#now().toISOString()
    const generation = stored.descriptor.binding_generation + 1
    stored.descriptor.binding = clone(binding)
    stored.descriptor.binding_generation = generation
    stored.descriptor.active_binding_generation = generation
    stored.descriptor.evidence_state = evidenceState(binding, switchedAt)
    stored.descriptor.dialog_state = initialState()
    stored.descriptor.binding_history.push({
      generation,
      incident_id: binding.incident_id,
      publication_id: binding.publication_id,
      revision: binding.revision,
      switched_at: switchedAt,
    })
    const descriptor = clone(stored.descriptor)
    stored.rebindIdempotency.set(request.idempotency_key, {
      fingerprint,
      generation,
      descriptor,
      previous_binding: previousBinding,
    })
    return { conversation: descriptor, previous_binding: previousBinding }
  }
}
