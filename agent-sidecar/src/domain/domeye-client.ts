import type {
  CountryOutageAsnPage,
  CountryOutageAudit,
  CountryOutageOverview,
  CountryOutageResolution,
  CountryOutageSeries,
  CountryOutageTrendProduct,
  ObservationBatch,
  SnapshotEnvelope,
  SnapshotIdentity,
} from './contracts.js'
import {
  DomeyeApiError,
  InvalidCountryOutageReferenceError,
  SnapshotConflictError,
} from './errors.js'

const COUNTRY_OUTAGE_REFERENCE =
  /^country_outage\/\d{4}-\d{2}-\d{2}[ +]\d{2}:\d{2}:\d{2}\/([A-Z]{2})\/[1-9]\d*\/[A-Za-z0-9_-]+$/
const VISIBILITY_SLOT_STATES = new Set([
  'observed',
  'source_unavailable',
  'processing_gap',
  'parse_failed',
  'not_observed',
])
const GAP_SLOT_METADATA_FIELDS = new Set([
  'snapshot_id',
  'observed_at_utc',
  'observed_at_local',
  'slot_state',
  'missing_reason',
])

export interface DomeyeClientOptions {
  baseUrl: string
  timeoutMs?: number
  maximumSnapshotBatchRetries?: number
  fetchImplementation?: typeof fetch
}

export interface AsnQuery {
  page?: number
  pageSize?: number
  query?: string
  addressFamily?: 'all' | 'ipv4' | 'ipv6'
  state?: 'all' | 'fully_visible' | 'partially_visible' | 'fully_invisible'
  sort?: string
}

function normalizeBaseUrl(value: string): URL {
  const url = new URL(value)
  if (!['http:', 'https:'].includes(url.protocol)) {
    throw new TypeError('Domeye API 只允许 HTTP/HTTPS 基址')
  }
  url.pathname = url.pathname.replace(/\/+$/, '') + '/'
  url.search = ''
  url.hash = ''
  return url
}

function isEnvelope(value: unknown): value is SnapshotEnvelope {
  if (!value || typeof value !== 'object') return false
  const item = value as Record<string, unknown>
  return (
    typeof item.incident_id === 'string' &&
    typeof item.publication_id === 'string' &&
    typeof item.revision === 'number' &&
    (typeof item.data_through === 'string' || item.data_through === null) &&
    typeof item.is_final === 'boolean' &&
    typeof item.observation_state === 'string' &&
    typeof item.publication_state === 'string' &&
    typeof item.window_start_utc === 'string' &&
    typeof item.window_end_utc === 'string'
  )
}

function canonicalCountryOutageReference(
  reference: string,
): string | null {
  const normalized = reference
    .trim()
    .replace(
      /^(country_outage\/\d{4}-\d{2}-\d{2})\+/,
      '$1 ',
    )
  return COUNTRY_OUTAGE_REFERENCE.test(normalized) ? normalized : null
}

function countryCodeFromReference(reference: string): string | null {
  const canonical = canonicalCountryOutageReference(reference)
  return canonical
    ? COUNTRY_OUTAGE_REFERENCE.exec(canonical)?.[1] ?? null
    : null
}

function assertResolution(
  value: unknown,
  requestedReference: string,
): asserts value is CountryOutageResolution {
  if (!value || typeof value !== 'object') {
    throw new DomeyeApiError('resolve 返回值不是对象', 502, true)
  }
  const item = value as Record<string, unknown>
  if (
    item.schema_version !== 'country_outage_resolution_v2' ||
    item.event_type !== 'country_outage' ||
    typeof item.incident_id !== 'string' ||
    item.incident_id.trim().length === 0 ||
    typeof item.publication_id !== 'string' ||
    item.publication_id.trim().length === 0 ||
    !Number.isSafeInteger(item.latest_revision) ||
    (item.latest_revision as number) < 1 ||
    typeof item.legacy_reference !== 'string' ||
    typeof item.observation_state !== 'string' ||
    typeof item.is_final !== 'boolean' ||
    !Number.isSafeInteger(item.missing_slot_count) ||
    (item.missing_slot_count as number) < 0 ||
    (
      typeof item.data_through !== 'string' &&
      item.data_through !== null
    )
  ) {
    throw new DomeyeApiError('resolve 返回值不符合国家中断 v2 合同', 502, true)
  }
  if (item.legacy_reference !== requestedReference) {
    throw new SnapshotConflictError(
      'resolve 返回的国家中断引用与本次请求不一致',
    )
  }
}

