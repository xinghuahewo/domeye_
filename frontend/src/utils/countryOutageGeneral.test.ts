import { describe, expect, it } from 'vitest'

import {
  normalizeCountryOutageGeneralAffectedAsPage,
  normalizeCountryOutageGeneralPage,
  normalizeCountryOutageGeneralPathDownstreamPage,
} from '@/utils/countryOutageGeneral'

const metadata = {
  revision: 1,
  publication_id: 'publication-1',
  publication_state: 'published',
  observation_state: 'evidence_complete',
  data_mode: 'replay',
  data_through: '2026-03-10T00:00:00Z',
  is_final_in_data_range: false,
  lifecycle_state: 'active',
  quality_state: 'complete',
  missing_slot_count: 0,
  collector_id: 'rrc25',
  incident_id: 'incident-1',
  cohort_id: 'cohort-1',
  window_start_utc: '2026-02-27T01:12:32Z',
  window_end_utc: '2026-03-10T00:00:00Z',
} as const

const trackKeys = [
  'interrupted_prefix_count',
  'completely_interrupted_prefix_count',
  'invisible_direction_count',
  'affected_asn_count',
  'route_interrupted_asn_count',
  'fixed_visible_ipv4_address_count',
  'fixed_visible_ipv6_slash48_count',
  'new_visible_ipv4_prefix_count',
  'new_visible_ipv6_prefix_count',
  'new_visible_ipv4_address_count',
  'new_visible_ipv6_slash48_count',
  'new_cumulative_ipv4_prefix_count',
  'new_cumulative_ipv6_prefix_count',
  'new_cumulative_ipv4_address_count',
  'new_cumulative_ipv6_slash48_count',
] as const

const capabilities = {
  overview: 'available',
  event_series: 'available',
  affected_as: 'available',
  path_downstreams: 'available',
  full_path_evidence: 'audit_only',
} as const

function pagePayloads() {
  const current = Object.fromEntries(trackKeys.map((key, index) => [key, index]))
  const tracks = Object.fromEntries(trackKeys.map((key, index) => [key, [index, index + 1]]))
  const trackDefinitions = Object.fromEntries(trackKeys.map((key) => [key, {
    label: key,
    unit: 'count',
    definition: key,
  }]))
  return {
    resolution: {
      ...metadata,
      schema_version: 'country_outage_general_resolution_v1',
      legacy_reference: 'country_outage/2026-02-27 09:12:32/IR/1/r',
      event_type: 'country_outage',
      country_code: 'IR',
      latest_revision: 1,
      capabilities,
    },
    overview: {
      ...metadata,
      schema_version: 'country_outage_general_overview_v1',
      event: {
        legacy_reference: 'country_outage/2026-02-27 09:12:32/IR/1/r',
        country_code: 'IR',
        detected_at_utc: '2026-02-27T01:12:32Z',
        event_end_at_utc: null,
        event_duration_seconds: null,
      },
      interval_seconds: 300,
      state_point_count: 2,
      cohort: {
        cohort_id: 'cohort-1',
        fixed_prefix_count: 10,
        fixed_asn_count: 4,
        independent_direction_relation_count: 20,
        new_prefix_count: 1,
      },
      current,
      peaks: {},
      affected_as_count: 1,
      route_interrupted_as_count: 0,
      path_downstream_relation_count: 1,
      concurrent_path_downstream_relation_count: 1,
      capabilities,
      semantic_boundary: 'rrc25_control_plane_observation_not_user_impact_or_cause',
    },
    series: {
      ...metadata,
      schema_version: 'country_outage_general_series_v1',
      interval_seconds: 300,
      point_count: 2,
      timestamps: ['2026-02-27T01:15:00Z', '2026-02-27T01:20:00Z'],
      track_definitions: trackDefinitions,
      tracks,
    },
  }
}

describe('通用观测页组合边界', () => {
  it('accepts one complete release with the exact track population', () => {
    const payloads = pagePayloads()
    const result = normalizeCountryOutageGeneralPage(
      payloads.resolution,
      payloads.overview,
      payloads.series,
    )
    expect(result.series.point_count).toBe(2)
    expect(result.resolution.country_code).toBe('IR')
  })

  it('rejects mixed releases and incomplete time series', () => {
    const mixed = pagePayloads()
    const mixedSeries = { ...mixed.series, publication_id: 'publication-2' }
    expect(() => normalizeCountryOutageGeneralPage(
      mixed.resolution,
      mixed.overview,
      mixedSeries,
    )).toThrow(/publication_id/)

    const incomplete = pagePayloads()
    incomplete.series.tracks.interrupted_prefix_count = [1]
    expect(() => normalizeCountryOutageGeneralPage(
      incomplete.resolution,
      incomplete.overview,
      incomplete.series,
    )).toThrow(/人口冲突/)
  })

  it('rejects oversized drilldowns and path rows without bounded real samples', () => {
    expect(() => normalizeCountryOutageGeneralAffectedAsPage({
      ...metadata,
      schema_version: 'country_outage_general_affected_as_page_v1',
      page: 1,
      page_size: 61,
      page_count: 1,
      total: 0,
      classification: 'all',
      query: '',
      sort: 'default',
      items: [],
    }, metadata)).toThrow(/超过 60 条/)

    expect(() => normalizeCountryOutageGeneralPathDownstreamPage({
      ...metadata,
      schema_version: 'country_outage_general_path_downstream_page_v1',
      page: 1,
      page_size: 30,
      page_count: 1,
      total: 1,
      affected_asn: null,
      scope: 'all',
      query: '',
      relationship_semantics: 'observed_ordered_rrc25_path_association_not_dependency_or_cause',
      items: [{
        affected_asn: 1,
        downstream_asn: 2,
        observed_path_count: 1,
        associated_fixed_prefix_count: 1,
        independent_direction_count: 1,
        route_observation_count: 1,
        concurrent_state_point_count: 0,
        peak_concurrent_interrupted_prefix_count: 0,
        peak_concurrent_ipv4_address_count: 0,
        peak_concurrent_ipv6_slash48_count: 0,
        path_samples: [],
        relationship_semantics: 'observed_ordered_rrc25_path_association_not_dependency_or_cause',
      }],
    }, metadata)).toThrow(/1 至 3 条/)
  })
})
