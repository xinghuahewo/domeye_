import type { P0MetricPoint, P0MetricSeries, P0Profile } from '@/api/p0'
import type { ChartSeries } from '@/components/LineChart.vue'

const OBSERVED_STATES = new Set<P0MetricPoint['value_state']>([
  'observed_nonzero',
  'observed_zero',
])

export function metricPointValue(point: P0MetricPoint): number | null {
  if (!OBSERVED_STATES.has(point.value_state)) return null
  return typeof point.value === 'number' && Number.isFinite(point.value) ? point.value : null
}

export function sumObservedMetric(metric: P0MetricSeries): number | null {
  let observedCount = 0
  let total = 0
  for (const point of metric.points) {
    const value = metricPointValue(point)
    if (value === null) continue
    observedCount += 1
    total += value
  }
  return observedCount > 0 ? total : null
}

export function ratioOfSums(metric: P0MetricSeries): number | null {
  if (metric.aggregation !== 'ratio_of_sums') return null

  let numerator = 0
  let denominator = 0
  let observedCount = 0
  for (const point of metric.points) {
    if (metricPointValue(point) === null || !point.formula_inputs) continue
    const input = point.formula_inputs
    if (
      typeof input.numerator_withdraw_count !== 'number'
      || !Number.isFinite(input.numerator_withdraw_count)
      || typeof input.denominator_update_total !== 'number'
      || !Number.isFinite(input.denominator_update_total)
    ) continue
    observedCount += 1
    numerator += input.numerator_withdraw_count
    denominator += input.denominator_update_total
  }

  return observedCount > 0 && denominator > 0 ? numerator / denominator : null
}

export function toChartSeries(
  metric: P0MetricSeries,
  name: string,
  color: string,
): ChartSeries {
  return {
    name,
    color,
    data: metric.points.map((point) => [point.time, metricPointValue(point)]),
  }
}

function shanghaiParts(value: Date) {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hourCycle: 'h23',
  }).formatToParts(value)
  return Object.fromEntries(parts.map((part) => [part.type, part.value]))
}

export function toShanghaiBackendTime(value: Date): string {
  const parts = shanghaiParts(value)
  return `${parts.year}-${parts.month}-${parts.day} ${parts.hour}:${parts.minute}:${parts.second}`
}

export function fixedProfileFinalDayRange(profile: P0Profile) {
  const endExclusive = new Date(profile.window_end_exclusive)
  if (!Number.isFinite(endExclusive.getTime())) throw new Error('P0 固定窗口终点无效')
  return {
    start_time: toShanghaiBackendTime(new Date(endExclusive.getTime() - 24 * 60 * 60 * 1000)),
    // 历史聚合接口的 end_time 是秒级闭区间；不能把 P0 的排他终点直接传入。
    end_time: toShanghaiBackendTime(new Date(endExclusive.getTime() - 1000)),
  }
}

export function formatProfileWindow(profile: P0Profile): string {
  const start = new Date(profile.window_start)
  const endExclusive = new Date(profile.window_end_exclusive)
  if (!Number.isFinite(start.getTime()) || !Number.isFinite(endExclusive.getTime())) return '固定窗口时间无效'

  const endInclusive = new Date(endExclusive.getTime() - 1)
  const date = (value: Date) => {
    const parts = shanghaiParts(value)
    return `${parts.year}-${parts.month}-${parts.day}`
  }
  return `${date(start)} — ${date(endInclusive)}`
}
