import { randomUUID } from 'node:crypto'

import type { CountryOutagePrincipal } from '../server/contracts.js'
import { CountryOutageHttpError } from '../server/errors.js'
import type {
  CreateP1ConversationRequest,
  CreateP1TurnRequest,
  P1AnswerEnvelope,
  P1ChatApplication,
  P1ChatEvent,
  P1ChatSubscription,
  P1ConversationDescriptor,
  P1ConversationState,
  P1TurnDescriptor,
} from './contracts.js'
import { P1_CHAT_SCHEMA_VERSION } from './contracts.js'
import { P1DeterministicQuestionEngine } from './deterministic-engine.js'
import type {
  P1FactBundle,
  P1GeneralReadModelProvider,
} from './general-read-model-provider.js'
import { P1ReadModelError } from './general-read-model-provider.js'

interface StoredConversation {
  owner: string
  descriptor: P1ConversationDescriptor
  bundle: P1FactBundle
  idempotency: Map<string, P1TurnDescriptor>
  events: P1ChatEvent[]
  listeners: Set<(event: P1ChatEvent) => void>
  active: Map<string, AbortController>
}
export interface P1ConversationManagerOptions {
  provider: P1GeneralReadModelProvider
  ttlMs?: number
  reminderBeforeMs?: number
  now?: () => Date
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

function clone<T>(value: T): T {
  return structuredClone(value)
}

export class P1ConversationManager implements P1ChatApplication {
  private readonly conversations = new Map<string, StoredConversation>()
  private readonly creationIdempotency = new Map<string, string>()
  private readonly provider: P1GeneralReadModelProvider
  private readonly engine: P1DeterministicQuestionEngine
  private readonly ttlMs: number
  private readonly reminderBeforeMs: number
  private readonly now: () => Date

  constructor(options: P1ConversationManagerOptions) {
    this.provider = options.provider
    this.engine = new P1DeterministicQuestionEngine(options.provider)
    this.ttlMs = options.ttlMs ?? 30 * 60 * 1000
    this.reminderBeforeMs = options.reminderBeforeMs ?? 5 * 60 * 1000
    this.now = options.now ?? (() => new Date())
  }

  private owner(principal: CountryOutagePrincipal): string {
    return `${principal.userId}\u0000${principal.authorizationScope}`
  }

  private require(
    principal: CountryOutagePrincipal,
    conversationId: string,
  ): StoredConversation {
    const stored = this.conversations.get(conversationId)
    if (!stored || stored.owner !== this.owner(principal)) {
      throw new CountryOutageHttpError(404, 'conversation_not_found', '会话不存在或无权访问')
    }
    if (new Date(stored.descriptor.expires_at).getTime() <= this.now().getTime()) {
      throw new CountryOutageHttpError(410, 'conversation_expired', '会话已到期，请从当前事件新建会话')
    }
    return stored
  }

  private emit(stored: StoredConversation, event: Omit<P1ChatEvent, 'schema_version' | 'event_id' | 'emitted_at'>): void {
    const value: P1ChatEvent = {
      schema_version: P1_CHAT_SCHEMA_VERSION,
      event_id: stored.events.length + 1,
      ...event,
      emitted_at: this.now().toISOString(),
    }
    stored.events.push(value)
    for (const listener of stored.listeners) listener(value)
  }

  async createConversation(
    principal: CountryOutagePrincipal,
    request: CreateP1ConversationRequest,
  ): Promise<{ conversation: P1ConversationDescriptor, deduplicated: boolean }> {
    const owner = this.owner(principal)
    const key = `${owner}\u0000${request.idempotency_key}`
    const existingId = this.creationIdempotency.get(key)
    if (existingId) {
      const existing = this.require(principal, existingId)
      return { conversation: clone(existing.descriptor), deduplicated: true }
    }
    let bundle: P1FactBundle
    try {
      bundle = await this.provider.load(
        request.event_reference,
        request.publication_id,
        request.revision,
      )
    } catch (error) {
      this.throwReadError(error)
    }
    const now = this.now()
    const conversationId = `conv_${randomUUID().replaceAll('-', '')}`
    const descriptor: P1ConversationDescriptor = {
      schema_version: P1_CHAT_SCHEMA_VERSION,
      conversation_id: conversationId,
      binding: bundle!.binding,
      state: initialState(),
      turns: [],
      expires_at: new Date(now.getTime() + this.ttlMs).toISOString(),
      reminder_at: new Date(now.getTime() + this.ttlMs - this.reminderBeforeMs).toISOString(),
      created_at: now.toISOString(),
    }
    const stored: StoredConversation = {
      owner,
      descriptor,
      bundle: bundle!,
      idempotency: new Map(),
      events: [],
      listeners: new Set(),
      active: new Map(),
    }
    this.conversations.set(conversationId, stored)
    this.creationIdempotency.set(key, conversationId)
    this.emit(stored, { event_type: 'conversation_ready', conversation_id: conversationId })
    return { conversation: clone(descriptor), deduplicated: false }
  }

  async getConversation(
    principal: CountryOutagePrincipal,
    conversationId: string,
  ): Promise<P1ConversationDescriptor> {
    return clone(this.require(principal, conversationId).descriptor)
  }

