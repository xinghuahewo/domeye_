export const CORE_EVENT_TYPES = [
  '前缀劫持',
  '子前缀劫持',
  '前缀中断',
  'AS中断',
  '国家中断',
  '路由泄漏',
] as const

export type EventLabel = (typeof CORE_EVENT_TYPES)[number]
export type EventLevel = 'high' | 'middle' | 'low' | 'unknown'

export const EVENT_KIND_LABELS = {
  hijack: '前缀劫持',
  sub_hijack: '子前缀劫持',
  prefix_outage: '前缀中断',
  as_outage: 'AS中断',
  country_outage: '国家中断',
  leak: '路由泄漏',
} as const

export type EventKind = keyof typeof EVENT_KIND_LABELS

export interface EventRow {
  key: string
  type: string
  level: EventLevel
  startTime: string | null
  endTime: string | null
  attackerAs: string
  attackedAs: string
  attackerOrg: string
  attackedOrg: string
  attackerCountry: string
  attackedCountry: string
  affectedPrefix: string
  summary: string
  detailUrl: string
}

export interface EventPage {
  data: EventRow[]
  totalPage: number
  recordCount: number
}

export interface ParsedDetailRef {
  raw: string
  kind: EventKind
  startTime: string
  problem: string
  eventId: string
  source: string
}

export type EvidencePhase = 'before' | 'during' | 'after' | 'context'
export type EvidencePhaseStatus = 'not_available' | 'observed_no_path' | 'observed_paths'
export type EvidenceKind = 'fact_record' | 'route_observation' | 'affected_object_set'

export interface EvidencePhaseCoverage {
  status: EvidencePhaseStatus
  snapshotCount: number
  pathCount: number
  evidenceIds: string[]
}

export interface EvidenceItem {
  evidenceId: string
  phase: EvidencePhase
  kind: EvidenceKind
  label: string
  sourceField: string
  semantics: string
  observedAtLocal: string | null
  observedAtUtc: string | null
  observationState: string
  pathCount: number
  paths: string[]
  objectCount: number
  objects: string[]
}

export interface EvidenceEvent {
  kind: EventKind
  label: string
  object: string
  level: string
  summary: string
  duration: string
  eventTimeLocal: string | null
  eventTimeUtc: string | null
  endTimeLocal: string | null
  endTimeUtc: string | null
  sourceTimezone: string
}

export interface EvidenceBundle {
  bundleVersion: 'evidence_bundle_v1'
  incidentId: string
  incidentIdSchema: 'incident_id_v1'
  event: EvidenceEvent
  dataSnapshot: {
    snapshotTimeLocal: string | null
    snapshotTimeUtc: string | null
    timezone: string
  }
  sourceRecord: {
    sourceSystem: string
    sourceTable: string
    sourceCode: string
    detailReference: string
    recordLocator: Record<string, unknown>
  }
  phaseCoverage: Record<'before' | 'during' | 'after', EvidencePhaseCoverage>
  evidenceItems: EvidenceItem[]
  assessment: {
    classification: 'observation_only'
    supports: string[]
    counterevidence: string[]
    gaps: string[]
    causalConclusion: null
  }
  dataQuality: {
    observedPhaseCount: number
    expectedPhaseCount: number
    routeObservationCount: number
    evidenceItemCount: number
    vantagePointIdentityAvailable: boolean
    rawBgpMessageAvailable: boolean
    timezoneSemantics: string
    limitations: string[]
  }
  factRecord: Record<string, unknown>
}

export interface FeaturePoint {
  time: string
  announce: number | null
  withdraw: number | null
  ipv4Prefixes: number | null
  ipv6Prefixes: number | null
  ipv4Addresses: number | null
}

export interface OutagePoint {
  time: string
  count: number
}

export interface CountPoint {
  time: string
  count: number
}

export interface EventTrendPoint {
  time: string
  counts: Record<EventLabel, number>
  total: number
}

