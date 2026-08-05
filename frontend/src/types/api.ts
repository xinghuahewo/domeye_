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

export interface LegacyEventSemanticGuardrails {
  contractVersion: 'legacy_event_semantic_guardrails_v1'
  lifecycleState: 'recorded' | 'unknown' | 'unavailable'
  attributionState: 'detector_fact_only' | 'legacy_biased'
  ratioState: 'not_applicable' | 'recompute_required'
  blockedClaims: string[]
  reasonCodes: string[]
}

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
  semanticGuardrails: LegacyEventSemanticGuardrails
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

export interface EventObservationExtreme {
  metric: string
  observed_at_utc: string
  observed_at_local: string
  value: number
}

export interface EventObservationSeriesPoint {
  snapshot_id: string
  observed_at_utc: string
  observed_at_local: string
  slot_state:
    | 'observed'
    | 'source_unavailable'
    | 'processing_gap'
    | 'parse_failed'
    | 'not_observed'
  missing_reason: string | null
  visible_prefix_vp_count: number | null
  invisible_prefix_vp_count: number | null
  visible_prefix_vp_ratio: number | null
  visible_prefix_vp_delta: number | null
  visible_prefix_vp_ratio_delta_pp: number | null
  visible_origin_asn_count: number | null
  visible_origin_asn_ratio: number | null
  visible_origin_asn_delta: number | null
  fully_visible_asn_count: number | null
  partially_visible_asn_count: number | null
  fully_invisible_asn_count: number | null
  non_fully_visible_asn_count: number | null
  ipv4_visible_prefix_vp_count: number | null
  ipv4_baseline_prefix_vp_count: number | null
  ipv4_visible_prefix_vp_ratio: number | null
  ipv4_visible_prefix_vp_delta: number | null
  ipv6_visible_prefix_vp_count: number | null
  ipv6_baseline_prefix_vp_count: number | null
  ipv6_visible_prefix_vp_ratio: number | null
  ipv6_visible_prefix_vp_delta: number | null
  announce_count: number | null
  withdraw_count: number | null
  update_total: number | null
  withdraw_ratio: number | null
  announce_delta: number | null
  withdraw_delta: number | null
  country_announce_count: number | null
  country_withdraw_count: number | null
  country_update_total: number | null
  country_withdraw_ratio: number | null
  country_announce_delta: number | null
  country_withdraw_delta: number | null
}

export interface EventObservationUpdatePoint {
  observed_at_utc: string
  observed_at_local: string
  announce_count: number | null
  withdraw_count: number | null
  update_total: number | null
  withdraw_ratio: number | null
  announce_delta: number | null
  withdraw_delta: number | null
}

export interface EventObservationResourcePoint {
  observed_at_utc: string
  observed_at_local: string
  ipv4_24_equivalent_count: number | null
  ipv6_48_equivalent_count: number | null
  ipv4_address_count: number | null
  announce_count: number | null
  withdraw_count: number | null
  update_total: number | null
  withdraw_ratio: number | null
  ipv4_24_equivalent_delta: number | null
  ipv6_48_equivalent_delta: number | null
  ipv4_address_delta: number | null
  announce_delta: number | null
  withdraw_delta: number | null
}

export interface EventObservationAsnTimeline {
  asn: string
  address_families: number[]
  baseline_prefix_count: number
  baseline_prefix_vp_count: number
  states: number[]
  state_slot_counts: {
    fully_visible: number
    partially_visible: number
    fully_invisible: number
    unknown: number
  }
  longest_fully_visible_slots: number
  longest_partially_visible_slots: number
  longest_fully_invisible_slots: number
}

export interface EventObservationCapability {
  state: 'available' | 'building' | 'unavailable' | 'not_applicable'
  reason?: string
}

export type CountryOutageObservationState =
  | 'legacy_summary'
  | 'aggregate_available'
  | 'state_partial'
  | 'state_complete'
  | 'evidence_complete'

export type CountryOutageDataMode = 'legacy' | 'replay' | 'live' | 'mixed'

export interface CountryOutageProcessingStatus {
  state: 'idle' | 'processing' | 'waiting_for_source' | 'failed' | 'final'
  updated_at: string | null
  attempted_through: string | null
  reason: string | null
  last_complete_data_through: string | null
}

export interface CountryOutageMissingSlot {
  observed_at: string
  slot_state:
    | 'source_unavailable'
    | 'processing_gap'
    | 'parse_failed'
    | 'not_observed'
  missing_reason: string
}

export interface CountryOutageReleaseMetadata {
  revision: number
  publication_id: string
  publication_state: string
  observation_state: CountryOutageObservationState
  data_mode: CountryOutageDataMode
  data_through: string | null
  updated_at: string | null
  is_final: boolean
  processing_status: CountryOutageProcessingStatus
  missing_slot_count: number
  incident_id: string
  cohort_id: string | null
  window_start_utc: string | null
  window_end_utc: string | null
  capability_contract_version: 'country_outage_capabilities_v1'
}

