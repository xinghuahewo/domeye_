<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'

import AsnDurationChart, { type DurationSeries } from '@/components/AsnDurationChart.vue'
import AsnStateHeatmap from '@/components/AsnStateHeatmap.vue'
import ObservationChart, {
  type ObservationChartMarker,
  type ObservationChartSeries,
} from '@/components/ObservationChart.vue'
import type {
  EventObservation,
  EventObservationExtreme,
  EventObservationResourcePoint,
} from '@/types/api'
import {
  createDurationBuckets,
  createEventObservationPresentation,
  formatSlotDuration,
} from '@/utils/eventObservationTemplate'

const props = defineProps<{
  observation: EventObservation
}>()

const presentation = computed(() => createEventObservationPresentation(props.observation))

type ResourceMetric =
  | 'ipv4_24_equivalent_count'
  | 'ipv6_48_equivalent_count'
  | 'ipv4_address_count'
type ResourceDeltaMetric =
  | 'ipv4_24_equivalent_delta'
  | 'ipv6_48_equivalent_delta'
  | 'ipv4_address_delta'
type MessageScope = 'country' | 'collector'
type AddressFamilyFilter = 'all' | 'ipv4' | 'ipv6' | 'dual'
type AsnStateFilter = 'all' | 'partial' | 'invisible' | 'unknown'
type AsnSort = 'invisible' | 'partial' | 'prefix_vp' | 'asn'

interface ResourceOption {
  key: ResourceMetric
  deltaKey: ResourceDeltaMetric
  label: string
  shortLabel: string
  unit: string
  color: string
}

const resourceOptions: ResourceOption[] = [
  {
    key: 'ipv4_24_equivalent_count',
    deltaKey: 'ipv4_24_equivalent_delta',
    label: 'IPv4 /24 等价资源块',
    shortLabel: 'IPv4 /24 等价',
    unit: '/24 等价资源块',
    color: '#1261a6',
  },
  {
    key: 'ipv6_48_equivalent_count',
    deltaKey: 'ipv6_48_equivalent_delta',
    label: 'IPv6 /48 等价资源块',
    shortLabel: 'IPv6 /48 等价',
    unit: '/48 等价资源块',
    color: '#16856f',
  },
  {
    key: 'ipv4_address_count',
    deltaKey: 'ipv4_address_delta',
    label: 'IPv4 地址资源量',
    shortLabel: 'IPv4 地址量',
    unit: 'IPv4 地址',
    color: '#7756a3',
  },
]

const selectedResource = ref<ResourceMetric>('ipv4_24_equivalent_count')
const messageScope = ref<MessageScope>(
  props.observation.resource_series.length ? 'country' : 'collector',
)
const asnQuery = ref('')
const addressFamilyFilter = ref<AddressFamilyFilter>('all')
const asnStateFilter = ref<AsnStateFilter>('all')
const asnSort = ref<AsnSort>('invisible')
const asnPage = ref(1)
const asnPageSize = 60

const resourceOption = computed(
  () => resourceOptions.find((item) => item.key === selectedResource.value) ?? resourceOptions[0]!,
)

function formatNumber(value: number | null | undefined, maximumFractionDigits = 3) {
  if (typeof value !== 'number') return '缺失'
  return value.toLocaleString('zh-CN', { maximumFractionDigits })
}

function humanizeObservationTerm(value: string) {
  return value
    .replaceAll('Prefix×VP', '路由观测关系')
    .replaceAll('PREFIX×VP', '路由观测关系')
}

function formatSigned(value: number | null | undefined, unit = '') {
  if (typeof value !== 'number') return '缺失'
  return `${value > 0 ? '+' : ''}${formatNumber(value)}${unit}`
}

function formatPercent(value: number | null | undefined) {
  if (typeof value !== 'number') return '缺失'
  return `${formatNumber(value * 100, 2)}%`
}

function timeLabel(value: string | null | undefined, withDate = false) {
  if (!value) return '未知'
  const normalized = value.replace(' ', 'T')
  const date = normalized.slice(0, 10)
  const time = normalized.slice(11, 16)
  return withDate ? `${date} ${time}` : time
}

function metricExtreme(
  metric: string,
  mode: 'min' | 'max',
  resource = false,
): EventObservationExtreme | null {
  const source = resource
    ? props.observation.resource_metric_extrema
    : props.observation.metric_extrema
  return source[metric]?.[mode] ?? null
}

function extremaMarker(
  extreme: EventObservationExtreme | null,
  label: string,
  unit: string,
  color?: string,
  scale = 1,
): ObservationChartMarker | null {
  if (!extreme) return null
  return {
    time: extreme.observed_at_local,
    label: `${timeLabel(extreme.observed_at_local)} · ${label} · ${formatNumber(extreme.value * scale)}${unit}`,
    color,
  }
}

function presentMarkers(
  markers: Array<ObservationChartMarker | null>,
): ObservationChartMarker[] {
  return markers.filter((item): item is ObservationChartMarker => item !== null)
}

const mainSeries = computed<ObservationChartSeries[]>(() => [
  {
    name: '可见路由观测关系',
    color: '#0b5cab',
    data: props.observation.series.map((point) => [
      point.observed_at_local,
      point.visible_prefix_vp_count,
    ]),
    area: true,
  },
  {
    name: '不可见路由观测关系',
    color: '#d96c0b',
    data: props.observation.series.map((point) => [
      point.observed_at_local,
      point.invisible_prefix_vp_count,
    ]),
  },
])

const mainMarkers = computed<ObservationChartMarker[]>(() =>
  [
    ...props.observation.annotations
    .filter((annotation) => (
      annotation.metric === 'visible_prefix_vp_count'
      && annotation.observed_at_local
    ))
    .map((annotation) => ({
      time: annotation.observed_at_local!,
      label: `${timeLabel(annotation.observed_at_local)} · ${humanizeObservationTerm(annotation.label)} · ${formatNumber(annotation.value)} ${humanizeObservationTerm(annotation.unit)}`,
      color: annotation.kind === 'rule_first_met' ? '#7756a3' : '#d96c0b',
    })),
    ...presentMarkers([
      extremaMarker(
        metricExtreme('visible_prefix_vp_count', 'max'),
        '可见路由观测关系窗口最大值',
        ' 条关系',
        '#16856f',
      ),
    ]),
  ],
)

const asnStateSeries = computed<ObservationChartSeries[]>(() => [
  {
    name: '可见 origin ASN',
    color: '#0b5cab',
    data: props.observation.series.map((point) => [
      point.observed_at_local,
      point.visible_origin_asn_count,
    ]),
  },
  {
    name: '全可见 ASN',
    color: '#16856f',
    data: props.observation.series.map((point) => [
      point.observed_at_local,
      point.fully_visible_asn_count,
    ]),
  },
  {
    name: '部分可见 ASN',
    color: '#d96c0b',
    data: props.observation.series.map((point) => [
      point.observed_at_local,
      point.partially_visible_asn_count,
    ]),
  },
  {
    name: '全不可见 ASN',
    color: '#7756a3',
    data: props.observation.series.map((point) => [
      point.observed_at_local,
      point.fully_invisible_asn_count,
    ]),
  },
])

const asnStateMarkers = computed(() => presentMarkers([
  ...props.observation.annotations
    .filter((annotation) => (
      annotation.kind === 'rule_first_met'
      && annotation.observed_at_local
    ))
    .map((annotation) => ({
      time: annotation.observed_at_local!,
      label: `${timeLabel(annotation.observed_at_local)} · ${annotation.label} · ${formatNumber(annotation.value)} ${annotation.unit}`,
      color: '#7756a3',
    })),
  extremaMarker(
    metricExtreme('fully_visible_asn_count', 'min'),
    '全可见 ASN 窗口最小值',
    ' ASN',
  ),
  extremaMarker(
    metricExtreme('partially_visible_asn_count', 'max'),
    '部分可见 ASN 窗口最大值',
    ' ASN',
    '#d96c0b',
  ),
  extremaMarker(
    metricExtreme('fully_invisible_asn_count', 'max'),
    '全不可见 ASN 窗口最大值',
    ' ASN',
    '#7756a3',
  ),
]))

const resourceSeries = computed<ObservationChartSeries[]>(() => [{
  name: resourceOption.value.label,
  color: resourceOption.value.color,
  data: props.observation.resource_series.map((point) => [
    point.observed_at_local,
    point[resourceOption.value.key],
  ]),
  area: true,
}])

const resourceDeltaSeries = computed<ObservationChartSeries[]>(() => [{
  name: `${resourceOption.value.shortLabel}单槽差值`,
  color: '#16856f',
  type: 'bar',
  data: props.observation.resource_series.map((point) => [
    point.observed_at_local,
    point[resourceOption.value.deltaKey],
  ]),
}])

