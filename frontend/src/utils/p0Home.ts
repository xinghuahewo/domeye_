import { getDashboardOverview, type DashboardRange } from '@/api/dashboard'
import { getTopEvents } from '@/api/events'
import {
  getP0MetricSeries,
  getP0Status,
  type P0MetricName,
  type P0MetricSeries,
  type P0Status,
} from '@/api/p0'
import type { DashboardOverview, EventRow } from '@/types/api'

import { fixedProfileFinalDayRange } from './p0Metrics'

export const P0_HOME_METRICS = [
  'bgp_announce_record_count',
  'bgp_withdraw_record_count',
  'bgp_update_record_count',
  'bgp_withdraw_ratio',
  'anomaly_incident_count',
] as const satisfies readonly P0MetricName[]

export type P0HomeMetricName = typeof P0_HOME_METRICS[number]

export interface P0HomeDataset {
  status: P0Status
  metrics: Record<P0HomeMetricName, P0MetricSeries>
  dashboard: PromiseSettledResult<DashboardOverview>
  events: PromiseSettledResult<EventRow[]>
}

export interface P0HomeLoaderDependencies {
  getStatus: () => Promise<P0Status>
  getMetric: (metricName: P0MetricName) => ReturnType<typeof getP0MetricSeries>
  getDashboard: (range: DashboardRange) => Promise<DashboardOverview>
  getEvents: () => Promise<EventRow[]>
}

const defaultDependencies: P0HomeLoaderDependencies = {
  getStatus: getP0Status,
  getMetric: getP0MetricSeries,
  getDashboard: getDashboardOverview,
  getEvents: getTopEvents,
}

function assertRepositoryIdentity(
  identity: { repository_state: string; production_active: boolean },
  label: string,
  expected?: { repository_state: string; production_active: boolean },
) {
  const internallyConsistent = (
    identity.repository_state === 'candidate' && identity.production_active === false
  ) || (
    identity.repository_state === 'production' && identity.production_active === true
  )
  if (!internallyConsistent) throw new Error(`${label}的仓库状态与生产激活标志不一致`)
  if (
    expected
    && (
      identity.repository_state !== expected.repository_state
      || identity.production_active !== expected.production_active
    )
  ) throw new Error(`${label}与 P0 状态端点的生产身份不一致`)
}

export function assertP0HomeAdmission(status: P0Status) {
  assertRepositoryIdentity(status, 'P0 状态端点')
  if (status.quality_decision.status !== 'passed') {
    throw new Error(`P0 数据质量门禁未通过：${status.quality_decision.status}`)
  }
  if (status.quality_decision.admission_level === 'not_accepted') {
    throw new Error('P0 数据尚未准入，首页不会回退到旧口径')
  }

  const available = new Set(status.available_metrics.map((item) => item.metric_name))
  const missing = P0_HOME_METRICS.filter((metricName) => !available.has(metricName))
  if (missing.length > 0) throw new Error(`P0 首页指标未完整准入：${missing.join('、')}`)
}

function assertMetricIdentity(
  status: P0Status,
  expectedName: P0HomeMetricName,
  response: Awaited<ReturnType<typeof getP0MetricSeries>>,
) {
  assertRepositoryIdentity(response, `P0 指标 ${expectedName}`, status)
  if (response.admission_status !== 'metric_candidate_ready') {
    throw new Error(`P0 指标 ${expectedName} 未处于准入状态`)
  }
  if (response.candidate_fingerprint_sha256 !== status.releases.metric_candidate_fingerprint_sha256) {
    throw new Error(`P0 指标 ${expectedName} 与状态端点 fingerprint 不一致`)
  }
  if (response.metric.metric_name !== expectedName) {
    throw new Error(`P0 指标响应身份错位：期望 ${expectedName}`)
  }
  if (response.metric.points.length !== response.metric.expected_sample_count) {
    throw new Error(`P0 指标 ${expectedName} 未保留完整时间槽与缺失状态`)
  }
  const profileStart = new Date(status.profile.window_start).getTime()
  const profileEnd = new Date(status.profile.window_end_exclusive).getTime()
  const metricStart = new Date(response.metric.window.start).getTime()
  const metricEnd = new Date(response.metric.window.end).getTime()
  if (profileStart !== metricStart || profileEnd !== metricEnd) {
    throw new Error(`P0 指标 ${expectedName} 与固定数据档窗口不一致`)
  }
}

export async function loadP0HomeDataset(
  dependencies: P0HomeLoaderDependencies = defaultDependencies,
): Promise<P0HomeDataset> {
  // P0 状态是首页的第一道闸门。失败时不得请求旧接口拼出“看似完整”的首页。
  const status = await dependencies.getStatus()
  assertP0HomeAdmission(status)

  const responses = await Promise.all(P0_HOME_METRICS.map((name) => dependencies.getMetric(name)))
  const metricEntries = responses.map((response, index) => {
    const expectedName = P0_HOME_METRICS[index]
    if (!expectedName) throw new Error('P0 首页指标响应数量异常')
    assertMetricIdentity(status, expectedName, response)
    return [expectedName, response.metric] as const
  })
  const metrics = Object.fromEntries(metricEntries) as Record<P0HomeMetricName, P0MetricSeries>

  const raw = status.raw_coverage
  const invalidReasonCount = Object.values(raw.invalid_reason_counts).reduce(
    (sum, count) => sum + count,
    0,
  )
  if (
    raw.expected_count !== raw.observed_count + raw.missing_count
    || raw.missing_count
      !== raw.missing_state_counts.source_unavailable + raw.missing_state_counts.parse_failed
    || raw.present_count !== raw.observed_count + raw.missing_state_counts.parse_failed
    || invalidReasonCount !== raw.missing_state_counts.parse_failed
  ) {
    throw new Error('P0 原始 UPDATE 覆盖、存在性与缺口分类不闭合')
  }
  if (metrics.bgp_update_record_count.expected_sample_count !== raw.expected_count) {
    throw new Error('P0 UPDATE 指标与原始制品覆盖的预期槽数不一致')
  }
  for (const metricName of [
    'bgp_announce_record_count',
    'bgp_withdraw_record_count',
    'bgp_update_record_count',
    'bgp_withdraw_ratio',
  ] as const) {
    const metric = metrics[metricName]
    const stateCounts = metric.points.reduce<Record<string, number>>((counts, point) => {
      counts[point.value_state] = (counts[point.value_state] ?? 0) + 1
      return counts
    }, {})
    if (
      metric.source_observed_sample_count !== raw.observed_count
      || metric.coverage.source_gap_sample_count !== raw.missing_count
      || (stateCounts.source_unavailable ?? 0) !== raw.missing_state_counts.source_unavailable
      || (stateCounts.parse_failed ?? 0) !== raw.missing_state_counts.parse_failed
      || (stateCounts.processing_gap ?? 0) !== metric.coverage.processing_gap_sample_count
    ) {
      throw new Error(`P0 指标 ${metricName} 与原始缺口分类不闭合`)
    }
  }

  // 历史事实仅在 P0 身份与指标都闭合后读取，并与固定窗口末日明确隔离。
  const finalDay = fixedProfileFinalDayRange(status.profile)
  const [dashboard, events] = await Promise.allSettled([
    dependencies.getDashboard(finalDay),
    dependencies.getEvents(),
  ])

  return { status, metrics, dashboard, events }
}
