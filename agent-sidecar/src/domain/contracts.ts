export type JsonObject = Record<string, unknown>

export interface SnapshotEnvelope {
  incident_id: string
  publication_id: string
  revision: number
  data_through: string | null
  is_final: boolean
  observation_state: string
  publication_state: string
  window_start_utc: string
  window_end_utc: string
  cohort_id?: string
}

export interface CountryOutageResolution {
  schema_version: 'country_outage_resolution_v2'
  incident_id: string
  publication_id: string
  legacy_reference: string
  event_type: 'country_outage'
  observation_state: string
  latest_revision: number
  data_mode: string
  data_through: string | null
  is_final: boolean
  missing_slot_count: number
  capability_contract_version: string
  capabilities: Record<string, CapabilityState>
}

export interface CapabilityState {
  state:
    | 'available'
    | 'building'
    | 'unavailable'
    | 'not_applicable'
    | 'unknown'
  reason?: string
}

export interface ObservationScope {
  collector_id: string
  collector_ids: string[]
  collector_count: number
  window_start_utc: string
  window_start_local: string
  window_end_utc: string
  window_end_local: string
  timezone: string
  interval_seconds: number | null
  observation_count: number
  expected_observation_count: number | null
  missing_observation_count?: number
  quality_status: string
  last_observation_at_utc: string | null
  last_observation_at_local: string | null
  [key: string]: unknown
}

export interface EventIdentity {
  incident_id: string
  legacy_reference: string
  event_type: 'country_outage'
  country_code: string
  country_name: string
  display_name: string
  [key: string]: unknown
}

export interface Cohort {
  cohort_id: string
  denominator_policy: string
  origin_asn_count: number
  prefix_vp_count: number
  ipv4_prefix_vp_count?: number
  ipv6_prefix_vp_count?: number
  mapping_version?: string
  [key: string]: unknown
}

export interface CountryOutageOverview extends SnapshotEnvelope {
  schema_version: 'country_outage_overview_v2'
  event_identity: EventIdentity
  observation_scope: ObservationScope
  cohort: Cohort | null
  capabilities: Record<string, CapabilityState>
  capability_contract_version: string
  missing_slot_count: number
  processing_status: JsonObject
  limitations: string[]
  [key: string]: unknown
}

export interface VisibilitySlot {
  observed_at_utc: string
  observed_at_local: string
  slot_state:
    | 'observed'
    | 'source_unavailable'
    | 'processing_gap'
    | 'parse_failed'
    | 'not_observed'
  missing_reason?: string | null
  visible_prefix_vp_count?: number
  visible_prefix_vp_ratio?: number
  visible_prefix_vp_delta?: number
  visible_prefix_vp_ratio_delta_pp?: number
  visible_origin_asn_count?: number
  fully_visible_asn_count?: number
  partially_visible_asn_count?: number
  fully_invisible_asn_count?: number
  ipv4_visible_prefix_vp_count?: number
  ipv4_visible_prefix_vp_ratio?: number
  ipv6_visible_prefix_vp_count?: number
  ipv6_visible_prefix_vp_ratio?: number
  announce_count?: number
  withdraw_count?: number
  update_total?: number
  withdraw_ratio?: number | null
  [key: string]: unknown
}

export interface ResourceSlot {
  observed_at_utc: string
  observed_at_local: string
  ipv4_24_equivalent_count?: number
  ipv4_24_equivalent_delta?: number | null
  ipv4_address_count?: number
  ipv4_address_delta?: number | null
  ipv6_48_equivalent_count?: number
  ipv6_48_equivalent_delta?: number | null
  announce_count?: number
  announce_delta?: number | null
  withdraw_count?: number
  withdraw_delta?: number | null
  update_total?: number
  withdraw_ratio?: number | null
  [key: string]: unknown
}

export interface CountryOutageSeries extends SnapshotEnvelope {
  schema_version: 'country_outage_series_v2'
  interval_seconds: number
  missing_slot_count: number
  metric_definitions: JsonObject[]
  series: VisibilitySlot[]
  metric_extrema: JsonObject
  resource_series: ResourceSlot[]
  resource_metric_extrema: JsonObject
  annotations: JsonObject[]
  [key: string]: unknown
}

