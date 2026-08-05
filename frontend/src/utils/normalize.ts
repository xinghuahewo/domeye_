import {
  CORE_EVENT_TYPES,
  EVENT_KIND_LABELS,
  type AsnProfile,
  type AsOverview,
  type CountPoint,
  type CountryOverview,
  type CountryProfile,
  type CountrySparkPoint,
  type DashboardOverview,
  type DashboardRanking,
  type CountryOutageAsnPage,
  type CountryOutageTrendProduct,
  type EvidenceBundle,
  type EvidenceItem,
  type EvidenceKind,
  type EvidencePhase,
  type EvidencePhaseCoverage,
  type EvidencePhaseStatus,
  type EventStory,
  type EventObservation,
  type EventObservationAudit,
  type EventKind,
  type EventLevel,
  type EventPage,
  type EventRow,
  type FeaturePoint,
  type LegacyEventSemanticGuardrails,
  type OutagePoint,
  type ParsedDetailRef,
} from '@/types/api'

export const isRecord = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === 'object' && !Array.isArray(value)

export const cleanText = (value: unknown): string => {
  if (value === null || value === undefined) return ''
  const text = String(value).trim()
  return ['None', 'NaT', 'null', 'undefined'].includes(text) ? '' : text
}

export const finiteNumber = (value: unknown): number | null => {
  if (value === '' || value === null || value === undefined) return null
  const number = typeof value === 'number' ? value : Number(value)
  return Number.isFinite(number) ? number : null
}

export const normalizeTime = (value: unknown): string | null => {
  const text = cleanText(value)
  if (!text || text === '-') return null
  return text.replace(/\s+/g, ' ')
}

const normalizeLevel = (value: unknown): EventLevel => {
  const level = cleanText(value).toLowerCase()
  return level === 'high' || level === 'middle' || level === 'low' ? level : 'unknown'
}

export const normalizeEventRow = (value: unknown): EventRow | null => {
  if (!isRecord(value)) return null
  const detailUrl = cleanText(value.detail_url)
  const type = cleanText(value.event_type) || '未知事件'
  const startTime = normalizeTime(value.start_time ?? value.s_time)
  const summary = cleanText(value.event_info)

  return {
    key: detailUrl || `${type}-${startTime || 'unknown'}-${summary}`,
    type,
    level: normalizeLevel(value.level ?? value.event_level),
    startTime,
    endTime: normalizeTime(value.end_time ?? value.e_time),
    attackerAs: cleanText(value.attacker_as),
    attackedAs: cleanText(value.attacked_as),
    attackerOrg: cleanText(value.attacker_org),
    attackedOrg: cleanText(value.attacked_org),
    attackerCountry: cleanText(value.attacker_country),
    attackedCountry: cleanText(value.attacked_country),
    affectedPrefix: cleanText(value.affected_prefix),
    summary,
    detailUrl,
  }
}

export const normalizeEventPage = (payload: unknown): EventPage => {
  if (!isRecord(payload)) throw new Error('事件列表响应格式异常')
  if (payload.status === false) throw new Error(cleanText(payload.msg) || '事件查询失败')
  const rows = Array.isArray(payload.data) ? payload.data : []
  const data = rows.map(normalizeEventRow).filter((row): row is EventRow => row !== null)

  return {
    data,
    totalPage: Math.max(0, finiteNumber(payload.total_page) ?? 0),
    recordCount: Math.max(0, finiteNumber(payload.record_count) ?? data.length),
  }
}

export const normalizeEventArray = (payload: unknown): EventRow[] => {
  if (isRecord(payload) && payload.status === false) {
    throw new Error(cleanText(payload.msg) || '事件查询失败')
  }
  const rows = Array.isArray(payload)
    ? payload
    : isRecord(payload) && Array.isArray(payload.data)
      ? payload.data
      : null
  if (!rows) throw new Error('事件响应格式异常')
  return rows.map(normalizeEventRow).filter((row): row is EventRow => row !== null)
}

const safeDecode = (value: string): string => {
  try {
    return decodeURIComponent(value)
  } catch {
    return value
  }
}

