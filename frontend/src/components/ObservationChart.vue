<script setup lang="ts">
import * as echarts from 'echarts'
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { formatChartTime, type TimeInput } from '@/utils/chartTime'
import { formatTimezoneLabel } from '@/utils/eventObservationTemplate'

export interface ObservationChartSeries {
  name: string
  color: string
  data: Array<[string, number | null]>
  type?: 'line' | 'bar'
  stack?: string
  area?: boolean
  symbol?: 'circle' | 'diamond' | 'rect' | 'triangle'
}

export interface ObservationChartMarker {
  time: string
  label: string
  color?: string
}

const props = withDefaults(defineProps<{
  series: ObservationChartSeries[]
  markers?: ObservationChartMarker[]
  unit?: string
  denominator?: string
  height?: number
  timezone?: string
  valueKind?: 'count' | 'percent' | 'signed'
  yMin?: number | null
  yMax?: number | null
  showZero?: boolean
  group?: string
}>(), {
  markers: () => [],
  unit: '',
  denominator: '',
  height: 300,
  timezone: 'Asia/Shanghai',
  valueKind: 'count',
  yMin: null,
  yMax: null,
  showZero: false,
  group: '',
})

const chartElement = ref<HTMLDivElement | null>(null)
let chart: echarts.ECharts | null = null
let resizeObserver: ResizeObserver | null = null

const hasData = computed(() => props.series.some((series) => series.data.length > 0))

function escapeHtml(value: unknown) {
  return String(value)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;')
}

function compactNumber(value: number) {
  const magnitude = Math.abs(value)
  if (magnitude >= 10_000_000) return `${(value / 10_000_000).toFixed(1)}千万`
  if (magnitude >= 10_000) return `${(value / 10_000).toFixed(magnitude >= 100_000 ? 0 : 1)}万`
  return value.toLocaleString('zh-CN')
}

function displayValue(value: number | null | undefined, signed = false) {
  if (typeof value !== 'number') return '—（缺失）'
  const sign = signed && value > 0 ? '+' : ''
  const rendered = props.valueKind === 'percent'
    ? value.toLocaleString('zh-CN', { maximumFractionDigits: 3 })
    : value.toLocaleString('zh-CN')
  return `${sign}${rendered}${props.unit}`
}

interface TooltipItem {
  axisValue?: TimeInput
  color?: string
  seriesName?: string
  data?: [string, number | null]
  value?: [string, number | null]
  marker?: string
}

function tooltipContent(parameters: unknown) {
  const items = (Array.isArray(parameters) ? parameters : [parameters]) as TooltipItem[]
  const first = items[0]
  if (!first) return ''
  const lines = items.map((item) => {
    const tuple = Array.isArray(item.data) ? item.data : item.value
    return `<div style="display:flex;align-items:center;justify-content:space-between;gap:18px;margin-top:7px"><span>${item.marker || ''}${escapeHtml(item.seriesName || '')}</span><b style="font-family:ui-monospace,SFMono-Regular,Menlo,monospace">${escapeHtml(displayValue(tuple?.[1], props.valueKind === 'signed'))}</b></div>`
  })
  const denominator = props.denominator
    ? `<div style="margin-top:8px;padding-top:7px;border-top:1px solid #e7ebef;color:#657383;font-size:10px;line-height:1.45">${escapeHtml(props.denominator)}</div>`
    : ''
  return `<div style="min-width:210px"><div style="color:#657383;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:10px">${escapeHtml(formatChartTime(first.axisValue ?? '', props.timezone, true))} · ${escapeHtml(formatTimezoneLabel(props.timezone))}</div>${lines.join('')}${denominator}</div>`
}