export interface DashboardRanking {
  name: string
  asn?: string
  eventCount: number
  highRiskCount: number
}

export interface DashboardOverview {
  startTime: string
  endTime: string
  timezone: string
  latestObservation: string | null
  eventCount: number
  previousEventCount: number
  eventChangeRate: number | null
  highRiskCount: number
  activeEventCount: number
  affectedAsnCount: number
  affectedCountryCount: number
  eventSeries: EventTrendPoint[]
  countryRankings: DashboardRanking[]
  asnRankings: DashboardRanking[]
}

export interface CountrySparkPoint {
  time: string
  announce: number
  withdraw: number
}

export interface CountryProfile {
  country: string
  announce: number
  withdraw: number
  updateTotal: number
  withdrawRate: number
  previousUpdateTotal: number
  updateChangeRate: number | null
  sampleCount: number
  latestObservation: string | null
  ipv4Prefixes: number | null
  ipv6Prefixes: number | null
  ipv4Addresses: number | null
  ipv4PrefixChange: number | null
  ipv6PrefixChange: number | null
  ipv4AddressChange: number | null
  resourceChange: number
  resourceChangeRate: number | null
  peakUpdates: number
  peakTime: string | null
  anomalyCount: number
  highRiskCount: number
  sparkline: CountrySparkPoint[]
  series: FeaturePoint[]
}

export interface CountryOverview {
  startTime: string
  endTime: string
  timezone: string
  latestObservation: string | null
  countryCount: number
  countriesWithAnomalies: number
  updateLeader: CountryProfile | null
  withdrawRateLeader: CountryProfile | null
  resourceChangeLeader: CountryProfile | null
  updateRankings: CountryProfile[]
  withdrawRateRankings: CountryProfile[]
  resourceChangeRankings: CountryProfile[]
  anomalyRankings: CountryProfile[]
  selectedCountry: CountryProfile | null
}

export interface AsnProfile {
  asn: string
  asName: string
  orgName: string
  country: string
  asType: string
  globalRank: number | null
  countryRank: number | null
  important: boolean
  announce: number
  withdraw: number
  updateTotal: number
  withdrawRate: number
  previousUpdateTotal: number
  updateChangeRate: number | null
  sampleCount: number
  latestObservation: string | null
  ipv4Prefixes: number | null
  ipv6Prefixes: number | null
  ipv4Addresses: number | null
  ipv4PrefixChange: number | null
  ipv6PrefixChange: number | null
  ipv4AddressChange: number | null
  resourceChange: number
  resourceChangeRate: number | null
  peakUpdates: number
  peakTime: string | null
  volatility: number
  anomalyCount: number
  highRiskCount: number
  sparkline: CountrySparkPoint[]
  series: FeaturePoint[]
}

export interface AsOverview {
  startTime: string
  endTime: string
  timezone: string
  latestObservation: string | null
  scopeKind: string
  scopeNote: string
  candidatePoolSize: number
  scopeSize: number
  featureAsnCount: number
  importantAsnCount: number
  asnsWithAnomalies: number
  updateLeader: AsnProfile | null
  withdrawRateLeader: AsnProfile | null
  resourceChangeLeader: AsnProfile | null
  volatilityLeader: AsnProfile | null
  updateRankings: AsnProfile[]
  withdrawRateRankings: AsnProfile[]
  resourceChangeRankings: AsnProfile[]
  volatilityRankings: AsnProfile[]
  anomalyRankings: AsnProfile[]
  selectedAsn: AsnProfile | null
}

export type HealthPayload = components['schemas']['HealthPayload']

export interface EventQuery {
  page_num?: number
  page_size?: number
  event_type?: string
  level?: string
  country?: string
  attacker_as?: string
  attacked_as?: string
  attacker_org?: string
  attacked_org?: string
  attacker_country?: string
  attacked_country?: string
  event_info?: string
  date?: string
  sort_mode?: string
}
import type { components } from './openapi.generated'