function assertSnapshotPayload<T extends SnapshotEnvelope>(
  value: unknown,
  schemaVersion: string,
): asserts value is T {
  if (!isEnvelope(value)) {
    throw new DomeyeApiError(`${schemaVersion} 返回值缺少快照身份`, 502, true)
  }
  if (
    (value as unknown as Record<string, unknown>).schema_version !==
    schemaVersion
  ) {
    throw new DomeyeApiError(`${schemaVersion} 返回了错误 schema`, 502, true)
  }
}

function assertTrendProduct(
  value: unknown,
  batch: ObservationBatch,
): asserts value is CountryOutageTrendProduct {
  if (!value || typeof value !== 'object') {
    throw new DomeyeApiError('趋势制品返回值不是对象', 502, true)
  }
  const item = value as Record<string, unknown>
  const snapshot = item.snapshot as Record<string, unknown> | undefined
  const graph = item.evidence_graph as Record<string, unknown> | undefined
  const renderContract = item.render_contract as Record<string, unknown> | undefined
  if (
    item.schema_version !== 'country_outage_trend_product_v1' ||
    typeof item.product_id !== 'string' ||
    typeof item.profile_id !== 'string' ||
    typeof item.analysis_id !== 'string' ||
    typeof item.graph_id !== 'string' ||
    !snapshot ||
    snapshot.incident_id !== batch.overview.incident_id ||
    snapshot.publication_id !== batch.overview.publication_id ||
    snapshot.revision !== batch.overview.revision ||
    snapshot.data_through !== batch.overview.data_through ||
    snapshot.collector_id !== 'rrc25' ||
    snapshot.window_start_utc !== batch.overview.window_start_utc ||
    snapshot.window_end_utc !== batch.overview.window_end_utc ||
    !graph ||
    graph.schema_version !== 'country_outage_evidence_graph_v1' ||
    graph.graph_id !== item.graph_id ||
    graph.profile_id !== item.profile_id ||
    graph.analysis_id !== item.analysis_id ||
    graph.hypothesis_nodes_allowed !== false ||
    graph.causal_relations_allowed !== false ||
    !Array.isArray(graph.nodes) ||
    !Array.isArray(graph.edges) ||
    !renderContract ||
    renderContract.source_product_id !== item.product_id ||
    renderContract.model_may_rewrite_deterministic_values !== false
  ) {
    throw new SnapshotConflictError(
      '趋势制品与 overview/series/audit 固定快照身份不一致',
    )
  }
}

function envelopeKey(value: SnapshotEnvelope): string {
  return JSON.stringify({
    incident_id: value.incident_id,
    publication_id: value.publication_id,
    revision: value.revision,
    data_through: value.data_through,
    is_final: value.is_final,
    publication_state: value.publication_state,
    observation_state: value.observation_state,
    window_start_utc: value.window_start_utc,
    window_end_utc: value.window_end_utc,
    cohort_id: value.cohort_id ?? '',
  })
}

function timestamp(value: string): number | null {
  const parsed = Date.parse(value)
  return Number.isFinite(parsed) ? parsed : null
}

function isShanghaiLocalTimestamp(
  value: string,
  expectedInstant: number,
): boolean {
  return (
    /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,3})?\+08:00$/.test(
      value,
    ) &&
    timestamp(value) === expectedInstant
  )
}

function nonNegativeSafeInteger(value: unknown): value is number {
  return (
    typeof value === 'number' &&
    Number.isSafeInteger(value) &&
    value >= 0
  )
}

function gapSlotHasResidualFact(slot: Record<string, unknown>): boolean {
  return Object.entries(slot).some(
    ([key, value]) =>
      !GAP_SLOT_METADATA_FIELDS.has(key) &&
      value !== null &&
      value !== undefined,
  )
}

