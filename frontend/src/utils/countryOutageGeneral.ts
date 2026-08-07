import type {
  CountryOutageGeneralAffectedAs,
  CountryOutageGeneralAffectedAsPage,
  CountryOutageGeneralMetadata,
  CountryOutageGeneralOverview,
  CountryOutageGeneralPageModel,
  CountryOutageGeneralPathDownstream,
  CountryOutageGeneralPathDownstreamPage,
  CountryOutageGeneralResolution,
  CountryOutageGeneralSeries,
  CountryOutageGeneralTrackKey,
} from '@/types/api'
import { isRecord } from '@/utils/normalize'

const TRACK_KEYS: CountryOutageGeneralTrackKey[] = [
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
]

const metadataKeys: Array<keyof CountryOutageGeneralMetadata> = [
  'revision',
  'publication_id',
  'publication_state',
  'observation_state',
  'data_mode',
  'data_through',
  'is_final_in_data_range',
  'lifecycle_state',
  'quality_state',
  'missing_slot_count',
  'collector_id',
  'incident_id',
  'cohort_id',
  'window_start_utc',
  'window_end_utc',
]

function fail(message: string): never {
  throw new Error(`国家中断页面数据无效：${message}`)
}

function text(value: unknown, field: string): string {
  if (typeof value !== 'string' || !value) fail(`${field} 缺失`)
  return value
}

function integer(value: unknown, field: string, minimum = 0): number {
  if (!Number.isInteger(value) || (value as number) < minimum) {
    fail(`${field} 不是有效整数`)
  }
  return value as number
}

function nullableText(value: unknown, field: string): string | null {
  if (value === null) return null
  return text(value, field)
}

function assertMetadata(value: Record<string, unknown>): CountryOutageGeneralMetadata {
  const expected = {
    publication_state: 'published',
    observation_state: 'evidence_complete',
    data_mode: 'replay',
    quality_state: 'complete',
    missing_slot_count: 0,
    collector_id: 'rrc25',
  }
  for (const [key, expectedValue] of Object.entries(expected)) {
    if (value[key] !== expectedValue) fail(`${key} 与完整读模型不一致`)
  }
  if (typeof value.is_final_in_data_range !== 'boolean') {
    fail('is_final_in_data_range 缺失')
  }
  integer(value.revision, 'revision', 1)
  for (const key of [
    'publication_id', 'data_through', 'lifecycle_state', 'incident_id',
    'cohort_id', 'window_start_utc', 'window_end_utc',
  ]) text(value[key], key)
  if (value.data_through !== value.window_end_utc) {
    fail('事件窗口终点与结果终点不一致')
  }
  return value as unknown as CountryOutageGeneralMetadata
}

function assertSameRelease(
  expected: CountryOutageGeneralMetadata,
  actual: CountryOutageGeneralMetadata,
  label: string,
) {
  for (const key of metadataKeys) {
    if (expected[key] !== actual[key]) fail(`${label} 的 ${key} 与事件解析不一致`)
  }
}

function normalizeResolution(value: unknown): CountryOutageGeneralResolution {
  if (!isRecord(value) || value.schema_version !== 'country_outage_general_resolution_v1') {
    fail('事件解析版本不受支持')
  }
  assertMetadata(value)
  if (value.event_type !== 'country_outage') fail('事件类型不一致')
  text(value.legacy_reference, 'legacy_reference')
  text(value.country_code, 'country_code')
  integer(value.latest_revision, 'latest_revision', 1)
  if (!isRecord(value.capabilities)) fail('页面能力缺失')
  return value as unknown as CountryOutageGeneralResolution
}

function normalizeOverview(value: unknown): CountryOutageGeneralOverview {
  if (!isRecord(value) || value.schema_version !== 'country_outage_general_overview_v1') {
    fail('概览版本不受支持')
  }
  assertMetadata(value)
  if (!isRecord(value.event) || !isRecord(value.cohort)) fail('概览事件或固定前缀集合缺失')
  if (!isRecord(value.current) || !isRecord(value.peaks)) fail('概览当前值或峰值缺失')
  if (!isRecord(value.capabilities)) fail('概览能力缺失')
  if (value.semantic_boundary !== 'rrc25_control_plane_observation_not_user_impact_or_cause') {
    fail('概览事实边界冲突')
  }
  integer(value.state_point_count, 'state_point_count', 1)
  integer(value.affected_as_count, 'affected_as_count')
  integer(value.route_interrupted_as_count, 'route_interrupted_as_count')
  integer(value.path_downstream_relation_count, 'path_downstream_relation_count')
  integer(value.concurrent_path_downstream_relation_count, 'concurrent_path_downstream_relation_count')
  for (const key of TRACK_KEYS) integer(value.current[key], `current.${key}`)
  return value as unknown as CountryOutageGeneralOverview
}

function normalizeSeries(value: unknown): CountryOutageGeneralSeries {
  if (!isRecord(value) || value.schema_version !== 'country_outage_general_series_v1') {
    fail('趋势版本不受支持')
  }
  assertMetadata(value)
  const pointCount = integer(value.point_count, 'point_count', 1)
  if (!Array.isArray(value.timestamps) || value.timestamps.length !== pointCount) {
    fail('趋势时间轴人口冲突')
  }
  if (!isRecord(value.tracks) || !isRecord(value.track_definitions)) {
    fail('趋势轨道缺失')
  }
  for (const timestamp of value.timestamps) text(timestamp, 'timestamps[]')
  if (Object.keys(value.tracks).sort().join('|') !== [...TRACK_KEYS].sort().join('|')) {
    fail('趋势轨道集合冲突')
  }
  for (const key of TRACK_KEYS) {
    const track = value.tracks[key]
    if (!Array.isArray(track) || track.length !== pointCount) fail(`${key} 人口冲突`)
    track.forEach((item, index) => integer(item, `${key}[${index}]`))
  }
  return value as unknown as CountryOutageGeneralSeries
}