export interface EventObservationAudit extends CountryOutageReleaseMetadata {
  schema_version: 'country_outage_audit_v2'
  run_id?: string | null
  artifact_set_id?: string | null
  engine_version: string
  algorithm_version: string | null
  mapping_version: string | null
  quality_status: string
  source_system: string
  source_table: string
  source_reference: string
  evidence_level: string
  consumed_deliverable_hashes_verified: boolean
  verified_hashes: Record<string, string>
  route_state_file: {
    filename: string | null
    recorded_sha256: string | null
    row_count: number | null
    request_path_scanned: boolean
  }
  input_summary: {
    rib_count: number | null
    catch_up_update_count: number | null
    formal_update_count: number | null
    input_compressed_bytes: number | null
    rib_physical_records: number | null
    rib_entries: number | null
    update_physical_records: number | null
    update_route_events: number | null
  }
  revision_history?: Array<Record<string, unknown>>
  supersedes_publication_id?: string | null
  correction_reason?: string | null
  missing_slots?: CountryOutageMissingSlot[]
}

export interface CountryOutageAsnPage extends CountryOutageReleaseMetadata {
  schema_version: 'country_outage_asn_page_v2'
  page: number
  page_size: number
  page_count: number
  total: number
  observed_at_utc: string[]
  observed_at_local: string[]
  state_codes: Record<string, string>
  duration_histogram: Record<
    'fully_visible' | 'partially_visible' | 'fully_invisible',
    Record<string, number>
  >
  items: EventObservationAsnTimeline[]
}

export type CountryOutageEvidenceNodeType =
  | 'Claim'
  | 'Evidence'
  | 'Limitation'
  | 'Unknown'

export interface CountryOutageEvidenceNode {
  node_id: string
  node_type: CountryOutageEvidenceNodeType
  claim_kind?: string
  text?: string
  values?: Record<string, unknown>
  evidence_refs?: string[]
  limitation_refs?: string[]
  unknown_refs?: string[]
  conclusion_level?: 'rrc25_control_plane_observation'
  evidence_kind?: string
  label?: string
  snapshot_ref?: {
    incident_id: string
    publication_id: string
    revision: number
    data_through: string
  }
  payload?: Record<string, unknown>
  source_refs?: string[]
  code?: string
}

export interface CountryOutageEvidenceGraph {
  schema_version: 'country_outage_evidence_graph_v1'
  algorithm_version: string
  graph_id: string
  profile_id: string
  analysis_id: string
  snapshot: Record<string, unknown>
  nodes: CountryOutageEvidenceNode[]
  edges: Array<{
    from: string
    relation: 'supported_by' | 'limited_by' | 'unknown_about'
    to: string
  }>
  node_types: CountryOutageEvidenceNodeType[]
  relation_types: Array<'supported_by' | 'limited_by' | 'unknown_about'>
  hypothesis_nodes_allowed: false
  causal_relations_allowed: false
}

export interface CountryOutageContemporaneousReference {
  schema_version: 'country_outage_contemporaneous_reference_v1'
  context_id: string
  status: 'complete' | 'insufficient_data'
  target_country_code: string
  projection_bucket_count: number
  comparable_country_count: number
  excluded_projection_count: number
  exclusion_reason_counts: Record<string, number>
  normalization: Record<string, unknown>
  target: {
    maximum_decline_percentage_points: number
    persistence_below_95_slot_count: number
    curve_shape: string
    curve_shape_label_zh: string
    asn_migration_ratio: number | null
    [key: string]: unknown
  } | null
  distribution_positions: {
    maximum_decline_percentage_points: {
      target_value: number
      empirical_percentile: number
      comparable_count: number
    }
    persistence_below_95_slot_count: {
      target_value: number
      empirical_percentile: number
      comparable_count: number
    }
    asn_migration_ratio: {
      target_value: number | null
      empirical_percentile: number | null
      comparable_count: number
      status: 'available' | 'insufficient_data'
    }
  } | null
  curve_shape_distribution: Array<{
    curve_shape: string
    curve_shape_label_zh: string
    country_count: number
    country_share: number
    is_target_shape: boolean
  }>
  common_fluctuation: {
    target_largest_drop_slot: {
      slot_index: number
      observed_at_utc: string
      declining_country_count: number
      comparable_country_count: number
      declining_country_share: number
      target_declined: boolean
    } | null
    interpretation: 'same_slot_rrc25_observation_only'
    collector_failure_claim: false
  } | null
  limitations: string[]
}