function assertNestedObservationIdentity(batch: ObservationBatch): void {
  const overview = batch.overview
  const scope = overview.observation_scope
  const countryCode = countryCodeFromReference(
    batch.resolution.legacy_reference,
  )
  if (
    !countryCode ||
    overview.event_identity.incident_id !== overview.incident_id ||
    overview.event_identity.legacy_reference !==
      batch.resolution.legacy_reference ||
    overview.event_identity.event_type !== 'country_outage' ||
    overview.event_identity.country_code !== countryCode
  ) {
    throw new SnapshotConflictError(
      'event_identity 与请求引用或固定快照身份不一致',
    )
  }
  if (
    scope.window_start_utc !== overview.window_start_utc ||
    scope.window_end_utc !== overview.window_end_utc
  ) {
    throw new SnapshotConflictError(
      'observation_scope 与快照 envelope 的观测窗口不一致',
    )
  }
  if (
    overview.cohort &&
    overview.cohort.cohort_id !== (overview.cohort_id ?? '')
  ) {
    throw new SnapshotConflictError(
      '固定 cohort 与快照 envelope 的 cohort_id 不一致',
    )
  }
  if (scope.interval_seconds !== batch.series.interval_seconds) {
    throw new SnapshotConflictError(
      'observation_scope 与 series 的观测间隔不一致',
    )
  }
  if (
    overview.capability_contract_version !==
      batch.resolution.capability_contract_version
  ) {
    throw new SnapshotConflictError(
      'resolve 与 overview 的能力合同版本不一致',
    )
  }

  const windowStart = timestamp(overview.window_start_utc)
  const windowEnd = timestamp(overview.window_end_utc)
  const localWindowStart = timestamp(scope.window_start_local)
  const localWindowEnd = timestamp(scope.window_end_local)
  if (
    windowStart === null ||
    windowEnd === null ||
    windowStart > windowEnd ||
    localWindowStart !== windowStart ||
    localWindowEnd !== windowEnd ||
    !isShanghaiLocalTimestamp(scope.window_start_local, windowStart) ||
    !isShanghaiLocalTimestamp(scope.window_end_local, windowEnd) ||
    scope.timezone !== 'Asia/Shanghai'
  ) {
    throw new SnapshotConflictError('观测窗口时间无效或 UTC/local 语义不一致')
  }

  const intervalSeconds = scope.interval_seconds
  const intervalMilliseconds =
    typeof intervalSeconds === 'number' &&
    Number.isSafeInteger(intervalSeconds) &&
    intervalSeconds > 0
      ? intervalSeconds * 1000
      : null
  const windowSpan = windowEnd - windowStart
  const expectedGridCount =
    intervalMilliseconds !== null &&
    windowSpan % intervalMilliseconds === 0
      ? windowSpan / intervalMilliseconds + 1
      : null
  if (
    intervalMilliseconds !== null &&
    (
      expectedGridCount === null ||
      !Number.isSafeInteger(expectedGridCount) ||
      expectedGridCount < 1 ||
      batch.series.series.length !== expectedGridCount
    )
  ) {
    throw new SnapshotConflictError(
      'series 未完整覆盖固定窗口的观测时间网格',
    )
  }

  let observedSlotCount = 0
  let previousSlotAt: number | null = null
  const missingSlots = new Map<
    string,
    { slotState: string; missingReason: string }
  >()
  for (const [index, slot] of batch.series.series.entries()) {
    const observedAt = timestamp(slot.observed_at_utc)
    const observedAtLocal = timestamp(slot.observed_at_local)
    const slotState = (slot as unknown as Record<string, unknown>)
      .slot_state
    const missingReason = (slot as unknown as Record<string, unknown>)
      .missing_reason
    if (
      typeof slotState !== 'string' ||
      !VISIBILITY_SLOT_STATES.has(slotState) ||
      (slotState === 'observed' &&
        missingReason !== undefined &&
        missingReason !== null) ||
      (slotState !== 'observed' &&
        (typeof missingReason !== 'string' ||
          missingReason.trim().length === 0)) ||
      observedAt === null ||
      observedAtLocal !== observedAt ||
      !isShanghaiLocalTimestamp(slot.observed_at_local, observedAt) ||
      observedAt < windowStart ||
      observedAt > windowEnd ||
      (previousSlotAt !== null && observedAt <= previousSlotAt) ||
      (
        intervalMilliseconds !== null &&
        observedAt !== windowStart + index * intervalMilliseconds
      ) ||
      (
        slotState !== 'observed' &&
        gapSlotHasResidualFact(
          slot as unknown as Record<string, unknown>,
        )
      )
    ) {
      throw new SnapshotConflictError(
        `series[${index}] 的观测时间与固定窗口或时间粒度不一致`,
      )
    }
    if (slotState === 'observed') {
      observedSlotCount += 1
    } else {
      missingSlots.set(slot.observed_at_utc, {
        slotState,
        missingReason: missingReason as string,
      })
    }
    previousSlotAt = observedAt
  }

  const missingSlotCount = missingSlots.size
  const expectedObservationCount =
    expectedGridCount ?? batch.series.series.length
  if (
    !nonNegativeSafeInteger(scope.observation_count) ||
    scope.observation_count !== observedSlotCount ||
    !nonNegativeSafeInteger(scope.expected_observation_count) ||
    scope.expected_observation_count !== expectedObservationCount ||
    !nonNegativeSafeInteger(scope.missing_observation_count) ||
    scope.missing_observation_count !== missingSlotCount ||
    !nonNegativeSafeInteger(batch.resolution.missing_slot_count) ||
    batch.resolution.missing_slot_count !== missingSlotCount ||
    !nonNegativeSafeInteger(overview.missing_slot_count) ||
    overview.missing_slot_count !== missingSlotCount ||
    !nonNegativeSafeInteger(batch.series.missing_slot_count) ||
    batch.series.missing_slot_count !== missingSlotCount ||
    !nonNegativeSafeInteger(batch.audit.missing_slot_count) ||
    batch.audit.missing_slot_count !== missingSlotCount ||
    !Array.isArray(batch.audit.missing_slots) ||
    batch.audit.missing_slots.length !== missingSlotCount
  ) {
    throw new SnapshotConflictError(
      '观测槽、预期槽与缺槽计数不一致',
    )
  }

  const declaredMissing = new Set<string>()
  for (const [index, raw] of batch.audit.missing_slots.entries()) {
    const item =
      raw && typeof raw === 'object'
        ? raw as Record<string, unknown>
        : null
    const observedAt = item?.observed_at
    const slotState = item?.slot_state
    const missingReason = item?.missing_reason
    const expected =
      typeof observedAt === 'string'
        ? missingSlots.get(observedAt)
        : undefined
    if (
      !expected ||
      declaredMissing.has(observedAt as string) ||
      slotState !== expected.slotState ||
      missingReason !== expected.missingReason
    ) {
      throw new SnapshotConflictError(
        `audit.missing_slots[${index}] 与 series 缺槽不一致`,
      )
    }
    declaredMissing.add(observedAt as string)
  }

  const lastObservedSlot = [...batch.series.series]
    .reverse()
    .find((slot) => slot.slot_state === 'observed')
  const expectedLastObservedUtc =
    lastObservedSlot?.observed_at_utc ?? null
  const expectedLastObservedLocal =
    lastObservedSlot?.observed_at_local ?? null
  if (
    scope.last_observation_at_utc !== expectedLastObservedUtc ||
    scope.last_observation_at_local !== expectedLastObservedLocal
  ) {
    throw new SnapshotConflictError(
      'observation_scope 的最后观测时间与 series 不一致',
    )
  }
}