const resourceMarkers = computed(() => presentMarkers([
  extremaMarker(
    metricExtreme(resourceOption.value.key, 'min', true),
    `${resourceOption.value.shortLabel}窗口最小值`,
    ` ${resourceOption.value.unit}`,
  ),
  extremaMarker(
    metricExtreme(resourceOption.value.key, 'max', true),
    `${resourceOption.value.shortLabel}窗口最大值`,
    ` ${resourceOption.value.unit}`,
    '#16856f',
  ),
]))

const resourceDeltaMarkers = computed(() => presentMarkers([
  extremaMarker(
    metricExtreme(resourceOption.value.deltaKey, 'min', true),
    `${resourceOption.value.shortLabel}最大单槽下降`,
    ` ${resourceOption.value.unit}/${presentation.value.intervalLabel}`,
  ),
  extremaMarker(
    metricExtreme(resourceOption.value.deltaKey, 'max', true),
    `${resourceOption.value.shortLabel}最大单槽上升`,
    ` ${resourceOption.value.unit}/${presentation.value.intervalLabel}`,
    '#16856f',
  ),
]))

const prefixDeltaSeries = computed<ObservationChartSeries[]>(() => [{
  name: '可见路由观测关系单槽差值',
  color: '#16856f',
  type: 'bar',
  data: props.observation.series.map((point) => [
    point.observed_at_local,
    point.visible_prefix_vp_delta,
  ]),
}])

const ratioDeltaSeries = computed<ObservationChartSeries[]>(() => [{
  name: '可见率单槽变化',
  color: '#16856f',
  type: 'bar',
  data: props.observation.series.map((point) => [
    point.observed_at_local,
    point.visible_prefix_vp_ratio_delta_pp,
  ]),
}])

const asnDeltaSeries = computed<ObservationChartSeries[]>(() => [{
  name: '可见 origin ASN 单槽差值',
  color: '#16856f',
  type: 'bar',
  data: props.observation.series.map((point) => [
    point.observed_at_local,
    point.visible_origin_asn_delta,
  ]),
}])

const prefixDeltaMarkers = computed(() => presentMarkers([
  extremaMarker(
    metricExtreme('visible_prefix_vp_delta', 'min'),
    '可见路由观测关系最大单槽下降',
    ` 条关系/${presentation.value.intervalLabel}`,
  ),
  extremaMarker(
    metricExtreme('visible_prefix_vp_delta', 'max'),
    '可见路由观测关系最大单槽上升',
    ` 条关系/${presentation.value.intervalLabel}`,
    '#16856f',
  ),
]))

const ratioDeltaMarkers = computed(() => presentMarkers([
  extremaMarker(
    metricExtreme('visible_prefix_vp_ratio_delta_pp', 'min'),
    '可见率最大单槽下降',
    ` 个百分点/${presentation.value.intervalLabel}`,
  ),
  extremaMarker(
    metricExtreme('visible_prefix_vp_ratio_delta_pp', 'max'),
    '可见率最大单槽上升',
    ` 个百分点/${presentation.value.intervalLabel}`,
    '#16856f',
  ),
]))

const asnDeltaMarkers = computed(() => presentMarkers([
  extremaMarker(
    metricExtreme('visible_origin_asn_delta', 'min'),
    '可见 origin ASN 最大单槽下降',
    ` ASN/${presentation.value.intervalLabel}`,
  ),
  extremaMarker(
    metricExtreme('visible_origin_asn_delta', 'max'),
    '可见 origin ASN 最大单槽上升',
    ` ASN/${presentation.value.intervalLabel}`,
    '#16856f',
  ),
]))

const messagePoints = computed(() => (
  messageScope.value === 'country'
    ? props.observation.resource_series
    : props.observation.series
))

function messageValue(
  point: EventObservationResourcePoint | EventObservation['series'][number],
  key: 'announce_count' | 'withdraw_count' | 'update_total' | 'withdraw_ratio',
) {
  return point[key]
}

const messageSeries = computed<ObservationChartSeries[]>(() => [
  {
    name: 'ANNOUNCE',
    color: '#0b5cab',
    data: messagePoints.value.map((point) => [
      point.observed_at_local,
      messageValue(point, 'announce_count'),
    ]),
  },
  {
    name: 'WITHDRAW',
    color: '#d96c0b',
    data: messagePoints.value.map((point) => [
      point.observed_at_local,
      messageValue(point, 'withdraw_count'),
    ]),
  },
  {
    name: 'UPDATE 总量',
    color: '#4b5967',
    data: messagePoints.value.map((point) => [
      point.observed_at_local,
      messageValue(point, 'update_total'),
    ]),
  },
])

const messageRatioSeries = computed<ObservationChartSeries[]>(() => [{
  name: 'WITHDRAW / UPDATE',
  color: '#7756a3',
  data: messagePoints.value.map((point) => {
    const ratio = messageValue(point, 'withdraw_ratio')
    return [point.observed_at_local, ratio === null ? null : ratio * 100]
  }),
  area: true,
}])

const messageDeltaSeries = computed<ObservationChartSeries[]>(() => [
  {
    name: 'ANNOUNCE 单槽差值',
    color: '#16856f',
    type: 'bar',
    data: messagePoints.value.map((point) => [
      point.observed_at_local,
      point.announce_delta,
    ]),
  },
  {
    name: 'WITHDRAW 单槽差值',
    color: '#d96c0b',
    type: 'bar',
    data: messagePoints.value.map((point) => [
      point.observed_at_local,
      point.withdraw_delta,
    ]),
  },
])

const messageExtremaSource = computed(
  () => messageScope.value === 'country'
    ? props.observation.resource_metric_extrema
    : props.observation.metric_extrema,
)

function currentMessageExtreme(metric: string, mode: 'min' | 'max') {
  return messageExtremaSource.value[metric]?.[mode] ?? null
}

const messageMarkers = computed(() => presentMarkers([
  extremaMarker(
    currentMessageExtreme('announce_count', 'max'),
    'ANNOUNCE 窗口峰值',
    ` 条/${presentation.value.intervalLabel}`,
  ),
  extremaMarker(
    currentMessageExtreme('withdraw_count', 'max'),
    'WITHDRAW 窗口峰值',
    ` 条/${presentation.value.intervalLabel}`,
    '#d96c0b',
  ),
  extremaMarker(
    currentMessageExtreme('update_total', 'max'),
    'UPDATE 总量窗口峰值',
    ` 条/${presentation.value.intervalLabel}`,
    '#4b5967',
  ),
]))

const messageRatioMarkers = computed(() => presentMarkers([
  extremaMarker(currentMessageExtreme('withdraw_ratio', 'max'), 'WITHDRAW 占比窗口峰值', '%', '#7756a3', 100),
]))

const messageDeltaMarkers = computed(() => presentMarkers([
  extremaMarker(
    currentMessageExtreme('announce_delta', 'min'),
    'ANNOUNCE 最大单槽下降',
    ` 条/${presentation.value.intervalLabel}`,
  ),
  extremaMarker(
    currentMessageExtreme('announce_delta', 'max'),
    'ANNOUNCE 最大单槽上升',
    ` 条/${presentation.value.intervalLabel}`,
    '#16856f',
  ),
  extremaMarker(
    currentMessageExtreme('withdraw_delta', 'min'),
    'WITHDRAW 最大单槽下降',
    ` 条/${presentation.value.intervalLabel}`,
    '#d96c0b',
  ),
  extremaMarker(
    currentMessageExtreme('withdraw_delta', 'max'),
    'WITHDRAW 最大单槽上升',
    ` 条/${presentation.value.intervalLabel}`,
    '#7756a3',
  ),
]))

const startPoint = computed(() => props.observation.series[0])
const endPoint = computed(() => props.observation.series.at(-1))
const minPrefix = computed(() => metricExtreme('visible_prefix_vp_count', 'min'))
const minCoverageRate = computed(() => metricExtreme('visible_prefix_vp_ratio', 'min'))

const observationCompleteness = computed(
  () => `${props.observation.observation_scope.observation_count} / ${props.observation.observation_scope.expected_observation_count}`,
)

const messageScopeText = computed(() => messageScope.value === 'country'
  ? presentation.value.countryMessageDescription
  : presentation.value.collectorMessageDescription)

const displayedAuditHashes = computed(() => Object.fromEntries(
  Object.entries(props.observation.audit.verified_hashes)
    .filter(([filename]) => !/episodes|waves/i.test(filename)),
))

const addressFamilySharedMax = computed(() => Math.max(
  props.observation.cohort.ipv4_prefix_vp_count ?? 0,
  props.observation.cohort.ipv6_prefix_vp_count ?? 0,
))

const ipv4CountSeries = computed<ObservationChartSeries[]>(() => [{
  name: 'IPv4 可见路由观测关系',
  color: '#0b5cab',
  data: props.observation.series.map((point) => [
    point.observed_at_local,
    point.ipv4_visible_prefix_vp_count,
  ]),
  area: true,
}])

const ipv6CountSeries = computed<ObservationChartSeries[]>(() => [{
  name: 'IPv6 可见路由观测关系',
  color: '#16856f',
  data: props.observation.series.map((point) => [
    point.observed_at_local,
    point.ipv6_visible_prefix_vp_count,
  ]),
  area: true,
}])