export interface CountryOutageTrendProduct {
  schema_version: 'country_outage_trend_product_v1'
  algorithm_version: string
  product_id: string
  profile_id: string
  analysis_id: string
  graph_id: string
  snapshot: {
    incident_id: string
    publication_id: string
    revision: number
    data_through: string
    collector_id: 'rrc25'
    window_start_utc: string
    window_end_utc: string
    [key: string]: unknown
  }
  profile: {
    quality: {
      status: string
      observed_slot_count: number
      expected_slot_count: number
      non_observed_slot_count: number
      window_start_observed: boolean
      window_end_observed: boolean
    }
    metric: {
      label: string
      unit: string
      statistical_population: string
      denominator: { value: number; unit: string; statistical_population: string }
    }
    baseline: { type: string; interpretation: string }
    time_grid: { slot_seconds: number; expected_slot_count: number }
    analysis: {
      status: string
      pattern: { status: string; label: string | null }
      key_points: Array<Record<string, unknown>>
      phases: Array<Record<string, unknown>>
      derived_facts: Array<Record<string, unknown>>
      window_ledger: Record<string, unknown>
    }
    [key: string]: unknown
  }
  contexts: {
    address_family: Record<string, unknown> | null
    asn: Record<string, unknown> | null
    activity: Record<string, unknown> | null
    contemporaneous_reference: CountryOutageContemporaneousReference | null
  }
  evidence_graph: CountryOutageEvidenceGraph
  reading_journey: string[]
  claim_ids: string[]
  qa_rule_version: string
  render_contract: {
    source_product_id: string
    surfaces: Array<'page' | 'report' | 'qa' | 'markdown' | 'pdf' | 'json_download'>
    model_may_rewrite_deterministic_values: false
  }
  event_identity: Record<string, unknown>
  observation_scope: Record<string, unknown>
  capabilities: Record<string, EventObservationCapability>
}

export interface EventObservation {
  schema_version: 'event_observation_v1' | 'country_outage_observation_v2'
  revision?: number
  publication_id?: string
  publication_state?: string
  observation_state?: CountryOutageObservationState
  data_mode?: CountryOutageDataMode
  data_through?: string | null
  updated_at?: string | null
  is_final?: boolean
  processing_status?: CountryOutageProcessingStatus
  missing_slot_count?: number
  incident_id?: string
  cohort_id?: string | null
  window_start_utc?: string | null
  window_end_utc?: string | null
  capability_contract_version?: 'country_outage_capabilities_v1'
  event_identity: {
    incident_id: string
    legacy_reference: string
    legacy_record_time_local: string | null
    event_type: string
    country_code: string
    country_name: string
    display_name: string
  }
  observation_scope: {
    collector_id: string
    collector_ids?: string[]
    collector_count: number
    vantage_point_count: number | null
    vantage_point_semantics: string
    window_start_utc: string | null
    window_start_local: string | null
    window_end_utc: string | null
    window_end_local: string | null
    timezone: string
    interval_seconds: number | null
    observation_count: number
    expected_observation_count: number | null
    missing_observation_count?: number
    quality_status: string
    last_observation_at_utc: string | null
    last_observation_at_local: string | null
    replay_completed_at_utc: string | null
    replay_completed_at_local: string | null
    left_boundary: string
    right_boundary: string
  }
  cohort: {
    cohort_id: string
    seed_observed_at_utc: string
    seed_observed_at_local: string
    origin_asn_count: number
    prefix_vp_count: number
    ipv4_prefix_vp_count: number | null
    ipv6_prefix_vp_count: number | null
    mapping_version: string
    denominator_policy: string
  } | null
  normal_band: {
    state: 'unavailable' | 'not_applicable'
    label: string
    reason: string
  }
  rule_marker: {
    metric: string
    threshold: number
    consecutive_observation_count: number
    interval_seconds: number
    first_met_at_utc: string | null
    first_met_at_local: string | null
  } | null
  capabilities?: Record<string, EventObservationCapability>
  legacy_summary?: {
    event_id: number
    source: string
    start_time_local: string | null
    end_time_local: string | null
    duration: string | null
    total_asn_count: number | null
    affected_asn_count: number | null
    affected_asns: Array<string | number>
    risk_level: string | null
    description: string | null
    summary: string | null
  } | null
  metric_definitions: Array<{
    key: string
    label: string
    unit: string
    population: string
    definition: string
  }>
  series: EventObservationSeriesPoint[]
  metric_extrema: Record<string, {
    min: EventObservationExtreme | null
    max: EventObservationExtreme | null
  }>
  resource_series: EventObservationResourcePoint[]
  resource_metric_extrema: Record<string, {
    min: EventObservationExtreme | null
    max: EventObservationExtreme | null
  }>
  country_update_series: EventObservationUpdatePoint[]
  country_update_metric_extrema: Record<string, {
    min: EventObservationExtreme | null
    max: EventObservationExtreme | null
  }>
  annotations: Array<{
    kind: string
    metric: string
    observed_at_utc: string | null
    observed_at_local: string | null
    label: string
    value: number | null
    unit: string
  }>
  asn_state: {
    state_codes: Record<string, string>
    observed_at_utc: string[]
    observed_at_local: string[]
    timelines: EventObservationAsnTimeline[]
  }
  limitations: string[]
  audit: EventObservationAudit | null
  asn_page?: CountryOutageAsnPage
  trend_product?: CountryOutageTrendProduct | null
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
