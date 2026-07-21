import { describe, expect, it } from 'vitest'

import {
  buildDetailEndpoint,
  normalizeAsOverview,
  normalizeCountryOverview,
  normalizeDashboardOverview,
  normalizeEvidenceBundle,
  normalizeEventPage,
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
    expect(bundle.dataQuality.vantagePointIdentityAvailable).toBe(false)
    expect(bundle.sourceRecord.sourceTable).toBe('hijack_202602')
  })
})
