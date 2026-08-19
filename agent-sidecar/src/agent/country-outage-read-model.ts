import { createHash } from 'node:crypto'

import type { DomeyeDataIdentity } from './contracts.js'
import type {
  CountryOutageMetricSeriesRead,
  CountryOutageMetricSeriesReadRequest,
  CountryOutageSeriesReadModel,
} from './capability-execution.js'

type JsonObject = Record<string, unknown>
type FetchResponse = Pick<Response, 'ok' | 'status' | 'text'>
type Fetcher = (
  input: string,
  init?: { headers?: Record<string, string>, signal?: AbortSignal },
) => Promise<FetchResponse>

export type DomeyeReadModelErrorCode =
  | 'invalid_reference'
  | 'cancelled'
  | 'read_timeout'
  | 'data_api_unavailable'
  | 'evidence_not_found'
  | 'invalid_data'
  | 'identity_conflict'
  | 'capability_unavailable'

export class DomeyeReadModelError extends Error {
  constructor(
    readonly code: DomeyeReadModelErrorCode,
    message: string,
    readonly retryable = false,
  ) {
    super(message)
    this.name = 'DomeyeReadModelError'
  }
}

export interface DomeyeVerifiedIdentityReceipt {
  readonly schema_version: 'domeye_verified_data_identity_receipt_v1'
  readonly receipt_id: string
  readonly candidate_id: string
  readonly reference_sha256: string
  readonly data_identity: DomeyeDataIdentity
  readonly resolver_response_sha256: string
  readonly overview_response_sha256: string
  readonly evidence_refs: readonly string[]
  readonly immutable: true
  readonly verified_at_utc: string
}

export interface DomeyeDataIdentityVerifier {
  verify(
    request: {
      readonly reference: string
      readonly publication_id: string
      readonly revision: number
      readonly candidate_id: string
    },
    signal?: AbortSignal,
  ): Promise<DomeyeVerifiedIdentityReceipt>
}

