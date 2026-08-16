import { describe, expect, it } from 'vitest'

import type { P0MetricSeries, P0Status } from '@/api/p0'

import {
  loadP0HomeDataset,
  P0_HOME_METRICS,
  type P0HomeLoaderDependencies,
} from './p0Home'

const START = '2026-01-31T16:00:00Z'
const END = '2026-01-31T16:15:00Z'

function metric(name: typeof P0_HOME_METRICS[number]): P0MetricSeries {
  return {
    schema_version: 'metric-series/v1',
    metric_name: name,
    window: { start: START, end: END, boundary: '[start,end)', timezone: 'Asia/Shanghai' },
    expected_sample_count: 3,
    source_observed_sample_count: name === 'anomaly_incident_count' ? 3 : 1,
    coverage: {
      source_gap_sample_count: name === 'anomaly_incident_count' ? 0 : 2,
      processing_gap_sample_count: 0,
    },
    points: name === 'anomaly_incident_count'
      ? [
          { time: START, value: 0, value_state: 'observed_zero', missing_reason: null },
          { time: '2026-01-31T16:05:00Z', value: 0, value_state: 'observed_zero', missing_reason: null },
          { time: '2026-01-31T16:10:00Z', value: 0, value_state: 'observed_zero', missing_reason: null },
        ]
      : [
          { time: START, value: 1, value_state: 'observed_nonzero', missing_reason: null },
          { time: '2026-01-31T16:05:00Z', value: null, value_state: 'parse_failed', missing_reason: 'parse_failed' },
          { time: '2026-01-31T16:10:00Z', value: null, value_state: 'source_unavailable', missing_reason: 'source_unavailable' },
        ],
  } as unknown as P0MetricSeries
}

function status(productionActive = false): P0Status {
  return {
    repository_state: productionActive ? 'production' : 'candidate',
    production_active: productionActive,
    quality_decision: { status: 'passed', admission_level: 'legacy_compatible' },
    available_metrics: P0_HOME_METRICS.map((metric_name) => ({ metric_name })),
    releases: { metric_candidate_fingerprint_sha256: 'a'.repeat(64) },
    profile: {
      id: 'feb-mar-2026',
      timezone: 'Asia/Shanghai',
      window_start: '2026-02-01T00:00:00+08:00',
      window_end_exclusive: '2026-02-01T00:15:00+08:00',
      snapshot_time: '2026-02-01T00:14:59+08:00',
    },
    raw_coverage: {
      artifact_type: 'update',
      collector_scope: ['rrc25'],
      status: 'partial',
      expected_count: 3,
      observed_count: 1,
      present_count: 2,
      missing_count: 2,
      coverage_ratio: 1 / 3,
      presence_ratio: 2 / 3,
      missing_value_state: 'mixed',
      missing_state_counts: { source_unavailable: 1, parse_failed: 1 },
      invalid_reason_counts: {
        compressed_stream_invalid: 1,
        compression_magic_mismatch: 0,
        empty_file: 0,
      },
    },
  } as unknown as P0Status
}

function dependencies(rawStatus = status()): P0HomeLoaderDependencies {
  const metrics = Object.fromEntries(P0_HOME_METRICS.map((name) => [name, metric(name)]))
  return {
    getStatus: async () => rawStatus,
    getMetric: async (name) => ({
      repository_state: rawStatus.repository_state,
      production_active: rawStatus.production_active,
      admission_status: 'metric_candidate_ready',
      candidate_fingerprint_sha256: 'a'.repeat(64),
      metric: metrics[name as keyof typeof metrics],
    }) as never,
    getDashboard: async () => ({ eventSeries: [], startTime: '', endTime: '' }) as never,
    getEvents: async () => [],
  }
}

describe('P0 首页跨组件数据闭包', () => {
  it('保留 source_unavailable 与 parse_failed 的互斥分类', async () => {
    const result = await loadP0HomeDataset(dependencies())
    expect(result.status.raw_coverage.present_count).toBe(2)
    expect(result.metrics.bgp_update_record_count.points[1]?.value_state).toBe('parse_failed')
  })

  it('接受显式且跨端点一致的生产激活身份', async () => {
    const result = await loadP0HomeDataset(dependencies(status(true)))
    expect(result.status.repository_state).toBe('production')
    expect(result.status.production_active).toBe(true)
  })

  it('拒绝仓库状态与生产激活标志不一致', async () => {
    const broken = status() as P0Status & { production_active: boolean }
    broken.production_active = true
    await expect(loadP0HomeDataset(dependencies(broken))).rejects.toThrow(
      '仓库状态与生产激活标志不一致',
    )

    const deps = dependencies(status(true))
    const original = deps.getMetric
    deps.getMetric = async (name) => ({
      ...await original(name),
      repository_state: 'candidate',
      production_active: false,
    })
    await expect(loadP0HomeDataset(deps)).rejects.toThrow('与 P0 状态端点的生产身份不一致')
  })

  it('拒绝存在性、原因或 Metric 点状态不闭合的候选', async () => {
    const broken = status()
    broken.raw_coverage.present_count = 3
    await expect(loadP0HomeDataset(dependencies(broken))).rejects.toThrow('覆盖、存在性与缺口分类不闭合')

    const deps = dependencies()
    const original = deps.getMetric
    deps.getMetric = async (name) => {
      const response = await original(name)
      if (name === 'bgp_update_record_count') {
        const point = response.metric.points[1]
        if (!point) throw new Error('测试点缺失')
        response.metric.points[1] = {
          ...point,
          value_state: 'source_unavailable',
          missing_reason: 'source_unavailable',
        }
      }
      return response
    }
    await expect(loadP0HomeDataset(deps)).rejects.toThrow('与原始缺口分类不闭合')
  })
})