function renderChart() {
  if (!chartElement.value) return
  const showMarkerLabels = chartElement.value.clientWidth >= 680
  chart ||= echarts.init(chartElement.value, undefined, { renderer: 'canvas' })
  chart.group = props.group
  if (props.group) echarts.connect(props.group)

  const markerData = props.markers.map((marker) => ({
    name: marker.label,
    xAxis: marker.time,
    lineStyle: { color: marker.color || '#d96c0b' },
  }))
  const zeroMarker = props.showZero
    ? [{ name: '零线', yAxis: 0, lineStyle: { color: '#536171', type: 'solid', opacity: 0.75 } }]
    : []

  chart.setOption({
    animation: false,
    color: props.series.map((item) => item.color),
    grid: {
      left: 66,
      right: 22,
      top: props.markers.length ? 58 : 42,
      bottom: 46,
      containLabel: false,
    },
    legend: {
      top: 5,
      right: 10,
      itemWidth: 20,
      itemHeight: 3,
      selectedMode: true,
      textStyle: {
        color: '#596979',
        fontFamily: 'Avenir Next, PingFang SC, sans-serif',
        fontSize: 10,
      },
    },
    tooltip: {
      trigger: 'axis',
      confine: true,
      borderWidth: 1,
      borderColor: '#cfd7df',
      backgroundColor: 'rgba(255,255,255,.97)',
      extraCssText: 'box-shadow:0 12px 32px rgba(19,37,54,.14);border-radius:3px;',
      textStyle: { color: '#182633', fontSize: 11 },
      axisPointer: {
        type: 'line',
        snap: true,
        lineStyle: { color: '#354b5e', width: 1, type: 'dashed' },
      },
      formatter: tooltipContent,
    },
    xAxis: {
      type: 'time',
      boundaryGap: false,
      axisLine: { lineStyle: { color: '#b7c2cc' } },
      axisTick: { show: false },
      axisLabel: {
        color: '#647383',
        hideOverlap: true,
        fontSize: 9,
        formatter: (value: number) => formatChartTime(value, props.timezone),
      },
      splitLine: { show: false },
    },
    yAxis: {
      type: 'value',
      min: props.yMin ?? undefined,
      max: props.yMax ?? undefined,
      minInterval: props.valueKind === 'percent' ? 0.01 : 1,
      name: props.unit,
      nameLocation: 'end',
      nameGap: 10,
      nameTextStyle: { color: '#657383', fontSize: 9 },
      axisLabel: {
        color: '#647383',
        fontSize: 9,
        formatter: (value: number) => props.valueKind === 'percent'
          ? `${value}%`
          : compactNumber(value),
      },
      splitLine: { lineStyle: { color: '#e2e8ed', type: 'dashed' } },
    },
    series: props.series.map((item, index) => ({
      name: item.name,
      type: item.type || 'line',
      data: item.data,
      stack: item.stack,
      showSymbol: false,
      symbol: item.symbol || (index % 2 === 0 ? 'circle' : 'diamond'),
      symbolSize: 6,
      connectNulls: false,
      smooth: false,
      barMaxWidth: 8,
      barMinHeight: 1,
      lineStyle: {
        width: 2,
        type: index % 2 === 0 ? 'solid' : 'dashed',
      },
      itemStyle: item.type === 'bar' && props.valueKind === 'signed'
        ? {
            color: (params: { value?: [string, number | null] }) => {
              const value = params.value?.[1]
              return typeof value === 'number' && value < 0 ? '#c44536' : item.color
            },
          }
        : { color: item.color },
      areaStyle: item.area ? { opacity: 0.12 } : undefined,
      emphasis: { focus: 'series' },
      ...(index === 0 && (markerData.length || zeroMarker.length) ? {
        markLine: {
          silent: true,
          symbol: ['none', 'none'],
          animation: false,
          precision: 3,
          lineStyle: { color: '#d96c0b', width: 1, type: 'dashed', opacity: 0.8 },
          label: {
            show: showMarkerLabels,
            position: 'insideEndTop',
            distance: 3,
            color: '#91450b',
            backgroundColor: 'rgba(255,250,244,.9)',
            padding: [2, 3],
            fontSize: 8,
            formatter: (params: { data?: { name?: string } }) => (
              params.data?.name?.split(' · ').slice(0, 2).join(' · ') || ''
            ),
          },
          data: [...markerData, ...zeroMarker],
        },
      } : {}),
    })),
  }, true)
}

watch(
  () => [
    props.series,
    props.markers,
    props.unit,
    props.denominator,
    props.timezone,
    props.valueKind,
    props.yMin,
    props.yMax,
    props.showZero,
    props.group,
  ],
  renderChart,
  { deep: true },
)

onMounted(() => {
  renderChart()
  if (chartElement.value) {
    resizeObserver = new ResizeObserver(() => chart?.resize())
    resizeObserver.observe(chartElement.value)
  }
})

onBeforeUnmount(() => {
  resizeObserver?.disconnect()
  chart?.dispose()
})
</script>

<template>
  <div class="observation-chart-shell" :style="{ minHeight: `${height}px` }">
    <div
      ref="chartElement"
      class="observation-chart-canvas"
      :style="{ height: `${height}px` }"
      role="img"
      :aria-label="`${formatTimezoneLabel(timezone)}时序图：${series.map((item) => item.name).join('、')}`"
    ></div>
    <p v-if="!hasData" class="observation-chart-empty">当前窗口没有可绘制数据</p>
  </div>
</template>

<style scoped>
.observation-chart-shell {
  position: relative;
  width: 100%;
  min-width: 0;
}

.observation-chart-canvas {
  width: 100%;
  min-width: 0;
}

.observation-chart-empty {
  position: absolute;
  inset: 50% auto auto 50%;
  margin: 0;
  color: #73808d;
  font-size: 11px;
  transform: translate(-50%, -50%);
}
</style>
