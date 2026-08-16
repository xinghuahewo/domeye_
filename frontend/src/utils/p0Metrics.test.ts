import { describe, expect, it, vi } from 'vitest'

import type { P0MetricPoint, P0MetricSeries, P0Status } from '@/api/p0'

import { loadP0HomeDataset, type P0HomeLoaderDependencies } from './p0Home'
import {
  fixedProfileFinalDayRange,
  metricPointValue,
  ratioOfSums,
  sumObservedMetric,
  toChartSeries,
} from './p0Metrics'

function point(overrides: Partial<P0MetricPoint> = {}): P0MetricPoint {
  return {
    time: '2026-02-01T00:00:00Z',
    value: 1,
    value_state: 'observed_nonzero',
    missing_reason: null,
    formula_inputs: null,
    ...overrides,
  } as P0MetricPoint
}

function metric(points: P0MetricPoint[], aggregation: P0MetricSeries['aggregation'] = 'sum_observation_values') {
  return { points, aggregation } as P0MetricSeries
}

describe('P0 指标前端语义', () => {
  it('保留 observed_zero，并且不把缺失点补成 0', () => {
    const observedZero = point({ value: 0, value_state: 'observed_zero' })
    const sourceGap = point({ value: null, value_state: 'source_unavailable', missing_reason: 'source_unavailable' })

    expect(metricPointValue(observedZero)).toBe(0)
    expect(metricPointValue(sourceGap)).toBeNull()
    expect(sumObservedMetric(metric([observedZero, sourceGap]))).toBe(0)
    expect(toChartSeries(metric([observedZero, sourceGap]), 'UPDATE', '#000').data).toEqual([
      ['2026-02-01T00:00:00Z', 0],
      ['2026-02-01T00:00:00Z', null],
    ])
  })

  it('撤回率使用 ratio_of_sums，而不是逐点比率平均', () => {
    const series = metric([
      point({ value: 0.5, formula_inputs: { numerator_withdraw_count: 1, denominator_update_total: 2 } }),
      point({ value: 0.01, formula_inputs: { numerator_withdraw_count: 1, denominator_update_total: 100 } }),
    ], 'ratio_of_sums')

    expect(ratioOfSums(series)).toBeCloseTo(2 / 102)
    expect(ratioOfSums(series)).not.toBeCloseTo((0.5 + 0.01) / 2)
  })

  it('完整保留 16,992 个时间槽以及其中的 null 断点', () => {
    const start = Date.parse('2026-01-31T16:00:00Z')
    const missingIndex = 3
    const points = Array.from({ length: 16_992 }, (_, index) => point({
      time: new Date(start + index * 300_000).toISOString(),
      value: index === missingIndex ? null : index,
      value_state: index === missingIndex ? 'source_unavailable' : index === 0 ? 'observed_zero' : 'observed_nonzero',
      missing_reason: index === missingIndex ? 'source_unavailable' : null,
    }))
    const chart = toChartSeries(metric(points), 'ANNOUNCE', '#000')

    expect(chart.data).toHaveLength(16_992)
    expect(chart.data[0]?.[1]).toBe(0)
    expect(chart.data[missingIndex]?.[1]).toBeNull()
  })

  it('从固定数据档推导窗口末日 24 小时，并按 Asia/Shanghai 传给历史事实接口', () => {
    const range = fixedProfileFinalDayRange({
      id: 'fixed-feb-mar-2026',
      timezone: 'Asia/Shanghai',
      window_start: '2026-02-01T00:00:00+08:00',
      window_end_exclusive: '2026-04-01T00:00:00+08:00',
      snapshot_time: '2026-03-31T23:59:59+08:00',
      boundary: '[start,end)',
    })

    expect(range).toEqual({
      start_time: '2026-03-31 00:00:00',
      end_time: '2026-03-31 23:59:59',
    })
  })

  it('原始 UPDATE 覆盖直接读取状态服务合同，不按点数自行推算', () => {
    const expectedCount = 16_992
    const sourceObservedCount = 10_271
    const sourceMissingCount = expectedCount - sourceObservedCount
    const processingGapCount = 6
    const status = {
      raw_coverage: {
        artifact_type: 'update',
        collector_scope: ['rrc00', 'rrc10'],
        status: 'partial',
        expected_count: expectedCount,
        observed_count: sourceObservedCount,
        missing_count: sourceMissingCount,
        coverage_ratio: sourceObservedCount / expectedCount,
        missing_value_state: 'source_unavailable',
      },
    } as P0Status
    const series = {
      ...metric([]),
      expected_sample_count: expectedCount,
      source_observed_sample_count: sourceObservedCount,
      metric_observed_sample_count: sourceObservedCount - processingGapCount,
      coverage: {
        source_coverage_ratio: sourceObservedCount / expectedCount,
        metric_coverage_ratio: (sourceObservedCount - processingGapCount) / expectedCount,
        subject_activity_density: null,
        source_gap_sample_count: sourceMissingCount,
        processing_gap_sample_count: processingGapCount,
        classification_complete: true,
      },
    } as P0MetricSeries

    expect(status.raw_coverage.artifact_type).toBe('update')
    expect(status.raw_coverage.observed_count).toBe(sourceObservedCount)
    expect(status.raw_coverage.expected_count).toBe(expectedCount)
    expect(status.raw_coverage.missing_count).toBe(sourceMissingCount)
    expect(series.coverage.processing_gap_sample_count).toBe(processingGapCount)
  })

  it('P0 状态不可用时拒绝，且不请求旧首页接口作为回退', async () => {
    const dependencies = {
      getStatus: vi.fn().mockRejectedValue(new Error('P0 unavailable')),
      getMetric: vi.fn(),
      getDashboard: vi.fn(),
      getEvents: vi.fn(),
    } as unknown as P0HomeLoaderDependencies

    await expect(loadP0HomeDataset(dependencies)).rejects.toThrow('P0 unavailable')
    expect(dependencies.getMetric).not.toHaveBeenCalled()
    expect(dependencies.getDashboard).not.toHaveBeenCalled()
    expect(dependencies.getEvents).not.toHaveBeenCalled()
  })

  it('P0 质量未准入时拒绝，且不请求指标或旧事实接口', async () => {
    const dependencies = {
      getStatus: vi.fn().mockResolvedValue({
        repository_state: 'candidate',
        production_active: false,
        quality_decision: {
          status: 'failed',
          admission_level: 'not_accepted',
        },
      } as P0Status),
      getMetric: vi.fn(),
      getDashboard: vi.fn(),
      getEvents: vi.fn(),
    } as unknown as P0HomeLoaderDependencies

    await expect(loadP0HomeDataset(dependencies)).rejects.toThrow('数据质量门禁未通过')
    expect(dependencies.getMetric).not.toHaveBeenCalled()
    expect(dependencies.getDashboard).not.toHaveBeenCalled()
    expect(dependencies.getEvents).not.toHaveBeenCalled()
  })
})
