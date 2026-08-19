import type {
  AgentSession,
  CreateAgentSessionOptions,
  SessionStats,
} from '@earendil-works/pi-coding-agent'

export const DOMEYE_MAXIMUM_PROVIDER_ATTEMPTS_PER_TURN = 10 as const

type PiStreamFunction = AgentSession['agent']['streamFunction']

export type DomeyeProviderAttemptPhase = 'cognition' | 'renderer'

export interface DomeyeProviderAttemptRecord {
  attempt_id: number
  phase: DomeyeProviderAttemptPhase
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

export interface DomeyeProviderRuntimeIdentity {
  readonly provider: string
  readonly model: string
  readonly model_version: string
  readonly expected_response_model: string
}

export interface DomeyeProviderUsageAudit {
  attempt_count: number
  maximum_attempt_count: typeof DOMEYE_MAXIMUM_PROVIDER_ATTEMPTS_PER_TURN
  cost_policy: 'audit_only'
  tokens: {
    input: number
    output: number
    cache_read: number
    cache_write: number
    total: number
  }
  estimated_cost_usd: number
  attempts: readonly DomeyeProviderAttemptRecord[]
}

export interface DomeyePiSessionHandle {
  readonly agent: {
    streamFunction: PiStreamFunction
  }
  readonly messages: readonly unknown[]
  prompt(
    text: string,
    options?: {
      expandPromptTemplates?: boolean
      source?: 'rpc'
    },
  ): Promise<void>
  abort(): Promise<void>
  getSessionStats(): SessionStats
  getLastAssistantText?(): string | undefined
  dispose(): void
}

export type DomeyePiSessionFactory = (
  options: CreateAgentSessionOptions,
) => Promise<{ session: DomeyePiSessionHandle }>

interface MutableAttemptRecord extends DomeyeProviderAttemptRecord {
  started_at_ms: number
}

function safeFailureCode(error: unknown): string {
  if (
    error instanceof Error
    && /^[a-z][a-z0-9_]{0,63}$/.test(error.message)
  ) {
    return error.message
  }
  return 'provider_call_failed'
}

function responseMessage(value: unknown): Record<string, unknown> | undefined {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return undefined
  }
  const record = value as Record<string, unknown>
  const response = record.type === 'error'
    ? record.error
    : record.type === 'done'
      ? record.message
      : value
  if (!response || typeof response !== 'object' || Array.isArray(response)) {
    return undefined
  }
  return response as Record<string, unknown>
}

function providerResponseFailure(value: unknown): Error | undefined {
  const record = value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : undefined
  const message = responseMessage(value)
  if (!message) {
    return record?.type === 'error'
      ? new Error('provider_call_failed')
      : undefined
  }
  if (record?.type !== 'error' && message.stopReason !== 'error') {
    return undefined
  }
  return new Error(
    typeof message.errorMessage === 'string'
      ? safeFailureCode(new Error(message.errorMessage))
      : 'provider_call_failed',
  )
}

function providerResponseIdentity(
  value: unknown,
  expected: DomeyeProviderRuntimeIdentity,
):
  | { response_model: string }
  | { error: Error, response_model: string | null }
  | undefined {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return undefined
  }
  const record = value as Record<string, unknown>
  const isTerminalEvent = record.type === 'done'
  const isTerminalResult = record.stopReason !== undefined
  if (!isTerminalEvent && !isTerminalResult) return undefined
  const message = responseMessage(value)
  if (!message || message.stopReason === 'error') return undefined
  if (
    message.provider !== expected.provider
    || message.model !== expected.model
    || message.responseModel !== expected.expected_response_model
  ) {
    return {
      error: new Error('provider_response_identity_mismatch'),
      response_model: typeof message.responseModel === 'string'
        ? message.responseModel
        : null,
    }
  }
  return { response_model: expected.expected_response_model }
}

export class DomeyeProviderAttemptBudget {
  readonly #maximum: typeof DOMEYE_MAXIMUM_PROVIDER_ATTEMPTS_PER_TURN
  readonly #now: () => Date
  readonly #records: MutableAttemptRecord[] = []

  constructor(
    maximum: typeof DOMEYE_MAXIMUM_PROVIDER_ATTEMPTS_PER_TURN =
      DOMEYE_MAXIMUM_PROVIDER_ATTEMPTS_PER_TURN,
    now: () => Date = () => new Date(),
  ) {
    if (maximum !== DOMEYE_MAXIMUM_PROVIDER_ATTEMPTS_PER_TURN) {
      throw new Error('provider_attempt_limit_must_be_ten')
    }
    this.#maximum = maximum
    this.#now = now
  }