const addressFamilyRatioSeries = computed<ObservationChartSeries[]>(() => [
  {
    name: 'IPv4 可见率',
    color: '#0b5cab',
    data: props.observation.series.map((point) => [
      point.observed_at_local,
      point.ipv4_visible_prefix_vp_ratio === null
        ? null
        : point.ipv4_visible_prefix_vp_ratio * 100,
    ]),
  },
  {
    name: 'IPv6 可见率',
    color: '#16856f',
    data: props.observation.series.map((point) => [
      point.observed_at_local,
      point.ipv6_visible_prefix_vp_ratio === null
        ? null
        : point.ipv6_visible_prefix_vp_ratio * 100,
    ]),
  },
])

const addressFamilyDeltaSeries = computed<ObservationChartSeries[]>(() => [
  {
    name: 'IPv4 单槽差值',
    color: '#0b5cab',
    type: 'bar',
    data: props.observation.series.map((point) => [
      point.observed_at_local,
      point.ipv4_visible_prefix_vp_delta,
    ]),
  },
  {
    name: 'IPv6 单槽差值',
    color: '#16856f',
    type: 'bar',
    data: props.observation.series.map((point) => [
      point.observed_at_local,
      point.ipv6_visible_prefix_vp_delta,
    ]),
  },
])

const ipv4CountMarkers = computed(() => presentMarkers([
  extremaMarker(
    metricExtreme('ipv4_visible_prefix_vp_count', 'min'),
    'IPv4 可见路由观测关系窗口最小值',
    ' 条关系',
  ),
  extremaMarker(
    metricExtreme('ipv4_visible_prefix_vp_count', 'max'),
    'IPv4 可见路由观测关系窗口最大值',
    ' 条关系',
    '#16856f',
  ),
]))

const ipv6CountMarkers = computed(() => presentMarkers([
  extremaMarker(
    metricExtreme('ipv6_visible_prefix_vp_count', 'min'),
    'IPv6 可见路由观测关系窗口最小值',
    ' 条关系',
  ),
  extremaMarker(
    metricExtreme('ipv6_visible_prefix_vp_count', 'max'),
    'IPv6 可见路由观测关系窗口最大值',
    ' 条关系',
    '#16856f',
  ),
]))

const addressFamilyRatioMarkers = computed(() => presentMarkers([
  extremaMarker(
    metricExtreme('ipv4_visible_prefix_vp_ratio', 'min'),
    'IPv4 可见率窗口最小值',
    '%',
    '#0b5cab',
    100,
  ),
  extremaMarker(
    metricExtreme('ipv6_visible_prefix_vp_ratio', 'min'),
    'IPv6 可见率窗口最小值',
    '%',
    '#16856f',
    100,
  ),
]))

const addressFamilyDeltaMarkers = computed(() => presentMarkers([
  extremaMarker(
    metricExtreme('ipv4_visible_prefix_vp_delta', 'min'),
    'IPv4 最大单槽下降',
    ` 条关系/${presentation.value.intervalLabel}`,
    '#0b5cab',
  ),
  extremaMarker(
    metricExtreme('ipv4_visible_prefix_vp_delta', 'max'),
    'IPv4 最大单槽上升',
    ` 条关系/${presentation.value.intervalLabel}`,
    '#4c82b1',
  ),
  extremaMarker(
    metricExtreme('ipv6_visible_prefix_vp_delta', 'min'),
    'IPv6 最大单槽下降',
    ` 条关系/${presentation.value.intervalLabel}`,
    '#16856f',
  ),
  extremaMarker(
    metricExtreme('ipv6_visible_prefix_vp_delta', 'max'),
    'IPv6 最大单槽上升',
    ` 条关系/${presentation.value.intervalLabel}`,
    '#4a9e8d',
  ),
]))

const filteredAsns = computed(() => {
  const query = asnQuery.value.trim().replace(/^AS/i, '')
  const rows = props.observation.asn_state.timelines.filter((row) => {
    if (query && !row.asn.includes(query)) return false
    if (addressFamilyFilter.value === 'ipv4' && !row.address_families.includes(4)) return false
    if (addressFamilyFilter.value === 'ipv6' && !row.address_families.includes(6)) return false
    if (
      addressFamilyFilter.value === 'dual'
      && !(row.address_families.includes(4) && row.address_families.includes(6))
    ) return false
    if (asnStateFilter.value === 'partial' && row.state_slot_counts.partially_visible === 0) return false
    if (asnStateFilter.value === 'invisible' && row.state_slot_counts.fully_invisible === 0) return false
    if (asnStateFilter.value === 'unknown' && row.state_slot_counts.unknown === 0) return false
    return true
  })

  return [...rows].sort((left, right) => {
    if (asnSort.value === 'invisible') {
      return right.longest_fully_invisible_slots - left.longest_fully_invisible_slots
        || right.state_slot_counts.fully_invisible - left.state_slot_counts.fully_invisible
        || Number(left.asn) - Number(right.asn)
    }
    if (asnSort.value === 'partial') {
      return right.longest_partially_visible_slots - left.longest_partially_visible_slots
        || right.state_slot_counts.partially_visible - left.state_slot_counts.partially_visible
        || Number(left.asn) - Number(right.asn)
    }
    if (asnSort.value === 'prefix_vp') {
      return right.baseline_prefix_vp_count - left.baseline_prefix_vp_count
        || Number(left.asn) - Number(right.asn)
    }
    return Number(left.asn) - Number(right.asn)
  })
})

const asnPageCount = computed(() => Math.max(1, Math.ceil(filteredAsns.value.length / asnPageSize)))
const displayedAsns = computed(() => {
  const start = (asnPage.value - 1) * asnPageSize
  return filteredAsns.value.slice(start, start + asnPageSize)
})
const displayedAsnRange = computed(() => {
  if (filteredAsns.value.length === 0) return '0 / 0'
  const start = (asnPage.value - 1) * asnPageSize + 1
  const end = Math.min(start + asnPageSize - 1, filteredAsns.value.length)
  return `${start}–${end} / ${filteredAsns.value.length}`
})

watch(
  [asnQuery, addressFamilyFilter, asnStateFilter, asnSort],
  () => { asnPage.value = 1 },
)

watch(asnPageCount, (pageCount) => {
  asnPage.value = Math.min(asnPage.value, pageCount)
})

function durationText(slots: number) {
  return formatSlotDuration(slots, props.observation.observation_scope.interval_seconds)
}

const durationBuckets = computed(() => createDurationBuckets(
  props.observation.observation_scope.interval_seconds,
  props.observation.observation_scope.expected_observation_count,
))
const durationCategories = computed(() => (
  durationBuckets.value.map((bucket) => bucket.label)
))

function durationDistribution(key: keyof Pick<
  EventObservation['asn_state']['timelines'][number],
  'longest_fully_visible_slots'
  | 'longest_partially_visible_slots'
  | 'longest_fully_invisible_slots'
>) {
  const counts = durationBuckets.value.map(() => 0)
  for (const row of filteredAsns.value) {
    const index = durationBuckets.value.findIndex(
      (bucket) => row[key] >= bucket.minSlots && row[key] <= bucket.maxSlots,
    )
    if (index < 0) continue
    counts[index] = (counts[index] ?? 0) + 1
  }
  return counts
}

const durationSeries = computed<DurationSeries[]>(() => [
  {
    name: '全可见',
    color: '#167c68',
    values: durationDistribution('longest_fully_visible_slots'),
  },
  {
    name: '部分可见',
    color: '#e09532',
    values: durationDistribution('longest_partially_visible_slots'),
  },
  {
    name: '全不可见',
    color: '#8c3f58',
    values: durationDistribution('longest_fully_invisible_slots'),
  },
])
</script>

