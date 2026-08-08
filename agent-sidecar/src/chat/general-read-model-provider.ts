import type { P1ConversationBinding } from './contracts.js'

type JsonObject = Record<string, any>

export interface P1FactBundle {
  binding: P1ConversationBinding
  resolution: JsonObject
  overview: JsonObject
  series: JsonObject
  asns: JsonObject
  paths: JsonObject
  audit: JsonObject
  derived: {
    ipv4: {
      maximum: number
      minimum: number
      drop: number
      drop_percent: number
      recovery: number
      recovery_percent: number
    }
    ipv6: {
      maximum: number
      minimum: number
      drop: number
      drop_percent: number
    }
  }
}
export interface P1GeneralReadModelProvider {
  load(
    reference: string,
    publicationId: string,
    revision: number,
    signal?: AbortSignal,
  ): Promise<P1FactBundle>
  resolve(reference: string, signal?: AbortSignal): Promise<P1ConversationBinding>
  findAsn(
    bundle: P1FactBundle,
    asn: number,
    signal?: AbortSignal,
  ): Promise<JsonObject | null>
}

export class P1ReadModelError extends Error {
  constructor(
    public readonly code: string,
    message: string,
    public readonly retryable = false,
  ) {
    super(message)
    this.name = 'P1ReadModelError'
  }
}

function object(value: unknown, label: string): JsonObject {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new P1ReadModelError('invalid_data', `${label} 不是 JSON 对象`)
  }
  return value as JsonObject
}

function string(value: unknown, label: string): string {
  if (typeof value !== 'string' || !value) {
    throw new P1ReadModelError('invalid_data', `${label} 缺失或无效`)
  }
  return value
}

function number(value: unknown, label: string): number {
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    throw new P1ReadModelError('invalid_data', `${label} 缺失或无效`)
  }
  return value
}

function bool(value: unknown, label: string): boolean {
  if (typeof value !== 'boolean') {
    throw new P1ReadModelError('invalid_data', `${label} 缺失或无效`)
  }
  return value
}

function bindingFromResolution(value: JsonObject): P1ConversationBinding {
  if (
    value.schema_version !== 'country_outage_general_resolution_v1' ||
    value.event_type !== 'country_outage' ||
    value.collector_id !== 'rrc25'
  ) {
    throw new P1ReadModelError(
      'unsupported_event',
      'P1 只接受 RRC25 country_outage general read-model 事件',
    )
  }
  return {
    incident_id: string(value.incident_id, 'incident_id'),
    legacy_reference: string(value.legacy_reference, 'legacy_reference'),
    publication_id: string(value.publication_id, 'publication_id'),
    revision: number(value.revision, 'revision'),
    collector_id: 'rrc25',
    cohort_id: string(value.cohort_id, 'cohort_id'),
    country_code: string(value.country_code, 'country_code'),
    window_start_utc: string(value.window_start_utc, 'window_start_utc'),
    window_end_utc: string(value.window_end_utc, 'window_end_utc'),
    data_through:
      value.data_through === null
        ? null
        : string(value.data_through, 'data_through'),
    is_final_in_data_range: bool(
      value.is_final_in_data_range,
      'is_final_in_data_range',
    ),
    lifecycle_state: string(value.lifecycle_state, 'lifecycle_state'),
  }
}

function assertSameIdentity(
  payload: JsonObject,
  binding: P1ConversationBinding,
  label: string,
): void {
  const actual = [
    payload.incident_id,
    payload.publication_id,
    payload.revision,
    payload.collector_id,
    payload.cohort_id,
  ]
  const expected = [
    binding.incident_id,
    binding.publication_id,
    binding.revision,
    binding.collector_id,
    binding.cohort_id,
  ]
  if (actual.some((value, index) => value !== expected[index])) {
    throw new P1ReadModelError(
      'publication_identity_conflict',
      `${label} 与会话 publication/revision 身份不一致`,
    )
  }
}

function extrema(values: unknown, label: string): { maximum: number, minimum: number } {
  if (!Array.isArray(values) || values.length === 0) {
    throw new P1ReadModelError('invalid_series_shape', `${label} 轨道无效`)
  }
  const numeric = values.map((value) => number(value, label))
  return { maximum: Math.max(...numeric), minimum: Math.min(...numeric) }
}

