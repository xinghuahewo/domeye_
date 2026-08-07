import { describe, expect, it } from 'vitest'

import {
  buildObservationEndpoint,
  buildDetailEndpoint,
  normalizeAsOverview,
  normalizeCountryOverview,
  normalizeCountryOutageObservation,
  normalizeDashboardOverview,
  normalizeEvidenceBundle,
  normalizeEventPage,
  normalizeEventObservation,
  normalizeTime,
  parseDetailUrl,
} from './normalize'

describe('API 数据归一化', () => {
  it('处理字符串计数、换行时间和空结束时间', () => {
    const page = normalizeEventPage({
      total_page: 10,
      record_count: '100',
      data: [{
        event_type: '前缀劫持',
        level: 'high',
        start_time: '2026-07-17\n08:00:00',
        end_time: 'None',
        detail_url: 'hijack/2026-07-17 08:00:00/1.2.3.0-24/1/r',
      }],
    })

    expect(page.recordCount).toBe(100)
    expect(page.data[0]?.startTime).toBe('2026-07-17 08:00:00')
    expect(page.data[0]?.endTime).toBeNull()
  })

  it('解析六类核心详情引用', () => {
    const refs = [
      'hijack/2026-07-17 08:00:00/1.2.3.0-24/1/r',
      'sub_hijack/2026-07-17 08:00:00/1.2.3.0-25/2/r',
      'prefix_outage/2026-07-17 08:00:00/1.2.3.0-24/3/r',
      'as_outage/2026-07-17 08:00:00/4134/4/r',
      'country_outage/2026-07-17 08:00:00/CN/5/r',
      'leak/2026-07-17 08:00:00/2001:db8::-32/6/r',
    ]

    for (const ref of refs) {
      const parsed = parseDetailUrl(ref)
      expect(parsed).not.toBeNull()
      expect(buildDetailEndpoint(parsed!)).toContain('%20')
    }
  })

  it('拒绝非核心详情并清理无效时间', () => {
    expect(parseDetailUrl('boundary_outage/2026-07-17 08:00:00/a/1/r')).toBeNull()
    expect(normalizeTime('NaT')).toBeNull()
    expect(normalizeTime('-')).toBeNull()
  })

  it('构造观测接口并拒绝混入分析叙事字段', () => {
    const parsed = parseDetailUrl('country_outage/2026-02-27 09:12:32/IR/1/r')
    expect(parsed).not.toBeNull()
    expect(buildObservationEndpoint(parsed!)).toBe(
      'events/observations/country_outage/2026-02-27%2009%3A12%3A32/IR/1/r',
    )

    const observation = {
      schema_version: 'event_observation_v1',
      event_identity: {},
      observation_scope: {},
      cohort: {},
      normal_band: {},
      rule_marker: {},
      metric_definitions: [],
      series: [],
      metric_extrema: {},
      resource_series: [],
      resource_metric_extrema: {},
      annotations: [],
      asn_state: {},
      limitations: [],
      audit: {},
    }
    expect(normalizeEventObservation(observation).schema_version).toBe('event_observation_v1')
    expect(() => normalizeEventObservation({
      ...observation,
      lifecycle: { state: 'forbidden' },
    })).toThrow('混入分析叙事字段')
  })

  it('组合 legacy summary 并拒绝跨接口发布身份漂移', () => {
    const metadata = {
      revision: 1,
      publication_id: 'publication_legacy_v1_example',
      publication_state: 'published',
      observation_state: 'legacy_summary',
      data_mode: 'legacy',
      data_through: null,
      updated_at: null,
      is_final: false,
      processing_status: {
        state: 'idle',
        updated_at: null,
        attempted_through: null,
        reason: null,
        last_complete_data_through: null,
      },
      missing_slot_count: 0,
      incident_id: 'legacy_country_outage_v1.example',
      cohort_id: null,
      window_start_utc: '2026-02-28T16:00:00Z',
      window_end_utc: null,
      capability_contract_version: 'country_outage_capabilities_v1',
    }
    const overview = {
      schema_version: 'country_outage_overview_v2',
      ...metadata,
      event_identity: {},
      observation_scope: {},
      cohort: null,
      normal_band: {},
      rule_marker: null,
      capabilities: {
        legacy_summary: { state: 'available' },
        asn_matrix: { state: 'unavailable', reason: '未保存' },
      },
      legacy_summary: { affected_asn_count: 5 },
      limitations: [],
    }
    const series = {
      schema_version: 'country_outage_series_v2',
      ...metadata,
      interval_seconds: null,
      metric_definitions: [],
      series: [],
      metric_extrema: {},
      resource_series: [],
      resource_metric_extrema: {},
      annotations: [],
    }
    const asns = {
      schema_version: 'country_outage_asn_page_v2',
      ...metadata,
      page: 1,
      page_size: 60,
      page_count: 1,
      total: 0,
      observed_at_utc: [],
      observed_at_local: [],
      state_codes: {},
      duration_histogram: {},
      items: [],
    }
    const audit = {
      schema_version: 'country_outage_audit_v2',
      ...metadata,
      engine_version: 'test-engine-v1',
      algorithm_version: null,
      mapping_version: null,
      quality_status: 'pass',
      source_system: 'test',
      source_table: 'test',
      source_reference: 'test',
      evidence_level: 'test',
      consumed_deliverable_hashes_verified: true,
      verified_hashes: {},
      route_state_file: {},
      input_summary: {},
    }

    const observation = normalizeCountryOutageObservation(
      overview,
      series,
      asns,
      audit,
    )
    expect(observation.cohort).toBeNull()
    expect(observation.legacy_summary?.affected_asn_count).toBe(5)
    expect(observation.capabilities?.asn_matrix?.state).toBe('unavailable')
    expect(observation.audit?.quality_status).toBe('pass')

    expect(() => normalizeCountryOutageObservation(
      overview,
      { ...series, data_through: '2026-02-28T16:05:00Z' },
      asns,
      audit,
    )).toThrow('发布身份不一致：data_through')

    expect(() => normalizeCountryOutageObservation(
      overview,
      {
        ...series,
        processing_status: {
          ...metadata.processing_status,
          state: 'failed',
          reason: 'parse_failed',
        },
      },
      asns,
      audit,
    )).toThrow('发布身份不一致：processing_status')

    expect(() => normalizeCountryOutageObservation(
      overview,
      series,
      asns,
      { ...audit, publication_id: 'publication_drift' },
    )).toThrow('发布身份不一致：publication_id')
  })

  it('把生产数据层紧凑序列展开为 4,320 个同发布状态点', () => {
    const metadata = {
      revision: 3,
      publication_id: 'observation-publication-test',
      publication_state: 'published',
      observation_state: 'evidence_complete',
      data_mode: 'replay',
      data_through: '2026-03-11T00:00:00Z',
      updated_at: '2026-08-07T00:00:00Z',
      is_final: true,
      processing_status: {
        state: 'final',
        updated_at: '2026-08-07T00:00:00Z',
        attempted_through: '2026-03-11T00:00:00Z',
        reason: null,
        last_complete_data_through: '2026-03-11T00:00:00Z',
      },
      missing_slot_count: 0,
      incident_id: 'incident-test',
      cohort_id: 'cohort-test',
      window_start_utc: '2026-02-24T00:05:00Z',
      window_end_utc: '2026-03-11T00:00:00Z',
      capability_contract_version: 'country_outage_capabilities_v1',
    }
    const overview = {
      schema_version: 'country_outage_overview_v2',
      ...metadata,
      event_identity: {},
      observation_scope: {},
      cohort: {},
      normal_band: {},
      rule_marker: null,
      capabilities: {
        fixed_cohort: { state: 'available' },
        asn_matrix: { state: 'unavailable' },
      },
      annotations: [],
      limitations: [],
    }
    const vector = (value: number) => Array.from({ length: 4320 }, () => value)
    const compact = {
      schema_version: 'country_outage_compact_series_v1',
      ...metadata,
      metric_definitions: [],
      series_contract: {
        schema_version: 'rrc25-compact-country-series/v1',
        collector_id: 'rrc25',
        country_code: 'IR',
        series_id: 'series-test',
        first_state_point_utc: '2026-02-24T00:05:00Z',
        point_count: 4320,
        step_seconds: 300,
        columns: [
          'baseline_v4', 'baseline_v6', 'cohort_visible_v4',
          'cohort_visible_v6', 'current_visible_v4', 'current_visible_v6',
          'announcement_v4', 'announcement_v6', 'withdrawal_v4',
          'withdrawal_v6',
        ],
        values: [
          vector(100), vector(10), vector(90), vector(9), vector(91),
          vector(9), vector(2), vector(0), vector(1), vector(0),
        ],
        quality: { status: 'complete', missing: 0, finality: 'final' },
      },
    }
    const audit = {
      schema_version: 'country_outage_audit_v2',
      ...metadata,
      engine_version: 'data-layer-test',
      algorithm_version: 'test',
      mapping_version: 'test',
      quality_status: 'pass',
      source_system: 'test',
      source_table: 'test',
      source_reference: 'test',
      evidence_level: 'test',
      consumed_deliverable_hashes_verified: true,
      verified_hashes: {},
      route_state_file: {},
      input_summary: {},
    }
    const observation = normalizeCountryOutageObservation(
      overview,
      compact,
      null,
      audit,
    )
    expect(observation.series).toHaveLength(4320)
    expect(observation.series[0]?.visible_prefix_vp_count).toBe(99)
    expect(observation.series[0]?.visible_prefix_vp_ratio).toBe(0.9)
    expect(observation.series.at(-1)?.observed_at_utc).toBe('2026-03-11T00:00:00Z')
    expect(observation.asn_page?.total).toBe(0)
  })

  it('归一化首页六类趋势、影响范围和排行', () => {
    const overview = normalizeDashboardOverview({
      start_time: '2026-03-31 00:00:00',
      end_time: '2026-03-31 23:59:59',
      timezone: 'Asia/Shanghai',
      latest_observation: '2026-03-31 23:59:00',
      event_count: 3,
      previous_event_count: 1,
      event_change_rate: 200,
      high_risk_count: 1,
      active_event_count: 1,
      affected_asn_count: 2,
      affected_country_count: 2,
      event_series: [{
        time: '2026-03-31 18:00:00',
        counts: { 前缀劫持: 1, 路由泄漏: 1 },
        total: 2,
      }],
      country_rankings: [{ name: '中国', event_count: 2, high_risk_count: 1 }],
      asn_rankings: [{ asn: '4134', name: 'AS4134', event_count: 2, high_risk_count: 1 }],
    })

    expect(overview.eventCount).toBe(3)
    expect(overview.eventSeries[0]?.counts['前缀中断']).toBe(0)
    expect(overview.countryRankings[0]?.name).toBe('中国')
    expect(overview.asnRankings[0]?.asn).toBe('4134')
  })

  it('归一化国家工作台排行、资源缺失和单国时序', () => {
    const profile = {
      country: '中国',
      announce: '100',
      withdraw: 20,
      update_total: 120,
      withdraw_rate: 16.7,
      previous_update_total: 90,
      update_change_rate: 33.3,
      sample_count: 2,
      latest_observation: '2026-03-31 23:55:00',
      ipv4_prefixes: 1000,
      ipv6_prefixes: null,
      ipv4_addresses: 256000,
      ipv4_prefix_change: 10,
      ipv6_prefix_change: null,
      ipv4_address_change: 2560,
      resource_change: 10,
      resource_change_rate: null,
      peak_updates: 70,
      peak_time: '2026-03-31 20:00:00',
      anomaly_count: 3,
      high_risk_count: 2,
      sparkline: [{ time: '2026-03-31 23:00:00', announce: 100, withdraw: 20 }],
      series: [{
        time: '2026-03-31 23:55:00',
        announce: 60,
        withdraw: 10,
        ipv4_prefixes: 1000,
        ipv6_prefixes: null,
        ipv4_addresses: 256000,
      }],
    }
    const overview = normalizeCountryOverview({
      start_time: '2026-03-30 23:59:59',
      end_time: '2026-03-31 23:59:59',
      timezone: 'Asia/Shanghai',
      latest_observation: '2026-03-31 23:55:00',
      country_count: 210,
      countries_with_anomalies: 85,
      update_leader: profile,
      withdraw_rate_leader: profile,
      resource_change_leader: profile,
      update_rankings: [profile],
      withdraw_rate_rankings: [profile],
      resource_change_rankings: [profile],
      anomaly_rankings: [profile],
      selected_country: profile,
    })

    expect(overview.countryCount).toBe(210)
    expect(overview.updateLeader?.updateTotal).toBe(120)
    expect(overview.selectedCountry?.ipv6Prefixes).toBeNull()
    expect(overview.selectedCountry?.series[0]?.announce).toBe(60)
    expect(overview.selectedCountry?.sparkline[0]?.withdraw).toBe(20)
  })

  it('归一化 ASN 候选集、静态信息、波动度和单 ASN 时序', () => {
    const profile = {
      asn: '3356',
      as_name: 'LEVEL3',
      org_name: '三级通信',
      country: '美国',
      as_type: 'Transit/Access',
      global_rank: 1,
      country_rank: 1,
      important: true,
      announce: 100,
      withdraw: 20,
      update_total: 120,
      withdraw_rate: 16.7,
      previous_update_total: 90,
      update_change_rate: 33.3,
      sample_count: 2,
      latest_observation: '2026-03-31 23:55:00',
      ipv4_prefixes: 1000,
      ipv6_prefixes: null,
      ipv4_addresses: 256000,
      ipv4_prefix_change: 10,
      ipv6_prefix_change: null,
      ipv4_address_change: 2560,
      resource_change: 10,
      resource_change_rate: 1,
      peak_updates: 70,
      peak_time: '2026-03-31 20:00:00',
      volatility: 28.4,
      anomaly_count: 3,
      high_risk_count: 2,
      sparkline: [{ time: '2026-03-31 23:00:00', announce: 100, withdraw: 20 }],
      series: [{
        time: '2026-03-31 23:55:00',
        announce: 60,
        withdraw: 10,
        ipv4_prefixes: 1000,
        ipv6_prefixes: null,
        ipv4_addresses: 256000,
      }],
    }
    const overview = normalizeAsOverview({
      start_time: '2026-03-30 23:59:59',
      end_time: '2026-03-31 23:59:59',
      timezone: 'Asia/Shanghai',
      latest_observation: '2026-03-31 23:55:00',
      scope_kind: 'operational_asn_cohort',
      scope_note: '候选集说明',
      candidate_pool_size: 1000,
      scope_size: 150,
      feature_asn_count: 147,
      important_asn_count: 24,
      asns_with_anomalies: 53,
      update_leader: profile,
      withdraw_rate_leader: profile,
      resource_change_leader: profile,
      volatility_leader: profile,
      update_rankings: [profile],
      withdraw_rate_rankings: [profile],
      resource_change_rankings: [profile],
      volatility_rankings: [profile],
      anomaly_rankings: [profile],
      selected_asn: profile,
    })

    expect(overview.candidatePoolSize).toBe(1000)
    expect(overview.scopeSize).toBe(150)
    expect(overview.selectedAsn?.asn).toBe('3356')
    expect(overview.selectedAsn?.important).toBe(true)
    expect(overview.selectedAsn?.volatility).toBe(28.4)
    expect(overview.selectedAsn?.ipv6Prefixes).toBeNull()
    expect(overview.selectedAsn?.series[0]?.withdraw).toBe(10)
  })

  it('归一化 Evidence Bundle 并保留空路径快照、反证和限制', () => {
    const bundle = normalizeEvidenceBundle({
      bundle_version: 'evidence_bundle_v1',
      incident_id: 'inc_v1_0123456789abcdef01234567',
      incident_id_schema: 'incident_id_v1',
      semantic_guardrails: {
        contract_version: 'legacy_event_semantic_guardrails_v1',
        lifecycle_state: 'recorded',
        attribution_state: 'detector_fact_only',
        ratio_state: 'not_applicable',
        blocked_claims: ['causal_conclusion'],
        reason_codes: [],
      },
      event: {
        kind: 'hijack',
        label: '前缀劫持',
        object: '1.2.3.0/24',
        level: 'high',
        summary: '检测到源 AS 变化',
        duration: '0:03:00',
        event_time_local: '2026-02-01T00:00:00+08:00',
        event_time_utc: '2026-01-31T16:00:00Z',
        end_time_local: '2026-02-01T00:03:00+08:00',
        end_time_utc: '2026-01-31T16:03:00Z',
        source_timezone: 'Asia/Shanghai',
      },
      data_snapshot: {
        snapshot_time_local: '2026-03-31T23:59:59+08:00',
        snapshot_time_utc: '2026-03-31T15:59:59Z',
        timezone: 'Asia/Shanghai',
      },
      source_record: {
        source_system: 'Domeye business fact table',
        source_table: 'hijack_202602',
        source_code: 'r',
        detail_reference: 'hijack/2026-02-01 00:00:00/1.2.3.0-24/7/r',
        record_locator: { event_id: 7 },
      },
      phase_coverage: {
        before: { status: 'observed_paths', snapshot_count: 1, path_count: 1, evidence_ids: ['ev_v1_aaaaaaaaaaaaaaaaaaaaaaaa'] },
        during: { status: 'observed_no_path', snapshot_count: 1, path_count: 0, evidence_ids: ['ev_v1_bbbbbbbbbbbbbbbbbbbbbbbb'] },
        after: { status: 'observed_paths', snapshot_count: 1, path_count: 1, evidence_ids: ['ev_v1_cccccccccccccccccccccccc'] },
      },
      evidence_items: [{
        evidence_id: 'ev_v1_bbbbbbbbbbbbbbbbbbbbbbbb',
        phase: 'during',
        kind: 'route_observation',
        label: '异常期间路径快照',
        source_field: 'eve_vp_paths',
        semantics: 'route_observation_not_causal_trace',
        observed_at_local: '2026-02-01T00:01:00+08:00',
        observed_at_utc: '2026-01-31T16:01:00Z',
        observation_state: 'no_path_in_snapshot',
        path_count: 0,
        paths: [],
      }],
      assessment: {
        classification: 'observation_only',
        supports: ['支持可见性下降描述'],
        counterevidence: ['异常后重新观测到路径，但不证明全网恢复。'],
        gaps: ['未保留 VP 身份'],
        causal_conclusion: null,
      },
      data_quality: {
        observed_phase_count: 3,
        expected_phase_count: 3,
        route_observation_count: 3,
        evidence_item_count: 4,
        vantage_point_identity_available: false,
        raw_bgp_message_available: false,
        timezone_semantics: 'timestamp_without_time_zone interpreted as Asia/Shanghai',
        limitations: ['Route Observation 不是因果链路。'],
      },
      fact_record: { event_info: '检测到源 AS 变化' },
    })

    expect(bundle.incidentId).toBe('inc_v1_0123456789abcdef01234567')
    expect(bundle.event.eventTimeUtc).toBe('2026-01-31T16:00:00Z')
    expect(bundle.phaseCoverage.during.status).toBe('observed_no_path')
    expect(bundle.evidenceItems[0]?.paths).toEqual([])
    expect(bundle.assessment.counterevidence[0]).toContain('不证明全网恢复')
    expect(bundle.assessment.causalConclusion).toBeNull()
    expect(bundle.semanticGuardrails.blockedClaims).toContain('causal_conclusion')
    expect(bundle.dataQuality.vantagePointIdentityAvailable).toBe(false)
    expect(bundle.sourceRecord.sourceTable).toBe('hijack_202602')
  })
})
