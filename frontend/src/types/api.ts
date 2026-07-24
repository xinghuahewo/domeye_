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

export type StoryClaimLevel = 'fact' | 'derived' | 'inference' | 'unknown'

export interface EventStorySnapshot {
  snapshot_id: string
  observed_at_utc: string
  observed_at_local: string
  affected_asn_count: number
  affected_asn_ratio: number
  fully_invisible_asn_count: number
  partially_visible_asn_count: number
  visible_origin_asn_count: number
  visible_origin_asn_ratio: number
  visible_prefix_vp_count: number
  visible_prefix_vp_ratio: number
  ipv4_visible_prefix_vp_count: number
  ipv4_baseline_prefix_vp_count: number
  ipv4_visible_prefix_vp_ratio: number
  ipv4_visible_origin_asn_count: number
  ipv4_baseline_origin_asn_count: number
  ipv6_visible_prefix_vp_count: number
  ipv6_baseline_prefix_vp_count: number
  ipv6_visible_prefix_vp_ratio: number
  ipv6_visible_origin_asn_count: number
  ipv6_baseline_origin_asn_count: number
  announce_count: number
  withdraw_count: number
}

export interface EventStoryAffectedAsn {
  asn: string
  affected_slot_count: number
  fully_invisible_slot_count: number
  partially_visible_slot_count: number
  first_affected_at: string
  last_affected_at: string
  first_affected_at_local: string
  last_affected_at_local: string
  end_classification: 'fully_visible' | 'partially_visible' | 'fully_invisible' | 'unknown'
  baseline_prefix_vp_count: number
  baseline_prefix_count: number
  address_families: number[]
}

export interface EventStoryClaim {
  claim_id: string
  level: StoryClaimLevel
  confidence: string
  title: string
  statement: string
  scope: string
  evidence_refs: string[]
}

export interface EventStoryUnknown {
  question: string
  reason: string
  evidence_needed: string
  next_action: string
}

export interface EventStory {
  schema_version: 'event_detail_story_v1'
  contract_scope: {
    acceptance_event: boolean
    event_types_covered: string[]
    collector_scope: string[]
    control_plane_only: boolean
    causal_analysis_performed: boolean
  }
  event: {
    incident_id: string
    legacy_reference: string
    legacy_record_time_local: string | null
    kind: string
    label: string
    country_code: string
    country_name: string
    severity: string
    status: string
    status_label: string
    headline: string
    scope_statement: string
    service_impact_statement: string
  }
  observation: {
    collector_id: string
    collector_count: number
    vantage_point_count: number
    vantage_point_count_semantics: string
    window_start_utc: string
    window_start_local: string
    window_end_utc: string
    window_end_local: string
    timezone: string
    observation_count: number
    interval_seconds: number
    left_censored: boolean
    right_censored: boolean
    coverage_state: string
    coverage_statement: string
    cohort: {
      cohort_id: string
      seed_observed_at_utc: string
      seed_observed_at_local: string
      baseline_origin_asn_count: number
      baseline_prefix_vp_count: number
      mapping_version: string
      denominator_policy: string
    }
    data_freshness: {
      last_observation_at_utc: string
      last_observation_at_local: string
      replay_completed_at_utc: string
      replay_completed_at_local: string
      quality_status: string
    }
  }
  baseline: {
    state: string
    label: string
    reason: string
    known_population: {
      origin_asn_count: number
      prefix_vp_count: number
    }
    consequence: string
  }
  detection: {
    rule: {
      metric: string
      threshold: number
      confirm_observation_count: number
      confirm_duration_seconds: number
      statement: string
    }
    onset: {
      at_utc: string
      at_local: string
      precision: string
      statement: string
    }
    detected: {
      at_utc: string
      at_local: string
      snapshot_id: string
    }
    legacy_record: {
      at_local: string | null
      semantics: string
      not_event_onset: boolean
    }
  }
  impact: {
    peak: EventStorySnapshot
    trough: EventStorySnapshot
    window_start: EventStorySnapshot
    window_end: EventStorySnapshot
    peak_statement: string
    trough_statement: string
    end_statement: string
    persistent_asns: EventStoryAffectedAsn[]
    ranking_semantics: string
  }
  series: EventStorySnapshot[]
  lifecycle: {
    episode_count: number
    wave_count: number
    wave_causal_relation: string
    current_state: string
    current_state_label: string
    duration_state: string
    onset_at_local: string
    detected_at_local: string
    peak_at_local: string
    trough_at_local: string
    partial_recovery_at_local: string | null
    full_recovery_at_local: string | null
    observation_end_at_local: string
    recovery_rule: string
    rebound_statement: string
  }
  precursor: {
    candidate_time_local: string | null
    relation: string
    causal_relation: string
    statement: string
  }
  comparisons: Array<{
    source: string
    value: string
    status: string
    explanation: string
  }>
  claims: EventStoryClaim[]
  unknowns: EventStoryUnknown[]
  actions: Array<{
    priority: number
    label: string
    reason: string
  }>
  evidence: {
    engine_version: string
    package_directory: string
    quality_status: string
    consumed_deliverable_hashes_verified: boolean
    verified_hashes: Record<string, string>
    route_state_file: {
      filename: string
      recorded_sha256: string
      row_count: number
      request_path_hash_reverified: boolean
      statement: string
    }
    input_summary: {
      rib_count: number
      catch_up_update_count: number
      formal_update_count: number
      input_compressed_bytes: number
      rib_physical_records: number
      rib_entries: number
      update_physical_records: number
      update_route_events: number
    }
  }
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