function isObject(value: unknown): value is JsonObject {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

function object(value: unknown, label: string): JsonObject {
  if (!isObject(value)) {
    throw new DomeyeReadModelError('invalid_data', `${label} 不是对象`)
  }
  return value
}

function string(value: unknown, label: string): string {
  if (typeof value !== 'string' || !value.trim()) {
    throw new DomeyeReadModelError('invalid_data', `${label} 缺失或无效`)
  }
  return value
}

function integer(value: unknown, label: string): number {
  if (!Number.isSafeInteger(value)) {
    throw new DomeyeReadModelError('invalid_data', `${label} 缺失或无效`)
  }
  return value as number
}

function boolean(value: unknown, label: string): boolean {
  if (typeof value !== 'boolean') {
    throw new DomeyeReadModelError('invalid_data', `${label} 缺失或无效`)
  }
  return value
}

function sha256(text: string): string {
  return createHash('sha256').update(text, 'utf8').digest('hex')
}

function deepFreeze<T>(value: T): Readonly<T> {
  if (value !== null && typeof value === 'object' && !Object.isFrozen(value)) {
    for (const child of Object.values(value as Record<string, unknown>)) {
      deepFreeze(child)
    }
    Object.freeze(value)
  }
  return value
}

function sameIdentityPayload(
  payload: JsonObject,
  identity: DomeyeDataIdentity,
): boolean {
  return (payload.event_type === undefined
      || payload.event_type === identity.event_type)
    && (payload.country_code === undefined
      || payload.country_code === identity.country_code)
    && payload.incident_id === identity.incident_id
    && payload.publication_id === identity.publication_id
    && payload.revision === identity.revision
    && payload.collector_id === identity.collector_id
    && payload.cohort_id === identity.cohort_id
    && payload.window_start_utc === identity.window_start_utc
    && payload.window_end_utc === identity.window_end_utc
    && payload.data_through === identity.data_through
    && payload.is_final_in_data_range === identity.is_final_in_data_range
    && payload.lifecycle_state === identity.lifecycle_state
}

function identityFromResolution(payload: JsonObject): DomeyeDataIdentity {
  if (
    payload.schema_version !== 'country_outage_general_resolution_v1'
    || payload.event_type !== 'country_outage'
    || payload.collector_id !== 'rrc25'
  ) {
    throw new DomeyeReadModelError(
      'invalid_data',
      'resolver 未返回 RRC25 country_outage 身份',
    )
  }
  const identity: DomeyeDataIdentity = {
    event_type: 'country_outage',
    incident_id: string(payload.incident_id, 'incident_id'),
    publication_id: string(payload.publication_id, 'publication_id'),
    revision: integer(payload.revision, 'revision'),
    collector_id: 'rrc25',
    cohort_id: string(payload.cohort_id, 'cohort_id'),
    country_code: string(payload.country_code, 'country_code'),
    window_start_utc: string(payload.window_start_utc, 'window_start_utc'),
    window_end_utc: string(payload.window_end_utc, 'window_end_utc'),
    data_through: string(payload.data_through, 'data_through'),
    is_final_in_data_range: boolean(
      payload.is_final_in_data_range,
      'is_final_in_data_range',
    ),
    lifecycle_state: payload.lifecycle_state === 'event_end_unknown'
      ? 'event_end_unknown'
      : (() => {
          throw new DomeyeReadModelError(
            'identity_conflict',
            '首片只允许 event_end_unknown 生命周期',
          )
        })(),
  }
  if (
    !/^[A-Z]{2}$/.test(identity.country_code)
    || !Number.isFinite(Date.parse(identity.window_start_utc))
    || !Number.isFinite(Date.parse(identity.window_end_utc))
    || !Number.isFinite(Date.parse(identity.data_through))
    || Date.parse(identity.window_start_utc) >= Date.parse(identity.window_end_utc)
    || identity.data_through !== identity.window_end_utc
  ) {
    throw new DomeyeReadModelError('invalid_data', 'resolver 时间或国家身份无效')
  }
  const capabilities = object(payload.capabilities, 'capabilities')
  if (
    capabilities.overview !== 'available'
    || capabilities.event_series !== 'available'
  ) {
    throw new DomeyeReadModelError(
      'capability_unavailable',
      'resolver 未声明 overview 和 event_series 可用',
    )
  }
  return identity
}

export class HttpCountryOutageReadModel
implements CountryOutageSeriesReadModel, DomeyeDataIdentityVerifier {
  readonly #baseUrl: string
  readonly #timeoutMs: number
  readonly #fetch: Fetcher
  readonly #now: () => Date

  constructor(
    baseUrl: string,
    options: {
      readonly timeout_ms?: number
      readonly fetch?: Fetcher
      readonly now?: () => Date
    } = {},
  ) {
    const parsed = new URL(baseUrl)
    if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {
      throw new DomeyeReadModelError('invalid_reference', '数据 API 必须使用 HTTP(S)')
    }
    this.#baseUrl = baseUrl.endsWith('/') ? baseUrl : `${baseUrl}/`
    this.#timeoutMs = options.timeout_ms ?? 15_000
    if (
      !Number.isSafeInteger(this.#timeoutMs)
      || this.#timeoutMs < 1
      || this.#timeoutMs > 30_000
    ) {
      throw new DomeyeReadModelError('invalid_reference', '数据 API 超时配置无效')
    }
    this.#fetch = options.fetch ?? fetch
    this.#now = options.now ?? (() => new Date())
  }

  #url(path: string, params: Record<string, string | number>): string {
    const value = new URL(path.replace(/^\/+/, ''), this.#baseUrl)
    for (const [key, item] of Object.entries(params)) {
      value.searchParams.set(key, String(item))
    }
    return value.toString()
  }

  async #get(
    path: string,
    params: Record<string, string | number>,
    signal?: AbortSignal,
  ): Promise<{ payload: JsonObject, response_sha256: string }> {
    signal?.throwIfAborted()
    const controller = new AbortController()
    let timedOut = false
    const timer = setTimeout(() => {
      timedOut = true
      controller.abort()
    }, this.#timeoutMs)
    timer.unref()
    const onAbort = (): void => controller.abort()
    signal?.addEventListener('abort', onAbort, { once: true })
    try {
      const response = await this.#fetch(this.#url(path, params), {
        headers: { Accept: 'application/json' },
        signal: controller.signal,
      })
      if (!response.ok) {
        throw new DomeyeReadModelError(
          response.status === 404 ? 'evidence_not_found' : 'data_api_unavailable',
          `Domeye 数据 API 返回 ${response.status}`,
          response.status >= 500,
        )
      }
      const text = await response.text()
      let parsed: unknown
      try {
        parsed = JSON.parse(text)
      } catch {
        throw new DomeyeReadModelError('invalid_data', `${path} 不是有效 JSON`)
      }
      return { payload: object(parsed, path), response_sha256: sha256(text) }
    } catch (error) {
      if (error instanceof DomeyeReadModelError) throw error
      if (signal?.aborted) {
        throw new DomeyeReadModelError('cancelled', '数据读取已取消')
      }
      if (timedOut) {
        throw new DomeyeReadModelError('read_timeout', '数据读取超时', true)
      }
      throw new DomeyeReadModelError('data_api_unavailable', '数据 API 不可用', true)
    } finally {
      clearTimeout(timer)
      signal?.removeEventListener('abort', onAbort)
    }
  }

  async verify(
    request: {
      readonly reference: string
      readonly publication_id: string
      readonly revision: number
      readonly candidate_id: string
    },
    signal?: AbortSignal,
  ): Promise<DomeyeVerifiedIdentityReceipt> {
    if (!request.reference.trim() || !request.candidate_id.trim()) {
      throw new DomeyeReadModelError('invalid_reference', '事件引用或 Candidate 无效')
    }
    const resolution = await this.#get(
      'events/resolve',
      { ref: request.reference },
      signal,
    )
    const identity = identityFromResolution(resolution.payload)
    if (
      identity.publication_id !== request.publication_id
      || identity.revision !== request.revision
    ) {
      throw new DomeyeReadModelError(
        'identity_conflict',
        'resolver 身份与请求的 publication/revision 不一致',
      )
    }
    const overview = await this.#get(
      `country-outages/${encodeURIComponent(identity.incident_id)}/overview`,
      { publication_id: identity.publication_id, revision: identity.revision },
      signal,
    )
    if (
      overview.payload.schema_version
        !== 'country_outage_general_overview_v1'
      || !sameIdentityPayload(overview.payload, identity)
    ) {
      throw new DomeyeReadModelError(
        'identity_conflict',
        'overview 与 resolver 身份不一致',
      )
    }
    const verifiedAt = this.#now().toISOString()
    const receiptBody = {
      candidate_id: request.candidate_id,
      reference_sha256: sha256(request.reference),
      data_identity: identity,
      resolver_response_sha256: resolution.response_sha256,
      overview_response_sha256: overview.response_sha256,
      verified_at_utc: verifiedAt,
    }
    return deepFreeze({
      schema_version: 'domeye_verified_data_identity_receipt_v1',
      receipt_id: `identity-receipt-sha256:${sha256(JSON.stringify(receiptBody))}`,
      ...receiptBody,
      evidence_refs: [
        `domeye:evidence:resolver:sha256:${resolution.response_sha256}`,
        `domeye:evidence:overview:sha256:${overview.response_sha256}`,
      ],
      immutable: true,
    }) as DomeyeVerifiedIdentityReceipt
  }

  async readMetricSeries(
    request: CountryOutageMetricSeriesReadRequest,
    signal?: AbortSignal,
  ): Promise<CountryOutageMetricSeriesRead> {
    const identity = request.data_identity
    const response = await this.#get(
      `country-outages/${encodeURIComponent(identity.incident_id)}/series`,
      { publication_id: identity.publication_id, revision: identity.revision },
      signal,
    )
    if (
      response.payload.schema_version !== 'country_outage_general_series_v1'
      || !sameIdentityPayload(response.payload, identity)
    ) {
      throw new DomeyeReadModelError(
        'identity_conflict',
        'series 与准入数据身份不一致',
      )
    }
    const tracks = object(response.payload.tracks, 'tracks')
    const definitions = object(response.payload.track_definitions, 'track_definitions')
    const definition = object(definitions[request.metric], 'metric definition')
    const rawValues = tracks[request.metric]
    const rawTimestamps = response.payload.timestamps
    if (!Array.isArray(rawValues) || !Array.isArray(rawTimestamps)) {
      throw new DomeyeReadModelError('invalid_data', 'series 轨道或时间轴无效')
    }
    const values = rawValues.map((value) => {
      if (value === null) return null
      if (!Number.isSafeInteger(value) || Number(value) < 0) {
        throw new DomeyeReadModelError('invalid_data', 'series 值必须为非负整数或 null')
      }
      return Number(value)
    })
    const timestamps = rawTimestamps.map((value) => string(value, 'timestamp'))
    return Object.freeze({
      data_identity: identity,
      metric: request.metric,
      unit: string(definition.unit, 'metric unit'),
      timestamps_utc: Object.freeze(timestamps),
      values: Object.freeze(values),
      population_definition:
        'normalized_deduplicated_merged_fixed_prefix_ipv4_unique_address_union',
      definition: string(definition.definition, 'metric definition'),
      completeness: Object.freeze({
        state: response.payload.quality_state === 'complete'
          && response.payload.observation_state === 'evidence_complete'
          ? 'complete' as const
          : 'incomplete' as const,
        missing_slot_count: integer(
          response.payload.missing_slot_count,
          'missing_slot_count',
        ),
      }),
      source_response_sha256: `sha256:${response.response_sha256}`,
      evidence_refs: Object.freeze([
        `domeye:evidence:series:sha256:${response.response_sha256}`,
        `sha256:${response.response_sha256}`,
      ]),
    })
  }
}