  get used(): number {
    return this.#records.filter(
      (record) => record.outcome !== 'limit_rejected',
    ).length
  }

  get remaining(): number {
    return this.#maximum - this.used
  }

  begin(
    phase: DomeyeProviderAttemptPhase,
    identity: DomeyeProviderRuntimeIdentity,
  ): MutableAttemptRecord | undefined {
    const started = this.#now()
    const startedAtMs = started.valueOf()
    if (this.remaining <= 0) {
      this.#records.push({
        attempt_id: this.#records.length + 1,
        phase,
        provider: identity.provider,
        model: identity.model,
        model_version: identity.model_version,
        expected_response_model: identity.expected_response_model,
        response_model: null,
        started_at_utc: started.toISOString(),
        ended_at_utc: started.toISOString(),
        latency_ms: 0,
        outcome: 'limit_rejected',
        failure_code: 'provider_request_limit_exceeded',
        started_at_ms: startedAtMs,
      })
      return undefined
    }
    const record: MutableAttemptRecord = {
      attempt_id: this.used + 1,
      phase,
      provider: identity.provider,
      model: identity.model,
      model_version: identity.model_version,
      expected_response_model: identity.expected_response_model,
      response_model: null,
      started_at_utc: started.toISOString(),
      ended_at_utc: null,
      latency_ms: null,
      outcome: 'started',
      failure_code: null,
      started_at_ms: startedAtMs,
    }
    this.#records.push(record)
    return record
  }

  complete(record: MutableAttemptRecord, responseModel: string): void {
    if (record.outcome !== 'started') return
    const ended = this.#now()
    record.ended_at_utc = ended.toISOString()
    record.latency_ms = Math.max(0, ended.valueOf() - record.started_at_ms)
    record.outcome = 'completed'
    record.response_model = responseModel
  }

  fail(
    record: MutableAttemptRecord,
    error: unknown,
    responseModel?: string | null,
  ): void {
    if (record.outcome !== 'started') return
    const ended = this.#now()
    record.ended_at_utc = ended.toISOString()
    record.latency_ms = Math.max(0, ended.valueOf() - record.started_at_ms)
    record.outcome = 'failed'
    record.failure_code = safeFailureCode(error)
    if (responseModel !== undefined) record.response_model = responseModel
  }

  failStarted(
    error: unknown,
    phase?: DomeyeProviderAttemptPhase,
  ): void {
    for (const record of this.#records) {
      if (
        record.outcome === 'started'
        && (phase === undefined || record.phase === phase)
      ) this.fail(record, error)
    }
  }

  snapshot(): readonly DomeyeProviderAttemptRecord[] {
    return this.#records.map(({ started_at_ms: _ignored, ...record }) => ({
      ...record,
    }))
  }
}

export class DomeyeTurnProviderAccounting {
  readonly budget: DomeyeProviderAttemptBudget
  readonly #stats: SessionStats[] = []

  constructor(budget = new DomeyeProviderAttemptBudget()) {
    this.budget = budget
  }

  recordSessionStats(stats: SessionStats | undefined): void {
    if (stats) this.#stats.push(structuredClone(stats))
  }