export function assertBatchIdentity(batch: ObservationBatch): void {
  const expected = {
    incidentId: batch.resolution.incident_id,
    publicationId: batch.resolution.publication_id,
    revision: batch.resolution.latest_revision,
    dataThrough: batch.resolution.data_through,
    isFinal: batch.resolution.is_final,
  }
  const envelopes = [batch.overview, batch.series, batch.audit]
  const firstKey = envelopeKey(envelopes[0]!)
  if (envelopes.some((value) => envelopeKey(value) !== firstKey)) {
    throw new SnapshotConflictError('overview、series、audit 的快照身份不一致')
  }
  const overview = batch.overview
  if (
    overview.incident_id !== expected.incidentId ||
    overview.publication_id !== expected.publicationId ||
    overview.revision !== expected.revision ||
    overview.data_through !== expected.dataThrough ||
    overview.is_final !== expected.isFinal ||
    overview.observation_state !== batch.resolution.observation_state
  ) {
    throw new SnapshotConflictError('resolve 与观测接口的快照身份不一致')
  }
  assertNestedObservationIdentity(batch)
}

export function assertAsnPageIdentity(
  page: CountryOutageAsnPage,
  snapshot: SnapshotIdentity,
): void {
  if (
    page.incident_id !== snapshot.incidentId ||
    page.publication_id !== snapshot.publicationId ||
    page.revision !== snapshot.revision ||
    page.data_through !== snapshot.dataThrough ||
    page.window_start_utc !== snapshot.windowStartUtc ||
    page.window_end_utc !== snapshot.windowEndUtc ||
    (page.cohort_id ?? '') !== snapshot.cohortId
  ) {
    throw new SnapshotConflictError('ASN 分页结果与报告快照身份不一致')
  }
}

