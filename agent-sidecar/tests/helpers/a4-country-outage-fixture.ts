import type {
  CountryOutageAsnPage,
  ObservationBatch,
} from '../../src/domain/contracts.js'
import { iranReferenceResourceSeries } from './iran-reference-resource-series.js'
import { iranReferenceVisibilitySeries } from './iran-reference-visibility-series.js'

export const A4_REFERENCE =
  'country_outage/2026-02-27 09:12:32/IR/1/r'
export const A4_INCIDENT_ID =
  'incident_go_v1_a1de26f854831330c616a72af21597eb'
export const A4_PUBLICATION_ID =
  'publication_v1_38bddead083db3f49023c2e1'
export const A4_DATA_THROUGH = '2026-02-28T15:00:00Z'
export const A4_COHORT_ID = 'cohort-a4-ir-rrc25-r1'

export function a4ObservationBatch(): ObservationBatch {
  const envelope = {
    incident_id: A4_INCIDENT_ID,
    publication_id: A4_PUBLICATION_ID,
    publication_state: 'published',
    observation_state: 'state_complete',
    revision: 1,
    data_through: A4_DATA_THROUGH,
    is_final: true,
    window_start_utc: '2026-02-28T10:05:00Z',
    window_end_utc: A4_DATA_THROUGH,
    cohort_id: A4_COHORT_ID,
  }
  const points = iranReferenceVisibilitySeries()
  const resourcePoints = iranReferenceResourceSeries()
  const extremaPoint = (
    metric: string,
    value: number,
    observedAtLocal: string,
  ) => ({
    metric,
    value,
    observed_at_local: observedAtLocal,
    observed_at_utc: new Date(observedAtLocal)
      .toISOString()
      .replace('.000Z', 'Z'),
  })
  return {
    resolution: {
      schema_version: 'country_outage_resolution_v2',
      incident_id: A4_INCIDENT_ID,
      publication_id: A4_PUBLICATION_ID,
      legacy_reference: A4_REFERENCE,
      event_type: 'country_outage',
      observation_state: 'state_complete',
      latest_revision: 1,
      data_mode: 'replay',
      data_through: A4_DATA_THROUGH,
      is_final: true,
      missing_slot_count: 0,
      capability_contract_version: 'country_outage_capabilities_v1',
      capabilities: {},
    },
    overview: {
      ...envelope,
      schema_version: 'country_outage_overview_v2',
      event_identity: {
        incident_id: A4_INCIDENT_ID,
        legacy_reference: A4_REFERENCE,
        event_type: 'country_outage',
        country_code: 'IR',
        country_name: '伊朗',
        display_name: '伊朗 BGP 路由观测',
      },
      observation_scope: {
        collector_id: 'rrc25',
        collector_ids: ['rrc25'],
        collector_count: 1,
        window_start_utc: '2026-02-28T10:05:00Z',
        window_start_local: '2026-02-28T18:05:00+08:00',
        window_end_utc: A4_DATA_THROUGH,
        window_end_local: '2026-02-28T23:00:00+08:00',
        timezone: 'Asia/Shanghai',
        interval_seconds: 300,
        observation_count: 60,
        expected_observation_count: 60,
        missing_observation_count: 0,
        quality_status: 'pass',
        last_observation_at_utc: A4_DATA_THROUGH,
        last_observation_at_local: '2026-02-28T23:00:00+08:00',
        right_boundary: '窗口结束后无本页同口径状态',
      },
      cohort: {
        cohort_id: A4_COHORT_ID,
        denominator_policy: 'fixed_from_complete_rib',
        origin_asn_count: 563,
        prefix_vp_count: 384_767,
        ipv4_prefix_vp_count: 383_804,
        ipv6_prefix_vp_count: 963,
      },
      capabilities: {
        fixed_cohort: { state: 'available' },
        asn_matrix: { state: 'available' },
        address_families: { state: 'available' },
        update_activity: { state: 'available' },
        country_resources: { state: 'available' },
        normal_band: {
          state: 'unavailable',
          reason: '缺少可信正常参照',
        },
      },
      capability_contract_version: 'country_outage_capabilities_v1',
      missing_slot_count: 0,
      processing_status: { state: 'final' },
      limitations: ['仅为 RRC25 BGP 控制面观测。'],
    },
    series: {
      ...envelope,
      schema_version: 'country_outage_series_v2',
      interval_seconds: 300,
      missing_slot_count: 0,
      metric_definitions: [],
      series: points,
      metric_extrema: {
        fully_invisible_asn_count: {
          max: extremaPoint(
            'fully_invisible_asn_count',
            87,
            '2026-02-28T21:50:00+08:00',
          ),
        },
        partially_visible_asn_count: {
          max: extremaPoint(
            'partially_visible_asn_count',
            188,
            '2026-02-28T18:40:00+08:00',
          ),
        },
        ipv4_visible_prefix_vp_ratio: {
          min: extremaPoint(
            'ipv4_visible_prefix_vp_ratio',
            0.8228522891892737,
            '2026-02-28T22:35:00+08:00',
          ),
        },
        ipv6_visible_prefix_vp_ratio: {
          min: extremaPoint(
            'ipv6_visible_prefix_vp_ratio',
            0.9532710280373832,
            '2026-02-28T22:50:00+08:00',
          ),
        },
      },
      resource_series: resourcePoints,
      resource_metric_extrema: {
        update_total: {
          max: extremaPoint(
            'update_total',
            340_960,
            '2026-02-28T18:25:00+08:00',
          ),
        },
        announce_count: {
          max: extremaPoint(
            'announce_count',
            298_812,
            '2026-02-28T18:25:00+08:00',
          ),
        },
        withdraw_count: {
          max: extremaPoint(
            'withdraw_count',
            42_148,
            '2026-02-28T18:25:00+08:00',
          ),
        },
        ipv4_24_equivalent_count: {
          max: extremaPoint(
            'ipv4_24_equivalent_count',
            39_260,
            '2026-02-28T18:20:00+08:00',
          ),
          min: extremaPoint(
            'ipv4_24_equivalent_count',
            37_379,
            '2026-02-28T22:30:00+08:00',
          ),
        },
      },
      annotations: [],
    },
    audit: {
      ...envelope,
      schema_version: 'country_outage_audit_v2',
      quality_status: 'pass',
      missing_slot_count: 0,
      missing_slots: [],
      source_system: 'country_outage_observation_package',
      source_reference: A4_INCIDENT_ID,
      evidence_level: 'aggregated_route_state_with_artifact_hashes',
      algorithm_version: 'test/1',
      mapping_version: 'mapping-test',
      verified_hashes: { 'cohort.json': 'abc123' },
    },
  } as ObservationBatch
}

export function a4AsnPage(): CountryOutageAsnPage {
  return {
    schema_version: 'country_outage_asn_page_v2',
    incident_id: A4_INCIDENT_ID,
    publication_id: A4_PUBLICATION_ID,
    publication_state: 'published',
    observation_state: 'state_complete',
    revision: 1,
    data_through: A4_DATA_THROUGH,
    is_final: true,
    window_start_utc: '2026-02-28T10:05:00Z',
    window_end_utc: A4_DATA_THROUGH,
    cohort_id: A4_COHORT_ID,
    page: 1,
    page_size: 2,
    page_count: 282,
    total: 563,
    items: [
      {
        asn: 34369,
        longest_fully_invisible_slots: 60,
        baseline_prefix_vp_count: 10,
      },
      {
        asn: 51554,
        longest_fully_invisible_slots: 60,
        baseline_prefix_vp_count: 20,
      },
    ],
  }
}