  audit(): DomeyeProviderUsageAudit {
    return providerUsageAudit(this.budget, this.#stats)
  }
}

function providerLimitErrorStream(
  model: Parameters<PiStreamFunction>[0],
): Awaited<ReturnType<PiStreamFunction>> {
  const message = {
    role: 'assistant' as const,
    content: [],
    api: model.api,
    provider: model.provider,
    model: model.id,
    responseModel: model.id,
    usage: {
      input: 0,
      output: 0,
      cacheRead: 0,
      cacheWrite: 0,
      totalTokens: 0,
      cost: {
        input: 0,
        output: 0,
        cacheRead: 0,
        cacheWrite: 0,
        total: 0,
      },
    },
    stopReason: 'error' as const,
    errorMessage: 'provider_request_limit_exceeded',
    timestamp: Date.now(),
  }
  return {
    async *[Symbol.asyncIterator]() {
      yield {
        type: 'error' as const,
        reason: 'error' as const,
        error: message,
      }
    },
    async result() {
      return message
    },
  } as unknown as Awaited<ReturnType<PiStreamFunction>>
}

function providerBoundaryErrorEvent(
  model: Parameters<PiStreamFunction>[0],
  failureCode: string,
) {
  return {
    type: 'error' as const,
    reason: 'error' as const,
    error: {
      role: 'assistant' as const,
      content: [],
      api: model.api,
      provider: model.provider,
      model: model.id,
      usage: {
        input: 0,
        output: 0,
        cacheRead: 0,
        cacheWrite: 0,
        totalTokens: 0,
        cost: {
          input: 0,
          output: 0,
          cacheRead: 0,
          cacheWrite: 0,
          total: 0,
        },
      },
      stopReason: 'error' as const,
      errorMessage: failureCode,
      timestamp: Date.now(),
    },
  }
}

export function installDomeyeProviderAttemptBoundary(
  session: DomeyePiSessionHandle,
  budget: DomeyeProviderAttemptBudget,
  phase: DomeyeProviderAttemptPhase,
  identity: DomeyeProviderRuntimeIdentity,
): void {
  if (
    !identity.provider.trim()
    || !identity.model.trim()
    || !identity.model_version.trim()
    || !identity.expected_response_model.trim()
  ) throw new Error('provider_response_binding_invalid')
  const original = session.agent.streamFunction
  session.agent.streamFunction = async (model, context, options) => {
    const record = budget.begin(phase, identity)
    if (!record) return providerLimitErrorStream(model)
    if (
      model.provider !== identity.provider
      || model.id !== identity.model
    ) {
      const error = new Error('provider_request_model_mismatch')
      budget.fail(record, error)
      throw error
    }
    let stream: Awaited<ReturnType<PiStreamFunction>>
    try {
      stream = await original(model, context, options)
    } catch (error) {
      budget.fail(record, error)
      throw error
    }
    let latchedFailure: Error | undefined
    let observedResponseModel: string | null | undefined
    const finish = (error?: unknown, responseModel?: string): void => {
      if (error === undefined && responseModel) {
        budget.complete(record, responseModel)
      }
      else budget.fail(record, error, observedResponseModel)
    }
    return {
      async *[Symbol.asyncIterator]() {
        let responseModel: string | undefined
        let terminalEvent: unknown
        try {
          for await (const event of stream) {
            if (latchedFailure) continue
            if (terminalEvent !== undefined) {
              const lateFailure = providerResponseFailure(event)
              latchedFailure = lateFailure
                ?? new Error('provider_stream_terminal_conflict')
              const message = responseMessage(event)
              observedResponseModel = typeof message?.responseModel === 'string'
                ? message.responseModel
                : observedResponseModel
              yield lateFailure
                ? event
                : providerBoundaryErrorEvent(
                    model,
                    'provider_stream_terminal_conflict',
                  )
              continue
            }
            const providerFailure = providerResponseFailure(event)
            if (providerFailure) {
              latchedFailure = providerFailure
              const message = responseMessage(event)
              observedResponseModel = typeof message?.responseModel === 'string'
                ? message.responseModel
                : null
              yield event
              continue
            }
            const responseIdentity = providerResponseIdentity(event, identity)
            if (responseIdentity && 'error' in responseIdentity) {
              latchedFailure = responseIdentity.error
              observedResponseModel = responseIdentity.response_model
              yield providerBoundaryErrorEvent(
                model,
                responseIdentity.error.message,
              )
              continue
            }
            if (responseIdentity && 'response_model' in responseIdentity) {
              responseModel = responseIdentity.response_model
              terminalEvent = event
              continue
            }
            yield event
          }
          if (!latchedFailure && !responseModel) {
            latchedFailure = new Error('provider_response_identity_missing')
            observedResponseModel = null
          }
          finish(latchedFailure, responseModel)
          if (latchedFailure?.message === 'provider_response_identity_missing') {
            throw latchedFailure
          }
          if (!latchedFailure && terminalEvent !== undefined) yield terminalEvent
        } catch (error) {
          latchedFailure ??= error instanceof Error
            ? error
            : new Error('provider_call_failed')
          finish(error)
          throw error
        }
      },
      async result() {
        try {
          if (latchedFailure) throw latchedFailure
          const result = await stream.result()
          const failure = providerResponseFailure(result)
          if (failure) {
            latchedFailure = failure
            const message = responseMessage(result)
            observedResponseModel = typeof message?.responseModel === 'string'
              ? message.responseModel
              : null
            finish(failure)
            return result
          }
          const responseIdentity = providerResponseIdentity(result, identity)
          if (responseIdentity && 'error' in responseIdentity) {
            latchedFailure = responseIdentity.error
            observedResponseModel = responseIdentity.response_model
            finish(responseIdentity.error)
            throw responseIdentity.error
          }
          if (!failure && !responseIdentity) {
            const error = new Error('provider_response_identity_missing')
            latchedFailure = error
            observedResponseModel = null
            finish(error)
            throw error
          }
          finish(
            failure,
            responseIdentity && 'response_model' in responseIdentity
              ? responseIdentity.response_model
              : undefined,
          )
          return result
        } catch (error) {
          finish(error)
          throw error
        }
      },
    } as unknown as Awaited<ReturnType<PiStreamFunction>>
  }
}

export function providerUsageAudit(
  budget: DomeyeProviderAttemptBudget,
  stats: readonly (SessionStats | undefined)[],
): DomeyeProviderUsageAudit {
  return {
    attempt_count: budget.used,
    maximum_attempt_count: DOMEYE_MAXIMUM_PROVIDER_ATTEMPTS_PER_TURN,
    cost_policy: 'audit_only',
    tokens: stats.reduce(
      (total, item) => ({
        input: total.input + (item?.tokens.input ?? 0),
        output: total.output + (item?.tokens.output ?? 0),
        cache_read: total.cache_read + (item?.tokens.cacheRead ?? 0),
        cache_write: total.cache_write + (item?.tokens.cacheWrite ?? 0),
        total: total.total + (item?.tokens.total ?? 0),
      }),
      { input: 0, output: 0, cache_read: 0, cache_write: 0, total: 0 },
    ),
    estimated_cost_usd: stats.reduce(
      (total, item) => total + (item?.cost ?? 0),
      0,
    ),
    attempts: budget.snapshot(),
  }
}