  async createTurn(
    principal: CountryOutagePrincipal,
    conversationId: string,
    request: CreateP1TurnRequest,
  ): Promise<{ turn: P1TurnDescriptor, deduplicated: boolean }> {
    const stored = this.require(principal, conversationId)
    const existing = stored.idempotency.get(request.idempotency_key)
    if (existing) return { turn: clone(existing), deduplicated: true }
    if (stored.active.size > 0) {
      throw new CountryOutageHttpError(409, 'conversation_busy', '当前会话已有一轮正在处理', true)
    }
    const current = await this.provider.resolve(stored.descriptor.binding.legacy_reference)
    if (
      current.publication_id !== stored.descriptor.binding.publication_id ||
      current.revision !== stored.descriptor.binding.revision
    ) {
      throw new CountryOutageHttpError(
        409,
        'revision_drift',
        '活动 publication/revision 已变化，请确认后重新绑定；历史回答仍保留原身份',
      )
    }
    const number = stored.descriptor.turns.length + 1
    const turnId = `turn_${randomUUID().replaceAll('-', '')}`
    const turn: P1TurnDescriptor = {
      turn_id: turnId,
      turn_number: number,
      question: request.question,
      state: 'queued',
      created_at: this.now().toISOString(),
    }
    stored.descriptor.turns.push(turn)
    stored.idempotency.set(request.idempotency_key, turn)
    const controller = new AbortController()
    stored.active.set(turnId, controller)
    this.emit(stored, { event_type: 'turn_state', conversation_id: conversationId, turn_id: turnId, state: 'queued' })
    try {
      turn.state = 'understanding'
      this.emit(stored, { event_type: 'turn_state', conversation_id: conversationId, turn_id: turnId, state: turn.state })
      const answer = await this.engine.answer({
        conversationId,
        turnId,
        turnNumber: number,
        question: request.question,
        state: clone(stored.descriptor.state),
        bundle: stored.bundle,
        signal: controller.signal,
      })
      if (controller.signal.aborted) throw new Error('cancelled')
      turn.state = answer.validation.passed ? 'completed' : 'failed'
      turn.answer = answer
      turn.completed_at = this.now().toISOString()
      if (answer.validation.passed) this.commitState(stored, answer)
      this.emit(stored, { event_type: 'turn_answer', conversation_id: conversationId, turn_id: turnId, state: turn.state, answer })
    } catch (error) {
      if (controller.signal.aborted || (error instanceof Error && error.message === 'cancelled')) {
        turn.state = 'cancelled'
        turn.completed_at = this.now().toISOString()
        this.emit(stored, { event_type: 'turn_state', conversation_id: conversationId, turn_id: turnId, state: 'cancelled' })
      } else {
        turn.state = 'failed'
        turn.completed_at = this.now().toISOString()
        turn.error = {
          code: error instanceof P1ReadModelError ? error.code : 'turn_failed',
          message: error instanceof Error ? error.message : '轮次处理失败',
          retryable: error instanceof P1ReadModelError ? error.retryable : false,
        }
        this.emit(stored, { event_type: 'turn_state', conversation_id: conversationId, turn_id: turnId, state: 'failed' })
      }
    } finally {
      stored.active.delete(turnId)
    }
    return { turn: clone(turn), deduplicated: false }
  }

  private commitState(stored: StoredConversation, answer: P1AnswerEnvelope): void {
    const state = stored.descriptor.state as unknown as Record<string, unknown>
    for (const key of answer.transition.clear) state[key] = null
    for (const [key, value] of Object.entries(answer.transition.set)) state[key] = value
    stored.descriptor.state.last_committed_turn_number = answer.turn_number
  }

  async cancelTurn(
    principal: CountryOutagePrincipal,
    conversationId: string,
    turnId: string,
  ): Promise<{ turn_id: string, state: P1TurnDescriptor['state'] }> {
    const stored = this.require(principal, conversationId)
    const turn = stored.descriptor.turns.find((value) => value.turn_id === turnId)
    if (!turn) throw new CountryOutageHttpError(404, 'turn_not_found', '轮次不存在')
    stored.active.get(turnId)?.abort()
    if (!['completed', 'failed', 'cancelled'].includes(turn.state)) turn.state = 'cancelled'
    return { turn_id: turnId, state: turn.state }
  }

  async subscribe(
    principal: CountryOutagePrincipal,
    conversationId: string,
    afterEventId: number,
    listener: (event: P1ChatEvent) => void,
  ): Promise<P1ChatSubscription> {
    const stored = this.require(principal, conversationId)
    let active = false
    return {
      replay: stored.events.filter((event) => event.event_id > afterEventId).map(clone),
      activate: () => {
        if (!active) {
          active = true
          stored.listeners.add(listener)
        }
      },
      close: () => {
        active = false
        stored.listeners.delete(listener)
      },
    }
  }

  async rebind(
    principal: CountryOutagePrincipal,
    conversationId: string,
    request: CreateP1ConversationRequest,
  ): Promise<{ conversation: P1ConversationDescriptor, previous_conversation_id: string }> {
    this.require(principal, conversationId)
    const result = await this.createConversation(principal, request)
    return { conversation: result.conversation, previous_conversation_id: conversationId }
  }

  private throwReadError(error: unknown): never {
    if (error instanceof P1ReadModelError) {
      throw new CountryOutageHttpError(
        error.code === 'evidence_not_found' ? 404 : error.retryable ? 503 : 409,
        error.code,
        error.message,
        error.retryable,
      )
    }
    throw error
  }
}