export const parseDetailUrl = (raw: string): ParsedDetailRef | null => {
  const preserved = raw.trim()
  const clean = preserved
    .replace(/^https?:\/\/[^/]+/i, '')
    .replace(/^\/+/, '')
    .replace(/^api\/v1\//, '')
  const parts = clean.split('/')
  if (parts.length !== 5) return null

  const [encodedKind, encodedStart, encodedProblem, encodedId, encodedSource] = parts
  if (!encodedKind || !encodedStart || !encodedProblem || !encodedId || !encodedSource) return null
  const kind = safeDecode(encodedKind) as EventKind
  const eventId = safeDecode(encodedId)
  if (!(kind in EVENT_KIND_LABELS) || !/^\d+$/.test(eventId)) return null

  return {
    raw: preserved,
    kind,
    startTime: safeDecode(encodedStart),
    problem: safeDecode(encodedProblem),
    eventId,
    source: safeDecode(encodedSource),
  }
}

export const buildDetailEndpoint = (detail: ParsedDetailRef): string =>
  [detail.kind, detail.startTime, detail.problem, detail.eventId, detail.source]
    .map((part) => encodeURIComponent(part))
    .join('/')

export const buildEvidenceEndpoint = (detail: ParsedDetailRef): string =>
  `events/evidence-bundle/${buildDetailEndpoint(detail)}`

export const buildStoryEndpoint = (detail: ParsedDetailRef): string =>
  `events/story/${buildDetailEndpoint(detail)}`

export const buildObservationEndpoint = (detail: ParsedDetailRef): string =>
  `events/observations/${buildDetailEndpoint(detail)}`

export const normalizeEventStory = (payload: unknown): EventStory => {
  if (!isRecord(payload)) throw new Error('事件叙事响应格式异常')
  if (payload.status === false) {
    throw new Error(cleanText(payload.msg) || '事件叙事暂不可用')
  }
  if (
    payload.schema_version !== 'event_detail_story_v1'
    || !isRecord(payload.event)
    || !isRecord(payload.observation)
    || !isRecord(payload.baseline)
    || !isRecord(payload.detection)
    || !isRecord(payload.impact)
    || !Array.isArray(payload.series)
    || !isRecord(payload.lifecycle)
    || !Array.isArray(payload.claims)
    || !Array.isArray(payload.unknowns)
    || !Array.isArray(payload.actions)
    || !isRecord(payload.evidence)
  ) {
    throw new Error('事件叙事响应缺少产品合同字段')
  }
  return payload as unknown as EventStory
}

export const normalizeEventObservation = (payload: unknown): EventObservation => {
  if (!isRecord(payload)) throw new Error('事件观测响应格式异常')
  if (payload.status === false) {
    throw new Error(cleanText(payload.msg) || '事件观测暂不可用')
  }
  if (payload.country_update_series === undefined) {
    payload.country_update_series = []
  }
  if (payload.country_update_metric_extrema === undefined) {
    payload.country_update_metric_extrema = {}
  }
  if (
    !['event_observation_v1', 'country_outage_observation_v2'].includes(
      cleanText(payload.schema_version),
    )
    || !isRecord(payload.event_identity)
    || !isRecord(payload.observation_scope)
    || !(payload.cohort === null || isRecord(payload.cohort))
    || !isRecord(payload.normal_band)
    || !(payload.rule_marker === null || isRecord(payload.rule_marker))
    || !Array.isArray(payload.metric_definitions)
    || !Array.isArray(payload.series)
    || !isRecord(payload.metric_extrema)
    || !Array.isArray(payload.resource_series)
    || !isRecord(payload.resource_metric_extrema)
    || !Array.isArray(payload.country_update_series)
    || !isRecord(payload.country_update_metric_extrema)
    || !Array.isArray(payload.annotations)
    || !isRecord(payload.asn_state)
    || !Array.isArray(payload.limitations)
    || !(payload.audit === null || isRecord(payload.audit))
  ) {
    throw new Error('事件观测响应缺少数据合同字段')
  }
  if (payload.schema_version === 'country_outage_observation_v2') {
    const capabilityStates = new Set([
      'available',
      'building',
      'unavailable',
      'not_applicable',
    ])
    if (
      !isRecord(payload.capabilities)
      || payload.capability_contract_version !== 'country_outage_capabilities_v1'
      || !Number.isInteger(payload.revision)
      || typeof payload.publication_id !== 'string'
      || payload.publication_id.length === 0
      || !isRecord(payload.processing_status)
      || ![
        'idle',
        'processing',
        'waiting_for_source',
        'failed',
        'final',
      ].includes(cleanText(payload.processing_status.state))
      || !Number.isInteger(payload.missing_slot_count)
      || ![
        'legacy_summary',
        'aggregate_available',
        'state_partial',
        'state_complete',
        'evidence_complete',
      ].includes(cleanText(payload.observation_state))
      || !['legacy', 'replay', 'live', 'mixed'].includes(cleanText(payload.data_mode))
      || !Object.values(payload.capabilities).every(
        (capability) => isRecord(capability)
          && capabilityStates.has(cleanText(capability.state)),
      )
    ) {
      throw new Error('国家中断观测 v2 能力或版本合同无效')
    }
  }
  if (
    'lifecycle' in payload
    || 'precursor' in payload
    || 'claims' in payload
    || 'actions' in payload
  ) {
    throw new Error('事件观测响应混入分析叙事字段')
  }
  return payload as unknown as EventObservation
}

const releaseMetadataKeys = [
  'revision',
  'publication_id',
  'publication_state',
  'observation_state',
  'data_mode',
  'data_through',
  'updated_at',
  'is_final',
  'processing_status',
  'missing_slot_count',
  'incident_id',
  'cohort_id',
  'window_start_utc',
  'window_end_utc',
  'capability_contract_version',
] as const

const assertMatchingCountryOutageRelease = (
  overview: Record<string, unknown>,
  companions: Record<string, unknown>[],
) => {
  for (const companion of companions) {
    for (const key of releaseMetadataKeys) {
      const overviewValue = overview[key]
      const companionValue = companion[key]
      const matches = (
        isRecord(overviewValue) && isRecord(companionValue)
          ? JSON.stringify(overviewValue) === JSON.stringify(companionValue)
          : overviewValue === companionValue
      )
      if (!matches) {
        throw new Error(`国家中断观测接口发布身份不一致：${key}`)
      }
    }
  }
}

export const normalizeCountryOutageObservation = (
  overview: unknown,
  series: unknown,
  asnPage: unknown,
  audit: unknown,
  trendProduct: unknown = null,
): EventObservation => {
  if (
    !isRecord(overview)
    || overview.schema_version !== 'country_outage_overview_v2'
    || !isRecord(series)
    || series.schema_version !== 'country_outage_series_v2'
  ) {
    throw new Error('国家中断观测 v2 响应格式异常')
  }
  const normalizedAsns = normalizeCountryOutageAsnPage(asnPage)
  const normalizedAudit = normalizeCountryOutageAudit(audit)
  const normalizedTrend = trendProduct === null
    ? null
    : normalizeCountryOutageTrendProduct(trendProduct)
  assertMatchingCountryOutageRelease(
    overview,
    [
      series,
      normalizedAsns as unknown as Record<string, unknown>,
      normalizedAudit as unknown as Record<string, unknown>,
    ],
  )
  if (normalizedTrend) {
    const trendSnapshot = normalizedTrend.snapshot as Record<string, unknown>
    for (const key of [
      'incident_id',
      'publication_id',
      'revision',
      'data_through',
      'window_start_utc',
      'window_end_utc',
    ] as const) {
      if (trendSnapshot[key] !== overview[key]) {
        throw new Error(`国家中断趋势制品发布身份不一致：${key}`)
      }
    }
  }
  return normalizeEventObservation({
    ...overview,
    ...series,
    schema_version: 'country_outage_observation_v2',
    asn_state: {
      state_codes: normalizedAsns.state_codes,
      observed_at_utc: normalizedAsns.observed_at_utc,
      observed_at_local: normalizedAsns.observed_at_local,
      timelines: normalizedAsns.items,
    },
    asn_page: normalizedAsns,
    audit: normalizedAudit,
    trend_product: normalizedTrend,
  })
}

export const normalizeCountryOutageTrendProduct = (
  payload: unknown,
): CountryOutageTrendProduct => {
  if (
    !isRecord(payload)
    || payload.schema_version !== 'country_outage_trend_product_v1'
    || !isRecord(payload.snapshot)
    || payload.snapshot.collector_id !== 'rrc25'
    || !isRecord(payload.profile)
    || !isRecord(payload.contexts)
    || !isRecord(payload.evidence_graph)
    || payload.evidence_graph.schema_version !== 'country_outage_evidence_graph_v1'
    || payload.evidence_graph.hypothesis_nodes_allowed !== false
    || payload.evidence_graph.causal_relations_allowed !== false
    || !Array.isArray(payload.evidence_graph.nodes)
    || !Array.isArray(payload.evidence_graph.edges)
    || !Array.isArray(payload.claim_ids)
    || !isRecord(payload.render_contract)
    || payload.render_contract.source_product_id !== payload.product_id
    || payload.graph_id !== payload.evidence_graph.graph_id
    || payload.profile_id !== payload.evidence_graph.profile_id
    || payload.analysis_id !== payload.evidence_graph.analysis_id
  ) {
    throw new Error('国家中断趋势制品 v1 响应格式异常')
  }
  const nodes = payload.evidence_graph.nodes
  const nodeIds = new Set<string>()
  const allowedNodeTypes = new Set(['Claim', 'Evidence', 'Limitation', 'Unknown'])
  for (const node of nodes) {
    if (
      !isRecord(node)
      || typeof node.node_id !== 'string'
      || !allowedNodeTypes.has(cleanText(node.node_type))
      || nodeIds.has(node.node_id)
    ) {
      throw new Error('国家中断趋势证据节点无效或重复')
    }
    nodeIds.add(node.node_id)
    if (
      node.node_type === 'Claim'
      && (
        !Array.isArray(node.evidence_refs)
        || node.evidence_refs.length === 0
        || !Array.isArray(node.limitation_refs)
        || node.limitation_refs.length === 0
        || !Array.isArray(node.unknown_refs)
        || node.unknown_refs.length === 0
      )
    ) {
      throw new Error('国家中断趋势 Claim 缺少 Evidence、Limitation 或 Unknown')
    }
  }
  const allowedRelations = new Set(['supported_by', 'limited_by', 'unknown_about'])
  for (const edge of payload.evidence_graph.edges) {
    if (
      !isRecord(edge)
      || !allowedRelations.has(cleanText(edge.relation))
      || !nodeIds.has(cleanText(edge.from))
      || !nodeIds.has(cleanText(edge.to))
    ) {
      throw new Error('国家中断趋势证据关系无效')
    }
  }
  return payload as unknown as CountryOutageTrendProduct
}

export const normalizeCountryOutageAsnPage = (
  payload: unknown,
): CountryOutageAsnPage => {
  if (
    !isRecord(payload)
    || payload.schema_version !== 'country_outage_asn_page_v2'
    || !releaseMetadataKeys.every((key) => key in payload)
    || !Array.isArray(payload.observed_at_utc)
    || !Array.isArray(payload.observed_at_local)
    || !isRecord(payload.state_codes)
    || !isRecord(payload.duration_histogram)
    || !Array.isArray(payload.items)
  ) {
    throw new Error('ASN 状态分页响应格式异常')
  }
  return payload as unknown as CountryOutageAsnPage
}

export const normalizeCountryOutageAudit = (
  payload: unknown,
): EventObservationAudit => {
  if (
    !isRecord(payload)
    || payload.schema_version !== 'country_outage_audit_v2'
    || !releaseMetadataKeys.every((key) => key in payload)
    || !isRecord(payload.verified_hashes)
    || !isRecord(payload.route_state_file)
    || !isRecord(payload.input_summary)
  ) {
    throw new Error('国家中断审计响应格式异常')
  }
  return payload as unknown as EventObservationAudit
}

const extractArray = (payload: unknown, context: string): unknown[] => {
  if (isRecord(payload) && payload.status === false) {
    throw new Error(cleanText(payload.msg) || `${context}查询失败`)
  }
  if (Array.isArray(payload)) return payload
  if (isRecord(payload) && Array.isArray(payload.data)) return payload.data
  throw new Error(`${context}响应格式异常`)
}

export const normalizeFeaturePoints = (payload: unknown): FeaturePoint[] =>
  extractArray(payload, '特征').flatMap((value) => {
    if (!isRecord(value)) return []
    const time = normalizeTime(value.t ?? value.time)
    if (!time) return []
    return [{
      time,
      announce: finiteNumber(value.announce),
      withdraw: finiteNumber(value.withdraw),
      ipv4Prefixes: finiteNumber(value.v4Prefix_num ?? value.ipv4_prefixes),
      ipv6Prefixes: finiteNumber(value.v6Prefix_num ?? value.ipv6_prefixes),
      ipv4Addresses: finiteNumber(value.v4IP_num ?? value.ipv4_addresses),
    }]
  })

export const normalizeOutagePoints = (payload: unknown): OutagePoint[] =>
  extractArray(payload, '中断时序').flatMap((value) => {
    if (!isRecord(value)) return []
    const time = normalizeTime(value.time_slot ?? value.time)
    const count = finiteNumber(value.outage_count ?? value.count)
    return time && count !== null ? [{ time, count }] : []
  })

export const normalizeCountPoints = (payload: unknown): CountPoint[] =>
  extractArray(payload, '事件统计').flatMap((value) => {
    if (!isRecord(value)) return []
    const time = normalizeTime(value.time)
    const count = finiteNumber(value.num)
    return time && count !== null ? [{ time, count }] : []
  })

const normalizeRanking = (value: unknown): DashboardRanking | null => {
  if (!isRecord(value)) return null
  const name = cleanText(value.name)
  if (!name) return null
  const asn = cleanText(value.asn)
  return {
    name,
    ...(asn ? { asn } : {}),
    eventCount: Math.max(0, finiteNumber(value.event_count) ?? 0),
    highRiskCount: Math.max(0, finiteNumber(value.high_risk_count) ?? 0),
  }
}

export const normalizeDashboardOverview = (payload: unknown): DashboardOverview => {
  if (!isRecord(payload)) throw new Error('首页聚合响应格式异常')
  if (payload.status === false) throw new Error(cleanText(payload.msg) || '首页聚合查询失败')
  const rawSeries = Array.isArray(payload.event_series) ? payload.event_series : []
  const eventSeries = rawSeries.flatMap((value) => {
    if (!isRecord(value)) return []
    const time = normalizeTime(value.time)
    if (!time) return []
    const rawCounts = isRecord(value.counts) ? value.counts : {}
    const counts = Object.fromEntries(CORE_EVENT_TYPES.map((eventType) => [
      eventType,
      Math.max(0, finiteNumber(rawCounts[eventType]) ?? 0),
    ])) as Record<(typeof CORE_EVENT_TYPES)[number], number>
    return [{ time, counts, total: Math.max(0, finiteNumber(value.total) ?? 0) }]
  })
  const rankings = (value: unknown) => (Array.isArray(value) ? value : [])
    .map(normalizeRanking)
    .filter((item): item is DashboardRanking => item !== null)

  return {
    startTime: normalizeTime(payload.start_time) ?? '',
    endTime: normalizeTime(payload.end_time) ?? '',
    timezone: cleanText(payload.timezone) || 'Asia/Shanghai',
    latestObservation: normalizeTime(payload.latest_observation),
    eventCount: Math.max(0, finiteNumber(payload.event_count) ?? 0),
    previousEventCount: Math.max(0, finiteNumber(payload.previous_event_count) ?? 0),
    eventChangeRate: finiteNumber(payload.event_change_rate),
    highRiskCount: Math.max(0, finiteNumber(payload.high_risk_count) ?? 0),
    activeEventCount: Math.max(0, finiteNumber(payload.active_event_count) ?? 0),
    affectedAsnCount: Math.max(0, finiteNumber(payload.affected_asn_count) ?? 0),
    affectedCountryCount: Math.max(0, finiteNumber(payload.affected_country_count) ?? 0),
    eventSeries,
    countryRankings: rankings(payload.country_rankings),
    asnRankings: rankings(payload.asn_rankings),
  }
}

const normalizeCountrySparkPoint = (value: unknown): CountrySparkPoint | null => {
  if (!isRecord(value)) return null
  const time = normalizeTime(value.time)
  if (!time) return null
  return {
    time,
    announce: Math.max(0, finiteNumber(value.announce) ?? 0),
    withdraw: Math.max(0, finiteNumber(value.withdraw) ?? 0),
  }
}

const normalizeCountryProfile = (value: unknown): CountryProfile | null => {
  if (!isRecord(value)) return null
  const country = cleanText(value.country)
  if (!country) return null
  const sparkline = (Array.isArray(value.sparkline) ? value.sparkline : [])
    .map(normalizeCountrySparkPoint)
    .filter((point): point is CountrySparkPoint => point !== null)
  return {
    country,
    announce: Math.max(0, finiteNumber(value.announce) ?? 0),
    withdraw: Math.max(0, finiteNumber(value.withdraw) ?? 0),
    updateTotal: Math.max(0, finiteNumber(value.update_total) ?? 0),
    withdrawRate: Math.max(0, finiteNumber(value.withdraw_rate) ?? 0),
    previousUpdateTotal: Math.max(0, finiteNumber(value.previous_update_total) ?? 0),
    updateChangeRate: finiteNumber(value.update_change_rate),
    sampleCount: Math.max(0, finiteNumber(value.sample_count) ?? 0),
    latestObservation: normalizeTime(value.latest_observation),
    ipv4Prefixes: finiteNumber(value.ipv4_prefixes),
    ipv6Prefixes: finiteNumber(value.ipv6_prefixes),
    ipv4Addresses: finiteNumber(value.ipv4_addresses),
    ipv4PrefixChange: finiteNumber(value.ipv4_prefix_change),
    ipv6PrefixChange: finiteNumber(value.ipv6_prefix_change),
    ipv4AddressChange: finiteNumber(value.ipv4_address_change),
    resourceChange: Math.max(0, finiteNumber(value.resource_change) ?? 0),
    resourceChangeRate: finiteNumber(value.resource_change_rate),
    peakUpdates: Math.max(0, finiteNumber(value.peak_updates) ?? 0),
    peakTime: normalizeTime(value.peak_time),
    anomalyCount: Math.max(0, finiteNumber(value.anomaly_count) ?? 0),
    highRiskCount: Math.max(0, finiteNumber(value.high_risk_count) ?? 0),
    sparkline,
    series: normalizeFeaturePoints(Array.isArray(value.series) ? value.series : []),
  }
}

export const normalizeCountryOverview = (payload: unknown): CountryOverview => {
  if (!isRecord(payload)) throw new Error('国家工作台响应格式异常')
  if (payload.status === false) throw new Error(cleanText(payload.msg) || '国家工作台查询失败')
  const rankings = (value: unknown) => (Array.isArray(value) ? value : [])
    .map(normalizeCountryProfile)
    .filter((profile): profile is CountryProfile => profile !== null)
  return {
    startTime: normalizeTime(payload.start_time) ?? '',
    endTime: normalizeTime(payload.end_time) ?? '',
    timezone: cleanText(payload.timezone) || 'Asia/Shanghai',
    latestObservation: normalizeTime(payload.latest_observation),
    countryCount: Math.max(0, finiteNumber(payload.country_count) ?? 0),
    countriesWithAnomalies: Math.max(0, finiteNumber(payload.countries_with_anomalies) ?? 0),
    updateLeader: normalizeCountryProfile(payload.update_leader),
    withdrawRateLeader: normalizeCountryProfile(payload.withdraw_rate_leader),
    resourceChangeLeader: normalizeCountryProfile(payload.resource_change_leader),
    updateRankings: rankings(payload.update_rankings),
    withdrawRateRankings: rankings(payload.withdraw_rate_rankings),
    resourceChangeRankings: rankings(payload.resource_change_rankings),
    anomalyRankings: rankings(payload.anomaly_rankings),
    selectedCountry: normalizeCountryProfile(payload.selected_country),
  }
}

const normalizeAsnProfile = (value: unknown): AsnProfile | null => {
  if (!isRecord(value)) return null
  const asn = cleanText(value.asn).replace(/^AS/i, '')
  if (!/^\d+$/.test(asn)) return null
  const sparkline = (Array.isArray(value.sparkline) ? value.sparkline : [])
    .map(normalizeCountrySparkPoint)
    .filter((point): point is CountrySparkPoint => point !== null)
  return {
    asn,
    asName: cleanText(value.as_name),
    orgName: cleanText(value.org_name),
    country: cleanText(value.country),
    asType: cleanText(value.as_type),
    globalRank: finiteNumber(value.global_rank),
    countryRank: finiteNumber(value.country_rank),
    important: value.important === true,
    announce: Math.max(0, finiteNumber(value.announce) ?? 0),
    withdraw: Math.max(0, finiteNumber(value.withdraw) ?? 0),
    updateTotal: Math.max(0, finiteNumber(value.update_total) ?? 0),
    withdrawRate: Math.max(0, finiteNumber(value.withdraw_rate) ?? 0),
    previousUpdateTotal: Math.max(0, finiteNumber(value.previous_update_total) ?? 0),
    updateChangeRate: finiteNumber(value.update_change_rate),
    sampleCount: Math.max(0, finiteNumber(value.sample_count) ?? 0),
    latestObservation: normalizeTime(value.latest_observation),
    ipv4Prefixes: finiteNumber(value.ipv4_prefixes),
    ipv6Prefixes: finiteNumber(value.ipv6_prefixes),
    ipv4Addresses: finiteNumber(value.ipv4_addresses),
    ipv4PrefixChange: finiteNumber(value.ipv4_prefix_change),
    ipv6PrefixChange: finiteNumber(value.ipv6_prefix_change),
    ipv4AddressChange: finiteNumber(value.ipv4_address_change),
    resourceChange: Math.max(0, finiteNumber(value.resource_change) ?? 0),
    resourceChangeRate: finiteNumber(value.resource_change_rate),
    peakUpdates: Math.max(0, finiteNumber(value.peak_updates) ?? 0),
    peakTime: normalizeTime(value.peak_time),
    volatility: Math.max(0, finiteNumber(value.volatility) ?? 0),
    anomalyCount: Math.max(0, finiteNumber(value.anomaly_count) ?? 0),
    highRiskCount: Math.max(0, finiteNumber(value.high_risk_count) ?? 0),
    sparkline,
    series: normalizeFeaturePoints(Array.isArray(value.series) ? value.series : []),
  }
}

export const normalizeAsOverview = (payload: unknown): AsOverview => {
  if (!isRecord(payload)) throw new Error('ASN 工作台响应格式异常')
  if (payload.status === false) throw new Error(cleanText(payload.msg) || 'ASN 工作台查询失败')
  const rankings = (value: unknown) => (Array.isArray(value) ? value : [])
    .map(normalizeAsnProfile)
    .filter((profile): profile is AsnProfile => profile !== null)
  return {
    startTime: normalizeTime(payload.start_time) ?? '',
    endTime: normalizeTime(payload.end_time) ?? '',
    timezone: cleanText(payload.timezone) || 'Asia/Shanghai',
    latestObservation: normalizeTime(payload.latest_observation),
    scopeKind: cleanText(payload.scope_kind),
    scopeNote: cleanText(payload.scope_note),
    candidatePoolSize: Math.max(0, finiteNumber(payload.candidate_pool_size) ?? 0),
    scopeSize: Math.max(0, finiteNumber(payload.scope_size) ?? 0),
    featureAsnCount: Math.max(0, finiteNumber(payload.feature_asn_count) ?? 0),
    importantAsnCount: Math.max(0, finiteNumber(payload.important_asn_count) ?? 0),
    asnsWithAnomalies: Math.max(0, finiteNumber(payload.asns_with_anomalies) ?? 0),
    updateLeader: normalizeAsnProfile(payload.update_leader),
    withdrawRateLeader: normalizeAsnProfile(payload.withdraw_rate_leader),
    resourceChangeLeader: normalizeAsnProfile(payload.resource_change_leader),
    volatilityLeader: normalizeAsnProfile(payload.volatility_leader),
    updateRankings: rankings(payload.update_rankings),
    withdrawRateRankings: rankings(payload.withdraw_rate_rankings),
    resourceChangeRankings: rankings(payload.resource_change_rankings),
    volatilityRankings: rankings(payload.volatility_rankings),
    anomalyRankings: rankings(payload.anomaly_rankings),
    selectedAsn: normalizeAsnProfile(payload.selected_asn),
  }
}

const textArray = (value: unknown): string[] => (Array.isArray(value) ? value : [])
  .map(cleanText)
  .filter(Boolean)

const evidencePhases = new Set<EvidencePhase>(['before', 'during', 'after', 'context'])
const evidenceKinds = new Set<EvidenceKind>(['fact_record', 'route_observation', 'affected_object_set'])
const phaseStatuses = new Set<EvidencePhaseStatus>(['not_available', 'observed_no_path', 'observed_paths'])
const lifecycleStates = new Set<LegacyEventSemanticGuardrails['lifecycleState']>([
  'recorded',
  'unknown',
  'unavailable',
])
const attributionStates = new Set<LegacyEventSemanticGuardrails['attributionState']>([
  'detector_fact_only',
  'legacy_biased',
])
const ratioStates = new Set<LegacyEventSemanticGuardrails['ratioState']>([
  'not_applicable',
  'recompute_required',
])

const normalizePhaseCoverage = (value: unknown): EvidencePhaseCoverage => {
  const record = isRecord(value) ? value : {}
  const candidate = cleanText(record.status) as EvidencePhaseStatus
  return {
    status: phaseStatuses.has(candidate) ? candidate : 'not_available',
    snapshotCount: Math.max(0, finiteNumber(record.snapshot_count) ?? 0),
    pathCount: Math.max(0, finiteNumber(record.path_count) ?? 0),
    evidenceIds: textArray(record.evidence_ids),
  }
}

const normalizeSemanticGuardrails = (value: unknown): LegacyEventSemanticGuardrails => {
  if (!isRecord(value) || value.contract_version !== 'legacy_event_semantic_guardrails_v1') {
    throw new Error('Evidence Bundle 缺少遗留语义约束')
  }
  const lifecycleState = cleanText(value.lifecycle_state) as LegacyEventSemanticGuardrails['lifecycleState']
  const attributionState = cleanText(value.attribution_state) as LegacyEventSemanticGuardrails['attributionState']
  const ratioState = cleanText(value.ratio_state) as LegacyEventSemanticGuardrails['ratioState']
  if (
    !lifecycleStates.has(lifecycleState)
    || !attributionStates.has(attributionState)
    || !ratioStates.has(ratioState)
  ) {
    throw new Error('Evidence Bundle 遗留语义约束无效')
  }
  return {
    contractVersion: 'legacy_event_semantic_guardrails_v1',
    lifecycleState,
    attributionState,
    ratioState,
    blockedClaims: textArray(value.blocked_claims),
    reasonCodes: textArray(value.reason_codes),
  }
}

const normalizeEvidenceItem = (value: unknown): EvidenceItem | null => {
  if (!isRecord(value)) return null
  const evidenceId = cleanText(value.evidence_id)
  const phase = cleanText(value.phase) as EvidencePhase
  const kind = cleanText(value.kind) as EvidenceKind
  if (!/^ev_v1_[0-9a-f]{24}$/.test(evidenceId) || !evidencePhases.has(phase) || !evidenceKinds.has(kind)) {
    return null
  }
  return {
    evidenceId,
    phase,
    kind,
    label: cleanText(value.label),
    sourceField: cleanText(value.source_field),
    semantics: cleanText(value.semantics),
    observedAtLocal: cleanText(value.observed_at_local) || null,
    observedAtUtc: cleanText(value.observed_at_utc) || null,
    observationState: cleanText(value.observation_state),
    pathCount: Math.max(0, finiteNumber(value.path_count) ?? 0),
    paths: textArray(value.paths),
    objectCount: Math.max(0, finiteNumber(value.object_count) ?? 0),
    objects: textArray(value.objects),
  }
}

export const normalizeEvidenceBundle = (payload: unknown): EvidenceBundle => {
  if (!isRecord(payload)) throw new Error('Evidence Bundle 响应格式异常')
  if (payload.status === false) throw new Error(cleanText(payload.msg) || 'Evidence Bundle 查询失败')
  if (
    payload.bundle_version !== 'evidence_bundle_v1'
    || payload.incident_id_schema !== 'incident_id_v1'
    || !/^inc_v1_[0-9a-f]{24}$/.test(cleanText(payload.incident_id))
  ) {
    throw new Error('Evidence Bundle 版本或事件标识无效')
  }
  if (!isRecord(payload.event) || !isRecord(payload.phase_coverage)) {
    throw new Error('Evidence Bundle 缺少事件或阶段覆盖信息')
  }
  const eventKind = cleanText(payload.event.kind) as EventKind
  if (!(eventKind in EVENT_KIND_LABELS)) throw new Error('Evidence Bundle 事件类型无效')
  const dataSnapshot = isRecord(payload.data_snapshot) ? payload.data_snapshot : {}
  const sourceRecord = isRecord(payload.source_record) ? payload.source_record : {}
  const assessment = isRecord(payload.assessment) ? payload.assessment : {}
  const dataQuality = isRecord(payload.data_quality) ? payload.data_quality : {}
  const recordLocator = isRecord(sourceRecord.record_locator) ? sourceRecord.record_locator : {}
  const factRecord = isRecord(payload.fact_record) ? payload.fact_record : {}
  const semanticGuardrails = normalizeSemanticGuardrails(payload.semantic_guardrails)
  const evidenceItems = (Array.isArray(payload.evidence_items) ? payload.evidence_items : [])
    .map(normalizeEvidenceItem)
    .filter((item): item is EvidenceItem => item !== null)

  return {
    bundleVersion: 'evidence_bundle_v1',
    incidentId: cleanText(payload.incident_id),
    incidentIdSchema: 'incident_id_v1',
    semanticGuardrails,
    event: {
      kind: eventKind,
      label: cleanText(payload.event.label) || EVENT_KIND_LABELS[eventKind],
      object: cleanText(payload.event.object),
      level: cleanText(payload.event.level),
      summary: cleanText(payload.event.summary),
      duration: cleanText(payload.event.duration),
      eventTimeLocal: cleanText(payload.event.event_time_local) || null,
      eventTimeUtc: cleanText(payload.event.event_time_utc) || null,
      endTimeLocal: cleanText(payload.event.end_time_local) || null,
      endTimeUtc: cleanText(payload.event.end_time_utc) || null,
      sourceTimezone: cleanText(payload.event.source_timezone) || 'Asia/Shanghai',
    },
    dataSnapshot: {
      snapshotTimeLocal: cleanText(dataSnapshot.snapshot_time_local) || null,
      snapshotTimeUtc: cleanText(dataSnapshot.snapshot_time_utc) || null,
      timezone: cleanText(dataSnapshot.timezone) || 'Asia/Shanghai',
    },
    sourceRecord: {
      sourceSystem: cleanText(sourceRecord.source_system),
      sourceTable: cleanText(sourceRecord.source_table),
      sourceCode: cleanText(sourceRecord.source_code),
      detailReference: cleanText(sourceRecord.detail_reference),
      recordLocator,
    },
    phaseCoverage: {
      before: normalizePhaseCoverage(payload.phase_coverage.before),
      during: normalizePhaseCoverage(payload.phase_coverage.during),
      after: normalizePhaseCoverage(payload.phase_coverage.after),
    },
    evidenceItems,
    assessment: {
      classification: 'observation_only',
      supports: textArray(assessment.supports),
      counterevidence: textArray(assessment.counterevidence),
      gaps: textArray(assessment.gaps),
      causalConclusion: null,
    },
    dataQuality: {
      observedPhaseCount: Math.max(0, finiteNumber(dataQuality.observed_phase_count) ?? 0),
      expectedPhaseCount: Math.max(0, finiteNumber(dataQuality.expected_phase_count) ?? 3),
      routeObservationCount: Math.max(0, finiteNumber(dataQuality.route_observation_count) ?? 0),
      evidenceItemCount: Math.max(0, finiteNumber(dataQuality.evidence_item_count) ?? evidenceItems.length),
      vantagePointIdentityAvailable: dataQuality.vantage_point_identity_available === true,
      rawBgpMessageAvailable: dataQuality.raw_bgp_message_available === true,
      timezoneSemantics: cleanText(dataQuality.timezone_semantics),
      limitations: textArray(dataQuality.limitations),
    },
    factRecord,
  }
}

export const errorMessage = (error: unknown): string => {
  if (error instanceof Error && error.message) return error.message
  return '暂时无法获取数据'
}