<template>
  <div class="observation-page">
    <header class="observation-masthead">
      <div class="masthead-copy">
        <RouterLink class="observation-back" to="/events">← 返回异常事件</RouterLink>
        <p class="masthead-kicker">{{ presentation.mastheadKicker }}</p>
        <h1>{{ observation.event_identity.display_name }}</h1>
        <p class="masthead-id">{{ observation.event_identity.incident_id }}</p>
        <div class="masthead-tags" aria-label="观测身份标签">
          <span>{{ presentation.collectorCountLabel }}</span>
          <span>控制面数据</span>
          <span>{{ presentation.intervalTag }}</span>
          <span>{{ observation.schema_version }}</span>
        </div>
      </div>

      <dl class="scope-console">
        <div class="is-wide">
          <dt>{{ presentation.localTimeLabel }}观察窗口</dt>
          <dd>
            {{ timeLabel(observation.observation_scope.window_start_local, true) }}
            <i>→</i>
            {{ timeLabel(observation.observation_scope.window_end_local, true) }}
          </dd>
        </div>
        <div><dt>COLLECTOR</dt><dd>{{ observation.observation_scope.collector_id }}</dd></div>
        <div><dt>窗口起点唯一 VP</dt><dd>{{ formatNumber(observation.observation_scope.vantage_point_count) }}</dd></div>
        <div><dt>固定 ORIGIN ASN</dt><dd>{{ formatNumber(observation.cohort.origin_asn_count) }}</dd></div>
        <div><dt>固定路由观测关系</dt><dd>{{ formatNumber(observation.cohort.prefix_vp_count) }}</dd></div>
        <div><dt>状态点完整度</dt><dd>{{ observationCompleteness }}</dd></div>
        <div><dt>质量状态</dt><dd>{{ observation.observation_scope.quality_status }}</dd></div>
        <div><dt>最后观测</dt><dd>{{ timeLabel(observation.observation_scope.last_observation_at_local, true) }}</dd></div>
      </dl>
    </header>

    <section class="boundary-rail" aria-label="数据解释边界">
      <article><b>01</b><span>观测视角</span><strong>{{ presentation.observerScopeText }}</strong></article>
      <article><b>02</b><span>数据层面</span><strong>BGP 控制面 · 非流量/服务可用性</strong></article>
      <article><b>03</b><span>比例分母</span><strong>固定人口</strong></article>
      <article><b>04</b><span>参照范围</span><strong>正常带不可用</strong></article>
      <article><b>05</b><span>时间边界</span><strong>窗外未覆盖 · 端点只作边界</strong></article>
    </section>

    <main class="observation-main">
      <section class="chart-panel is-hero">
        <header class="panel-heading">
          <div>
            <span>01 / FIXED COHORT STATE</span>
            <h2>固定观测范围内的路由传播覆盖</h2>
          </div>
          <p>每条关系代表一个前缀在一个 BGP 观测点的路由记录 · 固定 {{ formatNumber(observation.cohort.prefix_vp_count) }} 条</p>
        </header>

        <div class="hero-metrics" aria-label="路由传播覆盖窗口数据">
          <article>
            <span>窗口起点观测值</span>
            <strong>{{ formatNumber(startPoint?.visible_prefix_vp_count) }}</strong>
            <small>覆盖率 {{ formatPercent(startPoint?.visible_prefix_vp_ratio) }} · {{ timeLabel(startPoint?.observed_at_local) }}</small>
          </article>
          <article>
            <span>窗口最小值</span>
            <strong>{{ formatNumber(minPrefix?.value) }}</strong>
            <small>覆盖率 {{ formatPercent(minCoverageRate?.value) }} · {{ timeLabel(minPrefix?.observed_at_local) }}</small>
          </article>
          <article>
            <span>窗口末观测值</span>
            <strong>{{ formatNumber(endPoint?.visible_prefix_vp_count) }}</strong>
            <small>覆盖率 {{ formatPercent(endPoint?.visible_prefix_vp_ratio) }} · {{ timeLabel(endPoint?.observed_at_local) }}</small>
          </article>
          <article>
            <span>固定人口</span>
            <strong>{{ formatNumber(observation.cohort.prefix_vp_count) }}</strong>
            <small>条固定观测关系 · 窗口内分母不变</small>
          </article>
        </div>

        <figure class="chart-figure">
          <ObservationChart
            :series="mainSeries"
            :markers="mainMarkers"
            :timezone="observation.observation_scope.timezone"
            unit=" 条关系"
            :denominator="`固定路由观测关系 ${formatNumber(observation.cohort.prefix_vp_count)} 条；每条关系代表一个前缀在一个 BGP 观测点的路由记录，缺失点保持断开。`"
            :height="360"
          />
          <figcaption class="marker-register">
            <span v-for="marker in mainMarkers" :key="marker.label">
              <i :style="{ backgroundColor: marker.color }"></i>{{ marker.label }}
            </span>
          </figcaption>
        </figure>

        <figure class="chart-figure asn-overview">
          <div class="figure-heading">
            <strong>固定 origin ASN 可见状态数量</strong>
            <span>
              可见 origin ASN、全可见、部分可见、全不可见 ·
              固定人口 {{ formatNumber(observation.cohort.origin_asn_count) }} ASN
            </span>
          </div>
          <ObservationChart
            :series="asnStateSeries"
            :markers="asnStateMarkers"
            :timezone="observation.observation_scope.timezone"
            unit=" ASN"
            :denominator="`固定人口 ${formatNumber(observation.cohort.origin_asn_count)} origin ASN；状态名称同时提供文字图例。`"
            :height="280"
          />
          <figcaption class="marker-register is-compact">
            <span v-for="marker in asnStateMarkers" :key="marker.label">
              <i :style="{ backgroundColor: marker.color }"></i>{{ marker.label }}
            </span>
          </figcaption>
        </figure>
      </section>

      <section v-if="observation.resource_series.length" class="chart-panel">
        <header class="panel-heading has-controls">
          <div>
            <span>02 / COUNTRY RESOURCE LEDGER</span>
            <h2>国家级 IP / 前缀等价资源时序</h2>
          </div>
          <div class="metric-switch" aria-label="切换国家资源指标">
            <button
              v-for="option in resourceOptions"
              :key="option.key"
              type="button"
              :class="{ 'is-active': selectedResource === option.key }"
              :aria-pressed="selectedResource === option.key"
              @click="selectedResource = option.key"
            >
              {{ option.shortLabel }}
            </button>
          </div>
        </header>
        <p class="metric-boundary">
          Core BGPFeature 国家聚合 · 规范化前缀先去重，再换算等价资源量；
          <strong>不是唯一 BGP 前缀条目数，也不与路由观测关系数直接相减。</strong>
        </p>
        <div class="resource-grid">
          <figure class="chart-figure">
            <div class="figure-heading">
              <strong>{{ resourceOption.label }}</strong>
              <span>{{ resourceOption.unit }} · {{ presentation.intervalLabel }}状态点</span>
            </div>
            <ObservationChart
              :series="resourceSeries"
              :markers="resourceMarkers"
              :timezone="observation.observation_scope.timezone"
              :unit="` ${resourceOption.unit}`"
              :denominator="`${presentation.countryResourceDenominator}；单位为 ${resourceOption.unit}。`"
              :height="280"
            />
            <figcaption class="marker-register is-compact">
              <span v-for="marker in resourceMarkers" :key="marker.label">
                <i :style="{ backgroundColor: marker.color }"></i>{{ marker.label }}
              </span>
            </figcaption>
          </figure>
          <figure class="chart-figure">
            <div class="figure-heading">
              <strong>相邻状态点差值</strong>
              <span>正负共用零线 · 当前观察窗口内</span>
            </div>
            <ObservationChart
              :series="resourceDeltaSeries"
              :markers="resourceDeltaMarkers"
              :timezone="observation.observation_scope.timezone"
              :unit="` ${resourceOption.unit}`"
              value-kind="signed"
              :show-zero="true"
              :denominator="`当前值减前一 ${presentation.intervalLabel}状态点；窗口首点为缺失。`"
              :height="280"
            />
            <figcaption class="marker-register is-compact">
              <span v-for="marker in resourceDeltaMarkers" :key="marker.label">
                <i :style="{ backgroundColor: marker.color }"></i>{{ marker.label }}
              </span>
            </figcaption>
          </figure>
        </div>
      </section>

      <section class="chart-panel">
        <header class="panel-heading">
          <div>
            <span>03 / INTERVAL DELTAS</span>
            <h2>固定人口状态的相邻 {{ presentation.intervalLabel }}变化</h2>
          </div>
          <p>当前值减前一槽 · 正值向上、负值向下 · 首个状态点为缺失</p>
        </header>
        <div class="delta-grid">
          <figure class="chart-figure">
            <div class="figure-heading">
              <strong>可见路由观测关系</strong>
              <span>条关系 / {{ presentation.intervalLabel }}</span>
            </div>
            <ObservationChart
              :series="prefixDeltaSeries"
              :markers="prefixDeltaMarkers"
              :timezone="observation.observation_scope.timezone"
              unit=" 条关系"
              value-kind="signed"
              :show-zero="true"
              :denominator="`固定路由观测关系 ${formatNumber(observation.cohort.prefix_vp_count)} 条`"
              :height="250"
            />
            <figcaption class="marker-register is-compact">
              <span v-for="marker in prefixDeltaMarkers" :key="marker.label">
                <i :style="{ backgroundColor: marker.color }"></i>{{ marker.label }}
              </span>
            </figcaption>
          </figure>
          <figure class="chart-figure">
            <div class="figure-heading">
              <strong>路由传播覆盖率</strong>
              <span>百分点 / {{ presentation.intervalLabel }}</span>
            </div>
            <ObservationChart
              :series="ratioDeltaSeries"
              :markers="ratioDeltaMarkers"
              :timezone="observation.observation_scope.timezone"
              unit=" 个百分点"
              value-kind="signed"
              :show-zero="true"
              :denominator="`可见路由观测关系 ÷ 固定观测关系 ${formatNumber(observation.cohort.prefix_vp_count)} 条`"
              :height="250"
            />
            <figcaption class="marker-register is-compact">
              <span v-for="marker in ratioDeltaMarkers" :key="marker.label">
                <i :style="{ backgroundColor: marker.color }"></i>{{ marker.label }}
              </span>
            </figcaption>
          </figure>
          <figure class="chart-figure">
            <div class="figure-heading">
              <strong>可见 origin ASN</strong>
              <span>ASN / {{ presentation.intervalLabel }}</span>
            </div>
            <ObservationChart
              :series="asnDeltaSeries"
              :markers="asnDeltaMarkers"
              :timezone="observation.observation_scope.timezone"
              unit=" ASN"
              value-kind="signed"
              :show-zero="true"
              :denominator="`固定人口 ${formatNumber(observation.cohort.origin_asn_count)} origin ASN`"
              :height="250"
            />
            <figcaption class="marker-register is-compact">
              <span v-for="marker in asnDeltaMarkers" :key="marker.label">
                <i :style="{ backgroundColor: marker.color }"></i>{{ marker.label }}
              </span>
            </figcaption>
          </figure>
        </div>
      </section>

      <section class="chart-panel">
        <header class="panel-heading has-controls">
          <div>
            <span>04 / BGP UPDATE ACTIVITY</span>
            <h2>ANNOUNCE 与 WITHDRAW 报文活动</h2>
          </div>
          <div class="metric-switch" aria-label="切换报文统计范围">
            <button
              v-if="observation.resource_series.length"
              type="button"
              :class="{ 'is-active': messageScope === 'country' }"
              :aria-pressed="messageScope === 'country'"
              @click="messageScope = 'country'"
            >
              {{ presentation.originScopeLabel }}
            </button>
            <button
              type="button"
              :class="{ 'is-active': messageScope === 'collector' }"
              :aria-pressed="messageScope === 'collector'"
              @click="messageScope = 'collector'"
            >
              {{ presentation.collectorScopeLabel }}
            </button>
          </div>
        </header>
        <p class="metric-boundary">{{ messageScopeText }}</p>
        <div class="message-grid">
          <figure class="chart-figure is-wide">
            <div class="figure-heading">
              <strong>槽内报文数量</strong>
              <span>
                条 / {{ presentation.intervalLabel }} ·
                与其他时序图使用同一{{ presentation.localTimeLabel }}轴
              </span>
            </div>
            <ObservationChart
              :series="messageSeries"
              :markers="messageMarkers"
              :timezone="observation.observation_scope.timezone"
              unit=" 条"
              :denominator="messageScopeText"
              :height="300"
            />
            <figcaption class="marker-register is-compact">
              <span v-for="marker in messageMarkers" :key="marker.label">
                <i :style="{ backgroundColor: marker.color }"></i>{{ marker.label }}
              </span>
            </figcaption>
          </figure>
          <figure class="chart-figure">
            <div class="figure-heading">
              <strong>WITHDRAW 占 UPDATE 比例</strong>
              <span>WITHDRAW ÷ (ANNOUNCE + WITHDRAW)</span>
            </div>
            <ObservationChart
              :series="messageRatioSeries"
              :markers="messageRatioMarkers"
              :timezone="observation.observation_scope.timezone"
              unit="%"
              value-kind="percent"
              :denominator="messageScopeText"
              :height="230"
            />
            <figcaption class="marker-register is-compact">
              <span v-for="marker in messageRatioMarkers" :key="marker.label">
                <i :style="{ backgroundColor: marker.color }"></i>{{ marker.label }}
              </span>
            </figcaption>
          </figure>
          <figure class="chart-figure">
            <div class="figure-heading">
              <strong>报文单槽数量变化</strong>
              <span>当前槽减前一槽 · 条 / {{ presentation.intervalLabel }}</span>
            </div>
            <ObservationChart
              :series="messageDeltaSeries"
              :markers="messageDeltaMarkers"
              :timezone="observation.observation_scope.timezone"
              unit=" 条"
              value-kind="signed"
              :show-zero="true"
              :denominator="messageScopeText"
              :height="230"
            />
            <figcaption class="marker-register is-compact">
              <span v-for="marker in messageDeltaMarkers" :key="marker.label">
                <i :style="{ backgroundColor: marker.color }"></i>{{ marker.label }}
              </span>
            </figcaption>
          </figure>
        </div>
      </section>

      <section
        v-if="observation.cohort.ipv4_prefix_vp_count !== null || observation.cohort.ipv6_prefix_vp_count !== null"
        class="chart-panel"
      >
        <header class="panel-heading">
          <div>
            <span>05 / ADDRESS FAMILY</span>
            <h2>IPv4 与 IPv6 路由传播覆盖对照</h2>
          </div>
          <p>
            共享{{ presentation.localTimeLabel }}轴与纵轴尺度 · IPv4 分母
            {{ formatNumber(observation.cohort.ipv4_prefix_vp_count) }} · IPv6 分母
            {{ formatNumber(observation.cohort.ipv6_prefix_vp_count) }}
          </p>
        </header>
        <p class="metric-boundary">
          两个地址族分别使用各自固定路由观测关系作为分母；数量、比例与单槽差值均为独立观测值，
          不把两个地址族的量级差异改写为判断。
        </p>
        <div class="address-family-grid">
          <figure class="chart-figure">
            <div class="figure-heading">
              <strong>IPv4 可见路由观测关系</strong>
              <span>固定分母 {{ formatNumber(observation.cohort.ipv4_prefix_vp_count) }} 条关系</span>
            </div>
            <ObservationChart
              :series="ipv4CountSeries"
              :markers="ipv4CountMarkers"
              :timezone="observation.observation_scope.timezone"
              unit=" 条关系"
              :y-max="addressFamilySharedMax"
              :denominator="`IPv4 固定路由观测关系 ${formatNumber(observation.cohort.ipv4_prefix_vp_count)} 条`"
              :height="280"
            />
            <figcaption class="marker-register is-compact">
              <span v-for="marker in ipv4CountMarkers" :key="marker.label">
                <i :style="{ backgroundColor: marker.color }"></i>{{ marker.label }}
              </span>
            </figcaption>
          </figure>
          <figure class="chart-figure">
            <div class="figure-heading">
              <strong>IPv6 可见路由观测关系</strong>
              <span>固定分母 {{ formatNumber(observation.cohort.ipv6_prefix_vp_count) }} 条关系</span>
            </div>
            <ObservationChart
              :series="ipv6CountSeries"
              :markers="ipv6CountMarkers"
              :timezone="observation.observation_scope.timezone"
              unit=" 条关系"
              :y-max="addressFamilySharedMax"
              :denominator="`IPv6 固定路由观测关系 ${formatNumber(observation.cohort.ipv6_prefix_vp_count)} 条`"
              :height="280"
            />
            <figcaption class="marker-register is-compact">
              <span v-for="marker in ipv6CountMarkers" :key="marker.label">
                <i :style="{ backgroundColor: marker.color }"></i>{{ marker.label }}
              </span>
            </figcaption>
          </figure>
          <figure class="chart-figure">
            <div class="figure-heading">
              <strong>地址族可见率</strong>
              <span>各自可见路由观测关系 ÷ 各自固定观测关系</span>
            </div>
            <ObservationChart
              :series="addressFamilyRatioSeries"
              :markers="addressFamilyRatioMarkers"
              :timezone="observation.observation_scope.timezone"
              unit="%"
              value-kind="percent"
              denominator="IPv4 与 IPv6 各使用自己的固定路由观测关系作为分母；缺失点保持断开。"
              :height="260"
            />
            <figcaption class="marker-register is-compact">
              <span v-for="marker in addressFamilyRatioMarkers" :key="marker.label">
                <i :style="{ backgroundColor: marker.color }"></i>{{ marker.label }}
              </span>
            </figcaption>
          </figure>
          <figure class="chart-figure">
            <div class="figure-heading">
              <strong>地址族单槽差值</strong>
              <span>当前状态点减前一 {{ presentation.intervalLabel }}状态点</span>
            </div>
            <ObservationChart
              :series="addressFamilyDeltaSeries"
              :markers="addressFamilyDeltaMarkers"
              :timezone="observation.observation_scope.timezone"
              unit=" 条关系"
              value-kind="signed"
              :show-zero="true"
              denominator="各地址族独立计算；窗口首点为缺失。"
              :height="260"
            />
            <figcaption class="marker-register is-compact">
              <span v-for="marker in addressFamilyDeltaMarkers" :key="marker.label">
                <i :style="{ backgroundColor: marker.color }"></i>{{ marker.label }}
              </span>
            </figcaption>
          </figure>
        </div>
      </section>

      <section v-if="observation.asn_state.timelines.length" class="chart-panel">
        <header class="panel-heading">
          <div>
            <span>06 / ASN STATE MATRIX</span>
            <h2>ASN 可见状态与连续状态时间</h2>
          </div>
          <p>
            固定 {{ formatNumber(observation.cohort.origin_asn_count) }} origin ASN ·
            {{ observation.observation_scope.observation_count }} 个 {{ presentation.intervalLabel }}状态点 ·
            筛选与排序只改变读取顺序
          </p>
        </header>

        <div class="asn-controls" aria-label="ASN 状态筛选和排序">
          <label>
            <span>ASN</span>
            <input v-model="asnQuery" type="search" inputmode="numeric" placeholder="输入 ASN" />
          </label>
          <label>
            <span>地址族</span>
            <select v-model="addressFamilyFilter">
              <option value="all">全部地址族</option>
              <option value="ipv4">包含 IPv4</option>
              <option value="ipv6">包含 IPv6</option>
              <option value="dual">IPv4 + IPv6</option>
            </select>
          </label>
          <label>
            <span>窗口内状态</span>
            <select v-model="asnStateFilter">
              <option value="all">全部状态</option>
              <option value="partial">包含部分可见槽</option>
              <option value="invisible">包含全不可见槽</option>
              <option value="unknown">包含未知槽</option>
            </select>
          </label>
          <label>
            <span>读取顺序</span>
            <select v-model="asnSort">
              <option value="invisible">全不可见最长时间</option>
              <option value="partial">部分可见最长时间</option>
              <option value="prefix_vp">固定路由观测关系规模</option>
              <option value="asn">ASN 数字顺序</option>
            </select>
          </label>
          <div class="asn-result-count">
            <span>当前范围</span>
            <strong>{{ displayedAsnRange }}</strong>
          </div>
        </div>

        <p class="metric-boundary">
          每格表示一个 ASN 在一个 {{ presentation.intervalLabel }}状态点的双栈组合状态。分页每次显示
          {{ asnPageSize }} 个 ASN，筛选结果总数始终可见；排序不代表用户规模、商业重要性、责任或因果。
        </p>

        <figure class="chart-figure heatmap-figure">
          <div class="figure-heading">
            <strong>ASN × {{ presentation.intervalLabel }}状态点</strong>
            <span>
              {{ presentation.localTimeLabel }}
              {{ timeLabel(observation.observation_scope.window_start_local) }}–{{ timeLabel(observation.observation_scope.window_end_local) }}
            </span>
          </div>
          <div class="state-legend" aria-label="ASN 状态图例">
            <span><i class="is-visible"></i><b>全可见</b><small>0</small></span>
            <span><i class="is-partial"></i><b>部分可见</b><small>1</small></span>
            <span><i class="is-invisible"></i><b>全不可见</b><small>2</small></span>
            <span><i class="is-unknown"></i><b>未知</b><small>−1</small></span>
          </div>
          <AsnStateHeatmap
            :rows="displayedAsns"
            :times="observation.asn_state.observed_at_local"
            :timezone="observation.observation_scope.timezone"
            :height="620"
          />
          <figcaption class="heatmap-pagination">
            <span>第 {{ asnPage }} / {{ asnPageCount }} 页 · {{ displayedAsnRange }}</span>
            <div>
              <button
                type="button"
                :disabled="asnPage <= 1"
                @click="asnPage -= 1"
              >
                ← 上一页
              </button>
              <button
                type="button"
                :disabled="asnPage >= asnPageCount"
                @click="asnPage += 1"
              >
                下一页 →
              </button>
            </div>
          </figcaption>
        </figure>

        <div class="asn-duration-grid">
          <figure class="chart-figure">
            <div class="figure-heading">
              <strong>最长连续状态时间分布</strong>
              <span>当前筛选范围 {{ filteredAsns.length }} ASN · 每槽 {{ presentation.intervalLabel }}</span>
            </div>
            <AsnDurationChart
              :categories="durationCategories"
              :series="durationSeries"
              :height="300"
            />
            <figcaption class="duration-note">
              每个 ASN 分别计算全可见、部分可见、全不可见的最长连续槽数，再按分钟区间计数；
              0 表示窗口内没有该状态。
            </figcaption>
          </figure>

          <div class="asn-table-shell">
            <div class="figure-heading">
              <strong>逐 ASN 连续状态时间</strong>
              <span>当前热力图页 · {{ displayedAsns.length }} ASN</span>
            </div>
            <div class="asn-table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>ASN</th>
                    <th>地址族</th>
                    <th>固定前缀条目</th>
                    <th>固定观测关系</th>
                    <th>全可见最长</th>
                    <th>部分可见最长</th>
                    <th>全不可见最长</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="row in displayedAsns" :key="row.asn">
                    <td data-label="ASN"><b>AS{{ row.asn }}</b></td>
                    <td data-label="地址族">{{ row.address_families.join(' / ') || '未知' }}</td>
                    <td data-label="固定前缀条目">{{ formatNumber(row.baseline_prefix_count) }}</td>
                    <td data-label="固定观测关系">{{ formatNumber(row.baseline_prefix_vp_count) }}</td>
                    <td data-label="全可见最长">{{ durationText(row.longest_fully_visible_slots) }}</td>
                    <td data-label="部分可见最长">{{ durationText(row.longest_partially_visible_slots) }}</td>
                    <td data-label="全不可见最长">{{ durationText(row.longest_fully_invisible_slots) }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
        </div>
      </section>

      <section class="audit-panel">
        <header class="panel-heading">
          <div>
            <span>07 / DEFINITIONS &amp; AUDIT</span>
            <h2>指标定义、数据限制与审计身份</h2>
          </div>
          <p>辅助核对区 · 默认折叠 · 不参与前台数据解读</p>
        </header>

        <details>
          <summary>
            <span>指标字典</span>
            <strong>{{ observation.metric_definitions.length }} 项定义</strong>
          </summary>
          <div class="definition-table-shell">
            <table>
              <thead>
                <tr>
                  <th>指标</th>
                  <th>对象 / 人口</th>
                  <th>单位</th>
                  <th>定义</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="definition in observation.metric_definitions" :key="definition.key">
                  <td><b>{{ definition.label }}</b><code>{{ definition.key }}</code></td>
                  <td>{{ definition.population }}</td>
                  <td>{{ definition.unit }}</td>
                  <td>{{ definition.definition }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </details>

        <details>
          <summary>
            <span>数据限制与缺失语义</span>
            <strong>{{ observation.limitations.length }} 项限制</strong>
          </summary>
          <div class="limitation-content">
            <ol>
              <li v-for="limitation in observation.limitations" :key="limitation">
                {{ limitation }}
              </li>
            </ol>
            <dl>
              <div><dt>正常带</dt><dd>{{ observation.normal_band.label }}</dd></div>
              <div><dt>原因</dt><dd>{{ observation.normal_band.reason }}</dd></div>
              <div><dt>左边界</dt><dd>{{ observation.observation_scope.left_boundary }}</dd></div>
              <div><dt>右边界</dt><dd>{{ observation.observation_scope.right_boundary }}</dd></div>
              <div><dt>缺失值</dt><dd>以“缺失”显示，折线断开；不转换为 0。</dd></div>
              <div>
                <dt>时区</dt>
                <dd>
                  {{ observation.observation_scope.timezone }}
                  <template v-if="presentation.localTimeLabel !== observation.observation_scope.timezone">
                    （{{ presentation.localTimeLabel }}）
                  </template>
                </dd>
              </div>
            </dl>
          </div>
        </details>

        <details>
          <summary>
            <span>来源与哈希身份</span>
            <strong>{{ observation.audit.quality_status }}</strong>
          </summary>
          <div class="audit-content">
            <dl>
              <div><dt>观测身份</dt><dd>{{ observation.event_identity.incident_id }}</dd></div>
              <div><dt>历史登记类型</dt><dd>{{ observation.event_identity.event_type }}</dd></div>
              <div><dt>历史引用</dt><dd>{{ observation.event_identity.legacy_reference }}</dd></div>
              <div><dt>历史登记时间</dt><dd>{{ observation.event_identity.legacy_record_time_local || '未提供' }}</dd></div>
              <div><dt>引擎版本</dt><dd>{{ observation.audit.engine_version }}</dd></div>
              <div><dt>交付目录</dt><dd>{{ observation.audit.package_directory }}</dd></div>
              <div><dt>状态文件</dt><dd>{{ observation.audit.route_state_file.filename }}</dd></div>
              <div><dt>状态行数</dt><dd>{{ formatNumber(observation.audit.route_state_file.row_count) }}</dd></div>
              <div><dt>交付哈希核验</dt><dd>{{ observation.audit.consumed_deliverable_hashes_verified ? '已核验' : '未核验' }}</dd></div>
              <div><dt>请求路径扫描状态明细</dt><dd>{{ observation.audit.route_state_file.request_path_scanned ? '是' : '否' }}</dd></div>
            </dl>
            <div class="hash-list">
              <div v-for="(hash, filename) in displayedAuditHashes" :key="filename">
                <span>{{ filename }}</span>
                <code>{{ hash }}</code>
              </div>
            </div>
          </div>
        </details>
      </section>
    </main>
  </div>
</template>

<style scoped>
.observation-page {
  --obs-ink: #172632;
  --obs-muted: #61717e;
  --obs-line: #d7dfe6;
  --obs-blue: #0b5cab;
  --obs-orange: #d96c0b;
  display: grid;
  gap: 14px;
  min-width: 0;
  color: var(--obs-ink);
}

.observation-masthead {
  position: relative;
  display: grid;
  grid-template-columns: minmax(300px, .82fr) minmax(520px, 1.18fr);
  gap: 36px;
  overflow: hidden;
  padding: 26px;
  color: #edf4f7;
  background:
    linear-gradient(100deg, rgba(14, 35, 49, .98), rgba(21, 50, 67, .96)),
    repeating-linear-gradient(90deg, transparent 0, transparent 39px, rgba(255,255,255,.04) 40px);
  border: 1px solid #203e50;
  border-radius: 4px;
  box-shadow: 0 14px 36px rgba(18, 39, 53, .14);
}

.observation-masthead::after {
  position: absolute;
  top: 0;
  right: 0;
  width: 7px;
  height: 100%;
  content: "";
  background: var(--obs-orange);
}

.observation-back {
  display: inline-block;
  margin-bottom: 18px;
  color: #9dcaef;
  font-size: 11px;
  font-weight: 700;
  text-decoration: none;
}

.masthead-kicker {
  margin: 0;
  color: #78afd0;
  font: 750 9px/1.2 var(--mono);
  letter-spacing: .12em;
}

.masthead-copy h1 {
  max-width: 560px;
  margin: 7px 0 8px;
  font: 760 clamp(27px, 3.1vw, 43px)/1.02 "DIN Alternate", "Avenir Next", "PingFang SC", sans-serif;
  letter-spacing: -.045em;
}

.masthead-id {
  margin: 0;
  color: #a8bac5;
  font: 9px/1.5 var(--mono);
  overflow-wrap: anywhere;
}

.masthead-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 18px;
}

.masthead-tags span {
  padding: 5px 7px;
  color: #c9d7df;
  border: 1px solid #486172;
  border-radius: 2px;
  font: 700 8px/1 var(--mono);
  letter-spacing: .07em;
}

.scope-console {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 1px;
  align-self: stretch;
  margin: 0;
  background: #395669;
  border: 1px solid #395669;
}

.scope-console > div {
  min-width: 0;
  padding: 11px 12px;
  background: rgba(16, 40, 55, .93);
}

.scope-console .is-wide {
  grid-column: 1 / -1;
  padding-block: 13px;
  background: rgba(10, 31, 44, .96);
}

.scope-console dt {
  margin-bottom: 5px;
  color: #7fa2b8;
  font: 700 8px/1.2 var(--mono);
  letter-spacing: .07em;
}

.scope-console dd {
  margin: 0;
  overflow-wrap: anywhere;
  color: #eef5f8;
  font: 700 12px/1.35 var(--mono);
}

.scope-console .is-wide dd {
  display: flex;
  gap: 10px;
  align-items: center;
  font-size: 13px;
}

.scope-console dd i {
  color: #d98a49;
  font-style: normal;
}

.boundary-rail {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 1px;
  overflow: hidden;
  background: #bdc9d2;
  border: 1px solid #bdc9d2;
  border-radius: 3px;
}

.boundary-rail article {
  position: relative;
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 2px 9px;
  min-width: 0;
  padding: 12px 13px;
  background: #f8fafb;
}

.boundary-rail b {
  grid-row: 1 / span 2;
  color: #a2aeba;
  font: 800 17px/1 var(--mono);
}

.boundary-rail span {
  color: #7a8895;
  font-size: 9px;
}

.boundary-rail strong {
  overflow-wrap: anywhere;
  color: #263846;
  font: 700 9px/1.4 var(--mono);
}

.observation-main {
  display: grid;
  gap: 14px;
}

.chart-panel {
  min-width: 0;
  padding: 21px;
  background: #fff;
  border: 1px solid var(--obs-line);
  border-radius: 4px;
  box-shadow: 0 1px 2px rgba(22, 38, 50, .04);
}

.audit-panel {
  min-width: 0;
  padding: 21px;
  background: #f8fafb;
  border: 1px solid var(--obs-line);
  border-radius: 4px;
}

.audit-panel details {
  overflow: hidden;
  background: #fff;
  border: 1px solid #d7dfe5;
}

.audit-panel details + details {
  margin-top: 7px;
}

.audit-panel summary {
  display: flex;
  justify-content: space-between;
  gap: 14px;
  align-items: center;
  min-height: 48px;
  padding: 12px 14px;
  cursor: pointer;
  color: #2e4352;
  background: #fff;
  font-size: 11px;
}

.audit-panel summary::marker {
  color: #6e8291;
}

.audit-panel summary strong {
  color: #748490;
  font: 700 8px/1.3 var(--mono);
}

.definition-table-shell {
  overflow-x: auto;
  border-top: 1px solid #dfe6eb;
}

.definition-table-shell table {
  width: 100%;
  min-width: 760px;
  border-collapse: collapse;
}

.definition-table-shell th,
.definition-table-shell td {
  padding: 10px 12px;
  color: #52636f;
  border-right: 1px solid #e4e9ed;
  border-bottom: 1px solid #e4e9ed;
  font-size: 9px;
  line-height: 1.5;
  text-align: left;
  vertical-align: top;
}

.definition-table-shell th {
  color: #6d7c87;
  background: #f3f6f8;
  font: 700 8px/1.3 var(--mono);
}

.definition-table-shell td b,
.definition-table-shell td code {
  display: block;
}

.definition-table-shell td b {
  color: #263b4a;
}

.definition-table-shell td code {
  margin-top: 4px;
  color: #7a8995;
  font: 8px/1.4 var(--mono);
}

.limitation-content,
.audit-content {
  padding: 15px;
  border-top: 1px solid #dfe6eb;
}

.limitation-content ol {
  display: grid;
  gap: 7px;
  margin: 0 0 14px;
  padding-left: 20px;
}

.limitation-content li {
  color: #52636f;
  font-size: 10px;
  line-height: 1.6;
}

.limitation-content dl,
.audit-content dl {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 1px;
  margin: 0;
  background: #dfe6eb;
  border: 1px solid #dfe6eb;
}

.limitation-content dl > div,
.audit-content dl > div {
  min-width: 0;
  padding: 10px 11px;
  background: #f8fafb;
}

.limitation-content dt,
.audit-content dt {
  margin-bottom: 4px;
  color: #7c8a95;
  font-size: 8px;
}

.limitation-content dd,
.audit-content dd {
  margin: 0;
  overflow-wrap: anywhere;
  color: #40525f;
  font: 8px/1.55 var(--mono);
}

.hash-list {
  display: grid;
  gap: 1px;
  margin-top: 10px;
  background: #dfe6eb;
  border: 1px solid #dfe6eb;
}

.hash-list > div {
  display: grid;
  grid-template-columns: minmax(150px, .45fr) minmax(0, 1fr);
  gap: 12px;
  padding: 9px 11px;
  background: #f8fafb;
}

.hash-list span {
  color: #52636f;
  font-size: 9px;
}

.hash-list code {
  overflow-wrap: anywhere;
  color: #6c7b86;
  font: 8px/1.45 var(--mono);
}

.chart-panel.is-hero {
  border-top: 4px solid var(--obs-blue);
}

.panel-heading {
  display: flex;
  justify-content: space-between;
  gap: 22px;
  align-items: end;
  margin-bottom: 14px;
  padding-bottom: 13px;
  border-bottom: 1px solid var(--obs-line);
}

.panel-heading > div:first-child > span {
  color: var(--obs-blue);
  font: 750 9px/1.2 var(--mono);
  letter-spacing: .1em;
}

.panel-heading h2 {
  margin: 5px 0 0;
  font: 740 clamp(18px, 2vw, 24px)/1.15 "DIN Alternate", "Avenir Next", "PingFang SC", sans-serif;
  letter-spacing: -.025em;
}

.panel-heading > p {
  max-width: 510px;
  margin: 0;
  color: var(--obs-muted);
  font: 9px/1.55 var(--mono);
  text-align: right;
}

.hero-metrics {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 1px;
  margin-bottom: 12px;
  background: #cfd8df;
  border: 1px solid #cfd8df;
}

.hero-metrics article {
  min-width: 0;
  padding: 12px 14px;
  background: #f6f8fa;
}

.hero-metrics span {
  display: block;
  color: #6e7d89;
  font-size: 9px;
}

.hero-metrics strong {
  display: block;
  margin: 6px 0 4px;
  color: #142c3d;
  font: 780 clamp(18px, 2vw, 26px)/1 var(--mono);
}

.hero-metrics small {
  color: #788693;
  font: 8px/1.4 var(--mono);
}

.chart-figure {
  min-width: 0;
  margin: 0;
  overflow: hidden;
  background:
    linear-gradient(rgba(239, 243, 246, .34) 1px, transparent 1px),
    #fff;
  background-size: 100% 32px;
  border: 1px solid #dae2e8;
}

.figure-heading {
  display: flex;
  justify-content: space-between;
  gap: 15px;
  align-items: center;
  min-height: 40px;
  padding: 9px 12px;
  background: rgba(247, 249, 250, .94);
  border-bottom: 1px solid #e0e6eb;
}

.figure-heading strong {
  color: #273946;
  font-size: 11px;
}

.figure-heading span {
  color: #74828e;
  font: 8px/1.35 var(--mono);
  text-align: right;
}

.marker-register {
  display: flex;
  flex-wrap: wrap;
  gap: 5px 14px;
  margin: 0;
  padding: 9px 12px;
  background: #f8fafb;
  border-top: 1px solid #e0e6eb;
}

.marker-register span {
  display: inline-flex;
  gap: 6px;
  align-items: center;
  color: #536371;
  font: 8px/1.45 var(--mono);
}

.marker-register i {
  width: 8px;
  height: 2px;
  flex: 0 0 auto;
  background: var(--obs-orange);
}

.marker-register.is-compact {
  display: grid;
}

.asn-overview {
  margin-top: 12px;
}

.metric-boundary {
  margin: 0 0 13px;
  padding: 9px 11px;
  color: #576977;
  background: #f2f6f8;
  border-left: 3px solid #648298;
  font-size: 10px;
  line-height: 1.55;
}

.metric-boundary strong {
  color: #314858;
}

.metric-switch {
  display: inline-flex;
  flex-wrap: wrap;
  gap: 3px;
  justify-content: flex-end;
  padding: 3px;
  background: #eef2f5;
  border: 1px solid #d6dee4;
}

.metric-switch button {
  min-height: 30px;
  padding: 6px 9px;
  cursor: pointer;
  color: #596a78;
  background: transparent;
  border: 0;
  border-radius: 1px;
  font: 700 9px/1.2 var(--mono);
}

.metric-switch button:hover {
  color: #183c55;
  background: #fff;
}

.metric-switch button.is-active {
  color: #fff;
  background: #183c55;
  box-shadow: 0 1px 2px rgba(20, 43, 59, .18);
}

.resource-grid,
.delta-grid,
.message-grid,
.address-family-grid,
.asn-duration-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.delta-grid {
  grid-template-columns: repeat(3, minmax(0, 1fr));
}

.message-grid .is-wide {
  grid-column: 1 / -1;
}

.asn-controls {
  display: grid;
  grid-template-columns: minmax(150px, 1fr) repeat(3, minmax(145px, .8fr)) minmax(110px, .55fr);
  gap: 8px;
  margin-bottom: 12px;
}

.asn-controls label,
.asn-result-count {
  display: grid;
  gap: 5px;
  align-content: end;
}

.asn-controls label > span,
.asn-result-count > span {
  color: #6c7b88;
  font: 700 8px/1.2 var(--mono);
  letter-spacing: .06em;
}

.asn-controls input,
.asn-controls select {
  width: 100%;
  min-width: 0;
  height: 36px;
  padding: 7px 9px;
  color: #263a48;
  background: #fff;
  border: 1px solid #cdd7de;
  border-radius: 2px;
  font-size: 10px;
}

.asn-result-count {
  padding: 7px 10px;
  background: #173244;
}

.asn-result-count > span {
  color: #83a9bf;
}

.asn-result-count strong {
  color: #fff;
  font: 700 11px/1.2 var(--mono);
}

.heatmap-figure {
  margin-bottom: 12px;
}

.state-legend {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 18px;
  padding: 9px 12px;
  background: #f5f8fa;
  border-bottom: 1px solid #dfe6eb;
}

.state-legend span {
  display: inline-flex;
  gap: 6px;
  align-items: center;
  color: #465865;
  font-size: 9px;
}

.state-legend i {
  width: 10px;
  height: 10px;
  border: 1px solid rgba(20, 40, 55, .15);
}

.state-legend i.is-visible { background: #167c68; }
.state-legend i.is-partial { background: #e09532; }
.state-legend i.is-invisible { background: #8c3f58; }
.state-legend i.is-unknown { background: #e6e9ec; }

.state-legend b {
  font-weight: 700;
}

.state-legend small {
  color: #8996a0;
  font: 8px/1 var(--mono);
}

.heatmap-pagination {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  align-items: center;
  padding: 10px 12px;
  color: #62727f;
  background: #f7f9fa;
  border-top: 1px solid #dfe6eb;
  font: 9px/1.3 var(--mono);
}

.heatmap-pagination div {
  display: flex;
  gap: 5px;
}

.heatmap-pagination button {
  min-height: 30px;
  padding: 5px 9px;
  cursor: pointer;
  color: #28465a;
  background: #fff;
  border: 1px solid #cbd6dd;
  border-radius: 2px;
  font-size: 9px;
  font-weight: 700;
}

.heatmap-pagination button:disabled {
  cursor: default;
  color: #a6b0b8;
  background: #f0f3f5;
}

.duration-note {
  margin: 0;
  padding: 9px 12px;
  color: #687784;
  background: #f8fafb;
  border-top: 1px solid #e0e6eb;
  font-size: 9px;
  line-height: 1.55;
}

.asn-table-shell {
  min-width: 0;
  overflow: hidden;
  background: #fff;
  border: 1px solid #dae2e8;
}

.asn-table-scroll {
  max-height: 358px;
  overflow: auto;
}

.asn-table-shell table {
  width: 100%;
  border-collapse: collapse;
  font-size: 9px;
}

.asn-table-shell th {
  position: sticky;
  top: 0;
  z-index: 1;
  padding: 9px 8px;
  color: #6a7985;
  background: #f2f5f7;
  border-bottom: 1px solid #d7e0e6;
  font: 700 8px/1.3 var(--mono);
  text-align: left;
  white-space: nowrap;
}

.asn-table-shell td {
  padding: 8px;
  color: #465764;
  border-bottom: 1px solid #e7ecef;
  font: 8px/1.35 var(--mono);
  white-space: nowrap;
}

.asn-table-shell td b {
  color: #173b54;
}

@media (max-width: 1160px) {
  .observation-masthead {
    grid-template-columns: 1fr;
  }

  .delta-grid {
    grid-template-columns: 1fr;
  }

  .asn-controls {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 800px) {
  .observation-masthead {
    gap: 22px;
    padding: 20px;
  }

  .scope-console {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .boundary-rail {
    grid-template-columns: 1fr;
  }

  .boundary-rail article {
    grid-template-columns: 26px 90px minmax(0, 1fr);
    align-items: center;
  }

  .boundary-rail b {
    grid-row: auto;
  }

  .panel-heading {
    align-items: flex-start;
    flex-direction: column;
  }

  .panel-heading > p {
    text-align: left;
  }

  .metric-switch {
    justify-content: flex-start;
  }

  .hero-metrics,
  .resource-grid,
  .message-grid,
  .address-family-grid,
  .asn-duration-grid {
    grid-template-columns: 1fr;
  }

  .message-grid .is-wide {
    grid-column: auto;
  }

  .asn-table-scroll {
    max-height: none;
    overflow: visible;
  }

  .asn-table-shell table,
  .asn-table-shell thead,
  .asn-table-shell tbody,
  .asn-table-shell tr,
  .asn-table-shell td {
    display: block;
    width: 100%;
  }

  .asn-table-shell thead {
    display: none;
  }

  .asn-table-shell tr {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    padding: 8px 10px;
    border-bottom: 1px solid #dfe6eb;
  }

  .asn-table-shell td {
    display: grid;
    gap: 3px;
    min-width: 0;
    padding: 6px;
    border: 0;
    white-space: normal;
  }

  .asn-table-shell td::before {
    color: #84919b;
    content: attr(data-label);
    font-size: 7px;
  }
}

@media (max-width: 480px) {
  .observation-masthead,
  .chart-panel,
  .audit-panel {
    padding: 15px;
  }

  .scope-console {
    grid-template-columns: 1fr;
  }

  .scope-console .is-wide {
    grid-column: 1;
  }

  .scope-console .is-wide dd {
    align-items: flex-start;
    flex-direction: column;
    gap: 2px;
  }

  .boundary-rail article {
    grid-template-columns: 24px 80px minmax(0, 1fr);
    padding: 10px;
  }

  .panel-heading h2 {
    font-size: 18px;
  }

  .metric-switch {
    display: grid;
    width: 100%;
  }

  .asn-controls {
    grid-template-columns: 1fr;
  }

  .figure-heading {
    align-items: flex-start;
    flex-direction: column;
    gap: 4px;
  }

  .figure-heading span {
    text-align: left;
  }

  .marker-register span {
    align-items: flex-start;
  }

  .heatmap-pagination {
    align-items: flex-start;
    flex-direction: column;
  }

  .asn-table-shell tr {
    grid-template-columns: 1fr;
  }

  .limitation-content dl,
  .audit-content dl {
    grid-template-columns: 1fr;
  }

  .hash-list > div {
    grid-template-columns: 1fr;
    gap: 4px;
  }
}
</style>