export class HttpP1GeneralReadModelProvider
implements P1GeneralReadModelProvider {
  constructor(
    private readonly baseUrl: string,
    private readonly timeoutMs = 15_000,
  ) {}

  private url(path: string, params: Record<string, string | number> = {}): string {
    const base = this.baseUrl.endsWith('/') ? this.baseUrl : `${this.baseUrl}/`
    const value = new URL(path.replace(/^\/+/, ''), base)
    for (const [key, item] of Object.entries(params)) {
      value.searchParams.set(key, String(item))
    }
    return value.toString()
  }

  private async get(
    path: string,
    params: Record<string, string | number>,
    signal?: AbortSignal,
  ): Promise<JsonObject> {
    const controller = new AbortController()
    const timeout = setTimeout(() => controller.abort(), this.timeoutMs)
    timeout.unref()
    const abort = (): void => controller.abort()
    signal?.addEventListener('abort', abort, { once: true })
    try {
      const response = await fetch(this.url(path, params), {
        headers: { Accept: 'application/json' },
        signal: controller.signal,
      })
      if (!response.ok) {
        throw new P1ReadModelError(
          response.status === 404 ? 'evidence_not_found' : 'data_api_unavailable',
          `Domeye 数据 API 返回 ${response.status}`,
          response.status >= 500,
        )
      }
      return object(await response.json(), path)
    } catch (error) {
      if (error instanceof P1ReadModelError) throw error
      if (signal?.aborted) {
        throw new P1ReadModelError('cancelled', '数据读取已取消')
      }
      throw new P1ReadModelError(
        'data_api_unavailable',
        error instanceof Error ? error.message : 'Domeye 数据 API 不可用',
        true,
      )
    } finally {
      clearTimeout(timeout)
      signal?.removeEventListener('abort', abort)
    }
  }

  async resolve(reference: string, signal?: AbortSignal): Promise<P1ConversationBinding> {
    const resolution = await this.get('events/resolve', { ref: reference }, signal)
    return bindingFromResolution(resolution)
  }

  async load(
    reference: string,
    publicationId: string,
    revision: number,
    signal?: AbortSignal,
  ): Promise<P1FactBundle> {
    const resolution = await this.get('events/resolve', { ref: reference }, signal)
    const binding = bindingFromResolution(resolution)
    if (
      binding.legacy_reference.replaceAll('+', ' ') !== reference.replaceAll('+', ' ') ||
      binding.publication_id !== publicationId ||
      binding.revision !== revision
    ) {
      throw new P1ReadModelError(
        'binding_conflict',
        '请求身份与当前事件 publication/revision 不一致',
      )
    }
    const id = encodeURIComponent(binding.incident_id)
    const params = { publication_id: binding.publication_id }
    const [overview, series, asns, paths, audit] = await Promise.all([
      this.get(`country-outages/${id}/overview`, params, signal),
      this.get(`country-outages/${id}/series`, params, signal),
      this.get(`country-outages/${id}/asns`, {
        ...params, page: 1, page_size: 20, classification: 'all', sort: 'default',
      }, signal),
      this.get(`country-outages/${id}/path-downstreams`, {
        ...params, page: 1, page_size: 15, scope: 'all',
      }, signal),
      this.get(`country-outages/${id}/audit`, params, signal),
    ])
    for (const [label, payload] of Object.entries({ overview, series, asns, paths, audit })) {
      assertSameIdentity(payload, binding, label)
    }
    const timestamps = series.timestamps
    const tracks = object(series.tracks, 'series.tracks')
    const pointCount = number(series.point_count, 'series.point_count')
    if (
      !Array.isArray(timestamps) || timestamps.length !== pointCount ||
      Object.values(tracks).some((track) => !Array.isArray(track) || track.length !== pointCount)
    ) {
      throw new P1ReadModelError(
        'invalid_series_shape',
        'series 点数、时间戳和轨道长度不一致',
      )
    }
    const ipv4 = extrema(tracks.fixed_visible_ipv4_address_count, 'IPv4')
    const ipv6 = extrema(tracks.fixed_visible_ipv6_slash48_count, 'IPv6')
    const current4 = number(overview.current?.fixed_visible_ipv4_address_count, 'current IPv4')
    const drop4 = ipv4.maximum - ipv4.minimum
    const recovery4 = current4 - ipv4.minimum
    return {
      binding,
      resolution,
      overview,
      series,
      asns,
      paths,
      audit,
      derived: {
        ipv4: {
          maximum: ipv4.maximum,
          minimum: ipv4.minimum,
          drop: drop4,
          drop_percent: Number((drop4 / ipv4.maximum * 100).toFixed(6)),
          recovery: recovery4,
          recovery_percent: Number((recovery4 / drop4 * 100).toFixed(6)),
        },
        ipv6: {
          maximum: ipv6.maximum,
          minimum: ipv6.minimum,
          drop: ipv6.maximum - ipv6.minimum,
          drop_percent: Number(((ipv6.maximum - ipv6.minimum) / ipv6.maximum * 100).toFixed(6)),
        },
      },
    }
  }

  async findAsn(
    bundle: P1FactBundle,
    asn: number,
    signal?: AbortSignal,
  ): Promise<JsonObject | null> {
    const existing = Array.isArray(bundle.asns.items)
      ? bundle.asns.items.find((item: JsonObject) => item.asn === asn)
      : undefined
    if (existing) return existing
    const result = await this.get(
      `country-outages/${encodeURIComponent(bundle.binding.incident_id)}/asns`,
      {
        publication_id: bundle.binding.publication_id,
        page: 1,
        page_size: 20,
        classification: 'all',
        sort: 'default',
        query: String(asn),
      },
      signal,
    )
    assertSameIdentity(result, bundle.binding, 'asns query')
    const items = Array.isArray(result.items) ? result.items : []
    return items.find((item: JsonObject) => item.asn === asn) ?? null
  }
}