export interface CountryOutageAudit extends SnapshotEnvelope {
  schema_version: 'country_outage_audit_v2'
  quality_status: string
  missing_slot_count: number
  missing_slots: JsonObject[]
  source_system: string
  source_reference: string
  evidence_level: string
  algorithm_version: string
  mapping_version: string
  verified_hashes: Record<string, string>
  [key: string]: unknown
}

export interface CountryOutageAsnPage extends SnapshotEnvelope {
  schema_version: 'country_outage_asn_page_v2'
  page: number
  page_size: number
  page_count: number
  total: number
  items: JsonObject[]
  [key: string]: unknown
}

export interface CountryOutageTrendGraphNode extends JsonObject {
  node_id: string
  node_type: 'Claim' | 'Evidence' | 'Limitation' | 'Unknown'
  claim_kind?: string
  text?: string
  evidence_refs?: string[]
  limitation_refs?: string[]
  unknown_refs?: string[]
  source_refs?: string[]
}

export interface CountryOutageTrendProduct extends JsonObject {
  schema_version: 'country_outage_trend_product_v1'
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
  evidence_graph: {
    schema_version: 'country_outage_evidence_graph_v1'
    graph_id: string
    profile_id: string
    analysis_id: string
    nodes: CountryOutageTrendGraphNode[]
    edges: JsonObject[]
    hypothesis_nodes_allowed: false
    causal_relations_allowed: false
    [key: string]: unknown
  }
  render_contract: {
    source_product_id: string
    surfaces: string[]
    model_may_rewrite_deterministic_values: false
  }
  [key: string]: unknown
}

export interface SnapshotIdentity {
  incidentId: string
  publicationId: string
  revision: number
  dataThrough: string | null
  isFinal: boolean
  cohortId: string
  collectorId: 'rrc25'
  windowStartUtc: string
  windowEndUtc: string
}

export interface FactProvenance {
  endpoint: 'overview' | 'series' | 'audit'
  schemaVersion: string
  pointer: string
  publicationId: string
}

export interface NumericFact {
  factId: string
  metric: string
  label: string
  value: number
  unit: string
  observedAtUtc?: string
  observedAtLocal?: string
  provenance: FactProvenance
}

export interface DerivedNumericFact extends NumericFact {
  formula: string
  operands: Record<string, number>
}

export interface KeyVisibilityPoint {
  kind: 'start' | 'lowest' | 'end' | 'largest_drop' | 'largest_recovery'
  slotIndex: number
  observedAtUtc: string
  observedAtLocal: string
  visiblePrefixVpCount: number
  visiblePrefixVpRatio: number
  provenance: FactProvenance
}

export interface ReportEligibility {
  eligible: boolean
  reasons: string[]
  missingRequiredFields: string[]
  degradedCapabilities: Record<string, CapabilityState>
}

export interface CountryOutageFactSet {
  schemaVersion: 'country_outage_report_facts_v1'
  factSetId: string
  snapshot: SnapshotIdentity
  event: EventIdentity
  scope: ObservationScope
  cohort: Cohort
  capabilities: Record<string, CapabilityState>
  quality: {
    status: string
    missingSlotCount: number
    limitations: string[]
  }
  eligibility: ReportEligibility
  keyVisibilityPoints: KeyVisibilityPoint[]
  derivedFacts: DerivedNumericFact[]
  series: VisibilitySlot[]
  resourceSeries: ResourceSlot[]
  metricExtrema: JsonObject
  resourceMetricExtrema: JsonObject
  annotations: JsonObject[]
  audit: {
    sourceSystem: string
    sourceReference: string
    evidenceLevel: string
    algorithmVersion: string
    mappingVersion: string
    verifiedHashes: Record<string, string>
  }
  trendProduct?: CountryOutageTrendProduct
}

export interface ObservationBatch {
  resolution: CountryOutageResolution
  overview: CountryOutageOverview
  series: CountryOutageSeries
  audit: CountryOutageAudit
  trendProduct?: CountryOutageTrendProduct
}