export class DomeyeCountryOutageClient {
  readonly #baseUrl: URL
  readonly #timeoutMs: number
  readonly #maximumSnapshotBatchRetries: number
  readonly #fetch: typeof fetch

  constructor(options: DomeyeClientOptions) {
    this.#baseUrl = normalizeBaseUrl(options.baseUrl)
    this.#timeoutMs = options.timeoutMs ?? 5000
    this.#maximumSnapshotBatchRetries =
      options.maximumSnapshotBatchRetries ?? 2
    this.#fetch = options.fetchImplementation ?? fetch
  }

  async #getJson(
    path: string,
    query?: URLSearchParams,
    signal?: AbortSignal,
  ): Promise<unknown> {
    const url = new URL(path.replace(/^\/+/, ''), this.#baseUrl)
    if (query) url.search = query.toString()
    signal?.throwIfAborted()
    const timeoutSignal = AbortSignal.timeout(this.#timeoutMs)
    const requestSignal = signal
      ? AbortSignal.any([signal, timeoutSignal])
      : timeoutSignal
    const assertRequestActive = (): void => {
      if (signal?.aborted) signal.throwIfAborted()
      if (timeoutSignal.aborted) {
        throw new DomeyeApiError('Domeye API 请求超时', 503, true)
      }
    }
    let response: Response
    try {
      response = await this.#fetch(url, {
        method: 'GET',
        headers: { Accept: 'application/json' },
        signal: requestSignal,
      })
      assertRequestActive()
    } catch (error) {
      if (signal?.aborted) signal.throwIfAborted()
      if (error instanceof DomeyeApiError) throw error
      if (timeoutSignal.aborted) {
        throw new DomeyeApiError('Domeye API 请求超时', 503, true)
      }
      throw new DomeyeApiError(
        error instanceof Error ? error.message : 'Domeye API 请求失败',
        503,
        true,
      )
    }
    if (!response.ok) {
      let message: string
      try {
        message = await response.text()
        assertRequestActive()
      } catch (error) {
        if (signal?.aborted) signal.throwIfAborted()
        if (error instanceof DomeyeApiError) throw error
        if (timeoutSignal.aborted) {
          throw new DomeyeApiError('Domeye API 请求超时', 503, true)
        }
        throw new DomeyeApiError(
          error instanceof Error ? error.message : 'Domeye API 请求失败',
          503,
          true,
        )
      }
      throw new DomeyeApiError(
        message || `Domeye API 返回 HTTP ${response.status}`,
        response.status,
        response.status >= 500,
      )
    }
    try {
      const value = await response.json()
      assertRequestActive()
      return value
    } catch (error) {
      if (signal?.aborted) signal.throwIfAborted()
      if (error instanceof DomeyeApiError) throw error
      if (timeoutSignal.aborted) {
        throw new DomeyeApiError('Domeye API 请求超时', 503, true)
      }
      throw new DomeyeApiError('Domeye API 返回非 JSON 内容', 502, true)
    }
  }

  async resolve(
    reference: string,
    signal?: AbortSignal,
  ): Promise<CountryOutageResolution> {
    const normalizedReference =
      canonicalCountryOutageReference(reference)
    if (!normalizedReference) {
      throw new InvalidCountryOutageReferenceError()
    }
    const query = new URLSearchParams({ ref: normalizedReference })
    const value = await this.#getJson('events/resolve', query, signal)
    signal?.throwIfAborted()
    assertResolution(value, normalizedReference)
    return value
  }

  async #readPinnedBatch(
    resolution: CountryOutageResolution,
    signal?: AbortSignal,
  ): Promise<ObservationBatch> {
    signal?.throwIfAborted()
    const incident = encodeURIComponent(resolution.incident_id)
    const query = new URLSearchParams({
      publication_id: resolution.publication_id,
    })
    const [overviewValue, seriesValue, auditValue] = await Promise.all([
      this.#getJson(`country-outages/${incident}/overview`, query, signal),
      this.#getJson(`country-outages/${incident}/series`, query, signal),
      this.#getJson(`country-outages/${incident}/audit`, query, signal),
    ])
    signal?.throwIfAborted()
    assertSnapshotPayload<CountryOutageOverview>(
      overviewValue,
      'country_outage_overview_v2',
    )
    assertSnapshotPayload<CountryOutageSeries>(
      seriesValue,
      'country_outage_series_v2',
    )
    assertSnapshotPayload<CountryOutageAudit>(
      auditValue,
      'country_outage_audit_v2',
    )
    const batch: ObservationBatch = {
      resolution,
      overview: overviewValue,
      series: seriesValue,
      audit: auditValue,
    }
    assertBatchIdentity(batch)
    if (overviewValue.capabilities.trend_analysis?.state === 'available') {
      const trendValue = await this.#getJson(
        `country-outages/${incident}/trend`,
        query,
        signal,
      )
      signal?.throwIfAborted()
      assertTrendProduct(trendValue, batch)
      batch.trendProduct = trendValue
    }
    return batch
  }

  async getObservationBatch(
    reference: string,
    signal?: AbortSignal,
  ): Promise<ObservationBatch> {
    let lastConflict: SnapshotConflictError | undefined
    for (
      let attempt = 0;
      attempt <= this.#maximumSnapshotBatchRetries;
      attempt += 1
    ) {
      signal?.throwIfAborted()
      try {
        const resolution = await this.resolve(reference, signal)
        return await this.#readPinnedBatch(resolution, signal)
      } catch (error) {
        if (signal?.aborted) signal.throwIfAborted()
        if (!(error instanceof SnapshotConflictError)) throw error
        lastConflict = error
      }
    }
    throw (
      lastConflict ??
      new SnapshotConflictError('无法获得身份一致的国家中断快照')
    )
  }

  async getAsns(
    snapshot: SnapshotIdentity,
    query: AsnQuery = {},
    signal?: AbortSignal,
  ): Promise<CountryOutageAsnPage> {
    signal?.throwIfAborted()
    const page = Math.max(1, Math.trunc(query.page ?? 1))
    const pageSize = Math.min(60, Math.max(1, Math.trunc(query.pageSize ?? 60)))
    const parameters = new URLSearchParams({
      publication_id: snapshot.publicationId,
      page: String(page),
      page_size: String(pageSize),
      address_family: query.addressFamily ?? 'all',
      state: query.state ?? 'all',
      sort: query.sort ?? 'longest_fully_invisible_desc',
    })
    if (query.query) parameters.set('query', query.query.slice(0, 100))
    const incident = encodeURIComponent(snapshot.incidentId)
    const value = await this.#getJson(
      `country-outages/${incident}/asns`,
      parameters,
      signal,
    )
    signal?.throwIfAborted()
    assertSnapshotPayload<CountryOutageAsnPage>(
      value,
      'country_outage_asn_page_v2',
    )
    assertAsnPageIdentity(value, snapshot)
    return value
  }
}