export function normalizeCountryOutageGeneralPage(
  resolutionValue: unknown,
  overviewValue: unknown,
  seriesValue: unknown,
): CountryOutageGeneralPageModel {
  const resolution = normalizeResolution(resolutionValue)
  const overview = normalizeOverview(overviewValue)
  const series = normalizeSeries(seriesValue)
  assertSameRelease(resolution, overview, '概览')
  assertSameRelease(resolution, series, '趋势')
  if (
    overview.event.legacy_reference !== resolution.legacy_reference
    || overview.event.country_code !== resolution.country_code
    || overview.state_point_count !== series.point_count
    || overview.cohort.cohort_id !== resolution.cohort_id
  ) fail('事件、国家、固定前缀集合或状态点人口冲突')
  return { resolution, overview, series }
}

function normalizeAffectedAs(value: unknown): CountryOutageGeneralAffectedAs {
  if (!isRecord(value)) fail('受影响 AS 行无效')
  integer(value.rank, 'rank', 1)
  integer(value.asn, 'asn')
  nullableText(value.as_name, 'as_name')
  nullableText(value.organization, 'organization')
  nullableText(value.nature, 'nature')
  if (!['affected', 'route_interrupted'].includes(String(value.event_classification))) {
    fail('AS 分类无效')
  }
  for (const key of [
    'fixed_prefix_count', 'peak_partial_prefix_count', 'peak_complete_prefix_count',
    'peak_invisible_direction_count', 'path_downstream_asn_count',
    'concurrent_downstream_asn_count',
  ]) integer(value[key], key, key === 'fixed_prefix_count' ? 1 : 0)
  return value as unknown as CountryOutageGeneralAffectedAs
}

export function normalizeCountryOutageGeneralAffectedAsPage(
  value: unknown,
  expected: CountryOutageGeneralMetadata,
): CountryOutageGeneralAffectedAsPage {
  if (!isRecord(value) || value.schema_version !== 'country_outage_general_affected_as_page_v1') {
    fail('受影响 AS 分页版本不受支持')
  }
  const metadata = assertMetadata(value)
  assertSameRelease(expected, metadata, '受影响 AS 分页')
  const pageSize = integer(value.page_size, 'page_size', 1)
  if (pageSize > 60) fail('受影响 AS 分页超过 60 条')
  if (!Array.isArray(value.items) || value.items.length > pageSize) fail('受影响 AS 分页人口冲突')
  value.items.forEach(normalizeAffectedAs)
  integer(value.page, 'page', 1)
  integer(value.page_count, 'page_count', 1)
  integer(value.total, 'total')
  return value as unknown as CountryOutageGeneralAffectedAsPage
}

function normalizePathRow(value: unknown): CountryOutageGeneralPathDownstream {
  if (!isRecord(value)) fail('路径关联行无效')
  for (const key of ['affected_asn', 'downstream_asn']) integer(value[key], key)
  for (const key of [
    'observed_path_count', 'associated_fixed_prefix_count', 'independent_direction_count',
    'route_observation_count',
  ]) integer(value[key], key, 1)
  for (const key of [
    'concurrent_state_point_count', 'peak_concurrent_interrupted_prefix_count',
    'peak_concurrent_ipv4_address_count', 'peak_concurrent_ipv6_slash48_count',
  ]) integer(value[key], key)
  if (!Array.isArray(value.path_samples) || value.path_samples.length < 1 || value.path_samples.length > 3) {
    fail('路径样本必须为 1 至 3 条')
  }
  for (const sample of value.path_samples) {
    if (!isRecord(sample)) fail('路径样本无效')
    text(sample.prefix, 'path_samples.prefix')
    text(sample.as_path_canonical, 'path_samples.as_path_canonical')
  }
  if (value.relationship_semantics !== 'observed_ordered_rrc25_path_association_not_dependency_or_cause') {
    fail('路径关联事实边界冲突')
  }
  return value as unknown as CountryOutageGeneralPathDownstream
}

export function normalizeCountryOutageGeneralPathDownstreamPage(
  value: unknown,
  expected: CountryOutageGeneralMetadata,
): CountryOutageGeneralPathDownstreamPage {
  if (!isRecord(value) || value.schema_version !== 'country_outage_general_path_downstream_page_v1') {
    fail('路径关联分页版本不受支持')
  }
  const metadata = assertMetadata(value)
  assertSameRelease(expected, metadata, '路径关联分页')
  const pageSize = integer(value.page_size, 'page_size', 1)
  if (pageSize > 60) fail('路径关联分页超过 60 条')
  if (!Array.isArray(value.items) || value.items.length > pageSize) fail('路径关联分页人口冲突')
  value.items.forEach(normalizePathRow)
  integer(value.page, 'page', 1)
  integer(value.page_count, 'page_count', 1)
  integer(value.total, 'total')
  return value as unknown as CountryOutageGeneralPathDownstreamPage
}
